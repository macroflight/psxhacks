"""A protocol-aware PSX router."""
# pylint: disable=invalid-name
import argparse
import asyncio
import datetime
import json
import logging
import os
import pathlib
import random
import re
import statistics
import string
import time

VERSION = '0.2'

PSX_SERVER_RECONNECT_DELAY = 1.0

# Regexp matching "normal" PSX network keywords
REGEX_PSX_KEYWORDS = r"^(id|version|layout|metar|demand|load[1-3]|Q[hsdi]\d+|L[sih]\d+\(.*\))$"

NOLONG_KEYWORDS = [
    "Qs375",
    "Qs376",
    "Qs377",
    "Qs407",
    "Qs408",
    "Qs409",
    "Qs410",
    "Qs411",
    "Qs412",
]

HEADER_LINE_LENGTH = 110


class FrankenrouterException(Exception):
    """Frankenrouter exception.

    For now, no special handling, this class just exists to make
    pylint happy. :)
    """


class Frankenrouter():  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """Replaces the PSX USB subsystem."""

    def __init__(self):
        """Initialize the class."""
        log_format = "%(asctime)s: %(message)s"
        logging.basicConfig(
            format=log_format,
            level=logging.INFO,
            datefmt="%H:%M:%S",
        )
        self.args = None
        self.logger = logging.getLogger("frankenrouter")
        self.state = None
        self.clients = {}
        self.server = {}
        self.stream_logfiles = {}
        self.start_time = int(time.time())
        self.allowed_clients = {
        }
        self.shutdown_requested = False
        self.proxy_server = None
        self.next_client_id = 1

    def handle_args(self):
        """Handle command line arguments."""
        parser = argparse.ArgumentParser(
            prog='frankenrouter',
            description='A PSX router',
            formatter_class=argparse.ArgumentDefaultsHelpFormatter,
            epilog='Good luck!')
        parser.add_argument(
            '--sim-name',
            type=lambda x: x if x.isascii() and len(x) <= 16 else False,
            action='store', default="UnknownSim",
            help="The name of your sim or router (max 16 chars)",
        )
        parser.add_argument(
            '--listen-port', type=int,
            action='store', default=10748)
        parser.add_argument(
            '--listen-host', type=str,
            action='store', default=None)
        parser.add_argument(
            '--psx-main-server-host', type=str,
            action='store', default='127.0.0.1')
        parser.add_argument(
            '--psx-main-server-port', type=int,
            action='store', default=10747)
        parser.add_argument(
            '--allowed-clients',
            action='store', default="",
            type=str,
            help=(
                "Comma-separated lists of clients thay may connect." +
                " format: ALL or IP:access level:identifier" +
                ", e.g 192.168.1.42:full:FrankenThrottle"),
        )
        parser.add_argument(
            '--print-client-keywords',
            action='store', default="",
            type=str,
            help="Comma-separated lists of keywords sent from clients to print to stdout")
        parser.add_argument(
            '--print-server-keywords',
            action='store', default="",
            type=str,
            help="Comma-separated lists of keywords sent from server to print to stdout")
        parser.add_argument(
            '--print-client-non-psx',
            action='store_true',
            help="Print all non-PSX communication from clients")
        parser.add_argument(
            '--print-server-non-psx',
            action='store_true',
            help="Print all non-PSX communication from server")
        parser.add_argument(
            '--server-buffer-size', type=int,
            action='store', default=1048576)
        parser.add_argument(
            '--ping-interval', type=int,
            action='store', default=1,
            help="How often to send a ping message to the server",
        )
        parser.add_argument(
            '--status-interval', type=int,
            action='store', default=10,
            help="How often to print router status messages",
        )
        parser.add_argument(
            '--state-cache-file', type=pathlib.Path,
            action='store', default='frankenrouter.cache.json')
        parser.add_argument(
            '--log-dir', type=pathlib.Path,
            action='store', default='./')
        parser.add_argument(
            '--log-streams',
            action='store_true')
        parser.add_argument(
            '--debug',
            action='store_true')

        self.args = parser.parse_args()
        if self.args.debug:
            self.logger.setLevel(logging.DEBUG)
        if self.args.allowed_clients == "":
            parser.error("You must use --allowed-clients")
        if not self.args.sim_name:
            parser.error("Invalid --sim-name")
        if self.args.allowed_clients != "ALL":
            for client in self.args.allowed_clients.split(','):
                if client == "":
                    continue
                (ip, access_level, identifier) = client.split(":")
                if access_level not in ['full', 'readonly']:
                    parser.error(f"Invalid client access level {access_level}")
                if len(identifier) > 16:
                    parser.error("Client identifier max length is 16")
                self.allowed_clients[ip] = {
                    'source': 'command line',
                    'access': access_level,
                    'identifier': identifier,
                }

        self.args.print_client_keywords = self.args.print_client_keywords.split(",")
        self.args.print_server_keywords = self.args.print_server_keywords.split(",")

    def get_random_id(self):
        """Return a random string we can use for FRDP request id."""
        return ''.join(
            random.choices(string.ascii_letters + string.digits, k=16))

    def is_server_connected(self):
        """Return True if we are connected to the PSX main server."""
        if len(self.server) > 0:
            return True
        return False

    def is_client_connected(self, client_addr):
        """Return True if this client is connected."""
        if client_addr in self.clients:
            return True
        return False

    def print_status(self):
        """Print a multi-line status message."""
        self.logger.info("-" * HEADER_LINE_LENGTH)
        self.logger.info(
            "Frankenrouter %s listening on %d, %d keywords cached",
            self.args.sim_name, self.args.listen_port, len(self.state),
        )
        serverinfo = "[NO SERVER CONNECTION]"
        if self.is_server_connected():
            serverinfo = f"SERVER {self.server['ip']}:{self.server['port']}"
            serverinfo += f" {self.server['identifier']}"
            if self.server['ping_rtt']:
                serverinfo = serverinfo + f", RTT: {self.server['ping_rtt']:.3f} s"
            if len(self.server['writedraintimes']) > 0:
                average_writedrain = statistics.mean(self.server['writedraintimes'])
                serverinfo = serverinfo + f", average output delay {average_writedrain:.6f} s"
        self.logger.info(serverinfo)
        self.logger.info(
            "%-19s %-15s %5s %8s %7s %6s %6s %6s %6s %7s %7s",
            f"{len(self.clients)} clients",
            "",
            "Local",
            "",
            "",
            "Lines",
            "Lines",
            "Bytes",
            "Bytes",
            "ping",
            "Output",
        )
        self.logger.info(
            "%2s %-16s %-15s %5s %8s %7s %6s %6s %6s %6s %7s %7s",
            "id",
            "Identifier",
            "Client IP",
            "Port",
            "Access",
            "Clients",
            "sent",
            "recvd",
            "sent",
            "recvd",
            "RTT(s)",
            "delay(s)",
        )
        for data in self.clients.values():
            if len(data['writedraintimes']) > 0:
                average_writedrain = f"{statistics.mean(data['writedraintimes']):.6f}"
            else:
                average_writedrain = "  NODATA"

            self.logger.info(
                "%2d %-16s %-15s %5d %8s %7d %6d %6d %6d %6d %7s %7s",
                data['id'],
                data['identifier'],
                data['ip'],
                data['port'],
                data['access'],
                data['connected_clients'],
                data['messages sent'],
                data['messages received'],
                data['bytes sent'],
                data['bytes received'],
                f"{data['ping_rtt']:.3f}" if data['ping_rtt'] else "-",
                average_writedrain,
            )
        self.logger.info("-" * HEADER_LINE_LENGTH)

    async def to_stream(self, endpoint, line):
        """Write data to a stream and optionally to a log file.

        Also update traffic counters.
        """
        # Write to stream
        start_time = time.perf_counter()
        endpoint['writer'].write(f"{line}\n".encode())
        await endpoint['writer'].drain()
        elapsed = time.perf_counter() - start_time
        # keep a list of the last 100 messages send per endpoint
        endpoint['writedraintimes'].append(elapsed)
        endpoint['writedraintimes'] = endpoint['writedraintimes'][-100:]

        endpoint['messages sent'] += 1
        endpoint['bytes sent'] += len(line) + 1
        # Write to optional log file
        if self.args.log_streams:
            if endpoint['peername'] not in self.stream_logfiles:
                self.logger.warning(
                    "Log file not initialized for %s, this should not happen", endpoint['peername'])
                return
            self.stream_logfiles[endpoint['peername']].write(
                f"{datetime.datetime.now().isoformat()} >>> {line}\n")
        return elapsed

    def from_stream(self, endpoint, line):
        """Log data read from stream."""
        endpoint['messages received'] += 1
        endpoint['bytes received'] += len(line) + 1
        if self.args.log_streams:
            if endpoint['peername'] not in self.stream_logfiles:
                self.logger.warning(
                    "Log file not initialized for %s, this should not happen", endpoint['peername'])
                return
            self.stream_logfiles[endpoint['peername']].write(
                f"{datetime.datetime.now().isoformat()} <<< {line}\n")

    async def client_send_welcome(self, client):  # pylint: disable=too-many-branches
        """Send the same data as a real PSX server would send to a new client."""
        # If some mandatory keywords are not yet received from the server, fake them
        sent = []

        async def send_if_unsent(key):
            if key not in sent:
                if key not in self.state:
                    self.logger.warning(
                        "%s not found in self.state, client restart might be needed" +
                        " after server connection", key)
                    return
                line = f"{key}={self.state[key]}"
                await self.to_stream(client, line)
                sent.append(key)
                self.logger.debug("To %s: %s", client['peername'], line)

        async def send_unconditionally(key):
            line = f"{key}={self.state[key]}"
            await self.to_stream(client, line)
            sent.append(key)
            self.logger.debug("To %s: %s", client['peername'], line)

        async def send_line(line):
            await self.to_stream(client, line)
            self.logger.debug("To %s: %s", client['peername'], line)

        # Transmit the latest cached server data to the client

        # Correct order (as of 10.181)
        # id=1
        # version=10.181 NG
        # layout=1
        # Ls...
        # Lh...
        # Li...
        # Qi138
        # Qs440
        # Qs439
        # Qs450
        # load1
        # Qi0 ... Qi31
        # load2
        # Qi32 ...
        # Qh...
        # Qs...
        # load3
        # metar=2.4m/1.8m202506050523STBY
        # Qs124=1397634006009
        # Qs125=1397634006009

        # Send a "fake" client id (rather than the id of the proxy's
        # connection to the PSX main server.
        await send_line(f"id={client['id']}")

        # I think these along with id are mendatory for a PSX main
        # client to connect fully, so send something even if we do not
        # yet have a server connection.
        if 'version' not in self.state:
            self.state['version'] = "10.181 NG"
        if 'layout' not in self.state:
            self.state['layout'] = "1"

        for key in [
                "version", "layout",
        ]:
            await send_if_unsent(key)

        for prefix in [
                "Ls",
                "Lh",
                "Li",
        ]:
            for key in self.state.keys():
                if key.startswith(prefix):
                    await send_if_unsent(key)
        for prefix in [
                "Qi138",
                "Qs440",
                "Qs439",
                "Qs450",
        ]:
            for key in self.state.keys():
                if key == prefix:
                    await send_if_unsent(key)
        await send_line("load1")
        for prefix in [
                "Qi0",
                "Qi1",
                "Qi2",
                "Qi3",
                "Qi4",
                "Qi5",
                "Qi6",
                "Qi7",
                "Qi8",
                "Qi9",
                "Qi10",
                "Qi11",
                "Qi12",
                "Qi13",
                "Qi14",
                "Qi15",
                "Qi16",
                "Qi17",
                "Qi18",
                "Qi19",
                "Qi20",
                "Qi21",
                "Qi22",
                "Qi23",
                "Qi24",
                "Qi25",
                "Qi26",
                "Qi27",
                "Qi28",
                "Qi29",
                "Qi30",
                "Qi31",
        ]:
            for key in self.state.keys():
                if key == prefix:
                    await send_if_unsent(key)
        await send_line("load2")
        for prefix in [
                "Qi",
                "Qh",
                "Qs",
        ]:
            for key in self.state.keys():
                if key.startswith(prefix):
                    await send_if_unsent(key)
        await send_line("load3")
        await send_if_unsent("metar")
        await send_unconditionally("Qs124")
        await send_unconditionally("Qs125")
        await send_line(f"name=frankenrouter:{self.args.sim_name}")

    async def close_client_connection(self, client):
        """Close a client connection and remove client data."""
        try:
            client['writer'].close()
            await client['writer'].wait_closed()
        except ConnectionResetError:
            pass
        del self.clients[client['peername']]
        self.logger.info("Closed client connection %s", client['peername'])
        self.print_status()

    async def close_server_connection(self):
        """Close a server connection and remove server data."""
        if 'writer' in self.server:
            self.server['writer'].close()
            await self.server['writer'].wait_closed()
            self.logger.info("Closed server connection")
            self.server = {}
            self.print_status()

    async def handle_new_connection_cb(self, reader, writer):  # pylint: disable=too-many-branches,too-many-statements,too-many-locals
        """Handle a new client connection."""
        try:
            client_addr = writer.get_extra_info('peername')
            assert client_addr not in self.clients, f"Duplicate client ID {client_addr}"
            # Store the connection information and some other useful
            # things in self.clients.
            this_client = {
                'peername': client_addr,
                'ip': client_addr[0],
                'port': client_addr[1],
                'reader': reader,
                'writer': writer,
                'access': 'noaccess',
                'identifier': 'unknown',
                'id': self.next_client_id,
                'nolong': False,
                'messages sent': 0,
                'messages received': 0,
                'bytes sent': 0,
                'bytes received': 0,
                'is_frankenrouter': False,
                'ping_identifier': None,
                'ping_sent': None,
                'ping_rtt': None,
                'writedraintimes': [],
                'connected_clients': 0,
            }
            self.clients[client_addr] = this_client
            self.next_client_id += 1
            self.logger.info("New client connection: %s", client_addr)

            # Allow whitelisted IPs to connect
            if self.args.allowed_clients == "ALL":
                this_client['access'] = "full"
                this_client['identifier'] = "allow-all"
                if client_addr[0] in self.allowed_clients:
                    # Use the configured name if available
                    this_client['identifier'] = self.allowed_clients[client_addr[0]]['identifier']
                self.logger.info(
                    "Client identified as %s, access level %s (allow-all mode)",
                    this_client['identifier'],
                    this_client['access'],
                )
            elif client_addr[0] in self.allowed_clients:
                # Update client data with information from IP whitelist
                this_client['access'] = self.allowed_clients[
                    client_addr[0]]['access']
                this_client['identifier'] = self.allowed_clients[
                    client_addr[0]]['identifier']
                self.logger.info(
                    "Client identified as %s, access level %s",
                    this_client['identifier'],
                    this_client['access'],
                )
            else:
                self.logger.warning("Client %s not identified, closing connection", client_addr)
                writer.write("unauthorized\n".encode())
                await self.close_client_connection(this_client)
                return

            if self.args.log_streams:
                logfile = os.path.join(
                    self.args.log_dir,
                    f"client-{self.start_time}-{this_client['id']}-{this_client['ip']}-{this_client['port']}.psxnet.log"
                )
                self.stream_logfiles[client_addr] = open(logfile, 'a', encoding='utf-8')  # pylint: disable=consider-using-with

            self.print_status()
            await self.client_send_welcome(this_client)

            # Wait for data from client
            while self.is_client_connected(client_addr):
                self.logger.debug("Waiting for data from client %s", client_addr)
                # We know the protocol is text-based, so we can use readline()
                try:
                    data = await reader.readline()
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    del self.clients[client_addr]
                    self.logger.warning("Client connection broke (%s) for %s", exc, client_addr)
                    self.print_status()
                    return
                line = data.decode().strip()
                if line == "":
                    await self.close_client_connection(this_client)
                    return
                self.logger.debug("From client %s: %s", client_addr, line)

                # Log data from client
                self.from_stream(this_client, line)

                key, sep, value = line.partition("=")

                # FrankenRouter DiscoveryProtocol :)

                # For initial detection of other frankenrouters, we
                # send the "standard" name= keyword.
                if key == 'name':
                    self.logger.debug("key is name for %s", line)
                    if re.match(r"^frankenrouter:", value):
                        identifier = value.split(":")[1]
                        self.logger.info(
                            "Client %s identified as frankenrouter %s",
                            client_addr, identifier)
                        this_client['is_frankenrouter'] = True
                        this_client['identifier'] = f"R:{identifier}"
                        # We should not send this upstream, so stop here
                        continue

                if key == 'nolong':
                    print("NOLONG NOLONG")
                    # Toggle nolong bit for this client, but do not send upstream
                    this_client['nolong'] = not this_client['nolong']
                    self.logger.info(
                        "Client %s toggled nolong to %s", client_addr, this_client['nolong'])
                    continue

                if key == 'frankenrouter':
                    self.logger.debug("frankenrouter message from client %s", client_addr)
                    (messagetype, message) = value.split(":", 1)
                    if messagetype == 'ping':
                        self.logger.debug(
                            "Got FRDP ping message from client %s: %s", client_addr, line)
                        (identifier, request_id) = message.split(":", 1)
                        await self.client_broadcast(
                            f"frankenrouter=pong:{self.args.sim_name}:{request_id}",
                            include=[client_addr],
                        )
                        # store name and the fact that this client is a frankenrouter
                        this_client['is_frankenrouter'] = True
                        this_client['identifier'] = f"R:{identifier}"
                        continue
                    if messagetype == 'pong':
                        self.logger.debug(
                            "Got FRDP pong message from client %s: %s", client_addr, line)
                        (identifier, request_id, connected_clients) = message.split(":", 2)
                        connected_clients = int(connected_clients)
                        this_client['ping_rtt'] = time.perf_counter() - this_client['ping_sent']
                        this_client['connected_clients'] = connected_clients
                        continue
                    self.logger.critical(
                        "Unsupported FRDP message (%s): %s", messagetype, line)
                    continue

                # Print if key is in --print-client-keywords
                if key in self.args.print_client_keywords:
                    self.logger.info("%s from %s: %s", key, client_addr, line)

                # Print non-PSX keywoards (e.g "name") if --print-client-non-psx
                if self.args.print_client_non_psx:
                    if not re.match(REGEX_PSX_KEYWORDS, key):
                        self.logger.info("NONPSX %s from %s: %s", key, client_addr, line)

                # Pick up name information from clients
                # Note: we inhibit name changes on a connection from a
                # frankenrouter as other clients are multiplexed on
                # that connection.
                if key == 'name' and not this_client['is_frankenrouter']:
                    thisname = value
                    self.logger.info("Checking %s against name regexps", value)
                    if re.match(r".*PSX.NET EFB.*", value):
                        thisname = value.split(":")[0]
                    elif re.match(r":PSX Sounds", value):
                        thisname = "PSX Sounds"
                    # name=MSFS Router:PSX.NET Modules
                    elif re.match(r"^MSFS Router", value):
                        thisname = "MSFS Router"
                    # e.g name=FrankenUSB:frankenusb.py
                    elif re.match(r".*:franken.*.py", value):
                        thisname = value.split(":")[0]
                    this_client['identifier'] = f"L:{thisname}"
                    self.logger.info(
                        "Client %s identifies as %s, using that name",
                        this_client['peername'], thisname)
                    self.print_status()

                # Router management via client commands
                if key == 'RouterStop':
                    self.logger.info("Got RouterStop command from %s", client_addr)
                    self.shutdown_requested = True
                    continue

                if this_client['access'] == 'full':
                    if key in ["bang", "start", "again"]:
                        # Forward to server but not other clients
                        await self.send_to_server(key, client_addr)
                    elif key in ["nolong"]:
                        self.logger.warning("nolong not implemented, ignoring")
                    elif key in ["load1", "load2", "load3", "pleaseBeSoKindAndQuit"]:
                        # Forward to server and other clients
                        self.logger.info("%s from %s", key, client_addr)
                        await self.send_to_server(key, client_addr)
                        await self.client_broadcast(key, exclude=[client_addr])
                    elif key in [
                            'exit',
                    ]:
                        # Shut down client connection cleanly
                        self.logger.info("Client %s sent exit message, closing", client_addr)
                        await self.close_client_connection(this_client)
                        return
                    elif sep != "":
                        self.state[key] = value
                        line = f"{key}={value}"
                        await self.send_to_server(line, client_addr)
                        await self.client_broadcast(line, exclude=[client_addr])
                    else:
                        self.logger.warning("Unhandled data (%s) from client: %s", key, line)
                else:
                    self.logger.info(
                        "Read-only client tried to send data, ignoring: %s",
                        line
                    )
        except ConnectionResetError:
            self.logger.info("Connection reset by client %s", client_addr)
            del self.clients[client_addr]

    async def client_broadcast(self, line, exclude=None, include=None, islong=False):
        """Send a line to connected clients.

        If exclude is provided, send to all connected clients except
        clients in that list.

        If include is provided, send to those clients.
        """
        if exclude and include:
            self.logger.critical(
                "client_broadcast called with bost include and exclude - not supported")
            return
        if exclude:
            for client in self.clients.values():
                if client['peername'] in exclude:
                    self.logger.debug(
                        "Not sending to excluded client %s", client['peername'])
                    continue
                if islong and client['nolong']:
                    self.logger.debug(
                        "Not sending long string to nolong client %s: %s",
                        client['peername'], line)
                    continue
                await self.to_stream(client, line)
                self.logger.debug("To %s: %s", client['peername'], line)
        elif include:
            for client in self.clients.values():
                if client['peername'] not in include:
                    self.logger.debug(
                        "Not sending to not-included client %s", client['peername'])
                    continue
                if islong and client['nolong']:
                    self.logger.debug(
                        "Not sending long string to nolong client %s: %s",
                        client['peername'], line)
                    continue
                await self.to_stream(client, line)
                self.logger.debug("To %s: %s", client['peername'], line)
        else:
            for client in self.clients.values():
                if islong and client['nolong']:
                    self.logger.debug(
                        "Not sending long string to nolong client %s: %s",
                        client['peername'], line)
                    continue
                await self.to_stream(client, line)
                self.logger.debug("To %s: %s", client['peername'], line)

    async def send_to_server(self, line, client_addr=None):
        """Send a line to the PSX main server."""
        if not self.is_server_connected():
            self.logger.warning("Server is disconnected, discarding: %s", line)
            return
        await self.to_stream(self.server, line)
        self.logger.debug("To server from %s: %s", client_addr, line)

    async def handle_psx_server_connection(self):  # pylint: disable=too-many-branches,too-many-statements
        """Set up and maintain a PSX server connection."""
        while not self.shutdown_requested:
            try:
                reader, writer = await asyncio.open_connection(
                    self.args.psx_main_server_host,
                    self.args.psx_main_server_port,
                    limit=self.args.server_buffer_size,
                )
            # At least on Windows we get OSError after ~30s if the PSX server is down or unreachable
            except (ConnectionRefusedError, OSError):
                self.logger.warning(
                    "PSX server connection refused, sleeping %.1f s before retry",
                    PSX_SERVER_RECONNECT_DELAY,
                )
                await asyncio.sleep(PSX_SERVER_RECONNECT_DELAY)
                continue
            server_addr = writer.get_extra_info('peername')
            self.server = {
                'peername': server_addr,
                'ip': server_addr[0],
                'port': server_addr[1],
                'reader': reader,
                'writer': writer,
                'identifier': 'unknown',
                'messages sent': 0,
                'messages received': 0,
                'bytes sent': 0,
                'bytes received': 0,
                'servername': "unknown",
                'is_frankenrouter': False,
                'ping_identifier': None,
                'ping_sent': None,
                'ping_rtt': None,
                'writedraintimes': [],
            }
            self.logger.info("Connected to server: %s", server_addr)

            if self.args.log_streams:
                logfile = os.path.join(
                    self.args.log_dir,
                    f"server-{self.start_time}-{server_addr[0]}-p{server_addr[1]}.psxnet.log"
                )
                self.stream_logfiles[server_addr] = open(logfile, 'a', encoding='utf-8')  # pylint: disable=consider-using-with

            # Send our name (for when we connect to another router)
            await self.send_to_server(f"name=frankenrouter:{self.args.sim_name}")

            self.print_status()

            # Wait for and process data from server connection
            while self.is_server_connected():
                # We know the protocol is line-oriented and the lines will
                # not be too long to handle as a single unit, so we can
                # read one line at a time.
                try:
                    data = await reader.readline()
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    self.logger.info(
                        "Server connection broke (%s), sleeping %.1f s before reconnect",
                        exc,
                        PSX_SERVER_RECONNECT_DELAY,
                    )
                    await self.close_server_connection()
                    await asyncio.sleep(PSX_SERVER_RECONNECT_DELAY)
                    continue

                line = data.decode().strip()
                if line == '':
                    self.logger.info(
                        "Server disconnected, sleeping %.1f s before reconnect",
                        PSX_SERVER_RECONNECT_DELAY,
                    )
                    await self.close_server_connection()
                    await asyncio.sleep(PSX_SERVER_RECONNECT_DELAY)
                    break

                self.logger.debug("From server: %s", line)
                self.from_stream(self.server, line)

                # Store various things that we get e.g on initial
                # connection and that we might need later.
                key, sep, value = line.partition("=")

                # FrankenRouter DiscoveryProtocol :)
                if key == 'frankenrouter':
                    (messagetype, message) = value.split(":", 1)
                    if messagetype == 'ping':
                        self.logger.debug("Got FRDP ping message from server: %s", line)
                        (identifier, request_id) = message.split(":", 1)
                        # send a reply back
                        self.logger.debug("Sending FRDP pong to server")
                        await self.send_to_server(
                            "%s=%s:%s:%s:%s" % (  # pylint:disable=consider-using-f-string
                                "frankenrouter",
                                "pong",
                                self.args.sim_name,
                                request_id,
                                len(self.clients)))
                        # store name and the fact that this client is a frankenrouter
                        self.server['is_frankenrouter'] = True
                        self.server['identifier'] = f"R:{identifier}"
                        continue
                    if messagetype == 'pong':
                        self.logger.debug("Got FRDP pong message from server: %s", line)
                        (identifier, request_id) = message.split(":", 1)
                        self.server['ping_rtt'] = time.perf_counter() - self.server['ping_sent']
                        continue
                    self.logger.critical("Unsupported FRDP message (%s): %s", messagetype, line)
                    continue

                if key in self.args.print_server_keywords:
                    self.logger.info("%s from server: %s", key, line)
                if self.args.print_server_non_psx:
                    if not re.match(REGEX_PSX_KEYWORDS, key):
                        self.logger.info("NONPSX %s from server: %s", key, line)

                if key in [
                        'load1',
                        'load2',
                        'load3',
                ]:
                    # Load messages: send to connected clients
                    self.logger.info("From PSX: %s", key)
                    await self.client_broadcast(line)
                elif key in [
                        'bang',
                        'start',
                ]:
                    # Should not be sent by server, ignore
                    pass
                elif key in [
                        'exit',
                ]:
                    # Shut down server connection cleanly
                    self.logger.info(
                        "Server sent exit message, disconnecting, sleeping %.1f s before reconnect",
                        PSX_SERVER_RECONNECT_DELAY,
                    )
                    await self.close_server_connection()
                    await asyncio.sleep(PSX_SERVER_RECONNECT_DELAY)
                elif sep != "":
                    # Key-value message (including lexicon): store in
                    # state and send to connected clients
                    self.logger.debug("Storing key-value from server: %s=%s", key, value)
                    self.state[key] = value
                    if key in NOLONG_KEYWORDS:
                        await self.client_broadcast(line, islong=True)
                    else:
                        await self.client_broadcast(line)
                else:
                    self.logger.warning("Unhandled data from server: %s", line)

    def read_cache(self):
        """Read the state cache from file."""
        try:
            with open(self.args.state_cache_file, 'r', encoding='utf-8') as statefile:
                self.state = json.load(statefile)
                self.logger.info(
                    "Read %d entries from %s",
                    len(self.state),
                    self.args.state_cache_file,
                )
        except (FileNotFoundError, json.decoder.JSONDecodeError):
            self.logger.warning(
                "No initial state file %s found, you might need to reconnect some clients",
                self.args.state_cache_file,
            )
            self.state = {}

    def write_cache(self):
        """Write state cache from file."""
        if len(self.state) > 0:
            self.logger.info(
                "Writing %d cache entries to %s",
                len(self.state),
                self.args.state_cache_file,
            )
            with open(self.args.state_cache_file, 'w', encoding='utf-8') as statefile:
                statefile.write(json.dumps(self.state))

    async def shutdown(self):
        """Shut down the proxy."""
        self.logger.info("Shutting down")
        await self.client_broadcast("exit")
        self.logger.info("Exit message sent to clients, sleeping")
        await asyncio.sleep(1)

        self.logger.info("Closing listener")
        self.proxy_server.close()
        self.proxy_server.close_clients()
        await self.proxy_server.wait_closed()
        self.clients = {}

        self.logger.info("Closing server connection %s", self.server['peername'])
        try:
            await self.close_server_connection()
        except ConnectionResetError:
            pass
        self.server = {}
        self.write_cache()

    async def start_listener(self):
        """Start the listener."""
        self.proxy_server = await asyncio.start_server(
            self.handle_new_connection_cb,
            host=self.args.listen_host,
            port=self.args.listen_port,
            limit=self.args.server_buffer_size
        )

    async def routermonitor(self):
        """Monitor the router and shut down when requested."""
        last_status_message = time.perf_counter()
        last_ping = time.perf_counter()
        ping_interval = 5.0
        status_interval = 5.0
        while True:
            elapsed_since_ping = time.perf_counter() - last_ping
            if elapsed_since_ping > ping_interval:
                # If connected to a frankenrouter server, send FRDP ping
                if self.is_server_connected() and self.server['is_frankenrouter']:
                    self.logger.debug("Sending FRDP ping to server")
                    frdp_request_id = self.get_random_id()
                    await self.send_to_server(
                        f"frankenrouter=ping:{self.args.sim_name}:{frdp_request_id}")
                    self.server['ping_sent'] = time.perf_counter()
                    self.server['ping_identifier'] = frdp_request_id
                # Send FRDP ping to any frankenrouter clients
                for peername, data in self.clients.items():
                    if data['is_frankenrouter']:
                        self.logger.debug("Sending FRDP ping to client %s", peername)
                        frdp_request_id = self.get_random_id()
                        await self.client_broadcast(
                            f"frankenrouter=ping:{self.args.sim_name}:{frdp_request_id}",
                            include=[peername])
                        data['ping_sent'] = time.perf_counter()
                        data['ping_identifier'] = frdp_request_id
                last_ping = time.perf_counter()
            else:
                self.logger.debug("Only %.3f s since ping", elapsed_since_ping)

            # Status display
            if time.perf_counter() - last_status_message > status_interval:
                self.print_status()
                last_status_message = time.perf_counter()
            if self.shutdown_requested:
                await self.shutdown()
                self.logger.info("Monitor shutting down")
                return
            await asyncio.sleep(1.0)

    async def main(self):
        """Start the proxy."""
        self.handle_args()
        self.read_cache()
        self.logger.info("frankenusb version %s starting", VERSION)

        await asyncio.gather(
            self.start_listener(),
            self.handle_psx_server_connection(),
            self.routermonitor(),
        )


if __name__ == '__main__':
    me = Frankenrouter()
    asyncio.run(me.main())
