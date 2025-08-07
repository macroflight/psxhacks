"""A protocol-aware PSX router."""
# pylint: disable=invalid-name,too-many-lines,fixme
from __future__ import annotations
import argparse
import asyncio
import itertools
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

from frankenrouter.rules import RulesAction, RulesCode, Rules


__MYNAME__ = 'frankenrouter'
__MY_DESCRIPTION__ = 'A PSX Router'

VERSION = '0.8'

# If we have no upstream connection and no cached data, assume this
# version.
PSX_DEFAULT_VERSION = '10.182 NG'

# How long we wait for the upstream connection before accepting clients
# and serving them cached data.
UPSTREAM_WAITFOR = 5.0

# Status display static config
HEADER_LINE_LENGTH = 120

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
        self.last_load1 = 0.0
        self.last_bang = 0.0
        self.messagequeue_from_upstream = asyncio.Queue(maxsize=0)
        self.messagequeue_from_clients = asyncio.Queue(maxsize=0)
        self.taskgroup = None
        self.shutting_down = False
        self.tasks = set()
        # The toplevel coroutines we will be running
        self.bootstrap_task = 'Router Monitor'
        self.subsystems = {
            'Router Monitor': {
                'func': self.monitor_task,
                'kwargs': {},
            },
            'Client Listener': {
                'func': self.listener_task,
                'kwargs': {},
            },
            'Upstream Connector': {
                'func': self.upstream_connector_task,
                'kwargs': {},
            },
            'Forward From Upstream': {
                'func': self.forwarder_task,
                'kwargs': {
                    'messagequeue': self.messagequeue_from_upstream,
                },
            },
            'Forward From Clients': {
                'func': self.forwarder_task,
                'kwargs': {
                    'messagequeue': self.messagequeue_from_clients,
                },
            },
            'FRDP Sender': {
                'func': self.frdp_send_task,
                'kwargs': {},
            },
            'Status Display': {
                'func': self.status_display_task,
                'kwargs': {},
            },
            'Housekeeping': {
                'func': self.housekeeping_task,
                'kwargs': {},
            },
            'REST API': {
                'func': self.api_task,
                'kwargs': {},
            },
        }
        self.clients = {}
        self.upstream = None
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
        self.rules = Rules(self)

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
            '--log-directory',
            action='store', type=str, default=None,
            help=("Override config file setting for log directory")
        )
        parser.add_argument(
            '--enable-variable-stats',
            action='store_true',
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
            ("Router \"%s\" port %d, %d/%d msgs in queue from upstream/clients, uptime %d s" +
             ", API port %s, cache=%s"),
            self.config.identity.simulator, self.config.listen.port,
            self.messagequeue_from_upstream.qsize(),
            self.messagequeue_from_clients.qsize(),
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

            if data.display_name_source == 'FDRP IDENT':
                prefix = 'RI:'
            elif data.display_name_source == 'FDRP CLIENTINFO':
                prefix = 'CI:'
            elif data.display_name_source == 'name message':
                prefix = 'N:'
            elif data.display_name_source == 'access config':
                prefix = 'AC:'
            else:
                prefix = ''

            self.logger.info(
                "%2d %-26s %-15s %5d %8s %7d %6d %6d %9d %9d %5s %5s",
                data.client_id,
                f"{prefix}{data.display_name}",
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

        async def send_if_unsent(key, drain=False):
            """Send key-value to client if not already sent."""
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
                await client.to_stream(line, drain=drain)
                client.welcome_keywords_sent.add(key)
                self.logger.debug("To %s: %s", client.peername, line)

        async def send_line(line, drain=False):
            """Send line unconditionally to client."""
            await client.to_stream(line, drain=drain)
            self.logger.debug("To %s: %s", client.peername, line)

        #
        # See notes_on_psx_router.md for notes on what we send here and why
        #

        # Send a "fake" client id, rather than the id of the proxy's
        # connection to the PSX main server.
        await send_line(f"id={client.client_id}")

        # Send version and layout. If possible, use cache, if not,
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

        # This pauses the client. We use drain here to make sure this
        # is not delayed by buffering.
        await send_line("load1", drain=True)

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
                self.logger.info("All expected START keywords received, continuing")
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

        await send_line("load2", drain=True)
        for prefix in [
                "Qi",
                "Qh",
                "Qs",
        ]:
            for key in self.variables.sort_psx_keywords(self.cache.get_keywords()):
                if key.startswith(prefix):
                    await send_if_unsent(key)

        await send_line("load3", drain=True)
        await send_if_unsent("metar", drain=True)

        client.welcome_sent = True
        welcome_keyword_count = len(client.welcome_keywords_sent)
        client.welcome_keywords_sent = set()

        # Identify ourselves to the client (in case it's another
        # frankenrouter)
        await send_line(f"name=frankenrouter:{self.config.identity.simulator}")

        frdp_rtt = time.perf_counter() - start_time
        self.logger.info(
            "Added client %s in %.1f ms (%d keywords, %.0f/s)",
            client.client_id, frdp_rtt * 1000,
            welcome_keyword_count, welcome_keyword_count / frdp_rtt)

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
            self.last_load1 = time.perf_counter()

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
                await self.messagequeue_from_clients.put({
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
            key=None, exclude_name_regexp=None,
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
            if exclude_name_regexp is not None:
                if re.match(exclude_name_regexp, client.display_name):
                    self.logger.info(
                        "Not sending to %s due to regexp match for %s against %s",
                        client.peername, client.display_name, exclude_name_regexp)
                    continue
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
                    client.welcome_keywords_sent.add(key)
                elif client.waiting_for_start_keywords:
                    # send
                    client.welcome_keywords_sent.add(key)
                else:
                    self.logger.debug(
                        "Not sending START variable to %s: %s",
                        client.peername, line)
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
            self.logger.debug("Upstream is not connected, discarding data: %s",
                              line)
            return
        await self.upstream.to_stream(line, log=True)
        self.logger.debug("To upstream from %s: %s", client_addr, line)

    async def upstream_connector_task(self, name):  # pylint: disable=too-many-branches,too-many-statements,too-many-locals
        """Upstream connector Task."""
        try:  # pylint: disable=too-many-nested-blocks
            while True:  # pylint: disable=too-many-nested-blocks
                # Pause clients when we have no upstream connection
                await self.pause_clients()
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

                # Connection complete, refresh status display
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
                        break
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
                    await self.messagequeue_from_upstream.put({
                        'payload': data,
                        'received_time': t_read_data,
                        'sender': None,
                    })
                    # Give other tasks a chance to do something (we
                    # sometimes receive a large chunk of data from
                    # upstream and don't want to block the router
                    # while that is being ingested).
                    await asyncio.sleep(0)

        # Task cleanup: close connection
        except asyncio.exceptions.CancelledError:
            self.logger.info("Task %s was cancelled, close connection and exit", name)
            self.shutting_down = True  # don't accept new connections
            await self.close_upstream_connection()
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down",
                                 exc, name)
            self.logger.critical(traceback.format_exc())
            await self.close_upstream_connection()
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

    async def frdp_send_task(self, name):  # pylint: disable=too-many-branches
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
                for peername, data in self.clients.items():
                    if data.is_frankenrouter and not data.frdp_ident_sent:
                        self.logger.info("Sending FRDP IDENT to %s", data.peername)
                        await self.client_broadcast(f"addon=FRANKENROUTER:IDENT:{self.config.identity.simulator}:{self.config.identity.router}",  # pylint: disable=line-too-long
                                                    include=[peername])
                        data.frdp_ident_sent = True
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

    async def handle_message(self, sender, msg):  # pylint: disable=too-many-branches,too-many-statements
        """Handle a message.

        sender is a reference to a ConnectionClient or ConnectionUpstream object

        msg is a PSX network message from the queue (dict)
        """
        # Human-readable sender description
        sender_hr = "upstream" if sender.upstream is None else sender.peername

        line = msg['payload'].decode().splitlines()[0]
        self.logger.debug("Message from %s: %s", sender_hr, line)
        await sender.from_stream(line)

        (action, code, message, extra_data) = self.rules.route(line, sender)

        # Take actions based on RulesCode
        if code == RulesCode.FRDP_PING:
            # Send reply given by rules.route()
            if sender.upstream:
                await self.send_to_upstream(extra_data['reply'], sender.peername)
            else:
                await self.client_broadcast(extra_data['reply'], include=[sender.peername])
            self.logger.debug(
                "Got FRDP PING message from %s, sending PONG: %s",
                sender_hr, extra_data['reply'])
        elif code == RulesCode.FRDP_PONG:
            frdp_rtt = extra_data['frdp_rtt']
            self.logger.debug(
                "Got FRDP PONG message from %s: %s",
                sender_hr, line)
            # Store RTT unless we're in a situation that we know innduce high RTT
            dolog = True
            if sender.upstream and not self.is_upstream_connected():
                dolog = False
            if self.is_upstream_connected():
                time_since_connected = time.perf_counter() - self.upstream.connected_at
                if time_since_connected < 5.0:
                    dolog = False
            if time.perf_counter() - self.last_client_connected < 5.0:
                dolog = False
            if dolog:
                sender.frdp_ping_rtts.append(frdp_rtt)
                if frdp_rtt > self.config.performance.frdp_rtt_warning:
                    self.logger.warning("SLOW: FRDP RTT to %s is %.6f s", sender_hr, frdp_rtt)
        elif code == RulesCode.FRDP_IDENT:
            self.logger.debug(
                "Got FRDP IDENT message from %s: %s",
                sender_hr, line)
        elif code == RulesCode.FRDP_BANG:
            self.logger.debug(
                "Got FRDP IDENT message from %s: %s",
                sender_hr, line)
        elif code == RulesCode.LOAD1:
            self.logger.info("Got load1 message from %s", sender_hr)
        elif code == RulesCode.LOAD2:
            self.logger.info("Got load3 message from %s", sender_hr)
        elif code == RulesCode.LOAD3:
            self.logger.info("Got load3 message from %s", sender_hr)
        elif code == RulesCode.START:
            self.logger.info("Got start message from %s", sender_hr)
        elif code == RulesCode.PBSKAQ:
            self.logger.info("Got pleaseBeSoKindAndQuit message from %s", sender_hr)
        elif code == RulesCode.EXIT:
            self.logger.info("Got exit message from %s, closing connection", sender_hr)
            if sender.upstream:
                await self.close_upstream_connection()
            else:
                await self.close_client_connection(sender)
        elif code == RulesCode.KEYVALUE_NORMAL:
            self.logger.debug("Got normal key-value from %s: %s", sender_hr, line)
        elif code == RulesCode.KEYVALUE_FILTERED_INGRESS:
            self.logger.info(
                "Keyword update from %s dropped due to ingress filter (%s): %s",
                sender_hr, message, line)
        elif code == RulesCode.KEYVALUE_FILTER_EGRESS:
            self.logger.debug(
                "Keyword update from %s needs egress filtering (%s): %s",
                sender_hr, extra_data, line)
        elif code == RulesCode.NOLONG:
            self.logger.info("Got nolong from %s, toggled nolong flag", sender_hr)
        elif code == RulesCode.MESSAGE_INVALID:
            self.logger.warning("Got invalid message (%s): %s", message, line)
        elif code == RulesCode.FRDP_CLIENTINFO:
            self.logger.debug("Got FRDP CLIENTINFO: %s", line)
        elif code == RulesCode.FRDP_AUTH_FAIL:
            self.logger.warning("Client failed FRDP authentication: %s: %s", sender_hr, line)
            # Disconnect clients that fail authentication
            await self.close_client_connection(sender, clean=False)
        elif code == RulesCode.FRDP_AUTH_OK:
            self.logger.info("Client %s successfully authenticated: %s", sender_hr, line)
            await self.client_add_to_network(sender)
            sender.welcome_sent = True
        elif code == RulesCode.FRDP_AUTH_ALREADY_HAS_ACCESS:
            self.logger.warning(
                "Client %s successfully authenticated but already has access: %s",
                sender_hr, line)
        elif code == RulesCode.NAME_FROM_FRANKENROUTER:
            self.logger.info("Client %s is a frankenrouter: %s", sender_hr, line)
        elif code == RulesCode.NAME_LEARNED:
            self.logger.info("Client name learned: %s: %s", sender_hr, line)
        elif code == RulesCode.NAME_REJECTED:
            self.logger.warning("Ignoring name change from frankenrouter %s: %s", sender_hr, line)
        elif code == RulesCode.NONPSX:
            self.logger.warning("Non-PSX keyword forwarded from %s: %s", sender_hr, line)
        elif code == RulesCode.NOWRITE:
            self.logger.debug("Dropping message from non-write client %s: %s", sender_hr, line)
        elif code == RulesCode.DEMAND:
            self.logger.debug("Got demand= message from %s: %s", sender_hr, line)
        elif code == RulesCode.ADDON_FORWARDED:
            self.logger.info("Non-frankenrouter addon message from %s forwarded: %s",
                             sender_hr, line)
        elif code == RulesCode.AGAIN:
            self.logger.info("Keyword again from %s forwarded: %s", sender_hr, line)

        # Take action
        if action == RulesAction.DROP:
            # No action needed
            pass
        elif action == RulesAction.UPSTREAM_ONLY:
            self.logger.debug("sending to upstream only: %s", line)
            await self.send_to_upstream(line, sender.peername)
        elif action == RulesAction.NORMAL:
            self.logger.debug("sending normally: %s", line)
            if not sender.upstream:
                await self.send_to_upstream(line, sender.peername)
            if sender.upstream:
                await self.client_broadcast(line)
            else:
                await self.client_broadcast(line, exclude=[sender.peername])
        elif action == RulesAction.FILTER:
            # There are several different types of filtering:
            # - nolong: do not send NOLONG variables to clients is nolong=True
            # - start: do not send unless the client has requested START variables
            # - endpoint_name_regexp: do not send if endpoint name matches
            # Only one filter type will be given by the ruleset.
            if 'nolong' in extra_data:
                self.logger.debug("sending with islong: %s", line)
                if not sender.upstream:
                    await self.send_to_upstream(line, sender.peername)
                if sender.upstream:
                    await self.client_broadcast(line, islong=True)
                else:
                    await self.client_broadcast(line, exclude=[sender.peername], islong=True)
            elif 'start' in extra_data:
                self.logger.debug("sending with isonlystart: %s", line)
                if not sender.upstream:
                    await self.send_to_upstream(line, sender.peername)
                if sender.upstream:
                    await self.client_broadcast(
                        line, isonlystart=True, key=extra_data['key'])
                else:
                    await self.client_broadcast(line, exclude=[sender.peername],
                                                isonlystart=True, key=extra_data['key'])
            elif 'endpoint_name_regexp' in extra_data:
                self.logger.debug("sending with name regexp filter: %s", line)
                regex = extra_data['endpoint_name_regexp']
                if not sender.upstream:
                    await self.send_to_upstream(line, sender.peername)
                if sender.upstream:
                    await self.client_broadcast(line, exclude_name_regexp=regex)
                else:
                    await self.client_broadcast(line, exclude=[sender.peername],
                                                exclude_name_regexp=regex)
            else:
                self.logger.critical(
                    "RulesAction.FILTER but no known filter type in extra_data: %s: %s",
                    extra_data, line)
                # A safe fallback is to send to all
                await self.send_to_upstream(line, sender.peername)
                await self.client_broadcast(line, exclude=[sender.peername])

    async def forwarder_task(self, messagequeue, name):  # pylint: disable=too-many-branches
        """Read messages from the queue and forward them."""
        try:
            while True:
                await asyncio.sleep(0)
                try:
                    message = await messagequeue.get()
                except asyncio.QueueShutDown:
                    raise SystemExit("Message queue has been shut down, this shuld not happen")  # pylint: disable=raise-missing-from
                queuetime = time.perf_counter() - message['received_time']
                if message['sender'] is None:
                    if self.is_upstream_connected():
                        await self.handle_message(self.upstream, message)
                    else:
                        self.logger.warning(
                            "Dropping message from upstream - no longer connected: %s",
                            message)
                else:
                    if message['sender'] in self.clients:
                        await self.handle_message(self.clients[message['sender']], message)
                    else:
                        self.logger.warning(
                            "Dropping message from %s - no longer connected: %s",
                            message['sender'], message)
                totaltime = time.perf_counter() - message['received_time']
                print_delay_warning = False
                if (
                        totaltime > self.config.performance.total_delay_warning or
                        queuetime > self.config.performance.queue_time_warning
                ):
                    print_delay_warning = True
                # Do not warn about delay if we just connected to upstream.
                if self.is_upstream_connected():
                    if time.perf_counter() - self.upstream.connected_at < 5.0:
                        print_delay_warning = False
                # Do not warn about delay if a load1 was just sent
                if time.perf_counter() - self.last_load1 < 5.0:
                    print_delay_warning = False
                # Do not warn about delay if a bang was just sent
                if time.perf_counter() - self.last_bang < 5.0:
                    print_delay_warning = False
                # Do not warn about delay if a client just connected
                if time.perf_counter() - self.last_client_connected < 5.0:
                    print_delay_warning = False
                if print_delay_warning:
                    self.logger.warning(
                        "WARNING: forwarding from %s took %.1f ms" +
                        " (%.1f ms queue time, qsize=%d)",
                        "upstream" if message['sender'] is None else message['sender'],
                        totaltime * 1000, queuetime * 1000, messagequeue.qsize())
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
                                properties['func'](
                                    name=taskname,
                                    **properties['kwargs']
                                ), name=taskname)
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

        # If there is no config file, fallback to "dumb client mode"
        if not os.path.exists(self.args.config_file):
            # Use the default config
            self.config = config.RouterConfig()
            # Set sane defaults and ask the user to override them
            print(f"""
Configuration file {self.args.config_file} not found.
If you want to use a config file, press Control-C now and create one.

To run the router in "basic client mode", answer the questions below.

HOWTO for temporarily converting your PSX sim to a shared cockpit
slave sim:

1: Open the Instructor window for your PSX main server.
2: On the Network tab, click Stop and then choose "A main client" and click start
3: Answer the questions below
4: The router will start and connect to the shared cockpit master sim
5: Verify that your sim is working. You will probably need to restart
a few addons (the ones that does not automatically reconnect when the
PSX main server or router is restarted).

Note: choose a simulator name that let other shared cockpit users know
who you are, e.g your PSX forum nickname

Note: Router port should be 10747 unless you know better :)

Upstream host should be the IP address or hostname you got from the
owner of the shared cockpit master sim.

Upstream port should be the port number you got from the owner of the
shared cockpit master sim.

Password: leave blank if the master sim does not use a
password. Otherwise use the password given to you by the owner of the
shared cockpit master sim.

            """)

            # Default to listen on 10747 for "dumb client mode" and connect to 10748
            self.config.listen.port = 10747
            self.config.upstream.port = 10748

            while True:
                self.config.identity.simulator = input(
                    "The name of your simulator others will see (max 24 characters)? ")
                if (
                        len(self.config.identity.simulator) < 24 and
                        len(self.config.identity.simulator) > 0
                ):
                    break
            self.config.identity.router = self.config.identity.simulator

            listen = input(f"Router port (press Enter for {self.config.listen.port})? ")
            host = input(f"Upstream host (press Enter for {self.config.upstream.host})? ")
            port = input(f"Upstream port (press Enter for {self.config.upstream.port})? ")
            password = input(
                f"Upstream password (press Enter for {self.config.upstream.password})? ")
            if listen != "":
                self.config.listen.port = int(listen)
            if host != "":
                self.config.upstream.host = host
            if port != "":
                self.config.upstream.port = int(port)
            if password != "":
                self.config.upstream.password = password

        else:
            # Read the config file
            try:
                self.config = config.RouterConfig(self.args.config_file)
            except config.RouterConfigError as exc:
                raise SystemExit(
                    f"Failed to load config file {self.args.config_file}: {exc}") from exc

        # In interactive mode, ask the user for upstream connection
        # details
        if self.config.upstream.interactive is True:
            print("Interactive mode requested")
            host = input(f"Upstream host (press Enter for {self.config.upstream.host})? ")
            port = input(f"Upstream port (press Enter for {self.config.upstream.port})? ")
            password = input(
                f"Upstream password (press Enter for {self.config.upstream.password})? ")
            if host != "":
                self.config.upstream.host = host
            if port != "":
                self.config.upstream.port = int(port)
            if password != "":
                self.config.upstream.password = password

        # Override with command line options
        if self.args.log_traffic is not None:
            self.config.log.traffic = self.args.log_traffic

        if self.args.log_directory is not None:
            self.config.log.directory = self.args.log_directory

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
