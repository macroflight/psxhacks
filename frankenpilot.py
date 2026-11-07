"""frankenpilot - a configurable virtual Pilot Monitoring for Aerowinx PSX.

Replaces the "silent tasks" of PSX's own virtual pilot (Instructor > Situation >
Human > Pilot), which it switches off, while PSX keeps doing the voice
call-outs and checklists. Design: docs/frankenpilot.md.

Modes
- Shared cockpit (connected to a master frankenrouter): on by default when
  0-1 slave sims are connected, off with 2+; it sits in the pilot seat nobody
  is in (router seat assignment, SHAREDINFO). The router's frankenpilot page
  can force it on or off.
- Local (a plain PSX main server, or a standalone frankenrouter): on/off and
  the seat come from the command line / config file; by default the seat is
  the PSX Human Pilot seat (PnfMode bit 0).

Behaviour
- Every action happens once, when its trigger occurs (runway entry, liftoff,
  passing 10,000 ft above the airport, an EICAS message appearing...), after a
  small random delay like a person. A switch the crew changes afterwards is
  left alone until that action's next trigger.
- It uses only what a pilot can see: TAT, clouds out of the window (MSFS
  in-cloud data), EICAS, the aircraft's own state.
- Each action can be switched off (config file / router page).
"""
# pylint: disable=too-many-lines
import argparse
import asyncio
import json
import logging
import math
import os
import random
import sys
import time
import tomllib
import urllib.request

import psx
from psxhacks_version import get_version

__version__ = get_version("frankenpilot", __file__)
__MY_CLIENT_ID__ = 'FRANKENPILOT'
__MY_DISPLAY_NAME__ = 'frankenpilot'

FRDP = "FRANKENROUTER:1"

# EICAS message numbers (PSX 10.195 EicasMessages, per category; see docs)
EICAS_A = {
    'FUEL OVD CTR L': 211, 'FUEL OVD CTR R': 212, 'FUEL PMP STB L': 213, 'FUEL PMP STB R': 214,
    'FUEL LO STAB L': 229, 'FUEL LO STAB R': 230, 'FUEL LOW CTR L': 231, 'FUEL LOW CTR R': 232,
    'FUEL AUX L': 234, 'FUEL AUX R': 235, 'FUEL TANK/ENG': 192,
    'ANTI-ICE NAC': 129, 'ANTI-ICE WING': 130, 'ICING WING': 131,
}
EICAS_C = {'ICING NAC': 47}

AUTOBRAKE = {'RTO': 0, 'OFF': 1, 'DISARM': 2, '1': 3, '2': 4, '3': 5, '4': 6, 'MAX': 7}
GEAR_UP, GEAR_OFF, GEAR_DOWN = 1, 2, 3

# Every action, its default and a description (config file [actions], router page)
ACTIONS = {
    'runway_entry_lights': (True, "Runway entry: strobes, taxi and all lights except outboard "
                                  "landing lights"),
    'runway_entry_rto': (True, "Runway entry: autobrake RTO if not set"),
    'runway_entry_tcas': (True, "Runway entry: transponder TA/RA if not set"),
    'liftoff_lights': (True, "After liftoff: runway turnoff, taxi and wing lights off"),
    'lights_10000': (True, "Landing and logo lights off/on passing 10,000 ft above the airport"),
    'autobrake_5000': (True, "Autobrake 4 at 5,000 ft above the destination if not set"),
    'packs_after_takeoff': (True, "Packs on one by one after thrust reduction"),
    'gear_lever_off': (True, "Gear lever OFF after thrust reduction with the gear up"),
    'fd_follow': (True, "Other F/D switch follows the pilot's"),
    'exit_flaps_speedbrake': (True, "After runway exit: flaps up, speedbrake down"),
    'exit_lights': (True, "After runway exit: landing lights off, taxi light on, strobes off"),
    'exit_wxr_terr': (True, "After runway exit: weather radar and terrain off where on"),
    'exit_stab_trim': (True, "After runway exit: stabilizer trim to 6 units"),
    'exit_apu': (True, "After runway exit: APU start (delayed)"),
    'crossing_strobes': (True, "Strobes on while crossing a runway"),
    'fuel': (True, "Fuel pumps and outer crossfeeds in flight (Boeing procedure, EICAS driven)"),
    'anti_ice': (True, "Engine and wing anti-ice by the book"),
}

DEFAULT_PARAMS = {
    'lights_agl_ft': 10000.0,
    'autobrake_agl_ft': 5000.0,
    'autobrake_setting': '4',
    'exit_speed_kt': 30.0,
    'exit_turn_deg': 40.0,
    'apu_delay_s': 30.0,
    'pack_gap_s': [3.0, 8.0],
    'delay_s': [1.0, 4.0],
    'runway_half_width_m': 40.0,
    'stab_trim_units': 6.0,
}


def human_delay(span):
    """Return a random delay (seconds) in span [low, high]."""
    return random.uniform(span[0], span[1])


def nm_between(lat1, lon1, lat2, lon2):
    """Great-circle distance in nm."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat, dlon = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * math.degrees(math.asin(min(1.0, math.sqrt(a)))) * 60.0


def heading_diff(a, b):
    """Smallest absolute difference between two headings (degrees)."""
    return abs((a - b + 180.0) % 360.0 - 180.0)


def parse_local_runways(value):
    """Return [(designator, lat, lon, other_designator, other_lat, other_lon)] from LocalRwys.

    LocalRwys (Qs571): ';'-terminated runway-end entries
    <designator>'<lat rad>'<lon rad>'<displaced threshold ft>, designator 3 chars
    ("04 ", 27L). The position is the landing threshold; the paved runway starts
    the displaced distance before it (TNCC 11: 2740 ft, verified on the test sim
    2026-11-07), so each end returned here is moved back to the physical end.
    Ends are paired into runways by number +-18 and mirrored suffix.
    """
    ends = {}
    for entry in (value or '').split(';'):
        parts = entry.split("'")
        if len(parts) != 4 or len(parts[0]) != 3:
            continue
        try:
            displaced_m = max(0.0, float(parts[3] or 0)) * 0.3048
            ends[parts[0]] = (math.degrees(float(parts[1])), math.degrees(float(parts[2])),
                              displaced_m)
        except ValueError:
            continue
    mirror = {'L': 'R', 'R': 'L', 'C': 'C', ' ': ' ', 'W': 'W'}
    runways, seen = [], set()
    for des, end in ends.items():
        if not des[:2].isdigit():
            continue
        num = int(des[:2])
        other = f"{num + 18 if num <= 18 else num - 18:02d}{mirror.get(des[2], des[2])}"
        if other in ends and other not in seen:
            seen.add(des)
            runways.append(_runway(des, end, other, ends[other]))
    return runways


def _runway(des, end1, other, end2):
    """Return the runway tuple, each threshold moved back by its displacement to the paved end.

    end1/end2 are (lat, lon, displaced_m).
    """
    k = math.cos(math.radians((end1[0] + end2[0]) / 2))
    dx, dy = (end2[1] - end1[1]) * k, end2[0] - end1[0]
    norm = (math.hypot(dx, dy) or 1.0) * 111320.0      # metres -> degrees along the unit vector
    s1, s2 = end1[2] / norm, end2[2] / norm
    return (des, end1[0] - dy * s1, end1[1] - dx * s1 / k,
            other, end2[0] + dy * s2, end2[1] + dx * s2 / k)


def runway_position(lat, lon, runway):  # pylint: disable=too-many-locals
    """Return (cross_track_m, along_fraction, runway_heading) of a point relative to a runway.

    along_fraction 0 = at the first threshold, 1 = at the other one.
    """
    _, lat1, lon1, _, lat2, lon2 = runway
    k = math.cos(math.radians((lat1 + lat2) / 2))
    x1, y1 = lon1 * k * 111320.0, lat1 * 111320.0
    x2, y2 = lon2 * k * 111320.0, lat2 * 111320.0
    xp, yp = lon * k * 111320.0, lat * 111320.0
    dx, dy = x2 - x1, y2 - y1
    length2 = dx * dx + dy * dy or 1.0
    t = ((xp - x1) * dx + (yp - y1) * dy) / length2
    cross = abs((xp - x1) * dy - (yp - y1) * dx) / math.sqrt(length2)
    heading = math.degrees(math.atan2(dx, dy)) % 360.0
    return cross, t, heading


class AirportElevations:
    """ICAO -> elevation (ft), from UCAR stations.txt (shared with frankenweather's cache)."""

    URL = "https://weather.rap.ucar.edu/surface/stations.txt"
    CACHES = [os.path.join(os.path.expanduser("~"), ".cache", "frankenweather", "stations.txt"),
              os.path.join(os.path.expanduser("~"), ".cache", "frankenpilot", "stations.txt")]

    def __init__(self, logger):
        """Start empty; load() fills it."""
        self.logger = logger
        self.elev_ft = {}

    def load(self):
        """Load from a cache, downloading into our own cache if none exists."""
        for path in self.CACHES:
            if os.path.exists(path):
                self._parse(path)
                if self.elev_ft:
                    self.logger.info("Airport elevations: %d airports from %s",
                                     len(self.elev_ft), path)
                    return
        try:
            os.makedirs(os.path.dirname(self.CACHES[1]), exist_ok=True)
            req = urllib.request.Request(self.URL, headers={"User-Agent": "psxhacks-frankenpilot"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
            with open(self.CACHES[1], "wb") as fh:
                fh.write(data)
            self._parse(self.CACHES[1])
            self.logger.info("Airport elevations: downloaded %d airports", len(self.elev_ft))
        except OSError as exc:
            self.logger.warning("Airport elevations unavailable (%s): destination 10,000 ft rules "
                                "use 10,000 ft above sea level", exc)

    def _parse(self, path):
        with open(path, encoding="latin-1") as fh:
            for line in fh:
                if len(line) < 60 or line.startswith("!"):
                    continue
                icao = line[20:24].strip()
                try:
                    elev_m = float(line[55:59])
                except ValueError:
                    continue
                if len(icao) == 4 and icao.isalpha():
                    self.elev_ft[icao] = elev_m / 0.3048

    def get(self, icao):
        """Return the elevation in ft, or None."""
        return self.elev_ft.get(icao) if icao else None


class FrankenPilot:  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """The virtual Pilot Monitoring."""

    def __init__(self, args, config):
        """Set up from the command line and config file."""
        self.args = args
        self.logger = logging.getLogger("frankenpilot")
        self.psx = psx.Client()
        self.actions = {name: default for name, (default, _) in ACTIONS.items()}
        self.actions.update({k: bool(v) for k, v in config.get('actions', {}).items()
                             if k in ACTIONS})
        self.params = dict(DEFAULT_PARAMS)
        self.params.update({k: v for k, v in config.get('params', {}).items()
                            if k in DEFAULT_PARAMS})
        general = config.get('frankenpilot', {})
        self.enabled_setting = args.enabled or general.get('enabled', 'auto')   # auto|on|off
        self.seat_setting = (args.seat or general.get('seat', 'auto')).upper()  # AUTO|LEFT|RIGHT
        self.elevations = AirportElevations(self.logger)
        # Connection / mode
        self.connected = False
        self.router = None          # YOUAREON data, or None
        self.youareon_wait_until = 0.0
        self.connected_at = 0.0
        self.pnf_saved = None       # our PnfMode bits 0/2/3 before we took over
        self.routerinfo = {}        # uuid -> (time, type, simulator_name)
        self.seats = {}             # SHAREDINFO seat_left/right_simulator
        self.in_cloud = None
        self.in_cloud_at = 0.0
        # Flight state
        self.tasks = set()          # running action flows
        self.last_liftoff = None    # monotonic time of the last liftoff flow
        self.reset_flight()
        self.last_eicas = {'A': set(), 'C': set()}
        self.status_log = []        # recent actions for STATE
        self.active = False
        self.seat = None
        self.mode = 'starting'

    # ------------------------------------------------------------------ state

    def reset_flight(self):
        """Forget the flight phase (new situ, reconnect) and stop its unfinished flows."""
        self.cancel_actions()
        self.last_liftoff = None
        self.phase = 'UNKNOWN'      # UNKNOWN (just started), GROUND, AIRBORNE, ROLLOUT, TAXI_IN
        self.airborne_at = None
        self.flown = False
        self.dep_elev = None
        self.touchdown_hdg = None
        self.thrust_reduced = False
        self.max_tla = None
        self.on_runway = None       # runway designator we're lined up on / crossing
        self.crossing = False
        self.lights_line = 'dep'       # 10,000 ft line watched: dep (climb) / dst (descent)
        self.above_line = None
        self.above_autobrake_alt = None
        self.runway_entry_done = False
        self.icing = False             # we switched anti-ice on for icing
        self.icing_since = None        # when icing conditions began
        self.icing_seconds = 0.0       # time spent in icing conditions this episode
        self.cloud_exit_at = None      # when we last left cloud while icing
        self.icing_clear_since = None  # TAT > 10 C since

    def log_action(self, text):
        """Log an action and remember it for STATE."""
        self.logger.info("ACTION: %s", text)
        stamp = time.strftime('%H:%M:%SZ', time.gmtime())
        self.status_log = ([f"{stamp} {text}"] + self.status_log)[:20]

    def get_int(self, name, default=None):
        """Return a PSX variable as int, or default."""
        try:
            return int(self.psx.get(name))
        except (TypeError, ValueError):
            return default

    def attitude(self):
        """Return (lat, lon, alt_ft, heading_true, pitch) from PiBaHeAlTas, or None."""
        value = self.psx.get("PiBaHeAlTas")
        try:
            f = value.split(";")
            return (math.degrees(float(f[5])), math.degrees(float(f[6])), int(f[3]) / 1000.0,
                    math.degrees(float(f[2])) % 360.0, int(f[0]) / 1000.0)
        except (AttributeError, ValueError, IndexError):
            return None

    def ground_speed(self):
        """Return ground speed (kt) or None."""
        return self.get_int("GroundSpeed")

    def tat(self):
        """Return TAT (deg C) from MiscFltData, or None."""
        try:
            return int(self.psx.get("MiscFltData").split(";")[2]) / 10.0
        except (AttributeError, ValueError, IndexError):
            return None

    def eicas(self, cat):
        """Return the set of message numbers in an EICAS list (A or C)."""
        value = self.psx.get({'A': 'EicasA', 'C': 'EicasC'}[cat]) or ''
        return {int(x) for x in value.split(';') if x.strip().lstrip('-').isdigit()}

    def route_airports(self):
        """Return (departure, destination) ICAO from the active FMC route, or (None, None)."""
        mode = self.psx.get("FmcRteViAcMo") or ""
        active = mode[:1] if mode[:1] in "012" else "1"
        rte = self.psx.get(f"FmcRte{active}") or ""
        fields = rte.split(';')
        dep = fields[0].strip()[:4] if fields and len(fields[0].strip()) >= 4 else None
        dst = fields[1].strip()[:4] if len(fields) > 1 and len(fields[1].strip()) >= 4 else None
        return (dep if dep and dep.isalpha() else None), (dst if dst and dst.isalpha() else None)

    # ----------------------------------------------------------------- writes

    async def write(self, name, value, text, delay=True):
        """Set a PSX variable after a human-like delay, and log it."""
        if delay:
            await asyncio.sleep(human_delay(self.params['delay_s']))
        if not self.active or not self.connected:
            return
        self.psx.send(name, str(value))
        self.psx._set(name, str(value))  # pylint: disable=protected-access
        self.log_action(text)

    async def push_switch(self, name, on, text):
        """Set an alternate-action push switch (bit 1 = pushed) on/off, keeping its other bits."""
        value = self.get_int(name)
        if value is None or bool(value & 1) == on:
            return
        await self.write(name, (value | 1) if on else (value & ~1), text)

    async def press(self, name, text):
        """Press a DELTA push button once."""
        await self.write(name, 1, text)

    def spawn(self, coro):
        """Run an action in the background (cancelled if a new flight starts)."""
        task = asyncio.get_running_loop().create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task):
        self.tasks.discard(task)
        if not task.cancelled() and task.exception():
            self.logger.error("action failed: %s", task.exception())

    def cancel_actions(self):
        """Cancel actions still running from the previous flight (situ load, reconnect)."""
        for task in list(self.tasks):
            task.cancel()

    # ------------------------------------------------------------- PSX setup

    def setup_psx(self):
        """Subscribe to everything frankenpilot reads."""
        for name in ("id", "PiBaHeAlTas", "OnGround", "GroundSpeed", "MiscFltData", "Tla",
                     "FlapLever", "SpdBrkLever", "GearLever", "Autobr", "ApuStart", "PackSel1",
                     "PackSel2", "PackSel3", "LtLandOubL", "LtLandOubR", "LtLandInbL",
                     "LtLandInbR", "LtRwyTurnL", "LtRwyTurnR", "LtTaxi", "LtStrobe", "LtWing",
                     "LtLogo", "CfgTaxiLight", "TcasPanSel", "EfisActL", "EfisActR", "StabTrim",
                     "EicasA", "EicasC", "LocalRwys", "FmcRteViAcMo", "FmcRte0", "FmcRte1",
                     "FmcRte2", "PnfMode", "McpFdCp", "McpFdFo", "CfgNaiAuto", "CfgWaiAuto",
                     "NaiPush1", "NaiPush2", "NaiPush3", "NaiPush4", "WaiPush", "NaiTogg1",
                     "NaiTogg2", "NaiTogg3", "NaiTogg4", "WaiTogg", "FuelXfeed1", "FuelXfeed4",
                     "FuelOvrd2Fwd", "FuelOvrd2Aft", "FuelOvrd3Fwd", "FuelOvrd3Aft",
                     "FuelMain1Fwd", "FuelMain1Aft", "FuelMain2Fwd", "FuelMain2Aft",
                     "FuelMain3Fwd", "FuelMain3Aft", "FuelMain4Fwd", "FuelMain4Aft", "FuelCtrL",
                     "FuelCtrR", "FuelStabL", "FuelStabR", "FuelAuxL", "FuelAuxR"):
            self.psx.subscribe(name)
        self.psx.subscribe("version", self.on_connected)
        self.psx.subscribe("McpFdCp", self.on_fd)
        self.psx.subscribe("McpFdFo", self.on_fd)
        self.psx.subscribe("addon", self.on_addon)
        self.psx.onConnect = self.on_welcome
        self.psx.onDisconnect = self.on_disconnect
        self.psx.onPause = lambda: None
        self.psx.onResume = self.on_situ_loaded    # psx.py: load3 = a situation was loaded
        self.psx.logger = self.logger.debug

    def on_connected(self, _key, value):
        """Identify and ask for data once connected (version received)."""
        self.logger.info("Connected to PSX %s as client #%s", value, self.psx.get("id"))
        self.psx.send("name", f"{__MY_CLIENT_ID__}:{__MY_DISPLAY_NAME__} {__version__}")
        self.psx.send("demand", "GroundSpeed")
        self.psx.send("demand", "MiscFltData")
        self.psx.send("addon", f"{FRDP}:SUBSCRIBE")
        self.youareon_wait_until = time.monotonic() + 15.0
        self.connected_at = time.monotonic()
        self.connected = True

    def on_welcome(self):
        """Forget the flight phase on a full PSX welcome (connect or situ load)."""
        self.reset_flight()
        self.last_eicas = {'A': self.eicas('A'), 'C': self.eicas('C')}
        self.logger.info("PSX welcome: flight state reset")

    def on_situ_loaded(self):
        """Start a new flight when a situation is loaded (load3), like a welcome."""
        self.reset_flight()
        self.last_eicas = {'A': self.eicas('A'), 'C': self.eicas('C')}
        self.logger.info("Situation loaded: flight state reset")

    def on_disconnect(self):
        """Disconnected from PSX."""
        self.connected = False
        self.logger.info("Disconnected from PSX")

    def on_addon(self, _key, value):
        """FRDP (YOUAREON, ROUTERINFO, SHAREDINFO), MSFS in-cloud, and our own commands."""
        if value.startswith('FRANKENROUTER:'):
            parts = value[len('FRANKENROUTER:'):].split(':', 2)
            if len(parts) < 3:
                return
            try:
                data = json.loads(parts[2])
            except json.JSONDecodeError:
                return
            if parts[1] == 'YOUAREON':
                self.router = data
                self.logger.info("On frankenrouter %s/%s (%s)", data.get('simulator_name'),
                                 data.get('router_name'), data.get('type'))
            elif parts[1] == 'ROUTERINFO' and data.get('uuid'):
                self.routerinfo[data['uuid']] = (time.monotonic(), data.get('type'),
                                                 data.get('simulator_name'))
            elif parts[1] == 'SHAREDINFO':
                self.seats = {k: data.get(k, 'NOSIM')
                              for k in ('seat_left_simulator', 'seat_right_simulator')}
        elif value.startswith('FRANKENMSFSBRIDGE:'):
            try:
                data = json.loads(value[len('FRANKENMSFSBRIDGE:'):])
            except json.JSONDecodeError:
                return
            if 'in_cloud' in data:
                self.in_cloud = bool(data['in_cloud'])
                self.in_cloud_at = time.monotonic()
        elif value.startswith('FRANKENPILOT:COMMAND:'):
            self.on_command(value[len('FRANKENPILOT:COMMAND:'):])

    def on_command(self, payload):
        """Router page / other add-on commands: enabled, action toggles, params."""
        try:
            cmd = json.loads(payload)
        except json.JSONDecodeError:
            return
        if cmd.get('enabled') in ('auto', 'on', 'off'):
            self.enabled_setting = cmd['enabled']
            self.logger.info("Command: enabled = %s", self.enabled_setting)
        for name, on in (cmd.get('actions') or {}).items():
            if name in ACTIONS:
                self.actions[name] = bool(on)
                self.logger.info("Command: action %s = %s", name, self.actions[name])
        for name, value in (cmd.get('params') or {}).items():
            if name in DEFAULT_PARAMS and isinstance(value, type(DEFAULT_PARAMS[name])):
                self.params[name] = value
                self.logger.info("Command: param %s = %s", name, value)
        self.send_state()

    # ---------------------------------------------------------- mode and seat

    def update_mode(self):
        """Work out shared/local mode, whether we're active, and our seat."""
        now = time.monotonic()
        rtype = (self.router or {}).get('type')
        if rtype == 'master':
            self.mode = 'shared'
            slaves = {sim for t, typ, sim in self.routerinfo.values()
                      if typ == 'slave' and now - t < 30}
            auto_on = len(slaves) <= 1
            left = self.seats.get('seat_left_simulator', 'NOSIM')
            right = self.seats.get('seat_right_simulator', 'NOSIM')
            seat = 'RIGHT' if right == 'NOSIM' and left != 'NOSIM' else (
                'LEFT' if left == 'NOSIM' and right != 'NOSIM' else 'RIGHT')
            if now - self.connected_at < 12.0:
                # Slave routers' ROUTERINFO (every ~10 s) not all heard yet: don't decide
                self.mode, auto_on, seat = 'starting', False, None
        elif rtype == 'slave':
            self.mode = 'slave-router (not supported: run frankenpilot on the master sim)'
            auto_on, seat = False, None
        elif self.router is None and now < self.youareon_wait_until:
            self.mode = 'starting'
            auto_on, seat = False, None
        else:
            self.mode = 'local'
            auto_on = True
            if self.seat_setting in ('LEFT', 'RIGHT'):
                seat = self.seat_setting
            else:
                pnf = self.get_int("PnfMode", 0)
                seat = 'LEFT' if pnf & 1 else 'RIGHT'
        active = {'on': True, 'off': False}.get(self.enabled_setting, auto_on) and seat is not None
        if self.mode.startswith('slave'):
            active = False
        if active != self.active or seat != self.seat:
            self.logger.info("Mode %s: frankenpilot %s%s", self.mode, "ON" if active else "off",
                             f" in the {seat} seat" if active else "")
        changed = active and (not self.active or seat != self.seat)
        stopped = self.active and not active
        self.active, self.seat = active, seat
        if changed:
            self.apply_pnf_mode()
        elif stopped:
            self.restore_pnf_mode()

    def apply_pnf_mode(self):
        """Show our seat on the Instructor (bit 0); PSX's silent tasks and S/C off (bits 2, 3)."""
        pnf = self.get_int("PnfMode")
        if pnf is None or not self.connected:
            return
        if self.pnf_saved is None:
            self.pnf_saved = pnf & 0b1101
        new = (pnf & ~0b1101) | (1 if self.seat == 'LEFT' else 0)
        if new != pnf:
            self.psx.send("PnfMode", str(new))
            self.psx._set("PnfMode", str(new))  # pylint: disable=protected-access
            self.log_action(f"PSX virtual pilot: silent tasks off, seat {self.seat}")

    def restore_pnf_mode(self):
        """Inactive again: give PSX's virtual pilot back its seat, silent tasks and S/C setting."""
        pnf = self.get_int("PnfMode")
        if pnf is None or self.pnf_saved is None or not self.connected:
            return
        new = (pnf & ~0b1101) | self.pnf_saved
        self.pnf_saved = None
        if new != pnf:
            self.psx.send("PnfMode", str(new))
            self.psx._set("PnfMode", str(new))  # pylint: disable=protected-access
            self.log_action("PSX virtual pilot: silent tasks and settings restored")

    # ---------------------------------------------------------------- rules

    def tick(self):  # pylint: disable=too-many-branches,too-many-locals
        """Run the rules once (called every second)."""
        self.update_mode()
        if not self.active:
            return
        att = self.attitude()
        if att is None:
            return
        lat, lon, alt, hdg, _ = att
        on_ground = self.get_int("OnGround", 1) == 1
        gs = self.ground_speed() or 0
        dep, dst = self.route_airports()
        eicas_a, eicas_c = self.eicas('A'), self.eicas('C')
        new_a, new_c = eicas_a - self.last_eicas['A'], eicas_c - self.last_eicas['C']
        self.last_eicas = {'A': eicas_a, 'C': eicas_c}
        # --- phase tracking
        if self.phase == 'UNKNOWN':
            # Started (or a situ loaded) mid-flight is not a liftoff: no liftoff flow,
            # the departure is unknown, the destination 10,000 ft line is armed.
            if on_ground:
                self.phase = 'GROUND'
            else:
                self.phase, self.airborne_at, self.flown = 'AIRBORNE', time.monotonic(), True
                self.thrust_reduced, self.lights_line = True, 'dst'
            self.logger.info("Starting in phase %s at %.0f ft", self.phase, alt)
        elif self.phase == 'GROUND' and not on_ground:
            now = time.monotonic()
            bounce = self.last_liftoff is not None and now - self.last_liftoff < 120
            self.phase, self.airborne_at = 'AIRBORNE', now
            if not bounce:      # airborne again after a bounce: same takeoff, no new flow
                self.last_liftoff = now
                self.dep_elev = alt
                self.on_liftoff()
        elif self.phase == 'AIRBORNE':
            if not self.flown and time.monotonic() - self.airborne_at > 60:
                self.flown = True
            if on_ground and self.flown:
                self.phase, self.touchdown_hdg = 'ROLLOUT', hdg
                self.logger.info("Landed, touchdown heading %.0f", hdg)
            elif on_ground:
                self.phase = 'GROUND'       # a bounce or a situ quirk: not a flight
        elif self.phase == 'ROLLOUT':
            turned = heading_diff(hdg, self.touchdown_hdg) > self.params['exit_turn_deg']
            if gs < self.params['exit_speed_kt'] and turned:
                self.phase = 'TAXI_IN'
                self.on_runway_exit()
        elif self.phase == 'TAXI_IN' and not on_ground:
            self.phase, self.airborne_at = 'AIRBORNE', time.monotonic()   # touch and go
        # --- rules by phase
        if self.phase in ('GROUND', 'TAXI_IN') and on_ground:
            self.check_runway(lat, lon, hdg, gs)
        if self.phase == 'AIRBORNE':
            self.check_thrust_reduction()
            self.check_altitudes(alt, dep, dst)
            self.check_fuel(new_a)
        self.check_anti_ice(on_ground, new_a, new_c)

    # --- runway entry / crossing

    def check_runway(self, lat, lon, hdg, gs):
        """Runway entry flow when lined up before takeoff; strobes while crossing a runway."""
        on, aligned = None, False
        for rwy in parse_local_runways(self.psx.get("LocalRwys")):
            cross, t, rhdg = runway_position(lat, lon, rwy)
            if cross <= self.params['runway_half_width_m'] and -0.05 <= t <= 1.05:
                on = rwy[0]
                aligned = heading_diff(hdg, rhdg) < 20 or heading_diff(hdg, rhdg + 180) < 20
                break
        if on and aligned and self.phase == 'GROUND' and not self.runway_entry_done and gs < 40:
            self.runway_entry_done = True
            self.logger.info("Lined up on runway %s", on)
            self.spawn(self.runway_entry_flow())
        if on and not aligned and not self.crossing and self.actions['crossing_strobes']:
            self.crossing = True
            self.spawn(self.write("LtStrobe", 1, f"Strobes on: crossing runway {on}"))
        elif not on and self.crossing:
            self.crossing = False
            self.spawn(self.write("LtStrobe", 0, "Strobes off: runway crossed"))
        self.on_runway = on

    async def runway_entry_flow(self):
        """Strobes, taxi light, all lights except outboard landing; RTO; TA/RA."""
        if self.actions['runway_entry_lights']:
            for name, text in (("LtStrobe", "strobes"), ("LtTaxi", "taxi light"),
                               ("LtLandInbL", "inboard landing light L"),
                               ("LtLandInbR", "inboard landing light R"),
                               ("LtRwyTurnL", "runway turnoff light L"),
                               ("LtRwyTurnR", "runway turnoff light R"),
                               ("LtWing", "wing lights"), ("LtLogo", "logo lights")):
                if self.get_int(name) == 0:
                    await self.write(name, 1, f"Runway entry: {text} on")
        if self.actions['runway_entry_rto'] and self.get_int("Autobr") != AUTOBRAKE['RTO']:
            await self.write("Autobr", AUTOBRAKE['RTO'], "Runway entry: autobrake RTO")
        if self.actions['runway_entry_tcas']:
            sel = (self.psx.get("TcasPanSel") or "").split(";")
            if len(sel) == 4 and sel[3] != "3":
                await self.write("TcasPanSel", ";".join(sel[:3] + ["3"]),
                                 "Runway entry: transponder TA/RA")

    # --- after liftoff

    def on_liftoff(self):
        """Lights off after liftoff; remember the departure elevation."""
        self.logger.info("Liftoff at %.0f ft", self.dep_elev)
        self.runway_entry_done = False
        if self.actions['liftoff_lights']:
            async def flow():
                await asyncio.sleep(5.0)   # positive climb, gear up first
                for name, text in (("LtRwyTurnL", "runway turnoff light L"),
                                   ("LtRwyTurnR", "runway turnoff light R"),
                                   ("LtTaxi", "taxi light"), ("LtWing", "wing lights")):
                    if self.get_int(name) == 1:
                        await self.write(name, 0, f"After liftoff: {text} off")
            self.spawn(flow())

    def check_thrust_reduction(self):
        """Detect thrust reduction (levers back from their takeoff maximum): packs, gear lever."""
        try:
            tla = [int(x) for x in (self.psx.get("Tla") or "").split(";")[:4]]
        except ValueError:
            return
        if not tla or self.thrust_reduced:
            return
        top = max(tla)
        self.max_tla = top if self.max_tla is None else max(self.max_tla, top)
        # 3 deg of lever travel back, at least 20 s after liftoff
        if self.max_tla - top > 300 and time.monotonic() - self.airborne_at > 20:
            self.thrust_reduced = True
            self.logger.info("Thrust reduction detected")
            if self.actions['packs_after_takeoff']:
                self.spawn(self.packs_on())
            if self.actions['gear_lever_off'] and self.get_int("GearLever") == GEAR_UP:
                self.spawn(self.write("GearLever", GEAR_OFF, "After takeoff: gear lever OFF"))

    async def packs_on(self):
        """Packs on one at a time, a few seconds apart (avoid a bleed demand spike)."""
        first = True
        for n in (1, 2, 3):
            if self.get_int(f"PackSel{n}") == 0:
                if not first:
                    await asyncio.sleep(human_delay(self.params['pack_gap_s']))
                await self.write(f"PackSel{n}", 1, f"After takeoff: pack {n} on")
                first = False

    # --- 10,000 ft and 5,000 ft above the airports

    def check_altitudes(self, alt, dep, dst):
        """Lights at 10,000 ft above dep (climb) / dest (descent); autobrake at 5,000 ft above dest.

        Climbing, the line is departure elevation + 10,000 ft (the departure
        elevation is the altitude at liftoff). Once above it, lights go off and
        the line becomes destination + 10,000 ft, which only counts after we've
        been above it (a high destination can't switch them straight back on).
        """
        dst_elev = self.elevations.get(dst) or 0.0
        dep_elev = self.dep_elev if self.dep_elev is not None else (self.elevations.get(dep) or 0.0)
        dep_line = dep_elev + self.params['lights_agl_ft']
        dst_line = dst_elev + self.params['lights_agl_ft']
        line = dep_line if self.lights_line == 'dep' else dst_line
        above = alt > line
        if self.above_line is None:
            self.above_line = above
        elif above != self.above_line:
            self.above_line = above
            if self.lights_line == 'dep' and above:
                if self.actions['lights_10000']:
                    self.spawn(self.lights_10000(on=False))
                self.lights_line, self.above_line = 'dst', alt > dst_line
            elif self.lights_line == 'dst' and not above:
                if self.actions['lights_10000']:
                    self.spawn(self.lights_10000(on=True))
        ab_above = alt > dst_elev + self.params['autobrake_agl_ft']
        if self.above_autobrake_alt and not ab_above:
            setting = AUTOBRAKE.get(str(self.params['autobrake_setting']), AUTOBRAKE['4'])
            unset = self.get_int("Autobr") in (AUTOBRAKE['OFF'], AUTOBRAKE['DISARM'])
            if self.actions['autobrake_5000'] and unset:
                text = f"Descent: autobrake {self.params['autobrake_setting']} (none set)"
                self.spawn(self.write("Autobr", setting, text))
        self.above_autobrake_alt = ab_above

    async def lights_10000(self, on):
        """Landing and logo lights on (descent) / off (climb) at 10,000 ft above the airport."""
        state = "on" if on else "off"
        for name, text in (("LtLandOubL", "outboard landing light L"),
                           ("LtLandOubR", "outboard landing light R"),
                           ("LtLandInbL", "inboard landing light L"),
                           ("LtLandInbR", "inboard landing light R"),
                           ("LtLogo", "logo lights")):
            if self.get_int(name) != (1 if on else 0):
                await self.write(name, 1 if on else 0, f"10,000 ft: {text} {state}")

    # --- after runway exit

    def on_runway_exit(self):
        """Flaps, speedbrake, lights, strobes, WXR/TERR, stab trim, APU (delayed)."""
        self.logger.info("Runway exit detected")
        self.spawn(self.runway_exit_flow())

    async def runway_exit_flow(self):  # pylint: disable=too-many-branches
        """Run the after-landing flow."""
        if self.actions['exit_flaps_speedbrake']:
            if self.get_int("FlapLever", 0) != 0:
                await self.write("FlapLever", 0, "Runway exit: flaps up")
            if self.get_int("SpdBrkLever", 0) != 0:
                await self.write("SpdBrkLever", 0, "Runway exit: speedbrake down")
        if self.actions['exit_lights']:
            for name, text in (("LtLandOubL", "outboard landing light L"),
                               ("LtLandOubR", "outboard landing light R"),
                               ("LtLandInbL", "inboard landing light L"),
                               ("LtLandInbR", "inboard landing light R")):
                if self.get_int(name) == 1:
                    await self.write(name, 0, f"Runway exit: {text} off")
            if self.get_int("CfgTaxiLight", 1) == 1:
                if self.get_int("LtTaxi") == 0:
                    await self.write("LtTaxi", 1, "Runway exit: taxi light on")
            else:
                await self.write("LtLandInbL", 1,
                                 "Runway exit: inboard landing light L on as taxi light")
            if self.get_int("LtStrobe") == 1:
                await self.write("LtStrobe", 0, "Runway exit: strobes off")
        if self.actions['exit_wxr_terr']:
            for side, act, wxr, terr in (("captain", "EfisActL", "EcpWxrCp", "EcpTerrCp"),
                                         ("F/O", "EfisActR", "EcpWxrFo", "EcpTerrFo")):
                flags = (self.psx.get(act) or "").split(";")[-1]
                if len(flags) > 12:
                    if flags[6] == "1":
                        await self.press(wxr, f"Runway exit: weather radar off ({side})")
                    if flags[12] == "1":
                        await self.press(terr, f"Runway exit: terrain off ({side})")
        if self.actions['exit_stab_trim']:
            await self.stab_trim_to(self.params['stab_trim_units'])
        if self.actions['exit_apu'] and self.get_int("ApuStart") == 0:
            await asyncio.sleep(self.params['apu_delay_s'])
            if self.get_int("ApuStart") == 0:
                await self.write("ApuStart", 2, "Runway exit: APU start", delay=False)
                await asyncio.sleep(1.5)
                await self.write("ApuStart", 1, "Runway exit: APU selector ON", delay=False)

    async def stab_trim_to(self, units):
        """Trim like a pilot: hold the trim switch in blips, sometimes overshoot and come back."""
        target = units * 1000.0
        overshoot = random.random() < 0.3
        for _ in range(30):
            pos = self.get_int("StabTrim")
            if pos is None:
                return
            aim = target + (250 if overshoot and pos < target else -250 if overshoot else 0)
            err = aim - pos
            if abs(err) < 150:
                if overshoot:
                    overshoot = False
                    await asyncio.sleep(random.uniform(0.6, 1.5))
                    continue
                self.log_action(f"Runway exit: stab trim {pos / 1000:.1f} units")
                return
            direction = 1 if err > 0 else -1     # +1 = nose up = more units (test sim 2026-11-07)
            self.psx.send("StabTrimCp", str(direction))
            # The switch moves the stab ~320-340 units (0.33 trim units) per second on the
            # ground (test sim 2026-11-07); a pilot holds it at most a few seconds at a time.
            await asyncio.sleep(min(3.0, max(0.3, abs(err) / 330.0)) * random.uniform(0.7, 1.0))
            self.psx.send("StabTrimCp", "0")
            await asyncio.sleep(random.uniform(0.3, 0.9))

    # --- fuel (EICAS driven, in flight)

    def check_fuel(self, new):
        """React to fuel EICAS messages appearing (once per appearance)."""
        if not self.actions['fuel'] or not new:
            return
        rules = (
            ('FUEL OVD CTR L', "FuelCtrL", True), ('FUEL OVD CTR R', "FuelCtrR", True),
            ('FUEL LOW CTR L', "FuelCtrL", False), ('FUEL LOW CTR R', "FuelCtrR", False),
            ('FUEL PMP STB L', "FuelStabL", True), ('FUEL PMP STB R', "FuelStabR", True),
            ('FUEL LO STAB L', "FuelStabL", False), ('FUEL LO STAB R', "FuelStabR", False),
            ('FUEL AUX L', "FuelAuxL", False), ('FUEL AUX R', "FuelAuxR", False),
        )
        for msg, switch, on in rules:
            if EICAS_A[msg] in new:
                text = f">{msg}: {switch} {'on' if on else 'off'}"
                self.spawn(self.push_switch(switch, on, text))
        if EICAS_A['FUEL TANK/ENG'] in new:
            self.spawn(self.tank_to_engine())

    async def tank_to_engine(self):
        """>FUEL TANK/ENG: override pumps 2/3 off, outer crossfeeds closed, main pumps on."""
        for name in ("FuelOvrd2Fwd", "FuelOvrd2Aft", "FuelOvrd3Fwd", "FuelOvrd3Aft"):
            await self.push_switch(name, False, f">FUEL TANK/ENG: {name} off")
        for name in ("FuelXfeed1", "FuelXfeed4"):
            await self.push_switch(name, False, f">FUEL TANK/ENG: {name} closed")
        for n in (1, 2, 3, 4):
            for pos in ("Fwd", "Aft"):
                await self.push_switch(f"FuelMain{n}{pos}", True,
                                       f">FUEL TANK/ENG: FuelMain{n}{pos} on")

    # --- anti-ice (what a pilot sees: TAT, clouds out of the window, EICAS)

    def check_anti_ice(self, on_ground, new_a, new_c):  # pylint: disable=too-many-branches
        """Anti-ice by the book, edge-triggered so the crew can always override.

        ON (whatever the system type, like a pilot: even an AUTO or OFF auto
        system goes to ON): >ICING NAC -> engine, >ICING WING -> wing (in
        flight); proactively engine anti-ice on entering icing conditions (TAT
        <= 10 C and visible moisture: in cloud out of the window, MSFS).
        Back to AUTO (auto systems) / off (manual): at >ANTI-ICE NAC/WING (on,
        no ice), when TAT has been above 10 C for a minute, or after leaving
        cloud for a while - 2 min plus half the time spent in icing, at most
        10 min (long icing exposure keeps it on longer).
        """
        if not self.actions['anti_ice']:
            return
        tat = self.tat()
        if tat is None:
            return
        now = time.monotonic()
        cloud = self.in_cloud if now - self.in_cloud_at < 30 else False
        in_icing = bool(cloud and tat <= 10.0)
        self.icing_clear_since = (self.icing_clear_since or now) if tat > 10.0 else None
        if in_icing:
            self.icing_since = self.icing_since or now
            self.cloud_exit_at = None
        elif self.icing_since is not None:
            self.icing_seconds += now - self.icing_since
            self.icing_since = None
            self.cloud_exit_at = now
        # --- on
        if EICAS_C['ICING NAC'] in new_c:
            self.icing = True
            self.spawn(self.set_anti_ice('nai', True, ">ICING NAC"))
        elif in_icing and not self.icing:
            self.icing = True
            why = f"Icing conditions (TAT {tat:.0f}, in cloud)"
            self.spawn(self.set_anti_ice('nai', True, why))
        if not on_ground and EICAS_A['ICING WING'] in new_a:
            self.icing = True
            self.spawn(self.set_anti_ice('wai', True, ">ICING WING"))
        # --- off / back to AUTO
        if EICAS_A['ANTI-ICE NAC'] in new_a:
            self.spawn(self.set_anti_ice('nai', False, ">ANTI-ICE NAC (on, no ice)"))
        if not on_ground and EICAS_A['ANTI-ICE WING'] in new_a:
            self.spawn(self.set_anti_ice('wai', False, ">ANTI-ICE WING (on, no ice)"))
        if not self.icing:
            return
        wait = min(600.0, 120.0 + 0.5 * self.icing_seconds)
        why = None
        if self.icing_clear_since and now - self.icing_clear_since > 60:
            why = f"TAT {tat:.0f} > 10"
        elif self.cloud_exit_at is not None and now - self.cloud_exit_at > wait:
            why = f"Clear of cloud for {wait / 60:.0f} min"
        if why:
            self.icing, self.icing_seconds, self.cloud_exit_at = False, 0.0, None
            self.spawn(self.set_anti_ice('nai', False, why))
            if not on_ground:
                self.spawn(self.set_anti_ice('wai', False, why))

    async def set_anti_ice(self, kind, on, why):
        """Engine ('nai', all four) or wing ('wai') anti-ice ON, or back to AUTO/off.

        Auto systems (CfgNaiAuto/CfgWaiAuto) have 3-way toggles (NaiTogg1-4,
        WaiTogg): 1 = ON, 0 = AUTO (every situ has 0); manual ones push buttons.
        """
        auto = self.get_int("CfgNaiAuto" if kind == 'nai' else "CfgWaiAuto", 0) == 1
        names = [f"Nai{'Togg' if auto else 'Push'}{n}" for n in (1, 2, 3, 4)] if kind == 'nai' \
            else ["WaiTogg" if auto else "WaiPush"]
        label = "engine" if kind == 'nai' else "wing"
        for name in names:
            if auto:
                want = 1 if on else 0
                if self.get_int(name) != want:
                    await self.write(name, want, f"{why}: {label} anti-ice {name} "
                                                 f"{'ON' if on else 'AUTO'}")
            else:
                text = f"{why}: {label} anti-ice {name} {'on' if on else 'off'}"
                await self.push_switch(name, on, text)

    # --- F/D follow

    def on_fd(self, key, value):
        """When the flying pilot moves their F/D switch, move ours to match."""
        if not self.active or not self.actions['fd_follow'] or self.seat is None:
            return
        mine = "McpFdCp" if self.seat == 'LEFT' else "McpFdFo"
        theirs = "McpFdFo" if mine == "McpFdCp" else "McpFdCp"
        if key == theirs and self.psx.get(mine) != value:
            side = "captain's" if mine == "McpFdCp" else "F/O's"
            state = "on" if value == "1" else "off"
            self.spawn(self.write(mine, value, f"F/D follow: {side} F/D {state}"))

    # -------------------------------------------------------------- state out

    def send_state(self):
        """Broadcast our state for the router's frankenpilot page."""
        if not self.connected:
            return
        state = {
            'version': __version__, 'mode': self.mode, 'active': self.active, 'seat': self.seat,
            'enabled': self.enabled_setting, 'phase': self.phase,
            'actions': {name: {'on': self.actions[name], 'text': text}
                        for name, (_, text) in ACTIONS.items()},
            'params': self.params, 'recent': self.status_log[:10],
        }
        self.psx.send("addon", f"FRANKENPILOT:STATE:{json.dumps(state, separators=(',', ':'))}")

    # ------------------------------------------------------------------ main

    async def rules_task(self):
        """Run the rules every second and send STATE every 10 s."""
        last_state = 0.0
        while True:
            await asyncio.sleep(1.0)
            if not self.connected:
                continue
            try:
                self.tick()
            except Exception:  # pylint: disable=broad-exception-caught
                self.logger.exception("Rules failed")
            if time.monotonic() - last_state > 10:
                last_state = time.monotonic()
                self.send_state()

    async def run(self):
        """Connect to PSX and run."""
        await asyncio.to_thread(self.elevations.load)
        self.setup_psx()
        async with asyncio.TaskGroup() as tg:
            tg.create_task(self.psx.connect(self.args.psx_host, self.args.psx_port))
            tg.create_task(self.rules_task())


def main():
    """Parse arguments, read the config and run."""
    parser = argparse.ArgumentParser(
        description="frankenpilot: a configurable virtual Pilot Monitoring for PSX")
    parser.add_argument("--psx-host", default="127.0.0.1",
                        help="PSX main server or frankenrouter host")
    parser.add_argument("--psx-port", type=int, default=10747,
                        help="PSX main server or frankenrouter port")
    parser.add_argument("--psx-port-override", type=int, metavar="PORT",
                        help="Force this port (start scripts); warns if it differs from --psx-port")
    parser.add_argument("--config-file",
                        help="TOML config file ([frankenpilot], [actions], [params])")
    parser.add_argument("--enabled", choices=("auto", "on", "off"), help="On/off (default auto)")
    parser.add_argument("--seat", choices=("auto", "left", "right"),
                        help="Local mode: frankenpilot's seat (default: the PSX Human Pilot seat)")
    parser.add_argument("--debug", action="store_true", help="Debug logging")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    if args.psx_port_override is not None:
        if args.psx_port not in (args.psx_port_override, 10747):
            logging.warning("--psx-port-override %d replaces --psx-port %d",
                            args.psx_port_override, args.psx_port)
        args.psx_port = args.psx_port_override
    config = {}
    if args.config_file:
        with open(args.config_file, "rb") as fh:
            config = tomllib.load(fh)
    logging.info("frankenpilot %s starting", __version__)
    try:
        asyncio.run(FrankenPilot(args, config).run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
