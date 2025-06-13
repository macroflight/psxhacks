"""A protocol-aware PSX router."""
# pylint: disable=invalid-name
import argparse
import asyncio
import datetime
import json
import logging
import os
import time

VERSION = '0.1'

PSX_SERVER_RECONNECT_DELAY = 1.0


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
        self.proxy_server = None
        self.state = None
        self.clients = {}
        self.server = {}
        self.stream_logfiles = {}
        self.start_time = int(time.time())

    def handle_args(self):
        """Handle command line arguments."""
        parser = argparse.ArgumentParser(
            prog='frankenrouter',
            description='A PSX router',
            epilog='Good luck!')
        parser.add_argument('--listen-port',
                            action='store', default=10748)
        parser.add_argument('--listen-host',
                            action='store', default=None)
        parser.add_argument('--psx-main-server-port',
                            action='store', default=10747)
        parser.add_argument('--psx-main-server-host',
                            action='store', default='127.0.0.1')
        parser.add_argument('--server-buffer-size', type=int,
                            action='store', default=65536)
        parser.add_argument('--state-cache-file',
                            action='store', default='frankenrouter.cache.json')
        parser.add_argument('--log-dir',
                            action='store', default='./')
        parser.add_argument('--log-streams',
                            action='store_true')
        parser.add_argument('--debug',
                            action='store_true')

        self.args = parser.parse_args()
        if self.args.debug:
            self.logger.setLevel(logging.DEBUG)

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
        serverinfo = "[---]"
        if self.server_connected():
            serverinfo = "[PSX]"

        self.logger.info(
            "%5s %2d clients, %3d variables",
            serverinfo,
            len(self.clients),
            len(self.state),
        )

    def to_stream(self, endpoint, line):
        """Write data to a stream and optionally to a log file."""
        # Write to stream
        endpoint['writer'].write(f"{line}\n".encode())
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

    def client_send_welcome(self, client):  # pylint: disable=too-many-branches
        """Send the same data as a real PSX server would send to a new client."""
        # If some mandatory variables are not yet received from the server, fake them
        sent = []

        def send_if_unsent(key):
            if key not in sent:
                if key not in self.state:
                    self.logger.warning(
                        "%s not found in self.state, client restart might be needed" +
                        " after server connection", key)
                    return
                line = f"{key}={self.state[key]}"
                self.to_stream(client, line)
                sent.append(key)
                self.logger.debug("To %s: %s", client['peername'], line)

        def send_unconditionally(key):
            line = f"{key}={self.state[key]}"
            self.to_stream(client, line)
            sent.append(key)
            self.logger.debug("To %s: %s", client['peername'], line)

        def send_line(line):
            self.to_stream(client, line)
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

        for key in [
                "id", "version", "layout",
        ]:
            send_if_unsent(key)

        # Lexicon
        for prefix in [
                "Ls",
                "Lh",
                "Li",
        ]:
            for key in self.state.keys():
                if key.startswith(prefix):
                    send_if_unsent(key)
        for prefix in [
                "Qi138",
                "Qs440",
                "Qs439",
                "Qs450",
        ]:
            for key in self.state.keys():
                if key == prefix:
                    send_if_unsent(key)
        send_line("load1")
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
                    send_if_unsent(key)
        send_line("load2")
        for prefix in [
                "Qi",
                "Qh",
                "Qs",
        ]:
            for key in self.state.keys():
                if key.startswith(prefix):
                    send_if_unsent(key)
        send_line("load3")
        send_if_unsent("metar")
        send_unconditionally("Qs124")
        send_unconditionally("Qs125")

    async def handle_new_connection_cb(self, reader, writer):
        """Handle a new client connection."""
        client_addr = writer.get_extra_info('peername')
        assert client_addr not in self.clients, f"Duplicate client ID {client_addr}"
        # Store the connection information for later
        self.clients[client_addr] = {
            'peername': client_addr,
            'reader': reader,
            'writer': writer,
        }
        self.logger.info("Client connected: %s", client_addr)

        if self.args.log_streams:
            logfile = os.path.join(
                self.args.log_dir,
                f"client-{self.start_time}-{client_addr[0]}-p{client_addr[1]}.psxnet.log"
            )
            self.stream_logfiles[client_addr] = open(logfile, 'a', encoding='utf-8')  # pylint: disable=consider-using-with

        self.print_status()
        self.client_send_welcome(self.clients[client_addr])

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
                del self.clients[client_addr]
                self.logger.info("Client %s disconnected", client_addr)
                self.print_status()
                return
            self.logger.debug("From client %s: %s", client_addr, line)

            # Log data from client
            self.from_stream(self.clients[client_addr], line)

            key, sep, value = line.partition("=")
            if key in ["bang", "start", "again"]:
                # Forward to server but not other clients
                self.send_to_server(key, client_addr)
            elif key in ["nolong"]:
                self.logger.warning("nolong not implemented, ignoring")
            elif key in ["load1", "load2", "load3", "pleaseBeSoKindAndQuit"]:
                # Forward to server and other clients
                self.logger.info("%s from %s", key, client_addr)
                self.send_to_server(key, client_addr)
                self.client_broadcast(key, client_filter=[client_addr])
            elif key in [
                    'exit',
            ]:
                # Shut down client connection cleanly
                writer.close()
                del self.clients[client_addr]
                self.logger.info("Client %s is disconnecting", client_addr)
                self.print_status()
                return
            elif sep != "":
                self.state[key] = value
                line = f"{key}={value}"
                self.send_to_server(line, client_addr)
                self.client_broadcast(line, client_filter=[client_addr])
            else:
                self.logger.warning("Unhandled data from client: %s", line)

    def client_broadcast(self, line, client_filter=None):
        """Send a line to all connected clients except the ones in the filter list."""
        for client in self.clients.values():
            if client_filter and client['peername'] in client_filter:
                self.logger.debug("Not sending to filtered client %s", client['peername'])
                continue
            self.to_stream(client, line)
            self.logger.debug("To %s: %s", client['peername'], line)

    def send_to_server(self, line, client_addr):
        """Send a line to the PSX main server."""
        if not self.server_connected():
            self.logger.warning("Server is disconnected, discarding: %s", line)
            return
        self.to_stream(self.server, line)
        self.logger.debug("To server from %s: %s", client_addr, line)

    async def handle_psx_server_connection(self):
        """Set up and maintain a PSX server connection."""
        while True:
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
                    self.print_status()
                    await asyncio.sleep(PSX_SERVER_RECONNECT_DELAY)
                    continue

                self.logger.debug("From PSX server: %s", line)
                self.from_stream(self.server, line)

                # Store various things that we get e.g on initial
                # connection and that we might need later.
                key, sep, value = line.partition("=")

                if key in [
                        'load1',
                        'load2',
                        'load3',
                ]:
                    # Load messages: send to connected clients
                    self.logger.info("From PSX: %s", key)
                    self.client_broadcast(line)
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
                        "Server disconnecting, sleeping %.1f s before reconnect",
                        PSX_SERVER_RECONNECT_DELAY,
                    )
                    self.print_status()
                    self.server = {}
                    await asyncio.sleep(PSX_SERVER_RECONNECT_DELAY)
                elif sep != "":
                    # Key-value message (including lexicon): store in
                    # state and send to connected clients
                    self.logger.debug("Storing key-value from server: %s=%s", key, value)
                    self.state[key] = value
                    self.client_broadcast(line)
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

    def shutdown(self):
        """Shut down the proxy."""
        self.logger.info("Shutting down")
        self.write_cache()

    async def main(self):
        """Start the proxy."""
        self.handle_args()
        self.read_cache()
        self.logger.info("frankenusb version %s starting", VERSION)
        self.proxy_server = await asyncio.start_server(
            self.handle_new_connection_cb,
            host=self.args.listen_host,
            port=self.args.listen_port,
            limit=self.args.server_buffer_size,
        )
        self.logger.info("Listening on %s", self.proxy_server.sockets[0].getsockname())
        self.print_status()

        await self.handle_psx_server_connection()

        # Wait for connections
        await asyncio.sleep(99999999)  # find a better way


if __name__ == '__main__':
    me = Frankenrouter()
    try:
        asyncio.run(me.main())
    except KeyboardInterrupt:
        me.shutdown()
