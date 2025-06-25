"""A protocol-aware PSX router."""
# pylint: disable=invalid-name,too-many-lines, fixme
from __future__ import annotations
import argparse
import asyncio
import datetime
import inspect
import json
import logging
import math
import os
import pathlib
import random
import re
import statistics
import string
import sys
import time
import traceback

import variables
import routercache

VERSION = '0.5'

# If we have no server connection and no cached data, assume this
# version.
PSX_DEFAULT_VERSION = '10.182 NG'

PSX_SERVER_RECONNECT_DELAY = 5.0

PSX_PROTOCOL_SEPARATOR = b'\r\n'

# We typically see values up to 100k during e.g connection of certain
# addons (maybe they get both the initial welcome and then send
# "bang"?)
WRITE_BUFFER_WARNING = 100000

# Warn if a server or client read loop spends more than this long not polling
NOPOLL_WARNING = 0.005  # 1 ms

# Warn if we take longer than this from reading data to having done write+await drain
FORWARDING_TIME_WARNING = 0.005  # 1 ms

HEADER_LINE_LENGTH = 110


class Frankenrouter():  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """Replaces the PSX USB subsystem."""

    def __init__(self):
        """Initialize the class."""
        self.args = None
        self.logger = None
        self.variables = None
        self.cache = None
        self.clients = {}
        self.server = {}
        self.server_pending_messages = []
        self.log_data_file = None
        self.log_data_filename = None
        self.start_time = int(time.time())
        self.allowed_clients = {
        }
        self.shutdown_requested = False
        self.proxy_server = None
        self.next_client_id = 1
        self.starttime = time.perf_counter()
        self.server_reconnects = 0
        self.router_restarts = 0
        self.last_status_print = 0.0
        self.master_caution_sent_by_us = False
        self.statistics_keep_samples = 10000

        # Track when we last send the start keyword upstream
        self.start_sent_at = 0.0

        # Track when we last started welcoming a client. We can then
        # use this timestamp to e.g filter out variables that some
        # clients might be sensitive to receiving while running
        # normally.
        self.last_client_connected = 0.0

    def handle_args(self):  # pylint: disable=too-many-statements
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
            '--listen-port',
            type=int, action='store', default=10748,
            help="The port the router will listen on",
        )
        parser.add_argument(
            '--listen-host',
            type=str, action='store', default=None,
            help=(
                "The hostname/IP the router will listen on. Default is to listen on all " +
                "interfaces."
            ),
        )
        parser.add_argument(
            '--psx-main-server-host',
            type=str, action='store', default='127.0.0.1',
            help="Hostname or IP of the PSX main server or router to connect to.",
        )
        parser.add_argument(
            '--psx-main-server-port',
            type=int, action='store', default=10747,
            help="The port of the PSX main server or router to connect to.",
        )
        parser.add_argument(
            '--password',
            type=str, action='store',
            help=(
                "Password to use for connecting to an upstream router (if password" +
                " authentication is used). If you connect directly to a PSX main server" +
                " a password is never needed."
            ),
        )
        parser.add_argument(
            '--this-router-password',
            type=str, action='store',
            help=(
                "Password required for incoming connections to this router." +
                " Set to AUTO to generate"
            ),
        )
        parser.add_argument(
            '--this-router-password-readonly',
            type=str, action='store',
            help=(
                "Password required for readonly incoming connections to this router." +
                " Set to AUTO to generate"
            ),
        )
        parser.add_argument(
            '--blocked-clients',
            type=str, action='store', default="",
            help=(
                "Comma-separated lists of clients thay may not connect." +
                " format: IP,IP,IP,..."
            ),
        )
        parser.add_argument(
            '--allowed-clients',
            type=str, action='store', default="",
            help=(
                "Comma-separated lists of clients thay may connect." +
                " format: ALL or IP:access_level:identifier" +
                ", e.g 192.168.1.42:full:FrankenThrottle"
            ),
        )
        parser.add_argument(
            '--print-non-psx',
            action='store_true',
            help="Print all non-PSX keywords from server and clients")
        parser.add_argument(
            '--server-buffer-size', type=int,
            action='store', default=1048576)
        parser.add_argument(
            '--frdp-interval',
            type=int, action='store', default=1,
            help="How often to send FRDP ping to other frankenrouter server and clients (s)",
        )
        parser.add_argument(
            '--frdp-warning',
            type=float, action='store', default=0.1,
            help="Log a warning to the console if one FRDP RTT is higher than this (s)",
        )
        parser.add_argument(
            '--status-interval',
            type=int, action='store', default=10,
            help="How often to print router status to terminal and log (s)",
        )
        parser.add_argument(
            '--state-cache-file',
            type=str, action='store', default="AUTO",
            help=(
                "This file contains PSX state that is automatically read on startup" +
                " and used until we have connected to the PSX main server. We also save" +
                " the current state to this file on shutdown."
            ),
        )
        parser.add_argument(
            '--no-state-cache-file',
            action='store_true',
            help=(
                "Do not read the cached server data on startup. In this case, the router" +
                " will only provide a fake client ID and PSX version to clients that" +
                " connect before it has connected to the PSX main server."
            ),
        )
        parser.add_argument(
            '--variables-file',
            type=pathlib.Path, action='store', default='./Variables.txt',
            help=(
                "Path to an up to date Variables.txt file." +
                " This can be found in the Developers subdirectory of your PSX install"
            ),
        )
        parser.add_argument(
            '--log-dir',
            type=pathlib.Path, action='store', default='./',
            help=("Directory where the normal router output and any " +
                  " requested log files is written."),
        )
        parser.add_argument(
            '--log-data',
            action='store_true',
            help="Log all data to and from clients and server to a single file.",
        )
        parser.add_argument(
            '--debug',
            action='store_true',
            help="Print more debug info. Probably only useful for development.",
        )
        parser.add_argument(
            '--stop-on-exception',
            action='store_true',
            help=(
                "Stop when router encounters an unhandled exception. Normally the router" +
                " will try to recover from failure and restart, even though not all" +
                " clients will automatically reconnect."
            ),
        )
        parser.add_argument(
            '--stop-after',
            action='store', type=int, default=None,
            help="Stop after this long runtime (for profiling)",
        )
        parser.add_argument(
            '--forward-please-be-so-kind-and-quit-upstream',
            action='store_true',
            help=(
                "Forward pleaseBeSoKindAndQuit to upstream router. Use this with"
                " caution in shared cockpit setups."
            ),
        )

        self.args = parser.parse_args()
        if self.args.allowed_clients == "":
            print("!" * 80)
            print("ONLY LOCALHOST CLIENTS CAN CONNECT")
            print("")
            print("Use the --allowed-clients option or --this-router-password")
            print("to give access to other hosts.")
            print("!" * 80)
            self.args.allowed_clients = "127.0.0.1:full:LocalHost"
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

        self.args.blocked_clients = self.args.blocked_clients.split(",")
        if self.args.this_router_password == 'AUTO':
            self.args.this_router_password = self.get_random_id(18)
        if self.args.this_router_password_readonly == 'AUTO':
            self.args.this_router_password_readonly = self.get_random_id(18)
        if self.args.state_cache_file == 'AUTO':
            self.args.state_cache_file = f"frankenrouter-{self.args.sim_name}.cache.json"
        if not os.path.exists(self.args.variables_file):
            parser.error(
                f"{self.args.variables_file} not found, you need to use --variables-file")

    def get_random_id(self, length=16):
        """Return a random string we can use for FRDP request id."""
        return ''.join(
            random.choices(string.ascii_letters + string.digits, k=length))

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
        # No complicated status output when we're shutting down
        if self.shutdown_requested is True:
            return
        self.logger.info("-" * HEADER_LINE_LENGTH)
        self.logger.info(
            ("Frankenrouter %s port %d, %d keywords cached, uptime %d s" +
             ", server connects %d, self restarts %s"),
            self.args.sim_name, self.args.listen_port, self.cache.get_size(),
            int(time.perf_counter() - self.starttime),
            self.server_reconnects, self.router_restarts,
        )
        self.logger.info("%s",
                         (
                             "Ctrl-C to shut down cleanly." +
                             f" Password: {self.args.this_router_password}" +
                             f" Read-only password: {self.args.this_router_password_readonly}"
                         ))
        if self.log_data_filename:
            self.logger.info("Logging traffic to %s", self.log_data_filename)
        serverinfo = "[NO SERVER CONNECTION]"
        if self.is_server_connected():
            serverinfo = f"SERVER {self.server['ip']}:{self.server['port']}"
            serverinfo += f" {self.server['identifier']}"
            if len(self.server['ping_rtts']) > 0:
                ping_rtt_mean = statistics.mean(self.server['ping_rtts'][-100:])
                ping_rtt_max = max(self.server['ping_rtts'][-100:])
                serverinfo = serverinfo + f", RTT mean/max: {(ping_rtt_mean * 1000):.1f}/{(ping_rtt_max * 1000):.1f} ms"  # pylint: disable=line-too-long
            if len(self.server['write_drain']) > 0:
                writedrain_mean = statistics.mean(self.server['write_drain'][-100:])
                writedrain_max = max(self.server['write_drain'][-100:])
                serverinfo = serverinfo + f", output delay avg/max {(writedrain_mean * 1000):.1f}/{(writedrain_max * 1000):.1f} ms"  # pylint: disable=line-too-long
        self.logger.info(serverinfo)
        self.logger.info(
            "%-21s %-15s %5s %8s %7s %6s %6s %6s %6s %4s %10s",
            f"{len(self.clients)} clients",
            "",
            "Local",
            "",
            "",
            "Lines",
            "Lines",
            "Bytes",
            "Bytes",
            "FRDP ms",
            "Delay us",
        )
        self.logger.info(
            "%2s %-18s %-15s %5s %8s %7s %6s %6s %6s %6s %4s %4s %4s %4s",
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
            "mean",
            "max",
            "mean",
            "max",
        )
        for data in self.clients.values():
            writedrain_mean = "-"
            writedrain_max = "-"
            if len(data['write_drain']) > 0:
                writedrain_mean = f"{(1000.0 * statistics.mean(data['write_drain'][-100:])):.1f}"
                writedrain_max = f"{(1000.0 * max(data['write_drain'][-100:])):.1f}"

            ping_rtt_mean = "-"
            ping_rtt_max = "-"
            if len(data['ping_rtts']) > 0:
                ping_rtt_mean = f"{(1000.0 * statistics.mean(data['ping_rtts'][-100:])):.1f}"
                ping_rtt_max = f"{(1000.0 * max(data['ping_rtts'][-100:])):.1f}"

            self.logger.info(
                "%2d %-18s %-15s %5d %8s %7d %6d %6d %6d %6d %4s %4s %4s %4s",
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
                ping_rtt_mean,
                ping_rtt_max,
                writedrain_mean,
                writedrain_max,
            )
        self.logger.info("-" * HEADER_LINE_LENGTH)
        self.last_status_print = time.perf_counter()

    async def log_data(self, line, endpoints=None, inbound=True):
        """Write to optional log file."""
        server_endpoint_desc = 'PSX main server'
        maxlen = len(server_endpoint_desc)
        if self.args.log_data:
            direction = '>>>'
            if inbound:
                direction = '<<<'
            endpoint_desc = server_endpoint_desc
            if endpoints is not None:
                endpoints = str(endpoints)
                if len(endpoints) > maxlen:
                    endpoint_desc = endpoints[:(maxlen - 3)] + "..."
                else:
                    endpoint_desc = endpoints[:maxlen] + " " * (maxlen - len(endpoints[:maxlen]))
            self.log_data_file.write(
                f"{datetime.datetime.now().isoformat()} {direction} [{endpoint_desc}] {line}\n"
            )

    async def to_stream(self, endpoint, line, drain=True):
        """Write data to a stream and optionally to a log file.

        Also update traffic counters.
        """
        # Write to stream
        start_time = time.perf_counter()
        try:
            if endpoint['writer'].transport.get_write_buffer_size() > WRITE_BUFFER_WARNING:
                self.logger.warning(
                    "Write buffer %d > %d for %s",
                    endpoint['writer'].transport.get_write_buffer_size(),
                    WRITE_BUFFER_WARNING,
                    endpoint['peername'])
            if line is not None:
                endpoint['writer'].write(line.encode() + PSX_PROTOCOL_SEPARATOR)
            if drain:
                elapsed = time.perf_counter() - start_time
                endpoint['write_drain'].append(elapsed)
                # limit length of list
                endpoint['write_drain'] = endpoint['write_drain'][-self.statistics_keep_samples:]
                await endpoint['writer'].drain()
        except ConnectionResetError as exc:
            self.logger.warning(
                "Connection reset while sending to %s - continuing: %s", endpoint['peername'], exc)
        else:
            endpoint['messages sent'] += 1
            endpoint['bytes sent'] += len(line) + 1

    def from_stream(self, endpoint, line):
        """Log data read from stream."""
        endpoint['messages received'] += 1
        endpoint['bytes received'] += len(line) + 1

    async def client_add_to_network(self, client):  # pylint: disable=too-many-branches,too-many-statements,too-many-locals
        """Add a client to the network."""
        self.logger.info(
            "Adding client %s to network (%d keywords)",
            client['id'], self.cache.get_size())
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
            if key not in client['welcome_keywords_sent']:
                cached_value = self.cache.get_value(key)
                if cached_value is None:
                    self.logger.warning(
                        "%s not found in router cache, client restart might be needed" +
                        " after server connection", key)
                    return
                line = f"{key}={cached_value}"
                await self.to_stream(client, line)
                await self.log_data(line, endpoints=f"client {client['id']}", inbound=False)
                client['welcome_keywords_sent'].add(key)
                self.logger.debug("To %s: %s", client['peername'], line)

        async def send_line(line):
            await self.to_stream(client, line)
            await self.log_data(line, endpoints=f"client {client['id']}", inbound=False)
            self.logger.debug("To %s: %s", client['peername'], line)

        #
        # See notes_on_psx_router.md for notes on what we send here and why
        #

        # Send a "fake" client id, rather than the id of the proxy's
        # connection to the PSX main server.
        await send_line(f"id={client['id']}")

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
        client['waiting_for_start_keywords'] = True
        await self.send_to_server("start")
        self.start_sent_at = time.perf_counter()
        all_start_keywords = set(self.variables.keywords_with_mode('START'))
        all_econ_keywords = set(self.variables.keywords_with_mode('ECON'))
        expected_start_keywords = all_start_keywords - all_econ_keywords
        while True:
            await asyncio.sleep(0.010)
            now = time.perf_counter()
            missing = len(expected_start_keywords - client['welcome_keywords_sent'])
            if missing <= 0:
                self.logger.debug("All expected START keywords received, continuing")
                break
            waited = now - self.start_sent_at
            if waited > 1.0:
                self.logger.warning(
                    "Waited %.1f s for START data, missing %d of %d (%s), continuing anyway",
                    waited, missing,
                    len(expected_start_keywords),
                    expected_start_keywords - client['welcome_keywords_sent'])
                break
        client['waiting_for_start_keywords'] = False

        #
        # Send all other unsent keywords
        #

        if self.cache.get_size() < 10:
            self.logger.warning(
                "Router cache probably not initialized, some clients might misbehave")

        for keyword in [
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

        client['welcome_sent'] = True
        client['welcome_keywords_sent'] = set()

        # Send pending messages
        if len(client['pending_messages']) > 0:
            self.logger.info(
                "Sending %d held messages to client",
                len(client['pending_messages'])
            )
            for message in client['pending_messages']:
                await send_line(message)
        client['pending_messages'] = []

        # Identify ourselves to the client (in case it's another
        # frankenrouter)
        await send_line(f"name=frankenrouter:{self.args.sim_name}")

        elapsed = time.perf_counter() - start_time
        self.logger.info(
            "Added client %s in %.1f ms", client['id'], elapsed * 1000)

    async def close_client_connection(self, client):
        """Close a client connection and remove client data."""
        try:
            client['writer'].close()
            await client['writer'].wait_closed()
        except (ConnectionResetError, ConnectionAbortedError):
            pass
        # Remove client data and print new status
        del self.clients[client['peername']]
        self.logger.info("Closed client connection %s", client['peername'])
        self.print_status()

    async def close_server_connection(self):
        """Close a server connection and remove server data."""
        if 'writer' in self.server:
            try:
                self.server['writer'].close()
                await self.server['writer'].wait_closed()
            except ConnectionResetError:
                pass
        self.logger.info("Closed server connection")
        self.server = {}
        self.print_status()

    async def handle_new_connection_cb(self, reader, writer):  # pylint: disable=too-many-branches,too-many-statements,too-many-locals
        """Handle a new client connection."""
        # asyncio will intentionally not propagate exceptions from a
        # callback (see https://bugs.python.org/issue42526), so we
        # need to wrap the entire function in try-except.
        try:  # pylint: disable=too-many-nested-blocks
            # increase write buffer limits a bit
            writer.transport.set_write_buffer_limits(high=1048576, low=524288)
            self.logger.info(
                "to_stream: write buffer limits are %s", writer.transport.get_write_buffer_limits())

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
                'ping_rtts': [],
                'write_drain': [],
                'connected_clients': 0,
                'welcome_sent': False,
                'welcome_keywords_sent': set(),
                'waiting_for_start_keywords': False,
                'pending_messages': [],
                'demands': set(),
            }
            self.clients[client_addr] = this_client
            self.next_client_id += 1
            self.logger.info("New client connection: %s", client_addr)

            # Allow whitelisted IPs to connect
            if this_client['ip'] in self.args.blocked_clients:
                self.logger.warning(
                    "Blocked client %s connected, closing connection", this_client['ip'])
                self.to_stream(this_client, "bye now")
                await self.close_client_connection(this_client)
                return
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
                self.print_status()
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
                self.print_status()
            else:
                if self.args.this_router_password or self.args.this_router_password_readonly:
                    # Print message and keep connection open
                    self.logger.warning(
                        "Client %s connected, not identified, no access yet)", client_addr)
                    self.to_stream(this_client, "addon=frankenrouter:authorization token required")
                    self.print_status()
                else:
                    # Print error and close the connection
                    self.logger.warning(
                        "Client %s connected, not identified, connection closed", client_addr)
                    self.to_stream(this_client, "unauthorized")
                    await self.close_client_connection(this_client)
                    self.print_status()
                    return

            # New client connected, so print status
            self.print_status()
            if this_client['access'] != 'noaccess':
                await self.client_add_to_network(this_client)
                this_client['welcome_sent'] = True

            # Wait for data from client
            t_read_data = 0
            t_data_forwarded = 0
            while self.is_client_connected(client_addr):
                self.logger.debug("Waiting for data from client %s", client_addr)
                # We know the protocol is text-based, so we can use
                # readline()
                if t_read_data > 0 and t_data_forwarded > 0:
                    t_elapsed_from_read_to_forwarded = t_data_forwarded - t_read_data
                    if t_elapsed_from_read_to_forwarded > FORWARDING_TIME_WARNING:
                        self.logger.warning(
                            "Forwarding delay for client %s was %.6f s",
                            client_addr, t_elapsed_from_read_to_forwarded)

                    t_elapsed_since_last_data_read = time.perf_counter() - t_read_data
                    if t_elapsed_since_last_data_read > NOPOLL_WARNING:
                        self.logger.warning(
                            "Nopoll time for client %s was %.6f s",
                            client_addr, t_elapsed_since_last_data_read)
                try:
                    data = await reader.readline()
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    del self.clients[client_addr]
                    self.print_status()
                    self.logger.warning("Client connection broke (%s) for %s", exc, client_addr)
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
                                     client_addr)
                    await self.close_client_connection(this_client)
                    return
                # Note: we can get a partial line at EOF, so discard
                # data with no newline.
                if not data.endswith(b'\n'):
                    self.logger.warning(
                        "Got partial line data from %s, discarding: %s",
                        client_addr, data
                    )
                    continue
                line = data.decode().strip()
                self.logger.debug("From client %s: %s", client_addr, line)

                # Log data from client
                self.from_stream(this_client, line)
                await self.log_data(line, endpoints=f"client {this_client['id']}")

                key, sep, value = line.partition("=")

                #
                # FrankenRouter DiscoveryProtocol :)
                #
                if key == 'addon':
                    if value.startswith("FRANKENROUTER:"):
                        (_, message_type, payload) = value.split(":", 2)
                        if message_type == 'IDENT':
                            # Payload is JSON data describing a PSX client
                            try:
                                clientinfo = json.loads(payload)
                            except json.decoder.JSONDecodeError:
                                self.logger.warning(
                                    "Got invalid IDENT data from %s: %s",
                                    client_addr, line)
                            else:
                                self.logger.debug("Client info: %s", clientinfo)
                                peeraddr = (clientinfo['laddr'], clientinfo['lport'])
                                if peeraddr in self.clients:
                                    thisname = clientinfo['name']
                                    if len(thisname) > 16:
                                        newname = thisname[:16]
                                        self.logger.warning(
                                            "Client name %s is too long, using %s",
                                            thisname, newname)
                                        thisname = newname
                                    self.logger.info(
                                        "Setting name for %s to %s from IDENT data",
                                        peeraddr, thisname)
                                    self.clients[peeraddr]['identifier'] = f"I:{thisname}"
                                else:
                                    self.logger.warning(
                                        "Got IDENT data for non-connected client %s",
                                        peeraddr)
                        # Done handling FRANKENROUTER addon data - should not be forwarded
                        continue

                # For initial detection of other frankenrouters, we
                # send the "standard" name= keyword.
                if key == 'name':
                    self.logger.debug("key is name for %s", line)
                    if re.match(r".*:FRANKEN.PY frankenrouter", value):
                        identifier = value.split(":")[0]
                        self.logger.info(
                            "Client %s identified as frankenrouter %s",
                            client_addr, identifier)
                        this_client['is_frankenrouter'] = True
                        this_client['identifier'] = f"R:{identifier}"
                        self.print_status()
                        # We should not send this upstream, so stop here
                        continue

                if key == 'nolong':
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
                        (identifier, request_id, auth_token) = message.split(":", 2)
                        await self.client_broadcast(
                            f"frankenrouter=pong:{self.args.sim_name}:{request_id}",
                            include=[client_addr],
                        )
                        # store name and the fact that this client is a frankenrouter
                        this_client['is_frankenrouter'] = True
                        this_client['identifier'] = f"R:{identifier}"
                        if auth_token != "":
                            # If client provided a password, try it
                            if auth_token == self.args.this_router_password:
                                if this_client['access'] == "noaccess":
                                    self.logger.info(
                                        "Client %s has authenticated", this_client['identifier'])
                                    this_client['access'] = "full"
                                    await self.client_add_to_network(this_client)
                                    this_client['welcome_sent'] = True
                            elif auth_token == self.args.this_router_password_readonly:
                                if this_client['access'] == "noaccess":
                                    self.logger.info(
                                        "Client %s has authenticated", this_client['identifier'])
                                    this_client['access'] = "readonly"
                                    await self.client_add_to_network(this_client)
                                    this_client['welcome_sent'] = True
                            else:
                                self.logger.warning(
                                    "Client %s failed to authenticate, password used=%s",
                                    this_client['identifier'], auth_token)
                                await self.close_client_connection(this_client)
                        else:
                            # Client provided no password, but might
                            # already be authenticated by IP, so do
                            # nothing
                            pass
                        continue
                    if messagetype == 'pong':
                        self.logger.debug(
                            "Got FRDP pong message from client %s: %s", client_addr, line)
                        elapsed = time.perf_counter() - this_client['ping_sent']
                        if elapsed > self.args.frdp_warning:
                            self.logger.warning(
                                "SLOW: FRDP RTT to client %s is %.6f s", client_addr, elapsed)
                        (identifier, request_id, connected_clients) = message.split(":", 2)
                        connected_clients = int(connected_clients)
                        this_client['ping_rtts'].append(elapsed)
                        this_client['connected_clients'] = connected_clients
                        continue
                    self.logger.critical(
                        "Unsupported FRDP message (%s): %s", messagetype, line)
                    continue

                # Print non-PSX keywoards (e.g "name") if --print-client-non-psx
                if self.args.print_non_psx:
                    if not self.variables.is_psx_keyword(key):
                        self.logger.info("NONPSX keyword %s from %s: %s", key, client_addr, line)

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

                if key == 'name' and value != "" and not this_client['is_frankenrouter']:
                    learned_prefix = "L"
                    thisname = value
                    self.logger.info("Checking %s against name regexps", value)
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
                            this_client['peername'], thisname, newname)
                        thisname = newname
                    else:
                        self.logger.info(
                            "Client %s identifies as %s, using that name",
                            this_client['peername'], thisname)
                    this_client['identifier'] = f"{learned_prefix}:{thisname}"
                    self.print_status()

                if key == 'name':
                    self.logger.info(
                        "Not passing on name= keyword from %s to upstream", this_client['id'])
                    continue

                allow_write = False
                if this_client['access'] == 'full':
                    allow_write = True
                elif key in ['demand']:
                    # read-only clients may still send demand=...
                    allow_write = True

                if allow_write:
                    if key in ["bang", "again"]:
                        # Forward to server but not other clients
                        await self.send_to_server(key, client_addr)
                    elif key in ["start"]:
                        # Send to server, not other clients
                        self.logger.debug("start received from %s, sending to server", client_addr)
                        await self.send_to_server(key, client_addr)
                        self.start_sent_at = time.perf_counter()
                    elif key in ["demand"]:
                        # Add to list for this client
                        this_client['demands'].add(value)
                        self.logger.debug(
                            "added %s to demand list for %s + send to server", value, client_addr)
                        await self.send_to_server(line, client_addr)
                    elif key in ["load1", "load2", "load3"]:
                        # Forward to server and other clients
                        self.logger.info("%s from %s", key, client_addr)
                        await self.send_to_server(key, client_addr)
                        await self.client_broadcast(key, exclude=[client_addr])
                    elif key in ["pleaseBeSoKindAndQuit"]:
                        # Forward to other clients
                        self.logger.info("Forwarding %s from %s to all clients", key, client_addr)
                        await self.client_broadcast(key, exclude=[client_addr])
                        if self.args.forward_please_be_so_kind_and_quit_upstream:
                            self.logger.info("Forwarding %s from %s to upstream", key, client_addr)
                            await self.send_to_server(key, client_addr)
                    elif key == 'exit':
                        # Shut down client connection cleanly
                        self.logger.info("Client %s sent exit message, closing", client_addr)
                        await self.close_client_connection(this_client)
                        return
                    elif sep != "":
                        self.cache.update(key, value)
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
                t_data_forwarded = time.perf_counter()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical(
                "Unhandled exception %s in callback %s, shutting down %s connection",
                exc, inspect.currentframe().f_code.co_name, client_addr)
            self.logger.critical(traceback.format_exc())
            await self.close_client_connection(this_client)
            self.print_status()
            self.logger.info("Connection %s shut down", client_addr)

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

        sent_to_clients = []

        for client in self.clients.values():  # pylint: disable=too-many-nested-blocks
            if client['access'] == 'noaccess':
                self.logger.debug(
                    "Not sending to noaccess client %s", client['peername'])
                continue
            if exclude and client['peername'] in exclude:
                self.logger.debug(
                    "Not sending to excluded client %s", client['peername'])
                continue
            if include and client['peername'] not in include:
                self.logger.debug(
                    "Not sending to non-included client %s", client['peername'])
                continue
            if islong and client['nolong']:
                self.logger.debug(
                    "Not sending long string to nolong client %s: %s",
                    client['peername'], line)
                continue
            if isonlystart:
                self.logger.debug("isonlystart for %s", client['peername'])
                if client['is_frankenrouter']:
                    # send
                    pass
                elif client['waiting_for_start_keywords']:
                    # send
                    client['welcome_keywords_sent'].add(key)
                else:
                    self.logger.debug(
                        "Not sending START variable to %s: %s",
                        client['peername'], line)
                    continue
            if client['access'] == 'noaccess':
                self.logger.debug(
                    "Not sending to noaccess client %s", client['peername'])
                continue
            if not client['welcome_sent']:
                self.logger.debug("Client %s not welcomed, adding to pending_messages: %s",
                                  client['peername'], line)
                client['pending_messages'].append(line)
                continue
            await self.to_stream(client, line)
            self.logger.debug("To %s: %s", client['peername'], line)
            sent_to_clients.append(client['id'])

        # Log to single file
        if len(sent_to_clients) == 0:
            destination = 'no clients'
        else:
            destination = "clients " + ",".join(map(str, sent_to_clients))
            await self.log_data(line, endpoints=destination, inbound=False)

    async def send_to_server(self, line, client_addr=None):
        """Send a line to the PSX main server."""
        if not self.is_server_connected():
            self.server_pending_messages.append(line)
            self.logger.info(
                "Server is not connected, storing data for later send (%d entries): %s",
                len(self.server_pending_messages), line)
            return
        await self.to_stream(self.server, line)
        await self.log_data(line, inbound=False)
        self.logger.debug("To server from %s: %s", client_addr, line)

    async def handle_server_connection(self):  # pylint: disable=too-many-branches,too-many-statements,too-many-locals
        """Set up and maintain a PSX server connection."""
        while not self.shutdown_requested:  # pylint: disable=too-many-nested-blocks
            self.logger.info("Pausing clients (load1) before connecting to server")
            await self.client_broadcast("load1")
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
                'ping_rtts': [],
                'write_drain': [],
            }
            self.server_reconnects += 1
            self.logger.info("Connected to server: %s", server_addr)

            # Send our name (for when we connect to another router)
            await self.send_to_server(f"name={self.args.sim_name}:FRANKEN.PY frankenrouter PSX router {self.args.sim_name}")  # pylint: disable=line-too-long

            # (re)Send demand= for all keywords that any client has demanded
            clients_demand = set()
            for peername, data in self.clients.items():
                for demand_var in data['demands']:
                    self.logger.info(
                        "Adding demand variable %s from %s to req list",
                        demand_var, peername)
                    clients_demand.add(demand_var)
            for demand_var in clients_demand:
                self.logger.info("Sending demand=%s to server")
                await self.send_to_server(f"demand={demand_var}")

            if len(self.server_pending_messages) > 0:
                self.logger.info(
                    "Sending %d held messages to server",
                    len(self.server_pending_messages)
                )
                for message in self.server_pending_messages:
                    await self.send_to_server(message)
            self.server_pending_messages = []

            self.print_status()

            # Wait for and process data from server connection
            t_read_data = 0
            t_data_forwarded = 0
            while self.is_server_connected():
                # We know the protocol is line-oriented and the lines will
                # not be too long to handle as a single unit, so we can
                # read one line at a time.
                if t_read_data > 0 and t_data_forwarded > 0:
                    t_elapsed_since_last_data_read = time.perf_counter() - t_read_data
                    t_elapsed_from_read_to_forwarded = t_data_forwarded - t_read_data
                    if t_elapsed_from_read_to_forwarded > FORWARDING_TIME_WARNING:
                        self.logger.warning(
                            "Forwarding delay for server was %.6f s",
                            t_elapsed_from_read_to_forwarded)

                    if t_elapsed_since_last_data_read > NOPOLL_WARNING:
                        self.logger.info(
                            "Nopoll time for server was %.6f s",
                            t_elapsed_since_last_data_read)
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
                t_read_data = time.perf_counter()

                if data == b'':
                    self.logger.info(
                        "Server disconnected, sleeping %.1f s before reconnect",
                        PSX_SERVER_RECONNECT_DELAY,
                    )
                    await self.close_server_connection()
                    await asyncio.sleep(PSX_SERVER_RECONNECT_DELAY)
                    break
                # Note: we can get a partial line at EOF, so discard
                # data with no newline.
                if not data.endswith(b'\n'):
                    self.logger.warning("Got partial line data from server, discarding: %s", data)
                    continue
                line = data.decode().strip()

                self.logger.debug("From server: %s", line)
                self.from_stream(self.server, line)
                await self.log_data(line)

                # Store various things that we get e.g on initial
                # connection and that we might need later.
                key, sep, value = line.partition("=")

                # FrankenRouter DiscoveryProtocol :)
                if self.args.password:
                    # assume server is frankenrouter if we use --password
                    self.server['is_frankenrouter'] = True

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
                        elapsed = time.perf_counter() - self.server['ping_sent']
                        if elapsed > self.args.frdp_warning:
                            self.logger.warning("SLOW: FRDP RTT to server is %.6f s", elapsed)
                        (identifier, request_id) = message.split(":", 1)
                        self.server['ping_rtts'].append(elapsed)
                        continue
                    self.logger.critical("Unsupported FRDP message (%s): %s", messagetype, line)
                    continue

                if self.args.print_non_psx:
                    if not self.variables.is_psx_keyword(key):
                        self.logger.info("NONPSX keyword %s from server: %s", key, line)

                if key in [
                        'load1',
                        'load2',
                        'load3',
                ]:
                    # Load messages: send to connected clients
                    self.logger.info("Load message from server: %s", key)
                    await self.client_broadcast(line)
                elif key in [
                        'bang',
                        'start',
                ]:
                    # Should not be sent by server, ignore
                    pass
                elif key in ["pleaseBeSoKindAndQuit"]:
                    # Forward to clients
                    self.logger.info(
                        "Forwarding %s from server to all clients", key)
                    await self.client_broadcast(key)
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
                    self.logger.warning("Unhandled data from server: %s", line)
                t_data_forwarded = time.perf_counter()

    async def shutdown(self):
        """Shut down the proxy.

        In hard mode we try to call as little code as possible to
        avoid triggering exceptions.
        """
        self.print_status()
        self.shutdown_requested = True
        self.logger.info("Shutting down")
        await self.client_broadcast("exit")
        self.logger.info("Exit message sent to clients, sleeping")
        await asyncio.sleep(1)

        self.logger.info("Closing listener")
        self.proxy_server.close()
        await self.proxy_server.wait_closed()
        self.clients = {}

        if self.is_server_connected():
            self.logger.info("Closing server connection %s", self.server['peername'])
            try:
                await self.close_server_connection()
            except ConnectionResetError:
                pass
            self.server = {}
            self.cache.write_to_file(self.args.state_cache_file)
            self.shutdown_requested = False

    async def run_listener(self):
        """Start the listener."""
        while not self.shutdown_requested:
            try:
                self.proxy_server = await asyncio.start_server(
                    self.handle_new_connection_cb,
                    host=self.args.listen_host,
                    port=self.args.listen_port,
                    limit=self.args.server_buffer_size
                )
                while True:  # wait forever
                    await asyncio.sleep(3600.0)

            except Exception as exc:  # pylint: disable=broad-exception-caught
                self.logger.critical(
                    "asyncio.start_server caught unhandled exception %s, restarting listener", exc)
                self.proxy_server = None
                self.clients = {}
                await asyncio.sleep(1.0)

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

    async def routermonitor(self):  # pylint: disable=too-many-branches
        """Monitor the router and shut down when requested."""
        last_ping = time.perf_counter()
        started = time.perf_counter()
        while True:
            await asyncio.sleep(1.0)
            if self.args.stop_after:
                if time.perf_counter() - started > self.args.stop_after:
                    raise SystemExit("Maxiumum runtime reached")
            elapsed_since_ping = time.perf_counter() - last_ping
            if elapsed_since_ping > self.args.frdp_interval:
                # If connected to a frankenrouter server, send FRDP ping
                if self.is_server_connected() and self.server['is_frankenrouter']:
                    self.logger.debug("Sending FRDP ping to server")
                    frdp_request_id = self.get_random_id()
                    if self.args.password:
                        await self.send_to_server(
                            "%s=%s:%s:%s:%s" % (  # pylint: disable=consider-using-f-string
                                "frankenrouter",
                                "ping",
                                self.args.sim_name,
                                frdp_request_id,
                                self.args.password,
                            )
                        )
                    else:
                        await self.send_to_server(
                            f"frankenrouter=ping:{self.args.sim_name}:{frdp_request_id}:")
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
            if time.perf_counter() - self.last_status_print > self.args.status_interval:
                self.print_status()
                self.print_aircraft_status()
                self.last_status_print = time.perf_counter()
                # Make sure log data is flushed to disk
                if self.log_data_file:
                    self.log_data_file.flush()
                # Write chache to disk
                self.cache.write_to_file(self.args.state_cache_file)
            if self.shutdown_requested:
                await self.shutdown()
                self.logger.info("Monitor shutting down")
                return

    async def main(self):
        """Start the proxy."""
        self.handle_args()

        # Get information from Variables.txt
        self.variables = variables.Variables(
            vfilepath=self.args.variables_file)

        # Initialize logging
        router_log_file = os.path.join(
            self.args.log_dir,
            f"frankenrouter-{self.args.sim_name}.log"
        )
        if os.path.exists(router_log_file):
            if os.path.exists(router_log_file + ".OLD"):
                os.unlink(router_log_file + ".OLD")
            os.rename(router_log_file, router_log_file + ".OLD")
        log_format = "%(asctime)s: %(message)s"
        logging.basicConfig(
            format=log_format,
            level=logging.INFO,
            datefmt="%H:%M:%S",
            handlers=[
                logging.FileHandler(router_log_file),
                logging.StreamHandler(sys.stdout)
            ],
        )
        self.logger = logging.getLogger("frankenrouter")
        self.logger.info("Started logging to %s", router_log_file)
        if self.args.debug:
            self.logger.setLevel(logging.DEBUG)

        # Logging traffic data to a single file
        if self.args.log_data:
            self.log_data_filename = os.path.join(
                self.args.log_dir,
                f"frankenrouter-{self.args.sim_name}-traffic-{self.start_time}.psxnet.log"
            )
            self.log_data_file = open(self.log_data_filename, 'w', encoding='utf-8')  # pylint: disable=consider-using-with

        self.cache = routercache.RouterCache()
        if not self.args.no_state_cache_file:
            self.cache.read_from_file(self.args.state_cache_file)

        self.logger.info("frankenrouter version %s starting", VERSION)

        while True:
            try:
                await asyncio.gather(
                    self.run_listener(),
                    self.handle_server_connection(),
                    self.routermonitor(),
                )
            except Exception as exc:  # pylint: disable=broad-exception-caught
                uptime = int(time.perf_counter() - self.start_time)
                self.logger.critical("Unhandled exception after %d s uptime: %s", uptime, exc)
                self.logger.critical(traceback.format_exc())
                if self.args.stop_on_exception:
                    self.logger.critical("Shutting down...")
                    await self.shutdown()
                    break
                self.logger.critical("Trying to restart myself...")
                try:
                    await self.shutdown()
                except Exception:  # pylint: disable=broad-exception-caught
                    pass
                time.sleep(5)
                self.router_restarts += 1
                continue


if __name__ == '__main__':
    me = Frankenrouter()
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(me.main())
    except KeyboardInterrupt as exc:
        print("Caught KeyboardInterrupt, shutting down")
        loop.run_until_complete(me.shutdown())
    finally:
        print("Frankenrouter exited")
