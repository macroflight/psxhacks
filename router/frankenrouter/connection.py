"""A PSX router connection class."""

import asyncio
import ipaddress
import logging
import time

NOACCESS_ACCESS_LEVEL = 'noaccess'

PSX_PROTOCOL_SEPARATOR = b'\r\n'


class ConnectionException(Exception):  # pylint: disable=too-few-public-methods
    """A custom exception."""


class Connection():  # pylint: disable=too-many-instance-attributes,too-few-public-methods
    """A connection to the PSX router."""

    def __init__(self, reader, writer, config, log_traffic):
        """Initialize the instance."""
        self.logger = logging.getLogger(__name__)

        # Reference to function used to log traffic to file
        self.log_traffic = log_traffic

        self.reader = reader
        self.writer = writer
        self.config = config

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

        self.display_name = 'unknown connection'

        self.simulator_name = 'unknown sim'
        self.router_name = 'unknown router'

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

    async def to_stream(self, line, log=True):
        """Write data to a stream and optionally to a log file.

        Also update traffic counters.
        """
        try:
            if (
                    self.writer.transport.get_write_buffer_size() >
                    self.config.performance.write_buffer_warning
            ):
                self.logger.warning(
                    "Write buffer %d > %d for %s",
                    self.writer.transport.get_write_buffer_size(),
                    self.config.performance.write_buffer_warning,
                    self.peername
                )
            if line is not None:
                self.writer.write(line.encode() + PSX_PROTOCOL_SEPARATOR)
                await self.writer.drain()
        except ConnectionResetError:
            pass
        except BrokenPipeError:
            pass
        else:
            self.messages_sent += 1
            self.bytes_sent += len(line) + 1
        if log:
            if self.upstream:
                await self.log_traffic(line, inbound=False)
            else:
                await self.log_traffic(line, endpoints=[self.client_id], inbound=False)

    async def from_stream(self, line):
        """Log data read from stream."""
        self.messages_received += 1
        self.bytes_received += len(line) + 1
        if self.upstream:
            await self.log_traffic(line)
        else:
            await self.log_traffic(line, endpoints=[self.client_id])

    async def close(self):
        """Close a server connection and remove server data."""
        try:
            await self.to_stream("exit")
            await asyncio.sleep(0.5)
            self.writer.close()
            await self.writer.wait_closed()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError) as exc:
            self.logger.warning("Exception when closing: %s", exc)
            pass


class ClientConnection(Connection):  # pylint: disable=too-few-public-methods,too-many-instance-attributes
    """A connection to the PSX router."""

    def __init__(self, reader, writer, config, log_traffic):
        """Initialize the instance."""
        super().__init__(reader, writer, config, log_traffic)
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

        # List of messages pending
        self.pending_messages = []

        # List of variables this client has send demand= for
        self.demands = set()

        # Number of connected clients (for frankenrouter clients)
        self.connected_clients = 0

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

    def update_access_level(self, client_password=None):
        """Get the access level for connecting client."""

        def set_level(access):
            if access is None:
                self.logger.info("Setting %s for %s", NOACCESS_ACCESS_LEVEL, self.peername)
                self.access_level = NOACCESS_ACCESS_LEVEL
                self.display_name = 'auth pending'
            else:
                self.logger.info("Setting %s for %s", access.level, self.peername)
                self.access_level = access.level
                self.display_name = access.display_name

        client_ip = ipaddress.ip_address(self.ip)
        self.logger.info(
            "Checking access level for client %s. ip=%s, password=%s",
            self.peername, client_ip, client_password)
        for access in self.config.access:
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

    def __init__(self, reader, writer, config, log_traffic):
        """Initialize the instance."""
        super().__init__(reader, writer, config, log_traffic)

        self.upstream = True

        # True if we have sent an FRDP IDENT upstream
        self.frdp_ident_sent = False
        # True if we have sent an FRDP AUTH upstream
        self.frdp_auth_sent = False


#
# Unit tests
#
def test_connection(self):
    """Very basic test."""
    me = ClientConnection(None, None, None, None)
    self.assertEqual(me.nolong, False)
