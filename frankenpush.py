# pylint: disable=invalid-name,too-many-lines
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
  2. Copy the logon code shown on the sim's detail page
  3. Run this addon:
       python frankenpush.py --logon-code <code>
     The logon code is saved to frankenpush_cache.json after first use, so
     subsequent runs can omit --logon-code and will use the cached value.
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

# Logon code cache — written next to this script so the user doesn't have to
# re-enter the code on every run.
_CACHE_FILE = pathlib.Path.home() / '.frankenpush_cache.json'

# AFDS (Qs434) FMA mode number → display name. Mirrors variables.py AFDS_MODE_NAMES.
_AFDS_MODE_NAMES = {
    -29: '', 0: '', 1: 'ATT', 2: 'HDG HOLD', 3: 'HDG SEL', 4: 'LNAV',
    5: 'LOC', 6: 'ROLLOUT', 7: 'TO/GA', 8: 'TO/GA', 9: 'ALT',
    10: 'FLARE', 11: 'FLCH SPD', 12: 'G/S', 13: 'V/S', 14: 'VNAV ALT',
    15: 'VNAV PTH', 16: 'VNAV SPD', 17: 'VNAV', 18: 'IDLE', 19: 'SPD',
    20: 'THR', 21: 'THR HOLD', 22: 'THR REF', 23: 'NO AUTOLAND',
    24: 'LAND 2', 25: 'LAND 3', 26: 'CMD', 27: 'F/D', 28: 'TEST',
    29: 'VNAV FAIL', 30: 'VNAV OFF',
}


def _afds_mode_name(raw):
    """Return display name for an AFDS mode integer (handles negative pitchFault values)."""
    n = int(raw)
    return _AFDS_MODE_NAMES.get(n, _AFDS_MODE_NAMES.get(abs(n), str(n)))


def _parse_afds_fma(value):
    """Return (thr, roll, pitch, roll_armed, pitch_armed) strings from Qs434, or None."""
    try:
        f = value.split(';')
        return (_afds_mode_name(f[0]), _afds_mode_name(f[1]), _afds_mode_name(f[2]),
                _afds_mode_name(f[3]), _afds_mode_name(f[4]))
    except (ValueError, IndexError):
        return None


def _load_cached_logon_code():
    """Return the cached logon code, or None if no cache exists."""
    try:
        return json.loads(_CACHE_FILE.read_text()).get('logon_code') or None
    except (OSError, json.JSONDecodeError):
        return None


def _save_cached_logon_code(code):
    """Persist the logon code to the cache file for future runs."""
    try:
        _CACHE_FILE.write_text(json.dumps({'logon_code': code}))
    except OSError:
        pass  # not fatal — just means the next run will prompt again


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


def _flightinfo_from_flight_plan_push(data):
    """Build a frankenrouter FLIGHTINFO dict from a "flight_plan" message.

    Pushed down by Flight Centre (see CLAUDE.md's Flight Centre /
    frankenrouter integration section). Checklist state is expressed
    positionally against checklist_items, matching frankenrouter's existing
    list[bool] convention (router/frankenrouter/webapi.py) — "checklist_items"
    is included alongside it (a new key) so the router can show current
    labels instead of its own now-superseded local TOML list.

    A couple of judgement calls where Flight Centre's shape doesn't map
    1:1 onto frankenrouter's flightinfo dict:
      - captain_swap picks which of pilot_p1/pilot_p2 holds captain_code/
        fo_code (P1 is captain by default, in the left seat; captain_swap
        means the captain is the one in the right seat, i.e. P2), and also
        sets seat_swap — both describe the same fact for two consumers.
      - flight_notes and airline_sop have no separate frankenrouter slot;
        folded into the single free-text comments field.
      - observers is the departure-phase value only; frankenrouter's
        FLIGHTINFO has no per-phase observers slot.
    """
    fields = data.get("fields") or {}
    items = data.get("checklist_items") or []
    checked_by_id = {s["id"]: s["checked"] for s in (data.get("checklist_state") or [])}
    checklist = [bool(checked_by_id.get(item["id"], False)) for item in items]

    captain_swap = bool(fields.get("captain_swap"))
    p1, p2 = fields.get("pilot_p1"), fields.get("pilot_p2")
    captain_code, fo_code = (p2, p1) if captain_swap else (p1, p2)

    comments = "\n".join(
        part for part in (fields.get("airline_sop"), fields.get("flight_notes")) if part)

    return {
        "source": "flightcentre",
        "flight_plan_id": data.get("flight_plan_id"),
        "last_updated_by": "Flight Centre",
        "last_updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "portal_account": fields.get("simfest_portal_account") or "",
        "airline_icao": fields.get("airline_icao") or "",
        "airframe": fields.get("airframe") or "",
        "captain_code": captain_code or "",
        "fo_code": fo_code or "",
        "seat_swap": captain_swap,
        "p1_is_vatpri": bool(fields.get("vatpri_swap")),
        "observers": fields.get("observers") or "",
        "flight_number": fields.get("simfest_flight_number") or "",
        "vatsim_callsign": fields.get("callsign") or "",
        "dep_airport": fields.get("dep_airport") or "",
        "arr_airport": fields.get("arr_airport") or "",
        "route": fields.get("planned_route") or "",
        "preflight_starts": fields.get("report_time") or "",
        "eobt": fields.get("eobt") or "",
        "comments": comments,
        "scratchpad": fields.get("inflight_scratchpad") or "",
        "checklist": checklist,
        "checklist_items": [item.get("label") for item in items],
    }


def _unlinked_flightinfo():
    """Sentinel FLIGHTINFO for when Flight Centre reports no matching plan.

    ("link_status": linked=false) — the router UI shows a "check Flight
    Centre" banner whenever flight_plan_id is None but source is set.
    """
    return {
        "source": "flightcentre",
        "flight_plan_id": None,
        "last_updated_by": "Flight Centre",
        "last_updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


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
        self._sharedinfo: dict = {}         # latest SHAREDINFO from master router
        self._pending_simevents: list = []  # events received since last portal send
        # live FRDP peer StreamWriter, for sending Flight-Centre-sourced
        # FLIGHTINFO upstream
        self._frdp_writer = None

        # Flight Centre flight-plan push state (see CLAUDE.md's Flight
        # Centre / frankenrouter integration section). Set from "flight_plan"/
        # "link_status" messages received on the portal WebSocket; used to
        # correlate local checklist/scratchpad edits back to the right plan.
        self._active_flight_plan_id = None
        self._active_checklist_items: list = []   # [{"id", "label"}, ...]
        self._reverse_sync_queue: asyncio.Queue = asyncio.Queue()

        # Live flight state derived or received separately from position
        self._ias_kt = None
        self._vs_fpm = None
        self._left_pfd_alt_raw = None       # raw LeftPfdAlt string from PSX (demand)
        self._prev_alt_ft_for_vs = None     # previous altitude for VS computation
        self._prev_ts_for_vs = None         # timestamp of previous altitude sample

        # MCP window values (Qi32-35, ECON — updated by callbacks)
        self._mcp_spd = None    # McpWdoSpd raw integer
        self._mcp_hdg = None    # McpWdoHdg degrees
        self._mcp_vs = None     # McpWdoVs converted to fpm (raw × 100)
        self._mcp_alt = None    # McpWdoAlt converted to ft (raw × 100)
        self._mcp_psh_vs = None  # McpPshVs MCPMOM: bit 3 set = VS window visible

        # AFDS FMA modes (Qs434, ECON — parsed tuple or None)
        self._fma = None

        # Control surface levers (ECON)
        self._flap_lever = None     # Qh389 FlapLever 0-6
        self._gear_lever = None     # Qh170 GearLever 1=up 2=off 3=down
        self._spd_brk_lever = None  # Qh388 SpdBrkLever 0-800

        # Autosave situ monitoring — set when a file changes, consumed by _build_update
        self._pending_autosave_situ = None

        # Delta tracking: records the last-sent value of each slow-changing field.
        # Reset to {} on each portal (re)connection to force a full first send.
        self._sent_state: dict = {}

    # --- PSX callbacks ---

    @staticmethod
    def _tas_to_ias(tas_kt, alt_ft):
        """Derive IAS from TAS using ISA density model (troposphere + stratosphere)."""
        alt_m = alt_ft * 0.3048
        if alt_ft < 36089:
            density_ratio = max(1.0 - 2.2558e-5 * alt_m, 0.0) ** 4.2561
        else:
            density_ratio = 0.2971 * math.exp(-1.5769e-4 * (alt_m - 11000.0))
        return tas_kt * math.sqrt(max(density_ratio, 1e-9))

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
            return

        self._ias_kt = self._tas_to_ias(self._tas_kt, self._alt_ft)

        now = time.monotonic()
        if self._prev_alt_ft_for_vs is not None and self._prev_ts_for_vs is not None:
            dt = now - self._prev_ts_for_vs
            if dt > 0:
                raw_vs = (self._alt_ft - self._prev_alt_ft_for_vs) / dt * 60
                alpha = min(dt / 5.0, 1.0)
                self._vs_fpm = (raw_vs if self._vs_fpm is None
                                else (1 - alpha) * self._vs_fpm + alpha * raw_vs)
        self._prev_alt_ft_for_vs = self._alt_ft
        self._prev_ts_for_vs = now

    def _on_mcp_spd(self, _key, value):
        # McpWdoSpd encoding: >950 = blanked (AFDS controls speed),
        # 400-950 = Mach mode (value/1000 gives Mach, e.g. 780 → 0.780),
        # 0-399 = IAS mode (direct knots).
        try:
            n = int(value)
            if n > 950:
                self._mcp_spd = None
            elif n >= 400:
                self._mcp_spd = round(n / 1000, 3)  # Mach float e.g. 0.780
            else:
                self._mcp_spd = n  # IAS in kt
        except ValueError:
            pass

    def _on_mcp_hdg(self, _key, value):
        try:
            self._mcp_hdg = int(value)
        except ValueError:
            pass

    def _on_mcp_vs(self, _key, value):
        try:
            self._mcp_vs = int(value) * 100
        except ValueError:
            pass

    def _on_mcp_psh_vs(self, _key, value):
        try:
            self._mcp_psh_vs = int(value)
        except ValueError:
            pass

    def _on_mcp_alt(self, _key, value):
        try:
            self._mcp_alt = int(value) * 100
        except ValueError:
            pass

    def _on_afds(self, _key, value):
        self._fma = _parse_afds_fma(value)

    def _on_left_pfd_alt(self, _key, value):
        self._left_pfd_alt_raw = value or None

    def _on_flap_lever(self, _key, value):
        try:
            self._flap_lever = int(value)
        except ValueError:
            pass

    def _on_gear_lever(self, _key, value):
        try:
            self._gear_lever = int(value)
        except ValueError:
            pass

    def _on_spd_brk_lever(self, _key, value):
        try:
            self._spd_brk_lever = int(value)
        except ValueError:
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

    def _parse_capt_baro(self):
        """Return (mode_str, hpa_float) from LeftPfdAlt, or (None, None) if unavailable.

        LeftPfdAlt format: first char 's'=STD, else QNH; then alt_qnh_ft;alt_std_ft;...
        QNH hPa approximated from the difference between QNH and std altitudes.
        """
        raw = self._left_pfd_alt_raw
        if not raw or len(raw) < 2:
            return None, None
        if raw[0] == 's':
            return 'STD', None
        try:
            parts = raw[1:].split(';')
            alt_qnh = float(parts[0])
            alt_std = float(parts[1])
            hpa = round(1013.25 + (alt_qnh - alt_std) / 27.0, 1)
            return 'QNH', hpa
        except (ValueError, IndexError):
            return None, None

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

    def _build_update(self, full):  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
        """Build the JSON payload to send to Flight Centre.

        If full=True every field is included. Otherwise only fields whose
        values have changed since the last send are included — the server
        (push_manager.py) ignores absent keys rather than resetting them.
        Position fields are always included: they change on virtually every
        tick, so checking them for change adds cost without saving bandwidth.
        """
        update = {"protocol_version": PUSH_PROTOCOL_VERSION}

        # Position and derived real-time values — always include when we have a fix.
        if self._lat is not None:
            update.update({
                "lat": self._lat, "lon": self._lon,
                "alt_ft": self._alt_ft, "heading_deg": self._heading_deg,
                "tas_kt": self._tas_kt, "pitch_deg": self._pitch_deg,
                "bank_deg": self._bank_deg,
                "ias_kt": round(self._ias_kt, 1) if self._ias_kt is not None else None,
                "vs_fpm": round(self._vs_fpm) if self._vs_fpm is not None else None,
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

        # SHAREDINFO fields — delta-tracked.
        pilot_flying = self._sharedinfo.get("pilot_flying_simulator")
        elev_master = self._sharedinfo.get("elevation_source_simulator")
        traffic_master = self._sharedinfo.get("traffic_source_simulator")
        sharedinfo_now = (pilot_flying, elev_master, traffic_master)
        if full or self._sent_state.get("sharedinfo") != sharedinfo_now:
            update["pilot_flying_sim"] = pilot_flying
            update["elevation_master_sim"] = elev_master
            update["traffic_master_sim"] = traffic_master

        # MCP window values — delta-tracked.
        # V/S window is blank when McpPshVs bit 3 is not set.
        vs_visible = self._mcp_psh_vs is not None and (self._mcp_psh_vs & 8) != 0
        mcp_vs_out = self._mcp_vs if vs_visible else None
        mcp_now = (self._mcp_spd, self._mcp_hdg, mcp_vs_out, self._mcp_alt, self._mcp_psh_vs)
        if full or self._sent_state.get("mcp") != mcp_now:
            update["mcp_spd"] = self._mcp_spd
            update["mcp_hdg"] = self._mcp_hdg
            update["mcp_vs"] = mcp_vs_out
            update["mcp_alt"] = self._mcp_alt

        # AFDS FMA modes — delta-tracked.
        fma = self._fma
        if full or self._sent_state.get("fma") != fma:
            if fma is not None:
                update["fma_thr"] = fma[0]
                update["fma_roll"] = fma[1]
                update["fma_pitch"] = fma[2]
                update["fma_roll_armed"] = fma[3]
                update["fma_pitch_armed"] = fma[4]
            else:
                update.update({"fma_thr": None, "fma_roll": None, "fma_pitch": None,
                               "fma_roll_armed": None, "fma_pitch_armed": None})

        # Control surface levers — delta-tracked.
        _gear_labels = {1: "up", 2: "off", 3: "down"}
        gear_str = _gear_labels.get(self._gear_lever) if self._gear_lever is not None else None
        spd_brk_out = (self._spd_brk_lever > 0) if self._spd_brk_lever is not None else None
        controls_now = (self._flap_lever, self._gear_lever, self._spd_brk_lever)
        if full or self._sent_state.get("controls") != controls_now:
            update["flap_lever"] = self._flap_lever
            update["gear_lever"] = gear_str
            update["spd_brk_out"] = spd_brk_out

        # Captain's barometric setting — delta-tracked.
        capt_baro_mode, capt_baro_hpa = self._parse_capt_baro()
        capt_baro_now = (capt_baro_mode, capt_baro_hpa)
        if full or self._sent_state.get("capt_baro") != capt_baro_now:
            update["capt_baro_mode"] = capt_baro_mode
            update["capt_baro_hpa"] = capt_baro_hpa

        # Autosave situ — always include when pending (one-shot delivery).
        if self._pending_autosave_situ is not None:
            update["autosave_situ"] = self._pending_autosave_situ
            self._pending_autosave_situ = None

        # Sim events — always include when pending (one-shot delivery).
        if self._pending_simevents:
            update["simevents"] = self._pending_simevents
            self._pending_simevents = []

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
        if "pilot_flying_sim" in update:
            self._sent_state["sharedinfo"] = (
                update["pilot_flying_sim"],
                update["elevation_master_sim"],
                update["traffic_master_sim"])
        if "mcp_spd" in update:
            self._sent_state["mcp"] = (
                update["mcp_spd"], update["mcp_hdg"],
                update["mcp_vs"], update["mcp_alt"], self._mcp_psh_vs)
        if "fma_thr" in update:
            self._sent_state["fma"] = self._fma
        if "capt_baro_mode" in update:
            self._sent_state["capt_baro"] = (update["capt_baro_mode"], update["capt_baro_hpa"])
        if "flap_lever" in update:
            self._sent_state["controls"] = (
                self._flap_lever, self._gear_lever, self._spd_brk_lever)

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
            self._lat = None
            self._ias_kt = None
            self._vs_fpm = None
            self._prev_alt_ft_for_vs = None
            self._prev_ts_for_vs = None

        try:
            self.logger.debug("Starting %s", inspect.currentframe().f_code.co_name)
            self.psx = psx.Client()
            self.psx.onConnect = connected
            self.psx.onDisconnect = disconnected
            self.psx.onPause = lambda: None
            self.psx.onResume = lambda: self.psx.send("demand", "LeftPfdAlt")

            # PSX lexicon names (confirmed from session captures):
            #   Qs0 = CfgRego (confirmed) a.k.a. AcTailNo,
            #   PiBaHeAlTas = Qs121, FmcFltNo = Qs401,
            #   FmcRteViAcMo = Qs373, FmcRte1 = Qs376, FmcRte2 = Qs377,
            #   ActDestEta = Qi247, LeftPfdAlt = Qs562 (DEMAND),
            #   Afds = Qs434 (ECON), McpWdo* = Qi32-35 (ECON)
            self.psx.subscribe("AcTailNo", self._on_tail_number)
            self.psx.subscribe("CfgRego", self._on_tail_number)
            self.psx.subscribe("PiBaHeAlTas", self._on_position)
            self.psx.subscribe("FmcFltNo", self._on_flight_number)
            self.psx.subscribe("FmcRteViAcMo", self._on_route_mode)
            self.psx.subscribe("FmcRte1", self._on_route1)
            self.psx.subscribe("FmcRte2", self._on_route2)
            self.psx.subscribe("ActDestEta", self._on_eta)
            self.psx.subscribe("McpWdoSpd", self._on_mcp_spd)
            self.psx.subscribe("McpWdoHdg", self._on_mcp_hdg)
            self.psx.subscribe("McpWdoVs", self._on_mcp_vs)
            self.psx.subscribe("McpWdoAlt", self._on_mcp_alt)
            self.psx.subscribe("McpPshVs", self._on_mcp_psh_vs)
            self.psx.subscribe("Afds", self._on_afds)
            self.psx.subscribe("LeftPfdAlt", self._on_left_pfd_alt)
            self.psx.subscribe("FlapLever", self._on_flap_lever)
            self.psx.subscribe("GearLever", self._on_gear_lever)
            self.psx.subscribe("SpdBrkLever", self._on_spd_brk_lever)

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
            self._handle_frdp_flightinfo(payload)
        elif msg_type == 'ROUTERINFO':
            self._handle_frdp_routerinfo(payload)
        elif msg_type == 'SHAREDINFO':
            self._handle_frdp_sharedinfo(payload)
        elif msg_type == 'SIMEVENTS' and self.args.simevents:
            self._handle_frdp_simevents(payload)

    def _handle_frdp_flightinfo(self, payload):
        """Process a FLIGHTINFO addon message received over FRDP."""
        try:
            new_info = json.loads(payload)
        except json.JSONDecodeError:
            return
        old_info = self._flightinfo
        self._flightinfo = new_info
        self.logger.debug("FRDP: received FLIGHTINFO")
        # A FLIGHTINFO we ourselves pushed (source=flightcentre) shouldn't
        # be echoed back as if a crew member edited it locally — only
        # diff genuinely router-originated changes (checklist toggle /
        # scratchpad save) for the reverse-sync path to Flight Centre.
        if new_info.get('source') != 'flightcentre':
            self._queue_reverse_sync_deltas(old_info, new_info)

    def _handle_frdp_routerinfo(self, payload):
        """Process a ROUTERINFO addon message received over FRDP."""
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

    def _handle_frdp_sharedinfo(self, payload):
        """Process a SHAREDINFO addon message received over FRDP."""
        try:
            self._sharedinfo = json.loads(payload)
            self.logger.debug("FRDP: received SHAREDINFO")
        except json.JSONDecodeError:
            pass

    def _handle_frdp_simevents(self, payload):
        """Process a SIMEVENTS addon message received over FRDP."""
        try:
            data = json.loads(payload)
            # Payload is {"sim": ..., "router": ..., "events": [...]}
            events = data.get('events') if isinstance(data, dict) else data
            if isinstance(events, list):
                self._pending_simevents.extend(events)
                self.logger.debug("FRDP: queued %d SIMEVENTS from %s/%s",
                                  len(events),
                                  data.get('sim', '?') if isinstance(data, dict) else '?',
                                  data.get('router', '?') if isinstance(data, dict) else '?')
        except json.JSONDecodeError:
            pass

    def _queue_reverse_sync_deltas(self, old_info, new_info):
        """Diff two router-originated FLIGHTINFO dicts and queue deltas.

        Diffs the two fields a crew member can still edit locally
        (checklist toggles, scratchpad), and queues a delta message per
        change for _reverse_sync_loop to send to Flight Centre immediately
        — see CLAUDE.md's Flight Centre / frankenrouter integration
        section. No-op if Flight Centre hasn't told us which flight plan
        we're linked to yet.
        """
        if self._active_flight_plan_id is None:
            return
        old_info = old_info or {}
        new_info = new_info or {}

        old_checklist = old_info.get('checklist') or []
        new_checklist = new_info.get('checklist') or []
        for index, item in enumerate(self._active_checklist_items):
            old_checked = bool(old_checklist[index]) if index < len(old_checklist) else False
            new_checked = bool(new_checklist[index]) if index < len(new_checklist) else False
            if old_checked != new_checked:
                self._reverse_sync_queue.put_nowait({
                    "type": "checklist_toggle",
                    "protocol_version": PUSH_PROTOCOL_VERSION,
                    "flight_plan_id": self._active_flight_plan_id,
                    "checklist_item_id": item["id"],
                    "checked": new_checked,
                })

        old_scratchpad = old_info.get('scratchpad') or ''
        new_scratchpad = new_info.get('scratchpad') or ''
        if old_scratchpad != new_scratchpad:
            self._reverse_sync_queue.put_nowait({
                "type": "scratchpad_update",
                "protocol_version": PUSH_PROTOCOL_VERSION,
                "flight_plan_id": self._active_flight_plan_id,
                "text": new_scratchpad,
            })

    async def _send_frdp_flightinfo(self, flightinfo):
        """Send a FLIGHTINFO addon message upstream over the FRDP peer connection.

        This is the "frankenpush sends it to the routers via addon
        messages" leg of the Flight Centre -> sim push (see CLAUDE.md).
        Returns True if actually sent (a live FRDP connection is required;
        there's no queueing/retry — the next Flight Centre sync tick will
        naturally resend once the connection is back).
        """
        if self._frdp_writer is None:
            return False
        line = f"addon=FRANKENROUTER:{_FRDP_VERSION}:FLIGHTINFO:{json.dumps(flightinfo)}\r\n"
        try:
            self._frdp_writer.write(line.encode())
            await self._frdp_writer.drain()
        except (OSError, ConnectionError) as exc:
            self.logger.warning("FRDP: failed to send FLIGHTINFO: %s", exc)
            return False
        # frankenrouter floods a FLIGHTINFO to other peers but not back to
        # its origin connection, so update our own cache immediately rather
        # than waiting for an echo that will never arrive.
        self._flightinfo = flightinfo
        return True

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
                        self._frdp_writer = writer
                        self.logger.info("FRDP: peer connection established")
                        await self._run_frdp_session(reader, writer)
                    finally:
                        self._flightinfo = None
                        self._routerinfos.clear()
                        self._sharedinfo = {}
                        self._frdp_writer = None
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
        send_count = 0
        while True:
            if self.psx_connected and self._lat is not None:
                now = asyncio.get_running_loop().time()
                full = not self._sent_state or (now - last_full_at >= _FULL_SEND_INTERVAL)
                update = self._build_update(full)
                await ws.send_json(update)
                self._record_sent_state(update)
                if full:
                    last_full_at = now
                send_count += 1
                if send_count % 5 == 0:
                    self.psx.send("demand", "LeftPfdAlt")
                n_events = len(update.get("simevents") or [])
                if self.args.show_sent_to_fc:
                    self.logger.info("Sent to FC: %s", json.dumps(update, indent=2))
                elif n_events:
                    self.logger.info("Sent %s update to FC with %d sim event(s)",
                                     "full" if full else "delta", n_events)
                else:
                    self.logger.debug("Sent %s update", "full" if full else "delta")
            await asyncio.sleep(_SEND_INTERVAL)

    async def push_loop_coro(self):
        """Maintain a WebSocket connection to the portal and send position updates."""
        ws_url = self.args.portal_url.rstrip('/') + '/ws/push'
        headers = {"Authorization": f"Bearer {self.args.logon_code}"}
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
                            # Reset per-connection so a stale plan from a
                            # previous portal session isn't assumed linked.
                            self._active_flight_plan_id = None
                            self._active_checklist_items = []
                            while not self._reverse_sync_queue.empty():
                                self._reverse_sync_queue.get_nowait()

                            send_task = asyncio.ensure_future(
                                self._portal_send_loop(ws))
                            drain_task = asyncio.ensure_future(
                                self._drain_ws(ws))
                            reverse_sync_task = asyncio.ensure_future(
                                self._reverse_sync_loop(ws))
                            try:
                                await asyncio.wait(
                                    [send_task, drain_task, reverse_sync_task],
                                    return_when=asyncio.FIRST_COMPLETED)
                                close_code = ws.close_code
                                if close_code == 4001:
                                    self.logger.error(
                                        "Portal rejected logon code — "
                                        "check the code shown on your My sim page")
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
                                reverse_sync_task.cancel()
                                await asyncio.gather(
                                    send_task, drain_task, reverse_sync_task,
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

    async def _drain_ws(self, ws):
        """Handle messages the portal sends back down the push WebSocket.

        Currently just the "flight_plan"/"link_status" pushes from Flight
        Centre (see CLAUDE.md's Flight Centre / frankenrouter integration
        section). Exits when the server closes the connection.
        """
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            try:
                data = json.loads(msg.data)
            except (json.JSONDecodeError, ValueError, TypeError):
                continue
            try:
                await self._handle_portal_message(data)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                self.logger.warning("Error handling portal message: %s", exc)

    async def _handle_portal_message(self, data):
        """Apply one "flight_plan"/"link_status" message from Flight Centre.

        Pushes the resulting FLIGHTINFO to the router mesh over the
        existing FRDP peer connection.
        """
        msg_type = data.get("type")
        if msg_type == "flight_plan":
            self._active_flight_plan_id = data.get("flight_plan_id")
            self._active_checklist_items = data.get("checklist_items") or []
            sent = await self._send_frdp_flightinfo(_flightinfo_from_flight_plan_push(data))
            if sent:
                self.logger.info(
                    "Flight Centre: pushed flight plan %s to router",
                    self._active_flight_plan_id)
            else:
                self.logger.warning(
                    "Flight Centre: received flight plan %s but no FRDP peer "
                    "connection to push it to", self._active_flight_plan_id)
        elif msg_type == "link_status" and not data.get("linked"):
            self._active_flight_plan_id = None
            self._active_checklist_items = []
            await self._send_frdp_flightinfo(_unlinked_flightinfo())

    async def _reverse_sync_loop(self, ws):
        """Send reverse-sync deltas to Flight Centre as soon as they arrive.

        Sends checklist_toggle/scratchpad_update deltas (queued by
        _queue_reverse_sync_deltas as they're received over FRDP),
        independent of the regular telemetry send interval — the
        user-facing requirement is that a checklist toggle goes out
        immediately, not on the next periodic tick.
        """
        while True:
            message = await self._reverse_sync_queue.get()
            await ws.send_json(message)
            self.logger.debug("Sent reverse-sync message to FC: %s", message.get("type"))

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
            '--psx-port-override',
            type=int, action='store', default=None, metavar='PORT',
            help="Override --psx-port with this value (a warning is printed). "
                 "Used by start_scripts to force connecting to the correct router port.",
        )
        parser.add_argument(
            '--portal-url',
            type=str, action='store', default='https://mkro.se/flightcentre',
            help="PSCC Flight Centre portal URL.",
        )
        parser.add_argument(
            '--logon-code',
            type=str, action='store', default=None, dest='logon_code',
            help="Logon code from the 'My sim' page on the portal. "
                 "Saved to frankenpush_cache.json after first use; "
                 "omit this option on subsequent runs to use the cached value.",
        )
        parser.add_argument(
            '--logon-key',
            type=str, action='store', default=None, dest='logon_code',
            help="Deprecated alias for --logon-code.",
        )
        parser.add_argument(
            '--upload-autosave-from',
            type=str, action='store', default=None, metavar='PATH',
            help="Path to the PSX Situations directory. Monitors -Autosaved[A].situ "
                 "and -Autosaved[B].situ and uploads them to Flight Centre when they "
                 "change (PSX saves every ~3.5 minutes).",
        )
        parser.add_argument(
            '--simevents',
            action='store_true',
            help="Forward SIMEVENTS from the router to Flight Centre.",
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
        if self.args.psx_port_override is not None:
            print(f"WARNING: --psx-port-override={self.args.psx_port_override} "
                  f"overrides --psx-port={self.args.psx_port}", file=sys.stderr)
            self.args.psx_port = self.args.psx_port_override

        if self.args.logon_code:
            # Explicitly provided — persist for future runs.
            _save_cached_logon_code(self.args.logon_code)
        else:
            cached = _load_cached_logon_code()
            if cached:
                print(f"Using cached logon code from {_CACHE_FILE.name}.")
                self.args.logon_code = cached
            else:
                print("Enter the logon code shown on the 'My sim' page of the portal.")
                self.args.logon_code = input("Logon code: ").strip()
                _save_cached_logon_code(self.args.logon_code)

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
