"""fw_scanner.py — automated CB-detection quality scanner for FrankenWeather.

Fetches data from multiple independent sources for a global grid, runs
FrankenWeather's CB prediction logic at each point, and reports discrepancies.

Ground-truth evidence sources (used together as consensus):
  1. Open-Meteo WMO weather code  (direct thunderstorm observation)
  2. Open-Meteo CAPE / showers    (instability + confirmed precip)
  3. RainViewer global radar      (composite reflectivity, colour-decoded)
  4. AWC international TS SIGMETs (GeoJSON polygon coverage)
  5. AWC METAR TS weather         (nearby stations reporting TS/TSRA/GR/LTG)

A point is flagged when the consensus from these sources disagrees with what
FrankenWeather would predict from the same OM data.

Requires: aiohttp, Pillow, requests  (pip install aiohttp pillow "requests[socks]")
Usage:    python3 fw_scanner.py --help
"""
# pylint: disable=too-many-locals,broad-exception-caught
import argparse
import asyncio
import csv
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from typing import List, Optional, Tuple

import aiohttp

from fw_cb import om_cb_fields

try:
    from PIL import Image
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False

# ---------------------------------------------------------------------------
# External API endpoints
# ---------------------------------------------------------------------------

_OM_API = "https://api.open-meteo.com/v1/forecast"
_RV_API = "https://api.rainviewer.com/public/weather-maps.json"
_RV_TILE = "https://tilecache.rainviewer.com"
_AWC_SIGMET = "https://aviationweather.gov/api/data/isigmet?format=geojson"
_AWC_STATION = "https://aviationweather.gov/api/data/stationinfo"
_VATSIM_METAR = "https://metar.vatsim.net/metar.php?id=all"
_TS_MARKERS = ('TS', 'GR', 'LTG', 'FC')
_TIMEOUT = aiohttp.ClientTimeout(total=30)
_OM_CONCURRENCY = 1     # serial batches — avoids stampede when retrying after 429
_OM_DELAY = 0.15        # seconds between batch requests
_OM_BATCH_SIZE = 50     # grid points per HTTP request (OM supports multi-location batching)
_OM_RETRY_WAIT = 62     # seconds to pause on 429 before retrying (quota resets per minute)

# Radar tile parameters
_ZOOM = 6               # zoom 6 → ~22 km/pixel at equator
_TILE_PX = 256
_ECHO_RADIUS = 3        # pixels around target to sample


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class GridPoint:
    """A lat/lon point on the scan grid."""

    lat: float
    lon: float


@dataclass
class OmPoint:
    """Open-Meteo data extracted for one grid point."""

    wmo_code: int
    cape: float
    cin: float
    showers_mm: float
    temp_c: float
    dp_c: float
    raw: dict = field(repr=False)


@dataclass
class Evidence:
    """Multi-source CB evidence for one grid point."""

    om_wmo_ts: bool         # OM WMO 95/96/99 = direct thunderstorm observation
    om_cape_showers: bool   # CAPE + showers alone would predict CBs
    radar_echo: int         # RainViewer echo strength 0 (none) – 3 (heavy)
    in_sigmet: bool         # inside an active TS SIGMET polygon
    nearby_metar_ts: bool   # TSRA/GR/LTG METAR within 50 nm

    @property
    def score(self) -> int:
        """Number of independent sources indicating convective activity."""
        return sum([
            self.om_wmo_ts,
            self.om_cape_showers,
            self.radar_echo >= 2,
            self.in_sigmet,
            self.nearby_metar_ts,
        ])

    @property
    def consensus_cb(self) -> bool:
        """At least two independent sources agree CBs are present."""
        return self.score >= 2


@dataclass
class ScanResult:
    """Comparison of FW prediction against ground-truth evidence."""

    point: GridPoint
    om: Optional[OmPoint]
    ev: Optional[Evidence]
    fw_cb_oktas: int
    verdict: str    # OK | FALSE_NEG | FALSE_POS | NO_DATA


# ---------------------------------------------------------------------------
# Geo helpers
# ---------------------------------------------------------------------------

def _gcd_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in nautical miles."""
    r = 3440.065
    la1, lo1, la2, lo2 = (math.radians(x) for x in (lat1, lon1, lat2, lon2))
    d_la, d_lo = la2 - la1, lo2 - lo1
    a = math.sin(d_la / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(d_lo / 2) ** 2
    return r * 2 * math.asin(math.sqrt(a))


def _point_in_polygon(lat: float, lon: float, poly: list) -> bool:
    """Ray-casting point-in-polygon test (lat/lon coordinates)."""
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > lon) != (yj > lon) and lat < (xj - xi) * (lon - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _tile_xy(lat: float, lon: float, zoom: int) -> Tuple[int, int]:
    """Convert lat/lon to slippy-map tile x/y at given zoom."""
    n = 2 ** zoom
    tx = int((lon + 180) / 360 * n)
    lat_r = math.radians(lat)
    ty = int((1 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2 * n)
    return tx, ty


def _pixel_in_tile(lat: float, lon: float, zoom: int) -> Tuple[int, int]:
    """Pixel (x, y) within the tile that contains this lat/lon."""
    n = 2 ** zoom
    lat_r = math.radians(lat)
    px = int(((lon + 180) / 360 * n * _TILE_PX) % _TILE_PX)
    log_term = math.log(math.tan(lat_r) + 1 / math.cos(lat_r))
    py = int(((1 - log_term / math.pi) / 2 * n * _TILE_PX) % _TILE_PX)
    return px, py


# ---------------------------------------------------------------------------
# Radar helpers (RainViewer colour scheme 2)
# ---------------------------------------------------------------------------

def _pixel_echo_strength(r: int, g: int, b: int, a: int) -> int:
    """Map an RGBA pixel from RainViewer scheme 2 to echo strength 0-3.

    Scheme 2 colour progression:
      15-30 dBZ → blue/cyan shades
      35-50 dBZ → green shades
      50+   dBZ → yellow → orange → red  (CB core range)
    """
    if a < 30:
        return 0                        # transparent = no echo
    if b > max(r, g):
        return 1                        # blue-dominant = light rain
    if g >= r:
        return 2                        # green-dominant = moderate
    return 3                            # warm (yellow/orange/red) = heavy/CB


def _tile_echo(img: 'Image.Image', px: int, py: int) -> int:
    """Maximum echo strength in a small box around pixel (px, py)."""
    w, h = img.size
    best = 0
    for dy in range(-_ECHO_RADIUS, _ECHO_RADIUS + 1):
        for dx in range(-_ECHO_RADIUS, _ECHO_RADIUS + 1):
            x, y = px + dx, py + dy
            if 0 <= x < w and 0 <= y < h:
                best = max(best, _pixel_echo_strength(*img.getpixel((x, y))))
    return best


# ---------------------------------------------------------------------------
# Data fetchers
# ---------------------------------------------------------------------------

async def _get(session: aiohttp.ClientSession, url: str) -> Optional[bytes]:
    """GET url; return bytes or None on failure."""
    try:
        async with session.get(url, timeout=_TIMEOUT) as resp:
            if resp.status == 200:
                return await resp.read()
            return None
    except Exception:  # noqa: broad-except
        return None


def _om_get_sync(url: str, proxy: Optional[str]) -> tuple:
    """Run a blocking GET for an Open-Meteo URL, optionally via a SOCKS5/HTTP proxy.

    Returns (status_code, content) where status_code is None on connection error.
    """
    import requests  # pylint: disable=import-error,import-outside-toplevel
    try:
        proxies = {'http': proxy, 'https': proxy} if proxy else None
        r = requests.get(url, timeout=30, proxies=proxies)
        return r.status_code, r.content
    except Exception as exc:  # noqa: broad-except
        return None, str(exc).encode()


class _OmHourlyLimitError(Exception):
    """Raised when the Open-Meteo hourly request quota is exhausted."""


async def _fetch_om_batch(sem: asyncio.Semaphore,
                          points: List[GridPoint],
                          proxy: Optional[str],
                          debug: bool = False) -> List[Optional[dict]]:
    """Fetch Open-Meteo data for a batch of grid points in one HTTP request."""
    lats = ','.join(str(p.lat) for p in points)
    lons = ','.join(str(p.lon) for p in points)
    url = (
        f"{_OM_API}?latitude={lats}&longitude={lons}"
        "&hourly=cape,convective_inhibition,showers,temperature_2m,dewpoint_2m"
        "&current=weather_code,temperature_2m"
        "&timezone=UTC&forecast_days=1"
    )
    loop = asyncio.get_running_loop()
    async with sem:
        status, data = await loop.run_in_executor(None, _om_get_sync, url, proxy)
        if status == 429:
            body = data.decode(errors='replace') if data else ''
            if 'ourly' in body:
                raise _OmHourlyLimitError(body)
            print(f"  OM 429: minutely limit hit, pausing {_OM_RETRY_WAIT}s …", flush=True)
            await asyncio.sleep(_OM_RETRY_WAIT)
            status, data = await loop.run_in_executor(None, _om_get_sync, url, proxy)
        await asyncio.sleep(_OM_DELAY)
    if status != 200:
        if debug:
            snippet = data[:200].decode(errors='replace') if data else '(no body)'
            print(f"  OM FAIL  status={status}  body={snippet!r}", flush=True)
        return [None] * len(points)
    try:
        parsed = json.loads(data)
        if isinstance(parsed, dict):
            parsed = [parsed]
        if len(parsed) != len(points):
            if debug:
                print(f"  OM FAIL  got {len(parsed)} results for {len(points)} points",
                      flush=True)
            return [None] * len(points)
        return [p if not p.get('error') else None for p in parsed]
    except Exception as exc:  # noqa: broad-except
        if debug:
            print(f"  OM FAIL  parse error: {exc}", flush=True)
        return [None] * len(points)


def _parse_om(raw: dict) -> Optional[OmPoint]:
    """Extract CB-relevant scalars from an OM response dict."""
    if not raw or raw.get('error'):
        return None
    cur = raw.get("current") or {}
    hourly = raw.get("hourly") or {}
    hour = datetime.now(timezone.utc).hour

    def _h(key: str, default: float) -> float:
        lst = hourly.get(key) or []
        return float(lst[hour]) if hour < len(lst) else float(default)

    temp_c = float(cur.get("temperature_2m", 15))
    return OmPoint(
        wmo_code=int(cur.get("weather_code", 0)),
        cape=_h("cape", 0),
        cin=_h("convective_inhibition", 0),
        showers_mm=_h("showers", 0),
        temp_c=temp_c,
        dp_c=_h("dewpoint_2m", temp_c - 5.0),
        raw=raw,
    )


async def fetch_radar_path(session: aiohttp.ClientSession) -> Optional[str]:
    """Return the RainViewer path for the most recent radar frame."""
    data = await _get(session, _RV_API)
    if not data:
        return None
    try:
        frames = json.loads(data).get('radar', {}).get('past', [])
        return frames[-1]['path'] if frames else None
    except Exception:  # noqa: broad-except
        return None


async def fetch_tile(session: aiohttp.ClientSession,
                     path: str, tx: int, ty: int) -> Optional['Image.Image']:
    """Fetch one RainViewer tile and return a PIL Image (RGBA) or None."""
    if not _HAS_PIL:
        return None
    url = f"{_RV_TILE}{path}/{_TILE_PX}/{_ZOOM}/{tx}/{ty}/2/1_1.png"
    data = await _get(session, url)
    if not data:
        return None
    try:
        return Image.open(BytesIO(data)).convert('RGBA')
    except Exception:  # noqa: broad-except
        return None


async def fetch_ts_sigmets(session: aiohttp.ClientSession) -> List[list]:
    """Return list of TS SIGMET polygons as [[lat,lon], ...] lists."""
    data = await _get(session, _AWC_SIGMET)
    if not data:
        return []
    polygons = []
    try:
        for feat in json.loads(data).get('features', []):
            props = feat.get('properties', {})
            hazard = (props.get('hazard') or '').upper()
            if 'TS' not in hazard and 'CONVECTIVE' not in hazard:
                continue
            coords = (feat.get('geometry') or {}).get('coordinates', [])
            if coords:
                ring = coords[0] if isinstance(coords[0][0], list) else coords
                polygons.append([[c[1], c[0]] for c in ring])
    except Exception:  # noqa: broad-except
        pass
    return polygons


async def fetch_ts_metars(session: aiohttp.ClientSession) -> List[Tuple[float, float]]:
    """Return (lat, lon) of stations reporting TS/GR/LTG/FC in their current METAR.

    Uses the VATSIM global METAR feed (comprehensive worldwide coverage) and
    resolves station coordinates via the AWC station info API.
    """
    data = await _get(session, _VATSIM_METAR)
    if not data:
        return []
    ts_icao: set = set()
    for line in data.decode(errors='replace').splitlines():
        tokens = line.split()
        if not tokens or not any(m in line for m in _TS_MARKERS):
            continue
        icao = tokens[0]
        if len(icao) == 4 and icao.isalnum():
            ts_icao.add(icao)
    if not ts_icao:
        return []
    ids = ','.join(sorted(ts_icao))
    sdata = await _get(session, f"{_AWC_STATION}?ids={ids}&format=json")
    if not sdata:
        return []
    positions = []
    try:
        for st in json.loads(sdata):
            lat = st.get('lat')
            lon = st.get('lon')
            if lat is not None and lon is not None:
                positions.append((float(lat), float(lon)))
    except Exception:  # noqa: broad-except
        pass
    return positions


# ---------------------------------------------------------------------------
# Evidence synthesis
# ---------------------------------------------------------------------------

def _check_evidence(om: OmPoint, radar: int,
                    sigmet_polys: List[list],
                    ts_metars: List[Tuple[float, float]],
                    point: GridPoint) -> Evidence:
    """Build Evidence from all sources for one grid point."""
    lat, lon = point.lat, point.lon
    om_wmo_ts = om.wmo_code in (95, 96, 99)

    # CAPE + confirmed showers as a standalone signal (independent of WMO code)
    from fw_cb import cape_to_cb_oktas  # pylint: disable=import-outside-toplevel
    cape_oktas = cape_to_cb_oktas(om.cape, om.cin)
    om_cape_showers = cape_oktas > 0 and om.showers_mm >= 0.5

    in_sigmet = any(_point_in_polygon(lat, lon, p) for p in sigmet_polys)

    nearby = any(_gcd_nm(lat, lon, mlat, mlon) <= 50 for mlat, mlon in ts_metars)

    return Evidence(
        om_wmo_ts=om_wmo_ts,
        om_cape_showers=om_cape_showers,
        radar_echo=radar,
        in_sigmet=in_sigmet,
        nearby_metar_ts=nearby,
    )


def _verdict(fw_cb_oktas: int, ev: Optional[Evidence]) -> str:
    """Compare FW prediction against consensus evidence."""
    if ev is None:
        return 'NO_DATA'
    if fw_cb_oktas == 0 and ev.consensus_cb:
        return 'FALSE_NEG'
    if fw_cb_oktas > 0 and ev.score == 0:
        return 'FALSE_POS'
    return 'OK'


# ---------------------------------------------------------------------------
# Grid and main scan loop
# ---------------------------------------------------------------------------

def _generate_grid(lat_min: float, lat_max: float,
                   lon_min: float, lon_max: float, step: float) -> List[GridPoint]:
    """Generate scan grid, skipping polar latitudes where CBs don't form."""
    pts = []
    lat = lat_min
    while lat <= lat_max + 1e-9:
        lon = lon_min
        while lon <= lon_max + 1e-9:
            pts.append(GridPoint(round(lat, 2), round(lon, 2)))
            lon += step
        lat += step
    return pts


def _evidence_str(ev: Evidence) -> str:
    """One-line summary of evidence flags."""
    parts = []
    if ev.om_wmo_ts:
        parts.append('WMO-TS')
    if ev.om_cape_showers:
        parts.append('CAPE+SH')
    if ev.radar_echo >= 2:
        parts.append(f'radar={ev.radar_echo}')
    if ev.in_sigmet:
        parts.append('SIGMET')
    if ev.nearby_metar_ts:
        parts.append('METAR-TS')
    return ','.join(parts) if parts else 'none'


async def _fetch_all_om(points: List[GridPoint], proxy: Optional[str],
                        debug: bool = False) -> dict:
    """Fetch and parse OM data for all grid points, returning {(lat, lon): OmPoint}."""
    batches = [points[i:i + _OM_BATCH_SIZE]
               for i in range(0, len(points), _OM_BATCH_SIZE)]
    eta_s = int(len(batches) * _OM_DELAY / _OM_CONCURRENCY + 1)
    print(
        f"  Fetching OM : {len(points)} points in {len(batches)} batches"
        f" (~{eta_s}s + any 429 pauses) …",
        flush=True)
    sem = asyncio.Semaphore(_OM_CONCURRENCY)
    batch_raws = await asyncio.gather(
        *[_fetch_om_batch(sem, batch, proxy, debug=debug) for batch in batches]
    )
    all_raws = [raw for batch in batch_raws for raw in batch]
    return {(pt.lat, pt.lon): _parse_om(raw)
            for pt, raw in zip(points, all_raws) if raw}


async def run_scan(args: argparse.Namespace) -> None:
    """Execute the full scan and write results."""
    points = _generate_grid(args.lat_min, args.lat_max,
                            args.lon_min, args.lon_max, args.grid)
    print(f"Scanning {len(points)} points at {args.grid}° spacing …", flush=True)

    if not _HAS_PIL:
        print("Warning: Pillow not installed — radar tile analysis disabled.", file=sys.stderr)

    tile_cache: dict = {}
    results: List[ScanResult] = []

    connector = aiohttp.TCPConnector(limit=10)
    async with aiohttp.ClientSession(connector=connector) as session:
        # Fetch shared resources in parallel
        radar_path_coro = fetch_radar_path(session)
        sigmets_coro = fetch_ts_sigmets(session)
        metars_coro = fetch_ts_metars(session)
        radar_path, sigmet_polys, ts_metars = await asyncio.gather(
            radar_path_coro, sigmets_coro, metars_coro)

        ts = datetime.now(timezone.utc).strftime('%H:%MZ')
        print(f"  Radar frame : {radar_path or 'unavailable'}", flush=True)
        print(f"  TS SIGMETs  : {len(sigmet_polys)}", flush=True)
        print(f"  TS METARs   : {len(ts_metars)}  ({ts})", flush=True)

        # Fetch OM data in batches (multi-location per request), limited concurrency
        try:
            om_map = await _fetch_all_om(points, args.om_proxy, debug=args.debug)
        except _OmHourlyLimitError:
            print("  OM ERROR    : hourly API limit exceeded — try again next hour",
                  file=sys.stderr, flush=True)
            return
        om_ok = sum(v is not None for v in om_map.values())
        print(f"  OM data     : {om_ok}/{len(points)} points OK", flush=True)
        if om_ok == 0:
            print("  OM ERROR    : all requests failed — check network / API status",
                  file=sys.stderr, flush=True)

        # Analyse each point
        for pt in points:
            om = om_map.get((pt.lat, pt.lon))
            if om is None:
                results.append(ScanResult(pt, None, None, 0, 'NO_DATA'))
                continue

            # Radar tile lookup (cached by tile coord) — must happen before om_cb_fields
            # so fw_oktas reflects what FrankenWeather actually predicts (it passes radar_echo).
            radar_echo = 0
            if radar_path and _HAS_PIL:
                tx, ty = _tile_xy(pt.lat, pt.lon, _ZOOM)
                if (tx, ty) not in tile_cache:
                    tile_cache[(tx, ty)] = await fetch_tile(session, radar_path, tx, ty)
                    await asyncio.sleep(0.03)
                tile_img = tile_cache[(tx, ty)]
                if tile_img:
                    px, py = _pixel_in_tile(pt.lat, pt.lon, _ZOOM)
                    radar_echo = _tile_echo(tile_img, px, py)

            fw_oktas, _, _ = om_cb_fields(om.raw, radar_echo=radar_echo)

            # Skip entirely uninteresting points (low CAPE, no radar, no SIGMET needed)
            if fw_oktas == 0 and om.cape < args.min_cape:
                continue

            ev = _check_evidence(om, radar_echo, sigmet_polys, ts_metars, pt)
            v = _verdict(fw_oktas, ev)
            results.append(ScanResult(pt, om, ev, fw_oktas, v))

            if v in ('FALSE_NEG', 'FALSE_POS'):
                print(
                    f"  {v:10s}  lat={pt.lat:6.1f} lon={pt.lon:7.1f}"
                    f"  fw={fw_oktas}ok  CAPE={om.cape:.0f}  CIN={om.cin:.0f}"
                    f"  SH={om.showers_mm:.2f}mm  WMO={om.wmo_code}"
                    f"  ev=[{_evidence_str(ev)}]",
                    flush=True,
                )

    _write_csv(args.output, results)
    _print_summary(results, args.output)


def _write_csv(path: str, results: List[ScanResult]) -> None:
    """Write results to CSV."""
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['lat', 'lon', 'fw_cb_oktas', 'radar_echo', 'verdict',
                    'cape', 'cin', 'showers_mm', 'wmo_code',
                    'om_wmo_ts', 'cape_sh', 'in_sigmet', 'metar_ts', 'ev_score'])
        for r in results:
            om, ev = r.om, r.ev
            w.writerow([
                r.point.lat, r.point.lon, r.fw_cb_oktas,
                ev.radar_echo if ev else '',
                r.verdict,
                f'{om.cape:.0f}' if om else '',
                f'{om.cin:.0f}' if om else '',
                f'{om.showers_mm:.2f}' if om else '',
                om.wmo_code if om else '',
                int(ev.om_wmo_ts) if ev else '',
                int(ev.om_cape_showers) if ev else '',
                int(ev.in_sigmet) if ev else '',
                int(ev.nearby_metar_ts) if ev else '',
                ev.score if ev else '',
            ])


def _print_summary(results: List[ScanResult], output: str) -> None:
    """Print a summary of scan results."""
    vs = [r.verdict for r in results]
    fn = vs.count('FALSE_NEG')
    fp = vs.count('FALSE_POS')
    ok = vs.count('OK')
    nd = vs.count('NO_DATA')
    print(f"\nScan complete — {len(results)} interesting points examined")
    print(f"  OK          : {ok}")
    print(f"  False neg   : {fn}  (consensus says CB, FW predicted none)")
    print(f"  False pos   : {fp}  (FW predicted CB, no source confirms it)")
    print(f"  No data     : {nd}  (OM fetch failed)")
    print(f"  Output      : {output}")


def _build_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description='FrankenWeather CB detection scanner')
    ap.add_argument('--grid', type=float, default=10.0,
                    help='Grid spacing in degrees (default 10.0; use 5.0 for finer scan)')
    ap.add_argument('--lat-min', type=float, default=-60.0, dest='lat_min')
    ap.add_argument('--lat-max', type=float, default=75.0, dest='lat_max')
    ap.add_argument('--lon-min', type=float, default=-180.0, dest='lon_min')
    ap.add_argument('--lon-max', type=float, default=175.0, dest='lon_max')
    ap.add_argument('--min-cape', type=float, default=200.0, dest='min_cape',
                    help='Skip points with CAPE below this J/kg (default 200)')
    ap.add_argument('--output', default='fw_scan.csv',
                    help='CSV output file (default fw_scan.csv)')
    ap.add_argument('--om-proxy', default=None, dest='om_proxy',
                    metavar='URL',
                    help='Proxy for Open-Meteo requests, e.g. socks5h://host:1080')
    ap.add_argument('--debug', action='store_true',
                    help='Print OM failure details (status code, response snippet)')
    return ap.parse_args()


def main() -> None:
    """Entry point."""
    asyncio.run(run_scan(_build_args()))


if __name__ == '__main__':
    main()
