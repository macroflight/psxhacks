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
import collections
import json
import logging
import math
import os
import random
import re
import sys
import time
import tomllib
import urllib.request

import fp_cdu
import fp_webui
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
# Readable fuel panel switch names for the log
FUEL_LABELS = {
    'FuelCtrL': "center pump L", 'FuelCtrR': "center pump R",
    'FuelStabL': "stabilizer pump L", 'FuelStabR': "stabilizer pump R",
    'FuelAuxL': "AUX pump L", 'FuelAuxR': "AUX pump R",
    'FuelXfeed1': "crossfeed 1", 'FuelXfeed4': "crossfeed 4",
    **{f"FuelOvrd{n}{p}": f"override pump {n} {p.lower()}" for n in (2, 3) for p in ("Fwd", "Aft")},
    **{f"FuelMain{n}{p}": f"main pump {n} {p.lower()}"
       for n in (1, 2, 3, 4) for p in ("Fwd", "Aft")},
}

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
    'exit_autobrake': (True, "Autobrake OFF after runway exit"),
    'exit_mcp': (True, "Both F/Ds and A/T arm off, MCP SPD pressed twice after runway exit"),
    'exit_minimums': (True, "Radio and baro minimums off on both sides after runway exit"),
    'exit_wxr_terr': (True, "After runway exit: weather radar and terrain off where on"),
    'exit_stab_trim': (True, "After runway exit: stabilizer trim to 6 units"),
    'exit_apu': (True, "After runway exit: APU start (delayed)"),
    'crossing_strobes': (True, "Strobes on while crossing a runway"),
    'fuel': (True, "Fuel pumps and outer crossfeeds in flight (Boeing procedure, EICAS driven)"),
    'anti_ice': (True, "Engine and wing anti-ice by the book"),
    'altimeters': (True, "PFD baro follows the pilot flying's; standby QNH/STD by the MSA"),
    'flap_speeds': (False, "MCP speed to the flap manoeuvre speed on flap extension (opt-in)"),
    'step_climb': (True, "Step climb when VNAV says AT NOW (VNAV PTH, NG FMC, offline on VATSIM)"),
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
    # Anti-ice: how much cloud makes "icing conditions" (in flight, TAT <= 10 C)
    'icing_auto_cont_s': 60.0,      # auto system: in cloud this long in a row ...
    'icing_auto_frac': 0.6,         # ... and this much of the last 3 min (more than ~3 oktas)
    'icing_auto_depth_ft': 2000.0,  # auto system: or in cloud through this much climb/descent
    'icing_dense_cont_s': 30.0,     # auto system: or this long in a row in dense cloud ...
    'icing_dense': 0.1,             # ... with at least this mean MSFS cloud density
    'icing_manual_cont_s': 30.0,    # manual system (and fog on the ground): this long in cloud
    'icing_atis_base_ft': 3000.0,   # departure ATIS: BKN/OVC below this (AGL) after engine start
}


# Lines for the log file only (once-a-minute state, decision context), not the console
FILE_ONLY = logging.getLogger("frankenpilot.file")
LOG_DIR = os.path.join(os.path.expanduser("~"), ".cache", "frankenpilot", "logs")
LOG_KEEP = 10    # like frankenweather


def setup_logging(debug, log_dir):
    """Console and a log file per run in log_dir, both UTC (file with date; LOG_KEEP kept)."""
    level = logging.DEBUG if debug else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    console = logging.StreamHandler()
    console_formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%SZ")
    console_formatter.converter = time.gmtime       # UTC like the log file
    console.setFormatter(console_formatter)
    console.addFilter(lambda record: not record.name.startswith("frankenpilot.file"))
    root.addHandler(console)
    try:
        os.makedirs(log_dir, exist_ok=True)
        old = sorted(f for f in os.listdir(log_dir)
                     if f.startswith("frankenpilot_") and f.endswith(".log"))
        for name in old[:max(0, len(old) - LOG_KEEP + 1)]:
            os.remove(os.path.join(log_dir, name))
        name = time.strftime("frankenpilot_%Y%m%dT%H%M%SZ.log", time.gmtime())
        path = os.path.join(log_dir, name)
        handler = logging.FileHandler(path, encoding="utf-8")
    except OSError as exc:
        logging.warning("No log file in %s: %s", log_dir, exc)
        return None
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%SZ")
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    root.addHandler(handler)
    logging.info("Logging to %s (times UTC)", path)
    return path


def switch_group(name):
    """Return a switch's panel group: names differing only in side/number are neighbours.

    LtRwyTurnL/R, LtLandOubL/InbR, NaiPush1-4, FuelMain1Fwd/2Aft, PackSel1-3...
    """
    return re.sub(r"(Oub|Inb)?(L|R|Cp|Fo)?$", "", re.sub(r"[1-4](Fwd|Aft)?$|Fwd$|Aft$", "", name))


def human_delay(span):
    """Return a random delay (seconds) in span [low, high]."""
    return random.uniform(span[0], span[1])


METAR_WEATHER = r"[+-]?(VC)?(MI|BC|PR|DR|BL|SH|TS|FZ)?(DZ|RA|SN|SG|PL|GR|GS|UP|BR|FG)+"


def parse_metar(text):
    """Return (temp C or None, visibility m or None, [(amount, base ft)], weather tokens).

    Only what the anti-ice decision needs; trends (BECMG/TEMPO) and RMK are ignored.
    """
    temp = vis = None
    layers, weather = [], []
    for tok in re.split(r"\s+", text.strip())[1:]:
        if tok in ("BECMG", "TEMPO", "RMK", "NOSIG"):
            break
        if m := re.fullmatch(r"(M?\d\d)/(M?\d\d)?", tok):
            temp = int(m.group(1).replace("M", "-"))
        elif re.fullmatch(r"\d{4}", tok) and vis is None:
            vis = int(tok)
        elif m := re.fullmatch(r"(\d+)?(?:(\d)/(\d))?SM", tok):
            frac = int(m.group(2)) / int(m.group(3)) if m.group(2) else 0
            vis = int((int(m.group(1) or 0) + frac) * 1609)
        elif tok == "CAVOK":
            vis = 10000
        elif m := re.fullmatch(r"(FEW|SCT|BKN|OVC|VV)(\d{3})(CB|TCU)?", tok):
            layers.append((m.group(1), int(m.group(2)) * 100))
        elif re.fullmatch(METAR_WEATHER, tok):
            weather.append(tok)
    return temp, vis, layers, weather


def zone_wx_weather(wx):
    """Return (temp C, visibility m, precipitation level, [(oktas, base ft AGL)]) from PSX Wx<n>.

    PSX zone weather fields (see fw_cb.WX_DEFAULTS): [3] low cloud oktas,
    [5] its base, [9] CB oktas, [11] CB base (ft above the zone), [20]
    visibility m, [21] precipitation level, [22] surface temperature C.
    None when it can't be read.
    """
    try:
        f = wx.split(";")
        return (int(f[22]), int(f[20]), int(f[21]),
                [(int(f[3]), int(f[5])), (int(f[9]), int(f[11]))])
    except (AttributeError, ValueError, IndexError):
        return None


def zone_icing_reason(wx, base_limit_ft):
    """Return why a departure's PSX zone weather means icing after takeoff, or None.

    The same rules as atis_icing_reason(), for zones whose METAR has no
    weather groups (frankenweather's "RMK FRANKENWX MODEL WX").
    """
    weather = zone_wx_weather(wx)
    if weather is None:
        return None
    temp, vis, precip, layers = weather
    if temp > 10:
        return None
    if vis <= 1600:
        return f"visibility {vis} m at {temp} C"
    if precip > 0:
        return f"precipitation (level {precip}) at {temp} C"
    low = [(oktas, base) for oktas, base in layers if oktas >= 5 and base < base_limit_ft]
    if low:
        return f"{low[0][0]}/8 cloud based at {low[0][1]} ft at {temp} C"
    return None


def atis_icing_reason(metar, base_limit_ft):
    """Return why a departure METAR means icing conditions right after takeoff, or None.

    Boeing: icing conditions exist when OAT (on the ground) is 10 C or below
    and there is visible moisture: fog with visibility 1 mile (1,600 m) or
    less, precipitation, or cloud we'll be in shortly after takeoff (here
    BKN/OVC/VV below base_limit_ft). FEW/SCT alone are not enough.
    """
    temp, vis, layers, weather = parse_metar(metar)
    if temp is None or temp > 10:
        return None
    if vis is not None and vis <= 1600:
        return f"visibility {vis} m at {temp} C"
    precip = [w for w in weather if re.search(r"DZ|RA|SN|SG|PL|GS|UP", w)]
    if precip:
        return f"{precip[0]} at {temp} C"
    low = [(amt, base) for amt, base in layers
           if amt in ("BKN", "OVC", "VV") and base < base_limit_ft]
    if low:
        return f"{low[0][0]}{low[0][1] // 100:03d} at {temp} C"
    return None


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
        self.cur_alt, self.cur_dep = None, None    # this tick's altitude and departure
        self.pf_efis = {}               # 'L'/'R' -> (baro hPa*100, STD) last seen (on_efis())
        self.cdu_key_at = {'L': 0.0, 'R': 0.0}   # last key pressed on each CDU by someone else
        self.step_busy = False          # reading the CDU / climbing now
        self.msa = {}                   # role -> (icao, msa_ft, time) from frankenweather
        self.hands = asyncio.Lock()     # one switch at a time (see write())
        self.last_switch = ("", 0.0)    # (name, time) of the last switch moved (hand_delay())
        self.pnf_saved = None       # our PnfMode bits 0/2/3 before we took over
        self.routerinfo = {}        # uuid -> (time, type, simulator_name, vatsim_connection)
        self.vpilot_status = (None, 0.0)   # local mode: the vPilot plugin's own status, time
        self.vatsim_logged = None
        self.seats = {}             # SHAREDINFO seat_left/right_simulator
        self.in_cloud = None
        self.in_cloud_at = 0.0
        self.cloud_density = None
        self.cloud_hist = collections.deque(maxlen=240)   # (t, in cloud, alt ft, density), 1 Hz
        self.tat_hist = collections.deque(maxlen=90)      # (t, TAT C, alt ft), 1 Hz
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
        self.exit_off_since, self.exit_on, self.exit_why = None, None, ""
        self.stby_want = None          # standby altimeter target: 'STD', hPa*100 or None
        self.alt_task = None           # the altimeter sync task (one at a time)
        self.alt_why = "With the pilot flying"
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
        self.icing_nac_waiting = False  # >ICING NAC on the ground before all engines ran
        self.stby_std_above, self.arrival_qnh_done, self.prev_alt = None, None, None
        self.step_cruise_since = None   # level in VNAV PTH cruise since (step climbs)
        self.step_next_look = 0.0       # when to read VNAV CRZ 2/3 next
        self.step_logged = None         # last "due but not climbing" reason logged
        self.step_last_look = 0.0
        self.flap_seen = (None, 0.0)    # (lever position, since when) - flap_speeds
        self.flap_done = None           # lever position whose speed was set
        self.atis_checked = False      # departure ATIS looked at after engine start
        self.stby_std_above = None     # standby to STD once above this (departure MSA), ft
        self.arrival_qnh_done = None   # destination whose QNH the standby got below its MSA
        self.prev_alt = None

    def context(self):
        """Return the flight state behind a decision, for the log file."""
        att = self.attitude()
        alt = f"{att[2]:.0f} ft" if att else "?"
        cloud = self.in_cloud if time.monotonic() - self.in_cloud_at < 30 else None
        dens = f" dens {self.cloud_density:.2f}" if cloud and self.cloud_density is not None else ""
        return (f"phase {self.phase}, {alt}, GS {self.ground_speed() or 0:.0f} kt, "
                f"TAT {self.tat()}, SAT {self.sat()}, cloud {cloud}{dens}, "
                f"{'all' if self.engines_running() else 'not all'} engines running")

    def decide(self, text):
        """Log a decision in plain words: what was seen and what follows (context in the file)."""
        self.logger.info("DECISION: %s", text)
        FILE_ONLY.info("  context: %s", self.context())

    def log_action(self, text):
        """Log an action (with its context in the log file) and remember it for STATE."""
        self.logger.info("ACTION: %s", text)
        FILE_ONLY.info("  context: %s", self.context())
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

    def engines_running(self):
        """Return True when all four engines run (N1 at least 18 %, EngParam fields 13-16)."""
        try:
            n1 = [int(v) for v in self.psx.get("EngParam").split(";")[13:17]]
        except (AttributeError, ValueError):
            return False
        return len(n1) == 4 and min(n1) >= 180

    def eicas(self, cat):
        """Return the set of message numbers in an EICAS list (A or C)."""
        value = self.psx.get({'A': 'EicasA', 'C': 'EicasC'}[cat]) or ''
        return {int(x) for x in value.split(';') if x.strip().lstrip('-').isdigit()}

    def departure_runway(self):
        """Return the active FMC route's departure runway (e.g. '16R', '09'), or None."""
        mode = self.psx.get("FmcRteViAcMo") or ""
        active = mode[:1] if mode[:1] in "012" else "1"
        fields = (self.psx.get(f"FmcRte{active}") or "").split(';')
        rwy = fields[2].strip().upper() if len(fields) > 2 else ""
        return rwy or None

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

    def hand_delay(self, name):
        """Return how long the hand takes to the next switch, like a person.

        The first switch of a flow: the normal reaction delay (delay_s). The
        next one right beside the last (the other of an L/R pair, the next
        landing light, engine anti-ice 1-4): 0.2-0.6 s. Further away on the
        panel: 1-2 s. (Packs keep their own gap, see packs_on().)
        """
        last_name, last_at = self.last_switch
        if time.monotonic() - last_at > 5.0:
            return human_delay(self.params['delay_s'])
        if switch_group(name) == switch_group(last_name):
            return random.uniform(0.2, 0.6)
        return random.uniform(1.0, 2.0)

    async def write(self, name, value, text, delay=True, cache=True):
        """Set a PSX variable after a human-like delay, and log it.

        One pair of hands: flows that run at the same time (runway entry and
        anti-ice, say) take turns switch by switch instead of interleaving.
        cache=False for DELTA variables (knob turns, button presses).
        """
        async with self.hands:
            if delay:
                await asyncio.sleep(self.hand_delay(name))
            if not self.active or not self.connected:
                return
            self.psx.send(name, str(value))
            if cache:
                self.psx._set(name, str(value))  # pylint: disable=protected-access
            self.last_switch = (name, time.monotonic())
            self.log_action(text)

    async def push_switch(self, name, on, text):
        """Set an alternate-action push switch (bit 1 = pushed) on/off, keeping its other bits."""
        value = self.get_int(name)
        if value is None or bool(value & 1) == on:
            return
        await self.write(name, (value | 1) if on else (value & ~1), text)

    async def press(self, name, text):
        """Press a DELTA push button once."""
        await self.write(name, 1, text, cache=False)

    def spawn(self, coro):
        """Run an action in the background (cancelled if a new flight starts)."""
        task = asyncio.get_running_loop().create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

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
        for name in ("id", "PiBaHeAlTas", "OnGround", "GroundSpeed", "MiscFltData", "EngParam",
                     "Tla", "FlapLever", "SpdBrkLever", "GearLever", "Autobr", "ApuStart",
                     "PackSel1", "PackSel2", "PackSel3", "LtLandOubL", "LtLandOubR", "LtLandInbL",
                     "LtLandInbR", "LtRwyTurnL", "LtRwyTurnR", "LtTaxi", "LtStrobe", "LtWing",
                     "LtLogo", "CfgTaxiLight", "TcasPanSel", "EfisActL", "EfisActR", "StabTrim",
                     "EicasA", "EicasC", "LocalRwys", "FmcRteViAcMo", "FmcRte0", "FmcRte1",
                     "FmcRte2", "PnfMode", "McpFdCp", "McpFdFo", "McpAtArm", "CfgNaiAuto",
                     "CfgWaiAuto",
                     "NaiPush1", "NaiPush2", "NaiPush3", "NaiPush4", "WaiPush", "NaiTogg1",
                     "NaiTogg2", "NaiTogg3", "NaiTogg4", "WaiTogg", "FuelXfeed1", "FuelXfeed4",
                     "FuelOvrd2Fwd", "FuelOvrd2Aft", "FuelOvrd3Fwd", "FuelOvrd3Aft",
                     "FuelMain1Fwd", "FuelMain1Aft", "FuelMain2Fwd", "FuelMain2Aft",
                     "FuelMain3Fwd", "FuelMain3Aft", "FuelMain4Fwd", "FuelMain4Aft", "FuelCtrL",
                     "FuelCtrR", "FuelStabL", "FuelStabR", "FuelAuxL", "FuelAuxR",
                     *[f"Metar{n}" for n in range(1, 8)], *[f"Wx{n}" for n in range(1, 8)]):
            self.psx.subscribe(name)
        self.psx.subscribe("version", self.on_connected)
        for name in ("StbyBaro", "IsfdBaroStd", "CfgStbyInstr", "EcpBaroModeCp", "EcpBaroModeFo",
                     "Afds", "McpWdoAlt", "McpWdoSpd", "FmcAppRef", "ApproachNg",
                     *[f"{side}cdu{part}" for side in "LR" for part in
                       ("Title", "Line1b", "Line2b", "Line4s", "Line4b")]):
            self.psx.subscribe(name)
        self.psx.subscribe("KeybCduL", self.on_cdu_key)
        self.psx.subscribe("KeybCduR", self.on_cdu_key)
        self.psx.subscribe("EfisActL", self.on_efis)
        self.psx.subscribe("EfisActR", self.on_efis)
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
        self.psx.send("demand", "EngParam")
        self.psx.send("addon", f"{FRDP}:SUBSCRIBE")
        self.youareon_wait_until = time.monotonic() + 15.0
        self.connected_at = time.monotonic()
        self.connected = True

    def on_welcome(self):
        """Forget the flight phase on a full PSX welcome (connect or situ load)."""
        self.reset_flight()
        if self.active:
            # The situ brought its own PnfMode: remember that one for restore_pnf_mode()
            self.pnf_saved = None
            self.apply_pnf_mode()
        self.last_eicas = {'A': self.eicas('A'), 'C': self.eicas('C')}
        self.logger.info("PSX welcome: flight state reset")

    def on_situ_loaded(self):
        """Start a new flight when a situation is loaded (load3), like a welcome."""
        self.reset_flight()
        if self.active:
            # The situ brought its own PnfMode: remember that one for restore_pnf_mode()
            self.pnf_saved = None
            self.apply_pnf_mode()
        self.last_eicas = {'A': self.eicas('A'), 'C': self.eicas('C')}
        self.logger.info("Situation loaded: flight state reset")

    def on_disconnect(self):
        """Disconnected from PSX."""
        self.connected = False
        self.logger.info("Disconnected from PSX")

    def on_addon(self, _key, value):  # pylint: disable=too-many-branches
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
                                                 data.get('simulator_name'),
                                                 data.get('vatsim_connection'))
            elif parts[1] == 'SHAREDINFO':
                self.seats = {k: data.get(k, 'NOSIM')
                              for k in ('seat_left_simulator', 'seat_right_simulator')}
        elif value.startswith('FRANKENWEATHER:MSA:'):
            self.on_msa(value[len('FRANKENWEATHER:MSA:'):])
        elif value.startswith('PSX.NET.VPILOT.STATUS'):
            # No router (local mode): the vPilot plugin's status reaches us directly
            self.vpilot_status = (value.rsplit('|', 1)[-1].strip(), time.monotonic())
        elif value.startswith('FRANKENMSFSBRIDGE:'):
            try:
                data = json.loads(value[len('FRANKENMSFSBRIDGE:'):])
            except json.JSONDecodeError:
                return
            if 'in_cloud' in data:
                self.in_cloud = bool(data['in_cloud'])
                self.in_cloud_at = time.monotonic()
            if isinstance(data.get('cloud_density'), (int, float)):
                self.cloud_density = float(data['cloud_density'])
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
            slaves = {sim for t, typ, sim, _ in self.routerinfo.values()
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
        elif active and (self.get_int("PnfMode", 0) & 0b1100):
            # A situ load (its own PnfMode) or the Instructor switched PSX's silent tasks
            # back on: two pilots doing the same tasks, so switch them off again
            self.apply_pnf_mode()

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

    def tick(self):  # pylint: disable=too-many-branches,too-many-locals,too-many-statements
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
            if gs < self.params['exit_speed_kt'] and self.runway_exited(lat, lon, hdg, gs):
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
        self.cur_alt, self.cur_dep = alt, dep
        self.check_anti_ice(on_ground, new_a, new_c)
        self.check_altimeters(alt, dep, dst)
        if self.phase == 'AIRBORNE':
            self.check_step_climb(alt)
            self.check_flap_speeds()

    # --- runway entry / crossing

    def runway_here(self, lat, lon, hdg, gs):
        """Return (runway designator, other end's, lined up?, description) or Nones."""
        for rwy in parse_local_runways(self.psx.get("LocalRwys")):
            cross, t, rhdg = runway_position(lat, lon, rwy)
            if cross <= self.params['runway_half_width_m'] and -0.05 <= t <= 1.05:
                aligned = heading_diff(hdg, rhdg) < 20 or heading_diff(hdg, rhdg + 180) < 20
                where = (f"{cross:.0f} m from the centreline, heading {hdg:.0f} "
                         f"(runway {rhdg % 360:.0f}/{(rhdg + 180) % 360:.0f}), {gs:.0f} kt")
                return rwy[0], rwy[3], aligned, where
        return None, None, False, ""

    def check_runway(self, lat, lon, hdg, gs):
        """Runway entry flow on entering the departure runway; strobes while crossing one."""
        on, other, aligned, where = self.runway_here(lat, lon, hdg, gs)
        dep_rwy = self.departure_runway()
        ends = {on.strip().upper(), other.strip().upper()} if on else set()
        is_dep = bool(dep_rwy) and dep_rwy.lstrip('0') in {e.lstrip('0') for e in ends}
        entering = bool(on) and (is_dep or aligned) and gs < 40
        if entering and self.phase == 'GROUND' and not self.runway_entry_done:
            # Runway entry: entering the FMC departure runway (at any angle), or lined
            # up on another one (a runway change the FMC doesn't know)
            self.runway_entry_done, self.crossing = True, False
            why = (f"entering the departure runway {on.strip()} (FMC: {dep_rwy})" if is_dep
                   else f"lined up on runway {on.strip()} (FMC departure runway: "
                        f"{dep_rwy or 'none'})")
            self.decide(f"{why}: {where}, before takeoff, so the runway entry flow follows")
            self.spawn(self.runway_entry_flow())
        elif on and not self.crossing and not self.runway_entry_done \
                and self.actions['crossing_strobes']:
            self.crossing = True
            self.decide(f"on runway {on.strip()}, not the departure runway "
                        f"({dep_rwy or 'none in the FMC'}) and not lined up ({where}): "
                        "crossing it, strobes on until clear")
            self.spawn(self.write("LtStrobe", 1, f"Strobes on: crossing runway {on.strip()}"))
        elif not on and self.crossing:
            self.crossing = False
            self.decide("off the runway again: crossing done, strobes off")
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
        self.decide(f"liftoff at {self.dep_elev:.0f} ft (no longer on the ground after the "
                    "takeoff roll): after-liftoff lights in 5 s, departure elevation for the "
                    "10,000 ft line set to this altitude")
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
            gear = self.get_int("GearLever")
            packs_off = [n for n in (1, 2, 3) if self.get_int(f"PackSel{n}") == 0]
            todo = [f"pack{'s' if len(packs_off) > 1 else ''} "
                    f"{', '.join(map(str, packs_off))} on" if packs_off else "packs already on",
                    "gear lever OFF" if gear == GEAR_UP else
                    f"gear lever stays {({GEAR_OFF: 'OFF', GEAR_DOWN: 'DOWN'}).get(gear, gear)}"]
            self.decide(f"thrust reduction: thrust levers back from {self.max_tla / 100:.1f} to "
                        f"{top / 100:.1f} deg, {time.monotonic() - self.airborne_at:.0f} s after "
                        f"liftoff: {', '.join(todo)}")
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
                self.decide(f"climbing through {alt:.0f} ft = {self.params['lights_agl_ft']:.0f} "
                            f"ft above the departure ({dep or '?'}, {dep_elev:.0f} ft): landing "
                            f"and logo lights off; next line {dst_line:.0f} ft "
                            f"({dst or '?'} at {dst_elev:.0f} ft)")
                if self.actions['lights_10000']:
                    self.spawn(self.lights_10000(on=False))
                self.lights_line, self.above_line = 'dst', alt > dst_line
            elif self.lights_line == 'dst' and not above:
                self.decide(f"descending through {alt:.0f} ft = {self.params['lights_agl_ft']:.0f} "
                            f"ft above the destination ({dst or '?'}, {dst_elev:.0f} ft): landing "
                            "and logo lights on")
                if self.actions['lights_10000']:
                    self.spawn(self.lights_10000(on=True))
        ab_above = alt > dst_elev + self.params['autobrake_agl_ft']
        if self.above_autobrake_alt and not ab_above:
            setting = AUTOBRAKE.get(str(self.params['autobrake_setting']), AUTOBRAKE['4'])
            unset = self.get_int("Autobr") in (AUTOBRAKE['OFF'], AUTOBRAKE['DISARM'])
            if self.actions['autobrake_5000'] and unset:
                self.decide(f"descending through {alt:.0f} ft = "
                            f"{self.params['autobrake_agl_ft']:.0f} ft above the destination "
                            f"({dst or '?'}, {dst_elev:.0f} ft) with no autobrake set: "
                            f"autobrake {self.params['autobrake_setting']}")
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

    def runway_exited(self, lat, lon, hdg, gs):
        """Return True once the aircraft (slow) has left every runway for 3 s.

        With PSX's LocalRwys: off every runway (turning onto another runway, or
        backtracking, is still on a runway). Without runway data: turned more
        than exit_turn_deg from the touchdown heading.
        """
        now = time.monotonic()
        if parse_local_runways(self.psx.get("LocalRwys")):
            on, _, _, where = self.runway_here(lat, lon, hdg, gs)
            if on:
                self.exit_off_since, self.exit_on = None, (on.strip(), where)
                return False
            self.exit_off_since = self.exit_off_since or now
            if now - self.exit_off_since < 3.0:
                return False
            self.exit_why = (f"off every runway for 3 s (last on runway "
                             f"{self.exit_on[0] if self.exit_on else '?'}), {gs:.0f} kt")
            return True
        if heading_diff(hdg, self.touchdown_hdg) > self.params['exit_turn_deg']:
            self.exit_why = (f"no runway data: turned from {self.touchdown_hdg:.0f} to {hdg:.0f} "
                             f"(more than {self.params['exit_turn_deg']:.0f} deg), {gs:.0f} kt")
            return True
        return False

    def on_runway_exit(self):
        """Flaps, speedbrake, lights, strobes, WXR/TERR, stab trim, APU (delayed)."""
        self.decide(f"runway exit: landed on heading {self.touchdown_hdg:.0f}, now {self.exit_why} "
                    f"(slower than {self.params['exit_speed_kt']:.0f} kt): after-landing flow")
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
            else:       # no taxi light installed: both inboard landing lights instead
                for name, text in (("LtLandInbL", "inboard landing light L"),
                                   ("LtLandInbR", "inboard landing light R")):
                    await self.write(name, 1, f"Runway exit: {text} on as taxi light")
            if self.get_int("LtStrobe") == 1:
                await self.write("LtStrobe", 0, "Runway exit: strobes off")
        if self.actions['exit_autobrake'] and self.get_int("Autobr") != AUTOBRAKE['OFF']:
            await self.write("Autobr", AUTOBRAKE['OFF'], "Runway exit: autobrake OFF")
        if self.actions['exit_mcp']:
            # SPD twice (user: the MCP speed window back to 100/200 kt, aircraft config)
            # while the A/T is still armed (without it PSX ignores SPD), then F/Ds, A/T arm
            for n in (1, 2):
                await self.press("McpPshSpd", f"Runway exit: MCP SPD button ({n})")
                await asyncio.sleep(random.uniform(0.3, 0.7))
            for name, text in (("McpFdCp", "captain's F/D off"), ("McpFdFo", "F/O's F/D off"),
                               ("McpAtArm", "A/T arm off")):
                if self.get_int(name) == 1:
                    await self.write(name, 0, f"Runway exit: {text}")
        if self.actions['exit_minimums']:
            await self.clear_minimums()
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

    async def clear_minimums(self):
        """Radio and baro minimums off on both EFIS control panels (knob turned all the way down).

        EfisActL/R field 1 = "R"/"r" + radio minimum (-1 = off), field 2 = baro minimum
        (-101 = off), field 3 = MINS selector (1 = BARO, 0 = RADIO; EcpMinsModeCp/Fo);
        EcpMinsCp/Fo turn the selected one (test sim 2026-11-08).
        """
        for side, suffix, name in (('L', 'Cp', "captain's"), ('R', 'Fo', "F/O's")):
            try:
                fields = self.psx.get(f"EfisAct{side}").split(";")
                radio, baro, mode = int(fields[0].lstrip("Rr")), int(fields[1]), int(fields[2])
            except (AttributeError, ValueError, IndexError):
                continue
            todo = [(0, "radio")] if radio >= 0 else []
            todo += [(1, "baro")] if baro > -101 else []
            for want, what in todo:
                if mode != want:
                    await self.write(f"EcpMinsMode{suffix}", want,
                                     f"Runway exit: {name} MINS selector {what.upper()}")
                    mode = want
                async with self.hands:
                    await asyncio.sleep(self.hand_delay(f"EcpMins{suffix}"))
                    self.psx.send(f"EcpMins{suffix}", "-6000")
                    self.last_switch = (f"EcpMins{suffix}", time.monotonic())
                self.log_action(f"Runway exit: {name} {what} minimums off")
                await asyncio.sleep(0.5)

    async def stab_trim_to(self, units):
        """Trim like a pilot: hold the trim switch in blips, sometimes overshoot and come back."""
        target = units * 1000.0
        start = self.get_int("StabTrim")
        if start is None:
            return
        # Sometimes a little past the target first (chosen once, beyond it as seen from the
        # start: deciding per blip made it chase across the target and give up)
        aim = target + (250 if start < target else -250) if random.random() < 0.3 else target
        for _ in range(30):
            pos = self.get_int("StabTrim")
            if pos is None:
                return
            err = aim - pos
            if abs(err) < 150:
                if aim != target:
                    aim = target
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
        self.logger.warning("Runway exit: stab trim gave up at %s (target %.1f units)",
                            self.get_int("StabTrim"), units)

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
        todo = [(switch, on, f">{msg}: {FUEL_LABELS[switch]} {'on' if on else 'off'}")
                for msg, switch, on in rules if EICAS_A[msg] in new]
        for msg, switch, on in rules:
            if EICAS_A[msg] in new:
                self.decide(f">{msg} appeared on EICAS: {FUEL_LABELS[switch]} "
                            f"{'on' if on else 'off'} (Boeing fuel procedure)")
        if todo:    # one after the other, like a hand moving across the panel
            self.spawn(self.push_switches(todo))
        if EICAS_A['FUEL TANK/ENG'] in new:
            self.decide(">FUEL TANK/ENG appeared on EICAS: tank-to-engine (override pumps 2/3 "
                        "off, outer crossfeeds closed, main pumps on)")
            self.spawn(self.tank_to_engine())

    async def push_switches(self, todo):
        """Set several push switches (name, on, text) one after the other."""
        for name, on, text in todo:
            await self.push_switch(name, on, text)

    async def tank_to_engine(self):
        """>FUEL TANK/ENG: override pumps 2/3 off, outer crossfeeds closed, main pumps on."""
        todo = [(name, False, f">FUEL TANK/ENG: {FUEL_LABELS[name]} off")
                for name in ("FuelOvrd2Fwd", "FuelOvrd2Aft", "FuelOvrd3Fwd", "FuelOvrd3Aft")]
        todo += [(name, False, f">FUEL TANK/ENG: {FUEL_LABELS[name]} closed")
                 for name in ("FuelXfeed1", "FuelXfeed4")]
        mains = [f"FuelMain{n}{pos}" for n in (1, 2, 3, 4) for pos in ("Fwd", "Aft")]
        todo += [(name, True, f">FUEL TANK/ENG: {FUEL_LABELS[name]} on") for name in mains]
        await self.push_switches(todo)

    # --- anti-ice (what a pilot sees: TAT, clouds out of the window, EICAS)

    def sat(self):
        """Return SAT (deg C) from MiscFltData, or None."""
        try:
            return int(self.psx.get("MiscFltData").split(";")[1]) / 10.0
        except (AttributeError, ValueError, IndexError):
            return None

    def cloud_run(self, now):
        """Return (seconds in cloud in a row, altitude span ft, mean density) of the current run."""
        start, lo, hi, dens = None, None, None, []
        for t, cloud, alt, density in reversed(self.cloud_hist):
            if not cloud:
                break
            start, lo, hi = t, min(alt, lo if lo is not None else alt), max(alt, hi or alt)
            if density is not None:
                dens.append(density)
        if start is None:
            return 0.0, 0.0, None
        return now - start, hi - lo, (sum(dens) / len(dens) if dens else None)

    def climb_trend(self):
        """Return (climb rate ft/min, TAT expected in a minute) from the last ~90 s, or Nones."""
        if len(self.tat_hist) < 30:
            return None, None
        (t0, tat0, alt0), (t1, tat1, alt1) = self.tat_hist[0], self.tat_hist[-1]
        if t1 - t0 < 20:
            return None, None
        return (alt1 - alt0) / (t1 - t0) * 60.0, tat1 + (tat1 - tat0) / (t1 - t0) * 60.0

    def icing_significant(self, now, on_ground, auto):
        """Return why the cloud we're in makes icing conditions (TAT <= 10 C), or None.

        Few whiffs on a CAVOK day are not icing conditions for a crew with an auto
        system (the ice detectors handle those); a manual system, and fog on the
        ground, need less. Climb and cruise at SAT -40 C or colder: no icing (FCOM).
        A descent through a layer where TAT is about to rise above 10 C: none.
        """
        cont, depth, density = self.cloud_run(now)
        prm = self.params
        if on_ground or not auto:
            return f"in cloud for {cont:.0f} s" if cont >= prm['icing_manual_cont_s'] else None
        rate, tat_soon = self.climb_trend()
        descending = rate is not None and rate < -500
        sat = self.sat()
        if (descending and tat_soon > 10.0) or (
                not descending and sat is not None and sat <= -40.0):
            return None
        frac = sum(c for t, c, _, _ in self.cloud_hist if now - t <= 180) / 180.0
        reasons = (
            (cont >= prm['icing_auto_cont_s'] and frac >= prm['icing_auto_frac'],
             f"in cloud for {cont:.0f} s, {frac:.0%} of the last 3 min"),
            (depth >= prm['icing_auto_depth_ft'], f"in cloud through {depth:.0f} ft"),
            (cont >= prm['icing_dense_cont_s'] and (density or 0.0) >= prm['icing_dense'],
             f"in dense cloud for {cont:.0f} s"))
        return next((text for hit, text in reasons if hit), None)

    def departure_atis(self, dep):
        """Return (METAR text, zone Wx<n> or None) of the departure's PSX zone, or (None, None).

        The zone is the one whose Metar<n> starts with the departure's ICAO. A
        METAR without weather groups (frankenweather writes just "ICAO time
        RMK FRANKENWX MODEL WX") comes with that zone's Wx<n>: then the zone
        weather is what the crew would get on the ATIS.
        """
        if not dep:
            return None, None
        for n in range(1, 8):
            metar = (self.psx.get(f"Metar{n}") or "").strip()
            if metar.startswith(dep + " "):
                no_groups = parse_metar(metar)[0] is None
                return metar, (self.psx.get(f"Wx{n}") if no_groups else None)
        return None, None

    def check_anti_ice(self, on_ground, new_a, new_c):
        """Anti-ice by the book, edge-triggered so the crew can always override.

        ON (whatever the system type, like a pilot: even an AUTO or OFF auto
        system goes to ON): >ICING NAC -> engine, >ICING WING -> wing (in
        flight); engine anti-ice on entering icing conditions: TAT <= 10 C and
        enough cloud out of the window (MSFS) - see icing_significant(); on the
        ground, after engine start, also when the departure ATIS reports them.
        Back to AUTO (auto systems) / off (manual): see anti_ice_off().
        """
        if not self.actions['anti_ice']:
            return
        tat = self.tat()
        if tat is None:
            return
        now = time.monotonic()
        cloud = self.in_cloud if now - self.in_cloud_at < 30 else False
        alt = self.cur_alt or 0.0
        self.cloud_hist.append((now, bool(cloud), alt, self.cloud_density if cloud else None))
        self.tat_hist.append((now, tat, alt))
        in_icing = bool(cloud and tat <= 10.0)     # raw: how long in icing, when it ended
        self.icing_clear_since = (self.icing_clear_since or now) if tat > 10.0 else None
        if in_icing:
            self.icing_since = self.icing_since or now
            self.cloud_exit_at = None
        elif self.icing_since is not None:
            self.icing_seconds += now - self.icing_since
            self.icing_since = None
            self.cloud_exit_at = now
        self.anti_ice_on(now, on_ground, in_icing, tat, new_c)
        if not on_ground and EICAS_A['ICING WING'] in new_a:
            self.icing = True
            self.decide(">ICING WING appeared on EICAS (ice detectors): wing anti-ice ON")
            self.spawn(self.set_anti_ice('wai', True, ">ICING WING"))
        self.anti_ice_off(now, on_ground, tat, new_a)

    def anti_ice_on(self, now, on_ground, in_icing, tat, new_c):
        """Engine anti-ice on; on the ground only once all engines run (after engine start)."""
        if EICAS_C['ICING NAC'] in new_c:
            self.icing_nac_waiting = True
        if on_ground and not self.engines_running():
            return
        if self.icing_nac_waiting and EICAS_C['ICING NAC'] in self.last_eicas['C']:
            self.icing, self.icing_nac_waiting = True, False
            self.decide(">ICING NAC on EICAS (ice detectors)" +
                        (" and all engines running" if on_ground else "") + ": engine anti-ice ON")
            self.spawn(self.set_anti_ice('nai', True, ">ICING NAC"))
            return
        self.icing_nac_waiting = False
        if self.icing:
            return
        reason = None
        if in_icing:
            auto = self.get_int("CfgNaiAuto", 0) == 1
            reason = self.icing_significant(now, on_ground, auto)
            reason = reason and f"Icing conditions (TAT {tat:.0f}, {reason})"
        if not reason and on_ground and not self.atis_checked and self.phase == 'GROUND':
            # Once after engine start: icing conditions right after takeoff per the ATIS
            self.atis_checked = True
            metar, zone_wx = self.departure_atis(self.cur_dep)
            limit = self.params['icing_atis_base_ft']
            if zone_wx:
                atis = zone_icing_reason(zone_wx, limit)
                source = f"departure zone weather ({metar[:4]} Wx {zone_wx})"
            else:
                atis = metar and atis_icing_reason(metar, limit)
                source = f"departure ATIS ({metar})"
            reason = atis and f"Departure weather: {atis}"
            if metar:
                self.decide(f"all engines running, {source}: " +
                            (f"icing conditions after takeoff ({atis}), engine anti-ice ON"
                             if atis else "no icing conditions expected after takeoff"))
        elif reason:
            self.decide(f"{reason}, {'on the ground' if on_ground else 'in flight'}, "
                        f"{'auto' if self.get_int('CfgNaiAuto', 0) == 1 else 'manual'} "
                        "anti-ice system: engine anti-ice ON")
        if reason:
            self.icing = True
            self.spawn(self.set_anti_ice('nai', True, reason))

    def anti_ice_off(self, now, on_ground, tat, new_a):
        """Switch anti-ice back to AUTO / off when icing conditions have ended.

        TAT above 10 C for a minute; in flight also >ANTI-ICE NAC/WING, or clear
        of cloud for 2 min plus half the time spent in icing (at most 10 min).
        Nothing here depends on the engines: an engine failure on takeoff never
        switches anti-ice off. On the ground only TAT > 10 C ends icing
        conditions; fog patches or breaking out of a low layer on the takeoff
        roll don't, the clear-of-cloud time starts at liftoff.
        """
        if on_ground and self.cloud_exit_at is not None:
            self.cloud_exit_at = now
        if not on_ground and EICAS_A['ANTI-ICE NAC'] in new_a:
            self.decide(">ANTI-ICE NAC on EICAS (engine anti-ice on, no ice detected): "
                        "engine anti-ice back to AUTO/off")
            self.spawn(self.set_anti_ice('nai', False, ">ANTI-ICE NAC (on, no ice)"))
        if not on_ground and EICAS_A['ANTI-ICE WING'] in new_a:
            self.decide(">ANTI-ICE WING on EICAS (wing anti-ice on, no ice detected): "
                        "wing anti-ice back to AUTO/off")
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
            self.decide(f"icing conditions over ({why}; {self.icing_seconds / 60:.0f} min in "
                        "icing conditions): anti-ice back to AUTO/off")
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
        for name in names:
            label = f"engine {name[-1]}" if kind == 'nai' else "wing"
            if auto:
                want = 1 if on else 0
                if self.get_int(name) != want:
                    await self.write(name, want, f"{why}: {label} anti-ice "
                                                 f"{'ON' if on else 'AUTO'}")
            else:
                text = f"{why}: {label} anti-ice {'on' if on else 'off'}"
                await self.push_switch(name, on, text)

    # --- altimeters

    def efis(self, side):
        """Return (baro setting hPa*100, STD?) of the L or R PFD (EfisActL/R fields 4, 5).

        Field 5: 1 = QNH, 2 = STD, 3 = STD with a preselected QNH showing (the baro
        knob turned while in STD; test sim 2026-11-08). 3 is still STD.
        """
        try:
            fields = self.psx.get(f"EfisAct{side}").split(";")
            return int(fields[3]), fields[4] in ("2", "3")
        except (AttributeError, ValueError, IndexError):
            return None

    def sides(self):
        """Return (our PFD side, the pilot flying's): 'L'/'R'."""
        return ('L', 'R') if self.seat == 'LEFT' else ('R', 'L')

    def msa_for(self, role, icao):
        """Return frankenweather's MSA (ft) for the departure/destination icao, or None."""
        entry = self.msa.get(role)
        if not entry or not icao or entry[0] != icao or time.monotonic() - entry[2] > 600:
            return None
        return entry[1]

    def on_msa(self, payload):
        """Note frankenweather's departure/destination MSA ("<uuid>:{json}")."""
        try:
            data = json.loads(payload.split(':', 1)[1])
            self.msa[data['role']] = (data['icao'], int(data['msa_ft']), time.monotonic())
        except (IndexError, KeyError, TypeError, ValueError):
            pass

    def on_efis(self, key, _value):
        """Follow the pilot flying's PFD QNH/STD on ours; the standby altimeter by the MSA.

        Pilots go to STD when cleared to a flight level, to QNH when cleared
        to an altitude: frankenpilot only follows. The standby altimeter stays
        on the departure QNH until above the departure MSA (the terrain on
        departure charts is relative to it), and gets every new QNH set.
        """
        side = key[-1]          # EfisActL / EfisActR
        cur, last = self.efis(side), self.pf_efis.get(side)
        self.pf_efis[side] = cur    # both sides, also while inactive: the first change counts
        if self.seat is None:
            return
        pf_side = self.sides()[1]
        changed = cur is not None and last is not None and cur != last
        if side != pf_side or not changed or not self.active or not self.actions['altimeters']:
            return
        baro, std = cur
        pf_name = "captain" if pf_side == 'L' else "F/O"
        if std and not last[1]:
            msa = self.msa_for('departure', self.cur_dep)
            alt = self.cur_alt or 0.0
            if msa and alt < msa:
                self.stby_std_above = msa
                stby = (f"standby altimeter stays on QNH until above the departure MSA {msa} ft "
                        f"(now {alt:.0f} ft)")
            else:
                self.stby_std_above = None
                stby = ("standby altimeter STD too (" +
                        (f"above the departure MSA {msa} ft" if msa else
                         "no departure MSA from frankenweather") + ")")
            self.decide(f"the {pf_name} (pilot flying) set STD on their PFD: our PFD STD, {stby}")
            if not self.stby_std_above:
                self.stby_want = 'STD'
        elif not std:
            self.stby_std_above = None
            if last[1] or self.alt_task is None or self.alt_task.done():   # not every click
                self.decide(f"the {pf_name} (pilot flying) set QNH {baro / 100:.0f} on their PFD: "
                            "our PFD and the standby altimeter follow")
            self.stby_want = baro
        self.kick_altimeters()      # also a baro preselect while in STD

    def check_altimeters(self, alt, dep, dst):
        """Standby altimeter: STD above the departure MSA; destination QNH below its MSA."""
        prev, self.prev_alt = self.prev_alt, alt
        if not self.actions['altimeters']:
            return
        own_pf = self.efis(self.sides()[1]) if self.seat else None
        if self.stby_std_above is not None:
            if own_pf is None or not own_pf[1]:
                self.stby_std_above = None
            elif alt > self.stby_std_above:
                self.decide(f"climbing through {alt:.0f} ft, above the departure ({dep}) MSA "
                            f"{self.stby_std_above} ft: standby altimeter STD")
                self.stby_std_above = None
                self.stby_want = 'STD'
                self.kick_altimeters("Above the departure MSA")
        msa = self.msa_for('destination', dst)
        crossed = msa is not None and prev is not None and prev >= msa > alt
        if self.phase == 'AIRBORNE' and crossed and self.arrival_qnh_done != dst:
            qnh, source = self.arrival_qnh(dst)
            if qnh:
                self.arrival_qnh_done = dst
                self.decide(f"descending through {alt:.0f} ft, below the destination ({dst}) MSA "
                            f"{msa} ft: standby altimeter to the arrival QNH {qnh / 100:.0f} "
                            f"({source})")
                self.stby_want = qnh
                self.kick_altimeters("Below the destination MSA")

    def arrival_qnh(self, dst):
        """Return (QNH hPa*100, source) for dst from its PSX METAR or zone, or (None, '')."""
        for n in range(1, 8):
            metar = (self.psx.get(f"Metar{n}") or "").strip()
            if not dst or not metar.startswith(dst + " "):
                continue
            if m := re.search(r" Q(\d{4})\b", metar):
                return int(m.group(1)) * 100, f"{dst} METAR"
            if m := re.search(r" A(\d{4})\b", metar):
                return round(int(m.group(1)) * 33.8639), f"{dst} METAR"
            try:   # frankenweather: no weather groups, the zone's QNH (inHg*100)
                return round(int(self.psx.get(f"Wx{n}").split(";")[23]) * 33.8639), \
                    f"{dst} zone weather"
            except (AttributeError, ValueError, IndexError):
                return None, ""
        return None, ""

    def kick_altimeters(self, why="With the pilot flying"):
        """Start the altimeter sync task unless it's running (it works on the latest state)."""
        self.alt_why = why
        if self.alt_task is None or self.alt_task.done():
            self.alt_task = self.spawn(self.sync_altimeters())

    def standby_matches(self, want):
        """Return True if the standby altimeter shows want ('STD' or hPa*100)."""
        isfd = self.get_int("CfgStbyInstr") == 2
        cur = self.get_int("StbyBaro")
        if want == 'STD':
            return self.get_int("IsfdBaroStd") == 1 if isfd else \
                cur is not None and abs(cur - 101325) <= 4
        if isfd and self.get_int("IsfdBaroStd") == 1:
            return False
        return cur is not None and abs(cur - want) <= 4

    async def sync_altimeters(self):
        """Bring our PFD (STD/QNH, baro) to the pilot flying's and the standby to stby_want.

        One task at a time, re-reading both PFDs before every step: a pilot
        flicking STD/QNH or turning the knob click by click never leaves a queue
        of stale actions behind (a toggle pressed on old information flips the
        wrong way). Done when everything matches for a second.
        """
        for _ in range(40):
            if not self.active or self.seat is None or not self.actions['altimeters']:
                return
            own, pf_side = self.sides()
            pf, mine = self.efis(pf_side), self.efis(own)
            if pf is None or mine is None:
                return
            side_name = "captain's" if own == 'L' else "F/O's"
            unit = 100.0 if self.get_int(f"EcpBaroMode{'Cp' if own == 'L' else 'Fo'}", 1) == 1 \
                else 33.8639
            if mine[1] != pf[1]:
                await self.press(f"EcpStd{'Cp' if own == 'L' else 'Fo'}",
                                 f"Altimeters: {side_name} PFD {'STD' if pf[1] else 'QNH'}")
                for _ in range(20):         # wait until PSX shows the toggle
                    await asyncio.sleep(0.1)
                    now_mine = self.efis(own)
                    if now_mine is None or now_mine[1] != mine[1]:
                        break
                continue
            if abs(mine[0] - pf[0]) > unit / 2:
                await self.turn_pfd_baro(own, pf[0],
                                         f"Altimeters: {side_name} PFD baro {pf[0] / 100:.0f}")
                continue
            want = self.stby_want
            if want is not None and not self.standby_matches(want):
                if want == 'STD':
                    await self.standby_std(self.alt_why)
                else:
                    await self.standby_qnh(want, self.alt_why)
                continue
            await asyncio.sleep(1.0)        # settled - unless the pilot flying moved again
            if self.efis(pf_side) == pf and self.stby_want == want:
                return
        self.logger.warning("Altimeters: still not in sync after 40 steps")

    async def turn_pfd_baro(self, own, target, text):
        """Turn our PFD baro knob to target (hPa*100): 1 hPa or 0.01 inHg per click."""
        suffix = 'Cp' if own == 'L' else 'Fo'
        unit = 100.0 if self.get_int(f"EcpBaroMode{suffix}", 1) == 1 else 33.8639
        async with self.hands:
            await asyncio.sleep(self.hand_delay(f"EcpBaro{suffix}"))
            moved = False
            for _ in range(5):
                mine = self.efis(own)
                clicks = round((target - mine[0]) / unit) if mine else 0
                if clicks == 0 or not self.active:
                    break
                self.psx.send(f"EcpBaro{suffix}", str(max(-6000, min(6000, clicks))))
                moved = True
                await asyncio.sleep(0.4)
            if moved:
                self.last_switch = (f"EcpBaro{suffix}", time.monotonic())
                self.log_action(text)

    async def turn_standby(self, target, text):
        """Turn the standby altimeter's baro knob to target (StbyBaro, hPa*100)."""
        async with self.hands:
            await asyncio.sleep(self.hand_delay("TurnStbyBaro"))
            moved = False
            for _ in range(300):
                cur = self.get_int("StbyBaro")
                if cur is None or not self.active or abs(target - cur) <= 4:
                    break
                if self.stby_want not in (None, target) and not \
                        (self.stby_want == 'STD' and target == 101325):
                    break       # the pilot flying set something else meanwhile
                clicks = max(-16, min(16, round((target - cur) / 4))) or (1 if target > cur else -1)
                self.psx.send("TurnStbyBaro", str(clicks))
                moved = True
                await asyncio.sleep(0.1)
            if moved:
                self.last_switch = ("TurnStbyBaro", time.monotonic())
                self.log_action(text)

    async def standby_qnh(self, qnh, why):
        """Standby altimeter to a QNH (hPa*100): ISFD out of STD first."""
        if self.get_int("CfgStbyInstr") == 2 and self.get_int("IsfdBaroStd") == 1:
            await self.press("IsfdPushStd", f"{why}: standby altimeter STD off")
        await self.turn_standby(qnh, f"{why}: standby altimeter {qnh / 100:.0f}")

    async def standby_std(self, why):
        """Standby altimeter to STD: the ISFD's STD button, else turned to 1013.25."""
        if self.get_int("CfgStbyInstr") == 2:
            if self.get_int("IsfdBaroStd") != 1:
                await self.press("IsfdPushStd", f"{why}: standby altimeter STD")
        else:
            await self.turn_standby(101325, f"{why}: standby altimeter 1013 (STD)")

    # --- flap manoeuvre speeds (opt-in)

    FLAP_NAMES = ("UP", "1", "5", "10", "20", "25", "30")    # FlapLever 0-6
    FLAP_ADD_KT = {1: 60, 2: 40, 3: 20, 4: 10}              # PFD flap bugs: REF + this

    def selected_ref(self):
        """Return (landing flap '25'/'30', REF kt, wind correction kt) from APPROACH REF, or None.

        FmcAppRef "h100000g147..." = flaps 30 / 147 kt, "f153" = flaps 25 /
        153, "a999" = nothing selected; ApproachNg "G10" = 10 kt correction,
        "G-1;" = none entered (the page shows the default 5).
        """
        m = re.match(r"h\d{6}([a-z])(\d{3})", self.psx.get("FmcAppRef") or "")
        if not m or m.group(1) not in "fg" or m.group(2) == "999":
            return None
        corr = re.match(r"G(-?\d+)", self.psx.get("ApproachNg") or "")
        wind = int(corr.group(1)) if corr else -1
        return ("25" if m.group(1) == "f" else "30"), int(m.group(2)), (5 if wind < 0 else wind)

    def check_flap_speeds(self):
        """On flap extension (lever at rest 2 s): MCP speed to that flap's manoeuvre speed.

        What a pilot reads off the PFD speed tape: the flap bugs are the
        selected REF speed + 60 (1), 40 (5), 20 (10), 10 (20); at the landing
        flap REF + the wind correction. Never on retraction (takeoff, go-around),
        not with the speed window closed (VNAV SPD), not without a REF selected;
        flaps 25 on the way to a flaps 30 landing has no bug: nothing then.
        """
        if not self.actions['flap_speeds']:
            return
        lever, now = self.get_int("FlapLever"), time.monotonic()
        if lever != self.flap_seen[0]:
            if self.flap_done is not None and lever is not None and lever < self.flap_done:
                self.flap_done = lever      # retraction: only remember it
            self.flap_seen = (lever, now)
            return
        if lever is None or now - self.flap_seen[1] < 2.0 or lever == self.flap_done:
            return
        extended = self.flap_done is not None and lever > self.flap_done
        self.flap_done = lever
        if not extended or lever == 0:
            return
        name, ref = self.FLAP_NAMES[lever], self.selected_ref()
        target, why = None, None
        if ref is None:
            why = "no REF speed selected on APPROACH REF"
        elif name == ref[0]:
            target = ref[1] + ref[2]
            why = f"landing flap {name}: REF {ref[1]} + wind correction {ref[2]}"
        elif lever in self.FLAP_ADD_KT:
            target = ref[1] + self.FLAP_ADD_KT[lever]
            why = f"flaps {name} bug: REF {ref[1]} + {self.FLAP_ADD_KT[lever]}"
        else:
            why = f"flaps {name} on the way to a flaps {ref[0]} landing (no bug)"
        window = self.get_int("McpWdoSpd")
        if target and (window is None or window >= 1000):
            target, why = None, f"{why}, but the MCP speed window is closed (VNAV SPD)"
        self.decide(f"flaps {name} selected: " +
                    (f"MCP speed {target} kt ({why})" if target else f"no speed set ({why})"))
        if target:
            self.spawn(self.turn_mcp_speed(target, f"Flaps {name}: MCP speed {target}"))

    async def turn_mcp_speed(self, target, text):
        """Turn the MCP speed knob to target kt (1 kt per click)."""
        async with self.hands:
            await asyncio.sleep(self.hand_delay("McpTurnSpd"))
            for _ in range(30):
                cur = self.get_int("McpWdoSpd")
                if cur is None or cur >= 1000 or not self.active or cur == target:
                    break
                self.psx.send("McpTurnSpd", str(max(-16, min(16, target - cur))))
                await asyncio.sleep(0.15)
            self.last_switch = ("McpTurnSpd", time.monotonic())
            self.log_action(text)

    # --- step climbs (VNAV CRZ 2/3 on our own CDU: not on the network otherwise)

    def on_cdu_key(self, key, value):
        """Note when someone presses a key on a CDU (we leave a CDU in use alone)."""
        if value not in ("-1", None):
            self.cdu_key_at[key[-1]] = time.monotonic()

    def check_step_climb(self, alt):
        """Read VNAV CRZ 2/3 now and then in level VNAV PTH cruise; climb at "AT NOW".

        First look 5 minutes after levelling off, then shortly before the step
        point VNAV shows, otherwise every 10 minutes.
        """
        if not self.actions['step_climb'] or self.step_busy:
            return
        afds = (self.psx.get("Afds") or "").split(";")
        level_vnav_pth = len(afds) > 2 and afds[2] == "15" and afds[0] == "19"
        now = time.monotonic()
        if not level_vnav_pth or alt < 20000:
            self.step_cruise_since = None
            return
        if self.step_cruise_since is None:
            self.step_cruise_since, self.step_next_look = now, now + 300.0
            return
        # Our CDU already on VNAV CRZ 2/3 (common in cruise): reading it costs no
        # key presses, so every 30 s instead of the schedule
        own = self.sides()[0] if self.seat else None
        if own and fp_cdu.is_vnav_cruise_page2(fp_cdu.text(self.psx.get(f"{own}cduTitle"))):
            self.step_next_look = min(self.step_next_look, max(now, self.step_last_look + 30.0))
        if now >= self.step_next_look:
            self.step_last_look = now
            self.step_busy = True
            self.spawn(self.step_climb_look())

    async def step_climb_look(self):
        """Read VNAV CRZ 2/3 on our CDU; at "AT NOW" climb if everything allows it."""
        try:
            page = await self.read_vnav_page()
            now = time.monotonic()
            self.step_next_look = now + 600.0
            if page is None:
                return
            self.logger.info("CDU: VNAV CRZ: cruise %s, step to %s at %s, step size %s",
                             page['crz_alt'], page['step_to_ft'], page['at'] or '-',
                             page['step_size'])
            if not page['now']:
                if page['at_time']:
                    hhmm = int(page['at_time'])
                    utc = time.gmtime()
                    secs = ((hhmm // 100 * 60 + hhmm % 100) * 60 -
                            (utc.tm_hour * 3600 + utc.tm_min * 60 + utc.tm_sec)) % 86400
                    self.step_next_look = now + max(30.0, min(600.0, secs - 45.0))
                return
            self.step_next_look = now + 120.0
            why = self.step_climb_blocker(page)
            target = page['step_to_ft']
            if why:
                if self.step_logged != (target, why):
                    self.step_logged = (target, why)
                    self.decide(f"VNAV: step to FL{(target or 0) // 100:03d} due now "
                                f"(step size {page['step_size']}), not climbing: {why}")
                return
            self.decide(f"VNAV: step to FL{target // 100:03d} due now (cruise {page['crz_alt']}, "
                        f"step size {page['step_size']}), VNAV PTH, "
                        f"{self.vatsim_online()[1]}: MCP altitude FL{target // 100:03d}, "
                        "altitude knob pushed")
            await self.step_climb_to(target)
            self.step_cruise_since, self.step_logged = None, None
        finally:
            self.step_busy = False

    def step_climb_blocker(self, page):
        """Return why a due step climb mustn't be done, or None."""
        if page['step_size'] in ("", "0"):
            return "STEP is 0 (no step climbs)"
        if not page['step_to_ft']:
            return "no STEP TO level"
        online = self.vatsim_online()
        if online[0] is not False:
            return f"{online[1]} (ATC gives step climbs online)"
        crz = page['crz_alt'] or ""
        crz_ft = int(crz[2:]) * 100 if crz.startswith("FL") else (int(crz) if crz else None)
        mcp = self.get_int("McpWdoAlt")
        if mcp is None or crz_ft is None or mcp * 100 != crz_ft:
            return f"MCP altitude {mcp}00 isn't the cruise altitude {crz}"
        return None

    async def cdu_key(self, side, name):
        """Press (and release) a CDU key like a finger."""
        self.psx.send(f"KeybCdu{side}", str(fp_cdu.KEYS[name]))
        await asyncio.sleep(0.15)
        self.psx.send(f"KeybCdu{side}", "-1")
        await asyncio.sleep(random.uniform(0.4, 0.8))

    async def wait_title(self, side, predicate, timeout=3.0):
        """Return the CDU title once predicate(title) holds, or None after timeout."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            title = fp_cdu.text(self.psx.get(f"{side}cduTitle"))
            if predicate(title):
                return title
            await asyncio.sleep(0.1)
        return None

    async def read_vnav_page(self):
        """Read VNAV CRZ 2/3 on our own CDU and put the page that was showing back.

        Skipped (None) when someone pressed a key on it in the last minute, or
        when the page showing can't be brought back with its key and NEXT PAGE
        (INIT REF pages, DEP/ARR, other subsystems): better not to look than to
        leave the crew on another page. Never types into the scratchpad.
        """
        own = self.sides()[0] if self.seat else None
        if own is None or time.monotonic() - self.cdu_key_at[own] < 60.0:
            return None

        def lines():
            return {part: self.psx.get(f"{own}cdu{part}")
                    for part in ("Line1b", "Line2b", "Line4s", "Line4b")}
        async with self.hands:
            title = fp_cdu.text(self.psx.get(f"{own}cduTitle"))
            if fp_cdu.is_vnav_cruise_page2(title):
                return fp_cdu.parse_vnav_cruise(lines())
            key = fp_cdu.restore_key(title)
            if key is None:
                self.logger.info("CDU: not reading VNAV, the %s CDU shows %r (can't bring "
                                 "that page back)", own, title)
                return None
            await asyncio.sleep(self.hand_delay(f"KeybCdu{own}"))
            await self.cdu_key(own, "VNAV")
            vnav = await self.wait_title(own, lambda t: bool(re.search(r"\b(CLB|CRZ|DES)\b", t)))
            page = None
            if vnav and re.search(r"\bCRZ\b", vnav):
                for _ in range(3):
                    if fp_cdu.page_number(vnav)[0] == 2:
                        break
                    before = vnav
                    await self.cdu_key(own, "NEXT" if fp_cdu.page_number(vnav)[0] < 2 else "PREV")
                    vnav = await self.wait_title(own, lambda t, b=before: t != b) or before
                await asyncio.sleep(0.3)
                if fp_cdu.is_vnav_cruise_page2(vnav):
                    page = fp_cdu.parse_vnav_cruise(lines())
            await self.restore_cdu_page(own, title, key)
            self.last_switch = (f"KeybCdu{own}", time.monotonic())
            return page

    async def restore_cdu_page(self, own, title, key):
        """Bring the CDU page back: its key, then NEXT PAGE to the same page number."""
        want_name, (want_page, _) = fp_cdu.page_name(title), fp_cdu.page_number(title)
        await self.cdu_key(own, key)
        cur = await self.wait_title(own, lambda t: fp_cdu.page_name(t) == want_name) or ""
        for _ in range(10):
            if fp_cdu.page_name(cur) == want_name and fp_cdu.page_number(cur)[0] == want_page:
                return
            before = cur
            await self.cdu_key(own, "NEXT")
            cur = await self.wait_title(own, lambda t, b=before: t != b) or before
        self.logger.warning("CDU: couldn't bring back %r on the %s CDU (now %r)", title, own, cur)

    async def step_climb_to(self, target_ft):
        """MCP altitude to target_ft (100 ft per click), then push the altitude knob once."""
        async with self.hands:
            await asyncio.sleep(self.hand_delay("McpTurnAlt"))
            for _ in range(30):
                cur = self.get_int("McpWdoAlt")
                if cur is None or not self.active or cur == target_ft // 100:
                    break
                self.psx.send("McpTurnAlt", str(max(-16, min(16, target_ft // 100 - cur))))
                await asyncio.sleep(0.15)
            await asyncio.sleep(random.uniform(0.5, 1.0))
            if self.active and self.get_int("McpWdoAlt") == target_ft // 100:
                self.psx.send("McpPshAltSel", "1")
                self.last_switch = ("McpPshAltSel", time.monotonic())
                self.log_action(f"Step climb: MCP altitude FL{target_ft // 100:03d}, "
                                "altitude knob pushed")
            else:
                self.logger.warning("Step climb: MCP altitude didn't reach FL%03d",
                                    target_ft // 100)

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
            other = "F/O" if theirs == "McpFdFo" else "captain"
            self.decide(f"the {other} (pilot flying) switched their F/D {state}: {side} F/D "
                        "follows")
            self.spawn(self.write(mine, value, f"F/D follow: {side} F/D {state}"))

    # -------------------------------------------------------------- state out

    def vatsim_online(self):
        """Return (True/False/None, why): whether this flight is online on VATSIM.

        Any sim connected to VATSIM (as pilot or observer) makes it online.
        From the routers' ROUTERINFO (vatsim_connection) in a shared cockpit or
        on a router, else from the vPilot plugin's own status. Offline when at
        least one sim reports offline and none online (a sim without the plugin,
        like the master sim, reports unknown and doesn't count); unknown (None)
        when no sim reports either.
        """
        now = time.monotonic()
        # Slave sims only: the master sim is never connected to VATSIM
        sims = {name: conn for t, typ, name, conn in self.routerinfo.values()
                if now - t < 60 and typ != 'master' and conn in ('online', 'offline', 'unknown')}
        status, at = self.vpilot_status
        if not sims and status and now - at < 30:
            sims = {'this sim': {'CONNECTED': 'online', 'DISCONNECTED': 'offline'}.get(
                status, 'unknown')}
        online = sorted(n for n, c in sims.items() if c == 'online')
        offline = sorted(n for n, c in sims.items() if c == 'offline')
        if online:
            return True, f"online on VATSIM: {', '.join(online)}"
        if offline:     # sims without a vPilot plugin (the master sim) report unknown
            return False, f"not on VATSIM ({', '.join(offline)} offline)"
        return None, ("VATSIM status unknown" +
                      (f" ({', '.join(n for n, c in sims.items() if c == 'unknown')})"
                       if sims else " (no vPilot plugin status)"))

    def state_dict(self):
        """Return our state (router page via STATE, and our own web UI)."""
        return {
            'version': __version__, 'mode': self.mode, 'active': self.active, 'seat': self.seat,
            'enabled': self.enabled_setting, 'phase': self.phase,
            'actions': {name: {'on': self.actions[name], 'text': text}
                        for name, (_, text) in ACTIONS.items()},
            'params': self.params, 'recent': self.status_log[:10],
            'vatsim': self.vatsim_online()[1],
        }

    def send_state(self):
        """Broadcast our state for the router's frankenpilot page."""
        if not self.connected:
            return
        state = self.state_dict()
        self.psx.send("addon", f"FRANKENPILOT:STATE:{json.dumps(state, separators=(',', ':'))}")

    # ------------------------------------------------------------------ main

    async def rules_task(self):
        """Run the rules every second and send STATE every 10 s."""
        last_state = last_context = 0.0
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
            vatsim = self.vatsim_online()
            if vatsim[0] != self.vatsim_logged:
                self.vatsim_logged = vatsim[0]
                self.logger.info("VATSIM: %s", vatsim[1])
            if self.active and time.monotonic() - last_context > 60:
                last_context = time.monotonic()
                FILE_ONLY.info("STATE: %s", self.context())

    async def run(self):
        """Connect to PSX and run."""
        await asyncio.to_thread(self.elevations.load)
        self.setup_psx()
        async with asyncio.TaskGroup() as tg:
            tg.create_task(self.psx.connect(self.args.psx_host, self.args.psx_port))
            tg.create_task(self.rules_task())
            if self.args.web_port:
                tg.create_task(fp_webui.serve(self, self.args.web_port))


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
    parser.add_argument("--web-port", type=int, default=9757,
                        help="Web UI port (state, settings, log files); 0 = no web UI")
    parser.add_argument("--log-dir", default=LOG_DIR,
                        help=f"Log file directory, one file per run (default {LOG_DIR})")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args()
    args.log_path = setup_logging(args.debug, args.log_dir)
    if args.psx_port_override is not None:
        if args.psx_port not in (args.psx_port_override, 10747):
            logging.warning("--psx-port-override %d replaces --psx-port %d",
                            args.psx_port_override, args.psx_port)
        args.psx_port = args.psx_port_override
    config = {}
    if args.config_file:
        try:
            with open(args.config_file, "rb") as fh:
                config = tomllib.load(fh)
        except OSError as exc:
            logging.error("Can't read config file %s: %s", args.config_file, exc)
            return 2
        except tomllib.TOMLDecodeError as exc:
            found = re.search(r"line (\d+)", str(exc))
            line = int(found.group(1)) if found else None
            text = ""
            if line:
                with open(args.config_file, encoding="utf-8", errors="replace") as fh:
                    text = (fh.read().splitlines()[line - 1:line] or [""])[0].strip()
            logging.error("Config file %s is not valid TOML: %s%s (true/false must be lowercase, "
                          "text in quotes, e.g. flap_speeds = true, seat = \"left\")",
                          args.config_file, exc, f": {text!r}" if text else "")
            return 2
    logging.info("frankenpilot %s starting", __version__)
    try:
        asyncio.run(FrankenPilot(args, config).run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
