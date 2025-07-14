"""A protocol-aware PSX router."""
# pylint: disable=invalid-name,too-many-lines,fixme
from __future__ import annotations
import argparse
import asyncio
import itertools
import json
import logging
import logging.handlers
import math
import os
import pathlib
import random
import re
import statistics
import string
import time
import traceback
import queue

from aiohttp import web  # pylint: disable=import-error

from frankenrouter import config
from frankenrouter import connection
from frankenrouter import variables
from frankenrouter import routercache

__MYNAME__ = 'frankenrouter'
__MY_DESCRIPTION__ = 'A PSX Router'

VERSION = '0.6'

# If we have no upstream connection and no cached data, assume this
# version.
PSX_DEFAULT_VERSION = '10.182 NG'

# How long we wait for the upstream connection before accepting clients
# and serving them cached data.
UPSTREAM_WAITFOR = 5.0

# Status display static config
HEADER_LINE_LENGTH = 120
DISPLAY_NAME_MAXLEN = 24

# How often to check the RTT to upstream and client frankenrouters
FDRP_PING_INTERVAL = 5.0

# Keep 300 seconds of RTT data for the statistics in the status display
FDRP_KEEP_RTT_SAMPLES = 300

# A PSX main server instance in cruise will generate ~12 messages per
# second (measured during 1.5h flight). Keeping data on the last 10000
# messages will give us 1-15 minutes of data, should be enough.
VARIABLE_STATS_BUFFER_SIZE = 10000


class Frankenrouter():  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """Replaces the PSX USB subsystem."""

    def __init__(self):
        """Initialize the class."""
        self.args = None
        self.config = None
        self.logger = None
        self.traffic_logger = None
        self.variables = None
        self.cache = None
        self.messagequeue = asyncio.Queue(maxsize=0)
        self.taskgroup = None
        self.shutting_down = False
        self.tasks = set()
        # The toplevel coroutines we will be running
        self.bootstrap_task = 'Router Monitor'
        self.subsystems = {
            'Router Monitor': {
                'func': self.monitor_task,
            },
            'Client Listener': {
                'func': self.listener_task,
            },
            'Upstream Connector': {
                'func': self.upstream_connector_task,
            },
            'Forwarder': {
                'func': self.forwarder_task,
            },
            'FRDP Sender': {
                'func': self.frdp_send_task,
            },
            'Status Display': {
                'func': self.status_display_task,
            },
            'Housekeeping': {
                'func': self.housekeeping_task,
            },
            'REST API': {
                'func': self.api_task,
            },
        }
        self.clients = {}
        self.upstream = None
        self.upstream_pending_messages = []
        self.upstream_connections = 0
        self.log_traffic_filename = None
        self.start_time = int(time.time())
        self.allowed_clients = {}
        self.proxy_server = None
        self.api_server = None
        self.next_client_id = 1
        self.starttime = time.perf_counter()
        self.status_display_requested = False
        self.upstream_reconnect_requested = False
        self.longest_destination_string = 0
        self.variable_stats_buffer = []

        # Track when we last send the start keyword upstream
        self.start_sent_at = 0.0

        # Track when we last started welcoming a client. We can then
        # use this timestamp to e.g filter out variables that some
        # clients might be sensitive to receiving while running
        # normally.
        self.last_client_connected = 0.0

    def request_status_display(self):
        """Reqeust a status display update."""
        self.status_display_requested = asyncio.current_task().get_name()

    def handle_args(self):  # pylint: disable=too-many-statements
        """Handle command line arguments."""
        parser = argparse.ArgumentParser(
            prog='frankenrouter',
            description='A PSX router',
            formatter_class=argparse.ArgumentDefaultsHelpFormatter,
            epilog='Good luck!')
        parser.add_argument(
            '--config-file', '-f',
            type=pathlib.Path,
            action='store', default="frankenrouter.toml",
            help="The router config file",
        )
        parser.add_argument(
            '--read-buffer-size', type=int,
            action='store', default=1048576)
        parser.add_argument(
            '--upstream-reconnect-delay', type=float,
            action='store', default=1.0,
            help="How long to wait between upstream connection attempts.")
        parser.add_argument(
            '--status-interval',
            type=int, action='store', default=10,
            help="How often to print router status to terminal",
        )
        parser.add_argument(
            '--housekeeping-interval',
            type=int, action='store', default=30,
            help="How often to perform housekeeping tasks (writing cache to file, etc.) (s)",
        )
        parser.add_argument(
            '--state-cache-file',
            type=str, action='store', default="AUTO",
            help=(
                "This file contains PSX state that is automatically read on startup" +
                " and used until we have connected to the upstream router or PSX main" +
                " server. We also save the current state to this file on shutdown."
            ),
        )
        parser.add_argument(
            '--no-state-cache-file',
            action='store_true',
            help=(
                "Do not read the cached data on startup. In this case, the router" +
                " will only provide a fake client ID and PSX version to clients that" +
                " connect before it has connected to the PSX main server."
            ),
        )
        parser.add_argument(
            '--debug',
            action='store_true',
            help="Print more debug info. Probably only useful for development.",
        )
        parser.add_argument(
            '--forward-please-be-so-kind-and-quit-upstream',
            action='store_true',
            help=(
                "Forward pleaseBeSoKindAndQuit to upstream router. Use this with"
                " caution in shared cockpit setups."
            ),
        )
        parser.add_argument(
            '--log-traffic',
            action='store', type=bool, default=None,
            help=("Override config file setting for data logging")
        )
        parser.add_argument(
            '--enable-variable-stats',
            action='store', type=bool, default=None,
            help="Enable variable stats (experimental)",
        )
        parser.add_argument(
            '--no-pause-clients',
            action='store_true',
        )
        self.args = parser.parse_args()

    def get_random_id(self, length=16):
        """Return a random string we can use for FRDP request id."""
        return ''.join(
            random.choices(string.ascii_letters + string.digits, k=length))

    def is_upstream_connected(self):
        """Return True if we are connected to upstream."""
        if self.upstream is not None:
            return True
        return False

    def is_client_connected(self, client_addr):
        """Return True if this client is connected."""
        if client_addr in self.clients:
            return True
        return False

    def print_status(self):
        """Print a multi-line status message."""
        # No complicated status output when we're shutting down
        self.logger.info("-" * HEADER_LINE_LENGTH)
        self.logger.info(
            ("Router \"%s\" port %d, %d msgs in queue, uptime %d s" +
             ", API port %s, cache=%s"),
            self.config.identity.simulator, self.config.listen.port, self.messagequeue.qsize(),
            int(time.perf_counter() - self.starttime),
            self.config.listen.rest_api_port, self.cache.get_size(),
        )
        self.logger.info("Press Ctrl-C to shut down cleanly")
        if self.log_traffic_filename:
            self.logger.info("Logging traffic to %s", self.log_traffic_filename)
        upstreaminfo = "[NO UPSTREAM CONNECTION]"
        if self.is_upstream_connected():
            upstreaminfo = f"UPSTREAM {self.upstream.ip}:{self.upstream.port}"
            upstreaminfo += f" {self.upstream.display_name}"
            if len(self.upstream.frdp_ping_rtts) > 0:
                # Keep the last N samples
                self.upstream.frdp_ping_rtts = self.upstream.frdp_ping_rtts[-FDRP_KEEP_RTT_SAMPLES:]
                ping_rtt_median = statistics.median(self.upstream.frdp_ping_rtts)
                ping_rtt_max = max(self.upstream.frdp_ping_rtts)
                upstreaminfo = upstreaminfo + f", FRDP RTT median/max: {(ping_rtt_median * 1000):.1f}/{(ping_rtt_max * 1000):.1f} ms"  # pylint: disable=line-too-long
            if self.upstream_connections > 1:
                upstreaminfo = upstreaminfo + f", {self.upstream_connections - 1} reconnections"
        self.logger.info(upstreaminfo)
        self.logger.info(
            "%-21s %-23s %5s %8s %7s %6s %6s %9s %9s %5s",
            f"{len(self.clients)} clients",
            "",
            "Local",
            "",
            "",
            "Lines",
            "Lines",
            "Bytes",
            "Bytes",
            "FRDP RTT ms",
        )
        self.logger.info(
            "%2s %-26s %-15s %5s %8s %7s %6s %6s %9s %9s %5s %5s",
            "id",
            "Name",
            "Client IP",
            "Port",
            "Access",
            "Clients",
            "sent",
            "recvd",
            "sent",
            "recvd",
            "median",
            "max",
        )
        for data in self.clients.values():
            ping_rtt_median = "-"
            ping_rtt_max = "-"
            if len(data.frdp_ping_rtts) > 0:
                data.frdp_ping_rtts = data.frdp_ping_rtts[-FDRP_KEEP_RTT_SAMPLES:]
                ping_rtt_median = f"{(1000.0 * statistics.median(data.frdp_ping_rtts)):.1f}"
                ping_rtt_max = f"{(1000.0 * max(data.frdp_ping_rtts)):.1f}"

            self.logger.info(
                "%2d %-26s %-15s %5d %8s %7d %6d %6d %9d %9d %5s %5s",
                data.client_id,
                data.display_name,
                data.ip,
                data.port,
                data.access_level,
                data.connected_clients,
                data.messages_sent,
                data.messages_received,
                data.bytes_sent,
                data.bytes_received,
                ping_rtt_median,
                ping_rtt_max,
            )
        self.logger.info("-" * HEADER_LINE_LENGTH)

    async def log_connect_evt(self, peername, clientid=None, disconnect=False):
        """Log connection to optional log file."""
        if not self.config.log.traffic:
            return
        if clientid is None:
            self.traffic_logger.info(
                "%s UPSTREAM %s",
                "DISCONNECT" if disconnect else "CONNECT",
                peername)
        else:
            self.traffic_logger.info(
                "%s client %d %s",
                "DISCONNECT" if disconnect else "CONNECT",
                clientid, peername)

    def variable_stats_add(self, key, endpoint):
        """Store the reception of a variable in the stats buffer."""
        if not self.args.enable_variable_stats:
            return
        self.variable_stats_buffer.append(
            {
                'keyword': key,
                'endpoint': endpoint,
            }
        )

    async def log_traffic(self, line, endpoints=None, inbound=True):
        """Write to optional log file."""

        def make_clientlist(clients):
            """Make a compact client list from a list of integers."""
            def clientranges(i):
                for _, b in itertools.groupby(enumerate(i), lambda pair: pair[1] - pair[0]):
                    b = list(b)
                    yield b[0][1], b[-1][1]
            out = []
            for clientrange in clientranges(clients):
                if clientrange[0] == clientrange[1]:
                    out.append(f"{clientrange[0]}")
                else:
                    out.append(f"{clientrange[0]}-{clientrange[1]}")
            return ",".join(out)

        if not self.config.log.traffic:
            return
        # Normal traffic data
        if endpoints is None:
            # Data from upstream
            description = "upstream"
        else:
            description = make_clientlist(endpoints)
        direction = 'DATA TO  '
        if inbound:
            direction = 'DATA FROM'
        fmt = f"%s [%-{self.longest_destination_string}s] %s"
        if len(description) > self.longest_destination_string:
            # make it longer then
            self.longest_destination_string = len(description)
            description = description[:(self.longest_destination_string - 3)] + "..."
        self.traffic_logger.info(fmt, direction, description, line)

    async def client_add_to_network(self, client):  # pylint: disable=too-many-branches,too-many-statements,too-many-locals
        """Add a client to the network."""
        self.logger.info(
            "Adding client %s to network (%d keywords)",
            client.client_id, self.cache.get_size())
        start_time = time.perf_counter()
        self.last_client_connected = start_time

        async def send_if_unsent(key):
            if not self.cache.has_keyword(key):
                self.logger.info("Keyword %s not in cache, cannot send", key)
                return
            if key in self.variables.keywords_with_mode('DELTA'):
                self.logger.debug(
                    "Not sending DELTA variable %s to client", key)
                return
            if key not in client.welcome_keywords_sent:
                cached_value = self.cache.get_value(key)
                if cached_value is None:
                    self.logger.warning(
                        "%s not found in router cache, client restart might be needed" +
                        " after upstream connection", key)
                    return
                line = f"{key}={cached_value}"
                await client.to_stream(line)
                client.welcome_keywords_sent.add(key)
                self.logger.debug("To %s: %s", client.peername, line)

        async def send_line(line):
            await client.to_stream(line)
            self.logger.debug("To %s: %s", client.peername, line)

        #
        # See notes_on_psx_router.md for notes on what we send here and why
        #

        # Send a "fake" client id, rather than the id of the proxy's
        # connection to the PSX main server.
        await send_line(f"id={client.client_id}")

        # Send version and layout. If possible, use cache, it not,
        # make something up. Without at least version, PSX main
        # clients will not connect.
        if not self.cache.has_keyword('version'):
            self.cache.update('version', PSX_DEFAULT_VERSION)
        if not self.cache.has_keyword('layout'):
            self.cache.update('layout', 1)
        for key in [
                "version", "layout",
        ]:
            await send_if_unsent(key)

        # Send the Lexicon
        for prefix in [
                "Ls",
                "Lh",
                "Li",
        ]:
            for key in self.cache.get_keywords():
                if key.startswith(prefix):
                    await send_if_unsent(key)

        # FIXME: some variables always sent BEFORE load1?
        # "Qi138", "Qs440", "Qs439","Qs450",

        await send_line("load1")

        # Send "start" upstream
        self.logger.debug("Sending start upstream to get fresh data for client")
        client.waiting_for_start_keywords = True
        await self.send_to_upstream("start")
        self.start_sent_at = time.perf_counter()
        all_start_keywords = set(self.variables.keywords_with_mode('START'))
        all_econ_keywords = set(self.variables.keywords_with_mode('ECON'))
        expected_start_keywords = all_start_keywords - all_econ_keywords
        while True:
            await asyncio.sleep(0.010)
            now = time.perf_counter()
            missing = len(expected_start_keywords - client.welcome_keywords_sent)
            if missing <= 0:
                self.logger.debug("All expected START keywords received, continuing")
                break
            waited = now - self.start_sent_at
            if waited > 1.0:
                self.logger.warning(
                    "Waited %.1f s for START data, missing %d of %d (%s), continuing anyway",
                    waited, missing,
                    len(expected_start_keywords),
                    expected_start_keywords - client.welcome_keywords_sent)
                break
        client.waiting_for_start_keywords = False

        #
        # Send all other unsent keywords
        #

        if self.cache.get_size() < 10:
            self.logger.warning(
                "Router cache probably not initialized, some clients might misbehave")

        # Loop over the list [ "Qi0", "Qi1", ..., "Qi31" ]
        for keyword in list(map(lambda x: f"Qi{x}", list(range(0, 32)))):
            await send_if_unsent(keyword)

        await send_line("load2")
        for prefix in [
                "Qi",
                "Qh",
                "Qs",
        ]:
            for key in self.variables.sort_psx_keywords(self.cache.get_keywords()):
                if key.startswith(prefix):
                    await send_if_unsent(key)

        await send_line("load3")
        await send_if_unsent("metar")

        client.welcome_sent = True
        welcome_keyword_count = len(client.welcome_keywords_sent)
        client.welcome_keywords_sent = set()

        # Send pending messages
        if len(client.pending_messages) > 0:
            self.logger.debug(
                "Sending %d held messages to client",
                len(client.pending_messages)
            )
            for message in client.pending_messages:
                await send_line(message)
        client.pending_messages = []

        # Identify ourselves to the client (in case it's another
        # frankenrouter)
        await send_line(f"name=frankenrouter:{self.config.identity.simulator}")

        elapsed = time.perf_counter() - start_time
        self.logger.info(
            "Added client %s in %.1f ms (%d keywords, %.0f/s)",
            client.client_id, elapsed * 1000,
            welcome_keyword_count, welcome_keyword_count / elapsed)

    async def close_client_connection(self, client, clean=True):
        """Close a client connection and remove client data."""
        if client.is_closing:
            self.logger.debug("Client connection already closing: %s", client.peername)
            return
        client.is_closing = True
        self.logger.debug("Closing client connection %s cleanly", client.peername)
        # Send "exit" and close connection
        await client.close(clean)
        # Destroy client connection object and log
        await self.log_connect_evt(client.peername, clientid=client.client_id, disconnect=True)
        del self.clients[client.peername]
        self.logger.info("Client connection %s closed", client.peername)
        self.request_status_display()

    async def close_upstream_connection(self):
        """Close an upstream connection."""
        if self.upstream is None:
            self.logger.warning("Tried to close non-existant upstream connection")
            return
        peername = self.upstream.peername
        # Send "exit" and close connection
        await self.upstream.close()
        # Destroy upstream connection object
        await self.log_connect_evt(peername, disconnect=True)
        self.upstream = None
        self.logger.info("Closed upstream connection %s", peername)
        self.request_status_display()

    async def pause_clients(self):
        """Send load1 to pause clients."""
        if self.args.no_pause_clients:
            self.logger.info("Pausing clients")
            await self.client_broadcast("load1")

    async def handle_new_connection_cb(self, reader, writer):  # pylint: disable=too-many-branches,too-many-statements,too-many-locals
        """Handle a new client connection."""
        # asyncio will intentionally not propagate exceptions from a
        # callback (see https://bugs.python.org/issue42526), so we
        # need to wrap the entire function in try-except.
        try:  # pylint: disable=too-many-nested-blocks
            # Create the connection object (which needs our config
            # and a reference to the log function)
            this_client = connection.ClientConnection(
                reader, writer,
                self.config, self.log_traffic)
            this_client.client_id = self.next_client_id
            self.next_client_id += 1
            self.clients[this_client.peername] = this_client
            # Set the name of this auto-generated Task
            asyncio.current_task().set_name(f"Client connection {this_client.peername}")
            self.logger.info("New client connection: %s", this_client.peername)
            await self.log_connect_evt(this_client.peername, clientid=this_client.client_id)

            if self.shutting_down:
                self.logger.warning("Is shutting down, rejecting new connection")
                await this_client.to_stream("shutdown in progress")
                await self.close_client_connection(this_client, clean=False)
                return

            if self.clients[this_client.peername].access_level == 'blocked':
                self.logger.warning(
                    "Blocked client %s connected, closing connection", this_client.ip)
                await this_client.to_stream("bye now")
                await self.close_client_connection(this_client, clean=False)
                return

            # Get the client's initial access level based on IP
            this_client.update_access_level()

            # New client connected, so print status
            self.request_status_display()

            # Add client to network (send welcome message, etc) if it
            # has access (i.e authenticated based on IP or password)
            if this_client.has_access():
                await self.client_add_to_network(this_client)
                this_client.welcome_sent = True

            # Wait for data from client
            while self.is_client_connected(this_client.peername):
                self.logger.debug("Waiting for data from client %s", this_client.peername)
                # We know the protocol is text-based, so we can use
                # readline()
                try:
                    data = await reader.readline()
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    await self.log_connect_evt(
                        this_client.peername, clientid=this_client.client_id, disconnect=True)
                    del self.clients[this_client.peername]
                    self.request_status_display()
                    self.logger.warning("Client connection broke (%s) for %s",
                                        exc, this_client.peername)
                    self.request_status_display()
                    return
                t_read_data = time.perf_counter()
                # The real PSX server will not close a client
                # connection when it gets an empty line, just show an
                # error in the GUI. But AFAIK no PSX addon will send
                # empty lines, to this seems like a simple way to
                # detect a connection closed by the client (which
                # seems to be somewhat hard...)
                if data == b"":
                    self.logger.info("Got empty bytes object from %s, closing connection",
                                     this_client.peername)
                    await self.close_client_connection(this_client, clean=False)
                    return
                # Note: we can get a partial line at EOF, so discard
                # data with no newline.
                if not data.endswith(b'\n'):
                    self.logger.warning(
                        "Got partial line data from %s, discarding: %s",
                        this_client.peername, data
                    )
                    continue
                # Put message in queue
                await self.messagequeue.put({
                    'payload': data,
                    'received_time': t_read_data,
                    'sender': this_client.peername,
                })
        # Standard Task cleanup
        except asyncio.exceptions.CancelledError:
            self.logger.info(
                "Client %s handler was cancelled, cleanup and exit",
                this_client.peername)
            await self.close_client_connection(this_client, clean=True)
            self.logger.info("Client %s connection closed", this_client.peername)
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s handler, shutting down",
                                 exc, this_client.peername)
            self.logger.critical(traceback.format_exc())
            await self.close_client_connection(this_client, clean=True)
            self.request_status_display()
            self.logger.info("Connection %s shut down", this_client.peername)
            return
        # END OF handle_new_connection_cb

    async def client_broadcast(
            self, line, exclude=None, include=None,
            islong=False, isonlystart=False,
            key=None,
    ):  # pylint: disable=too-many-branches, too-many-arguments, too-many-positional-arguments
        """Send a line to connected clients.

        If exclude is provided, send to all connected clients except
        clients in that list.

        If include is provided, send to those clients.
        """
        if exclude and include:
            self.logger.critical(
                "client_broadcast called with both include and exclude - not supported")
            return

        send_to_clients = []

        for client in self.clients.values():  # pylint: disable=too-many-nested-blocks
            if not client.has_access():
                self.logger.debug(
                    "Not sending to noaccess client %s", client.peername)
                continue
            if exclude and client.peername in exclude:
                self.logger.debug(
                    "Not sending to excluded client %s", client.peername)
                continue
            if include and client.peername not in include:
                self.logger.debug(
                    "Not sending to non-included client %s", client.peername)
                continue
            if islong and client.nolong:
                self.logger.debug(
                    "Not sending long string to nolong client %s: %s",
                    client.peername, line)
                continue
            if isonlystart:
                self.logger.debug("isonlystart for %s", client.peername)
                if client.is_frankenrouter:
                    # send
                    pass
                elif client.waiting_for_start_keywords:
                    # send
                    client.welcome_keywords_sent.add(key)
                else:
                    self.logger.debug(
                        "Not sending START variable to %s: %s",
                        client.peername, line)
                    continue
            if not client.welcome_sent:
                self.logger.debug("Client %s not welcomed, adding to pending_messages: %s",
                                  client.peername, line)
                client.pending_messages.append(line)
                continue
            send_to_clients.append(client)

        writes = []
        sent_to_clients = []
        if len(send_to_clients) > 0:
            for client in send_to_clients:
                writes.append(client.to_stream(line, log=False))
                sent_to_clients.append(client.client_id)
            await asyncio.gather(*writes, return_exceptions=True)

        # Log traffic
        if len(sent_to_clients) > 0:
            await self.log_traffic(line, sent_to_clients, inbound=False)

    async def send_to_upstream(self, line, client_addr=None):
        """Send a line to upstream."""
        if not self.is_upstream_connected():
            self.upstream_pending_messages.append(line)
            self.logger.info(
                "Upstream is not connected, storing data for later send (%d entries): %s",
                len(self.upstream_pending_messages), line)
            return
        await self.upstream.to_stream(line, log=True)
        self.logger.debug("To upstream from %s: %s", client_addr, line)

    async def upstream_connector_task(self, name):  # pylint: disable=too-many-branches,too-many-statements,too-many-locals
        """Upstream connector Task."""
        try:  # pylint: disable=too-many-nested-blocks
            while True:  # pylint: disable=too-many-nested-blocks
                try:
                    reader, writer = await asyncio.open_connection(
                        self.config.upstream.host,
                        self.config.upstream.port,
                        limit=self.args.read_buffer_size,
                    )
                # At least on Windows we get OSError after ~30s if the
                # upstream is down or unreachable
                except (ConnectionRefusedError, OSError):
                    self.logger.warning(
                        "Upstream connection refused, sleeping %.1f s before retry",
                        self.args.upstream_reconnect_delay,
                    )
                    await asyncio.sleep(self.args.upstream_reconnect_delay)
                    continue
                # Create the connection object (which needs our config
                # and a reference to the log function)
                self.upstream = connection.UpstreamConnection(
                    reader, writer,
                    self.config, self.log_traffic)
                self.upstream_connections += 1
                self.logger.info("Connected to upstream: %s", self.upstream.peername)
                await self.log_connect_evt(self.upstream.peername)

                if self.config.upstream.password:
                    # assume upstream is frankenrouter if we use --password
                    self.logger.info("Assuming upstream is frankenrouter")
                    self.upstream.is_frankenrouter = True

                self.request_status_display()

                # Send our name (for when we connect to another router)
                await self.send_to_upstream(f"name={self.config.identity.router}:FRANKEN.PY frankenrouter PSX router {self.config.identity.router} in {self.config.identity.simulator}")  # pylint: disable=line-too-long

                # (re)Send demand= for all keywords that any client has demanded
                clients_demand = set()
                for peername, data in self.clients.items():
                    for demand_var in data.demands:
                        self.logger.debug(
                            "Adding demand variable %s from %s to req list",
                            demand_var, peername)
                        clients_demand.add(demand_var)
                for demand_var in clients_demand:
                    self.logger.debug("Sending demand=%s to upstream")
                    await self.send_to_upstream(f"demand={demand_var}")

                if len(self.upstream_pending_messages) > 0:
                    self.logger.debug(
                        "Sending %d held messages to upstream",
                        len(self.upstream_pending_messages)
                    )
                    for message in self.upstream_pending_messages:
                        await self.send_to_upstream(message)
                self.upstream_pending_messages = []

                self.request_status_display()

                # Wait for and process data from upstream connection
                while self.is_upstream_connected():
                    # We know the protocol is line-oriented and the lines will
                    # not be too long to handle as a single unit, so we can
                    # read one line at a time.
                    try:
                        data = await reader.readline()
                    except Exception as exc:  # pylint: disable=broad-exception-caught
                        self.logger.info(
                            "Upstream connection broke (%s), sleeping %.1f s before reconnect",
                            exc,
                            self.args.upstream_reconnect_delay,
                        )
                        await self.close_upstream_connection()
                        await asyncio.sleep(self.args.upstream_reconnect_delay)
                        continue
                    t_read_data = time.perf_counter()
                    if data == b'':
                        self.logger.info(
                            "Upstream disconnected, sleeping %.1f s before reconnect",
                            self.args.upstream_reconnect_delay,
                        )
                        await self.close_upstream_connection()
                        await asyncio.sleep(self.args.upstream_reconnect_delay)
                        break
                    # Note: we can get a partial line at EOF, so discard
                    # data with no newline.
                    if not data.endswith(b'\n'):
                        self.logger.warning(
                            "Got partial line data from upstream, discarding: %s", data)
                        continue
                    await self.messagequeue.put({
                        'payload': data,
                        'received_time': t_read_data,
                        'sender': None,
                    })
                # Pause clients when we have no upstream connection
                await self.pause_clients()
        # Standard Task cleanup
        except asyncio.exceptions.CancelledError:
            self.logger.info("Task %s was cancelled, cleanup and exit", name)
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down",
                                 exc, name)
            self.logger.critical(traceback.format_exc())
            return
        # End of upstream_connector_task()

    async def listener_task(self, name):
        """Run the client listener."""
        try:
            # Wait a while for the upstream connection. We prefer to give
            # the clients the real data, not old cached variables.
            started_waiting = time.perf_counter()
            while not self.is_upstream_connected():
                if time.perf_counter() - started_waiting > UPSTREAM_WAITFOR:
                    self.logger.info(
                        "Gave up waiting for upstream connection, will serve cached data")
                    break
                self.logger.info("Upstream not connected, not listening yet...")
                await asyncio.sleep(1.0)

                self.proxy_server = await asyncio.start_server(
                    self.handle_new_connection_cb,
                    port=self.config.listen.port,
                    limit=self.args.read_buffer_size
                )
                while True:  # wait forever
                    await asyncio.sleep(3600.0)
        # Task cleanup: close connections cleanly
        except asyncio.exceptions.CancelledError:
            self.shutting_down = True  # don't accept new connections
            self.logger.info("Proxy server shutting down its client connections")
            closures = []
            # Pause clients before closing connections
            await self.pause_clients()
            for this_client in self.clients.values():
                closures.append(self.close_client_connection(this_client, clean=True))
            await asyncio.gather(*closures)
            await asyncio.sleep(1.0)
            self.logger.info("Proxy server shutting down itself")
            self.proxy_server.close()
            await self.proxy_server.wait_closed()
            self.proxy_server = None
            self.clients = {}
            self.logger.info("Proxy server shut down")
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down",
                                 exc, name)
            self.logger.critical(traceback.format_exc())
            self.proxy_server.close()
            await self.proxy_server.wait_closed()
            self.proxy_server = None
            self.clients = {}
            return
        # End of listener_task()

    def print_variable_stats(self):
        """Display stats for variables."""
        if not self.args.enable_variable_stats:
            return
        messages_by_keyword = {}
        messages_by_endpoint = {}
        for message in self.variable_stats_buffer:
            if message['keyword'] not in messages_by_keyword:
                messages_by_keyword[message['keyword']] = 0
            messages_by_keyword[message['keyword']] += 1
            if message['endpoint'] not in messages_by_endpoint:
                messages_by_endpoint[message['endpoint']] = 0
            messages_by_endpoint[message['endpoint']] += 1

        self.logger.info("Top-5 received messages by keyword:")
        keywords_sorted = sorted(
            messages_by_keyword.items(),
            key=lambda elem: elem[1], reverse=True)
        for keyword, count in keywords_sorted[:5]:
            self.logger.info("%8s - %6d messages",
                             keyword,
                             count,
                             )

        self.logger.info("Top-5 received messages by endpoint:")
        endpoints_sorted = sorted(
            messages_by_endpoint.items(),
            key=lambda elem: elem[1], reverse=True)
        for endpoint, count in endpoints_sorted[:5]:
            self.logger.info("%32s - %6d messages",
                             "upstream" if endpoint is None else endpoint,
                             count,
                             )

    def print_aircraft_status(self):
        """Display a basic aircraft status line to verify sane data."""
        try:
            acft_state = self.cache.get_value('Qs121')
        except routercache.RouterCacheException:
            return
        if acft_state is not None:
            PiBaHeAlTas = acft_state.split(';')
            pitch = math.degrees(float(PiBaHeAlTas[0]) / 1000000)
            bank = math.degrees(float(PiBaHeAlTas[1]) / 1000000)
            heading_true = math.degrees(float(PiBaHeAlTas[2]))
            alt_true_ft = float(PiBaHeAlTas[3]) / 1000
            tas = float(PiBaHeAlTas[4]) / 1000
            lat = math.degrees(float(PiBaHeAlTas[5]))
            lon = math.degrees(float(PiBaHeAlTas[6]))
            self.logger.info(
                "pitch=%.1f bank=%.1f heading=%.0f altitude_true=%.0f TAS=%.0f lat=%.6f lon=%.6f",
                pitch, bank, heading_true, alt_true_ft, tas, lat, lon
            )

    async def logging_task(self, name):  # pylint: disable=too-many-branches
        """Handle application logging."""
        try:
            router_log_file = os.path.join(
                self.config.log.directory,
                f"frankenrouter-{self.config.identity.router}.log"
            )
            if os.path.exists(router_log_file):
                if os.path.exists(router_log_file + ".OLD"):
                    os.unlink(router_log_file + ".OLD")
                os.rename(router_log_file, router_log_file + ".OLD")

            self.logger = logging.getLogger(__MYNAME__)

            log_queue = queue.Queue(maxsize=0)

            queue_handler = logging.handlers.QueueHandler(log_queue)
            self.logger.addHandler(queue_handler)

            console_formatter = logging.Formatter("%(asctime)s: %(message)s", datefmt="%H:%M:%S")
            file_formatter = logging.Formatter("%(asctime)s: %(message)s")

            console_handler = logging.StreamHandler()
            console_handler.setFormatter(console_formatter)

            file_handler = logging.FileHandler(router_log_file)
            file_handler.setFormatter(file_formatter)

            self.logger.setLevel(logging.INFO)
            if self.args.debug:
                self.logger.setLevel(logging.DEBUG)

            listener = logging.handlers.QueueListener(log_queue, console_handler, file_handler)

            print('Starting logger')
            # start the listener
            listener.start()
            # report the logger is ready
            self.logger.debug("Task %s has initialized logging", name)
            # wait forever
            while True:
                await asyncio.sleep(60)

        # Keep logger alive until all other tasks have ended (or max
        # 10s), otherwise we won't see the logs from their shutdown.
        except asyncio.exceptions.CancelledError:
            self.logger.info("Logging task waiting for other tasks to exit...")
            logger_cancelled_at = time.perf_counter()
            while True:
                await asyncio.sleep(0.1)
                tasks_ended = set()
                for task in self.tasks:
                    if task.done():
                        tasks_ended.add(task)
                for task in tasks_ended:
                    self.tasks.discard(task)
                if len(self.tasks) <= 1:
                    self.logger.info("Logging task shutting down")
                    await asyncio.sleep(1.0)
                    raise
                self.logger.debug("Logging task delaying shutdown")
                if (time.perf_counter() - logger_cancelled_at) > 10.0:
                    self.logger.info("Logging task gave up waiting, shutting down NOW")
                    await asyncio.sleep(1.0)
                    raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down",
                                 exc, name)
            self.logger.critical(traceback.format_exc())
            return
        # End of logging_task()

    async def traffic_logging_task(self, name):
        """Handle traffic logging."""
        try:
            # If using traffic logging, open that log file
            self.log_traffic_filename = os.path.join(
                self.config.log.directory,
                f"{self.config.identity.router}-traffic-{self.start_time}.psxnet.log"
            )

            self.traffic_logger = logging.getLogger(f"{__MYNAME__}-traffic")

            log_queue = queue.Queue(maxsize=0)

            queue_handler = logging.handlers.QueueHandler(log_queue)
            self.traffic_logger.addHandler(queue_handler)

            file_formatter = logging.Formatter("%(asctime)s: %(message)s")

            file_handler = logging.FileHandler(self.log_traffic_filename)
            file_handler.setFormatter(file_formatter)

            self.traffic_logger.setLevel(logging.DEBUG)

            listener = logging.handlers.QueueListener(log_queue, file_handler)

            try:
                print('Starting traffic logger')
                # start the listener
                listener.start()
                # report the logger is ready
                self.logger.debug("Task %s has initialized traffic logging", name)
                # wait forever
                while True:
                    await asyncio.sleep(60)
            finally:
                # report the logger is done
                self.logger.debug("Task %s has stopped traffic logging", name)
                # ensure the listener is closed
                listener.stop()
        # Standard Task cleanup
        except asyncio.exceptions.CancelledError:
            self.logger.info("Task %s was cancelled, cleanup and exit", name)
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down",
                                 exc, name)
            self.logger.critical(traceback.format_exc())
            return
        # End of traffic_logging_task()

    async def frdp_send_task(self, name):
        """Handle sending FRDP messages."""
        try:
            last_ping = time.perf_counter()
            while True:
                await asyncio.sleep(1.0)
                #
                # FRDP PING
                #
                elapsed_since_ping = time.perf_counter() - last_ping
                if elapsed_since_ping > FDRP_PING_INTERVAL:
                    #
                    # Send FRDP ping to upstream if it is a frankenrouter
                    #
                    if self.is_upstream_connected() and self.upstream.is_frankenrouter:
                        frdp_request_id = self.get_random_id()
                        self.logger.debug(
                            "Sending FRDP ping to upstream, request_id is %s",
                            frdp_request_id)
                        await self.send_to_upstream(
                            f"addon=FRANKENROUTER:PING:{frdp_request_id}")
                        self.upstream.ping_sent = time.perf_counter()
                        self.upstream.frdp_ping_request_id = frdp_request_id
                    #
                    # Send FRDP ping to any frankenrouter clients
                    #
                    sendto = []
                    for peername, data in self.clients.items():
                        if data.is_frankenrouter:
                            frdp_request_id = self.get_random_id()
                            self.logger.debug(
                                "Sending FRDP ping to client %s, request_id is %s",
                                peername, frdp_request_id)
                            sendto.append(peername)
                            await self.client_broadcast(
                                f"addon=FRANKENROUTER:PING:{frdp_request_id}",
                                include=[peername])
                            data.ping_sent = time.perf_counter()
                            data.frdp_ping_request_id = frdp_request_id
                    # Update timestamp
                    last_ping = time.perf_counter()
                else:
                    self.logger.debug("Only %.3f s since ping", elapsed_since_ping)
                #
                # FRDP IDENT
                #
                # We only want to send this upstream if connected to
                # another frankenrouter.
                if self.is_upstream_connected() and self.upstream.is_frankenrouter:
                    if not self.upstream.frdp_ident_sent:
                        self.logger.info("Sending FRDP IDENT to upstream")
                        await self.send_to_upstream(f"addon=FRANKENROUTER:IDENT:{self.config.identity.simulator}:{self.config.identity.router}")  # pylint: disable=line-too-long
                        self.upstream.frdp_ident_sent = True

                #
                # FRDP AUTH
                #
                # We only want to send this upstream if connected to
                # another frankenrouter.
                if self.is_upstream_connected() and self.upstream.is_frankenrouter:
                    if self.config.upstream.password and not self.upstream.frdp_auth_sent:
                        await self.send_to_upstream(f"addon=FRANKENROUTER:AUTH:{self.config.upstream.password}")  # pylint: disable=line-too-long
                        self.upstream.frdp_auth_sent = True

        # Standard Task cleanup
        except asyncio.exceptions.CancelledError:
            self.logger.info("Task %s was cancelled, cleanup and exit", name)
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down",
                                 exc, name)
            self.logger.critical(traceback.format_exc())
            return
        # End of frdp_send_task()

    def print_client_warnings(self):
        """Print warnings about unexpected client counts."""
        checks = self.config.check
        if checks is None:
            return
        for check in checks:
            count = 0
            if check.checktype == 'is_frankenrouter':
                for client in self.clients.values():
                    if client.is_frankenrouter:
                        count += 1
                if check.limit_min and count < check.limit_min:
                    self.logger.warning("WARNING: Too few (%d) frankenrouter clients!", count)
                if check.limit_max and count > check.limit_max:
                    self.logger.warning("WARNING: Too many (%d) frankenrouter clients!", count)
            elif check.checktype == 'name_regexp':
                regexp = check.regexp
                for client in self.clients.values():
                    if re.match(regexp, client.display_name):
                        count += 1
                if check.limit_min and count < check.limit_min:
                    self.logger.warning(
                        "WARNING: Too few (%d) clients matching %s found!", count, regexp)
                if check.limit_max and count > check.limit_max:
                    self.logger.warning(
                        "WARNING: Too many (%d) clients matching %s found!", count, regexp)

    async def status_display_task(self, name):
        """Status display Task."""
        try:
            last_display = 0.0
            while True:
                await asyncio.sleep(1.0)
                display = False
                if self.status_display_requested:
                    display = True
                    self.logger.debug(
                        "Status display refresh requested by %s",
                        self.status_display_requested)
                if time.perf_counter() - last_display > self.args.status_interval:
                    display = True
                if display:
                    self.print_status()
                    last_display = time.perf_counter()
                    self.status_display_requested = False
                    self.print_client_warnings()
                    self.print_aircraft_status()
                    self.print_variable_stats()
        # Standard Task cleanup
        except asyncio.exceptions.CancelledError:
            self.logger.info("Task %s was cancelled, cleanup and exit", name)
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down",
                                 exc, name)
            self.logger.critical(traceback.format_exc())
            return
        # End of status_display_task()

    async def housekeeping_task(self, name):
        """Miscellaneous housekeeping Task."""
        try:
            last_run = 0.0
            while True:
                await asyncio.sleep(1.0)
                if time.perf_counter() - last_run > self.args.housekeeping_interval:
                    last_run = time.perf_counter()
                    self.logger.debug("Performing housekeeping")
                    # Write chache to disk
                    self.cache.write_to_file()

                    # Trim variable stats buffer
                    if self.args.enable_variable_stats:
                        len_before = len(self.variable_stats_buffer)
                        self.variable_stats_buffer = (
                            self.variable_stats_buffer[-VARIABLE_STATS_BUFFER_SIZE:])
                        len_after = len(self.variable_stats_buffer)
                        if len_after < len_before:
                            self.logger.info(
                                "Housekeeping trimmed variable stats buffer from %d to %d",
                                len_before, len_after)
        # Standard Task cleanup
        except asyncio.exceptions.CancelledError:
            self.logger.info("Task %s was cancelled, cleanup and exit", name)
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down",
                                 exc, name)
            self.logger.critical(traceback.format_exc())
        # End of housekeeping_task()

    async def handle_message_from_upstream(self, message):  # pylint: disable=too-many-branches,too-many-statements
        """Handle a message from upstream."""
        line = message['payload'].decode().splitlines()[0]
        self.logger.debug("From upstream: %s", line)
        await self.upstream.from_stream(line)

        # Store various things that we get e.g on initial
        # connection and that we might need later.
        key, sep, value = line.partition("=")

        self.variable_stats_add(key, None)

        if key == 'addon':
            (addon, rest) = value.split(":", 1)
            if addon == 'FRANKENROUTER':
                (messagetype, payload) = rest.split(":", 1)
                if messagetype == 'PING':  # pylint: disable=no-else-return
                    # addon=FRANKENROUTER:PING:<ID>
                    self.logger.debug("Got FRDP PING message from upstream: %s", line)
                    request_id = payload
                    # send reply
                    self.logger.debug("Sending FRDP pong to upstream")
                    await self.send_to_upstream(f"addon=FRANKENROUTER:PONG:{request_id}")
                    # store name and the fact that this client is a frankenrouter
                    self.upstream.is_frankenrouter = True
                    return
                elif messagetype == 'PONG':
                    request_id = payload
                    if request_id != self.upstream.frdp_ping_request_id:
                        self.logger.critical(
                            "Got unexpected PING request ID %s from upstream, expected %s",
                            request_id, self.upstream.frdp_ping_request_id)
                        return
                    elapsed = time.perf_counter() - self.upstream.ping_sent
                    # Do not warn or log data if we have just connected to
                    # upstream or sent a client welcome.
                    dolog = True
                    if self.is_upstream_connected():
                        time_since_connected = time.perf_counter() - self.upstream.connected_at
                        if time_since_connected < 5.0:
                            dolog = False
                    if time.perf_counter() - self.last_client_connected < 5.0:
                        dolog = False
                    if dolog:
                        self.upstream.frdp_ping_rtts.append(elapsed)
                        if elapsed > self.config.performance.frdp_rtt_warning:
                            self.logger.warning("SLOW: FRDP RTT to upstream is %.6f s", elapsed)
                    return
                else:
                    self.logger.critical("Unsupported FRDP message type %s (%s)", messagetype, line)
                    # No need for further processing a FRANKENROUTER message,
                    # it should not be forwarded upstream.
                    return

        if not self.variables.is_psx_keyword(key):
            self.logger.info("NONPSX keyword %s from upstream: %s", key, line)

        if key in [
                'load1',
                'load2',
                'load3',
        ]:
            # Load messages: send to connected clients
            self.logger.debug("Load message from upstream: %s", key)
            await self.client_broadcast(line)
        elif key in [
                'bang',
                'start',
        ]:
            # Should not be sent by upstream, ignore
            pass
        elif key in ["pleaseBeSoKindAndQuit"]:
            # Forward to clients
            self.logger.info(
                "Forwarding %s from upstream to all clients", key)
            await self.client_broadcast(key)
        elif key in [
                'exit',
        ]:
            # Shut down upstream connection cleanly
            self.logger.info(
                "Upstream sent exit message, sleeping %.1f s before reconnect",
                self.args.upstream_reconnect_delay,
            )
            await self.close_upstream_connection()
            await asyncio.sleep(self.args.upstream_reconnect_delay)
        elif sep != "":
            # Key-value message (including lexicon): store in
            # state and send to connected clients
            self.logger.debug("Storing key-value from upstream: %s=%s", key, value)
            self.cache.update(key, value)
            if key in self.variables.keywords_with_mode("NOLONG"):
                # the "nolong" keywords are only sent to
                # clients that have asked for them
                await self.client_broadcast(line, islong=True)
            elif key in self.variables.keywords_with_mode('START'):
                if key not in self.variables.keywords_with_mode('ECON'):
                    # START keywords that are not also ECON (e.g
                    # Ws493 and Qi208) get special handling
                    self.logger.debug(
                        "START (non-ECON) keyword, handling with isonlystart: %s", key)
                    await self.client_broadcast(line, isonlystart=True, key=key)
            else:
                await self.client_broadcast(line)
        else:
            self.logger.warning("Unhandled data from upstream: %s", line)

    async def handle_message_from_client(self, message):  # pylint: disable=too-many-locals,too-many-return-statements,too-many-branches,too-many-statements
        """Handle a message from a client."""
        client_addr = message['sender']
        if client_addr not in self.clients:
            self.logger.warning("Discarding message from disconnected client %s", client_addr)
            return
        line = message['payload'].decode().splitlines()[0]
        this_client = self.clients[client_addr]

        self.logger.debug("From client %s: %s", client_addr, line)

        # Log data from client
        await this_client.from_stream(line)

        key, sep, value = line.partition("=")

        self.variable_stats_add(key, client_addr)

        #
        # FrankenRouter DiscoveryProtocol :)
        #
        if key == 'addon' and value.startswith("FRANKENROUTER:"):
            (_, message_type, payload) = value.split(":", 2)
            if message_type == 'CLIENTINFO':  # pylint: disable=no-else-return
                # Payload is JSON data describing a PSX client
                try:
                    clientinfo = json.loads(payload)
                except json.decoder.JSONDecodeError:
                    self.logger.warning(
                        "Got invalid CLIENTINFO data from %s: %s",
                        client_addr, line)
                else:
                    self.logger.debug("Client info: %s", clientinfo)
                    peername = (clientinfo['laddr'], clientinfo['lport'])
                    if peername in self.clients:
                        thisname = clientinfo['name']
                        if len(thisname) > DISPLAY_NAME_MAXLEN:
                            newname = thisname[:DISPLAY_NAME_MAXLEN]
                            self.logger.warning(
                                "Client name %s is too long, using %s",
                                thisname, newname)
                            thisname = newname
                        self.logger.debug(
                            "Setting name for %s to %s from CLIENTINFO data",
                            peername, thisname)
                        self.clients[peername].display_name = f"I:{thisname}"
                    else:
                        self.logger.warning(
                            "Got CLIENTINFO data for non-connected client %s",
                            peername)
                # No further processing needed, and should not propagate upstream
                return
            elif message_type == 'IDENT':
                # addon=FRANKENROUTER:IDENT:<sim name>:<router name>
                (simname, routername) = payload.split(':')
                self.logger.debug(
                    "Got FRDP IDENT message from client %s: %s", client_addr, line)
                this_client.simulator_name = simname
                this_client.router_name = routername
                # No further processing needed, and should not propagate upstream
                return
            elif message_type == 'PING':
                self.logger.debug(
                    "Got FRDP PING message from client %s: %s", client_addr, line)
                request_id = payload
                await self.client_broadcast(
                    f"addon=FRANKENROUTER:PONG:{request_id}",
                    include=[client_addr]
                )
                # store name and the fact that this client is a frankenrouter
                this_client.is_frankenrouter = True
                # No further processing needed, and should not propagate upstream
                return
            elif message_type == 'PONG':
                self.logger.debug(
                    "Got FRDP PONG message from client %s: %s", client_addr, line)
                request_id = payload
                if request_id != this_client.frdp_ping_request_id:
                    self.logger.critical(
                        "Got unexpected PING request ID %s from %s, expected %s",
                        request_id, client_addr, this_client.frdp_ping_request_id)
                    return
                elapsed = time.perf_counter() - this_client.ping_sent
                dolog = True
                if self.is_upstream_connected():
                    time_since_connected = time.perf_counter() - self.upstream.connected_at
                    if time_since_connected < 5.0:
                        dolog = False
                if time.perf_counter() - self.last_client_connected < 5.0:
                    dolog = False
                if dolog:
                    if elapsed > self.config.performance.frdp_rtt_warning:
                        self.logger.warning(
                            "SLOW: FRDP RTT to client %s is %.6f s", client_addr, elapsed)
                    this_client.frdp_ping_rtts.append(elapsed)
                # No further processing needed, and should not propagate upstream
                return
            elif message_type == 'AUTH':
                # addon=FRANKENROUTER:AUTH:<<PASSWORD>>
                password = payload
                if password != "" and not this_client.has_access():
                    self.logger.info("Auth token provided, checking...")
                    # If client provided a password, try to use it to upgrade the connection
                    this_client.update_access_level(password)
                    if not this_client.has_access():
                        self.logger.warning(
                            "Client %s failed to authenticate, password used=%s",
                            this_client.display_name, password)
                        await self.close_client_connection(this_client, clean=False)
                    else:
                        await self.client_add_to_network(this_client)
                        this_client.welcome_sent = True
                        self.request_status_display()
                else:
                    # Client provided no password or already have been
                    # authenticated, so do nothing.
                    pass
                # No further processing needed, and should not propagate upstream
                return
            else:
                # Done handling FRANKENROUTER addon data - should not be forwarded
                self.logger.critical("Unsupported FRDP message type %s (%s)", message_type, line)
                return

        # For initial detection of other frankenrouters, we
        # send the "standard" name= keyword.
        if key == 'name':
            self.logger.debug("key is name for %s", line)
            if re.match(r".*:FRANKEN.PY frankenrouter", value):
                display_name = value.split(":")[0]
                self.logger.info(
                    "Client %s identified as frankenrouter %s",
                    client_addr, display_name)
                this_client.is_frankenrouter = True
                this_client.display_name = f"R:{display_name}"
                self.request_status_display()
                # We should not send this upstream, so stop here
                return

        if key == 'nolong':
            # Toggle nolong bit for this client, but do not send upstream
            this_client.nolong = not this_client.nolong
            self.logger.info(
                "Client %s toggled nolong to %s", client_addr, this_client.nolong)
            return

        # Print non-PSX keywords
        if not self.variables.is_psx_keyword(key):
            self.logger.warning("NONPSX keyword %s from %s: %s", key, client_addr, line)

        # Pick up name information from clients
        # Note: we inhibit name changes on a connection from a
        # frankenrouter as other clients are multiplexed on
        # that connection.

        # The community standard seems to be
        # name=SHORTNAME:LONGNAME but no prefix

        # Examples:
        # name=VPLG:vPilot Plugin
        # name=:PSX Sounds
        # name=EFB1:PSX.NET EFB For Windows
        # name=BACARS:BA ACARS Simulation

        # So I will use e.g
        # name=ICING:FRANKEN.PY frankenfreeze MSFS to PSX ice sync
        # name=WIND:FRANKEN.PY frankenwind MSFS to PSX wind sync
        # name=<simname>:FRANKEN.PY frankenrouter PSX router <routername>

        if key == 'name' and value != "" and not this_client.is_frankenrouter:
            learned_prefix = "L"
            thisname = value
            self.logger.debug("Checking %s against name regexps", value)
            if re.match(r".*PSX.NET EFB.*", value):
                thisname = value.split(":")[0]
            elif re.match(r":PSX Sounds", value):
                thisname = "PSX Sounds"
            # name=MSFS Router:PSX.NET Modules
            elif re.match(r"^MSFS Router", value):
                thisname = "MSFS Router"
            # name=BACARS:BA ACARS Simulation
            elif re.match(r"^BACARS:", value):
                thisname = "BACARS"
            # name=VPLG:vPilot Plugin
            elif re.match(r"VPLG:", value):
                thisname = "vPilot"
            # FRANKEN.PY clients
            elif re.match(r".*:FRANKEN\.PY", value):
                thisname = value.split(":")[0]
                learned_prefix = "F"
            if len(thisname) > 16:
                newname = thisname[:16]
                self.logger.info(
                    "Client %s name %s is too long, using %s",
                    this_client.peername, thisname, newname)
                thisname = newname
            else:
                self.logger.info(
                    "Client %s identifies as %s, using that name",
                    this_client.peername, thisname)
            this_client.display_name = f"{learned_prefix}:{thisname}"
            self.request_status_display()

        if key == 'name':
            self.logger.info(
                "Not passing on name= keyword from %s to upstream", this_client.client_id)
            return

        allow_write = False
        if this_client.can_write():
            allow_write = True
        elif key in ['demand']:
            # read-only clients may still send demand=...
            allow_write = True

        if allow_write:
            if key in ["bang", "again"]:
                # Forward to upstream but not other clients
                await self.send_to_upstream(key, client_addr)
            elif key in ["start"]:
                # Send to upstream, not other clients
                self.logger.debug("start received from %s, sending to upstream", client_addr)
                await self.send_to_upstream(key, client_addr)
                self.start_sent_at = time.perf_counter()
            elif key in ["demand"]:
                # Add to list for this client
                this_client.demands.add(value)
                self.logger.debug(
                    "added %s to demand list for %s + send to upstream", value, client_addr)
                await self.send_to_upstream(line, client_addr)
            elif key in ["load1", "load2", "load3"]:
                # Forward to upstream and other clients
                self.logger.debug("%s from %s", key, client_addr)
                await self.send_to_upstream(key, client_addr)
                await self.client_broadcast(key, exclude=[client_addr])
            elif key in ["pleaseBeSoKindAndQuit"]:
                # Forward to other clients
                self.logger.info("Forwarding %s from %s to all clients", key, client_addr)
                await self.client_broadcast(key, exclude=[client_addr])
                if self.args.forward_please_be_so_kind_and_quit_upstream:
                    self.logger.info("Forwarding %s from %s to upstream", key, client_addr)
                    await self.send_to_upstream(key, client_addr)
            elif key == 'exit':
                # Shut down client connection cleanly
                self.logger.info("Client %s sent exit message, closing", client_addr)
                await self.close_client_connection(this_client, clean=True)
                return
            elif sep != "":
                self.cache.update(key, value)
                line = f"{key}={value}"
                await self.send_to_upstream(line, client_addr)
                await self.client_broadcast(line, exclude=[client_addr])
            else:
                self.logger.warning("Unhandled data (%s) from client: %s", key, line)
        else:
            self.logger.info(
                "Read-only client tried to send data, ignoring: %s",
                line
            )

    async def forwarder_task(self, name):
        """Read messages from the queue and forward them."""
        try:
            while True:
                await asyncio.sleep(0)
                try:
                    message = await self.messagequeue.get()
                except asyncio.QueueShutDown:
                    raise SystemExit("Message queue has been shut down, this shuld not happen")  # pylint: disable=raise-missing-from
                queuetime = time.perf_counter() - message['received_time']
                if message['sender'] is None:
                    await self.handle_message_from_upstream(message)
                else:
                    await self.handle_message_from_client(message)
                totaltime = time.perf_counter() - message['received_time']
                print_delay_warning = False
                if (
                        totaltime > self.config.performance.total_delay_warning or
                        queuetime > self.config.performance.queue_time_warning
                ):
                    print_delay_warning = True
                if self.is_upstream_connected():
                    time_since_connected = time.perf_counter() - self.upstream.connected_at
                    if time_since_connected < 5.0:
                        # There is no point in warning about delays
                        # while the upstream is sending us the initial
                        # "welcome message" which includes all
                        # variables.
                        print_delay_warning = False
                if time.perf_counter() - self.last_client_connected < 5.0:
                    # If a client recently connected it is also
                    # expected to see some delays due to the welcome
                    # message.
                    print_delay_warning = False
                if print_delay_warning:
                    self.logger.warning(
                        "WARNING: forwarding from %s took %.1f ms" +
                        " (%.1f ms queue time, qsize=%d)",
                        "upstream" if message['sender'] is None else message['sender'],
                        totaltime * 1000, queuetime * 1000, self.messagequeue.qsize())
        # Standard Task cleanup
        except asyncio.exceptions.CancelledError:
            self.logger.info("Task %s was cancelled, cleanup and exit", name)
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down",
                                 exc, name)
            self.logger.critical(traceback.format_exc())
        # End of forwarder_task()

    async def api_task(self, name):
        """REST API Task."""
        try:
            routes = web.RouteTableDef()

            @routes.get('/')
            async def handle(request):
                name = request.match_info.get('name', "Anonymous")
                text = "Hello, " + name
                return web.Response(text=text)

            @routes.get('/clients')
            async def handle_clients(_):
                clients = []
                for client in self.clients.values():
                    clients.append({
                        'ip': client.ip,
                        'port': client.port,
                        'display_name': client.display_name,
                    })
                return web.json_response(clients)

            @routes.post('/upstream/set')
            async def handle_upstream_set(request):
                data = await request.post()
                new_host = data.get('host')
                new_port = int(data.get('port'))
                self.logger.info(
                    "Got request to change upstream to %s:%s",
                    new_host, new_port)
                self.logger.info(
                    "Current upstream is %s:%s (connected=%s)",
                    self.config.upstream.host,
                    self.config.upstream.port,
                    self.is_upstream_connected(),
                )
                reconnect = False
                if new_host != self.config.upstream.host:
                    self.config.upstream.host = new_host
                    reconnect = True
                if new_port != self.config.upstream.port:
                    self.config.upstream.port = new_port
                    reconnect = True
                if not reconnect:
                    return web.Response(text="Already connected to that host/port")
                self.logger.info(
                    "Will change upstream to %s:%s",
                    self.config.upstream.host,
                    self.config.upstream.port)
                self.upstream_reconnect_requested = True
                return web.Response(text="Connecting to new host/port")

            # Run the API
            app = web.Application()
            app.add_routes(routes)
            loop = asyncio.get_event_loop()
            handler = app.make_handler()
            await loop.create_server(
                handler,
                '0.0.0.0',
                self.config.listen.rest_api_port,
            )
            while True:  # wait forever
                await asyncio.sleep(3600.0)

        # Standard Task cleanup
        except asyncio.exceptions.CancelledError:
            self.logger.info("Task %s was cancelled, cleanup and exit", name)
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down",
                                 exc, name)
            self.logger.critical(traceback.format_exc())
        # End of api_task()

    async def monitor_task(self, name):  # pylint: disable=too-many-branches,too-many-statements
        """Monitor the other coroutines and restart as needed."""
        try:  # pylint: disable=too-many-nested-blocks
            while True:
                # Measure the time it takes to sleep 1.0s. If it takes
                # a lot longer, the router is overloaded.
                startsleep = time.perf_counter()
                await asyncio.sleep(1.0)
                delay = time.perf_counter() - startsleep - 1.0
                if delay > self.config.performance.monitor_delay_warning:
                    self.logger.info("Monitor delay is %.1f ms", delay * 1000)

                self.logger.debug("%s checking for running and ended tasks", name)
                running = []
                tasks_ended = set()
                for task in self.tasks:
                    done = task.done()
                    if done:
                        tasks_ended.add(task)
                        # A Task is done when the wrapped coroutine
                        # either returned a value, raised an
                        # exception, or the Task was cancel
                        self.logger.info("Task %s is done", task.get_name())
                        try:
                            task.result()
                        except asyncio.InvalidStateError:
                            self.logger.info("--> in invalid state, result not available")
                        except asyncio.CancelledError:
                            self.logger.info("--> has been cancelled")
                        exc = task.exception()
                        if exc is not None:
                            self.logger.info("--> ended with exception: %s", exc)
                    else:
                        # self.logger.debug("Task %s running", task.get_name())
                        running.append(task.get_name())
                self.logger.debug(
                    "Found %d tasks in any state, %d running: %s",
                    len(self.tasks), len(running), running)

                # Remove ended tasks from self.tasks
                for task in tasks_ended:
                    self.logger.debug("Removing %s from task list", task)
                    self.tasks.discard(task)
                self.logger.debug("Tasks after cleanup: %d", len(self.tasks))

                # Ensure the expected tasks are running
                if not asyncio.current_task().cancelled():
                    for taskname, properties in self.subsystems.items():
                        if 'start' in properties:
                            if properties['start'] is False:
                                continue
                        if taskname not in running:
                            self.logger.info("%s not running, starting it", taskname)
                            thistask = self.taskgroup.create_task(
                                properties['func'](name=taskname), name=taskname)
                            self.tasks.add(thistask)
                            self.logger.debug("Started %s, now has %d tasks",
                                              taskname, len(self.tasks))

                # Restart upstream connection if requested
                if self.upstream_reconnect_requested:
                    name = 'Upstream Connector'
                    self.logger.info("Upstream reconnect requested")
                    for task in self.tasks:
                        if task.get_name() == name:
                            self.logger.info("Restarting %s", name)
                            self.upstream_reconnect_requested = False
                            await asyncio.sleep(2.0)
                            task.cancel(msg="Reconnecting")
                            break

        # Standard Task cleanup
        except asyncio.exceptions.CancelledError:
            self.logger.info("Task %s was cancelled, cleanup and exit", name)
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down",
                                 exc, name)
            self.logger.critical(traceback.format_exc())
            return
        # End of monitor_task()

    async def main(self):  # pylint: disable=too-many-branches,too-many-statements
        """Start the proxy."""
        self.handle_args()

        # Read the config file
        try:
            self.config = config.RouterConfig(self.args.config_file)
        except config.RouterConfigError as exc:
            raise SystemExit(
                f"Failed to load config file {self.args.config_file}: {exc}") from exc

        # Override with command line options
        if self.args.log_traffic is not None:
            self.config.log.traffic = self.args.log_traffic

        # Other things we need to set based on the config
        if self.config.listen.rest_api_port is None:
            self.subsystems['REST API']['start'] = False

        # Get information from Variables.txt
        self.variables = variables.Variables(vfilepath=self.config.psx.variables)

        # Initialize the router cache
        self.cache = routercache.RouterCache(
            f"frankenrouter-{self.config.identity.router}.cache.json")
        if not self.args.no_state_cache_file:
            self.cache.read_from_file()

        if self.args.debug:
            print(f"config: identity/simulator = {self.config.identity.simulator}")
            print(f"config: identity/router = {self.config.identity.router}")
            print(f"config: listen/port = {self.config.listen.port}")
            print(f"config: listen/rest_api_port = {self.config.listen.rest_api_port}")
            print(f"config: log/traffic = {self.config.log.traffic}")
            print(f"config: log/directory = {self.config.log.directory}")
            print(f"config: psx/variables = {self.config.psx.variables}")
            i = 0
            for rule in self.config.access:
                try:
                    print(f"config: access/{i}/display_name = {rule.display_name}")
                    print(f"config: access/{i}/match_ipv4 = {rule.match_ipv4}")
                    print(f"config: access/{i}/is_frankenrouter = {rule.is_frankenrouter}")
                except AttributeError as exc:
                    print(f"Missing attribute - continuing: {exc}")
                i += 1
            i = 0
            for rule in self.config.check:
                try:
                    print(f"config: check/{i}/checktype = {rule.checktype}")
                    print(f"config: check/{i}/regexp = {rule.regexp}")
                    print(f"config: check/{i}/limit_min = {rule.limit_min}")
                    print(f"config: check/{i}/limit_max = {rule.limit_max}")
                    print(f"config: check/{i}/comment = {rule.comment}")
                except AttributeError as exc:
                    print(f"Missing attribute - continuing: {exc}")
                i += 1

        # Start the Monitor task
        try:
            async with asyncio.TaskGroup() as self.taskgroup:
                # Initialize logging
                task = self.taskgroup.create_task(
                    self.logging_task(name="Logging"), name="Logging")
                self.tasks.add(task)
                await asyncio.sleep(0)
                print(f"frankenrouter version {VERSION} starting")

                if self.config.log.traffic:
                    # Initialize traffic logging
                    task = self.taskgroup.create_task(
                        self.traffic_logging_task(
                            name="Traffic Logging"), name="Traffic Logging")
                    self.tasks.add(task)
                    await asyncio.sleep(0)

                # Start the Monitor task
                task = self.taskgroup.create_task(
                    self.subsystems[self.bootstrap_task]['func'](name=self.bootstrap_task),
                    name=self.bootstrap_task)
                self.tasks.add(task)
                self.logger.info("%s started", self.bootstrap_task)
        except asyncio.CancelledError:  # instead of KeyboardInterrupt
            print("Router stopped.")
            raise
        self.logger.info("All tasks ended, shutting down")


if __name__ == '__main__':
    try:
        asyncio.run(Frankenrouter().main())
    except KeyboardInterrupt:
        print("Shut down due to ^C")
