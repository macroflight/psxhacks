"""FrankenWeather - Dynamic real-world weather zones for PSX."""
# pylint: disable=invalid-name,duplicate-code,too-many-lines
import argparse
import asyncio
import copy
import inspect
import json
import logging
import math
import os
import pathlib
import random
import re
import sys
import time
import tomllib
import traceback
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Optional

import aiohttp
import requests  # pylint: disable=import-error

try:
    from PIL import Image as _PIL_Image
    _HAS_PIL = True
except ImportError:
    _PIL_Image = None
    _HAS_PIL = False
from pyproj import Geod

import psx
import wind_corridor
from frankenturb import (
    TurbulenceEngine, TurbulenceState, parse_pibahealtas,
    compute_cb_turbulence,
    PirepFetcher, compute_pirep_turbulence,
    CapeFetcher, compute_cape_turbulence,
    GairmetFetcher, compute_gairmet_turbulence,
)
from frankenturb.cb import (
    find_nearest_cb, parse_wx_zone_basic, parse_wx_zone_position, parse_wx_clust,
)
from fw_cb import (
    WX_DEFAULTS as _WX_DEFAULTS,
    cape_to_cb_oktas as _cape_to_cb_oktas,
    cb_base_ft as _cb_base_ft,
    cb_tops_ft as _cb_tops_ft,
    om_cb_fields as _om_cb_fields,
    apply_om_cb as _apply_om_cb,
    apply_fake_cb as _apply_fake_cb,
)
import fw_webui as _fw_webui  # pylint: disable=wrong-import-order


__MYNAME__ = 'frankenweather'
__MY_CLIENT_ID__ = 'FWXR'
__MY_DISPLAY_NAME__ = 'FrankenWeather'
__MY_DESCRIPTION__ = 'Dynamic real-world weather zones for PSX using Open-Meteo'

_OM_URL = "https://api.open-meteo.com/v1/forecast"
_OM_VARS = (
    "temperature_2m,relative_humidity_2m,weather_code,cloud_cover,pressure_msl,"
    "wind_speed_10m,wind_direction_10m,wind_gusts_10m,visibility"
)

# Enroute wind importer: fetch Open-Meteo pressure-level wind/temperature for
# each not-yet-passed route waypoint and inject it into WxCorridorTxt as a
# fresh Format-A corridor, so PSX's enroute wind keeps drifting with reality
# even if the crew never requests a new datalink wind uplink.
_ENROUTE_FETCH_INTERVAL_S = 3600.0     # background refresh cadence ("hourly")
# Target flight levels (ft) for the generated corridor, each mapped to the
# nearest Open-Meteo standard pressure level below. The PSX NG FMC manual
# titles this corridor format "Format A — 6 wind records per leg": PSX's own
# reader expects exactly this many columns, not merely "up to" this many, so
# this tuple's length is load-bearing, not just a starting default.
_ENROUTE_FL_LIST_FT = (10000, 18000, 24000, 30000, 34000, 39000)
# Bounds for a flight-level list to be considered safe to build a Format-A
# corridor with — reusing the flight plan's captured levels is a nicety, but
# generating a structurally valid corridor always takes precedence. Format A
# needs *exactly* len(_ENROUTE_FL_LIST_FT) columns: a captured snapshot
# spanning a sliding multi-level window (e.g. a Format-E flight plan with a
# different 4-level window per climb/cruise/descent segment) unions to some
# other count across all its waypoints, which PSX has rejected/malformed
# (e.g. "below 1000ft minimum, 0ft" from a misparsed header; a 9-column
# corridor built from such a union) — such lists are discarded in favor of
# the default rather than sent as-is.
_FL_LIST_MIN_FT = 1000
_FL_LIST_MAX_FT = 60000
_FL_LIST_LEN = len(_ENROUTE_FL_LIST_FT)


def _valid_fl_list(levels: list) -> bool:
    """Return True if a flight-level list is safe to build a Format-A corridor with."""
    if len(levels) != _FL_LIST_LEN:
        return False
    return all(_FL_LIST_MIN_FT <= fl <= _FL_LIST_MAX_FT for fl in levels)


# Open-Meteo standard pressure levels (hPa) → approximate ISA altitude (ft).
# Used to pick, for each target flight level above, the closest hPa level to
# request temperature/wind for.
_OM_PRESSURE_LEVELS_FT = {
    1000: 364, 975: 1266, 950: 1773, 925: 2299, 900: 2844, 850: 4781,
    800: 6562, 700: 9882, 600: 13801, 500: 18289, 400: 23574, 300: 30065,
    250: 33999, 200: 38662, 150: 44647, 100: 53083, 70: 60663, 50: 68000,
    30: 77000,
}


def _nearest_om_hpa(target_ft: float) -> int:
    """Return the Open-Meteo pressure level (hPa) whose ISA altitude is closest to target_ft."""
    return min(_OM_PRESSURE_LEVELS_FT, key=lambda hpa: abs(_OM_PRESSURE_LEVELS_FT[hpa] - target_ft))


_UCAR_STATIONS_URL = "https://weather.rap.ucar.edu/surface/stations.txt"
_STATIONS_CACHE = os.path.join(os.path.expanduser("~"), ".cache", "frankenweather", "stations.txt")
# os.path.expanduser("~") resolves to the right home dir on both Linux and Windows.
# Only ever loaded if it actually exists (see _load_config_file) — nothing is written
# here unless the user explicitly saves from the web UI.
_DEFAULT_CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".frankenweather.toml")
_VATSIM_ALL_URL = "https://metar.vatsim.net/all"
_VATSIM_CACHE_MAX_S = 1800             # re-fetch VATSIM METARs every 30 minutes
_AIRPORT_SNAP_NM = 25.0               # snap zone to a real airport if within this radius
_REPOSITION_DIST_NM = 500.0           # zone this far away → aircraft was repositioned
_REFRESH_MAX_S = 300                  # always refresh weather after this many seconds
_PUSH_COOLDOWN_S = 5.0                # ignore Wx echo-backs for this long after our write
_MSFS_BRIDGE_TIMEOUT_S = 300.0        # stop using bridge data after this silence period
_NM_TO_M = 1852.0

_HDG_WINDOW_S = 300.0                 # heading-change detection window (5 min)
_MANEUVER_ENTER_DEG = 180.0           # total hdg change to enter maneuvering mode
_MANEUVER_EXIT_DEG = 60.0             # total hdg change to exit maneuvering mode

# ---------------------------------------------------------------------------
# Radar (RainViewer) and lightning (Blitzortung) constants
# ---------------------------------------------------------------------------

_RV_API = "https://api.rainviewer.com/public/weather-maps.json"
_RV_TILE = "https://tilecache.rainviewer.com"
_RV_ZOOM = 6           # zoom 6 → ~22 km/pixel at equator, ~5°×5° per tile
_RV_TILE_PX = 256
_RV_ECHO_RADIUS = 3    # pixel radius to sample around target point
_RV_TIMEOUT = aiohttp.ClientTimeout(total=15)
_RV_DENSITY_SCAN_NM = 150.0   # radius of local echo scan around aircraft
_RV_DENSITY_GRID = 5          # NxN sample grid (5×5 = 25 points)
_RV_DENSITY_THRESHOLD = 0.25  # echo≥2 fraction triggering full CB squeeze

_BZ_URL = "https://data.blitzortung.org/Data_1/strikes"
_BZ_LOOKBACK_MIN = 20  # fetch this many recent minute-files of strike data
_BZ_RADIUS_NM = 80.0   # strike within this radius → lightning signal for CB gate
_BZ_CACHE_S = 240.0    # re-fetch Blitzortung no more often than this

# ---------------------------------------------------------------------------
# Coordinate parsing (UCAR stations.txt format)
# ---------------------------------------------------------------------------


def _parse_ucar_lat(s: str) -> Optional[float]:
    s = s.strip()
    if len(s) < 4:
        return None
    hem = s[-1].upper()
    if hem not in ('N', 'S'):
        return None
    try:
        parts = s[:-1].split()
        val = int(parts[0]) + float(parts[1]) / 60.0
        return -val if hem == 'S' else val
    except (ValueError, IndexError):
        return None


def _parse_ucar_lon(s: str) -> Optional[float]:
    s = s.strip()
    if len(s) < 5:
        return None
    hem = s[-1].upper()
    if hem not in ('E', 'W'):
        return None
    try:
        parts = s[:-1].split()
        val = int(parts[0]) + float(parts[1]) / 60.0
        return -val if hem == 'W' else val
    except (ValueError, IndexError):
        return None


def _parse_airports_lines(lines) -> dict:
    airports: dict = {}
    for line in lines:
        if not line or line[0] == '!' or len(line) < 55:
            continue
        icao = line[20:24]
        if not (icao[:1].isalpha() and icao.isalnum() and icao != 'ICAO'):
            continue
        lat = _parse_ucar_lat(line[39:45])
        lon = _parse_ucar_lon(line[47:54])
        if lat is None or lon is None:
            continue
        airports[icao] = (lat, lon)
    return airports


def load_airports(path: str) -> dict:
    """Load ICAO → (lat_deg, lon_deg) from a local UCAR stations.txt file."""
    with open(path, encoding="latin-1") as fh:
        return _parse_airports_lines(fh)


def download_airports(url: str) -> dict:
    """Download and parse UCAR stations.txt from url."""
    with urllib.request.urlopen(url, timeout=30) as resp:
        text = resp.read().decode('latin-1')
    return _parse_airports_lines(text.splitlines())


def get_airports(url: str, cache_path: str) -> tuple:
    """Return airports dict loaded from cache, or downloaded and cached if absent.

    Returns ``(airports, source_description)`` where source_description is a
    human-readable string suitable for logging.
    """
    if os.path.exists(cache_path):
        return load_airports(cache_path), f"cache ({cache_path})"
    with urllib.request.urlopen(url, timeout=30) as resp:
        text = resp.read().decode('latin-1')
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w", encoding="latin-1") as fh:
        fh.write(text)
    return _parse_airports_lines(text.splitlines()), url


# ---------------------------------------------------------------------------
# FMC route waypoint parsing (mirrors frankenpush.py's _parse_route_waypoints
# and psccfc/connector/frdp.py — kept in sync by hand)
# ---------------------------------------------------------------------------

_FMC_WAYPOINT_PREFIX_LEN = 10
_FMC_WAYPOINT_SENTINEL_LATLON = "9.0/9.0"

# Field 1 of an FmcRte entry tags the waypoint with whatever it's "on": an
# airway/track identifier for a plain enroute leg (e.g. "N261B", "P2", or a
# North Atlantic Track "NATW"), or a SID/STAR/approach construct name for a
# terminal-procedure leg (e.g. "CELTK7", "SIRI1H", "APPR_TRANS", "ILS_27L",
# "MISSED_APPR"). Airway/track identifiers always lead with 1-2 letters then
# digits (NAT tracks are the sole all-letter exception); procedure names lead
# with a longer alphabetic fix name before any digit — that's the reliable
# split, since plenty of legitimate enroute fixes (e.g. NAT-track waypoints)
# would otherwise look identical to STAR fixes by name alone.
_AIRWAY_TAG_RE = re.compile(r'[A-Z]{1,2}\d{1,4}[A-Z]?')
_NAT_TRACK_TAG_RE = re.compile(r'NAT[A-Z]')


def _is_airway_or_track_tag(tag: str) -> bool:
    """Return True if an FmcRte field-1 tag looks like an airway/track id, not a procedure name."""
    return bool(_AIRWAY_TAG_RE.fullmatch(tag) or _NAT_TRACK_TAG_RE.fullmatch(tag))


def _parse_fmc_route_waypoints(route: str) -> list:
    """Extract [(name, lat_deg, lon_deg), ...] entries from a PSX FmcRte string.

    Waypoints tagged with a SID/STAR/approach procedure name (see
    _is_airway_or_track_tag) are excluded — this also transparently covers
    every "("-prefixed pseudo-waypoint (top of descent, intercepts,
    runway-relative markers, ...), since those are always procedure-tagged,
    and real dispatch wind corridors never cover SID/STAR/approach construct
    fixes in the first place.
    """
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
        if fields[1] and not _is_airway_or_track_tag(fields[1]):
            continue  # part of a SID/STAR/approach procedure — skip
        name = fields[0][_FMC_WAYPOINT_PREFIX_LEN:].strip().upper()
        latlon = fields[3]
        if not name or latlon == _FMC_WAYPOINT_SENTINEL_LATLON:
            continue
        lat_str, _sep2, lon_str = latlon.partition("/")
        try:
            lat_deg = math.degrees(float(lat_str))
            lon_deg = math.degrees(float(lon_str))
        except ValueError:
            continue
        waypoints.append((name, lat_deg, lon_deg))
    return waypoints


def _is_name_suffix(candidate: tuple, full: list) -> bool:
    """Return True if `candidate` is a non-empty tail-match of the `full` name sequence."""
    if not candidate or len(candidate) > len(full):
        return False
    return full[len(full) - len(candidate):] == list(candidate)


# ---------------------------------------------------------------------------
# PSX weather field builders
# ---------------------------------------------------------------------------

def _cloud_pct_to_oktas(pct: float) -> int:
    """Convert cloud cover percentage to oktas (0–8)."""
    if pct < 6.25:
        return 0  # SKC
    if pct < 25.0:
        return 2  # FEW
    if pct < 50.0:
        return 4  # SCT
    if pct < 87.5:
        return 6  # BKN
    return 8      # OVC


def _hpa_to_psx_qnh(hpa: float) -> int:
    """Convert hPa to inHg×100 (PSX QNH format)."""
    return int(round(hpa * 2.953))


def _dewpoint(temp_c: float, rh_pct: float) -> float:
    """Magnus-formula dew point from temperature (°C) and relative humidity (%)."""
    a, b = 17.625, 243.04
    gamma = math.log(max(rh_pct, 0.01) / 100.0) + a * temp_c / (b + temp_c)
    return b * gamma / (a - gamma)


def _fmt_temp(t: float) -> str:
    t_int = round(t)
    return f"M{-t_int:02d}" if t_int < 0 else f"{t_int:02d}"


_WMO_METAR_WX: dict = {
    45: "FG", 48: "FZFG",
    51: "-DZ", 53: "-DZ", 55: "DZ",
    56: "-FZDZ", 57: "FZDZ",
    61: "-RA", 63: "RA", 65: "+RA",
    66: "-FZRA", 67: "FZRA",
    71: "-SN", 73: "SN", 75: "+SN",
    77: "SG",
    80: "-SHRA", 81: "SHRA", 82: "+SHRA",
    85: "SHSN", 86: "+SHSN",
    95: "TS", 96: "TSRA", 99: "+TSRA",
}


def _cloud_base_ft(wmo_code: int) -> int:
    """Estimate cloud base in feet from WMO weather code."""
    if wmo_code in (45, 48):
        return 100
    if wmo_code in (51, 53, 55, 56, 57):
        return 500
    if wmo_code in (61, 63, 65, 66, 67):
        return 1000
    if wmo_code in (71, 73, 75, 77):
        return 800
    if wmo_code in (80, 81, 82, 85, 86, 95, 96, 99):
        return 1500
    return 2000


# Reverse map: CB oktas → representative CAPE (J/kg), used to estimate
# regional convective intensity from already-computed zone weather strings.
_OKTAS_TO_CAPE = {0: 0.0, 2: 300.0, 4: 1000.0, 6: 2250.0, 8: 4000.0}


def _metar_wind_str(wind_dir: int, wind_spd: int, wind_gust: int) -> str:
    """Format a METAR wind group string."""
    if wind_spd == 0:
        return "00000KT"
    gust = f"G{wind_gust:02d}" if wind_gust > wind_spd + 10 else ""
    return f"{wind_dir:03d}{wind_spd:02d}{gust}KT"


def _metar_sky_str(cloud_pct: float, wmo_code: int) -> str:
    """Format a METAR sky condition group string."""
    oktas = _cloud_pct_to_oktas(cloud_pct)
    base_str = f"{_cloud_base_ft(wmo_code) // 100:03d}"
    if oktas == 0:
        return "SKC"
    if oktas <= 2:
        return f"FEW{base_str}"
    if oktas <= 4:
        return f"SCT{base_str}"
    if oktas <= 6:
        return f"BKN{base_str}"
    return f"OVC{base_str}"


def _gen_metar(icao: str, om: dict, now: datetime) -> str:
    """Generate a METAR-format string from Open-Meteo current weather."""
    cur = om.get("current", {})
    wind_dir = int(round(float(cur.get("wind_direction_10m", 0)) / 10.0)) * 10
    wind_spd = int(round(float(cur.get("wind_speed_10m", 0))))
    wind_gust = int(round(float(cur.get("wind_gusts_10m", 0))))
    vis_m = float(cur.get("visibility", 10000))
    wmo_code = int(cur.get("weather_code", 0))
    cloud_pct = float(cur.get("cloud_cover", 0))
    temp_c = float(cur.get("temperature_2m", 15))
    rh_pct = float(cur.get("relative_humidity_2m", 60))
    qnh_hpa = float(cur.get("pressure_msl", 1013.25))
    dp_c = _dewpoint(temp_c, rh_pct)

    parts = [icao, now.strftime('%d%H%MZ'),
             _metar_wind_str(wind_dir, wind_spd, wind_gust),
             "9999" if vis_m >= 10000 else f"{int(min(vis_m, 9000)):04d}"]
    parts += ([_WMO_METAR_WX[wmo_code]] if wmo_code in _WMO_METAR_WX else [])
    parts += [_metar_sky_str(cloud_pct, wmo_code),
              f"{_fmt_temp(temp_c)}/{_fmt_temp(dp_c)}", f"Q{int(round(qnh_hpa)):04d}"]
    return ' '.join(parts)


_METAR_WIND_RE = re.compile(r'(VRB|\d{3})(\d{2,3})(?:G(\d{2,3}))?KT')
_METAR_SKY_RE = re.compile(r'^(FEW|SCT|BKN|OVC)(\d{3})(CB|TCU)?')
_METAR_TEMP_RE = re.compile(r'^(M?\d{1,2})/(M?\d{1,2})$')
_METAR_QNH_RE = re.compile(r'^Q(\d{4})$')
_METAR_ALT_RE = re.compile(r'^A(\d{4})$')


def _update_metar_qnh(metar: str, qnh_hpa: float) -> str:
    """Replace the Q-format QNH token in a METAR string with a new value."""
    return re.sub(r'\bQ\d{4}\b', f"Q{int(round(qnh_hpa)):04d}", metar)


_WIND_UPDATE_INTERVAL_S = 60.0

_SIGMET_COORD_RE = re.compile(r'([NS])(\d{2})(\d{2})\s+([EW])(\d{3})(\d{2})')
_SIGMET_ALT_RE = re.compile(
    r'(?:TOP\s+FL(\d{3}))'
    r'|(?:FL(\d{3})/(?:FL)?(\d{3}))'
    r'|(?:SFC/FL(\d{3}))'
    r'|(?:(\d+)FT/FL(\d{3}))')


def _sigmet_top_ft(text: str) -> int:
    """Return the upper altitude limit in feet from SIGMET text, or 35000 if not found."""
    m = _SIGMET_ALT_RE.search(text)
    if not m:
        return 35000
    if m.group(1):      # TOP FL260
        return int(m.group(1)) * 100
    if m.group(2):      # FL140/180
        return int(m.group(3)) * 100
    if m.group(4):      # SFC/FL100
        return int(m.group(4)) * 100
    if m.group(5):      # 3000FT/FL240
        return int(m.group(6)) * 100
    return 35000


# ---------------------------------------------------------------------------
# Radar helpers (RainViewer colour scheme 2 — logic shared with fw_scanner.py)
# ---------------------------------------------------------------------------

def _rv_pixel_echo_strength(r: int, g: int, b: int, a: int) -> int:
    """Map an RGBA pixel from RainViewer scheme 2 to echo strength 0–3."""
    if a < 30:
        return 0
    if b > max(r, g):
        return 1   # blue-dominant: light rain (15-30 dBZ)
    if g >= r:
        return 2   # green-dominant: moderate (35-50 dBZ)
    return 3       # warm (yellow/orange/red): heavy / CB core (50+ dBZ)


def _rv_tile_xy(lat: float, lon: float, zoom: int) -> tuple:
    """Convert lat/lon to slippy-map tile (tx, ty) at given zoom."""
    n = 2 ** zoom
    tx = int((lon + 180) / 360 * n)
    lat_r = math.radians(lat)
    ty = int((1 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2 * n)
    return tx, ty


def _rv_pixel_in_tile(lat: float, lon: float, zoom: int) -> tuple:
    """Pixel (x, y) within the tile that contains this lat/lon."""
    n = 2 ** zoom
    lat_r = math.radians(lat)
    px = int(((lon + 180) / 360 * n * _RV_TILE_PX) % _RV_TILE_PX)
    log_term = math.log(math.tan(lat_r) + 1 / math.cos(lat_r))
    py = int(((1 - log_term / math.pi) / 2 * n * _RV_TILE_PX) % _RV_TILE_PX)
    return px, py


def _rv_tile_echo(img: object, px: int, py: int) -> int:
    """Maximum echo strength in a small box around pixel (px, py) in a PIL image."""
    w, h = img.size
    best = 0
    for dy in range(-_RV_ECHO_RADIUS, _RV_ECHO_RADIUS + 1):
        for dx in range(-_RV_ECHO_RADIUS, _RV_ECHO_RADIUS + 1):
            x, y = px + dx, py + dy
            if 0 <= x < w and 0 <= y < h:
                best = max(best, _rv_pixel_echo_strength(*img.getpixel((x, y))))
    return best


def _point_in_polygon(lat: float, lon: float, polygon: list) -> bool:
    """Ray-casting point-in-polygon test for (lat, lon) vertex lists."""
    n = len(polygon)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        yi, xi = polygon[i]
        yj, xj = polygon[j]
        if ((yi > lat) != (yj > lat)) and \
                (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _parse_ts_sigmets(raw: str) -> list:
    """Parse WxSigmet string into a list of active TS SIGMET dicts.

    Each dict has 'polygon' (list of (lat, lon) tuples) and 'top_ft' (int).
    Only TS hazard entries with at least 3 polygon points are returned.
    """
    result = []
    lines = raw.split('^')
    idx = 0
    while idx < len(lines):
        if lines[idx].strip() != 'Hazard: TS':
            idx += 1
            continue
        idx += 1
        entry_lines = []
        while idx < len(lines):
            ln = lines[idx].strip()
            if ln.startswith('---') or ln.startswith('Hazard:'):
                break
            entry_lines.append(ln)
            idx += 1
        text = ' '.join(entry_lines)
        wi_pos = text.find(' WI ')
        if wi_pos < 0:
            continue
        coords = [
            ((-1 if m.group(1) == 'S' else 1) * (int(m.group(2)) + int(m.group(3)) / 60.0),
             (-1 if m.group(4) == 'W' else 1) * (int(m.group(5)) + int(m.group(6)) / 60.0))
            for m in _SIGMET_COORD_RE.finditer(text, wi_pos)
        ]
        if len(coords) < 3:
            continue
        result.append({'polygon': coords, 'top_ft': max(_sigmet_top_ft(text[wi_pos:]), 25000)})
    return result


_METAR_GR_RE = re.compile(r'^[+-]?(SH|TS|VC|FZ|BL|DR|MI|PR)?(GR|GS)$')
_METAR_SH_RE = re.compile(r'^[+-]?SH[A-Z]{2,4}$')
_METAR_FC_RE = re.compile(r'^[+-]?FC$')
_METAR_VIS4_RE = re.compile(r'^\d{4}$')
_METAR_VISSM_RE = re.compile(r'^(\d+(?:/\d+)?)SM$')


def _metar_vis(token: str) -> 'int | None':
    """Parse a METAR visibility token; return metres or None if not a vis token."""
    if _METAR_VIS4_RE.match(token):
        return int(token)
    m = _METAR_VISSM_RE.match(token)
    if m:
        p = m.group(1).split('/')
        sm = float(p[0]) / float(p[1]) if len(p) == 2 else float(p[0])
        return int(sm * 1609.34)
    return None


def _metar_parse_temp(s: str) -> int:
    """Parse a METAR temperature field (possibly M-prefixed negative)."""
    return -int(s[1:]) if s.startswith('M') else int(s)


def _metar_wx_token(token: str, out: dict) -> None:
    """Update convective flags in parsed METAR dict from a present-weather token."""
    if token == 'TSNO':
        pass  # "Thunderstorm information not available" — sensor broken, not a CB indicator
    elif 'TS' in token:
        if token.startswith('+'):
            out['ts_oktas'] = max(out['ts_oktas'], 8)
        elif token.startswith('-') or 'VC' in token:
            out['ts_oktas'] = max(out['ts_oktas'], 2)
        else:
            out['ts_oktas'] = max(out['ts_oktas'], 4)
    elif _METAR_GR_RE.match(token):
        out['ts_oktas'] = max(out['ts_oktas'], 4)
    elif _METAR_FC_RE.match(token):
        out['ts_oktas'] = max(out['ts_oktas'], 8)
    elif _METAR_SH_RE.match(token):
        out['showers'] = True


def _parse_metar(raw: str) -> dict:  # pylint: disable=too-many-branches
    """Parse a raw METAR string into a dict of weather fields."""
    out: dict = {
        'wind_dir': 0, 'wind_var': False, 'wind_spd': 0, 'wind_gust': 0,
        'vis_m': 10000, 'sky': [], 'temp_c': 15, 'dp_c': 10, 'qnh_hpa': 1013.0,
        'ts_oktas': 0, 'showers': False,
    }
    for token in raw.split():
        m = _METAR_WIND_RE.match(token)
        if m:
            out['wind_var'] = m.group(1) == 'VRB'
            out['wind_dir'] = 0 if out['wind_var'] else int(m.group(1))
            out['wind_spd'] = int(m.group(2))
            out['wind_gust'] = int(m.group(3)) if m.group(3) else 0
            continue
        vis = _metar_vis(token)
        if vis is not None:
            out['vis_m'] = vis
            continue
        if token in ('SKC', 'CLR', 'NSC'):
            out['sky'] = []
            continue
        if token == 'CAVOK':
            out['vis_m'] = 10000
            out['sky'] = []
            continue
        m = _METAR_SKY_RE.match(token)
        if m:
            oktas = {'FEW': 2, 'SCT': 4, 'BKN': 6, 'OVC': 8}[m.group(1)]
            out['sky'].append((oktas, int(m.group(2)) * 100))
            cloud_type = m.group(3)
            if cloud_type == 'CB':
                out['ts_oktas'] = max(out['ts_oktas'], oktas)
            elif cloud_type == 'TCU':
                out['showers'] = True
            continue
        m = _METAR_TEMP_RE.match(token)
        if m:
            out['temp_c'] = _metar_parse_temp(m.group(1))
            out['dp_c'] = _metar_parse_temp(m.group(2))
            continue
        m = _METAR_QNH_RE.match(token)
        if m:
            out['qnh_hpa'] = float(m.group(1))
            continue
        m = _METAR_ALT_RE.match(token)
        if m:
            out['qnh_hpa'] = round(int(m.group(1)) / 2.953)
            continue
        _metar_wx_token(token, out)
    # LTG (lightning) in remarks; DSNT = distant (~VCTS), otherwise overhead
    if 'LTG' in raw:
        if 'LTG DSNT' in raw:
            out['ts_oktas'] = max(out['ts_oktas'], 2)
        else:
            out['ts_oktas'] = max(out['ts_oktas'], 4)
    return out


def metar_to_wx_string(parsed: dict) -> str:
    """Convert a parsed METAR dict to a PSX Wx semicolon string."""
    data = list(_WX_DEFAULTS)

    best_cov, best_base = 0, 2000
    for oktas, base_ft in parsed['sky']:
        if oktas > best_cov or (oktas == best_cov and base_ft < best_base):
            best_cov, best_base = oktas, base_ft
    if best_cov > 0:
        data[3] = str(best_cov)
        data[4] = str(best_base + 1500)
        data[5] = str(best_base)

    ts_oktas = parsed.get('ts_oktas', 0)
    if ts_oktas > 0:
        cb_base = _cb_base_ft(parsed['temp_c'], parsed['dp_c'])
        if ts_oktas >= 8:
            cb_tops = 55000
        elif ts_oktas >= 4:
            cb_tops = 40000
        else:
            cb_tops = 30000
        data[9] = str(ts_oktas)
        data[10] = str(cb_tops)
        data[11] = str(cb_base)

    data[18] = f"000{parsed['wind_dir']:03d}{parsed['wind_spd']:02d}"
    data[19] = str(max(parsed['wind_gust'], parsed['wind_spd']))
    data[20] = str(max(min(int(parsed['vis_m']), 9999), 100))
    data[22] = str(int(round(parsed['temp_c'])))
    data[23] = str(_hpa_to_psx_qnh(parsed['qnh_hpa']))

    return ";".join(data)


def om_to_wx_string(om: dict, radar_echo: int = 0,  # pylint: disable=too-many-locals
                    lightning: bool = False) -> str:
    """Convert Open-Meteo current-weather dict to PSX Wx semicolon string."""
    data = list(_WX_DEFAULTS)
    cur = om.get("current", {})

    wind_dir = int(round(float(cur.get("wind_direction_10m", 0)) / 10.0)) * 10
    wind_spd = int(round(float(cur.get("wind_speed_10m", 0))))
    wind_gust = int(round(float(cur.get("wind_gusts_10m", 0))))
    vis_m = int(min(float(cur.get("visibility", 9999)), 9999))
    wmo_code = int(cur.get("weather_code", 0))
    cloud_pct = float(cur.get("cloud_cover", 0))
    temp_c = int(round(float(cur.get("temperature_2m", 15))))
    qnh_hpa = float(cur.get("pressure_msl", 1013.25))

    cloud_oktas = _cloud_pct_to_oktas(cloud_pct)
    if cloud_oktas > 0:
        cloud_base_ft = _cloud_base_ft(wmo_code)
        cloud_top_ft = cloud_base_ft + 1500
        data[3] = str(cloud_oktas)
        data[4] = str(cloud_top_ft)
        data[5] = str(cloud_base_ft)

    cb_oktas, cb_tops, cb_base = _om_cb_fields(om, radar_echo=radar_echo, lightning=lightning)
    if cb_oktas > 0:
        data[9] = str(cb_oktas)
        data[10] = str(cb_tops)
        data[11] = str(cb_base)

    data[18] = f"000{wind_dir:03d}{wind_spd:02d}"
    data[19] = str(max(wind_gust, wind_spd))
    data[20] = str(max(vis_m, 100))
    data[22] = str(temp_c)
    data[23] = str(_hpa_to_psx_qnh(qnh_hpa))

    return ";".join(data)


def build_wxmode_string(lat_deg: float, lon_deg: float,
                        elevation_m: float, month: int, icao: str) -> str:
    """Build PSX WxMode string: lat_rad;lon_rad;320;elev_m;MM;ICAOrwy."""
    lat_rad = math.radians(lat_deg)
    lon_rad = math.radians(lon_deg)
    return f"{lat_rad:.8f};{lon_rad:.8f};320;{int(round(elevation_m))};{month:02d};{icao}00n"


def _parse_cb_arg(s: str) -> tuple:
    """Parse --fake-cb=O:B:T into (oktas, base_ft, top_ft)."""
    parts = s.split(':')
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("expected O:B:T (e.g. 6:3000:45000)")
    try:
        oktas, base_ft, top_ft = int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError as exc:
        raise argparse.ArgumentTypeError("all three values must be integers") from exc
    if not 0 <= oktas <= 8:
        raise argparse.ArgumentTypeError("coverage (O) must be 0–8 oktas")
    if base_ft >= top_ft:
        raise argparse.ArgumentTypeError("base (B) must be below top (T)")
    return (oktas, base_ft, top_ft)


def _parse_cape_squeeze(s: str) -> tuple:
    """Parse --cape-squeeze=CAPE:MIN_FWD into (cape_threshold_jkg, min_fwd_nm)."""
    parts = s.split(':')
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("expected CAPE:MIN_FWD (e.g. 2000:50)")
    try:
        cape, min_fwd = float(parts[0]), float(parts[1])
    except ValueError as exc:
        raise argparse.ArgumentTypeError("values must be numbers") from exc
    if cape <= 0:
        raise argparse.ArgumentTypeError("CAPE threshold must be positive")
    if min_fwd < 0:
        raise argparse.ArgumentTypeError("MIN_FWD must be non-negative")
    return (cape, min_fwd)


def _parse_nm_range(s: str) -> tuple:
    """Parse a MIN,MAX nm argument into a (min_nm, max_nm) float tuple."""
    parts = s.split(',')
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("expected MIN,MAX (e.g. 150,250)")
    try:
        lo, hi = float(parts[0]), float(parts[1])
    except ValueError as exc:
        raise argparse.ArgumentTypeError("values must be numbers") from exc
    if lo < 0 or hi < 0:
        raise argparse.ArgumentTypeError("values must be non-negative")
    if lo > hi:
        raise argparse.ArgumentTypeError("MIN must be no greater than MAX")
    return (lo, hi)


def _isnan(v):
    """Return True if v is float NaN."""
    try:
        return v != v  # pylint: disable=comparison-with-itself
    except TypeError:
        return False


def _parse_psx_wind(wx_str):
    """Parse surface wind direction and speed from a PSX Wx* weather string.

    The 19th field encodes VVVDDSS (VVV=variability, DD=direction/10, SS=speed kt).
    Returns (direction_deg, speed_kt) or None.
    """
    parts = wx_str.strip().split(';')
    if len(parts) < 19:
        return None
    wind_field = parts[18]
    if len(wind_field) < 7:
        return None
    try:
        dir_deg = (int(wind_field[3:5]) * 10) % 360
        speed_kt = int(wind_field[5:7])
        return dir_deg, speed_kt
    except (ValueError, IndexError):
        return None


def _intensity_label(intensity):
    """Map 0–1 intensity to a human-readable severity label."""
    if intensity < 0.10:
        return "none"
    if intensity < 0.25:
        return "light"
    if intensity < 0.50:
        return "moderate"
    if intensity < 0.75:
        return "severe"
    return "extreme"


# WxBurst channel offsets
_BURST_SINK = 0
_BURST_BANK = 100
_BURST_YAW = 200
_BURST_SPD = 300
_BURST_GUST = 400

_TURB_TYPES = ('wave', 'rotor', 'mechanical', 'shear', 'cb', 'pirep', 'cape', 'gairmet')

# ---------------------------------------------------------------------------
# --config-file (TOML): every setting the web GUI can change, in one place.
# Each tuple is (toml_key, attribute_name, cast); manual weather params map
# 1:1 by key into self._manual_wx_params instead, since that's already a dict.
# ---------------------------------------------------------------------------
_CONFIG_MSFS_FIELDS = (
    ("in_cloud_sync", "_msfs_in_cloud_sync", bool),
    ("qnh_check", "_msfs_qnh_check", str),
    ("wind_sync", "_msfs_wind_sync", bool),
)
_CONFIG_ENROUTE_WIND_FIELDS = (
    ("enabled", "_enroute_wind_enabled", bool),
    ("deviation", "_enroute_wind_deviation", int),
)
_CONFIG_MANUAL_WX_FIELDS = (
    "hi_oktas", "hi_top", "hi_base", "lo_oktas", "lo_top", "lo_base",
    "cb_oktas", "cb_top", "cb_base", "turb_severity", "turb_top", "turb_base",
    "mb_mode", "mb_chance", "mb_outflow", "inv_on", "inv_top", "inv_tmp",
    "wind_dir", "wind_spd", "wind_gust", "wind_var", "precip", "vis_m",
    "surf_temp", "qnh_hpa",
)
_CONFIG_TURB_FIELDS = (
    ("enabled", "_turb_enabled", bool),
    ("manual_turb_enabled", "_turb_manual_turb_enabled", bool),
    ("manual_turb_kind", "_turb_manual_turb_kind", str),
    ("manual_turb_intensity", "_turb_manual_turb_intensity", float),
    ("intensity_bias", "_turb_intensity_bias", int),
    ("lateral_size_bias", "_turb_lateral_size_bias", int),
    ("wind_mode", "_turb_wind_mode", str),
    ("manual_wind_dir", "_turb_manual_wind_dir", int),
    ("manual_wind_spd", "_turb_manual_wind_spd", int),
    ("msfs_turb_magnitude", "_turb_msfs_magnitude", int),
)


def _toml_format_value(value) -> str:
    """Format a scalar as a TOML value literal (bool/int/float/str only)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'


def _dict_to_toml(config: dict) -> str:
    """Serialize a {section: {key: scalar|dict}} dict to TOML text.

    Purpose-built for frankenweather's own config schema (flat sections with
    at most one level of sub-tables, scalar values only) — not a general TOML
    writer. There's no TOML-writing library in this project's dependencies
    (only stdlib tomllib, which is read-only), and the schema is simple
    enough that a small hand-written serializer is clearer than adding one.
    """
    lines = []

    def emit_table(path: list, table: dict) -> None:
        scalars = {k: v for k, v in table.items() if not isinstance(v, dict)}
        subtables = {k: v for k, v in table.items() if isinstance(v, dict)}
        lines.append(f"[{'.'.join(path)}]")
        for key, value in scalars.items():
            lines.append(f"{key} = {_toml_format_value(value)}")
        lines.append("")
        for key, value in subtables.items():
            emit_table(path + [key], value)

    for section, table in config.items():
        emit_table([section], table)
    return "\n".join(lines).rstrip("\n") + "\n"


def _sign(v):
    """Return +1 or -1 from a float."""
    return 1 if v >= 0.0 else -1


def _pick_burst(state, intensity):
    """Choose (base_offset, direction, label) for one WxBurst event."""
    r = random.choice
    if state.kind == 'wave':
        vert_dir = _sign(state.vertical) if not _isnan(state.vertical) else r([-1, 1])
        roll_dir = _sign(state.roll) if not _isnan(state.roll) else r([-1, 1])
        spd_dir = _sign(state.gust) if not _isnan(state.gust) else r([-1, 1])
        if intensity < 0.25:
            return r([(_BURST_SPD, spd_dir, 'spd'), (_BURST_GUST, spd_dir, 'gust')])
        if intensity < 0.50:
            return r([
                (_BURST_SINK, vert_dir, 'sink'),
                (_BURST_SPD, spd_dir, 'spd'),
                (_BURST_GUST, spd_dir, 'gust'),
            ])
        return r([
            (_BURST_SINK, vert_dir, 'sink'),
            (_BURST_BANK, roll_dir, 'bank'),
            (_BURST_SPD, spd_dir, 'spd'),
        ])
    if state.kind == 'rotor':
        return r([
            (_BURST_SINK, r([-1, 1]), 'sink'),
            (_BURST_BANK, r([-1, 1]), 'bank'),
            (_BURST_BANK, r([-1, 1]), 'bank'),
            (_BURST_YAW, r([-1, 1]), 'yaw'),
        ])
    if state.kind == 'shear':
        return r([
            (_BURST_SINK, r([-1, 1]), 'sink'),
            (_BURST_BANK, r([-1, 1]), 'bank'),
        ])
    # mechanical / cb / pirep / cape / gairmet — broad random mix
    return r([
        (_BURST_SINK, r([-1, 1]), 'sink'),
        (_BURST_BANK, r([-1, 1]), 'bank'),
        (_BURST_YAW, r([-1, 1]), 'yaw'),
        (_BURST_SPD, r([-1, 1]), 'spd'),
    ])


# ---------------------------------------------------------------------------
# Standalone web UI context (adapts Script to fw_webui protocol)
# ---------------------------------------------------------------------------

class StandaloneFWContext:
    """Adapts a Script instance to the fw_webui context protocol."""

    def __init__(self, fw, color_scheme='dark'):
        """Store frankenweather instance and color scheme."""
        self._fw = fw
        self.color_scheme = color_scheme

    @property
    def fw_state(self):
        """Return the last-broadcast FrankenWeather STATE dict."""
        return self._fw._web_state  # pylint: disable=protected-access

    @property
    def fw_turbstate(self):
        """Return the last-broadcast TURBSTATE dict."""
        return self._fw._web_turbstate  # pylint: disable=protected-access

    @property
    def fw_state_received_at(self):
        """Return epoch of last STATE broadcast."""
        return self._fw._web_state_received_at  # pylint: disable=protected-access

    @property
    def fw_turbstate_received_at(self):
        """Return epoch of last TURBSTATE broadcast."""
        return self._fw._web_turbstate_received_at  # pylint: disable=protected-access

    @property
    def fw_windstate(self):
        """Return the last-broadcast WINDSTATE dict."""
        return self._fw._web_windstate  # pylint: disable=protected-access

    @property
    def fw_windstate_received_at(self):
        """Return epoch of last WINDSTATE broadcast."""
        return self._fw._web_windstate_received_at  # pylint: disable=protected-access

    def cache_get(self, name):
        """Return a PSX-variable-like value from local frankenweather state."""
        return self._fw._web_cache_get(name)  # pylint: disable=protected-access

    async def send_manualwx_cmd(self, cmd):
        """Apply a manual wx command directly to the running frankenweather instance."""
        self._fw._handle_manual_wx_command(json.dumps(cmd))  # pylint: disable=protected-access

    async def send_turb_cmd(self, cmd):
        """Apply a turbulence command directly to the running frankenweather instance."""
        self._fw._handle_turb_command(json.dumps(cmd))  # pylint: disable=protected-access

    async def send_mode_cmd(self, mode):
        """Apply a mode change directly to the running frankenweather instance."""
        self._fw._handle_fw_command(json.dumps({"mode": mode}))  # pylint: disable=protected-access
        await asyncio.sleep(3)

    async def send_fw_settings_cmd(self, cmd):
        """Apply MSFS settings toggles directly to the running frankenweather instance."""
        self._fw._handle_fw_command(json.dumps(cmd))  # pylint: disable=protected-access


# ---------------------------------------------------------------------------
# Main script class
# ---------------------------------------------------------------------------

class Script:  # pylint: disable=too-many-instance-attributes
    """FrankenWeather script."""

    def __init__(self):  # pylint: disable=too-many-statements
        """Initialise script state."""
        self.args = None
        self.taskgroup = None
        self.tasks: set = set()
        self.logger = None
        self.psx = None
        self.psx_connected = False
        self.psx_paused = False

        self.airports: dict = {}
        self.geod = Geod(ellps="WGS84")

        # Aircraft state
        self.ac_lat: Optional[float] = None
        self.ac_lon: Optional[float] = None
        self.ac_hdg: Optional[float] = None
        self.ac_alt_ft: Optional[float] = None
        self.ac_tas_kt: Optional[float] = None

        # VATSIM METAR cache: ICAO → raw METAR string
        self.vatsim_cache: dict = {}
        self.vatsim_cache_time = 0.0

        # Our current desired zone values (zone_num → string)
        self.zone_wx: dict = {}
        self.zone_mode: dict = {}
        self.zone_is_metar: dict = {}
        self.last_write_time = 0.0

        # Fixed zone positions: zone_num (1-7) → (lat, lon, icao)
        self.zone_positions: dict = {}
        self.zone_relocated_time: dict = {}  # zone_num → last relocation timestamp

        # FMC route state — dep/dst airports get a dedicated zone when within range
        self.fmc_dep_icao: Optional[str] = None
        self.fmc_dst_icao: Optional[str] = None
        self.fmc_changed_event: asyncio.Event = asyncio.Event()

        # Enroute wind importer (Open-Meteo-driven periodic WxCorridor refresh)
        # route_waypoints mirrors PSX's live (front-trimmed) FmcRte list;
        # _enroute_waypoints is our own persistent, index-stable superset for
        # the whole flight (see _update_fmc_route_waypoints).
        self.route_waypoints: list = []           # [(name, lat_deg, lon_deg), ...] in route order
        self._enroute_waypoints: list = []        # persistent per-flight superset, same shape
        self._enroute_wind_enabled: bool = False   # opt-in; off until enabled from the web UI
        self._enroute_wind_deviation: int = 30     # Qs497 random wind/OAT deviation, 10-80
        # Exact text of the last corridor we ourselves sent (MSFS wind sync or
        # the enroute wind importer) — compared by value, not time, in
        # _handle_corridor to tell our own echo apart from an external load.
        self._corridor_last_own_value: Optional[str] = None
        self._corridor_snapshot_txt: Optional[str] = None
        self._corridor_snapshot_waypoints: dict = {}   # name → {fl_ft: (dir,spd,oat)}
        self._waypoint_passed: set = set()             # indices into _enroute_waypoints
        self._waypoint_om_wind: dict = {}       # _enroute_waypoints index → {fl_ft: (dir,spd,oat)}
        self._enroute_last_fetch_time: float = 0.0     # epoch
        self._enroute_next_fetch_time: float = 0.0     # epoch
        self._enroute_wind_changed_event: asyncio.Event = asyncio.Event()
        self._enroute_log_path: Optional[str] = None   # fixed per-flight --save-logs base path
        self._enroute_last_corridor_txt: Optional[str] = None  # last corridor we generated
        self._web_windstate: Optional[dict] = None
        self._web_windstate_received_at: float = 0.0

        # Parsed TS SIGMETs used to lift WMO/showers CB suppression when CAPE agrees
        self.ts_sigmets: list = []

        # MSFS bridge state (via frankenmsfsbridge)
        self.msfs_in_cloud: Optional[bool] = None
        self.msfs_qnh_hpa: Optional[float] = None
        self.msfs_cloud_density: Optional[float] = None   # 0–9
        self.msfs_wind_vert: Optional[float] = None       # kt, positive = up
        self.msfs_precip_state: Optional[int] = None      # 2=none, 4=rain, 8=snow
        self._msfs_bridge_last_seen: Optional[float] = None  # monotonic, for the timeout check
        self._msfs_bridge_last_seen_epoch: Optional[float] = None  # time.time(), for web display
        # Runtime toggles for MSFS sync features
        self._msfs_in_cloud_sync: bool = True
        self._msfs_qnh_check: str = "CHECK"   # "CHECK" or "SYNC"
        self._msfs_wind_sync: bool = False
        self.focused_zone: int = 0          # 0 = WxBasic, 1-7 = Wx1-Wx7
        self.cloud_sync_last_alt_ft: float = 0.0

        # Maneuvering mode detection
        self._hdg_history: list = []   # [(monotonic_time, heading_deg)]
        self._maneuvering: bool = False

        # Radar (RainViewer) tile cache — keyed by (tx, ty), cleared on new frame
        self._rv_frame_path: Optional[str] = None
        self._rv_tile_cache: dict = {}

        # Blitzortung lightning strikes — list of (lat, lon) from last _BZ_LOOKBACK_MIN minutes
        self._bz_strikes: list = []
        self._bz_fetch_time: float = 0.0

        # MSFS wind state (--msfs-wind-sync via frankenmsfsbridge)
        self.msfs_wind_dir: Optional[float] = None
        self.msfs_wind_spd: Optional[float] = None
        self.msfs_oat_c: Optional[float] = None
        self._wind_last_encoded: Optional[str] = None
        self._wind_last_updated: float = 0.0
        self._corridor_txt: Optional[str] = None

        # API state broadcast
        self._instance_uuid: str = str(uuid.uuid4())
        self.zone_reason: dict = {}               # zone_num → short reason string
        self.zone_placement_reason: dict = {}     # zone_num → placement description
        self.zone_weather_detail: dict = {}       # zone_num → detailed weather source explanation
        self._state_changed_event: asyncio.Event = asyncio.Event()

        # Conflict detection — suspend PSX changes when a higher-UUID instance is present
        self._conflict_uuid: Optional[str] = None
        self._conflict_last_seen: float = 0.0

        # Operational mode: "enabled" | "paused" | "disabled" | "manual"
        # paused  = stop updating PSX weather; keep WxAutoSet=0 (existing zones remain)
        # disabled = stop updating; set WxAutoSet=1 (PSX resumes its own auto-weather)
        # manual  = all zones get the same manually-configured weather
        self._fw_mode: str = "enabled"
        # True when OM is temporarily unavailable; WxAutoSet=1 until it recovers.
        self._om_unavailable: bool = False
        # Manual weather parameters (used when _fw_mode == "manual")
        self._manual_wx_force_update: bool = False
        self._manual_wx_params: dict = {
            "hi_oktas": 0, "hi_top": 45000, "hi_base": 45000,
            "lo_oktas": 0, "lo_top": 45000, "lo_base": 45000,
            "cb_oktas": 0, "cb_top": 35000, "cb_base": 3000,
            "turb_severity": 0, "turb_top": 5000, "turb_base": 0,
            "mb_mode": 0, "mb_chance": 0, "mb_outflow": 400,
            "inv_on": False, "inv_top": 2320, "inv_tmp": 5,
            "wind_dir": 0, "wind_spd": 0, "wind_gust": 0, "wind_var": 0,
            "precip": 0, "vis_m": 9999,
            "surf_temp": 15, "qnh_hpa": 1013.25,
        }

        # -------------------------------------------------------------------
        # Turbulence subsystem (merged from frankenturb)
        # -------------------------------------------------------------------
        self._turb_enabled: bool = True
        self._turb_low_speed: bool = True
        self._turb_manual_turb_enabled: bool = False
        self._turb_manual_turb_kind: str = "mechanical"
        self._turb_manual_turb_intensity: float = 0.3
        self._turb_intensity_bias: int = 100   # 0-999 %
        self._turb_wind_mode: str = "live"     # "live", "psx", or "manual"
        self._turb_manual_wind_dir: int = 0
        self._turb_manual_wind_spd: int = 0
        self._turb_psx_wind = None             # (dir_deg, speed_kt) or None
        self._turb_lateral_size_bias: int = 50
        self._turb_rate: int = 100             # 0-100, injection rate scale
        self._turb_type_enabled: dict = {k: True for k in _TURB_TYPES}
        self._turb_type_biases: dict = {k: 100 for k in _TURB_TYPES}
        self._turb_msfs_magnitude: int = 100  # 0-200%; scales MSFS turbulence influence
        self._turb_msfs_factor: float = 1.0   # last computed raw factor (for display)
        self._turb_engine: Optional[TurbulenceEngine] = None
        self._turb_state: Optional[TurbulenceState] = None
        self._turb_sources: list = []
        self._turb_print_count: int = 0
        self._turb_state_changed_event: asyncio.Event = asyncio.Event()
        self._turb_pirep_fetcher = None
        self._turb_cape_fetcher = None
        self._turb_gairmet_fetcher = None

        # Standalone web UI state cache (populated by broadcast coroutines)
        self._web_state: Optional[dict] = None
        self._web_turbstate: Optional[dict] = None
        self._web_state_received_at: float = 0.0
        self._web_turbstate_received_at: float = 0.0

        # Snapshot of every web-GUI-configurable setting at its built-in default,
        # captured before any --config-file load — used by "reset to default".
        self._default_config_dict: dict = self._build_config_dict()

    # ------------------------------------------------------------------
    # PSX helpers
    # ------------------------------------------------------------------

    def psx_send_and_set(self, key: str, value: str) -> None:
        """Send a PSX key=value and update the local variable cache."""
        self.logger.debug("→ PSX %s = %s", key, value)
        self.psx.send(key, value)
        self.psx._set(key, value)  # pylint: disable=protected-access

    def _sync_psx_clock(self) -> None:
        """Sync PSX clocks (TimeEarth, TimeClockL, TimeClockR) to current real-world time."""
        ms = int(time.time() * 1000)
        self.logger.info("Syncing PSX clocks to real time: %d ms", ms)
        for key in ("Qs123", "Qs124", "Qs125"):
            self.psx_send_and_set(key, str(ms))

    # ------------------------------------------------------------------
    # PSX event handlers
    # ------------------------------------------------------------------

    def handle_piba(self, _key: str, value: str) -> None:
        """Update aircraft position from PiBaHeAlTas."""
        try:
            parts = value.split(';')
            if len(parts) < 7:
                return
            self.ac_hdg = math.degrees(float(parts[2])) % 360
            self.ac_alt_ft = float(parts[3]) / 1000.0
            self.ac_tas_kt = float(parts[4])
            self.ac_lat = math.degrees(float(parts[5]))
            self.ac_lon = math.degrees(float(parts[6]))
        except (ValueError, IndexError):
            return
        if (self.cloud_sync_last_alt_ft is not None and
                abs(self.ac_alt_ft - self.cloud_sync_last_alt_ft) > 500):
            self.cloud_sync_last_alt_ft = self.ac_alt_ft
            self._apply_msfs_sync()

    def handle_wx_change(self, key: str, value: str) -> None:
        """Re-apply our weather if PSX overwrites a zone we've set."""
        elapsed = time.time() - self.last_write_time
        self.logger.debug("← PSX %s changed (%.1fs since last write)", key, elapsed)
        if elapsed < _PUSH_COOLDOWN_S:
            self.logger.debug("  within cooldown — ignoring echo")
            return
        if key == "WxBasic":
            zone_num = 0
        elif key.startswith("Wx") and key[2:].isdigit():
            zone_num = int(key[2:])
        else:
            return
        desired = self.zone_wx.get(zone_num)
        if desired and value != desired:
            self.logger.info("PSX overwrite detected on %s — re-applying", key)
            self.last_write_time = time.time()
            self.psx_send_and_set(key, desired)
            return
        focused_key = "WxBasic" if self.focused_zone == 0 else f"Wx{self.focused_zone}"
        if key == focused_key:
            self._apply_msfs_sync()

    def handle_focused_zone(self, _key: str, value: str) -> None:
        """Track the PSX focused weather zone and re-apply MSFS sync."""
        self.focused_zone = int(value)
        self._apply_msfs_sync()

    def _web_cache_get(self, name: str) -> Optional[str]:
        """Provide PSX variable lookups for the standalone web UI."""
        if name == 'FocussedWxZone':
            return str(self.focused_zone)
        m = __import__('re').match(r'^Wx(\d+)$', name)
        if m:
            zone_num = int(m.group(1))
            return self.zone_wx.get(zone_num)
        if self.psx and __import__('re').match(r'^Metar\d+$', name):
            return self.psx.get(name)
        if self.psx:
            return self.psx.get(name)
        return None

    def _update_fmc_arpts(self) -> None:
        """Refresh fmc_dep_icao/fmc_dst_icao from the current PSX FMC route state."""
        if not self.psx:
            return
        mode_str = self.psx.get("FmcRteViAcMo") or ""
        if len(mode_str) < 3 or mode_str[2] not in ('1', '2'):
            if self.fmc_dep_icao is not None or self.fmc_dst_icao is not None:
                self.logger.info("FMC: no active route — dep/dst arpt zones released")
            self.fmc_dep_icao = None
            self.fmc_dst_icao = None
            return
        rte_key = "FmcRte1" if mode_str[2] == '1' else "FmcRte2"
        rte_str = self.psx.get(rte_key) or ""
        fields = rte_str.split(';')
        dep_raw = fields[0].strip() if len(fields) > 0 else ''
        dst_raw = fields[1].strip() if len(fields) > 1 else ''
        dep = dep_raw if len(dep_raw) == 4 and dep_raw.isalnum() else None
        dst = dst_raw if len(dst_raw) == 4 and dst_raw.isalnum() else None
        if dep != self.fmc_dep_icao or dst != self.fmc_dst_icao:
            self.logger.info("FMC route %s: dep=%s dst=%s", mode_str[2], dep, dst)
        self.fmc_dep_icao = dep
        self.fmc_dst_icao = dst

    def handle_fmc_change(self, _key: str, _value: str) -> None:
        """Update dep/dst airports when FMC route mode or route data changes."""
        self._update_fmc_arpts()
        self._update_fmc_route_waypoints()
        self.fmc_changed_event.set()

    # ------------------------------------------------------------------
    # Enroute wind importer — route waypoint tracking
    # ------------------------------------------------------------------

    def _update_fmc_route_waypoints(self) -> None:
        """Refresh route_waypoints [(name, lat, lon), ...] from the live FMC route state.

        route_waypoints mirrors PSX's own FmcRte list exactly, which PSX
        trims from the front as waypoints are passed. _enroute_waypoints is
        our own persistent superset for the whole flight: waypoints already
        trimmed by PSX stay in it (marked passed) so the enroute-wind page
        keeps showing them, instead of disappearing the moment PSX drops them.
        """
        if not self.psx:
            return
        mode_str = self.psx.get("FmcRteViAcMo") or ""
        if len(mode_str) < 3 or mode_str[2] not in ('1', '2'):
            if self.route_waypoints:
                self.logger.info("FMC: no active route — enroute wind waypoint list cleared")
                # The FMC route just reset (flight ended) — clear the flight-plan
                # snapshot; the next flight's first WxCorridor load will recapture
                # one, and its first corridor update will start a fresh log file.
                self._corridor_snapshot_txt = None
                self._corridor_snapshot_waypoints = {}
            self._set_route_waypoints([])
            return
        rte_key = "FmcRte1" if mode_str[2] == '1' else "FmcRte2"
        rte_str = self.psx.get(rte_key) or ""
        new_live = _parse_fmc_route_waypoints(rte_str)
        new_live_names = tuple(w[0] for w in new_live)
        old_live_names = tuple(w[0] for w in self.route_waypoints)
        if new_live_names == old_live_names:
            return
        self.route_waypoints = new_live
        persistent_names = [w[0] for w in self._enroute_waypoints]
        if persistent_names and _is_name_suffix(new_live_names, persistent_names):
            dropped = set(persistent_names) - set(new_live_names)
            newly_passed = [
                i for i, name in enumerate(persistent_names)
                if name in dropped and i not in self._waypoint_passed
            ]
            for i in newly_passed:
                self._waypoint_passed.add(i)
                self.logger.info(
                    "Enroute wind: passed %s — freezing its fetched wind",
                    self._enroute_waypoints[i][0])
            if newly_passed:
                # Just a waypoint being passed — the wind data for every
                # remaining waypoint is unchanged, so PSX's own corridor is
                # left in place rather than resent: PSX recalculates when it
                # receives WxCorridorTxt, and that's pointless work when
                # nothing about the actual wind data changed. Only update the
                # UI (so the page can dim the passed waypoint).
                self._enroute_wind_changed_event.set()
            return
        self.logger.info(
            "FMC route changed (%d → %d waypoints, not a simple pass-trim) — "
            "resetting enroute wind fetch state",
            len(persistent_names), len(new_live_names))
        self._set_route_waypoints(new_live)

    def _set_route_waypoints(self, waypoints: list) -> None:
        """Replace both the live and persistent waypoint lists; refetch everything."""
        self.route_waypoints = waypoints
        self._enroute_waypoints = list(waypoints)
        self._waypoint_passed = set()
        self._waypoint_om_wind = {}
        self._enroute_next_fetch_time = 0.0  # fetch immediately on the next cycle
        if not waypoints:
            self._enroute_log_path = None  # next flight starts a fresh log file
        self._enroute_wind_changed_event.set()

    def _current_fl_list_ft(self) -> list:
        """Return the flight levels to use for the generated corridor.

        If the captured flight-plan snapshot parsed successfully, reuse its
        exact flight levels (the union of levels seen across its waypoints)
        so the flight-plan-vs-Open-Meteo diff compares the same levels
        instead of needing to interpolate between two different grids —
        but only if that list is safe to build a corridor with (see
        _valid_fl_list). Falls back to a fixed default set otherwise (no
        snapshot yet, an unsupported/unparseable corridor format, or a
        parsed level list that looks unsafe) — generating a structurally
        correct corridor always takes precedence over matching the flight
        plan's levels.
        """
        if self._corridor_snapshot_waypoints:
            levels = sorted({
                fl for lvls in self._corridor_snapshot_waypoints.values() for fl in lvls
            })
            if _valid_fl_list(levels):
                return levels
        return list(_ENROUTE_FL_LIST_FT)

    @staticmethod
    def _corridor_pretty(corridor_txt: Optional[str]) -> str:
        """Render a '^'-delimited PSX wind corridor as one section per line, for readability."""
        if not corridor_txt:
            return "(none)"
        return corridor_txt.replace('^', '\n')

    @staticmethod
    def _fmt_epoch(epoch: Optional[float]) -> str:
        """Format a Unix epoch as a UTC timestamp string, or 'never' if falsy."""
        if not epoch:
            return "never"
        return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    @staticmethod
    def _fmt_wind(w: Optional[dict]) -> str:
        """Format a {dir_deg, spd_kt, oat_c} dict as e.g. '260/26kt -7C', or '—' if absent."""
        if not w:
            return "—"
        return f"{w['dir_deg']:03.0f}/{w['spd_kt']:.0f}kt {w['oat_c']:+.0f}C"

    @staticmethod
    def _fmt_diff(d: Optional[dict]) -> str:
        """Format a diff {dir_deg, spd_kt, oat_c} dict as signed deltas, or '—' if absent."""
        if not d:
            return "—"
        return f"dir{d['dir_deg']:+.0f} spd{d['spd_kt']:+.0f}kt oat{d['oat_c']:+.0f}C"

    def _render_enroute_wind_text(self, state: dict) -> str:
        """Render the enroute-wind windstate dict as a human-readable text report."""
        lines = [
            f"FrankenWeather enroute wind report — {self._fmt_epoch(time.time())}",
            f"Enroute wind importer enabled: {state['enabled']}",
            f"Last Open-Meteo fetch: {self._fmt_epoch(state['last_fetch_epoch'])}",
            f"Next Open-Meteo fetch: {self._fmt_epoch(state['next_fetch_epoch'])}",
            "",
        ]
        if not state["has_snapshot"]:
            lines.append("No flight-plan wind corridor snapshot captured yet.")
        elif not state["snapshot_parseable"]:
            lines.append(
                "Flight plan wind corridor could not be parsed (unsupported format) — "
                "no flight-plan-vs-Open-Meteo comparison is available.")
            lines.append("Raw flight-plan wind corridor:")
            lines.append(self._corridor_pretty(state.get("snapshot_raw")))
        else:
            lines.append(
                "Per-waypoint flight-plan vs. Open-Meteo wind "
                "(diff = open-meteo minus flight-plan):")
            for wp in state["waypoints"]:
                status = "PASSED" if wp["passed"] else "ahead"
                lines.append(
                    f"  {wp['name']:6s} [{status:6s}] lat={wp['lat']:.3f} lon={wp['lon']:.3f}")
                if not wp["levels"]:
                    lines.append("      (no wind data yet)")
                for lvl in wp["levels"]:
                    lines.append(
                        f"      FL{lvl['fl_ft'] // 100:03d}: "
                        f"flight-plan={self._fmt_wind(lvl['flightplan']):18s} "
                        f"open-meteo={self._fmt_wind(lvl['openmeteo']):18s} "
                        f"diff={self._fmt_diff(lvl['diff'])}")
        lines.append("")
        lines.append("Flight-plan wind corridor (raw, as first captured):")
        lines.append(self._corridor_pretty(self._corridor_snapshot_txt))
        lines.append("")
        lines.append("Last Open-Meteo-generated wind corridor sent to PSX:")
        lines.append(self._corridor_pretty(self._enroute_last_corridor_txt))
        return "\n".join(lines)

    def _save_enroute_wind_log(self) -> None:
        """If --save-logs is set, write a JSON + human-readable enroute wind log per flight.

        Called after every WxCorridor refresh (not just at flight end), so the
        files stay current throughout the flight — handy for development
        without waiting for the flight to finish. The base filename is fixed
        the first time this runs for a given flight and reused for every
        subsequent write; _set_route_waypoints([]) clears it on flight end so
        the next flight gets a fresh pair of files.
        """
        if not self.args or not self.args.save_logs:
            return
        if self._enroute_log_path is None:
            os.makedirs(self.args.save_logs, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            self._enroute_log_path = os.path.join(self.args.save_logs, f"enroute_wind_{ts}")
        try:
            state = self._build_windstate_dict()
            with open(f"{self._enroute_log_path}.json", "w", encoding="utf-8") as fh:
                json.dump(state, fh, indent=2)
            with open(f"{self._enroute_log_path}.txt", "w", encoding="utf-8") as fh:
                fh.write(self._render_enroute_wind_text(state))
        except OSError as exc:
            self.logger.warning("Enroute wind: failed to save log: %s", exc)

    def handle_sigmet_change(self, _key: str, value: str) -> None:
        """Re-parse TS SIGMETs when PSX downloads updated SIGMET data."""
        self.ts_sigmets = _parse_ts_sigmets(value)
        self.logger.info("SIGMETs: %d active TS areas (raw %d bytes)",
                         len(self.ts_sigmets), len(value))

    def _handle_addon(self, _key: str, value: str) -> None:
        """Process addon messages: FRANKENMSFSBRIDGE (slave sim) and FRANKENWEATHER (conflict)."""
        if value.startswith("FRANKENWEATHER:"):
            self._handle_fw_addon(value)
            return
        prefix = "FRANKENMSFSBRIDGE:"
        if not value.startswith(prefix):
            return
        try:
            data = json.loads(value[len(prefix):])
        except (ValueError, KeyError):
            self.logger.warning("Malformed FRANKENMSFSBRIDGE addon: %s", value[:80])
            return
        changed = False
        if "in_cloud" in data:
            new_cloud = bool(data["in_cloud"])
            if new_cloud != self.msfs_in_cloud:
                self.logger.info("MSFS in-cloud (bridge): %s → %s",
                                 self.msfs_in_cloud, new_cloud)
                self.msfs_in_cloud = new_cloud
                changed = True
        if "qnh_hpa" in data:
            new_qnh = float(data["qnh_hpa"])
            prev = self.msfs_qnh_hpa
            self.msfs_qnh_hpa = new_qnh
            if prev is None or abs(new_qnh - prev) > 0.5:
                changed = True
        for _key, _attr, _cast in (
                ("oat_c", "msfs_oat_c", float),
                ("wind_dir", "msfs_wind_dir", float),
                ("wind_spd", "msfs_wind_spd", float),
                ("cloud_density", "msfs_cloud_density", float),
                ("wind_vert", "msfs_wind_vert", float),
                ("precip_state", "msfs_precip_state", int),
        ):
            if _key in data:
                setattr(self, _attr, _cast(data[_key]))
        self._msfs_bridge_last_seen = time.monotonic()
        self._msfs_bridge_last_seen_epoch = time.time()
        if changed:
            self._apply_msfs_sync()
        if self._msfs_wind_sync:
            self._apply_wind_injection()

    def _handle_fw_addon(self, value: str) -> None:
        """Dispatch FRANKENWEATHER addon messages: COMMAND, TURBCOMMAND, or STATE broadcast."""
        rest = value[len("FRANKENWEATHER:"):]
        if rest.startswith("COMMAND:"):
            self._handle_fw_command(rest[len("COMMAND:"):])
            return
        if rest.startswith("TURBCOMMAND:"):
            self._handle_turb_command(rest[len("TURBCOMMAND:"):])
            return
        if rest.startswith("MANUALWXCOMMAND:"):
            self._handle_manual_wx_command(rest[len("MANUALWXCOMMAND:"):])
            return
        if not rest.startswith("STATE:"):
            return
        # State broadcast — UUID conflict detection.
        # UUID v4 strings (hex+hyphens) compare correctly as plain strings.
        rest = rest[len("STATE:"):]
        colon = rest.find(':')
        if colon <= 0:
            return
        recv_uuid = rest[:colon]
        if recv_uuid == self._instance_uuid:
            return
        if recv_uuid > self._instance_uuid:
            if self._conflict_uuid != recv_uuid:
                self.logger.error(
                    "CONFLICT: another FRANKENWEATHER instance detected "
                    "(UUID %s > ours %s) — suspending PSX weather changes",
                    recv_uuid, self._instance_uuid)
            self._conflict_uuid = recv_uuid
            self._conflict_last_seen = time.monotonic()

    def _handle_fw_command(self, json_str: str) -> None:  # pylint: disable=too-many-branches,too-many-statements
        """Apply a FRANKENWEATHER:COMMAND message received via PSX addon."""
        try:
            cmd = json.loads(json_str)
        except ValueError:
            self.logger.warning("Malformed FRANKENWEATHER COMMAND: %s", json_str[:80])
            return
        settings_changed = False
        if "config_action" in cmd:
            action = cmd["config_action"]
            if action == "save":
                self._save_config_file()
            elif action == "load":
                self._load_config_file()
                settings_changed = True
            elif action == "reset":
                self._apply_config_dict(copy.deepcopy(self._default_config_dict))
                self.logger.info("Settings reset to default (not saved to config file)")
                settings_changed = True
            else:
                self.logger.warning("config_action: unknown action %r", action)
        if "msfs_in_cloud_sync" in cmd:
            self._msfs_in_cloud_sync = bool(cmd["msfs_in_cloud_sync"])
            self.logger.info("msfs_in_cloud_sync → %s", self._msfs_in_cloud_sync)
            settings_changed = True
        if "msfs_qnh_check" in cmd:
            val = str(cmd["msfs_qnh_check"])
            if val in ("CHECK", "SYNC"):
                self._msfs_qnh_check = val
                self.logger.info("msfs_qnh_check → %s", val)
                settings_changed = True
        if "msfs_wind_sync" in cmd:
            self._msfs_wind_sync = bool(cmd["msfs_wind_sync"])
            self.logger.info("msfs_wind_sync → %s", self._msfs_wind_sync)
            if self._msfs_wind_sync and self._enroute_wind_enabled:
                # The two features both write WxCorridorTxt — mutually exclusive.
                self._enroute_wind_enabled = False
                self.logger.info("enroute_wind_enabled → False (disabled by msfs_wind_sync)")
                self._restore_corridor_snapshot()
            settings_changed = True
        if "enroute_wind_enabled" in cmd:
            self._enroute_wind_enabled = bool(cmd["enroute_wind_enabled"])
            self.logger.info("enroute_wind_enabled → %s", self._enroute_wind_enabled)
            if self._enroute_wind_enabled:
                if self._msfs_wind_sync:
                    self._msfs_wind_sync = False
                    self.logger.info("msfs_wind_sync → False (disabled by enroute_wind_enabled)")
                self._enroute_next_fetch_time = 0.0  # fetch right away on enable
                self._enroute_wind_changed_event.set()
                self._apply_enroute_wind_qs497()
            else:
                self._restore_corridor_snapshot()
            settings_changed = True
        if "enroute_wind_deviation" in cmd:
            try:
                val = int(cmd["enroute_wind_deviation"])
            except (TypeError, ValueError):
                val = None
            if val in (10, 20, 30, 40, 50, 60, 70, 80):
                self._enroute_wind_deviation = val
                self.logger.info("enroute_wind_deviation → %s", val)
                if self._enroute_wind_enabled:
                    self._apply_enroute_wind_qs497()
                settings_changed = True
            else:
                self.logger.warning(
                    "enroute_wind_deviation: invalid value %r ignored",
                    cmd["enroute_wind_deviation"])
        new_mode = cmd.get("mode")
        if new_mode is None:
            if settings_changed:
                self._state_changed_event.set()
            return
        if new_mode not in ("enabled", "paused", "disabled", "manual"):
            self.logger.warning("FRANKENWEATHER COMMAND: unknown mode %r", new_mode)
            return
        old_mode = self._fw_mode
        if new_mode == old_mode:
            if settings_changed:
                self._state_changed_event.set()
            return
        self._fw_mode = new_mode
        self.logger.info("FRANKENWEATHER mode: %s → %s (via COMMAND)", old_mode, new_mode)
        if self.psx_connected:
            if new_mode == "disabled":
                self.psx_send_and_set("WxAutoSet", "1")
            elif old_mode == "disabled":
                self.psx_send_and_set("WxAutoSet", "0")
                self._sync_psx_clock()
        if new_mode == "manual":
            self._manual_wx_force_update = True
            self.fmc_changed_event.set()
        self._state_changed_event.set()

    def _handle_manual_wx_command(self, json_str: str) -> None:
        """Apply a FRANKENWEATHER:MANUALWXCOMMAND message."""
        try:
            cmd = json.loads(json_str)
        except ValueError:
            self.logger.warning("Malformed MANUALWXCOMMAND: %s", json_str[:80])
            return
        int_fields = (
            "hi_oktas", "hi_top", "hi_base",
            "lo_oktas", "lo_top", "lo_base",
            "cb_oktas", "cb_top", "cb_base",
            "turb_severity", "turb_top", "turb_base",
            "mb_mode", "mb_chance", "mb_outflow",
            "inv_top", "inv_tmp",
            "wind_dir", "wind_spd", "wind_gust", "wind_var",
            "precip", "vis_m", "surf_temp",
        )
        for field in int_fields:
            if field in cmd:
                self._manual_wx_params[field] = int(cmd[field])
        if "qnh_hpa" in cmd:
            self._manual_wx_params["qnh_hpa"] = float(cmd["qnh_hpa"])
        if "inv_on" in cmd:
            self._manual_wx_params["inv_on"] = bool(cmd["inv_on"])
        self.logger.info("Manual wx params updated")
        if self._fw_mode == "manual":
            self._manual_wx_force_update = True
            self.fmc_changed_event.set()
        self._state_changed_event.set()

    def _build_manual_wx_string(self) -> str:
        """Build a 24-field PSX Wx string from _manual_wx_params."""
        p = self._manual_wx_params
        wind_var = int(p.get("wind_var", 0))
        wind_dir = int(p.get("wind_dir", 0))
        wind_spd = int(p.get("wind_spd", 0))
        wind_enc = f"{wind_var:03d}{wind_dir:03d}{wind_spd:02d}"
        qnh_psx = int(round(float(p.get("qnh_hpa", 1013.25)) * 2.953))
        inv_on = 1 if p.get("inv_on") else 0
        inv_tmp = int(round(float(p.get("inv_tmp", 5)) * 10))
        return ";".join([
            str(int(p.get("hi_oktas", 0))),      # [0]  hiCloudCov
            str(int(p.get("hi_top", 45000))),     # [1]  hiCloudTop
            str(int(p.get("hi_base", 45000))),    # [2]  hiCloudBase
            str(int(p.get("lo_oktas", 0))),       # [3]  loCloudCov
            str(int(p.get("lo_top", 45000))),     # [4]  loCloudTop
            str(int(p.get("lo_base", 45000))),    # [5]  loCloudBase
            str(int(p.get("turb_severity", 0))),  # [6]  turbIntensity
            str(int(p.get("turb_top", 5000))),    # [7]  turbTop
            str(int(p.get("turb_base", 0))),      # [8]  turbBase
            str(int(p.get("cb_oktas", 0))),       # [9]  cbCloudCov
            str(int(p.get("cb_top", 35000))),     # [10] cbCloudTop
            str(int(p.get("cb_base", 3000))),     # [11] cbCloudBase
            str(int(p.get("mb_mode", 0))),        # [12] microburstMode
            str(int(p.get("mb_chance", 0))),      # [13] microburstRandom (% chance)
            str(int(p.get("mb_outflow", 400))),   # [14] microburstOutflow
            str(inv_on),                           # [15] inversionOn
            str(int(p.get("inv_top", 2320))),     # [16] inversionTop
            str(inv_tmp),                          # [17] inversionTmp (tenths °C)
            wind_enc,                              # [18] arptWindVarDirSpd
            str(int(p.get("wind_gust", 0))),      # [19] arptWindGust
            str(int(p.get("vis_m", 9999))),       # [20] visibMtrs
            str(int(p.get("precip", 0))),         # [21] precipLevel
            str(int(p.get("surf_temp", 15))),     # [22] surfaceTmp
            str(qnh_psx),                          # [23] QNH (inHg×100)
        ])

    async def _update_zones_manual(self) -> None:
        """Write the same manual Wx string to all 7 PSX weather zones."""
        wx_str = self._build_manual_wx_string()
        now = datetime.now(timezone.utc)
        self.psx_send_and_set("WxAutoSet", "0")
        for zone_num, (lat, lon, icao) in self.zone_positions.items():
            wxmode = build_wxmode_string(lat, lon, 0.0, now.month, icao)
            self.zone_mode[zone_num] = wxmode
            self.zone_is_metar[zone_num] = False
            self.zone_reason[zone_num] = "Manual"
            self.psx_send_and_set(f"WxMode{zone_num}", wxmode)
        await asyncio.sleep(1.0)
        self.last_write_time = time.time()
        self.psx_send_and_set("WxBasic", wx_str)
        for zone_num in range(1, 8):
            self.zone_wx[zone_num] = wx_str
            self.psx_send_and_set(f"Wx{zone_num}", wx_str)
        self._state_changed_event.set()
        self.logger.info("Manual mode zone update complete — wx=%s", wx_str[:60])

    # ------------------------------------------------------------------
    # Turbulence subsystem
    # ------------------------------------------------------------------

    def _turb_type_effective_bias(self, kind: str) -> int:
        """Return combined bias for a turbulence type: 0 when disabled."""
        if not self._turb_type_enabled.get(kind, True):
            return 0
        return self._turb_type_biases.get(kind, 100)

    def _compute_msfs_turb_factor(self, cb_active: bool) -> float:
        """Compute turbulence multiplier from MSFS bridge data.

        Returns 1.0 if bridge data is absent or magnitude is zero.
        cb_active should be True when a PSX CB is the dominant turbulence source,
        because PSX CB clouds do not appear in MSFS so in-cloud will read False.
        """
        if self._turb_msfs_magnitude == 0:
            return 1.0
        if (self._msfs_bridge_last_seen is None or
                time.monotonic() - self._msfs_bridge_last_seen > _MSFS_BRIDGE_TIMEOUT_S):
            return 1.0
        if self.msfs_in_cloud is None:
            return 1.0

        in_cloud = self.msfs_in_cloud or cb_active

        if not in_cloud:
            raw = 0.8
        else:
            raw = 1.3
            # Cloud density 0–9: up to +30 %
            if self.msfs_cloud_density is not None:
                d = max(0.0, min(9.0, self.msfs_cloud_density))
                raw *= 1.0 + d / 9.0 * 0.3
            # Vertical wind: up to +40 % for 10 kt; ignore noise below 3 kt
            if self.msfs_wind_vert is not None:
                v = abs(self.msfs_wind_vert)
                if v > 3.0:
                    raw *= 1.0 + min((v - 3.0) / 7.0, 1.0) * 0.4
            # Precipitation type (character indicator: rain=convective, snow=stratiform)
            if self.msfs_precip_state is not None:
                if self.msfs_precip_state & 4:    # rain → convective
                    raw *= 1.2
                elif self.msfs_precip_state & 8:  # snow → stratiform
                    raw *= 1.1
            raw = min(3.0, raw)

        # Scale by magnitude: 0 % = no influence, 100 % = full, 200 % = amplified
        return 1.0 + (raw - 1.0) * self._turb_msfs_magnitude / 100.0

    # ------------------------------------------------------------------
    # --config-file (TOML): load/save every setting the web GUI can change
    # ------------------------------------------------------------------

    def _build_config_dict(self) -> dict:
        """Build the config-file dict mirroring every setting the web GUI can change."""
        config = {
            "general": {"mode": self._fw_mode},
            "msfs": {key: getattr(self, attr) for key, attr, _cast in _CONFIG_MSFS_FIELDS},
            "enroute_wind": {
                key: getattr(self, attr) for key, attr, _cast in _CONFIG_ENROUTE_WIND_FIELDS},
            "manual_weather": {
                key: self._manual_wx_params[key] for key in _CONFIG_MANUAL_WX_FIELDS},
            "turbulence": {key: getattr(self, attr) for key, attr, _cast in _CONFIG_TURB_FIELDS},
        }
        config["turbulence"]["type_enabled"] = dict(self._turb_type_enabled)
        config["turbulence"]["type_biases"] = dict(self._turb_type_biases)
        return config

    def _apply_config_dict(self, config: dict) -> None:  # pylint: disable=too-many-branches
        """Apply a loaded config-file dict onto runtime state; reconciles side effects after."""
        old_mode = self._fw_mode
        general = config.get("general", {})
        if general.get("mode") in ("enabled", "paused", "disabled", "manual"):
            self._fw_mode = general["mode"]

        msfs = config.get("msfs", {})
        for key, attr, cast in _CONFIG_MSFS_FIELDS:
            if key in msfs:
                setattr(self, attr, cast(msfs[key]))
        if self._msfs_qnh_check not in ("CHECK", "SYNC"):
            self._msfs_qnh_check = "CHECK"

        enroute = config.get("enroute_wind", {})
        for key, attr, cast in _CONFIG_ENROUTE_WIND_FIELDS:
            if key in enroute:
                setattr(self, attr, cast(enroute[key]))
        if self._enroute_wind_deviation not in (10, 20, 30, 40, 50, 60, 70, 80):
            self._enroute_wind_deviation = 30

        manual_wx = config.get("manual_weather", {})
        for key in _CONFIG_MANUAL_WX_FIELDS:
            if key in manual_wx:
                self._manual_wx_params[key] = manual_wx[key]

        turb = config.get("turbulence", {})
        for key, attr, cast in _CONFIG_TURB_FIELDS:
            if key in turb:
                setattr(self, attr, cast(turb[key]))
        for kind in _TURB_TYPES:
            if kind in turb.get("type_enabled", {}):
                self._turb_type_enabled[kind] = bool(turb["type_enabled"][kind])
            if kind in turb.get("type_biases", {}):
                self._turb_type_biases[kind] = int(turb["type_biases"][kind])

        self._reconcile_after_config_load(old_mode)

    def _reconcile_after_config_load(self, old_mode: str) -> None:
        """Re-apply the side effects _handle_fw_command/_handle_turb_command normally do.

        Needed because a config-file load can change many settings at once
        outside those per-field command handlers (at startup, or via the
        "Load from file" web button at runtime).
        """
        if self._turb_engine is not None:
            if self._turb_wind_mode == "manual":
                self._turb_engine.set_fixed_wind(
                    float(self._turb_manual_wind_dir), float(self._turb_manual_wind_spd))
            else:
                self._turb_engine.clear_fixed_wind()
                if self._turb_wind_mode == "psx" and self.psx_connected:
                    self._turb_update_psx_wind()
        if self._enroute_wind_enabled:
            self._enroute_next_fetch_time = 0.0
            self._apply_enroute_wind_qs497()
        else:
            self._restore_corridor_snapshot()
        if self.psx_connected and self._fw_mode != old_mode:
            if self._fw_mode == "disabled":
                self.psx_send_and_set("WxAutoSet", "1")
            elif old_mode == "disabled":
                self.psx_send_and_set("WxAutoSet", "0")
                self._sync_psx_clock()
        if self._fw_mode == "manual":
            self._manual_wx_force_update = True
            self.fmc_changed_event.set()
        self._turb_state_changed_event.set()
        self._state_changed_event.set()
        self._enroute_wind_changed_event.set()

    def _load_config_file(self) -> None:
        """Load settings from --config-file, if given and it exists; else keep defaults."""
        path = self.args.config_file
        if not path:
            return
        if not os.path.exists(path):
            self.logger.info(
                "Config file %s does not exist yet; using default settings "
                "(save from the web UI to create it)", path)
            return
        try:
            with open(path, "rb") as fh:
                config = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            self.logger.warning("Failed to load config file %s: %s", path, exc)
            return
        self._apply_config_dict(config)
        self.logger.info("Loaded settings from config file %s", path)

    def _save_config_file(self) -> bool:
        """Save current settings to --config-file. Returns True on success."""
        path = self.args.config_file
        if not path:
            self.logger.warning("Cannot save settings: no --config-file given at startup")
            return False
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(_dict_to_toml(self._build_config_dict()))
        except OSError as exc:
            self.logger.warning("Failed to save config file %s: %s", path, exc)
            return False
        self.logger.info("Saved settings to config file %s", path)
        return True

    def _turb_update_psx_wind(self) -> None:
        """Read the focused PSX weather zone and update the fixed wind profile."""
        if self._turb_engine is None:
            return
        zone_str = self.psx.get("FocussedWxZone")
        zone = 0
        if zone_str is not None:
            try:
                zone = int(zone_str)
            except ValueError:
                pass
        wx_var = "WxBasic" if zone == 0 else f"Wx{zone}"
        wx_str = self.psx.get(wx_var)
        if not wx_str:
            return
        result = _parse_psx_wind(wx_str)
        if result is None:
            self.logger.warning("PSX wind: could not parse %s=%r", wx_var, wx_str)
            return
        dir_deg, speed_kt = result
        self.logger.info("Turb PSX wind: zone=%d %s → %03d°/%dkt",
                         zone, wx_var, dir_deg, speed_kt)
        self._turb_psx_wind = (dir_deg, speed_kt)
        self._turb_engine.set_fixed_wind(float(dir_deg), float(speed_kt))

    def _handle_turb_command(self, json_str: str) -> None:  # pylint: disable=too-many-branches,too-many-statements
        """Apply a FRANKENWEATHER:TURBCOMMAND message."""
        try:
            cmd = json.loads(json_str)
        except ValueError:
            self.logger.warning("Malformed TURBCOMMAND: %s", json_str[:80])
            return
        changed = False
        if "enabled" in cmd:
            self._turb_enabled = bool(cmd["enabled"])
            changed = True
        if "manual_turb_enabled" in cmd:
            self._turb_manual_turb_enabled = bool(cmd["manual_turb_enabled"])
            changed = True
        if "manual_turb_kind" in cmd:
            kind = str(cmd["manual_turb_kind"])
            if kind in _TURB_TYPES:
                self._turb_manual_turb_kind = kind
                changed = True
        if "manual_turb_intensity" in cmd:
            v = float(cmd["manual_turb_intensity"])
            if 0.0 <= v <= 1.0:
                self._turb_manual_turb_intensity = v
                changed = True
        if "intensity_bias" in cmd:
            v = int(cmd["intensity_bias"])
            if 0 <= v <= 999:
                self._turb_intensity_bias = v
                changed = True
        if "lateral_size_bias" in cmd:
            v = int(cmd["lateral_size_bias"])
            if 0 <= v <= 999:
                self._turb_lateral_size_bias = v
                changed = True
        if "wind_mode" in cmd:
            mode = str(cmd["wind_mode"])
            if mode in ("live", "psx", "manual"):
                self._turb_wind_mode = mode
                if self._turb_engine is not None:
                    if mode == "live":
                        self._turb_engine.clear_fixed_wind()
                    elif mode == "psx":
                        self._turb_engine.clear_fixed_wind()
                        if self.psx_connected:
                            self._turb_update_psx_wind()
                    elif mode == "manual":
                        self._turb_engine.set_fixed_wind(
                            float(self._turb_manual_wind_dir),
                            float(self._turb_manual_wind_spd))
                changed = True
        if "manual_wind_dir" in cmd:
            self._turb_manual_wind_dir = int(cmd["manual_wind_dir"]) % 360
            if self._turb_wind_mode == "manual" and self._turb_engine is not None:
                self._turb_engine.set_fixed_wind(
                    float(self._turb_manual_wind_dir),
                    float(self._turb_manual_wind_spd))
            changed = True
        if "manual_wind_spd" in cmd:
            v = int(cmd["manual_wind_spd"])
            if 0 <= v <= 300:
                self._turb_manual_wind_spd = v
                if self._turb_wind_mode == "manual" and self._turb_engine is not None:
                    self._turb_engine.set_fixed_wind(
                        float(self._turb_manual_wind_dir),
                        float(self._turb_manual_wind_spd))
                changed = True
        if "type_enabled" in cmd:
            for kind, val in cmd["type_enabled"].items():
                if kind in _TURB_TYPES:
                    self._turb_type_enabled[kind] = bool(val)
            changed = True
        if "type_bias" in cmd:
            kind = cmd["type_bias"].get("kind")
            val = cmd["type_bias"].get("value")
            if kind in _TURB_TYPES and val is not None:
                v = int(val)
                if 0 <= v <= 999:
                    self._turb_type_biases[kind] = v
                    changed = True
        if "msfs_turb_magnitude" in cmd:
            v = int(cmd["msfs_turb_magnitude"])
            if 0 <= v <= 200:
                self._turb_msfs_magnitude = v
                changed = True
        if changed:
            self._turb_state_changed_event.set()

    def _get_nearest_cb(self, lat: float, lon: float):
        """Collect CB data from PSX cache and return the nearest active storm cell."""
        raw_time = self.psx.get("TimeEarth")
        try:
            time_earth_ms = int(raw_time) if raw_time else int(time.time() * 1000)
        except ValueError:
            time_earth_ms = int(time.time() * 1000)

        zone_positions = {}
        for zone_i in range(1, 8):
            raw = self.psx.get(f"WxMode{zone_i}")
            if raw:
                pos = parse_wx_zone_position(raw)
                if pos is not None:
                    zone_positions[zone_i] = pos

        zone_cb_data = {}
        planet_raw = self.psx.get("WxBasic")
        if planet_raw:
            planet_cb = parse_wx_zone_basic(planet_raw)
            if planet_cb is not None:
                zone_cb_data[0] = planet_cb
        for zone_i in range(1, 8):
            raw = self.psx.get(f"Wx{zone_i}")
            if raw:
                cb_data = parse_wx_zone_basic(raw)
                if cb_data is not None:
                    zone_cb_data[zone_i] = cb_data

        clust_raw = self.psx.get("WxClust")
        clust_positions = parse_wx_clust(clust_raw) if clust_raw else []

        return find_nearest_cb(lat, lon, zone_positions, zone_cb_data,
                               clust_positions, time_earth_ms,
                               lat_scale=self._turb_lateral_size_bias / 100.0)

    async def psx_wind_coro(self) -> None:
        """Refresh PSX weather-zone wind every 30 s when PSX wind mode is active."""
        myname = inspect.currentframe().f_code.co_name
        try:
            self.logger.debug("Starting %s", myname)
            while True:
                await asyncio.sleep(30.0)
                if self._turb_wind_mode == "psx" and self.psx_connected:
                    self._turb_update_psx_wind()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s", exc, myname)
            self.logger.critical(traceback.format_exc())

    async def turb_state_broadcast_coro(self) -> None:  # pylint: disable=too-many-locals
        """Broadcast TURBSTATE to the PSX network when turbulence state changes."""
        myname = inspect.currentframe().f_code.co_name
        try:
            self.logger.debug("Starting %s", myname)
            while True:
                try:
                    await asyncio.wait_for(self._turb_state_changed_event.wait(), timeout=60.0)
                except asyncio.TimeoutError:
                    pass
                self._turb_state_changed_event.clear()
                state = self._turb_state
                sources = self._turb_sources
                if not math.isnan(getattr(state, 'source_lat', float('nan'))):
                    src_lat = state.source_lat
                    src_lon = state.source_lon
                else:
                    src_lat = None
                    src_lon = None
                payload = {
                    "enabled": self._turb_enabled,
                    "low_speed": self._turb_low_speed,
                    "manual_turb_enabled": self._turb_manual_turb_enabled,
                    "manual_turb_kind": self._turb_manual_turb_kind,
                    "manual_turb_intensity": self._turb_manual_turb_intensity,
                    "intensity_bias": self._turb_intensity_bias,
                    "lateral_size_bias": self._turb_lateral_size_bias,
                    "wind_mode": self._turb_wind_mode,
                    "manual_wind_dir": self._turb_manual_wind_dir,
                    "manual_wind_spd": self._turb_manual_wind_spd,
                    "type_enabled": dict(self._turb_type_enabled),
                    "type_biases": dict(self._turb_type_biases),
                    "active_kind": state.kind if state else "none",
                    "active_intensity": round(state.intensity, 3) if state else 0.0,
                    "active_reason": state.reason if state else "",
                    "source_lat": src_lat,
                    "source_lon": src_lon,
                    "sources": [
                        {"kind": s.kind, "intensity": round(e, 3), "reason": s.reason}
                        for e, s in sources
                    ],
                    "msfs_active": (
                        self._msfs_bridge_last_seen is not None and
                        time.monotonic() - self._msfs_bridge_last_seen <= _MSFS_BRIDGE_TIMEOUT_S
                    ),
                    "msfs_last_seen_epoch": self._msfs_bridge_last_seen_epoch,
                    "msfs_in_cloud": self.msfs_in_cloud,
                    "msfs_cloud_density": self.msfs_cloud_density,
                    "msfs_wind_vert": self.msfs_wind_vert,
                    "msfs_precip_state": self.msfs_precip_state,
                    "msfs_turb_factor": round(self._turb_msfs_factor, 3),
                    "msfs_turb_magnitude": self._turb_msfs_magnitude,
                    "msfs_qnh_hpa": (round(self.msfs_qnh_hpa, 1)
                                     if self.msfs_qnh_hpa is not None else None),
                }
                self._web_turbstate = payload
                self._web_turbstate_received_at = time.time()
                msg = (f"addon=FRANKENWEATHER:TURBSTATE:{self._instance_uuid}:"
                       f"{json.dumps(payload)}")
                if self.psx_connected:
                    self.psx.send("addon", msg[len("addon="):])
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s", exc, myname)
            self.logger.critical(traceback.format_exc())

    async def turbulence_coro(self) -> None:  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
        """Compute turbulence, inject WxBurst into PSX, broadcast state changes."""
        myname = inspect.currentframe().f_code.co_name
        last_print = 0.0
        last_quiet_print = 0.0
        try:
            self.logger.debug("Starting %s", myname)
            loop = asyncio.get_running_loop()
            while True:
                await asyncio.sleep(0.2)

                if not self.psx_connected or self.psx_paused:
                    continue

                raw = self.psx.get("PiBaHeAlTas")
                if not raw:
                    continue
                try:
                    _, _, _, alt_ft, tas_kt, lat, lon = parse_pibahealtas(raw)
                except ValueError as exc:
                    self.logger.warning("Bad PiBaHeAlTas: %s", exc)
                    continue

                low_speed = tas_kt < 30.0
                if low_speed != self._turb_low_speed:
                    self._turb_low_speed = low_speed
                    self._turb_state_changed_event.set()

                if self._turb_engine is None:
                    continue

                if self._turb_manual_turb_enabled:
                    manual_state = TurbulenceState(
                        kind=self._turb_manual_turb_kind,
                        intensity=self._turb_manual_turb_intensity,
                        reason="Manual",
                    )
                    eff = self._turb_manual_turb_intensity
                    if eff >= 0.01 and not low_speed:
                        inject_prob = (eff ** 1.5) * (self._turb_rate / 100.0)
                        if random.random() < inject_prob:
                            base, direction, _ = _pick_burst(manual_state, eff)
                            magnitude = min(99, random.randint(1, max(1, int(eff * 99))))
                            self.psx_send_and_set("WxBurst", str(direction * (base + magnitude)))
                    state_changed = (
                        self._turb_state is None or
                        self._turb_state.kind != manual_state.kind or
                        abs((self._turb_state.intensity or 0.0) - manual_state.intensity) > 0.05
                    )
                    self._turb_state = manual_state
                    self._turb_sources = [(eff, manual_state)] if eff >= 0.01 else []
                    if state_changed:
                        self._turb_state_changed_event.set()
                    continue

                if self._om_unavailable:
                    continue

                state, pirep_rec, cape_sample, gairmet_region = await asyncio.gather(
                    loop.run_in_executor(None, self._turb_engine.compute, lat, lon, alt_ft),
                    loop.run_in_executor(
                        None, self._turb_pirep_fetcher.find_relevant, lat, lon, alt_ft),
                    loop.run_in_executor(None, self._turb_cape_fetcher.get, lat, lon),
                    loop.run_in_executor(
                        None, self._turb_gairmet_fetcher.get_active, lat, lon, alt_ft),
                )

                terrain_state = state

                def _eff(s):
                    return s.intensity * self._turb_type_effective_bias(s.kind)

                cb = self._get_nearest_cb(lat, lon)
                cb_state = None
                if cb is not None:
                    cb_state = compute_cb_turbulence(alt_ft, cb)
                    if _eff(cb_state) > _eff(state):
                        state = cb_state

                pirep_state = None
                if pirep_rec is not None:
                    pirep_state = compute_pirep_turbulence(alt_ft, pirep_rec)
                    if _eff(pirep_state) > _eff(state):
                        state = pirep_state

                cape_state = None
                if cape_sample is not None:
                    cape_state = compute_cape_turbulence(alt_ft, cape_sample)
                    if _eff(cape_state) > _eff(state):
                        state = cape_state

                gairmet_state = None
                if gairmet_region is not None:
                    gairmet_state = compute_gairmet_turbulence(alt_ft, gairmet_region)
                    if _eff(gairmet_state) > _eff(state):
                        state = gairmet_state

                cb_active = state.kind == 'cb'
                msfs_factor = self._compute_msfs_turb_factor(cb_active)
                self._turb_msfs_factor = msfs_factor
                effective_intensity = min(
                    1.0,
                    state.intensity * self._turb_intensity_bias *
                    self._turb_type_effective_bias(state.kind) / 10000.0 * msfs_factor,
                )
                if self._turb_enabled and effective_intensity >= 0.01 and not low_speed:
                    inject_prob = (effective_intensity ** 1.5) * (self._turb_rate / 100.0)
                    if random.random() < inject_prob:
                        base, direction, label = _pick_burst(state, effective_intensity)
                        magnitude = min(99, random.randint(1, max(1,
                                        int(effective_intensity * 99))))
                        psx_value = direction * (base + magnitude)
                        self.psx_send_and_set("WxBurst", str(psx_value))
                        self.logger.debug("Injected WxBurst=%d (%s%s%02d)",
                                          psx_value, label,
                                          '+' if direction > 0 else '-', magnitude)

                all_sources = []
                for src_s in [terrain_state, cb_state, pirep_state, cape_state, gairmet_state]:
                    if src_s is None or src_s.intensity < 0.01:
                        continue
                    src_eff = min(1.0, src_s.intensity * self._turb_intensity_bias *
                                  self._turb_type_effective_bias(src_s.kind) / 10000.0)
                    if src_eff < 0.01:
                        continue
                    all_sources.append((src_eff, src_s))
                all_sources.sort(key=lambda t: t[0], reverse=True)

                state_changed = (
                    self._turb_state is None or
                    self._turb_state.kind != state.kind or
                    abs((self._turb_state.intensity or 0.0) - (state.intensity or 0.0)) > 0.05
                )
                self._turb_state = state
                self._turb_sources = all_sources
                if state_changed:
                    self._turb_state_changed_event.set()

                now = time.monotonic()
                if effective_intensity < 0.01:
                    last_print = 0.0
                    if now - last_quiet_print >= 60.0:
                        last_quiet_print = now
                        self.logger.info(
                            "Turbulence [%s] lat=%.3f lon=%.3f alt=%.0fft | none",
                            "ON " if self._turb_enabled else "OFF", lat, lon, alt_ft,
                        )
                    continue
                if now - last_print < 10.0:
                    continue
                last_print = now

                intensity_label = _intensity_label(effective_intensity)
                enabled_str = "ON " if self._turb_enabled else "OFF"
                kind_str = state.kind
                vert_str = f"{state.vertical:+.2f}" if not _isnan(state.vertical) else "rand"
                roll_str = f"{state.roll:+.2f}" if not _isnan(state.roll) else "rand"
                gust_str = f"{state.gust:+.2f}" if not _isnan(state.gust) else "rand"

                if self._turb_print_count % 20 == 0:
                    self.logger.info("--- Turbulence %s", "-" * 73)
                    self.logger.info(
                        "     [   ] lat(°)   lon(°)   alt(ft)  kind        "
                        "label      (0-1)  vert  roll  gust")
                    self.logger.info("--- Turbulence %s", "-" * 73)
                self._turb_print_count += 1

                self.logger.info(
                    "Turbulence [%s] lat=%.3f lon=%.3f alt=%.0fft | "
                    "%-10s | %-8s (%.2f) | vert=%s roll=%s gust=%s",
                    enabled_str, lat, lon, alt_ft,
                    kind_str, intensity_label, effective_intensity,
                    vert_str, roll_str, gust_str,
                )
                for src_eff, src in all_sources:
                    marker = ">" if (src is state) else " "
                    self.logger.info("           [%s%-10s] %.2f %s",
                                     marker, src.kind, src_eff, src.reason or "")
                    if src.kind == 'cb' and cb is not None:
                        self.logger.info(
                            "           [  cb-geo   ] %s brg=%03.0f° rng=%.0fnm "
                            "base=%.0fft top=%.0fft cov=%d",
                            cb.source, cb.bearing_deg, cb.range_center_nm,
                            cb.cloud_base_ft_msl, cb.cloud_top_ft_msl, cb.coverage)

        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s", exc, myname)
            self.logger.critical(traceback.format_exc())

    def _handle_corridor(self, _key: str, value: str) -> None:
        """Cache the current PSX wind corridor text, and capture flight-plan snapshots.

        Snapshot capture always runs — independent of whether the enroute
        wind importer is actively enabled — so the flight-plan-vs-Open-Meteo
        comparison is available as soon as a route is loaded, even before the
        user opts in to letting FrankenWeather write the corridor. Only the
        active Open-Meteo fetch/write (enroute_wind_coro) requires opt-in.

        A change is "ours" iff its value exactly matches the last corridor we
        sent (from MSFS wind sync or the enroute wind importer) — compared by
        value, not by a time window, since PSX can re-broadcast an unchanged
        variable well after we sent it (e.g. a periodic full resync), and a
        short cooldown would then misread our own old write as a fresh
        flight-plan load. Anything else is treated as external (a situ load
        or flight-plan import) and captured as the flight-plan snapshot.
        """
        self._corridor_txt = value
        if value == self._corridor_last_own_value:
            return
        self.logger.info(
            "Enroute wind: WxCorridor changed externally — capturing flight-plan snapshot")
        self._corridor_snapshot_txt = value
        self._corridor_snapshot_waypoints = wind_corridor.extract_waypoint_winds(value)
        self._waypoint_passed = set()
        self._waypoint_om_wind = {}
        self._enroute_next_fetch_time = 0.0
        self._enroute_wind_changed_event.set()

    def _apply_wind_injection(self) -> None:
        """Inject MSFS wind into the PSX wind corridor as a FWIND waypoint."""
        if not self._msfs_wind_sync or self._enroute_wind_enabled:
            return
        corridor = getattr(self, '_corridor_txt', None)
        if not corridor:
            return
        if (self.msfs_wind_dir is None or self.msfs_wind_spd is None or
                self.msfs_oat_c is None):
            return
        if self.ac_lat is None:
            return

        encoded = (f"{self.msfs_wind_dir:.0f}/{self.msfs_wind_spd:.0f}"
                   f"/{self.msfs_oat_c:.0f}")
        now = time.monotonic()
        if (encoded == self._wind_last_encoded and
                now - self._wind_last_updated < _WIND_UPDATE_INTERVAL_S):
            return

        new_corridor, msg = wind_corridor.update_corridor(
            corridor,
            self.ac_lat, self.ac_lon, self.ac_alt_ft,
            self.msfs_wind_dir, self.msfs_wind_spd, self.msfs_oat_c)
        if new_corridor is None:
            self.logger.debug("Wind corridor: %s", msg)
            return
        self.logger.info("Wind corridor: %s", msg)
        self.psx.send("WxCorridorTxt", new_corridor)
        self._wind_last_encoded = encoded
        self._wind_last_updated = now
        self._corridor_last_own_value = new_corridor

    def _restore_corridor_snapshot(self) -> None:
        """Send the captured flight-plan wind corridor back to PSX.

        Called whenever the enroute wind importer turns off (explicitly, or
        because msfs_wind_sync was re-enabled), so PSX reverts to the
        original flight-plan data instead of being left with our last
        generated corridor. The restored value is remembered as our own
        write so the echoed change isn't mistaken for a fresh external load.
        """
        if not self._corridor_snapshot_txt:
            return
        self.psx.send("WxCorridorTxt", self._corridor_snapshot_txt)
        self._corridor_last_own_value = self._corridor_snapshot_txt
        self._corridor_txt = self._corridor_snapshot_txt
        self.logger.info("Enroute wind: restored original flight-plan wind corridor")

    def _apply_enroute_wind_qs497(self) -> None:
        """Ensure Qs497 tells PSX to use the wind corridor, with our chosen deviation level.

        Qs497 is a 3-digit PSX value: the first digit must be '2' for PSX to
        actually use the wind corridor data; the other two digits (a multiple
        of 10, 10-80) control how much random wind/OAT deviation PSX applies
        on top of it, simulating forecast inaccuracy. Re-sent on every corridor
        refresh (not just once) to keep enforcing it while the importer is on.
        """
        if not self.psx_connected:
            return
        value = f"2{self._enroute_wind_deviation:02d}"
        self.psx_send_and_set("Qs497", value)

    def _apply_enroute_wind_injection(self) -> None:
        """Build a fresh Format-A corridor from route_waypoints + fetched OM wind.

        route_waypoints (PSX's live, front-trimmed list) is always a tail of
        _enroute_waypoints (our persistent superset), so live index i maps to
        persistent index offset+i — that's how _waypoint_om_wind (keyed by
        persistent index) is looked up here.

        Only actually sent to PSX when the built corridor differs from the
        last one we sent: PSX recalculates on every WxCorridorTxt receipt, so
        resending identical wind data (e.g. an hourly refetch that happens to
        return the same forecast) would just cost PSX work for no benefit.
        """
        if not self.route_waypoints:
            return
        offset = len(self._enroute_waypoints) - len(self.route_waypoints)
        wind_by_live_index = {
            i: self._waypoint_om_wind.get(offset + i, {})
            for i in range(len(self.route_waypoints))
        }
        new_corridor = wind_corridor.build_corridor_a(
            self.route_waypoints, self._current_fl_list_ft(), wind_by_live_index)
        if new_corridor != self._enroute_last_corridor_txt:
            self.psx.send("WxCorridorTxt", new_corridor)
            self._corridor_last_own_value = new_corridor
            self._enroute_last_corridor_txt = new_corridor
            self.logger.info(
                "Enroute wind: refreshed WxCorridor for %d waypoint(s) (%d passed)",
                len(self.route_waypoints), len(self._waypoint_passed))
        else:
            self.logger.debug("Enroute wind: corridor unchanged, not resending to PSX")
        self._apply_enroute_wind_qs497()
        self._save_enroute_wind_log()

    def _sigmet_cb_override(self, wx_str: str, pos: tuple,
                            om: dict, zone_label: str) -> str:
        """Restore CAPE-suppressed CBs if the zone lies inside a TS SIGMET polygon.

        TS SIGMETs are observational — controllers report actual thunderstorms.
        Trust the SIGMET even when CAPE is zero; minimum 4 oktas when CAPE
        doesn't contribute more.
        """
        if not self.ts_sigmets:
            return wx_str
        fields = wx_str.split(';')
        if fields[9] != '0':
            return wx_str  # CBs already present — no override needed
        for sig in self.ts_sigmets:
            if not _point_in_polygon(pos[0], pos[1], sig['polygon']):
                continue
            hourly = om.get("hourly", {})
            hi = datetime.now(timezone.utc).hour
            cape = float((hourly.get("cape") or [0])[hi])
            cin = float((hourly.get("convective_inhibition") or [0])[hi])
            h_temp = float((hourly.get("temperature_2m") or [15.0])[hi])
            h_dp = float((hourly.get("dewpoint_2m") or [10.0])[hi])
            cb_oktas = max(_cape_to_cb_oktas(cape, cin), 4)
            cb_tops = max(_cb_tops_ft(cape), sig['top_ft'])
            fields[9] = str(cb_oktas)
            fields[10] = str(cb_tops)
            fields[11] = str(_cb_base_ft(h_temp, h_dp))
            self.logger.info("%s: CB suppression lifted by TS SIGMET (%d oktas top=%dft)",
                             zone_label, cb_oktas, cb_tops)
            return ';'.join(fields)
        return wx_str

    # ------------------------------------------------------------------
    # MSFS sync (in-cloud and QNH)
    # ------------------------------------------------------------------

    def _apply_msfs_sync(self) -> None:  # pylint: disable=too-many-branches,too-many-locals,too-many-statements
        """Apply MSFS→PSX sync for the focused zone: clouds and/or QNH."""
        need_cloud = self._msfs_in_cloud_sync and self.msfs_in_cloud is not None
        need_qnh = True
        if not need_cloud and not need_qnh:
            return
        if self.ac_alt_ft is None:
            return
        if self._msfs_bridge_last_seen is not None:
            bridge_age = time.monotonic() - self._msfs_bridge_last_seen
            if bridge_age > _MSFS_BRIDGE_TIMEOUT_S:
                self.logger.debug("MSFS bridge data stale (%.0fs), skipping sync", bridge_age)
                return
        zone_key = "WxBasic" if self.focused_zone == 0 else f"Wx{self.focused_zone}"
        metar_zone_num = max(self.focused_zone, 1)
        wx = self.psx.get(zone_key) if self.psx else None
        if not wx:
            return
        data = wx.split(';')
        if len(data) < 24:
            return

        changed = False

        if need_qnh and self.zone_is_metar.get(metar_zone_num, False):
            self.logger.debug("QNH sync skipped [%s]: zone uses real METAR", zone_key)
            need_qnh = False

        if need_qnh and self.msfs_qnh_hpa is not None:
            psx_qnh_hpa = int(data[23]) / 2.953
            diff = self.msfs_qnh_hpa - psx_qnh_hpa
            if abs(diff) > 1.0:
                if self._msfs_qnh_check == "SYNC":
                    data[23] = str(_hpa_to_psx_qnh(self.msfs_qnh_hpa))
                    self.logger.info(
                        "QNH sync [%s]: %.1f → %.1f hPa",
                        zone_key, psx_qnh_hpa, self.msfs_qnh_hpa)
                    changed = True
                    old_metar = self.psx.get(f"Metar{metar_zone_num}")
                    if old_metar:
                        new_metar = _update_metar_qnh(old_metar, self.msfs_qnh_hpa)
                        if new_metar != old_metar:
                            self.psx_send_and_set(f"Metar{metar_zone_num}", new_metar)
                else:
                    self.logger.warning(
                        "*** QNH mismatch [%s]: MSFS %.1f hPa  PSX %.1f hPa  Δ%.1f ***",
                        zone_key, self.msfs_qnh_hpa, psx_qnh_hpa, diff)

        if need_cloud:
            alt = self.ac_alt_ft
            margin = 1000
            hi_cov, hi_top, hi_base = int(data[0]), int(data[1]), int(data[2])
            lo_cov, lo_top, lo_base = int(data[3]), int(data[4]), int(data[5])
            cb_cov, cb_top, cb_base = int(data[9]), int(data[10]), int(data[11])
            in_hi = hi_cov > 0 and hi_base + margin <= alt <= hi_top - margin
            in_lo = lo_cov > 0 and lo_base + margin <= alt <= lo_top - margin
            in_cb = cb_cov > 0 and cb_base + margin <= alt <= cb_top - margin
            if self.msfs_in_cloud:
                if in_hi or in_lo or in_cb:
                    self.logger.debug("Cloud sync [%s]: already in cloud", zone_key)
                elif hi_cov == 0:
                    data[0] = "8"
                    data[1] = str(int(alt + margin))
                    data[2] = str(int(alt - margin))
                    self.logger.info("Cloud sync [%s]: created hi layer at %dft",
                                     zone_key, int(alt))
                    changed = True
                elif alt > hi_top - margin:
                    data[0] = "8"
                    data[1] = str(int(alt + margin))
                    self.logger.info("Cloud sync [%s]: raised hi layer top to %dft",
                                     zone_key, int(alt + margin))
                    changed = True
                else:
                    data[0] = "8"
                    data[2] = str(int(alt - margin))
                    self.logger.info("Cloud sync [%s]: lowered hi layer base to %dft",
                                     zone_key, int(alt - margin))
                    changed = True
            else:
                if in_hi:
                    data[0] = "0"
                    self.logger.info("Cloud sync [%s]: MSFS clear — zeroed hi layer", zone_key)
                    changed = True
                elif in_lo:
                    data[3] = "0"
                    self.logger.info("Cloud sync [%s]: MSFS clear — zeroed lo layer", zone_key)
                    changed = True
                else:
                    self.logger.debug("Cloud sync [%s]: MSFS clear, PSX not in cloud", zone_key)

        if changed:
            self.cloud_sync_last_alt_ft = self.ac_alt_ft
            self.last_write_time = time.time()
            new_wx = ";".join(data)
            self.psx_send_and_set(zone_key, new_wx)
            self.zone_wx[self.focused_zone] = new_wx

    # ------------------------------------------------------------------
    # Maneuvering mode detection
    # ------------------------------------------------------------------

    def _update_maneuvering_mode(self) -> bool:  # pylint: disable=too-many-return-statements
        """Update self._maneuvering from speed and heading history. Returns True if changed."""
        if self.ac_hdg is None:
            return False

        # Below 200 kt (taxi, takeoff roll, approach, landing) always maneuver.
        # Clear heading history so ground turns don't re-trigger after takeoff.
        if self.ac_tas_kt is not None and self.ac_tas_kt < 200.0:
            self._hdg_history.clear()
            if not self._maneuvering:
                self._maneuvering = True
                self.logger.info(
                    "MANEUVERING mode: speed %.0f kt — zones redistributed", self.ac_tas_kt)
                return True
            return False

        now = time.monotonic()
        self._hdg_history.append((now, self.ac_hdg))
        cutoff = now - _HDG_WINDOW_S
        self._hdg_history = [(t, h) for t, h in self._hdg_history if t >= cutoff]
        if len(self._hdg_history) < 2:
            return False
        total_change = sum(
            abs(((b - a + 180.0) % 360.0) - 180.0)
            for (_, a), (_, b) in zip(self._hdg_history, self._hdg_history[1:])
        )
        if not self._maneuvering and total_change > _MANEUVER_ENTER_DEG:
            self._maneuvering = True
            self.logger.info(
                "MANEUVERING mode: heading changed %.0f° in %.0fs — zones redistributed",
                total_change, now - self._hdg_history[0][0])
            return True
        if self._maneuvering and total_change < _MANEUVER_EXIT_DEG:
            self._maneuvering = False
            self.logger.info(
                "CRUISE mode: heading change %.0f° in %.0fs — resuming forward placement",
                total_change, now - self._hdg_history[0][0])
            return True
        return False

    # ------------------------------------------------------------------
    # Zone geometry
    # ------------------------------------------------------------------

    def _dist_nm(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Return geodesic distance between two points in nautical miles."""
        _, _, dist_m = self.geod.inv(lon1, lat1, lon2, lat2)
        return dist_m / _NM_TO_M

    def _airports_within_nm(self, lat: float, lon: float, max_nm: float) -> list:
        """Return list of (icao, alat, alon, dist_nm) within max_nm, sorted by distance."""
        results = []
        for icao, (alat, alon) in self.airports.items():
            _, _, dist_m = self.geod.inv(lon, lat, alon, alat)
            dist_nm = dist_m / _NM_TO_M
            if dist_nm <= max_nm:
                results.append((icao, alat, alon, dist_nm))
        results.sort(key=lambda x: x[3])
        return results

    def _rv_local_sample_points(self) -> list:
        """Return a grid of (lat, lon) points within _RV_DENSITY_SCAN_NM of the aircraft.

        Used to pre-fetch RainViewer tiles covering the local area and to compute
        the local CB echo density for the CB squeeze.
        """
        if self.ac_lat is None or self.ac_lon is None:
            return []
        n = _RV_DENSITY_GRID
        radius = _RV_DENSITY_SCAN_NM
        lat_per_nm = 1.0 / 60.0
        lon_per_nm = lat_per_nm / max(0.01, math.cos(math.radians(self.ac_lat)))
        pts = []
        for i in range(n):
            for j in range(n):
                dlat = (i / (n - 1) * 2 - 1) * radius * lat_per_nm
                dlon = (j / (n - 1) * 2 - 1) * radius * lon_per_nm
                lat_c = self.ac_lat + dlat
                lon_c = ((self.ac_lon + dlon + 180.0) % 360.0) - 180.0
                pts.append((lat_c, lon_c))
        return pts

    def _rv_local_density(self) -> float:
        """Fraction of local sample grid points (within _RV_DENSITY_SCAN_NM) with echo >= 2."""
        pts = self._rv_local_sample_points()
        if not pts:
            return 0.0
        hits = sum(1 for lat, lon in pts if self._rv_echo_at(lat, lon) >= 2)
        return hits / len(pts)

    def _effective_fwd_max(self) -> float:
        """Return fwd_max, squeezed toward the cape-squeeze minimum.

        Two independent signals can trigger the squeeze and are combined via max():
        - CAPE signal: average CB oktas across active zones mapped to approximate CAPE
        - Radar signal: fraction of local grid points (within _RV_DENSITY_SCAN_NM)
          with echo >= 2; full squeeze at _RV_DENSITY_THRESHOLD
        Both are linear [0..1]; the larger factor is used.
        """
        _, fwd_max = self.args.new_zone_infront_range
        if not self.args.cape_squeeze:
            return fwd_max
        cape_threshold, min_fwd = self.args.cape_squeeze

        cape_factor = 0.0
        if self.zone_wx:
            estimates = []
            for zone_num in range(1, 8):
                wx = self.zone_wx.get(zone_num)
                if wx:
                    parts = wx.split(';')
                    if len(parts) > 9:
                        estimates.append(_OKTAS_TO_CAPE.get(int(parts[9]), 0.0))
            if estimates:
                avg_cape = sum(estimates) / len(estimates)
                cape_factor = max(0.0, min(1.0, avg_cape / cape_threshold))

        density = self._rv_local_density()
        radar_factor = max(0.0, min(1.0, density / _RV_DENSITY_THRESHOLD))

        factor = max(cape_factor, radar_factor)
        if factor == 0.0:
            return fwd_max
        return fwd_max - factor * (fwd_max - min_fwd)

    def _placement_desc(self, lat: float, lon: float, icao: str,
                        is_arpt: bool = False) -> str:
        """Return a human-readable placement description for a zone at (lat, lon)."""
        if self.ac_lat is None or self.ac_hdg is None:
            return ""
        az, _, dist_m = self.geod.inv(self.ac_lon, self.ac_lat, lon, lat)
        dist_nm = dist_m / _NM_TO_M

        if is_arpt:
            label = "Dep" if icao == self.fmc_dep_icao else "Dst"
            return f"{label} airport {icao}, {dist_nm:.0f}nm from aircraft"

        rel_rad = math.radians((az % 360 - self.ac_hdg + 360) % 360)
        fwd_nm = dist_nm * math.cos(rel_rad)
        right_nm = dist_nm * math.sin(rel_rad)

        if abs(fwd_nm) < 5:
            pos = [f"{dist_nm:.0f}nm abeam"]
        elif fwd_nm >= 0:
            pos = [f"{fwd_nm:.0f}nm ahead"]
        else:
            pos = [f"{abs(fwd_nm):.0f}nm behind"]

        if abs(right_nm) >= 5:
            pos.append(f"{abs(right_nm):.0f}nm {'right' if right_nm > 0 else 'left'} of track")

        is_fake = len(icao) == 4 and icao[0] == 'X' and icao[1:].isdigit()
        if not is_fake:
            pos.append(f"at {icao}")

        return ", ".join(pos)

    def _pick_position(  # pylint: disable=too-many-locals
            self, exclude_zone: int = None, initial: bool = False) -> tuple:
        """Pick a zone placement and return (lat, lon, icao).

        When initial=True: random bearing, distance up to new_zone_infront_range
        MAX, distributing zones evenly around the aircraft at startup.
        When initial=False (relocation): forward along track, distance within
        new_zone_infront_range, lateral offset within new_zone_leftright_range.
        Both modes retry up to 10 times to maintain new_zone_notnear clearance
        from other zones, and snap to a nearby airport if within _AIRPORT_SNAP_NM.
        """
        fwd_min, configured_fwd_max = self.args.new_zone_infront_range
        fwd_max = self._effective_fwd_max()
        fwd_min = fwd_min * fwd_max / configured_fwd_max if configured_fwd_max > 0 else 0.0
        if not initial and self.args.cape_squeeze and fwd_max < configured_fwd_max:
            self.logger.info("CB squeeze active: fwd_max %.0f→%.0fnm",
                             configured_fwd_max, fwd_max)
        lat_min, lat_max = self.args.new_zone_leftright_range
        min_sep = self.args.new_zone_notnear
        others = [(lat, lon) for zn, (lat, lon, _) in self.zone_positions.items()
                  if zn != exclude_zone]
        other_icaos = {icao for zn, (_, _, icao) in self.zone_positions.items()
                       if zn != exclude_zone}
        search_nm = fwd_max + (0.0 if initial else lat_max) + _AIRPORT_SNAP_NM
        airports_nearby = self._airports_within_nm(self.ac_lat, self.ac_lon, search_nm)

        # Generate a pool of candidates; score by radar echo to bias toward active cells.
        # When radar is unavailable all echoes are 0 → reduces to clearance-only selection.
        candidates: list = []
        for _ in range(10):
            if initial or self._maneuvering:
                bearing = random.uniform(0.0, 360.0)
                dist_m = random.uniform(0.0, fwd_max) * _NM_TO_M
            else:
                fwd_nm = random.uniform(fwd_min, fwd_max)
                lat_nm = random.uniform(lat_min, lat_max) * random.choice([-1, 1])
                bearing = (self.ac_hdg + math.degrees(math.atan2(lat_nm, fwd_nm))) % 360.0
                dist_m = math.sqrt(fwd_nm ** 2 + lat_nm ** 2) * _NM_TO_M
            lon_c, lat_c, _ = self.geod.fwd(
                lons=self.ac_lon, lats=self.ac_lat, az=bearing, dist=dist_m)
            lon_c = ((lon_c + 180.0) % 360.0) - 180.0
            best_d, best_ap = float('inf'), None
            for ap_icao, ap_lat, ap_lon, _ in airports_nearby:
                d = self._dist_nm(lat_c, lon_c, ap_lat, ap_lon)
                if d < best_d:
                    best_d, best_ap = d, (ap_icao, ap_lat, ap_lon)
            if (best_d <= _AIRPORT_SNAP_NM and best_ap is not None and
                    best_ap[0] not in other_icaos):
                icao, lat_c, lon_c = best_ap
            else:
                icao = f"X{random.randint(100, 999):03d}"
            too_close = bool(others and
                             any(self._dist_nm(lat_c, lon_c, o[0], o[1]) < min_sep
                                 for o in others))
            echo = self._rv_echo_at(lat_c, lon_c)
            candidates.append((echo, not too_close, lat_c, lon_c, icao))

        # Sort: clearance-passing first (-True < -False), then highest echo within each group.
        candidates.sort(key=lambda c: (-c[1], -c[0]))
        _, _, lat_c, lon_c, icao = candidates[0]
        return lat_c, lon_c, icao

    def _place_all_zones(self) -> None:
        """Place all 7 zones randomly around the aircraft on startup."""
        self.zone_positions.clear()
        self.zone_relocated_time.clear()
        next_zone = 1
        for icao, lat, lon in self._arpt_coverage_needed():
            if next_zone > 7:
                break
            az, _, dist_m = self.geod.inv(self.ac_lon, self.ac_lat, lon, lat)
            self.zone_positions[next_zone] = (lat, lon, icao)
            self.zone_placement_reason[next_zone] = self._placement_desc(
                lat, lon, icao, is_arpt=True)
            self.logger.info(
                "Zone %d: initial placement at %s @ %.3f/%.3f  %.0f°/%.0fnm (dep/dst arpt)",
                next_zone, icao, lat, lon, az % 360, dist_m / _NM_TO_M)
            next_zone += 1
        for zone_num in range(next_zone, 8):
            lat, lon, icao = self._pick_position(initial=True)
            self.zone_positions[zone_num] = (lat, lon, icao)
            self.zone_placement_reason[zone_num] = self._placement_desc(lat, lon, icao)
            az, _, dist_m = self.geod.inv(self.ac_lon, self.ac_lat, lon, lat)
            self.logger.info(
                "Zone %d: initial placement at %s @ %.3f/%.3f  %.0f°/%.0fnm",
                zone_num, icao, lat, lon, az % 360, dist_m / _NM_TO_M)

    def _check_and_relocate(self) -> bool:  # pylint: disable=too-many-locals
        """Relocate zones that are no longer useful given aircraft position and altitude.

        In cruise (>= cruise_alt ft): relocate a zone that is more than
        cruise_behind_dist nm behind the aircraft (aft hemisphere).
        Below cruise alt: relocate any zone more than low_alt_dist nm away.
        Returns True if any zone was moved.
        """
        in_cruise = (self.ac_alt_ft is not None and
                     self.ac_alt_ft >= 18000.0)
        arpt_icaos = {icao for icao, _, _ in self._arpt_coverage_needed()}
        any_moved = False
        now = time.time()
        for zone_num in range(1, 8):
            if zone_num not in self.zone_positions:
                continue
            lat, lon, icao = self.zone_positions[zone_num]
            if icao in arpt_icaos:
                continue  # dep/dst airport — never relocate
            az_to_zone, _, dist_nm = self.geod.inv(self.ac_lon, self.ac_lat, lon, lat)
            dist_nm /= _NM_TO_M
            if in_cruise and not self._maneuvering:
                is_behind = abs((az_to_zone - self.ac_hdg + 180) % 360 - 180) > 90.0
                if not (is_behind and dist_nm > self.args.cruise_behind_dist):
                    continue
                reason = (f"{dist_nm:.0f}nm behind"
                          f" (limit {self.args.cruise_behind_dist:.0f}nm)")
            else:
                if dist_nm <= self.args.low_alt_dist:
                    continue
                if now - self.zone_relocated_time.get(zone_num, 0) < _REFRESH_MAX_S:
                    continue
                reason = f"{dist_nm:.0f}nm away (limit {self.args.low_alt_dist:.0f}nm)"
            new_lat, new_lon, new_icao = self._pick_position(exclude_zone=zone_num)
            self.zone_positions[zone_num] = (new_lat, new_lon, new_icao)
            self.zone_placement_reason[zone_num] = self._placement_desc(
                new_lat, new_lon, new_icao)
            self.zone_relocated_time[zone_num] = now
            new_az, _, new_dist_m = self.geod.inv(
                self.ac_lon, self.ac_lat, new_lon, new_lat)
            self.logger.info(
                "Zone %d: relocated from %s @ %.3f/%.3f [%.0f°/%.0fnm] (%s)"
                " → %s @ %.3f/%.3f [%.0f°/%.0fnm]",
                zone_num, icao, lat, lon, az_to_zone % 360, dist_nm, reason,
                new_icao, new_lat, new_lon, new_az % 360, new_dist_m / _NM_TO_M)
            any_moved = True
        return any_moved

    def _arpt_coverage_needed(self) -> list:
        """Return (icao, lat, lon) for dep/dst airports within arpt_zone_dist of aircraft."""
        _ARPT_ZONE_DIST_NM = 200.0
        if self.ac_lat is None:
            return []
        result = []
        seen: set = set()
        for icao in (self.fmc_dep_icao, self.fmc_dst_icao):
            if not icao or icao in seen:
                continue
            seen.add(icao)
            if icao not in self.airports:
                self.logger.warning("FMC arpt %s not in airport database — no zone assigned",
                                    icao)
                continue
            lat, lon = self.airports[icao]
            dist = self._dist_nm(self.ac_lat, self.ac_lon, lat, lon)
            if dist <= _ARPT_ZONE_DIST_NM:
                result.append((icao, lat, lon))
            else:
                self.logger.debug("FMC arpt %s too far (%.0fnm > %.0fnm limit)",
                                  icao, dist, _ARPT_ZONE_DIST_NM)
        return result

    def _most_expendable_zone(self, exclude_icaos: set) -> Optional[int]:
        """Return the zone number farthest from aircraft not serving an excluded airport."""
        best_zn, best_dist = None, -1.0
        for zn, (zlat, zlon, zicao) in self.zone_positions.items():
            if zicao in exclude_icaos:
                continue
            d = self._dist_nm(self.ac_lat, self.ac_lon, zlat, zlon)
            if d > best_dist:
                best_dist, best_zn = d, zn
        return best_zn

    def _ensure_arpt_zones(self) -> bool:
        """Ensure dep/dst airports within arpt_zone_dist have a dedicated zone.

        Returns True if any zone was relocated to an airport.
        """
        needed = self._arpt_coverage_needed()
        if not needed:
            return False
        covered = {zicao for _, _, zicao in self.zone_positions.values()}
        protected = covered & {icao for icao, _, _ in needed}
        moved = False
        for icao, lat, lon in needed:
            if icao in covered:
                continue
            best_zn = self._most_expendable_zone(protected)
            if best_zn is None:
                self.logger.warning("No expendable zone available for arpt %s", icao)
                continue
            old_pos = self.zone_positions[best_zn]
            self.zone_positions[best_zn] = (lat, lon, icao)
            self.zone_placement_reason[best_zn] = self._placement_desc(
                lat, lon, icao, is_arpt=True)
            self.zone_relocated_time[best_zn] = time.time()
            old_az, _, old_dm = self.geod.inv(
                self.ac_lon, self.ac_lat, old_pos[1], old_pos[0])
            new_az, _, new_dm = self.geod.inv(self.ac_lon, self.ac_lat, lon, lat)
            self.logger.info(
                "Zone %d: relocated from %s @ %.3f/%.3f [%.0f°/%.0fnm]"
                " → %s @ %.3f/%.3f [%.0f°/%.0fnm] (dep/dst arpt)",
                best_zn, old_pos[2], old_pos[0], old_pos[1],
                old_az % 360, old_dm / _NM_TO_M,
                icao, lat, lon, new_az % 360, new_dm / _NM_TO_M)
            covered.add(icao)
            protected.add(icao)
            moved = True
        return moved

    # ------------------------------------------------------------------
    # VATSIM METAR cache
    # ------------------------------------------------------------------

    async def _refresh_vatsim_cache(self, session: aiohttp.ClientSession) -> None:
        """Fetch all VATSIM METARs and update the local cache."""
        try:
            async with session.get(_VATSIM_ALL_URL,
                                   timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status != 200:
                    self.logger.warning("VATSIM METAR /all: HTTP %d", r.status)
                    return
                text = await r.text()
                metars: dict = {}
                for line in text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split()
                    if parts:
                        icao = parts[0]
                        if len(icao) == 4 and icao.upper() == icao and icao.isalnum():
                            metars[icao] = line
                self.vatsim_cache = metars
                self.vatsim_cache_time = time.time()
                self.logger.info("VATSIM METAR cache refreshed: %d airports", len(metars))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.warning("VATSIM METAR cache refresh failed: %s", exc)

    # ------------------------------------------------------------------
    # Open-Meteo fetch
    # ------------------------------------------------------------------

    @staticmethod
    def _om_get_sync(url: str, proxies: Optional[dict]) -> tuple:
        """Run a blocking Open-Meteo GET; intended for run_in_executor.

        Returns (status_code, json_body_or_None).
        """
        r = requests.get(url, proxies=proxies, timeout=30)
        body = r.json() if r.status_code in (200, 429) else None
        return r.status_code, body

    async def _fetch_om_batch(self, positions: list) -> list:
        """Fetch Open-Meteo current weather for all positions. Returns list of dicts."""
        lats = ','.join(f"{p[0]:.4f}" for p in positions)
        lons = ','.join(
            f"{max(-180.0, min(180.0, p[1])):.4f}" for p in positions)
        url = (f"{_OM_URL}?latitude={lats}&longitude={lons}"
               f"&current={_OM_VARS}&wind_speed_unit=kn&timezone=UTC"
               f"&hourly=cape,convective_inhibition,temperature_2m,dewpoint_2m,showers"
               f"&forecast_days=1")
        proxies = ({'http': self.args.om_proxy, 'https': self.args.om_proxy}
                   if self.args.om_proxy else None)
        loop = asyncio.get_running_loop()
        for attempt in range(2):
            try:
                status, body = await loop.run_in_executor(
                    None, self._om_get_sync, url, proxies)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                self.logger.warning("Open-Meteo fetch failed: %s", exc)
                return []
            if status == 429:
                if attempt == 0:
                    self.logger.warning("Open-Meteo rate limited — sleeping 60s")
                    await asyncio.sleep(60)
                    continue
                self.logger.warning("Open-Meteo still rate limited after retry")
                return []
            if status != 200:
                self.logger.warning("Open-Meteo HTTP %d", status)
                return []
            if isinstance(body, dict):
                body = [body]
            return body or []
        return []

    @staticmethod
    def _enroute_hpa_levels(fl_list_ft: list) -> list:
        """Return the sorted unique Open-Meteo pressure levels needed for fl_list_ft."""
        return sorted({_nearest_om_hpa(fl) for fl in fl_list_ft}, reverse=True)

    async def _fetch_enroute_om_batch(self, targets: list) -> None:  # pylint: disable=too-many-locals
        """Fetch Open-Meteo pressure-level wind/OAT for each (index, name, lat, lon) target.

        Updates self._waypoint_om_wind[index] with {fl_ft: (dir_deg, spd_kt, oat_c)}
        for every level in _current_fl_list_ft(), using the nearest Open-Meteo
        pressure level's hourly forecast for the current UTC hour. A model is
        pinned explicitly (gfs_seamless) since pressure-level fields are not
        guaranteed to be populated under the default best-match model blend.
        """
        if not targets:
            return
        fl_list_ft = self._current_fl_list_ft()
        hpa_levels = self._enroute_hpa_levels(fl_list_ft)
        hourly_vars = ",".join(
            f"{var}_{hpa}hPa"
            for hpa in hpa_levels
            for var in ("temperature", "winddirection", "windspeed"))
        lats = ','.join(f"{t[2]:.4f}" for t in targets)
        lons = ','.join(f"{max(-180.0, min(180.0, t[3])):.4f}" for t in targets)
        url = (f"{_OM_URL}?latitude={lats}&longitude={lons}"
               f"&hourly={hourly_vars}&wind_speed_unit=kn&timezone=UTC"
               f"&models=gfs_seamless&forecast_days=1")
        proxies = ({'http': self.args.om_proxy, 'https': self.args.om_proxy}
                   if self.args.om_proxy else None)
        loop = asyncio.get_running_loop()
        try:
            status, body = await loop.run_in_executor(None, self._om_get_sync, url, proxies)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.warning("Enroute wind: Open-Meteo fetch failed: %s", exc)
            return
        if status != 200 or not body:
            self.logger.warning("Enroute wind: Open-Meteo HTTP %s", status)
            return
        if isinstance(body, dict):
            body = [body]
        hi = datetime.now(timezone.utc).hour
        fl_to_hpa = {fl: _nearest_om_hpa(fl) for fl in fl_list_ft}
        for (idx, name, _lat, _lon), loc in zip(targets, body):
            hourly = loc.get("hourly") or {}
            levels = {}
            for fl, hpa in fl_to_hpa.items():
                try:
                    spd = hourly[f"windspeed_{hpa}hPa"][hi]
                    wdir = hourly[f"winddirection_{hpa}hPa"][hi]
                    temp = hourly[f"temperature_{hpa}hPa"][hi]
                except (KeyError, IndexError, TypeError):
                    continue
                if spd is None or wdir is None or temp is None:
                    continue
                levels[fl] = (float(wdir), float(spd), float(temp))
            if levels:
                self._waypoint_om_wind[idx] = levels
            else:
                self.logger.warning(
                    "Enroute wind: no Open-Meteo pressure-level data for %s", name)

    # ------------------------------------------------------------------
    # Radar (RainViewer) helpers
    # ------------------------------------------------------------------

    async def _fetch_rv_frame(self, session: aiohttp.ClientSession) -> None:
        """Refresh the RainViewer radar frame path; clear tile cache when frame changes."""
        try:
            async with session.get(_RV_API, timeout=_RV_TIMEOUT) as resp:
                if resp.status != 200:
                    return
                data = await resp.json(content_type=None)
            past = (data.get('radar') or {}).get('past') or []
            path = past[-1].get('path') if past else None
            if path and path != self._rv_frame_path:
                self._rv_frame_path = path
                self._rv_tile_cache.clear()
                self.logger.debug("RainViewer: new frame %s", path)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.debug("RainViewer frame fetch failed: %s", exc)

    async def _fetch_rv_tiles(self, session: aiohttp.ClientSession,
                              positions: list) -> None:
        """Fetch and cache RainViewer tiles for the given (lat, lon) positions."""
        if not _HAS_PIL or self._rv_frame_path is None:
            return
        needed = {_rv_tile_xy(lat, lon, _RV_ZOOM) for lat, lon in positions}
        to_fetch = needed - set(self._rv_tile_cache)
        if not to_fetch:
            return

        async def _get_tile(tx: int, ty: int) -> tuple:
            url = (f"{_RV_TILE}{self._rv_frame_path}"
                   f"/{_RV_TILE_PX}/{_RV_ZOOM}/{tx}/{ty}/2/1_1.png")
            try:
                async with session.get(url, timeout=_RV_TIMEOUT) as r:
                    if r.status != 200:
                        return tx, ty, None
                    data = await r.read()
                return tx, ty, _PIL_Image.open(BytesIO(data)).convert('RGBA')
            except Exception:  # pylint: disable=broad-exception-caught
                return tx, ty, None

        results = await asyncio.gather(*[_get_tile(tx, ty) for tx, ty in to_fetch])
        for tx, ty, img in results:
            if img is not None:
                self._rv_tile_cache[(tx, ty)] = img
        self.logger.debug("RainViewer: fetched %d/%d tiles",
                          sum(1 for _, _, img in results if img is not None), len(to_fetch))

    def _rv_echo_at(self, lat: float, lon: float) -> int:
        """Return cached RainViewer echo strength 0-3 at (lat, lon), or 0 if not available."""
        if not _HAS_PIL or self._rv_frame_path is None:
            return 0
        tx, ty = _rv_tile_xy(lat, lon, _RV_ZOOM)
        img = self._rv_tile_cache.get((tx, ty))
        if img is None:
            return 0
        px, py = _rv_pixel_in_tile(lat, lon, _RV_ZOOM)
        return _rv_tile_echo(img, px, py)

    # ------------------------------------------------------------------
    # Lightning (Blitzortung) helpers
    # ------------------------------------------------------------------

    async def _fetch_bz_strikes(self, session: aiohttp.ClientSession) -> None:
        """Refresh Blitzortung strike list from the last _BZ_LOOKBACK_MIN minutes."""
        if time.monotonic() - self._bz_fetch_time < _BZ_CACHE_S:
            return
        now_utc = datetime.now(timezone.utc)
        strikes = []

        async def _get_minute(dt: datetime) -> list:
            url = (f"{_BZ_URL}/{dt.year}/{dt.month:02d}/{dt.day:02d}"
                   f"/{dt.hour:02d}/{dt.minute:02d}.json")
            try:
                async with session.get(url, timeout=_RV_TIMEOUT) as r:
                    if r.status != 200:
                        return []
                    data = await r.json(content_type=None)
                    if not isinstance(data, list):
                        return []
                    return [(float(s['lat']), float(s['lon']))
                            for s in data if 'lat' in s and 'lon' in s]
            except Exception:  # pylint: disable=broad-exception-caught
                return []

        minutes = [
            (now_utc - timedelta(minutes=offset)).replace(second=0, microsecond=0)
            for offset in range(1, _BZ_LOOKBACK_MIN + 1)
        ]
        results = await asyncio.gather(*[_get_minute(dt) for dt in minutes])
        for batch in results:
            strikes.extend(batch)

        old_count = len(self._bz_strikes)
        self._bz_strikes = strikes
        self._bz_fetch_time = time.monotonic()
        if strikes or old_count:
            self.logger.debug("Blitzortung: %d strikes (last %d min)",
                              len(strikes), _BZ_LOOKBACK_MIN)

    def _bz_near(self, lat: float, lon: float) -> bool:
        """Return True if a recent Blitzortung strike lies within _BZ_RADIUS_NM."""
        lat_tol = 2.0   # fast coarse filter before expensive _dist_nm call
        for slat, slon in self._bz_strikes:
            if abs(slat - lat) > lat_tol:
                continue
            if self._dist_nm(lat, lon, slat, slon) <= _BZ_RADIUS_NM:
                return True
        return False

    # ------------------------------------------------------------------
    # Zone update
    # ------------------------------------------------------------------

    async def _update_zones(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
            self, session: aiohttp.ClientSession) -> None:
        """Recalculate and push all 7 weather zones to PSX."""
        positions = list(self.zone_positions[i] for i in range(1, 8))
        self.logger.info("Updating zone weather — ac=%.2f/%.2f hdg=%.0f° [%s]",
                         self.ac_lat, self.ac_lon, self.ac_hdg,
                         "MANEUVERING" if self._maneuvering else "CRUISE")

        # Refresh VATSIM METAR cache if stale
        if time.time() - self.vatsim_cache_time > _VATSIM_CACHE_MAX_S:
            await self._refresh_vatsim_cache(session)

        # Find all airports within snapping range for each zone
        zone_candidates = [self._airports_within_nm(lat, lon, _AIRPORT_SNAP_NM)
                           for lat, lon, _ in positions]

        # Per-zone: pick the nearest airport that has a VATSIM METAR; otherwise Open-Meteo
        snap_positions: list = []
        snap_icaos: list = []
        raw_metars: list = []   # real METAR string, or None if Open-Meteo needed
        om_zone_idx: list = []  # indices of zones that need Open-Meteo
        used_icaos: set = set()

        for i, (lat, lon, stored_icao) in enumerate(positions):
            for icao, alat, alon, dist_nm in zone_candidates[i]:
                if icao in self.vatsim_cache and icao not in used_icaos:
                    snap_positions.append((alat, alon))
                    snap_icaos.append(icao)
                    raw_metars.append(self.vatsim_cache[icao])
                    used_icaos.add(icao)
                    self.logger.debug("Zone %d → %s (VATSIM METAR, %.1fnm)", i + 1, icao, dist_nm)
                    break
            else:
                # No VATSIM METAR — snap to nearest available airport anyway, use Open-Meteo
                best_ap = next(
                    (ap for ap in zone_candidates[i] if ap[0] not in used_icaos), None)
                if best_ap is not None:
                    snap_positions.append((best_ap[1], best_ap[2]))
                    snap_icaos.append(best_ap[0])
                    raw_metars.append(None)
                    om_zone_idx.append(i)
                    used_icaos.add(best_ap[0])
                    ac_dist = self._dist_nm(self.ac_lat, self.ac_lon, best_ap[1], best_ap[2])
                    self.logger.warning(
                        "Zone %d: no METAR for %s (%.0fnm from ac) — using Open-Meteo",
                        i + 1, best_ap[0], ac_dist)
                else:
                    snap_positions.append((lat, lon))
                    snap_icaos.append(stored_icao)
                    raw_metars.append(None)
                    om_zone_idx.append(i)
                    used_icaos.add(stored_icao)
                    self.logger.debug(
                        "Zone %d → %s (Open-Meteo; no airports within %.0fnm)",
                        i + 1, stored_icao, _AIRPORT_SNAP_NM)

        # Fetch Open-Meteo for all 7 zones — CB always comes from OM even for METAR zones
        om_batch = await self._fetch_om_batch(snap_positions)
        if not om_batch:
            if not self._om_unavailable:
                self._om_unavailable = True
                self.logger.warning(
                    "Open-Meteo unavailable — reverting to PSX default weather")
                self.psx_send_and_set("WxAutoSet", "1")
                self._state_changed_event.set()
            return
        if self._om_unavailable:
            self._om_unavailable = False
            self.logger.info("Open-Meteo available again — resuming FrankenWeather")
            self.psx_send_and_set("WxAutoSet", "0")
            self._state_changed_event.set()
        om_by_zone: dict = {i: om_batch[i] for i in range(min(len(om_batch), 7))}

        # Refresh radar and lightning sources; failures degrade to echo=0 / no lightning
        await self._fetch_rv_frame(session)
        await self._fetch_rv_tiles(session, snap_positions + self._rv_local_sample_points())
        await self._fetch_bz_strikes(session)

        now = datetime.now(timezone.utc)
        month = now.month

        new_modes: list = []
        new_wxs: list = []
        new_metars: list = []
        _per_zone: list = []   # per-zone CB trigger info for weather detail generation
        for i in range(7):
            lat, lon = snap_positions[i]
            icao = snap_icaos[i]
            om = om_by_zone.get(i, {})
            radar_echo = self._rv_echo_at(lat, lon)
            has_lightning = self._bz_near(lat, lon)
            ts_oktas = 0
            has_showers_metar = False
            if raw_metars[i] is not None:
                parsed = _parse_metar(raw_metars[i])
                new_modes.append(build_wxmode_string(lat, lon, 0.0, month, icao))
                wx = metar_to_wx_string(parsed)
                ts_oktas = parsed.get('ts_oktas', 0)
                has_showers_metar = parsed.get('showers', False)
                need_cb = ts_oktas > 0 or has_showers_metar or radar_echo >= 2 or has_lightning
                if om and need_cb:
                    refined = _apply_om_cb(
                        wx, om,
                        metar_showers=has_showers_metar,
                        metar_ts=ts_oktas > 0,
                        radar_echo=radar_echo,
                        lightning=has_lightning)
                    if refined.split(';')[9] != '0':
                        wx = refined
                    elif ts_oktas > 0:
                        # OM suppressed CBs but METAR directly observes TS/GR — keep
                        # METAR-derived coverage; OM is lagging the actual observation.
                        pass
                new_metars.append(raw_metars[i])
            else:
                elev = float(om.get("elevation", 0)) if om else 0.0
                new_modes.append(build_wxmode_string(lat, lon, elev, month, icao))
                wx = (om_to_wx_string(om, radar_echo=radar_echo, lightning=has_lightning)
                      if om else ";".join(_WX_DEFAULTS))
                new_metars.append(_gen_metar(icao, om, now) if om else None)
            wx_before_sigmet = wx
            final_wx = self._sigmet_cb_override(wx, (lat, lon), om, f"Zone {i + 1} {icao}")
            new_wxs.append(final_wx)
            _per_zone.append({
                'radar_echo': radar_echo,
                'lightning': has_lightning,
                'ts_oktas': ts_oktas,
                'showers_metar': has_showers_metar,
                'sigmet_override': (final_wx.split(';')[9] != '0' and
                                    wx_before_sigmet.split(';')[9] == '0'),
            })

        if self.args.fake_cb:
            oktas, base_ft, top_ft = self.args.fake_cb
            new_wxs = [_apply_fake_cb(wx, oktas, base_ft, top_ft) for wx in new_wxs]
            self.logger.debug("fake-cb applied: %d oktas, base %dft, top %dft",
                              oktas, base_ft, top_ft)

        arpt_set = {self.fmc_dep_icao, self.fmc_dst_icao} - {None}
        cb_suffixes = []
        for i in range(7):
            parts = new_wxs[i].split(';')
            cb_oktas = int(parts[9]) if len(parts) > 11 and parts[9] != '0' else 0
            om = om_by_zone.get(i, {})
            hourly = om.get("hourly", {})

            def _hval(key, default, _h=now.hour, _hr=hourly):
                lst = _hr.get(key) or []
                return float(lst[_h]) if _h < len(lst) else float(default)

            cape = _hval("cape", 0)
            cin = _hval("convective_inhibition", 0)
            showers_mm = _hval("showers", 0)
            wmo_code = int((om.get("current") or {}).get("weather_code", 0))
            if cb_oktas > 0:
                cb_part = (f"  CB {cb_oktas} oktas"
                           f" base={parts[11]}ft tops={parts[10]}ft"
                           f" (WMO={wmo_code} showers={showers_mm:.2f}mm/h)")
            elif _cape_to_cb_oktas(cape, cin) > 0 and showers_mm < 0.5:
                cb_part = (f"  (CAPE suppressed: WMO={wmo_code}"
                           f" showers={showers_mm:.2f}mm/h)")
            else:
                cb_part = ""
            cb_suffixes.append(f"  CAPE={cape:.0f} J/kg{cb_part}")

            # Build zone reason (short) and weather_detail (long) for API broadcast
            src = "VATSIM" if raw_metars[i] else "OM"
            stored_icao = self.zone_positions.get(i + 1, (None, None, ""))[2]
            reason = f"{src} {snap_icaos[i]}"
            if stored_icao in arpt_set:
                reason += " (dep/dst arpt)"
            if cb_oktas > 0:
                reason += f" CB {cb_oktas}ok {parts[11]}-{parts[10]}ft"
            elif _cape_to_cb_oktas(cape, cin) > 0 and showers_mm < 0.5:
                reason += f" CAPE={cape:.0f}J/kg suppressed"
            self.zone_reason[i + 1] = reason[:100]

            pz = _per_zone[i]
            snap_icao = snap_icaos[i]
            is_metar = raw_metars[i] is not None
            _snap_fake = (len(snap_icao) == 4 and snap_icao[0] == 'X' and
                          snap_icao[1:].isdigit())
            if stored_icao in arpt_set:
                _role = ("departure" if stored_icao == self.fmc_dep_icao
                         else "destination")
                base = f"{_role} airport {snap_icao}"
                base += "; VATSIM METAR" if is_metar else "; OpenMeteo"
            elif is_metar:
                base = f"VATSIM METAR {snap_icao}"
                if snap_icao != stored_icao and stored_icao:
                    base += f" (nearest METAR to {stored_icao})"
            else:
                base = "OpenMeteo"
                if snap_icao and not _snap_fake:
                    base += f" at {snap_icao}"
                    if stored_icao and stored_icao != snap_icao and not (
                            len(stored_icao) == 4 and stored_icao[0] == 'X' and
                            stored_icao[1:].isdigit()):
                        base += f" (nearest to {stored_icao})"
            cb_sources = []
            if pz['ts_oktas'] > 0:
                cb_sources.append(f"METAR TS ({pz['ts_oktas']}oktas)")
            if pz['showers_metar']:
                cb_sources.append("METAR showers")
            if cb_oktas > 0 and (cape > 0 or not is_metar):
                if showers_mm >= 0.5:
                    cb_sources.append(f"CAPE {cape:.0f} J/kg + showers {showers_mm:.2f} mm/h")
                elif cape > 0:
                    cb_sources.append(f"CAPE {cape:.0f} J/kg (WMO {wmo_code})")
            if pz['radar_echo'] >= 2:
                cb_sources.append(f"radar echo {pz['radar_echo']}")
            if pz['lightning']:
                cb_sources.append("Blitzortung lightning")
            if pz['sigmet_override']:
                cb_sources.append("TS SIGMET override")
            if cb_oktas > 0:
                detail = base + f"; CBs {cb_oktas}oktas {parts[11]}-{parts[10]}ft"
                if cb_sources:
                    detail += " from " + " + ".join(cb_sources)
            elif _cape_to_cb_oktas(cape, cin) > 0 and showers_mm < 0.5:
                detail = base + (f"; no CBs — CAPE {cape:.0f} J/kg suppressed"
                                 f" (showers {showers_mm:.2f} mm/h, WMO {wmo_code})")
                if pz['lightning']:
                    detail += "; lightning present but CAPE suppressed"
            else:
                detail = base
                reasons_no_cb = []
                if cape > 0:
                    reasons_no_cb.append(f"CAPE {cape:.0f} J/kg")
                if wmo_code:
                    reasons_no_cb.append(f"WMO {wmo_code}")
                if reasons_no_cb:
                    detail += "; no CBs (" + ", ".join(reasons_no_cb) + ")"
                elif not is_metar:
                    detail += "; no CBs"
            self.zone_weather_detail[i + 1] = detail

        self.psx_send_and_set("WxAutoSet", "0")

        for i, wxmode in enumerate(new_modes):
            zone_num = i + 1
            self.zone_mode[zone_num] = wxmode
            self.zone_is_metar[zone_num] = raw_metars[i] is not None
            src = "VATSIM" if raw_metars[i] else "OM"
            self.psx_send_and_set(f"WxMode{zone_num}", wxmode)
            self.logger.info("Zone %d [%s]: %s @ %.3f/%.3f%s",
                             zone_num, src, snap_icaos[i],
                             snap_positions[i][0], snap_positions[i][1],
                             cb_suffixes[i])

        await asyncio.sleep(1.0)

        self.last_write_time = time.time()
        # WxBasic (planet fallback) mirrors zone 1's weather data
        self.zone_wx[0] = new_wxs[0]
        self.psx_send_and_set("WxBasic", new_wxs[0])
        for i, wx in enumerate(new_wxs):
            zone_num = i + 1
            self.zone_wx[zone_num] = wx
            self.psx_send_and_set(f"Wx{zone_num}", wx)

        for i, metar in enumerate(new_metars):
            if metar:
                self.psx_send_and_set(f"Metar{i + 1}", metar)

        self._state_changed_event.set()
        self._apply_msfs_sync()
        self.logger.info("Zone update complete")

    # ------------------------------------------------------------------
    # API state broadcast
    # ------------------------------------------------------------------

    def _build_state_message(self) -> str:  # pylint: disable=too-many-locals
        """Build the FRANKENWEATHER:<uuid>:<json> addon message payload."""
        cfg = self.args
        config = {
            "cruise_behind_dist": cfg.cruise_behind_dist,
            "low_alt_dist": cfg.low_alt_dist,
            "new_zone_infront_range": list(cfg.new_zone_infront_range),
            "new_zone_leftright_range": list(cfg.new_zone_leftright_range),
            "new_zone_notnear": cfg.new_zone_notnear,
            "cape_squeeze": list(cfg.cape_squeeze) if cfg.cape_squeeze else None,
            "fake_cb": list(cfg.fake_cb) if cfg.fake_cb else None,
            "msfs_in_cloud_sync": self._msfs_in_cloud_sync,
            "msfs_qnh_check": self._msfs_qnh_check,
            "msfs_wind_sync": self._msfs_wind_sync,
            "enroute_wind_enabled": self._enroute_wind_enabled,
            "enroute_wind_deviation": self._enroute_wind_deviation,
            "config_file": cfg.config_file,
            "config_file_exists": bool(cfg.config_file and os.path.exists(cfg.config_file)),
        }
        wx_auto = self._fw_mode == "disabled" or self._om_unavailable
        arpt_icaos = {self.fmc_dep_icao, self.fmc_dst_icao} - {None}
        zones = []
        for zone_num in range(1, 8):
            raw_mode = self.psx.get(f"WxMode{zone_num}") if self.psx else None
            pos = parse_wx_zone_position(raw_mode) if raw_mode else None
            if pos is None:
                continue
            lat, lon = pos[0], pos[1]
            mode_parts = raw_mode.split(';')
            icao_raw = mode_parts[5] if len(mode_parts) >= 6 else ""
            icao = icao_raw[:4] if len(icao_raw) >= 4 else ""
            if wx_auto:
                source = "PSX"
                reason = "Set by PSX"
            elif self._fw_mode == "manual":
                source = "MANUAL"
                reason = "Manual weather"
            else:
                source = "VATSIM" if self.zone_is_metar.get(zone_num) else "OM"
                reason = self.zone_reason.get(zone_num, "")
            zones.append({
                "zone": zone_num,
                "icao": icao,
                "lat": round(lat, 4),
                "lon": round(lon, 4),
                "source": source,
                "reason": reason,
                "placement": self._placement_desc(lat, lon, icao,
                                                  is_arpt=icao in arpt_icaos),
                "weather_detail": self.zone_weather_detail.get(zone_num, ""),
            })
        zone_positions = [(z["lat"], z["lon"]) for z in zones]
        _SIGMET_MAX_NM = 100.0
        sigmets = []
        for s in self.ts_sigmets:
            poly = s["polygon"]
            relevant = any(
                self._dist_nm(zlat, zlon, vlat, vlon) <= _SIGMET_MAX_NM
                for zlat, zlon in zone_positions
                for vlat, vlon in poly
            ) or any(
                _point_in_polygon(zlat, zlon, poly)
                for zlat, zlon in zone_positions
            )
            if relevant:
                sigmets.append({
                    "polygon": [[round(la, 4), round(lo, 4)] for la, lo in poly],
                    "top_ft": s["top_ft"],
                })
        state = {
            "fw_mode": self._fw_mode,
            "om_unavailable": self._om_unavailable,
            "mode": "MANEUVERING" if self._maneuvering else "CRUISE",
            "ac_lat": round(self.ac_lat, 4) if self.ac_lat is not None else None,
            "ac_lon": round(self.ac_lon, 4) if self.ac_lon is not None else None,
            "ac_hdg": round(self.ac_hdg, 1) if self.ac_hdg is not None else None,
            "ac_alt_ft": round(self.ac_alt_ft) if self.ac_alt_ft is not None else None,
            "zones": zones,
            "config": config,
            "sigmets": sigmets,
            "manual_wx_params": dict(self._manual_wx_params),
        }
        self._web_state = state
        self._web_state_received_at = time.time()
        payload = json.dumps(state, separators=(',', ':'))
        return f"FRANKENWEATHER:STATE:{self._instance_uuid}:{payload}"

    async def state_broadcast_coro(self) -> None:
        """Broadcast current state as a PSX addon message on change or every 60 seconds."""
        myname = inspect.currentframe().f_code.co_name
        try:
            self.logger.debug("Starting %s", myname)
            while True:
                try:
                    await asyncio.wait_for(self._state_changed_event.wait(), timeout=60.0)
                except asyncio.TimeoutError:
                    pass
                self._state_changed_event.clear()
                if not self.psx_connected:
                    continue
                msg = self._build_state_message()
                self.psx.send("addon", msg)
                self.logger.debug("State broadcast sent (%d bytes)", len(msg))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down", exc, myname)
            self.logger.critical(traceback.format_exc())

    # ------------------------------------------------------------------
    # Coroutines
    # ------------------------------------------------------------------

    def _should_skip_wx_update(self) -> bool:
        """Return True (and log) when the zone weather update should be suppressed."""
        if self._fw_mode in ("paused", "disabled"):
            self.logger.debug("Weather update skipped: fw_mode=%s", self._fw_mode)
            return True
        if self._conflict_uuid is None:
            return False
        age = time.monotonic() - self._conflict_last_seen
        if age < 300.0:
            self.logger.error(
                "CONFLICT: FRANKENWEATHER %s is primary (ours: %s) — "
                "PSX weather changes suspended (last seen %.0fs ago)",
                self._conflict_uuid, self._instance_uuid, age)
            return True
        self.logger.info(
            "Conflict with FRANKENWEATHER %s expired (%.0fs) — resuming",
            self._conflict_uuid, age)
        self._conflict_uuid = None
        return False

    async def weather_update_coro(self) -> None:  # pylint: disable=too-many-branches
        """Periodically update PSX weather zones from Open-Meteo."""
        myname = inspect.currentframe().f_code.co_name
        last_update_time = 0.0
        try:
            self.logger.debug("Starting %s", myname)
            async with aiohttp.ClientSession() as session:
                while True:
                    try:
                        await asyncio.wait_for(self.fmc_changed_event.wait(), timeout=10.0)
                    except asyncio.TimeoutError:
                        pass
                    self.fmc_changed_event.clear()
                    if not self.psx_connected or self.psx_paused:
                        continue
                    if self.ac_lat is None:
                        continue
                    entered_maneuvering = self._update_maneuvering_mode()
                    # When PSX manages its own weather (WxAutoSet=1), don't reposition
                    # zones — PSX will move them and we'd overwrite that on recovery.
                    # The last known zone_positions are preserved for STATE broadcasts.
                    wx_auto = self._fw_mode == "disabled" or self._om_unavailable
                    if wx_auto:
                        any_relocated = False
                    elif not self.zone_positions:
                        self._place_all_zones()
                        any_relocated = True
                    elif any(self._dist_nm(self.ac_lat, self.ac_lon, lat, lon) >
                             _REPOSITION_DIST_NM
                             for _, (lat, lon, _) in self.zone_positions.items()):
                        self.logger.info("Aircraft repositioned — reinitializing weather zones")
                        self._place_all_zones()
                        any_relocated = True
                    elif entered_maneuvering:
                        self._place_all_zones()
                        any_relocated = True
                    else:
                        any_relocated = self._check_and_relocate()
                        any_relocated = self._ensure_arpt_zones() or any_relocated
                    elapsed = time.time() - last_update_time
                    force = self._manual_wx_force_update
                    self._manual_wx_force_update = False
                    if not any_relocated and not force and elapsed < _REFRESH_MAX_S:
                        continue
                    if self._should_skip_wx_update():
                        continue
                    if self._fw_mode == "manual":
                        await self._update_zones_manual()
                    else:
                        await self._update_zones(session)
                    last_update_time = time.time()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down", exc, myname)
            self.logger.critical(traceback.format_exc())

    @staticmethod
    def _ang_diff(a_deg: float, b_deg: float) -> float:
        """Return the shortest signed difference a-b in degrees, in (-180, 180]."""
        return ((a_deg - b_deg + 180.0) % 360.0) - 180.0

    def _build_windstate_dict(self) -> dict:  # pylint: disable=too-many-locals
        """Build the enroute-wind status dict: per-waypoint flight-plan vs OM diff, fetch timers."""
        has_snapshot = self._corridor_snapshot_txt is not None
        snapshot_parseable = (not has_snapshot) or bool(self._corridor_snapshot_waypoints)
        waypoints = []
        for i, (name, lat, lon) in enumerate(self._enroute_waypoints):
            om_levels = self._waypoint_om_wind.get(i, {})
            snap_levels = self._corridor_snapshot_waypoints.get(name, {})
            levels = []
            for fl in sorted(set(om_levels) | set(snap_levels)):
                om = om_levels.get(fl)
                snap = snap_levels.get(fl)
                diff = None
                if om is not None and snap is not None:
                    diff = {
                        "dir_deg": round(self._ang_diff(om[0], snap[0]), 1),
                        "spd_kt": round(om[1] - snap[1], 1),
                        "oat_c": round(om[2] - snap[2], 1),
                    }
                levels.append({
                    "fl_ft": fl,
                    "flightplan": ({"dir_deg": snap[0], "spd_kt": snap[1], "oat_c": snap[2]}
                                   if snap else None),
                    "openmeteo": ({"dir_deg": om[0], "spd_kt": om[1], "oat_c": om[2]}
                                  if om else None),
                    "diff": diff,
                })
            waypoints.append({
                "name": name,
                "lat": round(lat, 4),
                "lon": round(lon, 4),
                "passed": i in self._waypoint_passed,
                "levels": levels,
            })
        return {
            "enabled": self._enroute_wind_enabled,
            "deviation": self._enroute_wind_deviation,
            "has_snapshot": has_snapshot,
            "snapshot_parseable": snapshot_parseable,
            "snapshot_raw": (self._corridor_snapshot_txt
                             if has_snapshot and not snapshot_parseable else None),
            "waypoints": waypoints,
            "last_fetch_epoch": self._enroute_last_fetch_time or None,
            "next_fetch_epoch": self._enroute_next_fetch_time or None,
        }

    def _build_windstate_message(self) -> str:
        """Build the FRANKENWEATHER:WINDSTATE:<uuid>:<json> addon message payload."""
        state = self._build_windstate_dict()
        self._web_windstate = state
        self._web_windstate_received_at = time.time()
        payload = json.dumps(state, separators=(',', ':'))
        return f"FRANKENWEATHER:WINDSTATE:{self._instance_uuid}:{payload}"

    async def windstate_broadcast_coro(self) -> None:
        """Broadcast WINDSTATE to the PSX network when enroute wind state changes."""
        myname = inspect.currentframe().f_code.co_name
        try:
            self.logger.debug("Starting %s", myname)
            while True:
                try:
                    await asyncio.wait_for(self._enroute_wind_changed_event.wait(), timeout=60.0)
                except asyncio.TimeoutError:
                    pass
                self._enroute_wind_changed_event.clear()
                if not self.psx_connected:
                    continue
                if not self._enroute_wind_enabled and not self.route_waypoints:
                    continue
                msg = self._build_windstate_message()
                self.psx.send("addon", msg)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down", exc, myname)
            self.logger.critical(traceback.format_exc())

    async def enroute_wind_coro(self) -> None:
        """Periodically fetch Open-Meteo enroute wind and refresh PSX's WxCorridor.

        Contacts Open-Meteo every _ENROUTE_FETCH_INTERVAL_S ("hourly" background
        drift), plus immediately whenever the FMC route genuinely changes — a
        reroute, not just the aircraft passing a waypoint — since that's the
        only case where a current, non-excluded waypoint can be missing wind
        data (_set_route_waypoints resets _enroute_next_fetch_time to 0 for
        that case). Passing a waypoint alone triggers neither a new OM fetch
        nor a corridor resend: the wind data for every remaining waypoint is
        unchanged, so PSX's corridor is simply left in place rather than
        making PSX recalculate for nothing (_apply_enroute_wind_injection also
        skips the resend on its own if a refetch happens to return identical
        data). Opt-in via _enroute_wind_enabled, and mutually exclusive with MSFS
        wind sync.
        """
        myname = inspect.currentframe().f_code.co_name
        try:
            self.logger.debug("Starting %s", myname)
            while True:
                await asyncio.sleep(15.0)
                if not self._enroute_wind_enabled or not self.psx_connected:
                    continue
                if not self._enroute_waypoints:
                    continue
                if time.time() < self._enroute_next_fetch_time:
                    continue
                targets = [
                    (i, name, lat, lon)
                    for i, (name, lat, lon) in enumerate(self._enroute_waypoints)
                    if i not in self._waypoint_passed
                ]
                await self._fetch_enroute_om_batch(targets)
                self._apply_enroute_wind_injection()
                now = time.time()
                self._enroute_last_fetch_time = now
                self._enroute_next_fetch_time = now + _ENROUTE_FETCH_INTERVAL_S
                self._enroute_wind_changed_event.set()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down", exc, myname)
            self.logger.critical(traceback.format_exc())

    async def get_psx_connection_coro(self) -> None:
        """Maintain PSX connection."""
        myname = inspect.currentframe().f_code.co_name
        try:
            self.logger.debug("Starting %s", myname)

            def connected(*_):
                self.logger.info("PSX CONNECTED")
                self.psx_connected = True
                self.psx.send("name", f"{__MY_CLIENT_ID__}:{__MY_DISPLAY_NAME__}")
                self._state_changed_event.set()
                self._turb_state_changed_event.set()
                if self._fw_mode == "disabled":
                    self.psx_send_and_set("WxAutoSet", "1")
                else:
                    self._sync_psx_clock()

            def disconnected():
                self.logger.info("PSX DISCONNECTED")
                self.psx_connected = False

            def onresume():
                self.logger.info("PSX RESUMED")
                self.psx_connected = True
                self.psx_paused = False
                self._turb_state_changed_event.set()

            self.psx = psx.Client()
            self.psx.onPause = lambda: setattr(self, 'psx_paused', True)
            self.psx.onDisconnect = disconnected
            self.psx.onConnect = lambda: None
            self.psx.onResume = onresume

            self.psx.subscribe("id")
            self.psx.subscribe("version", connected)
            self.psx.subscribe("PiBaHeAlTas", self.handle_piba)
            self.psx.subscribe("WxBasic", self.handle_wx_change)
            self.psx.subscribe("FocussedWxZone", self.handle_focused_zone)
            self.psx.subscribe("FmcRteViAcMo", self.handle_fmc_change)
            self.psx.subscribe("FmcRte1", self.handle_fmc_change)
            self.psx.subscribe("FmcRte2", self.handle_fmc_change)
            self.psx.subscribe("WxSigmet", self.handle_sigmet_change)
            self.psx.subscribe("addon", self._handle_addon)
            self.psx.subscribe("WxCorridorTxt", self._handle_corridor)

            for i in range(1, 8):
                self.psx.subscribe(f"Wx{i}", self.handle_wx_change)
                self.psx.subscribe(f"WxMode{i}")

            # Turbulence subsystem subscriptions
            self.psx.subscribe("TimeEarth")
            self.psx.subscribe("WxClust")
            self.psx.subscribe("AcftHeight")

            await self.psx.connect(self.args.psx_host, self.args.psx_port)
            self.logger.warning("psx.connect() returned — this should not happen")

        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down", exc, myname)
            self.logger.critical(traceback.format_exc())

    async def monitor_coro(self) -> None:
        """Monitor coroutines and restart them if they exit."""
        myname = inspect.currentframe().f_code.co_name
        try:
            self.logger.debug("Starting %s", myname)
            while True:
                running = []
                ended_tasks: set = set()
                for task in self.tasks:
                    if task.done():
                        ended_tasks.add(task)
                        exc = task.exception()
                        if exc:
                            self.logger.info("Task %s ended: %s", task.get_name(), exc)
                        else:
                            self.logger.info("Task %s ended peacefully", task.get_name())
                    else:
                        running.append(task.get_name())
                for task in ended_tasks:
                    self.tasks.discard(task)

                coros = [
                    ("PSXConnection", self.get_psx_connection_coro),
                    ("WeatherUpdate", self.weather_update_coro),
                    ("EnrouteWind", self.enroute_wind_coro),
                    ("StateBroadcast", self.state_broadcast_coro),
                    ("WindStateBroadcast", self.windstate_broadcast_coro),
                ]
                coros += [
                    ("TurbulenceTask", self.turbulence_coro),
                    ("PSXWind", self.psx_wind_coro),
                    ("TurbBroadcast", self.turb_state_broadcast_coro),
                ]
                for name, coro_fn in coros:
                    if name not in running:
                        self.logger.info("Starting %s...", name)
                        task = self.taskgroup.create_task(coro_fn(), name=name)
                        self.tasks.add(task)

                self.logger.debug("Running tasks: %s", running)
                await asyncio.sleep(5.0)

        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down", exc, myname)
            self.logger.critical(traceback.format_exc())

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def handle_args(self) -> None:
        """Parse command line arguments."""
        parser = argparse.ArgumentParser(
            prog=__MYNAME__,
            description=__MY_DESCRIPTION__,
            formatter_class=argparse.ArgumentDefaultsHelpFormatter)
        parser.add_argument(
            '--psx-host', type=str, default='127.0.0.1',
            help="Hostname or IP of the PSX server.")
        parser.add_argument(
            '--psx-port', type=int, default=10747,
            help="Port of the PSX server.")
        parser.add_argument(
            '--stations', type=str, default=None,
            help="UCAR stations.txt file (downloads from UCAR if not given).")
        parser.add_argument(
            '--cruise-behind-dist', type=float, default=50.0, metavar='NM',
            help="In cruise: relocate a zone more than this many nm behind the aircraft.")
        parser.add_argument(
            '--low-alt-dist', type=float, default=200.0, metavar='NM',
            help="Below cruise alt: relocate any zone more than this many nm away.")
        parser.add_argument(
            '--new-zone-infront-range', type=_parse_nm_range, default=(150.0, 250.0),
            metavar='MIN,MAX',
            help="Forward range in nm when placing a new zone ahead (e.g. 150,250).")
        parser.add_argument(
            '--new-zone-leftright-range', type=_parse_nm_range, default=(0.0, 100.0),
            metavar='MIN,MAX',
            help="Lateral range in nm left or right of track for a new zone (e.g. 0,100).")
        parser.add_argument(
            '--new-zone-notnear', type=float, default=50.0, metavar='NM',
            help="Minimum separation in nm from existing zones; retry up to 10 times.")
        parser.add_argument(
            '--cape-squeeze', type=_parse_cape_squeeze, default=(500.0, 50.0),
            metavar='CAPE:MIN_FWD',
            help="Squeeze zone spacing when CAPE is high: at avg CAPE >= CAPE J/kg "
                 "shrink fwd_max to MIN_FWD nm (default: 500:50).")
        parser.add_argument(
            '--fake-cb', type=_parse_cb_arg, default=None, metavar='O:B:T',
            help="Override CB in all zones: O=oktas (0-8), B=base ft, T=top ft "
                 "(e.g. --fake-cb=6:3000:45000).")
        parser.add_argument(
            '--om-proxy', type=str, default=None, metavar='URL',
            help="Proxy URL for all Open-Meteo requests, e.g. socks5h://localhost:1080."
                 " Requires PySocks (pip install requests[socks]).")
        parser.add_argument(
            '--debug', action='store_true',
            help="Log PSX weather changes, every value sent to PSX, and all PSX traffic.")
        parser.add_argument(
            '--save-logs', type=str, default=None, metavar='DIR',
            help="[DEVELOPMENT] Directory to save enroute-wind flight-plan-vs-Open-Meteo "
                 "diff data to as timestamped JSON, one file per flight, updated on every "
                 "WxCorridor refresh while the enroute wind importer is enabled.")
        parser.add_argument(
            '--config-file', type=str, default=_DEFAULT_CONFIG_FILE, metavar='PATH',
            help="TOML file for every setting the web GUI can change (MSFS sync, enroute "
                 "wind, manual weather, turbulence). Loaded at startup if it exists; "
                 "otherwise defaults are used until you save from the web UI (nothing is "
                 "written until then). See docs/frankenweather.md for the file format.")

        parser.add_argument(
            '--web-port', type=int, default=None, metavar='PORT',
            help="Enable standalone web UI on this TCP port (e.g. 8085).")

        # Removed options, kept as accepted-but-ignored so old startup scripts still run;
        # handle_args() logs a deprecation warning for each one actually passed.
        parser.add_argument(
            '--turb-config-file', type=str, default=None, metavar='PATH',
            help="[REMOVED] Turbulence settings are now part of --config-file.")
        parser.add_argument(
            '--cruise-alt', type=float, default=None, metavar='FT',
            help="[REMOVED] No longer used.")
        parser.add_argument(
            '--arpt-zone-dist', type=float, default=None, metavar='NM',
            help="[REMOVED] No longer used.")
        parser.add_argument(
            '--msfs-in-cloud-sync', action='store_true',
            help="[REMOVED] Now a runtime toggle on the /weather/settings web page.")
        parser.add_argument(
            '--msfs-qnh-check', choices=('CHECK', 'USE'), default=None,
            help="[REMOVED] Now a runtime toggle on the /weather/settings web page.")
        parser.add_argument(
            '--msfs-qnh-check-maxdiff', type=float, default=None, metavar='HPA',
            help="[REMOVED] No longer used.")
        parser.add_argument(
            '--msfs-wind-sync', action='store_true',
            help="[REMOVED] Now a runtime toggle on the /weather/settings web page.")
        parser.add_argument(
            '--disable-psx-weather-updates', action='store_true',
            help="[REMOVED] No longer supported.")
        parser.add_argument(
            '--no-turbulence', action='store_true',
            help="[REMOVED] The turbulence subsystem can no longer be disabled entirely.")
        parser.add_argument(
            '--turb-rate', type=int, default=None, metavar='0-100',
            help="[REMOVED] No longer used.")
        parser.add_argument(
            '--turb-intensity-bias', type=int, default=None, metavar='0-999',
            help="[REMOVED] No longer used.")
        self.args = parser.parse_args()

    # (arg_name, replacement_hint or None for the generic "just remove it" message)
    _REMOVED_ARGS = (
        ('cruise_alt', None),
        ('arpt_zone_dist', None),
        ('msfs_in_cloud_sync', None),
        ('msfs_qnh_check', None),
        ('msfs_qnh_check_maxdiff', None),
        ('msfs_wind_sync', None),
        ('disable_psx_weather_updates', None),
        ('no_turbulence', None),
        ('turb_rate', None),
        ('turb_intensity_bias', None),
        ('turb_config_file', 'turbulence settings are now part of --config-file'),
    )

    def _warn_removed_args(self) -> None:
        """Log a deprecation warning for each removed CLI option actually passed."""
        for name, hint in self._REMOVED_ARGS:
            value = getattr(self.args, name)
            if value:
                suffix = f"; {hint}" if hint else "; remove it from your startup script"
                self.logger.warning(
                    "--%s is deprecated and no longer has any effect%s",
                    name.replace('_', '-'), suffix)

    async def run(self) -> None:
        """Entry point."""
        self.handle_args()

        logging.basicConfig(
            format="%(asctime)s: %(message)s",
            level=logging.INFO,
            datefmt="%H:%M:%S",
            handlers=[logging.StreamHandler(sys.stdout)])
        self.logger = logging.getLogger(__MYNAME__)
        if self.args.debug:
            self.logger.setLevel(logging.DEBUG)
        self._warn_removed_args()

        if self.args.stations:
            self.airports = load_airports(self.args.stations)
            self.logger.info("Loaded %d airports from %s",
                             len(self.airports), self.args.stations)
        else:
            self.airports, source = get_airports(_UCAR_STATIONS_URL, _STATIONS_CACHE)
            self.logger.info("Loaded %d airports from %s", len(self.airports), source)

        self._turb_engine = TurbulenceEngine(om_proxy=self.args.om_proxy)
        self._turb_pirep_fetcher = PirepFetcher()
        self._turb_cape_fetcher = CapeFetcher(proxy=self.args.om_proxy)
        self._turb_gairmet_fetcher = GairmetFetcher()
        self.logger.info("Turbulence engine initialized")
        self._load_config_file()
        # Trigger initial TURBSTATE broadcast so the router has config data immediately.
        self._turb_state_changed_event.set()

        async with asyncio.TaskGroup() as self.taskgroup:
            task = self.taskgroup.create_task(self.monitor_coro(), name="Monitor")
            self.tasks.add(task)
            if self.args.web_port:
                task = self.taskgroup.create_task(
                    self.run_web_ui_coro(), name="WebUI")
                self.tasks.add(task)
            print("All tasks created")
        print("All tasks completed")

    async def run_web_ui_coro(self) -> None:
        """Run a standalone aiohttp web server exposing the FrankenWeather UI."""
        from aiohttp import web as _web  # pylint: disable=import-outside-toplevel
        port = self.args.web_port
        ctx = StandaloneFWContext(self)
        routes = _web.RouteTableDef()

        @routes.get('/')
        async def _home(_):
            raise _web.HTTPFound('/weather')

        _fw_webui.register_weather_routes(routes, ctx)

        static_path = pathlib.Path(__file__).parent / 'router' / 'frankenrouter' / 'static'

        @_web.middleware
        async def _cors(request, handler):
            response = await handler(request)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response

        app = _web.Application(middlewares=[_cors])
        app.add_routes(routes)
        if static_path.is_dir():
            app.router.add_static('/static', static_path)
        runner = _web.AppRunner(app)
        await runner.setup()
        site = _web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        self.logger.info("Standalone web UI running on http://0.0.0.0:%d/", port)
        try:
            while True:
                await asyncio.sleep(3600.0)
        except asyncio.CancelledError:
            await runner.cleanup()
            raise


if __name__ == '__main__':
    try:
        asyncio.run(Script().run())
    except Exception:  # pylint: disable=broad-exception-caught
        traceback.print_exc()
        input("An error occurred, press Enter to continue...")
    except SystemExit as exc:
        if exc.code not in (None, 0):
            input("An error occurred, press Enter to continue...")
