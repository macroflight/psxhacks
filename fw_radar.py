"""Weather radar (RainViewer) helpers shared by frankenweather.py and fw_scanner.py.

RainViewer's tiles come in one colour scheme only since 2026 ("Universal
Blue": the colour scheme in a tile URL is ignored, zoom 7 is the most). Its
published colour table (rainviewer.com/files/rainviewer_api_colors_table.csv)
maps every colour to a reflectivity in whole dBZ, rain and snow apart, so a
tile decodes exactly (checked on live tiles, 2026-11-15). The old scheme-2
guess (blue light, green moderate, warm heavy) read Universal Blue's beige
drizzle (-10 to +10 dBZ) as heavy rain.
"""
import math
from dataclasses import dataclass
from typing import Optional

# Echo classes (0 none, 1 light, 2 moderate, 3 heavy / CB core) by dBZ
ECHO_LIGHT_DBZ = 15
ECHO_MODERATE_DBZ = 35
ECHO_HEAVY_DBZ = 50

# Universal Blue colour (RGBA hex) -> dBZ, from RainViewer's colour table
_RAIN = {
    '63615914': -10, '66635a19': -9, '69665c1e': -8, '6c685d24': -7, '6f6b5f29': -6,
    '726e612e': -5, '75706234': -4, '78736439': -3, '7c75653e': -2, '7f786744': -1, '827b6949': 0,
    '857d6a4e': 1, '88806c54': 2, '8b826d59': 3, '8e856f5e': 4, '92887164': 5, '9e93756e': 6,
    'aa9e7978': 7, 'b6a97e82': 8, 'c2b4828c': 9, 'cec08796': 10, 'd2c48ba0': 11, 'd6c88faa': 12,
    'dacc93b4': 13, 'ded097be': 14, '88ddeeff': 15, '6cd1ebff': 16, '51c5e8ff': 17, '36bae5ff': 18,
    '1baee2ff': 19, '00a3e0ff': 20, '009ad5ff': 21, '0091caff': 22, '0088bfff': 23, '007fb4ff': 24,
    '0077aaff': 25, '0070a3ff': 26, '00699cff': 27, '006295ff': 28, '005b8eff': 29, '005588ff': 30,
    '005180ff': 31, '004e78ff': 32, '004a70ff': 33, '004768ff': 34, 'ffee00ff': 35, 'ffe000ff': 36,
    'ffd200ff': 37, 'ffc500ff': 38, 'ffb700ff': 39, 'ffaa00ff': 40, 'ff9f00ff': 41, 'ff9500ff': 42,
    'ff8b00ff': 43, 'ff8100ff': 44, 'ff4400ff': 45, 'f23600ff': 46, 'e62800ff': 47, 'd91b00ff': 48,
    'cd0d00ff': 49, 'c10000ff': 50, 'a80000ff': 51, '8f0000ff': 52, '760000ff': 53, '5d0000ff': 54,
    'ffaaffff': 55, 'ff9fffff': 56, 'ff95ffff': 57, 'ff8bffff': 58, 'ff81ffff': 59, 'ff77ffff': 60,
    'ff6cffff': 61, 'ff62ffff': 62, 'ff58ffff': 63, 'ff4effff': 64, 'ffffffff': 65, '00ff00ff': 75,
}
_SNOW = {
    'ceffff0c': -9, 'cdffff19': -8, 'ccffff26': -7, 'cbffff33': -6, 'cbffff3f': -5, 'caffff4c': -4,
    'c9ffff59': -3, 'c8ffff66': -2, 'c7ffff72': -1, 'c7ffff7f': 0, 'c6ffff8c': 1, 'c5ffff99': 2,
    'c4ffffa5': 3, 'c3ffffb2': 4, 'c3ffffbf': 5, 'c2ffffcc': 6, 'c1ffffd8': 7, 'c0ffffe5': 8,
    'bffffff2': 9, 'bfffffff': 10, 'b8f8ffff': 11, 'b2f2ffff': 12, 'abebffff': 13, 'a5e5ffff': 14,
    '9fdfffff': 15, '98d8ffff': 16, '92d2ffff': 17, '8bcbffff': 18, '85c5ffff': 19, '7fbfffff': 20,
    '78b8ffff': 21, '72b2ffff': 22, '6babffff': 23, '65a5ffff': 24, '5f9fffff': 25, '5b9bffff': 26,
    '5898ffff': 27, '5595ffff': 28, '5292ffff': 29, '4f8fffff': 30, '4b8bffff': 31, '4888ffff': 32,
    '4585ffff': 33, '4282ffff': 34, '3f7fffff': 35, '3b7bffff': 36, '3878ffff': 37, '3575ffff': 38,
    '3272ffff': 39, '2f6fffff': 40, '2b6bffff': 41, '2868ffff': 42, '2565ffff': 43, '2262ffff': 44,
    '1f5fffff': 45, '1b5bffff': 46, '1858ffff': 47, '1555ffff': 48, '1252ffff': 49, '0f4fffff': 50,
    '0c4bffff': 51, '0948ffff': 52, '0645ffff': 53, '0242ffff': 54, '003fffff': 55, '003bffff': 56,
    '0038ffff': 57, '0035ffff': 58, '0032ffff': 59, '002fffff': 60, '002bffff': 61, '0028ffff': 62,
    '0025ffff': 63, '0022ffff': 64, '001fffff': 65, '001bffff': 66, '0018ffff': 67, '0015ffff': 68,
    '0012ffff': 69, '000fffff': 70, '000cffff': 71, '0009ffff': 72, '0006ffff': 73, '0002ffff': 74,
    '0000ffff': 75,
}


def pixel_dbz(r: int, g: int, b: int, a: int) -> Optional[int]:
    """Return a tile pixel's reflectivity (dBZ), or None for no echo (or an unknown colour)."""
    if a == 0:
        return None
    key = f"{r:02x}{g:02x}{b:02x}{a:02x}"
    dbz = _RAIN.get(key)
    return dbz if dbz is not None else _SNOW.get(key)


def echo_class(dbz: Optional[int]) -> int:
    """Return the echo class 0-3 for a reflectivity (None: 0)."""
    if dbz is None or dbz < ECHO_LIGHT_DBZ:
        return 0
    if dbz < ECHO_MODERATE_DBZ:
        return 1
    if dbz < ECHO_HEAVY_DBZ:
        return 2
    return 3


def pixel_echo(r: int, g: int, b: int, a: int) -> int:
    """Return a tile pixel's echo class 0-3."""
    return echo_class(pixel_dbz(r, g, b, a))


# ---------------------------------------------------------------------------
# Storm cells (package 4a): tiles -> dBZ -> cells -> motion
# ---------------------------------------------------------------------------

TILE_URL = "https://tilecache.rainviewer.com"
TILE_PX = 256
CELL_ZOOM = 7                 # RainViewer's most (about 1.2 km a pixel at the equator)
COVERAGE_ZOOM = 5             # the coverage mask needs less
COVERAGE_PATH = "/v2/coverage/0"
CELL_DBZ = 40                 # a cell: connected pixels of at least this
CB_DBZ = 45                   # a cell this strong (its peak) is a CB; weaker ones TCU/showers
CELL_MIN_KM2 = 8.0            # smaller ones are noise or speckle
SPLIT_KM2 = 400.0             # a larger one is split into its stronger cores
TRACK_MAX_KT = 70.0           # a cell can't have moved faster than this between frames
TRACK_GAP_NM = 4.0            # ... plus this (centres wobble as cells change shape)
TRACK_PREDICTED_NM = 8.0      # a tracked cell: this far from where its motion puts it
EARTH_KM = 6371.0


def tile_url(path: str, zoom: int, tx: int, ty: int) -> str:
    """Return a radar frame tile's URL (path from weather-maps.json, no smoothing)."""
    return f"{TILE_URL}{path}/{TILE_PX}/{zoom}/{tx}/{ty}/2/0_0.png"


def coverage_url(zoom: int, tx: int, ty: int) -> str:
    """Return a coverage mask tile's URL (opaque: no radar there; transparent: covered)."""
    return f"{TILE_URL}{COVERAGE_PATH}/{TILE_PX}/{zoom}/{tx}/{ty}/0/0_0.png"


def global_px(lat: float, lon: float, zoom: int) -> tuple:
    """Return the Web Mercator pixel (x, y) of (lat, lon) at zoom, over the whole world."""
    n = TILE_PX * 2 ** zoom
    lat = max(-85.0, min(85.0, lat))
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y


def px_latlon(x: float, y: float, zoom: int) -> tuple:
    """Return (lat, lon) of a world pixel at zoom (global_px()'s inverse)."""
    n = TILE_PX * 2 ** zoom
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n))))
    return lat, ((lon + 180.0) % 360.0) - 180.0


def tiles_around(lat: float, lon: float, radius_nm: float, zoom: int) -> list:
    """Return the (tx, ty) tiles covering radius_nm around (lat, lon)."""
    dlat = radius_nm / 60.0
    dlon = dlat / max(0.1, math.cos(math.radians(lat)))
    x0, y0 = global_px(min(85.0, lat + dlat), lon - dlon, zoom)
    x1, y1 = global_px(max(-85.0, lat - dlat), lon + dlon, zoom)
    n = 2 ** zoom
    return [(tx % n, ty) for ty in range(int(y0 // TILE_PX), int(y1 // TILE_PX) + 1)
            for tx in range(int(x0 // TILE_PX), int(x1 // TILE_PX) + 1) if 0 <= ty < n]


def tile_dbz(img) -> bytearray:
    """Return a PIL RGBA tile's reflectivity per pixel, row by row (dBZ + 64; 0: no echo)."""
    out = bytearray(TILE_PX * TILE_PX)
    for i, px in enumerate(img.getdata()):
        if px[3]:
            dbz = pixel_dbz(*px)
            if dbz is not None:
                out[i] = max(1, min(255, dbz + 64))
    return out


def tile_covered(img) -> bytearray:
    """Return a coverage tile as 1 (radar coverage) / 0 per pixel; img is PIL RGBA."""
    return bytearray(0 if px[3] else 1 for px in img.getdata())


def _dist_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2.0) ** 2 + math.cos(p1) * math.cos(p2) *
         math.sin(math.radians(lon2 - lon1) / 2.0) ** 2)
    return 2.0 * EARTH_KM * math.asin(min(1.0, math.sqrt(a))) / 1.852


def _course(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360.0


@dataclass
class Cell:  # pylint: disable=too-many-instance-attributes
    """A storm cell on the radar: connected pixels of at least CELL_DBZ."""

    lat: float                  # reflectivity-weighted centre
    lon: float
    max_dbz: int
    area_km2: float
    frame: int                  # the radar frame's time (unix s)
    motion: Optional[tuple] = None   # (towards deg true, kt) from earlier frames, None: new
    age_s: int = 0              # since it was first seen (0: this frame)
    key: str = ""               # stays the same while the cell is tracked

    @property
    def is_cb(self) -> bool:
        """Return True if the cell is strong enough for a CB (else TCU / heavy showers)."""
        return self.max_dbz >= CB_DBZ


def _components(pixels: dict, threshold: int) -> list:
    """Return the 8-connected groups (lists of world pixels) of pixels at least threshold dBZ."""
    strong = {p for p, v in pixels.items() if v >= threshold}
    groups, seen = [], set()
    for start in strong:
        if start in seen:
            continue
        stack, members = [start], []
        seen.add(start)
        while stack:
            px = stack.pop()
            members.append(px)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    nb = (px[0] + dx, px[1] + dy)
                    if nb in strong and nb not in seen:
                        seen.add(nb)
                        stack.append(nb)
        groups.append(members)
    return groups


def _cores(pixels: dict, members: list, threshold: int, px_km2: float) -> list:
    """Split a large group into its stronger cores (5 dBZ steps), as long as it splits."""
    if len(members) * px_km2 <= SPLIT_KM2 or threshold + 5 > max(pixels[p] for p in members):
        return [members]
    inner = {p: pixels[p] for p in members}
    subs = [g for g in _components(inner, threshold + 5) if len(g) * px_km2 >= CELL_MIN_KM2]
    if len(subs) <= 1:
        return [subs[0]] if subs and len(subs[0]) * px_km2 > SPLIT_KM2 else [members]
    out = []
    for sub in subs:
        out += _cores(pixels, sub, threshold + 5, px_km2)
    return out


def find_cells(tiles: dict, zoom: int, frame: int, area: Optional[tuple] = None) -> list:  # pylint: disable=too-many-locals
    """Return the cells in decoded tiles {(tx, ty): tile_dbz()}, strongest first.

    Connected areas of at least CELL_DBZ (8-connected over the mosaic, so
    across tile edges); a large one (a line, a cluster) is split into its
    cores at 5 dBZ steps (_cores()). area (lat_min, lon_min, lat_max,
    lon_max) keeps only cells centred in it.
    """
    pixels = {}
    for (tx, ty), grid in tiles.items():
        base_x, base_y = tx * TILE_PX, ty * TILE_PX
        for i, value in enumerate(grid):
            if value >= CELL_DBZ + 64:
                y, x = divmod(i, TILE_PX)
                pixels[(base_x + x, base_y + y)] = value - 64
    cells = []
    for group in _components(pixels, CELL_DBZ):
        lat0, _ = px_latlon(group[0][0], group[0][1], zoom)
        px_km = 2 * math.pi * EARTH_KM * math.cos(math.radians(lat0)) / (TILE_PX * 2 ** zoom)
        px_km2 = px_km * px_km
        for members in _cores(pixels, group, CELL_DBZ, px_km2):
            area_km2 = len(members) * px_km2
            if area_km2 < CELL_MIN_KM2:
                continue
            weights = [10 ** (pixels[p] / 10.0) for p in members]
            total = sum(weights)
            cx = sum(p[0] * w for p, w in zip(members, weights)) / total
            cy = sum(p[1] * w for p, w in zip(members, weights)) / total
            lat, lon = px_latlon(cx + 0.5, cy + 0.5, zoom)
            if area and not (area[0] <= lat <= area[2] and area[1] <= lon <= area[3]):
                continue
            cells.append(Cell(lat, lon, max(pixels[p] for p in members), area_km2, frame))
    cells.sort(key=lambda c: (-c.max_dbz, -c.area_km2))
    return cells


def _ahead(lat: float, lon: float, hdg: float, nm: float) -> tuple:
    """Return (lat, lon) nm from a point along hdg (flat-earth, short distances)."""
    dlat = nm * math.cos(math.radians(hdg)) / 60.0
    dlon = nm * math.sin(math.radians(hdg)) / (60.0 * max(0.1, math.cos(math.radians(lat))))
    return lat + dlat, lon + dlon


def track(previous: list, cells: list, dt_s: float) -> None:  # pylint: disable=too-many-locals
    """Match cells to the previous frame's; give them motion, age and key.

    A previous cell with a motion is looked for where that motion puts it now
    (within TRACK_PREDICTED_NM), one without within TRACK_MAX_KT of where it
    was. Each previous cell is matched once, the strongest new cells first.
    A matched cell's motion is its move since then, blended with the
    previous motion; a move far off the previous motion (cores splitting and
    merging) counts for little. dt_s 0 (the same frame looked at again, e.g.
    after the aircraft moved): matched cells keep their key, age and motion.
    """
    hours = max(0.0, dt_s) / 3600.0
    free = list(previous)
    for cell in cells:
        best, best_d = None, math.inf
        for old in free:
            if old.motion is not None and dt_s > 0:
                plat, plon = _ahead(old.lat, old.lon, old.motion[0], old.motion[1] * hours)
                d, reach = _dist_nm(plat, plon, cell.lat, cell.lon), TRACK_PREDICTED_NM
            else:
                d = _dist_nm(old.lat, old.lon, cell.lat, cell.lon)
                reach = TRACK_MAX_KT * hours + TRACK_GAP_NM
            if d <= reach and d < best_d:
                best, best_d = old, d
        if best is None:
            cell.key = f"{cell.frame}-{cell.lat:.3f}-{cell.lon:.3f}"
            continue
        free.remove(best)
        cell.key, cell.age_s = best.key, best.age_s + int(max(0.0, dt_s))
        if dt_s <= 0:
            cell.motion = best.motion
            continue
        hdg = _course(best.lat, best.lon, cell.lat, cell.lon)
        kt = _dist_nm(best.lat, best.lon, cell.lat, cell.lon) / hours
        if best.motion is None:
            cell.motion = (hdg, kt)
            continue
        off = abs((hdg - best.motion[0] + 180.0) % 360.0 - 180.0)
        weight = 0.5 if off <= 60.0 else 0.15   # vectors: the new move's share
        u = (weight * math.sin(math.radians(hdg)) * kt +
             (1 - weight) * math.sin(math.radians(best.motion[0])) * best.motion[1])
        v = (weight * math.cos(math.radians(hdg)) * kt +
             (1 - weight) * math.cos(math.radians(best.motion[0])) * best.motion[1])
        cell.motion = (math.degrees(math.atan2(u, v)) % 360.0, math.hypot(u, v))
