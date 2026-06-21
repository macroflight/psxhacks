"""FrankenWeather - Dynamic real-world weather zones for PSX."""
# pylint: disable=invalid-name,duplicate-code,too-many-lines
import argparse
import asyncio
import inspect
import json
import logging
import math
import os
import random
import re
import sys
import time
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


__MYNAME__ = 'frankenweather'
__MY_CLIENT_ID__ = 'FWXR'
__MY_DISPLAY_NAME__ = 'FrankenWeather'
__MY_DESCRIPTION__ = 'Dynamic real-world weather zones for PSX using Open-Meteo'

_OM_URL = "https://api.open-meteo.com/v1/forecast"
_OM_VARS = (
    "temperature_2m,relative_humidity_2m,weather_code,cloud_cover,pressure_msl,"
    "wind_speed_10m,wind_direction_10m,wind_gusts_10m,visibility"
)

_UCAR_STATIONS_URL = "https://weather.rap.ucar.edu/surface/stations.txt"
_STATIONS_CACHE = os.path.join(os.path.expanduser("~"), ".cache", "frankenweather", "stations.txt")
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
    if 'TS' in token:
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

        # Parsed TS SIGMETs used to lift WMO/showers CB suppression when CAPE agrees
        self.ts_sigmets: list = []

        # MSFS bridge state (--msfs-in-cloud-sync / --msfs-qnh-check via frankenmsfsbridge)
        self.msfs_in_cloud: Optional[bool] = None
        self.msfs_qnh_hpa: Optional[float] = None
        self._msfs_bridge_last_seen: Optional[float] = None
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
        self.zone_reason: dict = {}               # zone_num → human-readable reason string
        self._state_changed_event: asyncio.Event = asyncio.Event()

        # Conflict detection — suspend PSX changes when a higher-UUID instance is present
        self._conflict_uuid: Optional[str] = None
        self._conflict_last_seen: float = 0.0

        # Operational mode: "enabled" | "paused" | "disabled"
        # paused  = stop updating PSX weather; keep WxAutoSet=0 (existing zones remain)
        # disabled = stop updating; set WxAutoSet=1 (PSX resumes its own auto-weather)
        self._fw_mode: str = "enabled"
        # True when OM is temporarily unavailable; WxAutoSet=1 until it recovers.
        self._om_unavailable: bool = False

        # -------------------------------------------------------------------
        # Turbulence subsystem (merged from frankenturb)
        # -------------------------------------------------------------------
        self._turb_enabled: bool = True
        self._turb_intensity_bias: int = 100   # 0-999 %
        self._turb_wind_mode: str = "live"     # "live", "psx", or "manual"
        self._turb_manual_wind_dir: int = 0
        self._turb_manual_wind_spd: int = 0
        self._turb_psx_wind = None             # (dir_deg, speed_kt) or None
        self._turb_lateral_size_bias: int = 50
        self._turb_rate: int = 100             # 0-100, injection rate scale
        self._turb_type_enabled: dict = {k: True for k in _TURB_TYPES}
        self._turb_type_biases: dict = {k: 100 for k in _TURB_TYPES}
        self._turb_engine: Optional[TurbulenceEngine] = None
        self._turb_state: Optional[TurbulenceState] = None
        self._turb_sources: list = []
        self._turb_print_count: int = 0
        self._turb_state_changed_event: asyncio.Event = asyncio.Event()
        self._turb_pirep_fetcher = None
        self._turb_cape_fetcher = None
        self._turb_gairmet_fetcher = None

    # ------------------------------------------------------------------
    # PSX helpers
    # ------------------------------------------------------------------

    def psx_send_and_set(self, key: str, value: str) -> None:
        """Send a PSX key=value and update the local variable cache."""
        self.logger.debug("→ PSX %s = %s", key, value)
        self.psx.send(key, value)
        self.psx._set(key, value)  # pylint: disable=protected-access

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
        if self.args.disable_psx_weather_updates:
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
        self.fmc_changed_event.set()

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
        if "oat_c" in data:
            self.msfs_oat_c = float(data["oat_c"])
        if "wind_dir" in data:
            self.msfs_wind_dir = float(data["wind_dir"])
        if "wind_spd" in data:
            self.msfs_wind_spd = float(data["wind_spd"])
        self._msfs_bridge_last_seen = time.monotonic()
        if changed:
            self._apply_msfs_sync()
        if self.args.msfs_wind_sync:
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

    def _handle_fw_command(self, json_str: str) -> None:
        """Apply a FRANKENWEATHER:COMMAND message received via PSX addon."""
        try:
            cmd = json.loads(json_str)
        except ValueError:
            self.logger.warning("Malformed FRANKENWEATHER COMMAND: %s", json_str[:80])
            return
        new_mode = cmd.get("mode")
        if new_mode not in ("enabled", "paused", "disabled"):
            self.logger.warning("FRANKENWEATHER COMMAND: unknown mode %r", new_mode)
            return
        old_mode = self._fw_mode
        if new_mode == old_mode:
            return
        self._fw_mode = new_mode
        self.logger.info("FRANKENWEATHER mode: %s → %s (via COMMAND)", old_mode, new_mode)
        if self.psx_connected:
            if new_mode == "disabled":
                self.psx_send_and_set("WxAutoSet", "1")
            elif old_mode == "disabled":
                self.psx_send_and_set("WxAutoSet", "0")
        self._state_changed_event.set()

    # ------------------------------------------------------------------
    # Turbulence subsystem
    # ------------------------------------------------------------------

    def _turb_type_effective_bias(self, kind: str) -> int:
        """Return combined bias for a turbulence type: 0 when disabled."""
        if not self._turb_type_enabled.get(kind, True):
            return 0
        return self._turb_type_biases.get(kind, 100)

    def _turb_load_config(self, path: str) -> None:
        """Load turbulence settings from JSON config file."""
        import pathlib  # pylint: disable=import-outside-toplevel
        try:
            with open(path, encoding="utf-8") as fh:
                cfg = json.load(fh)
        except FileNotFoundError:
            p = pathlib.Path(path)
            if p.parent.exists():
                p.write_text("{}\n", encoding="utf-8")
            return
        except Exception as exc:  # pylint: disable=broad-except
            self.logger.warning("Turb config load failed: %s", exc)
            return
        if "turb_enabled" in cfg:
            self._turb_enabled = bool(cfg["turb_enabled"])
        if "intensity_bias" in cfg:
            self._turb_intensity_bias = int(cfg["intensity_bias"])
        if "lateral_size_bias" in cfg:
            self._turb_lateral_size_bias = int(cfg["lateral_size_bias"])
        if "wind_mode" in cfg:
            self._turb_wind_mode = str(cfg["wind_mode"])
        if "manual_wind_dir" in cfg:
            self._turb_manual_wind_dir = int(cfg["manual_wind_dir"])
        if "manual_wind_spd" in cfg:
            self._turb_manual_wind_spd = int(cfg["manual_wind_spd"])
        for kind in _TURB_TYPES:
            if "type_biases" in cfg and kind in cfg["type_biases"]:
                self._turb_type_biases[kind] = int(cfg["type_biases"][kind])
            if "type_enabled" in cfg and kind in cfg["type_enabled"]:
                self._turb_type_enabled[kind] = bool(cfg["type_enabled"][kind])
        self.logger.info("Loaded turb config from %s", path)

    def _turb_save_config(self, path: str) -> None:
        """Save turbulence settings to JSON config file."""
        cfg = {
            "turb_enabled": self._turb_enabled,
            "intensity_bias": self._turb_intensity_bias,
            "lateral_size_bias": self._turb_lateral_size_bias,
            "wind_mode": self._turb_wind_mode,
            "manual_wind_dir": self._turb_manual_wind_dir,
            "manual_wind_spd": self._turb_manual_wind_spd,
            "type_biases": dict(self._turb_type_biases),
            "type_enabled": dict(self._turb_type_enabled),
        }
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(cfg, fh, indent=2)
                fh.write("\n")
        except Exception as exc:  # pylint: disable=broad-except
            self.logger.warning("Turb config save failed: %s", exc)
            return
        self.logger.info("Saved turb config to %s", path)

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
        if changed:
            if self.args.turb_config_file:
                self._turb_save_config(self.args.turb_config_file)
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
                }
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

                if tas_kt < 30.0:
                    continue

                if self._turb_engine is None:
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

                effective_intensity = min(
                    1.0,
                    state.intensity * self._turb_intensity_bias *
                    self._turb_type_effective_bias(state.kind) / 10000.0,
                )
                if self._turb_enabled and effective_intensity >= 0.01:
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
        """Cache the current PSX wind corridor text for wind injection."""
        self._corridor_txt = value

    def _apply_wind_injection(self) -> None:
        """Inject MSFS wind into the PSX wind corridor as a FWIND waypoint."""
        if not self.args.msfs_wind_sync:
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
        need_cloud = self.args.msfs_in_cloud_sync and self.msfs_in_cloud is not None
        need_qnh = bool(self.args.msfs_qnh_check)
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
            if abs(diff) > self.args.msfs_qnh_check_maxdiff:
                if self.args.msfs_qnh_check == "USE":
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
            self.logger.info(
                "Zone %d: initial placement at %s @ %.3f/%.3f  %.0f°/%.0fnm (dep/dst arpt)",
                next_zone, icao, lat, lon, az % 360, dist_m / _NM_TO_M)
            next_zone += 1
        for zone_num in range(next_zone, 8):
            lat, lon, icao = self._pick_position(initial=True)
            self.zone_positions[zone_num] = (lat, lon, icao)
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
                     self.ac_alt_ft >= self.args.cruise_alt)
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
        if self.ac_lat is None or not getattr(self.args, 'arpt_zone_dist', 0):
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
            if dist <= self.args.arpt_zone_dist:
                result.append((icao, lat, lon))
            else:
                self.logger.debug("FMC arpt %s too far (%.0fnm > %.0fnm limit)",
                                  icao, dist, self.args.arpt_zone_dist)
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
                if not self.args.disable_psx_weather_updates:
                    self.psx_send_and_set("WxAutoSet", "1")
                self._state_changed_event.set()
            return
        if self._om_unavailable:
            self._om_unavailable = False
            self.logger.info("Open-Meteo available again — resuming FrankenWeather")
            if not self.args.disable_psx_weather_updates:
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
        for i in range(7):
            lat, lon = snap_positions[i]
            icao = snap_icaos[i]
            om = om_by_zone.get(i, {})
            radar_echo = self._rv_echo_at(lat, lon)
            has_lightning = self._bz_near(lat, lon)
            if raw_metars[i] is not None:
                parsed = _parse_metar(raw_metars[i])
                new_modes.append(build_wxmode_string(lat, lon, 0.0, month, icao))
                wx = metar_to_wx_string(parsed)
                ts_oktas = parsed.get('ts_oktas', 0)
                has_showers = parsed.get('showers', False)
                need_cb = ts_oktas > 0 or has_showers or radar_echo >= 2 or has_lightning
                if om and need_cb:
                    refined = _apply_om_cb(
                        wx, om,
                        metar_showers=has_showers,
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
            new_wxs.append(self._sigmet_cb_override(wx, (lat, lon), om, f"Zone {i + 1} {icao}"))

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

            # Build zone reason for API broadcast
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

        if self.args.disable_psx_weather_updates:
            self.logger.info("PSX updates disabled — logging zones only")
            for i in range(7):
                src = "VATSIM" if raw_metars[i] else "Open-Meteo"
                self.logger.info("Zone %d (dry-run) [%s]: %s @ %.3f/%.3f%s  wx=%s",
                                 i + 1, src, snap_icaos[i],
                                 snap_positions[i][0], snap_positions[i][1],
                                 cb_suffixes[i], new_wxs[i])
        else:
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
            "cruise_alt": cfg.cruise_alt,
            "cruise_behind_dist": cfg.cruise_behind_dist,
            "low_alt_dist": cfg.low_alt_dist,
            "new_zone_infront_range": list(cfg.new_zone_infront_range),
            "new_zone_leftright_range": list(cfg.new_zone_leftright_range),
            "new_zone_notnear": cfg.new_zone_notnear,
            "cape_squeeze": list(cfg.cape_squeeze) if cfg.cape_squeeze else None,
            "arpt_zone_dist": cfg.arpt_zone_dist,
            "fake_cb": list(cfg.fake_cb) if cfg.fake_cb else None,
            "disable_psx_weather_updates": cfg.disable_psx_weather_updates,
            "msfs_in_cloud_sync": cfg.msfs_in_cloud_sync,
            "msfs_qnh_check": cfg.msfs_qnh_check,
            "msfs_qnh_check_maxdiff": cfg.msfs_qnh_check_maxdiff,
            "msfs_wind_sync": cfg.msfs_wind_sync,
        }
        wx_auto = self._fw_mode == "disabled" or self._om_unavailable
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
            })
        sigmets = [
            {"polygon": [[round(la, 4), round(lo, 4)] for la, lo in s["polygon"]],
             "top_ft": s["top_ft"]}
            for s in self.ts_sigmets
        ]
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
        }
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

    async def weather_update_coro(self) -> None:
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
                    if not any_relocated and elapsed < _REFRESH_MAX_S:
                        continue
                    if self._should_skip_wx_update():
                        continue
                    await self._update_zones(session)
                    last_update_time = time.time()
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
                if not self.args.no_turbulence:
                    self._turb_state_changed_event.set()
                if self._fw_mode == "disabled":
                    self.psx_send_and_set("WxAutoSet", "1")

            def disconnected():
                self.logger.info("PSX DISCONNECTED")
                self.psx_connected = False

            def onresume():
                self.logger.info("PSX RESUMED")
                self.psx_connected = True
                self.psx_paused = False
                if not self.args.no_turbulence:
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
            if not self.args.no_turbulence:
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
                    ("StateBroadcast", self.state_broadcast_coro),
                ]
                if not self.args.no_turbulence:
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
            '--cruise-alt', type=float, default=18000.0, metavar='FT',
            help="Altitude in feet above which cruise relocation rules apply.")
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
            '--arpt-zone-dist', type=float, default=200.0, metavar='NM',
            help="Always assign a weather zone to the FMC dep/dst airport "
                 "if within this distance (0 to disable).")
        parser.add_argument(
            '--msfs-in-cloud-sync', action='store_true',
            help="Adjust PSX cloud layers to match MSFS in-cloud state "
                 "(data supplied by frankenmsfsbridge).")
        parser.add_argument(
            '--msfs-qnh-check', choices=('CHECK', 'USE'), default=None,
            metavar='CHECK|USE',
            help="CHECK: warn when MSFS QNH (from frankenmsfsbridge) differs from the "
                 "active PSX zone by more than --msfs-qnh-check-maxdiff. "
                 "USE: also update the PSX QNH and METAR to match.")
        parser.add_argument(
            '--msfs-qnh-check-maxdiff', type=float, default=2.0, metavar='HPA',
            help="QNH difference threshold in hPa for --msfs-qnh-check.")
        parser.add_argument(
            '--msfs-wind-sync', action='store_true',
            help="Inject MSFS wind at current altitude into the PSX wind corridor as "
                 "a FWIND waypoint (via frankenmsfsbridge).")
        parser.add_argument(
            '--fake-cb', type=_parse_cb_arg, default=None, metavar='O:B:T',
            help="Override CB in all zones: O=oktas (0-8), B=base ft, T=top ft "
                 "(e.g. --fake-cb=6:3000:45000).")
        parser.add_argument(
            '--disable-psx-weather-updates', action='store_true',
            help="Fetch and log weather data but do not write anything to PSX.")
        parser.add_argument(
            '--om-proxy', type=str, default=None, metavar='URL',
            help="Proxy URL for all Open-Meteo requests, e.g. socks5h://localhost:1080."
                 " Requires PySocks (pip install requests[socks]).")
        parser.add_argument(
            '--debug', action='store_true',
            help="Log PSX weather changes, every value sent to PSX, and all PSX traffic.")
        parser.add_argument(
            '--no-turbulence', action='store_true',
            help="Disable the turbulence subsystem entirely.")
        parser.add_argument(
            '--turb-rate', type=int, default=100, metavar='0-100',
            help="Scale turbulence injection frequency (100=normal up to 5 Hz).")
        parser.add_argument(
            '--turb-intensity-bias', type=int, default=100, metavar='0-999',
            help="Global turbulence intensity bias percentage (100=normal).")
        parser.add_argument(
            '--turb-config-file', type=str, default=None, metavar='PATH',
            help="JSON file for persisting turbulence settings.")
        self.args = parser.parse_args()

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

        if self.args.stations:
            self.airports = load_airports(self.args.stations)
            self.logger.info("Loaded %d airports from %s",
                             len(self.airports), self.args.stations)
        else:
            self.airports, source = get_airports(_UCAR_STATIONS_URL, _STATIONS_CACHE)
            self.logger.info("Loaded %d airports from %s", len(self.airports), source)

        if not self.args.no_turbulence:
            self._turb_rate = max(0, min(100, self.args.turb_rate))
            self._turb_intensity_bias = max(0, min(999, self.args.turb_intensity_bias))
            if self.args.turb_config_file:
                self._turb_load_config(self.args.turb_config_file)
            self._turb_engine = TurbulenceEngine(om_proxy=self.args.om_proxy)
            self._turb_pirep_fetcher = PirepFetcher()
            self._turb_cape_fetcher = CapeFetcher(proxy=self.args.om_proxy)
            self._turb_gairmet_fetcher = GairmetFetcher()
            self.logger.info("Turbulence engine initialized")
            # Trigger initial TURBSTATE broadcast so the router has config data immediately.
            self._turb_state_changed_event.set()

        async with asyncio.TaskGroup() as self.taskgroup:
            task = self.taskgroup.create_task(self.monitor_coro(), name="Monitor")
            self.tasks.add(task)
            print("All tasks created")
        print("All tasks completed")


if __name__ == '__main__':
    try:
        asyncio.run(Script().run())
    except Exception:  # pylint: disable=broad-exception-caught
        traceback.print_exc()
        input("An error occurred, press Enter to continue...")
    except SystemExit as exc:
        if exc.code not in (None, 0):
            input("An error occurred, press Enter to continue...")
