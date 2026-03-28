#!/usr/bin/env python3
"""Measure frankenrouter forwarding latency with multiple concurrent clients.

This script acts simultaneously as a fake PSX upstream server and N fake PSX
clients to measure the end-to-end message forwarding latency of a frankenrouter
under realistic multi-client load.

Setup
-----
1. Configure the router's upstream host/port to point at this script
   (--listen-host / --listen-port, default 0.0.0.0:10747).
2. Run this script.  It will wait for the router to connect as upstream,
   then open --num-clients connections to the router on --router-host:--router-port.
3. Once all clients have completed their welcome sequence, messages are sent at
   --rate msg/s and latency is reported every --report-interval seconds.

Directions
----------
  upstream  (default)
      fake-server --[keyword=seqno]--> router --[keyword=seqno]--> ALL fake-clients
      One latency sample is recorded per (message, client) pair, so each sent
      message produces --num-clients samples.  This shows how broadcast latency
      scales with the number of connected clients.

  client
      fake-clients --[keyword=seqno]--> router --[keyword=seqno]--> fake-server
      Messages are sent round-robin across all clients.  One latency sample is
      recorded per sent message.  Requires the router to grant 'full' write
      access to the connecting clients.

Keyword choice
--------------
Use an ECON-mode keyword present in Variables.txt, e.g. 'Qs3' or 'Qi224'.
Unknown keywords are forwarded with a warning log on the router.
Each message uses a unique sequence number so duplicate-suppression is avoided.
"""
import argparse
import asyncio
import collections
import statistics
import time

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

SEP = b'\r\n'


class LatencyMeasurer:
    def __init__(self, args):
        self.args = args
        self.num_clients = args.num_clients

        # seqno → perf_counter timestamp of send
        self.send_times: dict[int, float] = {}
        # seqno → number of client receipts recorded so far (upstream direction)
        self.receipt_counts: dict[int, int] = {}
        # all measured latencies in seconds — rolling window, bounded to avoid unbounded growth
        self.latencies: collections.deque[float] = collections.deque(maxlen=100_000)
        # latency samples collected during the current ramp interval (cleared by ramp_controller)
        self.current_interval_lats: list[float] = []
        self.seqno = 0

        self.upstream_writer: asyncio.StreamWriter | None = None
        self.upstream_ready = asyncio.Event()

        # One writer per fake client; populated as clients complete their welcome
        self.client_writers: list[asyncio.StreamWriter] = []
        self.clients_welcomed = 0
        self.all_clients_ready = asyncio.Event()

        # CPU monitoring — rolling window of 1-second samples
        self.cpu_samples: collections.deque[float] = collections.deque(maxlen=300)
        self.router_pid: int | None = None
        # CPU samples collected during the current ramp interval (cleared by ramp_controller)
        self.current_interval_cpu: list[float] = []

        # Ramp mode state
        self.current_rate: float = 1.0   # sender reads this; ramp_controller writes it
        self.ramp_stop = asyncio.Event()  # set when ramp is complete

        # Peer mode state
        self.peer_a_writer: asyncio.StreamWriter | None = None
        self.peer_clients_welcomed = 0
        self.peer_clients_ready = asyncio.Event()

    # ------------------------------------------------------------------
    # Low-level I/O
    # ------------------------------------------------------------------

    @staticmethod
    async def read_line(reader: asyncio.StreamReader) -> str | None:
        """Read one PSX protocol line, returning None on EOF/error."""
        while True:
            try:
                data = await reader.readuntil(b'\n')
            except (asyncio.IncompleteReadError, ConnectionError, OSError):
                return None
            line = data.rstrip(b'\r\n').decode(errors='replace')
            if line:  # skip bare separator artifacts
                return line

    @staticmethod
    async def write_line(writer: asyncio.StreamWriter, line: str) -> None:
        writer.write(line.encode() + SEP)
        await writer.drain()

    # ------------------------------------------------------------------
    # Latency bookkeeping
    # ------------------------------------------------------------------

    def record_upstream_receipt(self, line: str) -> None:
        """Record a test-message receipt on one of the fake clients (upstream direction).

        The send_times entry is kept until all num_clients have received the
        message, so that each client contributes one latency sample.
        """
        eq = line.find('=')
        if eq == -1:
            return
        if line[:eq] != self.args.keyword:
            return
        try:
            seqno = int(line[eq + 1:])
        except ValueError:
            return
        t_sent = self.send_times.get(seqno)
        if t_sent is None:
            return  # already fully received or unknown seqno
        lat = time.perf_counter() - t_sent
        self.latencies.append(lat)
        self.current_interval_lats.append(lat)
        self.receipt_counts[seqno] = self.receipt_counts.get(seqno, 0) + 1
        if self.receipt_counts[seqno] >= self.num_clients:
            del self.send_times[seqno]
            del self.receipt_counts[seqno]

    def record_client_receipt(self, line: str) -> None:
        """Record a test-message receipt at the fake server (client direction).

        One sample per sent message (the server receives each message once).
        """
        eq = line.find('=')
        if eq == -1:
            return
        if line[:eq] != self.args.keyword:
            return
        try:
            seqno = int(line[eq + 1:])
        except ValueError:
            return
        t_sent = self.send_times.pop(seqno, None)
        if t_sent is not None:
            lat = time.perf_counter() - t_sent
            self.latencies.append(lat)
            self.current_interval_lats.append(lat)

    # ------------------------------------------------------------------
    # Router CPU monitoring
    # ------------------------------------------------------------------

    def find_router_pid(self) -> int | None:
        """Return the PID of the running frankenrouter process, or None."""
        if not PSUTIL_AVAILABLE:
            return None
        for proc in psutil.process_iter(['pid', 'cmdline']):
            try:
                cmdline = proc.info['cmdline'] or []
                if any('frankenrouter' in arg for arg in cmdline):
                    return proc.info['pid']
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return None

    async def cpu_monitor(self) -> None:
        """Sample the router process CPU usage once per second."""
        try:
            proc = psutil.Process(self.router_pid)
            proc.cpu_percent()  # first call always returns 0 — use as baseline
            while True:
                await asyncio.sleep(1.0)
                try:
                    sample = proc.cpu_percent()
                    self.cpu_samples.append(sample)
                    self.current_interval_cpu.append(sample)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    print('[cpu] Router process gone, stopping CPU monitor')
                    return
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            print(f'[cpu] Cannot monitor PID {self.router_pid}: {exc}')

    # ------------------------------------------------------------------
    # Fake PSX upstream server  (router connects here)
    # ------------------------------------------------------------------

    async def upstream_handler(
            self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Called when the router connects to us as its upstream PSX server."""
        peername = writer.get_extra_info('peername')
        print(f'[upstream] Router connected from {peername}')
        self.upstream_writer = writer

        # Send a minimal PSX state so the router has something to cache and
        # can complete the client welcome sequence without hanging.
        for line in ('version=10.184 NG', 'layout=1'):
            await self.write_line(writer, line)

        self.upstream_ready.set()

        try:
            while True:
                line = await self.read_line(reader)
                if line is None:
                    print('[upstream] Router disconnected')
                    break
                # Silently consume protocol messages the router sends upstream
                if (line in ('start', 'bang', 'again')
                        or line.startswith('name=')
                        or line.startswith('demand=')
                        or line.startswith('addon=')
                        or line.startswith('id=')):
                    continue
                # In 'client' direction our test messages arrive here
                self.record_client_receipt(line)
        except Exception as exc:
            print(f'[upstream] Handler error: {exc}')

    # ------------------------------------------------------------------
    # Fake PSX clients  (connect to the router)
    # ------------------------------------------------------------------

    async def run_client(self, client_id: int) -> None:
        """Connect to the router as one fake PSX client and consume the welcome."""
        host = self.args.router_host
        port = self.args.router_port
        tag = f'client-{client_id}'

        while True:
            try:
                reader, writer = await asyncio.open_connection(host, port)
                break
            except (ConnectionRefusedError, OSError):
                if client_id == 0:
                    print(f'[{tag}] Connection refused, retrying in 2 s ...')
                await asyncio.sleep(2.0)

        print(f'[{tag}] Connected. Waiting for welcome (load3) ...')

        # Consume the welcome sequence; discard everything until load3
        while True:
            line = await self.read_line(reader)
            if line is None:
                print(f'[{tag}] Router closed connection during welcome')
                return
            if line == 'load3':
                break

        self.client_writers.append(writer)
        self.clients_welcomed += 1
        print(
            f'[{tag}] Welcome complete'
            f' ({self.clients_welcomed}/{self.num_clients} clients ready)'
        )
        if self.clients_welcomed >= self.num_clients:
            self.all_clients_ready.set()

        # After welcome: read and record test messages (upstream direction)
        while True:
            line = await self.read_line(reader)
            if line is None:
                print(f'[{tag}] Disconnected from router')
                break
            self.record_upstream_receipt(line)

    def record_peer_receipt(self, line: str) -> None:
        """Record an addon=LATENCYTEST receipt on peer client B."""
        if not line.startswith('addon=LATENCYTEST:'):
            return
        try:
            seqno = int(line[len('addon=LATENCYTEST:'):])
        except ValueError:
            return
        t_sent = self.send_times.pop(seqno, None)
        if t_sent is not None:
            lat = time.perf_counter() - t_sent
            self.latencies.append(lat)
            self.current_interval_lats.append(lat)

    # ------------------------------------------------------------------
    # Peer mode clients
    # ------------------------------------------------------------------

    async def run_peer_client(self, label: str, host: str, port: int, is_receiver: bool) -> None:
        """Connect one peer client, complete the welcome sequence, then read messages.

        The sender side (is_receiver=False) stores its writer for peer_sender to use.
        The receiver side (is_receiver=True) records incoming LATENCYTEST messages.
        """
        while True:
            try:
                reader, writer = await asyncio.open_connection(host, port)
                break
            except (ConnectionRefusedError, OSError):
                print(f'[{label}] Connection refused, retrying in 2 s ...')
                await asyncio.sleep(2.0)

        print(f'[{label}] Connected to {host}:{port}. Waiting for welcome (load3) ...')

        while True:
            line = await self.read_line(reader)
            if line is None:
                print(f'[{label}] Router closed connection during welcome')
                return
            if line == 'load3':
                break

        if not is_receiver:
            self.peer_a_writer = writer

        self.peer_clients_welcomed += 1
        print(
            f'[{label}] Welcome complete'
            f' ({self.peer_clients_welcomed}/2 peer clients ready)'
        )
        if self.peer_clients_welcomed >= 2:
            self.peer_clients_ready.set()

        while True:
            line = await self.read_line(reader)
            if line is None:
                print(f'[{label}] Disconnected')
                break
            if is_receiver:
                self.record_peer_receipt(line)

    async def peer_sender(self) -> None:
        """Send addon=LATENCYTEST:<seqno> at --rate msg/s once both peer clients are ready."""
        await self.peer_clients_ready.wait()

        rate = self.args.rate
        interval = 1.0 / rate
        print(f'[peer-sender] Both clients ready — sending {rate} msg/s')

        next_send = time.perf_counter()
        while True:
            self.seqno += 1
            seqno = self.seqno
            self.send_times[seqno] = time.perf_counter()
            await self.write_line(self.peer_a_writer, f'addon=LATENCYTEST:{seqno}')

            next_send += interval
            sleep_for = next_send - time.perf_counter()
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
            else:
                await asyncio.sleep(0)

    async def peer_reporter(self) -> None:
        """Print peer latency statistics at regular intervals."""
        interval = self.args.report_interval

        while True:
            await asyncio.sleep(interval)
            sent = self.seqno
            pending = len(self.send_times)
            received = len(self.latencies)
            ts = time.strftime('%H:%M:%S')

            if self.latencies:
                lats = self.latencies
                print(
                    f'[{ts}]'
                    f'  sent={sent:7d}'
                    f'  recv={received:7d}'
                    f'  pending={pending:4d}'
                    f'  |'
                    f'  min={min(lats) * 1000:8.3f} ms'
                    f'  max={max(lats) * 1000:8.3f} ms'
                    f'  avg={statistics.mean(lats) * 1000:8.3f} ms'
                    f'  median={statistics.median(lats) * 1000:8.3f} ms'
                )
            else:
                print(f'[{ts}]  sent={sent:7d}  — no responses yet')

    async def run_all_clients(self) -> None:
        """Launch all fake client connections concurrently."""
        # Stagger connections slightly so the router isn't flooded with
        # simultaneous welcome sequences, which would inflate startup latency.
        stagger = self.args.connect_stagger
        tasks = []
        for i in range(self.num_clients):
            if i > 0 and stagger > 0:
                await asyncio.sleep(stagger)
            tasks.append(asyncio.ensure_future(self.run_client(i)))
        await asyncio.gather(*tasks)

    # ------------------------------------------------------------------
    # Sender
    # ------------------------------------------------------------------

    async def sender(self) -> None:
        """Send test messages at the configured rate once all sides are ready."""
        await asyncio.gather(
            self.upstream_ready.wait(),
            self.all_clients_ready.wait(),
        )

        keyword = self.args.keyword
        direction = self.args.direction
        rate = self.args.rate
        interval = 1.0 / rate

        print(
            f'[sender] All {self.num_clients} client(s) ready —'
            f' sending {rate} msg/s using {keyword}=<seqno>, direction={direction}'
        )

        next_send = time.perf_counter()
        while True:
            self.seqno += 1
            seqno = self.seqno
            line = f'{keyword}={seqno}'
            self.send_times[seqno] = time.perf_counter()

            if direction == 'upstream':
                # Send from the fake server; all clients will receive it
                await self.write_line(self.upstream_writer, line)
            else:
                # Round-robin across all fake clients
                writer = self.client_writers[(seqno - 1) % len(self.client_writers)]
                await self.write_line(writer, line)

            next_send += interval
            sleep_for = next_send - time.perf_counter()
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
            else:
                await asyncio.sleep(0)  # yield without sleeping if behind

    # ------------------------------------------------------------------
    # Reporter
    # ------------------------------------------------------------------

    async def reporter(self) -> None:
        """Print latency statistics at regular intervals."""
        interval = self.args.report_interval
        direction = self.args.direction
        num_clients = self.num_clients

        while True:
            await asyncio.sleep(interval)
            sent = self.seqno
            pending = len(self.send_times)
            received = len(self.latencies)
            # In upstream direction each send produces num_clients samples
            expected = sent * num_clients if direction == 'upstream' else sent
            ts = time.strftime('%H:%M:%S')

            if self.latencies:
                lats = self.latencies
                print(
                    f'[{ts}]'
                    f'  clients={num_clients}'
                    f'  sent={sent:7d}'
                    f'  expected={expected:7d}'
                    f'  recv={received:7d}'
                    f'  pending={pending:4d}'
                    f'  |'
                    f'  min={min(lats) * 1000:8.3f} ms'
                    f'  max={max(lats) * 1000:8.3f} ms'
                    f'  avg={statistics.mean(lats) * 1000:8.3f} ms'
                    f'  median={statistics.median(lats) * 1000:8.3f} ms'
                )
            else:
                print(
                    f'[{ts}]'
                    f'  clients={num_clients}'
                    f'  sent={sent:7d}'
                    f'  — no responses yet'
                )

            if self.cpu_samples:
                cpu = list(self.cpu_samples)
                print(
                    f'         router CPU (pid {self.router_pid})'
                    f'  |'
                    f'  min={min(cpu):5.1f}%'
                    f'  max={max(cpu):5.1f}%'
                    f'  avg={statistics.mean(cpu):5.1f}%'
                    f'  median={statistics.median(cpu):5.1f}%'
                    f'  (last {len(cpu)} s)'
                )

    # ------------------------------------------------------------------
    # Ramp mode: sender and controller
    # ------------------------------------------------------------------

    async def ramp_sender(self) -> None:
        """Send test messages whose rate is controlled externally by ramp_controller."""
        await asyncio.gather(self.upstream_ready.wait(), self.all_clients_ready.wait())

        keyword = self.args.keyword
        direction = self.args.direction
        rate = self.current_rate
        next_send = time.perf_counter()

        while not self.ramp_stop.is_set():
            # Pick up rate changes from ramp_controller and reset the
            # accumulator so we don't burst-send to catch up.
            if self.current_rate != rate:
                rate = self.current_rate
                next_send = time.perf_counter()

            self.seqno += 1
            seqno = self.seqno
            line = f'{keyword}={seqno}'
            self.send_times[seqno] = time.perf_counter()

            if direction == 'upstream':
                await self.write_line(self.upstream_writer, line)
            else:
                writer = self.client_writers[(seqno - 1) % len(self.client_writers)]
                await self.write_line(writer, line)

            next_send += 1.0 / rate
            sleep_for = next_send - time.perf_counter()
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
            else:
                await asyncio.sleep(0)

    async def ramp_controller(self) -> None:
        """Increase message rate until router CPU hits the target, then report."""
        import sys

        await asyncio.gather(self.upstream_ready.wait(), self.all_clients_ready.wait())

        target_cpu = self.args.ramp_target_cpu
        step_interval = self.args.ramp_interval
        factor = self.args.ramp_factor
        max_rate = self.args.ramp_max_rate

        print(
            f'[ramp] Starting ramp: 1 msg/s → target {target_cpu:.0f}% CPU'
            f' | step {step_interval}s | factor ×{factor} | max {max_rate} msg/s'
        )

        # Give cpu_monitor time to collect a baseline before the first measurement
        await asyncio.sleep(2.0)

        rate = 1.0
        self.current_rate = rate

        while True:
            # Reset per-interval accumulators
            self.current_interval_lats.clear()
            self.current_interval_cpu.clear()

            await asyncio.sleep(step_interval)

            # Collect interval statistics
            interval_lats = list(self.current_interval_lats)
            cpu_window = list(self.current_interval_cpu)
            avg_cpu = statistics.mean(cpu_window) if cpu_window else 0.0

            lat_info = ''
            if interval_lats:
                lat_info = (
                    f'  lat_avg={statistics.mean(interval_lats) * 1000:.1f} ms'
                    f'  lat_max={max(interval_lats) * 1000:.1f} ms'
                )
            else:
                lat_info = '  (no latency samples yet)'

            print(f'[ramp] rate={rate:8.1f} msg/s  CPU={avg_cpu:5.1f}%{lat_info}')

            if avg_cpu >= target_cpu:
                print()
                print(f'[ramp] *** TARGET REACHED ***')
                print(f'[ramp] {rate:.1f} msg/s produces {avg_cpu:.1f}% router CPU')
                if interval_lats:
                    print(f'[ramp] Latency at this rate ({len(interval_lats)} samples):')
                    print(f'[ramp]   min    = {min(interval_lats) * 1000:.3f} ms')
                    print(f'[ramp]   max    = {max(interval_lats) * 1000:.3f} ms')
                    print(f'[ramp]   avg    = {statistics.mean(interval_lats) * 1000:.3f} ms')
                    print(f'[ramp]   median = {statistics.median(interval_lats) * 1000:.3f} ms')
                self.ramp_stop.set()
                await asyncio.sleep(0.5)  # let in-flight messages drain
                sys.exit(0)

            next_rate = rate * factor
            if next_rate > max_rate:
                print()
                print(
                    f'[ramp] Reached max rate {max_rate} msg/s'
                    f' without hitting {target_cpu:.0f}% CPU (last CPU: {avg_cpu:.1f}%)'
                )
                self.ramp_stop.set()
                sys.exit(0)

            rate = next_rate
            self.current_rate = rate

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def run(self) -> None:
        if self.args.mode == 'peer':
            await self._run_peer()
            return

        server = await asyncio.start_server(
            self.upstream_handler,
            host=self.args.listen_host,
            port=self.args.listen_port,
        )
        addrs = ', '.join(str(s.getsockname()) for s in server.sockets)
        print(f'Fake PSX upstream server listening on {addrs}')
        print(f'  → configure the router upstream to connect here')
        print(
            f'  → will open {self.num_clients} client connection(s) to'
            f' {self.args.router_host}:{self.args.router_port}'
        )

        # Resolve router PID for CPU monitoring
        if PSUTIL_AVAILABLE and not self.args.no_cpu:
            if self.args.router_pid:
                self.router_pid = self.args.router_pid
                print(f'[cpu] Monitoring router PID {self.router_pid} (from --router-pid)')
            else:
                self.router_pid = self.find_router_pid()
                if self.router_pid:
                    print(f'[cpu] Auto-detected router PID {self.router_pid}')
                else:
                    print('[cpu] Could not find frankenrouter process; use --router-pid to set it')
        elif not PSUTIL_AVAILABLE and not self.args.no_cpu:
            print('[cpu] psutil not installed — CPU monitoring disabled (pip install psutil)')

        if self.args.mode == 'ramp':
            if not PSUTIL_AVAILABLE:
                print('Error: ramp mode requires psutil — pip install psutil')
                return
            if self.router_pid is None:
                print(
                    'Error: ramp mode requires CPU monitoring.\n'
                    'Auto-detection failed; use --router-pid to specify the PID.'
                )
                return
            tasks = [
                self.run_all_clients(),
                self.ramp_sender(),
                self.ramp_controller(),
                self.cpu_monitor(),
            ]
        else:
            tasks = [
                self.run_all_clients(),
                self.sender(),
                self.reporter(),
            ]
            if self.router_pid is not None:
                tasks.append(self.cpu_monitor())

        async with server:
            await asyncio.gather(*tasks)

    async def _run_peer(self) -> None:
        """Entry point for peer mode — no fake upstream server needed."""
        a_host = self.args.peer_a_host
        a_port = self.args.peer_a_port
        b_host = self.args.peer_b_host
        b_port = self.args.peer_b_port
        print(
            f'Peer mode: sending from {a_host}:{a_port}'
            f' → receiving at {b_host}:{b_port}'
        )
        print(f'  keyword: addon=LATENCYTEST:<seqno>  rate: {self.args.rate} msg/s')
        await asyncio.gather(
            self.run_peer_client('peer-a', a_host, a_port, is_receiver=False),
            self.run_peer_client('peer-b', b_host, b_port, is_receiver=True),
            self.peer_sender(),
            self.peer_reporter(),
        )


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description='Measure frankenrouter forwarding latency with multiple clients',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog=(
            'Example: python test_latency.py --num-clients 10 --rate 50'
            ' --keyword Qs3 --direction upstream'
        ),
    )
    parser.add_argument(
        '--listen-host', default='0.0.0.0', metavar='HOST',
        help='Host to listen on (fake PSX upstream server)',
    )
    parser.add_argument(
        '--listen-port', type=int, default=10747, metavar='PORT',
        help='Port to listen on (fake PSX upstream server)',
    )
    parser.add_argument(
        '--router-host', default='127.0.0.1', metavar='HOST',
        help='Router host to connect to as fake PSX clients',
    )
    parser.add_argument(
        '--router-port', type=int, default=10748, metavar='PORT',
        help='Router port to connect to as fake PSX clients',
    )
    parser.add_argument(
        '--num-clients', type=int, default=1, metavar='N',
        help='Number of simultaneous fake PSX client connections',
    )
    parser.add_argument(
        '--connect-stagger', type=float, default=0.1, metavar='SECONDS',
        help='Delay between successive client connections (avoids welcome flood)',
    )
    parser.add_argument(
        '--rate', type=float, default=10.0, metavar='MSG/S',
        help='Test message send rate in messages per second',
    )
    parser.add_argument(
        '--keyword', default='Qs3', metavar='KEYWORD',
        help='PSX ECON-mode keyword to use for test messages',
    )
    parser.add_argument(
        '--direction', choices=['upstream', 'client'], default='upstream',
        help=(
            '"upstream": fake server → router → all fake clients '
            '(num_clients samples per send); '
            '"client": fake clients → router → fake server, round-robin '
            '(requires full write access)'
        ),
    )
    parser.add_argument(
        '--report-interval', type=float, default=10.0, metavar='SECONDS',
        help='How often to print the latency summary',
    )
    parser.add_argument(
        '--router-pid', type=int, default=None, metavar='PID',
        help='PID of the frankenrouter process to monitor (auto-detected if not set)',
    )
    parser.add_argument(
        '--no-cpu', action='store_true',
        help='Disable router CPU monitoring',
    )

    # Mode selection
    parser.add_argument(
        '--mode', choices=['measure', 'ramp', 'peer'], default='measure',
        help=(
            '"measure": continuous latency measurement at fixed --rate (default); '
            '"ramp": increase rate from 1 msg/s until router CPU hits --ramp-target-cpu; '
            '"peer": connect two clients and measure end-to-end latency between them '
            'using addon=LATENCYTEST:<seqno>'
        ),
    )

    # Peer mode options
    peer = parser.add_argument_group('peer mode options')
    peer.add_argument(
        '--peer-a-host', default='127.0.0.1', metavar='HOST',
        help='Host of the router that peer client A (sender) connects to',
    )
    peer.add_argument(
        '--peer-a-port', type=int, default=10748, metavar='PORT',
        help='Port of the router that peer client A (sender) connects to',
    )
    peer.add_argument(
        '--peer-b-host', default='127.0.0.1', metavar='HOST',
        help='Host of the router that peer client B (receiver) connects to',
    )
    peer.add_argument(
        '--peer-b-port', type=int, default=10748, metavar='PORT',
        help='Port of the router that peer client B (receiver) connects to',
    )

    # Ramp mode options
    ramp = parser.add_argument_group('ramp mode options')
    ramp.add_argument(
        '--ramp-target-cpu', type=float, default=90.0, metavar='PCT',
        help='CPU percentage at which to stop the ramp and report',
    )
    ramp.add_argument(
        '--ramp-interval', type=float, default=5.0, metavar='SECONDS',
        help='Seconds to spend at each rate level before measuring CPU and stepping up',
    )
    ramp.add_argument(
        '--ramp-factor', type=float, default=1.5, metavar='FACTOR',
        help='Multiplicative rate increase per step (e.g. 1.5 → ×1.5 each step)',
    )
    ramp.add_argument(
        '--ramp-max-rate', type=float, default=10000.0, metavar='MSG/S',
        help='Safety ceiling: stop if this rate is reached without hitting target CPU',
    )

    args = parser.parse_args()

    try:
        asyncio.run(LatencyMeasurer(args).run())
    except KeyboardInterrupt:
        print('\nStopped.')


if __name__ == '__main__':
    main()
