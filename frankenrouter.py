"""A protocol-aware PSX router."""
# pylint: disable=invalid-name
import argparse
import asyncio
import datetime
import json
import logging
import os
import pathlib
import re
import time

VERSION = '0.2'

PSX_SERVER_RECONNECT_DELAY = 1.0

# Regexp matching "normal" PSX network keywords
REGEX_PSX_KEYWORDS = r"^(id|version|layout|metar|demand|load[1-3]|Q[hsdi]\d+|L[sih]\d+\(.*\))$"


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
            action='store', default="127.0.0.1:full:localhost",
            type=str,
            help=(
                "Comma-separated lists of clients thay may connect." +
                " format: IP:access level:identifier" +
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
            action='store', default=65536)
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
        for client in self.args.allowed_clients.split(','):
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

    def server_connected(self):
        """Return True if we are connected to the PSX main server."""
        if len(self.server) > 0:
            return True
        return False

    def client_connected(self, client_addr):
        """Return True if this client is connected."""
        if client_addr in self.clients:
            return True
        return False

    def print_status(self):
        """Print a one-line status message."""
        serverinfo = "[NO SERVER CONNECTION]"
        if self.server_connected():
            serverinfo = f"[{self.server['ip']}:{self.server['port']}]"

        self.logger.info(
            "%5s %2d clients, %3d keywords",
            serverinfo,
            len(self.clients),
            len(self.state),
        )
        self.logger.info(
            "%2s %-16s %-15s %5s %8s",
            "id",
            "Identifier     ",
            "Client IP       ",
            "Port ",
            "Access  ",
        )
        for data in self.clients.values():
            self.logger.info(
                "%2d %-16s %-15s %5d %8s",
                data['id'],
                data['identifier'],
                data['ip'],
                data['port'],
                data['access'],
            )

    async def to_stream(self, endpoint, line):
        """Write data to a stream and optionally to a log file."""
        # Write to stream
        endpoint['writer'].write(f"{line}\n".encode())
        await endpoint['writer'].drain()
        if self.args.log_streams:
            if endpoint['peername'] not in self.stream_logfiles:
                self.logger.warning(
                    "Log file not initialized for %s, this should not happen", endpoint['peername'])
                return
            self.stream_logfiles[endpoint['peername']].write(
                f"{datetime.datetime.now().isoformat()} >>> {line}\n")

    def from_stream(self, endpoint, line):
        """Log data read from stream."""
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

        if 'id' not in self.state:
            self.state['id'] = "1"
        if 'version' not in self.state:
            self.state['version'] = "10.181 NG"
        if 'layout' not in self.state:
            self.state['layout'] = "1"

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

        # Send a "fake" client id
        await send_line(f"id={client['id']}")

        for key in [
                "version", "layout",
        ]:
            await send_if_unsent(key)

        # Lexicon
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

    async def handle_new_connection_cb(self, reader, writer):  # pylint: disable=too-many-branches,too-many-statements
        """Handle a new client connection."""
        try:
            client_addr = writer.get_extra_info('peername')
            assert client_addr not in self.clients, f"Duplicate client ID {client_addr}"
            # Store the connection information for later
            self.clients[client_addr] = {
                'peername': client_addr,
                'ip': client_addr[0],
                'port': client_addr[1],
                'reader': reader,
                'writer': writer,
                'access': 'noaccess',
                'identifier': 'unknown',
                'id': self.next_client_id,
            }
            self.next_client_id += 1
            self.logger.info("Client connected: %s", client_addr)

            if client_addr[0] in self.allowed_clients:
                self.clients[client_addr]['access'] = self.allowed_clients[
                    client_addr[0]]['access']
                self.clients[client_addr]['identifier'] = self.allowed_clients[
                    client_addr[0]]['identifier']
                self.logger.info(
                    "Client identified as %s, access level %s",
                    self.clients[client_addr]['identifier'],
                    self.clients[client_addr]['access'],
                )
            else:
                self.logger.warning("Client not identified, closing connection")
                writer.write("unauthorized\n".encode())
                writer.close()
                await writer.wait_closed()
                del self.clients[client_addr]
                self.logger.info("Client %s was disconnected", client_addr)
                self.print_status()
                return
            if self.args.log_streams:
                logfile = os.path.join(
                    self.args.log_dir,
                    f"client-{self.start_time}-{client_addr[0]}-p{client_addr[1]}.psxnet.log"
                )
                self.stream_logfiles[client_addr] = open(logfile, 'a', encoding='utf-8')  # pylint: disable=consider-using-with

            self.print_status()
            await self.client_send_welcome(self.clients[client_addr])

            # Wait for data from client
            while self.client_connected(client_addr):
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
                    writer.close()
                    await writer.wait_closed()
                    del self.clients[client_addr]
                    self.logger.info("Client %s disconnected", client_addr)
                    self.print_status()
                    return
                self.logger.debug("From client %s: %s", client_addr, line)

                # Log data from client
                self.from_stream(self.clients[client_addr], line)

                key, sep, value = line.partition("=")
                if key in self.args.print_client_keywords:
                    self.logger.info("%s from %s: %s", key, client_addr, line)
                if self.args.print_client_non_psx:
                    if not re.match(REGEX_PSX_KEYWORDS, key):
                        self.logger.info("NONPSX %s from %s: %s", key, client_addr, line)

                # Handle name information from clients
                if key == 'name':
                    thisname = value
                    if re.match(r".*PSX.NET EFB.*", value):
                        thisname = value.split(":")[0]
                    elif re.match(r":PSX Sounds", value):
                        thisname = "PSX Sounds"
                    # name=MSFS Router:PSX.NET Modules
                    elif re.match(r"^MSFS Router", value):
                        thisname = "MSFS Router"
                    elif re.match(r".*:franken.*.py", value):
                        thisname = value.split(":")[0]
                    self.clients[client_addr]['identifier'] = f"L:{thisname}"
                    self.logger.info(
                        "Client %s identifies as %s, using that name",
                        self.clients[client_addr]['peername'], thisname)
                    self.print_status()

                # Router management via client commands
                if key == 'RouterStop':
                    self.logger.info("Got RouterStop command from %s", client_addr)
                    self.shutdown_requested = True
                    continue

                if self.clients[client_addr]['access'] == 'full':
                    if key in ["bang", "start", "again"]:
                        # Forward to server but not other clients
                        await self.send_to_server(key, client_addr)
                    elif key in ["nolong"]:
                        self.logger.warning("nolong not implemented, ignoring")
                    elif key in ["load1", "load2", "load3", "pleaseBeSoKindAndQuit"]:
                        # Forward to server and other clients
                        self.logger.info("%s from %s", key, client_addr)
                        await self.send_to_server(key, client_addr)
                        await self.client_broadcast(key, client_filter=[client_addr])
                    elif key in [
                            'exit',
                    ]:
                        # Shut down client connection cleanly
                        writer.close()
                        await writer.wait_closed()
                        del self.clients[client_addr]
                        self.logger.info("Client %s sent exit message, disconnecting", client_addr)
                        self.print_status()
                        return
                    elif sep != "":
                        self.state[key] = value
                        line = f"{key}={value}"
                        await self.send_to_server(line, client_addr)
                        await self.client_broadcast(line, client_filter=[client_addr])
                    else:
                        self.logger.warning("Unhandled data from client: %s", line)
                else:
                    self.logger.info(
                        "Read-only client tried to send data, ignoring: %s",
                        line
                    )
        except ConnectionResetError:
            self.logger.info("Connection reset by client %s", client_addr)
            # writer.close()
            # await writer.wait_closed()
            del self.clients[client_addr]

    async def client_broadcast(self, line, client_filter=None):
        """Send a line to all connected clients except the ones in the filter list."""
        for client in self.clients.values():
            if client_filter and client['peername'] in client_filter:
                self.logger.debug("Not sending to filtered client %s", client['peername'])
                continue
            await self.to_stream(client, line)
            self.logger.debug("To %s: %s", client['peername'], line)

    async def send_to_server(self, line, client_addr):
        """Send a line to the PSX main server."""
        if not self.server_connected():
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
            }
            self.logger.info("Connected to server: %s", server_addr)

            if self.args.log_streams:
                logfile = os.path.join(
                    self.args.log_dir,
                    f"server-{self.start_time}-{server_addr[0]}-p{server_addr[1]}.psxnet.log"
                )
                self.stream_logfiles[server_addr] = open(logfile, 'a', encoding='utf-8')  # pylint: disable=consider-using-with

            self.print_status()

            # Wait for and process data from server connection
            while self.server_connected():
                # We know the protocol is line-oriented and the lines will
                # not be too long to handle as a single unit, so we can
                # read one line at a time.
                try:
                    data = await reader.readline()
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    self.server = {}
                    self.logger.info(
                        "Server connection broke (%s), sleeping %.1f s before reconnect",
                        exc,
                        PSX_SERVER_RECONNECT_DELAY,
                    )
                    writer.close()
                    await writer.wait_closed()
                    self.print_status()
                    await asyncio.sleep(PSX_SERVER_RECONNECT_DELAY)
                    continue

                line = data.decode().strip()
                if line == '':
                    self.server = {}
                    self.logger.info(
                        "Server disconnected, sleeping %.1f s before reconnect",
                        PSX_SERVER_RECONNECT_DELAY,
                    )
                    writer.close()
                    await writer.wait_closed()
                    self.print_status()
                    await asyncio.sleep(PSX_SERVER_RECONNECT_DELAY)
                    continue

                self.logger.debug("From PSX server: %s", line)
                self.from_stream(self.server, line)

                # Store various things that we get e.g on initial
                # connection and that we might need later.
                key, sep, value = line.partition("=")

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
                    writer.close()
                    await writer.wait_closed()
                    self.print_status()
                    self.server = {}
                    await asyncio.sleep(PSX_SERVER_RECONNECT_DELAY)
                elif sep != "":
                    # Key-value message (including lexicon): store in
                    # state and send to connected clients
                    self.logger.debug("Storing key-value from server: %s=%s", key, value)
                    self.state[key] = value
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
            self.server['writer'].close()
            await self.server['writer'].wait_closed()
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
        while True:
            self.logger.debug(
                "MONITOR: clients: %d",
                len(self.clients)
            )
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
    #try:
    asyncio.run(me.main())
    #except Exception as exc:
    #    raise SystemExit(f"Caught exception: {exc}") from exc
