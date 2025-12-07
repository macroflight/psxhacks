"""A PSX router connection class."""

import asyncio
import collections
import ipaddress
import logging
import time
import traceback

NOACCESS_ACCESS_LEVEL = 'noaccess'

# The correct separator
PSX_PROTOCOL_SEPARATOR = b'\r\n'

# All supported separators
SUPPORTED_PROTOCOL_SEPARATORS = (b'\r\n', b'\n\r', b'\r', b'\n')


class ConnectionException(Exception):  # pylint: disable=too-few-public-methods
    """A custom exception."""


class ConnectionClosed(ConnectionException):  # pylint: disable=too-few-public-methods
    """A custom exception."""


class Connection():  # pylint: disable=too-many-instance-attributes,too-few-public-methods
    """A connection to the PSX router."""

    def __init__(self, reader, writer, router):
        """Initialize the instance."""
        self.logger = logging.getLogger(__name__)
        # Reference to main router object
        self.router = router

        self.reader = reader
        self.writer = writer

        self.last_line_type = None

        self.upstream = False
        self.client_id = None

        # (ip, port) tuple - a unique identifier for the connection
        self.peername = writer.get_extra_info('peername')
        self.ip = self.peername[0]
        self.port = self.peername[1]

        # Connection time
        self.connected_at = time.perf_counter()

        # Set to True if the connection is being closed
        self.is_closing = False

        # We set this to true when we close the connection from inside
        # the class, then the router can detect that and remove the
        # connection from its list.
        self.closed = False

        self.display_name = 'unknown connection'
        self.display_name_source = 'new connection'

        self.client_provided_id = None
        self.client_provided_display_name = None

        self.simulator_name = 'unknown sim'
        self.router_name = 'unknown router'
        self.uuid = None

        # Traffic counters
        self.messages_sent = 0
        self.messages_received = 0
        self.bytes_sent = 0
        self.bytes_received = 0

        # Set to true if the connection is to another frankenrouter
        self.is_frankenrouter = False

        # FRDP PING
        # ID of the last FRDP PING sent
        self.frdp_ping_request_id = None
        # Timestamp of last sent FRDP PING
        self.frdp_ping_sent = None
        # List of the most recent FRDP PING RTTs
        self.frdp_ping_rtts = []

        # True if we have sent an FRDP IDENT message already
        self.frdp_ident_sent = False

        # Statistics buffer for write performance
        self.message_write_times = collections.deque(maxlen=100)

    async def to_stream(self, line, log=True, drain=True):  # pylint: disable=too-many-branches
        """Write data to a stream and optionally to a log file.

        Also update traffic counters.
        """
        t_start = time.perf_counter()
        if line is None:
            self.logger.critical("Not sending message=None to stream")
            return False
        linelen = len(line)
        if linelen <= 2:
            self.logger.critical("Dropping too-short message: %s", line)
            return False
        if self.closed:
            self.logger.info("Cannot send to closed connection %s", self.peername)
            return False
        try:
            # Check if the client buffer is growing too much
            if (
                    self.writer.transport.get_write_buffer_size() >
                    self.router.config.performance.write_buffer_warning
            ):
                self.logger.warning(
                    "Write buffer %d > %d for %s",
                    self.writer.transport.get_write_buffer_size(),
                    self.router.config.performance.write_buffer_warning,
                    self.peername
                )
            # Encode and send
            self.writer.write(line.encode() + PSX_PROTOCOL_SEPARATOR)
            if drain:
                await self.writer.drain()
        except ConnectionError as exc:
            self.logger.info(
                "Got %s on write to %s/%s, closing connection",
                type(exc).__name__,
                self.client_id, self.peername
            )
            await self.close(clean=False)
            return
        except Exception:  # pylint: disable=broad-exception-caught
            msg = f"Unhandled exception: {traceback.format_exc()}"
            if self.router.config.identity.stop_minded:
                raise SystemExit(f"{msg}\nRouter is stop-minded so shutting down now")  # pylint: disable=raise-missing-from
            self.logger.critical("%s\nRouter is go-minded so trying to continue", msg)

        t_send = time.perf_counter() - t_start
        self.messages_sent += 1
        self.bytes_sent += len(line) + 1
        if log:
            if self.upstream:
                await self.router.log_traffic(line, inbound=False)
            else:
                await self.router.log_traffic(line, endpoints=[self.client_id], inbound=False)
        # Update stats for this connection
        self.message_write_times.append(t_send)
        # Update stats for the router as a whole
        self.router.message_write_times.append(t_send)
        # Add message to bucket for this second
        now = int(time.time())
        if len(self.router.writes_counter) == 0:
            self.router.writes_counter.appendleft({
                'second': now,
                'count': 0
            })
        elif self.router.writes_counter[0]['second'] != now:
            self.router.writes_counter.appendleft({
                'second': now,
                'count': 1
            })
        self.router.writes_counter[0]['count'] += 1
        return True

    async def read_line_from_stream(self):
        r"""Read a single PSX message line from the stream.

        Handle all possble combinations of newline:

        Normal stream:
        Qi123=12\r\n
        Qi124=13\r\n

        But we can also get streams with any combination of \r and \n...

        And apparently some addons add some NULL bytes in their
        traffic. For now, we simply filter that out. Example from an
        MCP 'Qi33= 073\x00\r'
        """
        if self.closed:
            self.logger.info("Cannot read from closed connection %s", self.peername)
            return
        try:
            data = await self.reader.readuntil(SUPPORTED_PROTOCOL_SEPARATORS)
        except asyncio.IncompleteReadError as exc:
            # If we reached EOL before a separator was found, this
            # happens, and we should return None
            self.logger.info("readuntil returned IncompleteReadError, probably disconnect")
            raise ConnectionClosed from exc
        except (ConnectionError, OSError) as exc:
            self.logger.info("readuntil returned %s, probably disconnect", exc)
            raise ConnectionClosed from exc
        except Exception as exc:  # pylint: disable=broad-exception-caught
            msg = f"Unhandled exception: {traceback.format_exc()}"
            if self.router.config.identity.stop_minded:
                raise SystemExit(f"{msg}\nRouter is stop-minded so shutting down now")  # pylint: disable=raise-missing-from
            self.logger.critical("%s\nRouter is go-minded so trying to continue", msg)

        self.logger.debug("readuntil returned: %s", data)

        if b'\x00' in data:
            self.logger.warning("Stripping away NULL byte from %s", data)
            data = data.replace(b'\x00', b'')

        # Remove any newline components from the end of the string
        data_no_newline = data.replace(b'\n', b'').replace(b'\r', b'')
        self.logger.debug("with newlines removed: %s", data_no_newline)
        if data_no_newline == b'':
            # If the message is e.g Qi123=456\r\n, we will first get
            # Qi123=456\r and then \n. So an empty separator can be ignored.
            self.logger.debug("returning None")
            return None
        # Now add the correct newline and return
        retval = data_no_newline + b'\r\n'
        self.logger.debug("returning: %s", retval)
        return retval

    async def from_stream(self, line):
        """Log data read from stream."""
        self.messages_received += 1
        self.bytes_received += len(line) + 1
        if self.upstream:
            await self.router.log_traffic(line)
        else:
            await self.router.log_traffic(line, endpoints=[self.client_id])

    async def close(self, clean=True):
        """Close a server connection and remove server data."""
        try:
            if clean:
                await self.to_stream("exit")
                await asyncio.sleep(0.5)
            self.writer.close()
            await self.writer.wait_closed()
        except ConnectionError as exc:
            self.logger.warning("Exception when closing: %s", exc)
        except Exception:  # pylint: disable=broad-exception-caught
            msg = f"Unhandled exception: {traceback.format_exc()}"
            if self.router.config.identity.stop_minded:
                raise SystemExit(f"{msg}\nRouter is stop-minded so shutting down now")  # pylint: disable=raise-missing-from
            self.logger.critical("%s\nRouter is go-minded so trying to continue", msg)

        self.closed = True


class ClientConnection(Connection):  # pylint: disable=too-few-public-methods,too-many-instance-attributes
    """A connection to the PSX router."""

    def __init__(self, reader, writer, router):
        """Initialize the instance."""
        super().__init__(reader, writer, router)
        self.access_level = NOACCESS_ACCESS_LEVEL

        # The client ID generated by the router
        self.client_id = None

        self.display_name = 'unknown client'

        # Set to true if the client has requested nolong
        self.nolong = False

        # True if the client has been sent the welcome message
        self.welcome_sent = False
        # We keep track of welcome keywords sent so to this client
        self.welcome_keywords_sent = set()
        # Is the client waiting for the requested START keywords to arrive?
        self.waiting_for_start_keywords = False

        # List of variables this client has send demand= for
        self.demands = set()

        # Increase the write buffer a bit to fit a PSX welcome message
        self.writer.transport.set_write_buffer_limits(high=1048576, low=524288)

    def has_access(self):
        """Return true if client has access."""
        if self.access_level != NOACCESS_ACCESS_LEVEL:
            return True
        return False

    def can_write(self):
        """Return true if client has access."""
        if self.access_level == 'full':
            return True
        return False

    def update_access_level(self, client_password=None):  # pylint: disable=too-many-branches
        """Get the access level for connecting client."""

        def set_level(access):
            if access is None:
                self.logger.info("Setting %s for %s", NOACCESS_ACCESS_LEVEL, self.peername)
                self.access_level = NOACCESS_ACCESS_LEVEL
                self.display_name = 'auth pending'
                self.display_name_source = 'new connection'
            else:
                self.logger.info("Setting %s for %s", access.level, self.peername)
                self.access_level = access.level
                self.display_name = access.display_name
                self.display_name_source = 'access config'

        client_ip = ipaddress.ip_address(self.ip)
        self.logger.info(
            "Checking access level for client %s. ip=%s, password=%s",
            self.peername, client_ip, client_password)
        for access in self.router.config.access:
            # 1: check password and IP
            valid_password = False
            if access.match_password is not None:
                if access.match_password == client_password:
                    self.logger.info("Matching password")
                    valid_password = True

            valid_ip = False
            matching_network = None
            if access.match_ipv4 is not None:
                for elem in access.match_ipv4:
                    if elem == 'ANY':
                        valid_ip = True
                        matching_network = elem
                    else:
                        network = ipaddress.ip_network(elem)
                        if client_ip in network:
                            self.logger.info("Match: %s in %s", client_ip, network)
                            valid_ip = True
                            matching_network = network

            self.logger.debug("Checking against %s, valid_password=%s, valid_ip=%s",
                              access, valid_password, valid_ip)

            if access.match_ipv4 is not None and access.match_password is None:
                # Only IP match required
                if valid_ip:
                    self.logger.info("Access level %s granted based on IP match - %s in %s",
                                     access.level, client_ip, matching_network)
                    set_level(access)
                    return

            if access.match_password is not None and access.match_ipv4 is None:
                # Only password match required
                if valid_password:
                    self.logger.info("Access level %s granted based on password - %s is valid",
                                     access.level, client_password)
                    set_level(access)
                    return

            # require both to match
            if valid_password and valid_ip:
                self.logger.info(
                    "Access level %s granted based on IP+password - %s in %s, %s is valid",
                    access.level, client_ip, matching_network,
                    client_password)
                set_level(access)
                return

        # No match for any rule, deny access
        set_level(None)


class UpstreamConnection(Connection):  # pylint: disable=too-few-public-methods
    """A connection to an upstream router or PSX main server."""

    def __init__(self, reader, writer, router):
        """Initialize the instance."""
        super().__init__(reader, writer, router)

        self.upstream = True

        # True if we have sent an FRDP AUTH upstream
        self.frdp_auth_sent = False


#
# Unit tests
#
def test_connection(self):
    """Very basic test."""
    me = ClientConnection(None, None, None)
    self.assertEqual(me.nolong, False)
