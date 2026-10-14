"""frankenboostproxy.py - Proxy/telemetry relay for a PSX "boost" motion data feed.

Connects upstream to a boost data source (--boost-host / --boost-port, a
PSX boost server, usually MSFS/PSX.NET.MSFS.Client on 20749) and relays its
line-based feed, byte for byte and unmodified, to any number of downstream
clients accepted on --proxy-boost-port. This lets several motion-platform-
style consumers share one upstream boost connection.

Each upstream line is also parsed (frankenturb.boost.parse_boost_line) and
fed into an AccelerationComputer, which derives body-frame accelerations
and angular rates (see frankenturb/boost.py's module docstring for the
full coordinate-system description). A separate connection to the main
PSX network (--psx-host / --psx-port) supplies additional information not
present in the boost feed itself: indicated airspeed, and the simulator
name/router UUID of the frankenrouter (if any) it is connected to - see
"PSX connection" below. Every _ACCEL_EMIT_INTERVAL_S (5 Hz) a combined
telemetry record is built from the latest parsed boost sample, the latest
computed AccelerationState, and this PSX-sourced state:

    {
      "ts": <float, time.time() epoch>,
      "sim_name": <str or null>, "router_uuid": <str or null>,
      "is_flight": <bool>,
      "lat": <float, degrees>, "lon": <float, degrees>, "alt_ft": <float>,
      "heading_deg": <float>, "pitch_deg": <float>, "bank_deg": <float>,
      "ias_kt": <float or null>, "tas_kt": <float or null>,
      "ground_speed_kt": <float>,
      "vertical_speed_fpm": <float>, "aoa_deg": <float>,
      "heave_g": <float>, "surge_g": <float>, "sway_g": <float>,
      "roll_rate_dps": <float>, "pitch_rate_dps": <float>, "yaw_rate_dps": <float>,
      "gross_weight_kg": <float or null>, "kinetic_energy_gj": <float or null>
    }

The boost protocol carries no true airspeed field, so "ground_speed_kt"
(derived from successive lat/lon/alt samples by AccelerationComputer) is
the closest available substitute and is reported as such, not relabelled
("tas_kt", from the PSX connection, is the real thing - see below).
"vertical_speed_fpm" is likewise ground-track-derived (exact); "aoa_deg"
(pitch attitude vs. the ground-track velocity vector's direction,
rotated into the body frame - see frankenturb/boost.py's module
docstring) is only an approximation of true angle of attack - real wind
(especially vertical wind) biases it since it isn't airspeed-based. A
sideslip angle (the same rotation's xy-plane angle) was tried and
removed: ordinary cruise crosswind alone produced several degrees of
apparent sideslip during a genuinely coordinated turn, making it
unusable without a real wind vector to correct for.
"kinetic_energy_gj" (gigajoules) is TAS-based (the aircraft's own
translational energy, not energy relative to the ground/runway) and,
like "gross_weight_kg", is null whenever any input it depends on isn't
known yet (see "PSX connection" below).

This record is:

  - Sent, one JSON object per line, to every client connected on
    --accel-port, at the full 5 Hz rate.
  - Optionally (--send-addon, opt-in) also broadcast on the PSX network as
    an addon message of the form "addon=FRANKENBOOSTPROXY:TELEMETRY:<json>",
    throttled to at most once per --addon-interval seconds (default 1 s).

PSX connection
--------------
A psx.Client() connection to --psx-host/--psx-port is always maintained
(independent of --send-addon, which only controls whether *this* addon's
own telemetry is broadcast). It subscribes to:

  - "Ias" (Qi131, indicated airspeed) - Mode=START, so PSX only resends
    it at connection/situ-load time, not continuously: "ias_kt" is a
    last-known value, not a live reading, between those events.
  - "PiBaHeAlTas" (Qs121) - Mode=ECON, resent on effectively every PSX
    tick (~5 Hz) while flying, so "tas_kt" (true airspeed, the one field
    taken from it) stays live.
  - "TrueZfw" (Qi123, zero-fuel weight) and "FuelQty" (Qs438, per-tank
    fuel quantities - same format psx_fuel_transfer.py already relies
    on) to compute "gross_weight_kg" and, combined with "tas_kt",
    "kinetic_energy_gj".
  - "addon=FRANKENROUTER:<version>:SUBSCRIBE" (see router/docs/NOTES.md's
    "FRDP SUBSCRIBE" section), to receive ROUTERINFO and pick out its own
    directly-connected router's "simulator_name"/"uuid" (by matching its
    own connection entry by client_provided_id) - silently staying null
    if not connected to a frankenrouter, or until the first ROUTERINFO
    after connecting.

Lines arrive at a high rate (tens of Hz) and forwarding delay must stay
low, so each downstream client (on either port) only ever has its most
recently received (and not yet forwarded) item queued; older, unforwarded
items are dropped independently per client, so one slow client cannot
delay delivery to the others.

If --boost-log-file is set, every incoming (raw, pre-parse) boost line is
appended to it. Writes are not flushed immediately - only every 60 s (or on
clean shutdown) - so this does not need to stay real-time.
"""

import argparse
import asyncio
import json
import logging
import socket
import sys
import time
from typing import IO, Optional

from psx import Client
from frankenturb import parse_pibahealtas
from frankenturb.boost import AccelerationComputer, AccelerationState, BoostSample, \
    KT_TO_MS, parse_boost_line

__version__ = "2.0.0"

_ADDON_PREFIX = "FRANKENBOOSTPROXY:TELEMETRY:"
_FRDP_VERSION = '1'
_MY_CLIENT_ID = "BOOSTPROXY"
_RECONNECT_DELAY_S = 10.0
_BOOST_LOG_FLUSH_INTERVAL_S = 60.0
_ACCEL_EMIT_INTERVAL_S = 0.2  # 5 Hz telemetry tick
_LB_TO_KG = 0.45359237


def _print_prominent(msg: str) -> None:
    """Print a hard-to-miss error banner to stdout."""
    border = '!' * 70
    print(f"\n{border}\n!!! {msg}\n{border}\n", file=sys.stdout, flush=True)


def _set_nodelay(writer: asyncio.StreamWriter) -> None:
    sock = writer.transport.get_extra_info('socket')
    if sock is not None:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)


class BoostProxy:  # pylint: disable=too-many-instance-attributes,too-few-public-methods
    """Relays the raw boost feed downstream and publishes derived acceleration telemetry."""

    def __init__(self, args: argparse.Namespace):
        """Initialise from parsed command-line arguments."""
        self.boost_host: str = args.boost_host
        self.boost_port: int = args.boost_port
        self.proxy_boost_port: int = args.proxy_boost_port
        self.accel_port: int = args.accel_port
        self.send_addon: bool = args.send_addon
        self.addon_interval_s: float = args.addon_interval
        self.psx_host: str = args.psx_host
        self.psx_port: int = args.psx_port

        self.logger = logging.getLogger('frankenboostproxy')

        # One maxsize=1 "keep only the latest item" queue per connected
        # downstream client - see _enqueue_latest(). Raw queues carry the
        # unmodified upstream line bytes; accel queues carry one JSON
        # telemetry record per line, encoded to bytes.
        self._raw_queues: list["asyncio.Queue[bytes]"] = []
        self._accel_queues: list["asyncio.Queue[bytes]"] = []

        self.psx = Client()
        self.psx_connected = False
        self._last_addon_send_time: Optional[float] = None

        # PSX-sourced state, filled in by _psx_task()'s subscriptions -
        # None until PSX has actually told us, see module docstring's
        # "PSX connection" section.
        self._ias_kt: Optional[float] = None
        self._tas_kt: Optional[float] = None
        self._zfw_lbs: Optional[float] = None
        self._fuel_lbs: Optional[float] = None
        self._sim_name: Optional[str] = None
        self._router_uuid: Optional[str] = None

        self._accel_computer = AccelerationComputer()
        self._latest_sample: Optional[BoostSample] = None
        self._latest_accel: Optional[AccelerationState] = None

        # Optional raw incoming-boost-traffic log - see _boost_log_flush_task().
        self.boost_log_file: Optional[str] = args.boost_log_file
        self._boost_log_fh: Optional[IO[str]] = None
        if self.boost_log_file is not None:
            self._boost_log_fh = open(  # pylint: disable=consider-using-with
                self.boost_log_file, 'a', encoding='latin-1')
            self.logger.info("Logging incoming boost traffic to %s", self.boost_log_file)

    # ── PSX ───────────────────────────────────────────────────────────────

    def _on_ias(self, _key: str, value: str) -> None:
        try:
            self._ias_kt = int(value) / 1000.0
        except ValueError:
            self.logger.warning("Malformed Ias value from PSX: %r", value)

    def _on_pibahealtas(self, _key: str, value: str) -> None:
        """Pick true airspeed out of PiBaHeAlTas - the only field we need from it.

        Unlike Ias (Mode=START, see _on_ias), PiBaHeAlTas is Mode=ECON and
        in practice resent on every PSX tick while flying (~5 Hz), so
        tas_kt stays live rather than frozen at its connection-time value.
        """
        try:
            _, _, _, _, tas_kt, _, _ = parse_pibahealtas(value)
        except ValueError:
            self.logger.warning("Malformed PiBaHeAlTas value from PSX: %r", value)
            return
        self._tas_kt = tas_kt

    def _on_zfw(self, _key: str, value: str) -> None:
        try:
            self._zfw_lbs = float(value)
        except ValueError:
            self.logger.warning("Malformed TrueZfw value from PSX: %r", value)

    def _on_fuelqty(self, _key: str, value: str) -> None:
        """Sum the first 9 (main/aux/centre tank) fields of FuelQty into total fuel, lbs.

        Format: "d<tank1_lbs*10>;<tank2_lbs*10>;...;" (leading "d", values in
        tenths of a pound) - see psx_fuel_transfer.py, which already relies
        on this same layout for ZFW/fuel transfer bookkeeping.
        """
        try:
            fields = value[1:] if value.startswith('d') else value
            self._fuel_lbs = sum(int(field) / 10.0 for field in fields.split(';')[:9])
        except (ValueError, IndexError):
            self.logger.warning("Malformed FuelQty value from PSX: %r", value)

    def _on_addon(self, _key: str, value: str) -> None:
        """Pick our own router's simulator_name/uuid out of an FRDP ROUTERINFO.

        See router/docs/NOTES.md's "FRDP SUBSCRIBE" section: we opted in
        with SUBSCRIBE rather than becoming a full FRDP peer, so this is
        the only addon= traffic we receive. In a multi-router shared
        cockpit, ROUTERINFO for *other* sims can also reach us (relayed),
        so pick out our own router by finding our own connection (by the
        client_provided_id from our name= message) in its connections list.
        """
        if not value.startswith('FRANKENROUTER:'):
            return
        parts = value[len('FRANKENROUTER:'):].split(':', 2)
        if len(parts) < 3 or parts[1] != 'ROUTERINFO':
            return
        try:
            routerinfo = json.loads(parts[2])
        except json.JSONDecodeError:
            return
        router_uuid = routerinfo.get('uuid')
        if not router_uuid:
            return
        for conn in routerinfo.get('connections', []):
            if conn.get('client_provided_id') == _MY_CLIENT_ID:
                self._sim_name = routerinfo.get('simulator_name')
                self._router_uuid = router_uuid
                break

    async def _psx_task(self) -> None:
        def connected(*_args) -> None:
            self.logger.info("Connected to PSX as client #%s", self.psx.get("id"))
            self.psx.send("name", f"{_MY_CLIENT_ID}:FrankenBoostProxy")
            # Opt into FRDP broadcasts (ROUTERINFO) if connected to a
            # frankenrouter - see router/docs/NOTES.md's "FRDP SUBSCRIBE"
            # section. Silently ignored if connected directly to a PSX
            # main server instead.
            self.psx.send("addon", f"FRANKENROUTER:{_FRDP_VERSION}:SUBSCRIBE")
            self.psx_connected = True

        def disconnected() -> None:
            self.logger.info("Disconnected from PSX")
            self.psx_connected = False

        self.psx.onConnect = lambda: None
        self.psx.onDisconnect = disconnected
        self.psx.onResume = lambda: None
        self.psx.onPause = lambda: None
        self.psx.logger = self.logger.debug

        self.psx.subscribe("id")
        self.psx.subscribe("version", connected)
        self.psx.subscribe("Ias", self._on_ias)
        self.psx.subscribe("PiBaHeAlTas", self._on_pibahealtas)
        self.psx.subscribe("TrueZfw", self._on_zfw)
        self.psx.subscribe("FuelQty", self._on_fuelqty)
        self.psx.subscribe("addon", self._on_addon)

        await self.psx.connect(self.psx_host, self.psx_port)

    # ── Boost upstream ────────────────────────────────────────────────────

    @staticmethod
    def _enqueue_latest(queues: list, item: bytes) -> None:
        """Fan an item out to every queue in the list, keeping only the latest each."""
        for queue in queues:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(item)

    async def _boost_log_flush_task(self) -> None:
        """Periodically flush the incoming-boost-traffic log, if enabled."""
        if self._boost_log_fh is None:
            return
        while True:
            await asyncio.sleep(_BOOST_LOG_FLUSH_INTERVAL_S)
            self._boost_log_fh.flush()

    async def _boost_upstream_task(self) -> None:
        while True:
            try:
                reader, writer = await asyncio.open_connection(
                    self.boost_host, self.boost_port
                )
            except OSError as exc:
                _print_prominent(
                    f"Cannot connect to boost host {self.boost_host}:{self.boost_port}: {exc}"
                )
                await asyncio.sleep(_RECONNECT_DELAY_S)
                continue

            _set_nodelay(writer)
            self.logger.info("Connected to boost host %s:%d", self.boost_host, self.boost_port)
            try:
                while True:
                    line = await reader.readline()
                    if not line:
                        _print_prominent(
                            f"Boost host {self.boost_host}:{self.boost_port} disconnected"
                        )
                        break
                    self._enqueue_latest(self._raw_queues, line)
                    if self._boost_log_fh is not None:
                        self._boost_log_fh.write(line.decode('latin-1'))
                    self._process_sample(line)
            except (ConnectionError, OSError) as exc:
                _print_prominent(f"Boost upstream connection error: {exc}")
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionError, OSError):
                    pass

            await asyncio.sleep(_RECONNECT_DELAY_S)

    def _process_sample(self, line: bytes) -> None:
        """Parse one upstream line and feed it into the acceleration computer."""
        sample = parse_boost_line(line.decode('latin-1'), wall_time=time.monotonic())
        if sample is None:
            self.logger.warning("Malformed line from upstream, dropping: %r", line[:80])
            return
        self._latest_sample = sample
        accel = self._accel_computer.update(sample)
        if accel is not None:
            self._latest_accel = accel

    # ── Acceleration telemetry ───────────────────────────────────────────

    def _weight_and_energy(self) -> tuple[Optional[float], Optional[float]]:
        """Return (gross_weight_kg, kinetic_energy_gj), either/both None if not yet known.

        Gross weight = TrueZfw + FuelQty (see _on_zfw()/_on_fuelqty()),
        both reported by PSX in lbs, converted here since the rest of the
        telemetry record and the gauges GUI show it in kg. Kinetic energy
        uses TAS (through-the-air velocity), not ground speed - it's the
        aircraft's own translational energy, not energy relative to the
        ground/runway.
        """
        if self._zfw_lbs is None or self._fuel_lbs is None:
            return None, None
        gross_weight_kg = (self._zfw_lbs + self._fuel_lbs) * _LB_TO_KG
        if self._tas_kt is None:
            return gross_weight_kg, None
        tas_mps = self._tas_kt * KT_TO_MS
        kinetic_energy_gj = 0.5 * gross_weight_kg * tas_mps ** 2 / 1e9
        return gross_weight_kg, kinetic_energy_gj

    def _build_record(self, sample: BoostSample, accel: AccelerationState) -> dict:
        """Combine a boost sample, its derived acceleration, and PSX state into one record."""
        gross_weight_kg, kinetic_energy_gj = self._weight_and_energy()
        return {
            "ts": time.time(),
            "sim_name": self._sim_name,
            "router_uuid": self._router_uuid,
            "is_flight": sample.is_flight,
            "lat": sample.lat,
            "lon": sample.lon,
            "alt_ft": round(sample.alt_ft, 1),
            "heading_deg": round(sample.heading_deg, 1),
            "pitch_deg": round(sample.pitch_deg, 2),
            "bank_deg": round(sample.bank_deg, 2),
            "ias_kt": self._ias_kt,
            "tas_kt": self._tas_kt,
            "ground_speed_kt": round(accel.ground_speed_kt, 1),
            "vertical_speed_fpm": round(accel.vertical_speed_fpm, 0),
            "aoa_deg": round(accel.aoa_deg, 2),
            "heave_g": round(accel.heave_g, 3),
            "surge_g": round(accel.surge_g, 3),
            "sway_g": round(accel.sway_g, 3),
            "roll_rate_dps": round(accel.roll_rate_dps, 2),
            "pitch_rate_dps": round(accel.pitch_rate_dps, 2),
            "yaw_rate_dps": round(accel.yaw_rate_dps, 2),
            "gross_weight_kg": (round(gross_weight_kg, 0)
                                if gross_weight_kg is not None else None),
            "kinetic_energy_gj": (round(kinetic_energy_gj, 2)
                                  if kinetic_energy_gj is not None else None),
        }

    async def _accel_emit_task(self) -> None:
        """Every _ACCEL_EMIT_INTERVAL_S, publish the latest telemetry record."""
        while True:
            await asyncio.sleep(_ACCEL_EMIT_INTERVAL_S)
            if self._latest_sample is None or self._latest_accel is None:
                continue
            record = self._build_record(self._latest_sample, self._latest_accel)
            encoded = (json.dumps(record) + "\n").encode('utf-8')
            self._enqueue_latest(self._accel_queues, encoded)

            if not self.send_addon:
                continue
            now = time.monotonic()
            if (self._last_addon_send_time is not None and
                    now - self._last_addon_send_time < self.addon_interval_s):
                continue
            self._last_addon_send_time = now
            if self.psx_connected:
                self.psx.send("addon", f"{_ADDON_PREFIX}{json.dumps(record)}")

    # ── Downstream ────────────────────────────────────────────────────────

    async def _forward_to_downstream(
        self,
        writer: asyncio.StreamWriter,
        queue: "asyncio.Queue[bytes]",
    ) -> None:
        while True:
            item = await queue.get()
            try:
                writer.write(item)
                await writer.drain()
            except (ConnectionError, OSError):
                break

    async def _wait_downstream_closed(self, reader: asyncio.StreamReader) -> None:
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
        except (ConnectionError, OSError):
            pass

    async def _handle_downstream(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        queues: list,
        label: str,
    ) -> None:
        peer = writer.get_extra_info('peername')

        _set_nodelay(writer)
        queue: "asyncio.Queue[bytes]" = asyncio.Queue(maxsize=1)
        queues.append(queue)
        self.logger.info("%s downstream connected from %s (%d total)",
                         label, peer, len(queues))

        forward_task = asyncio.create_task(self._forward_to_downstream(writer, queue))
        closed_task = asyncio.create_task(self._wait_downstream_closed(reader))
        _, pending = await asyncio.wait(
            {forward_task, closed_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

        queues.remove(queue)
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass
        self.logger.info("%s downstream session from %s ended (%d remaining)",
                         label, peer, len(queues))

    async def _run_downstream_server(self, port: int, queues: list, label: str) -> None:
        server = await asyncio.start_server(
            lambda r, w: self._handle_downstream(r, w, queues, label), '0.0.0.0', port
        )
        self.logger.info("Listening for %s downstream connections on port %d", label, port)
        async with server:
            await server.serve_forever()

    # ── Run ───────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Run the boost proxy until interrupted."""
        tasks = [
            self._psx_task(),
            self._boost_upstream_task(),
            self._run_downstream_server(self.proxy_boost_port, self._raw_queues, "raw"),
            self._run_downstream_server(self.accel_port, self._accel_queues, "accel"),
            self._accel_emit_task(),
            self._boost_log_flush_task(),
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            if self._boost_log_fh is not None:
                self._boost_log_fh.flush()
                self._boost_log_fh.close()


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    """Parse arguments and run the boost proxy."""
    parser = argparse.ArgumentParser(
        description='Proxy/telemetry relay for a PSX boost motion data feed'
    )
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    parser.add_argument(
        '--boost-host', default='127.0.0.1', metavar='HOST',
        help='Boost data source hostname (default: 127.0.0.1)',
    )
    parser.add_argument(
        '--boost-port', type=int, default=20749, metavar='PORT',
        help='Boost data source port (default: 20749)',
    )
    parser.add_argument(
        '--proxy-boost-port', type=int, default=10749, metavar='PORT',
        help='Port to listen on for downstream clients that want the raw, unmodified '
             'boost feed (default: 10749)',
    )
    parser.add_argument(
        '--accel-port', type=int, default=10751, metavar='PORT',
        help='Port to listen on for downstream clients that want the derived '
             'acceleration/position telemetry at the full 5 Hz rate (default: 10751)',
    )
    parser.add_argument(
        '--send-addon', action='store_true',
        help='Also broadcast the acceleration/position telemetry on the PSX network '
             '(via --psx-host/--psx-port, which is always connected to) as an '
             'addon=FRANKENBOOSTPROXY:TELEMETRY:... message, throttled to '
             '--addon-interval (default: off)',
    )
    parser.add_argument(
        '--addon-interval', type=float, default=1.0, metavar='SECONDS',
        help='Minimum interval, in seconds, between addon telemetry broadcasts when '
             '--send-addon is set (default: 1.0)',
    )
    parser.add_argument(
        '--boost-log-file', default=None, metavar='FILE',
        help='If set, append all incoming raw boost traffic to this file. '
             f'Flushed every {int(_BOOST_LOG_FLUSH_INTERVAL_S)}s, not written in real time.',
    )
    parser.add_argument(
        '--psx-host', default='127.0.0.1', metavar='HOST',
        help='PSX Main Server (or frankenrouter) hostname, always connected to for '
             'Ias/router identity (default: 127.0.0.1)',
    )
    parser.add_argument(
        '--psx-port', type=int, default=10747, metavar='PORT',
        help='PSX Main Server (or frankenrouter) port, always connected to for '
             'Ias/router identity (default: 10747)',
    )
    parser.add_argument(
        '--psx-port-override', type=int, default=None, metavar='PORT',
        help='Override --psx-port with this value (a warning is printed). Used by '
             'start_scripts to force connecting to the correct router port.',
    )
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    args = parser.parse_args()

    if args.psx_port_override is not None:
        if args.psx_port_override != args.psx_port:
            print(f"WARNING: --psx-port-override={args.psx_port_override} "
                  f"overrides --psx-port={args.psx_port}", file=sys.stderr)
        args.psx_port = args.psx_port_override

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format='%(asctime)s %(levelname)-5s %(name)s: %(message)s',
        datefmt='%H:%M:%S',
        stream=sys.stdout,
    )

    try:
        asyncio.run(BoostProxy(args).run())
    except KeyboardInterrupt:
        print('\nStopped.')


if __name__ == '__main__':
    try:
        main()
    except Exception:  # pylint: disable=broad-exception-caught
        import traceback
        traceback.print_exc()
        input("An error occurred, press Enter to continue...")
    except SystemExit as exc:
        if exc.code not in (None, 0):
            input("An error occurred, press Enter to continue...")
