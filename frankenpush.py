# pylint: disable=invalid-name
"""FrankenPush - PSCC Flight Centre push connector.

Connects to a local PSX main server (or frankenrouter), reads live flight
data, and streams it to a PSCC Flight Centre portal over an authenticated
WebSocket connection.  No inbound port forwarding is needed — the connection
is always initiated outward from your machine to the portal.

Also connects as an FRDP peer to receive FLIGHTINFO (crew codes, airline,
VATSIM callsign, etc.) and ROUTERINFO (connected simulator names) from
frankenrouter, and includes them in each push update to the portal.
The FRDP peer connection requires no extra configuration.

Setup:
  1. Register a "My sim" on the portal (your portal URL)/mysim
     (choose type "Shared cockpit master sim" if this is a master sim)
  2. Copy the logon key shown on the sim's detail page
  3. Run this addon:
       python frankenpush.py --portal-url https://your-portal/path --logon-key <key>
     or just:
       python frankenpush.py
     and enter the values when prompted.
"""

import argparse
import asyncio
import datetime
import inspect
import json
import logging
import math
import pathlib
import sys
import time
import traceback
import uuid

import aiohttp

import psx

__MYNAME__ = 'frankenpush'
__MY_CLIENT_ID__ = 'PUSH'
__MY_DISPLAY_NAME__ = 'FrankenPush'
__MY_DESCRIPTION__ = 'PSCC Flight Centre push connector'

_FRDP_VERSION = '1'
_FRDP_CLIENT_ID = 'FrankenPush'
_FRDP_ROUTER_ID = 'frankenpush'
# The literal substring frankenrouter's rules.py matches to mark a connecting
# peer as is_frankenrouter=True (required to receive FLIGHTINFO/ROUTERINFO).
_FRDP_MARKER = 'FRANKEN.PY frankenrouter'

# ROUTERINFO is sent every 10 s by frankenrouter; expire entries unseen for 2× that.
_ROUTERINFO_MAX_AGE = 20.0

# Increment this whenever the push protocol changes in a way that is not
# backward-compatible with the version of push_manager.py on the portal.
# The portal will reject connections whose version does not match.
PUSH_PROTOCOL_VERSION = 1

# Matches the portal's WS broadcast rate (web/ws.py _BROADCAST_INTERVAL).
_SEND_INTERVAL = 2.0

# How often to send a full snapshot regardless of what changed.
# Between full sends only changed fields are sent to reduce bandwidth.
_FULL_SEND_INTERVAL = 300.0

# PSX FMC route parsing — mirrors psccfc/connector/frdp.py (kept in sync by hand).
_WAYPOINT_PREFIX_LEN = 10
_WAYPOINT_SENTINEL_LATLON = "9.0/9.0"


def _pick_active_route(mode_value, route1, route2):
    """Select route1 or route2 per FmcRteViAcMo's 3rd character (index 2).

    '1' = route 1 active, '2' = route 2 active.  Returns None if neither.
    """
    if not mode_value or len(mode_value) < 3:
        return None
    indicator = mode_value[2]
    if indicator == "1":
        return route1
    if indicator == "2":
        return route2
    return None


def _parse_route_airports(route):
    """Extract (dep_icao, arr_icao) from a PSX FmcRte string.

    Returns (None, None) when missing/malformed or 'bbbb' placeholder is used.
    """
    if not route:
        return None, None
    fields = route.split(";")
    dep_raw = fields[0].strip() if fields else ""
    arr_raw = fields[1].strip() if len(fields) > 1 else ""
    if dep_raw.upper() == "BBBB" or arr_raw.upper() == "BBBB":
        return None, None
    dep = dep_raw if len(dep_raw) == 4 and dep_raw.isalnum() else None
    arr = arr_raw if len(arr_raw) == 4 and arr_raw.isalnum() else None
    return dep, arr


def _parse_route_waypoints(route):
    """Extract [[name, lat_deg, lon_deg], ...] entries from a PSX FmcRte string."""
    if not route or "#" not in route:
        return []
    _header, _sep, body = route.partition("#")
    waypoints = []
    for entry in body.split(";"):
        if not entry:
            continue
        fields = entry.split("'")
        if len(fields) < 4:
            continue
        name = fields[0][_WAYPOINT_PREFIX_LEN:]
        latlon = fields[3]
        if latlon == _WAYPOINT_SENTINEL_LATLON:
            continue
        lat_str, _sep2, lon_str = latlon.partition("/")
        try:
            lat_deg = math.degrees(float(lat_str))
            lon_deg = math.degrees(float(lon_str))
        except ValueError:
            continue
        waypoints.append([name, lat_deg, lon_deg])
    return waypoints


def _print_version_mismatch_warning():
    """Print a prominent console warning that frankenpush is out of date."""
    print()
    print("=" * 68)
    print("  *** FRANKENPUSH IS OUT OF DATE ***")
    print()
    print("  This copy of frankenpush (protocol version"
          f" {PUSH_PROTOCOL_VERSION}) is not")
    print("  compatible with the Flight Centre portal.")
    print()
    print("  Please update frankenpush to the latest version.")
    print("=" * 68)
    print()


class Script():  # pylint: disable=too-many-instance-attributes
    """FrankenPush script."""

    def __init__(self):
        """Initialize script state."""
        self.args = None
        self.taskgroup = None
        self.tasks = set()
        self.logger = None
        self.psx = None
        self.psx_connected = False

        # Latest PSX state — updated by psx callbacks, read by push loop.
        # All None until PSX sends the first value.
        self._lat = None
        self._lon = None
        self._alt_ft = None
        self._heading_deg = None
        self._tas_kt = None
        self._pitch_deg = None
        self._bank_deg = None
        self._tail_number = None
        self._flight_number = None
        self._route_mode = None
        self._route1 = None
        self._route2 = None
        self._eta = None

        # FRDP peer connection state
        self._flightinfo = None             # latest FLIGHTINFO dict
        self._routerinfos: dict = {}        # {uuid: routerinfo_dict}

        # Autosave situ monitoring — set when a file changes, consumed by _build_update
        self._pending_autosave_situ = None

        # Delta tracking: records the last-sent value of each slow-changing field.
        # Reset to {} on each portal (re)connection to force a full first send.
        self._sent_state: dict = {}

    # --- PSX callbacks ---

    def _on_position(self, _key, value):
        """Parse PiBaHeAlTas (pitch;bank;heading_rad;alt_ft*1e3;tas_kt*1e3;lat_rad;lon_rad)."""
        try:
            parts = value.split(';')
            if len(parts) != 7:
                return
            pitch_mrad, bank_mrad, heading_rad, alt_x1000, tas_x1000, lat_rad, lon_rad = (
                float(p) for p in parts)
            self._pitch_deg = math.degrees(pitch_mrad / 1_000_000)
            self._bank_deg = math.degrees(bank_mrad / 1_000_000)
            self._heading_deg = math.degrees(heading_rad) % 360
            self._alt_ft = alt_x1000 / 1000
            self._tas_kt = tas_x1000 / 1000
            self._lat = math.degrees(lat_rad)
            self._lon = math.degrees(lon_rad)
        except (ValueError, IndexError):
            pass

    def _on_tail_number(self, _key, value):
        self._tail_number = value.strip().upper() or None

    def _on_flight_number(self, _key, value):
        self._flight_number = value or None

    def _on_route_mode(self, _key, value):
        self._route_mode = value or None

    def _on_route1(self, _key, value):
        self._route1 = value or None

    def _on_route2(self, _key, value):
        self._route2 = value or None

    def _on_eta(self, _key, value):
        self._eta = value or None

    # --- helpers ---

    @property
    def _connected_sim_names(self):
        now = time.time()

        # Expire entries not seen within 2× the ROUTERINFO broadcast interval.
        stale = [uid for uid, ri in self._routerinfos.items()
                 if now - ri.get('received', 0) > _ROUTERINFO_MAX_AGE]
        for uid in stale:
            self.logger.debug("ROUTERINFO: expiring stale entry %s (%s)",
                              uid, self._routerinfos[uid].get('simulator_name', '?'))
            del self._routerinfos[uid]

        # The master sim is the router whose upstream connection is not a
        # frankenrouter (i.e. it connects directly to PSX).  Its simulator
        # name represents the master crew and must be excluded from the list
        # of *client* sim names shown as crew on the flight board.
        master_sim_name = None
        for ri in self._routerinfos.values():
            for conn in ri.get('connections', []):
                if conn.get('upstream') and not conn.get('is_frankenrouter'):
                    master_sim_name = ri.get('simulator_name')
                    break
            if master_sim_name is not None:
                break

        names = sorted({
            ri['simulator_name']
            for ri in self._routerinfos.values()
            if 'simulator_name' in ri and ri['simulator_name'] != master_sim_name
        })
        self.logger.debug("ROUTERINFO: master=%r  clients=%r", master_sim_name, names)
        return names

    def _build_update(self, full):
        """Build the JSON payload to send to Flight Centre.

        If full=True every field is included. Otherwise only fields whose
        values have changed since the last send are included — the server
        (push_manager.py) ignores absent keys rather than resetting them.
        Position fields are always included: they change on virtually every
        tick, so checking them for change adds cost without saving bandwidth.
        """
        update = {"protocol_version": PUSH_PROTOCOL_VERSION}

        # Position — always include when we have a fix.
        if self._lat is not None:
            update.update({
                "lat": self._lat, "lon": self._lon,
                "alt_ft": self._alt_ft, "heading_deg": self._heading_deg,
                "tas_kt": self._tas_kt, "pitch_deg": self._pitch_deg,
                "bank_deg": self._bank_deg,
            })

        # Scalar fields — only when changed or full.
        for key in ("tail_number", "flight_number", "eta"):
            val = getattr(self, f"_{key}")
            if full or self._sent_state.get(key) != val:
                update[key] = val

        # Route: extract the fields FC needs rather than sending raw PSX strings.
        active_route = _pick_active_route(self._route_mode, self._route1, self._route2)
        dep_airport, arr_airport = _parse_route_airports(active_route)
        waypoints = _parse_route_waypoints(active_route)
        route_now = (dep_airport, arr_airport, tuple(tuple(w) for w in waypoints))
        if full or self._sent_state.get("route") != route_now:
            update["dep_airport"] = dep_airport
            update["arr_airport"] = arr_airport
            update["waypoints"] = waypoints

        # flightinfo and connected_sim_names: the server only updates these
        # when the key is present, so omitting them on delta sends is safe.
        fi = self._flightinfo
        if full or self._sent_state.get("flightinfo") != fi:
            update["flightinfo"] = fi

        names = self._connected_sim_names
        if full or self._sent_state.get("names") != names:
            update["connected_sim_names"] = names

        # Autosave situ — always include when pending (one-shot delivery).
        if self._pending_autosave_situ is not None:
            update["autosave_situ"] = self._pending_autosave_situ
            self._pending_autosave_situ = None

        return update

    def _record_sent_state(self, update):
        """Record what was just sent so the next delta can skip unchanged fields."""
        for key in ("tail_number", "flight_number", "eta"):
            if key in update:
                self._sent_state[key] = update[key]
        if "dep_airport" in update:
            self._sent_state["route"] = (
                update["dep_airport"], update["arr_airport"],
                tuple(tuple(w) for w in update["waypoints"]))
        if "flightinfo" in update:
            self._sent_state["flightinfo"] = update["flightinfo"]
        if "connected_sim_names" in update:
            self._sent_state["names"] = update["connected_sim_names"]

    # --- coroutines ---

    async def get_psx_connection_coro(self):
        """Maintain a connection to the PSX main server."""
        def connected():
            self.logger.info("PSX CONNECTED")
            self.psx_connected = True
            self.psx.send("name", f"{__MY_CLIENT_ID__}:{__MY_DISPLAY_NAME__}")

        def disconnected():
            self.logger.info("PSX DISCONNECTED")
            self.psx_connected = False
            self._lat = None  # clear position so stale data isn't sent

        try:
            self.logger.debug("Starting %s", inspect.currentframe().f_code.co_name)
            self.psx = psx.Client()
            self.psx.onConnect = connected
            self.psx.onDisconnect = disconnected
            self.psx.onPause = lambda: None
            self.psx.onResume = lambda: None

            # PSX lexicon names (confirmed from session captures):
            #   Qs0 = CfgRego (confirmed) a.k.a. AcTailNo,
            #   PiBaHeAlTas = Qs121, FmcFltNo = Qs401,
            #   FmcRteViAcMo = Qs373, FmcRte1 = Qs376, FmcRte2 = Qs377,
            #   ActDestEta = Qi247
            self.psx.subscribe("AcTailNo", self._on_tail_number)
            self.psx.subscribe("CfgRego", self._on_tail_number)
            self.psx.subscribe("PiBaHeAlTas", self._on_position)
            self.psx.subscribe("FmcFltNo", self._on_flight_number)
            self.psx.subscribe("FmcRteViAcMo", self._on_route_mode)
            self.psx.subscribe("FmcRte1", self._on_route1)
            self.psx.subscribe("FmcRte2", self._on_route2)
            self.psx.subscribe("ActDestEta", self._on_eta)

            self.psx.logger = self.logger.debug

            await self.psx.connect(self.args.psx_host, self.args.psx_port)
            self.logger.warning("psx.connect() returned unexpectedly")

        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical(
                "Unhandled exception %s in %s, shutting down",
                exc, inspect.currentframe().f_code.co_name)
            self.logger.critical(traceback.format_exc())

    def _handle_frdp_line(self, line, writer):
        """Process one line received on the FRDP peer connection."""
        rest = line[len('addon=FRANKENROUTER:'):]
        parts = rest.split(':', 2)
        if len(parts) < 2:
            return
        msg_type = parts[1]
        payload = parts[2] if len(parts) > 2 else ''
        if msg_type == 'PING':
            writer.write(
                f"addon=FRANKENROUTER:{_FRDP_VERSION}:PONG:{payload}\r\n".encode())
        elif msg_type == 'FLIGHTINFO':
            try:
                self._flightinfo = json.loads(payload)
                self.logger.debug("FRDP: received FLIGHTINFO")
            except json.JSONDecodeError:
                pass
        elif msg_type == 'ROUTERINFO':
            try:
                ri = json.loads(payload)
                ri_uuid = ri.get('uuid')
                if ri_uuid:
                    ri['received'] = time.time()
                    self._routerinfos[ri_uuid] = ri
                    self.logger.debug("FRDP: ROUTERINFO from %s (%s)",
                                      ri_uuid, ri.get('simulator_name', '?'))
            except json.JSONDecodeError:
                pass

    async def _run_frdp_session(self, reader, writer):
        """Read loop for a single FRDP peer session."""
        while True:
            raw = await reader.readline()
            if not raw:
                self.logger.info("FRDP: EOF from peer")
                break
            line = raw.decode(errors='replace').rstrip('\r\n')
            if line.startswith('addon=FRANKENROUTER:'):
                self._handle_frdp_line(line, writer)

    async def get_frdp_connection_coro(self):
        """Connect as an FRDP peer to receive FLIGHTINFO and ROUTERINFO.

        Connects to the same host:port as the PSX connection but identifies
        as a frankenrouter peer, which causes the frankenrouter to send
        FLIGHTINFO (crew codes, airline, etc.) and ROUTERINFO (connected
        simulator names) broadcasts.
        """
        frdp_uuid = str(uuid.uuid4())
        handshake = (
            f"name={_FRDP_CLIENT_ID}:{_FRDP_MARKER} PSX router {_FRDP_ROUTER_ID}\r\n"
            f"addon=FRANKENROUTER:{_FRDP_VERSION}:IDENT:"
            f"{_FRDP_CLIENT_ID}:{_FRDP_ROUTER_ID}:{frdp_uuid}\r\n"
        ).encode()
        backoff = 2.0
        try:
            self.logger.debug("Starting %s", inspect.currentframe().f_code.co_name)
            while True:
                try:
                    reader, writer = await asyncio.open_connection(
                        self.args.psx_host, self.args.psx_port)
                    try:
                        writer.write(handshake)
                        backoff = 2.0
                        self.logger.info("FRDP: peer connection established")
                        await self._run_frdp_session(reader, writer)
                    finally:
                        self._flightinfo = None
                        self._routerinfos.clear()
                        writer.close()
                        self.logger.info("FRDP: peer connection closed")
                except (OSError, asyncio.IncompleteReadError) as exc:
                    self.logger.warning("FRDP: connection failed: %s", exc)
                self.logger.info("FRDP: retrying in %.0f s ...", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical(
                "Unhandled exception %s in %s, shutting down",
                exc, inspect.currentframe().f_code.co_name)
            self.logger.critical(traceback.format_exc())

    async def _portal_send_loop(self, ws):
        """Send updates to the portal at the broadcast interval.

        Sends a full snapshot immediately after (re)connecting and every
        _FULL_SEND_INTERVAL seconds thereafter. In between, only fields
        that have changed since the last send are included in the message.
        """
        self._sent_state = {}   # clear so the first send is always a full snapshot
        last_full_at = 0.0
        while True:
            if self.psx_connected and self._lat is not None:
                now = asyncio.get_running_loop().time()
                full = not self._sent_state or (now - last_full_at >= _FULL_SEND_INTERVAL)
                update = self._build_update(full)
                await ws.send_json(update)
                self._record_sent_state(update)
                if full:
                    last_full_at = now
                if self.args.show_sent_to_fc:
                    self.logger.info("Sent to FC: %s", json.dumps(update, indent=2))
                else:
                    self.logger.debug("Sent %s update", "full" if full else "delta")
            await asyncio.sleep(_SEND_INTERVAL)

    async def push_loop_coro(self):
        """Maintain a WebSocket connection to the portal and send position updates."""
        ws_url = self.args.portal_url.rstrip('/') + '/ws/push'
        headers = {"Authorization": f"Bearer {self.args.logon_key}"}
        backoff = 2.0

        try:
            self.logger.debug("Starting %s", inspect.currentframe().f_code.co_name)
            while True:
                try:
                    async with aiohttp.ClientSession() as session:
                        self.logger.info("Connecting to portal at %s ...", ws_url)
                        async with session.ws_connect(
                                ws_url, headers=headers, heartbeat=30) as ws:
                            self.logger.info("Connected to portal")
                            backoff = 2.0  # reset on successful connection
                            send_task = asyncio.ensure_future(
                                self._portal_send_loop(ws))
                            drain_task = asyncio.ensure_future(
                                self._drain_ws(ws))
                            try:
                                await asyncio.wait(
                                    [send_task, drain_task],
                                    return_when=asyncio.FIRST_COMPLETED)
                                close_code = ws.close_code
                                if close_code == 4001:
                                    self.logger.error(
                                        "Portal rejected logon key — "
                                        "check the key shown on your My sim page")
                                    backoff = 30.0  # long pause on auth failure
                                elif close_code == 4002:
                                    _print_version_mismatch_warning()
                                    raise SystemExit(1)
                                elif close_code is not None:
                                    self.logger.info(
                                        "Portal closed connection (code %s)", close_code)
                                else:
                                    self.logger.info("Portal connection closed")
                            finally:
                                send_task.cancel()
                                drain_task.cancel()
                                await asyncio.gather(send_task, drain_task,
                                                     return_exceptions=True)

                except aiohttp.ClientResponseError as exc:
                    self.logger.warning("Portal HTTP error %s: %s", exc.status, exc.message)
                except (aiohttp.ClientError, OSError) as exc:
                    self.logger.warning("Portal connection failed: %s", exc)
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    self.logger.warning("Push loop error: %s", exc)

                self.logger.info("Retrying portal connection in %.0f s ...", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical(
                "Unhandled exception %s in %s, shutting down",
                exc, inspect.currentframe().f_code.co_name)
            self.logger.critical(traceback.format_exc())

    @staticmethod
    async def _drain_ws(ws):
        """Discard server messages; exits when server closes the connection."""
        async for _ in ws:
            pass

    async def _monitor_autosave_coro(self):
        """Poll PSX autosave situ files and queue changed ones for upload.

        Checks -Autosaved[A].situ and -Autosaved[B].situ every 30 seconds.
        When a file's mtime changes, its content and timestamp are stored as
        _pending_autosave_situ and included in the next push update to the
        portal.
        """
        situ_dir = pathlib.Path(self.args.upload_autosave_from)
        mtimes = {
            situ_dir / "-Autosaved[A].situ": None,
            situ_dir / "-Autosaved[B].situ": None,
        }
        self.logger.info("Autosave monitor: watching %s", situ_dir)
        while True:
            for path, last_mtime in list(mtimes.items()):
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if last_mtime is not None and mtime == last_mtime:
                    continue
                mtimes[path] = mtime
                try:
                    age = datetime.datetime.now(
                        datetime.timezone.utc).timestamp() - mtime
                    if age > 7 * 60:
                        self.logger.debug(
                            "Autosave %s is %.0f s old, skipping", path.name, age)
                        continue
                    content = path.read_text(encoding='utf-8', errors='replace')
                    ts = datetime.datetime.fromtimestamp(
                        mtime, tz=datetime.timezone.utc).isoformat()
                    self._pending_autosave_situ = {"content": content, "timestamp": ts}
                    self.logger.info("Autosave queued for upload: %s", path.name)
                except OSError as exc:
                    self.logger.warning("Failed to read autosave file: %s", exc)
            await asyncio.sleep(30.0)

    async def monitor_coro(self):
        """Monitor the coroutines and start/restart as needed."""
        try:
            self.logger.debug("Starting %s", inspect.currentframe().f_code.co_name)
            while True:
                running = []
                tasks_ended = set()
                for task in self.tasks:
                    done = task.done()
                    if done:
                        tasks_ended.add(task)
                        exc = task.exception()
                        if exc is None:
                            self.logger.info("Task %s ended peacefully", task.get_name())
                        else:
                            self.logger.info("Task %s ended: %s", task.get_name(), exc)
                    else:
                        running.append(task.get_name())
                for task in tasks_ended:
                    self.tasks.discard(task)

                all_coros = [
                    ("PSXConnection", self.get_psx_connection_coro),
                    ("PushLoop", self.push_loop_coro),
                    ("FRDPConnection", self.get_frdp_connection_coro),
                ]
                if self.args.upload_autosave_from:
                    all_coros.append(
                        ("AutosaveMonitor", self._monitor_autosave_coro))
                for name, coro_fn in all_coros:
                    if name not in running:
                        self.logger.info("Starting %s ...", name)
                        task = self.taskgroup.create_task(coro_fn(), name=name)
                        self.tasks.add(task)
                        self.logger.info("Started %s.", name)

                self.logger.debug("Running tasks: %s", running)
                await asyncio.sleep(5.0)

        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical(
                "Unhandled exception %s in %s, shutting down",
                exc, inspect.currentframe().f_code.co_name)
            self.logger.critical(traceback.format_exc())

    def handle_args(self):
        """Parse command-line arguments, prompting for missing required values."""
        parser = argparse.ArgumentParser(
            prog=__MYNAME__,
            description=__MY_DESCRIPTION__,
            formatter_class=argparse.ArgumentDefaultsHelpFormatter)
        parser.add_argument(
            '--psx-host',
            type=str, action='store', default='127.0.0.1',
            help="Hostname or IP of the PSX main server or router to connect to.",
        )
        parser.add_argument(
            '--psx-port',
            type=int, action='store', default=10747,
            help="Port of the PSX main server or router.",
        )
        parser.add_argument(
            '--portal-url',
            type=str, action='store', default='https://mkro.se/flightcentre',
            help="PSCC Flight Centre portal URL.",
        )
        parser.add_argument(
            '--logon-key',
            type=str, action='store', default=None,
            help="Logon key from the 'My sim' page on the portal.",
        )
        parser.add_argument(
            '--upload-autosave-from',
            type=str, action='store', default=None, metavar='PATH',
            help="Path to the PSX Situations directory. Monitors -Autosaved[A].situ "
                 "and -Autosaved[B].situ and uploads them to Flight Centre when they "
                 "change (PSX saves every ~3.5 minutes).",
        )
        parser.add_argument(
            '--show-sent-to-fc',
            action='store_true',
            help="Print each update sent to Flight Centre (useful for troubleshooting).",
        )
        parser.add_argument(
            '--debug',
            action='store_true',
            help="Print more debug info.",
        )
        self.args = parser.parse_args()

        if not self.args.logon_key:
            print("Enter the logon key shown on the 'My sim' page of the portal.")
            self.args.logon_key = input("Logon key: ").strip()

    async def run(self):
        """Start everything."""
        self.handle_args()

        log_format = "%(asctime)s: %(message)s"
        logging.basicConfig(
            format=log_format,
            level=logging.INFO,
            datefmt="%H:%M:%S",
            handlers=[logging.StreamHandler(sys.stdout)],
        )
        self.logger = logging.getLogger(__MYNAME__)
        if self.args.debug:
            self.logger.setLevel(logging.DEBUG)
            asyncio.get_event_loop().set_debug(True)

        print(f"Connecting to PSX at {self.args.psx_host}:{self.args.psx_port}")
        print(f"Pushing to portal at {self.args.portal_url.rstrip('/')}/ws/push")
        print(f"FRDP peer: {self.args.psx_host}:{self.args.psx_port}")
        print("Press Ctrl+C to stop.")

        async with asyncio.TaskGroup() as self.taskgroup:
            task = self.taskgroup.create_task(self.monitor_coro(), name="Monitor")
            self.tasks.add(task)
        print("All tasks completed.")


if __name__ == '__main__':
    try:
        asyncio.run(Script().run())
    except Exception:  # pylint: disable=broad-exception-caught
        traceback.print_exc()
        input("An error occurred, press Enter to continue...")
    except SystemExit as exc:
        if exc.code not in (None, 0):
            input("Press Enter to continue...")
