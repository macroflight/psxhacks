"""Satellite cloud top heights for frankenweather's radar CBs (--radar-cbs).

EUMETSAT's EUMETView WMS serves Meteosat's (MSG, 0 degrees) "Cloud Top
Height" product (layer msg_fes:cth) as colour images, one per 15 minutes,
about 20-25 minutes after the fact, without an account (© EUMETSAT). The
colours run from LEGEND_MIN_M to LEGEND_MAX_M as the layer's legend shows
(GetLegendGraphic); higher tops show as LEGEND_MAX_M. A radar storm's CB
top is the highest cloud top near it: the radar has no heights, and
"stronger is taller" was 7,000 ft too high for a compact winter storm and
too low for a squall line (FTECH1 2026-11-17). Where a high cloud deck
covers the storm the satellite sees the deck: the CB reaches at least
that high, so it still counts. Outside Meteosat's view (the Americas,
Asia-Pacific) or with an old image, the strength formula decides.

Pure functions only: frankenweather.py fetches.
"""
import math
import re
from datetime import datetime, timezone
from typing import Optional

WMS_URL = "https://view.eumetsat.int/geoserver/msg_fes/ows"
LAYER = "cth"
LEGEND_MIN_M = 320.0         # the legend's colour bar, left end
LEGEND_MAX_M = 16000.0       # ... right end (higher tops show as this)
MAX_AGE_S = 2700             # an image older than this (45 min) isn't used
DEG_PER_PX = 0.025           # about 1.5 nm a pixel (coarser ones skip MSG pixels: 3-5 km here)
BOX_DEG = 7.0                # images reach this far around the aircraft (radar cells: up to 380 nm)
MAX_COLOUR_DIST = 40         # a pixel further from every legend colour: not a cloud top
# Meteosat at 0 degrees: usable up to about 70 degrees of arc from the point under it
SUBSAT_LON = 0.0
COVERAGE_ARC_DEG = 70.0
_NM_PER_DEG = 60.0


def covers(lat: float, lon: float) -> bool:
    """Return True if Meteosat at 0 degrees sees (lat, lon) well enough."""
    p, dl = math.radians(lat), math.radians(lon - SUBSAT_LON)
    return math.degrees(math.acos(max(-1.0, min(1.0, math.cos(p) * math.cos(dl))))) <= \
        COVERAGE_ARC_DEG


def latest_time(capabilities_xml: str) -> Optional[str]:
    """Return the newest image time ("2026-11-17T20:00:00Z") from the WMS GetCapabilities XML."""
    pattern = r"<Name>" + LAYER + r"</Name>.*?<Dimension name=\"time\"[^>]*default=\"([^\"]+)\""
    m = re.search(pattern, capabilities_xml, re.S)
    return m.group(1) if m else None


def age_s(image_time: str, now: datetime) -> float:
    """Return how old an image time ("2026-11-17T20:00:00Z") is at now (aware UTC)."""
    t = datetime.strptime(image_time[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    return (now - t).total_seconds()


def box(lat: float, lon: float) -> tuple:
    """Return the image area around a point (lat_min, lon_min, lat_max, lon_max) and its px size."""
    lat_min, lat_max = max(-89.0, lat - BOX_DEG), min(89.0, lat + BOX_DEG)
    half_lon = min(60.0, BOX_DEG / max(0.2, math.cos(math.radians(lat))))
    lon_min, lon_max = lon - half_lon, lon + half_lon
    width = max(1, round((lon_max - lon_min) / DEG_PER_PX))
    height = max(1, round((lat_max - lat_min) / DEG_PER_PX))
    return (lat_min, lon_min, lat_max, lon_max), (width, height)


def getmap_params(bbox: tuple, size: tuple, when: str) -> dict:
    """Return the WMS 1.3.0 GetMap query (EPSG:4326 axis order: lat, lon)."""
    return {"service": "WMS", "version": "1.3.0", "request": "GetMap", "layers": LAYER,
            "styles": "", "crs": "EPSG:4326", "bbox": ",".join(f"{v:.4f}" for v in bbox),
            "width": str(size[0]), "height": str(size[1]), "format": "image/png",
            "transparent": "true", "time": when}


def legend_params() -> dict:
    """Return the WMS GetLegendGraphic query for the layer's colour bar."""
    return {"service": "WMS", "version": "1.3.0", "request": "GetLegendGraphic",
            "format": "image/png", "width": "640", "height": "80", "layer": LAYER}


def _coloured(pixel: tuple) -> bool:
    r, g, b, a = pixel
    return a >= 128 and not (r > 245 and g > 245 and b > 245)


def legend_palette(img) -> list:
    """Return [((r, g, b), metres), ...] from the legend image (PIL RGBA): its colour bar.

    The bar is the run of coloured pixels with the most different colours on
    any row (its black frame and the text are runs of one colour); its left
    end is LEGEND_MIN_M and its right end LEGEND_MAX_M. Empty if there is none.
    """
    width, height = img.size
    best = (0, 0, 0, 0)   # (distinct colours, row, start, length)
    for y in range(height):
        row = [img.getpixel((x, y)) for x in range(width)] + [(255, 255, 255, 255)]
        run_start = None
        for x, pixel in enumerate(row):
            on = _coloured(pixel)
            if on and run_start is None:
                run_start = x
            elif not on and run_start is not None:
                distinct = len({p[:3] for p in row[run_start:x]})
                if distinct > best[0]:
                    best = (distinct, y, run_start, x - run_start)
                run_start = None
    distinct, row_y, start, length = best
    if distinct < 50:
        return []
    return [(img.getpixel((start + i, row_y))[:3],
             LEGEND_MIN_M + (LEGEND_MAX_M - LEGEND_MIN_M) * i / (length - 1))
            for i in range(length)]


def decode(img, palette: list) -> list:
    """Return the image's cloud top heights (m) row by row, None where no cloud top."""
    width, height = img.size
    cache: dict = {}
    data = list(img.getdata())
    rows = []
    for y in range(height):
        row = []
        for pixel in data[y * width:(y + 1) * width]:
            if pixel[3] < 128 or pixel[:3] == (0, 0, 0):
                row.append(None)
                continue
            rgb = pixel[:3]
            if rgb not in cache:
                dist, metres = min((sum((a - b) ** 2 for a, b in zip(rgb, c)), m)
                                   for c, m in palette)
                cache[rgb] = metres if dist <= MAX_COLOUR_DIST ** 2 else None
            row.append(cache[rgb])
        rows.append(row)
    return rows


def top_near(grid: list, bbox: tuple, lat: float, lon: float, radius_nm: float) -> Optional[float]:  # pylint: disable=too-many-locals
    """Return the highest cloud top (m) within radius_nm of (lat, lon), or None (none, outside)."""
    lat_min, lon_min, lat_max, lon_max = bbox
    height = len(grid)
    if not height or not lat_min <= lat <= lat_max:
        return None
    width = len(grid[0])
    dlat, dlon = (lat_max - lat_min) / height, (lon_max - lon_min) / width
    lon = lon_min + (lon - lon_min) % 360.0   # the box may cross the antimeridian
    cos_lat = max(0.05, math.cos(math.radians(lat)))
    ry = int(radius_nm / _NM_PER_DEG / dlat) + 1
    rx = int(radius_nm / _NM_PER_DEG / cos_lat / dlon) + 1
    cy, cx = int((lat_max - lat) / dlat), int((lon - lon_min) / dlon)
    best = None
    for y in range(max(0, cy - ry), min(height, cy + ry + 1)):
        plat = lat_max - (y + 0.5) * dlat
        for x in range(max(0, cx - rx), min(width, cx + rx + 1)):
            metres = grid[y][x]
            if metres is None or (best is not None and metres <= best):
                continue
            plon = lon_min + (x + 0.5) * dlon
            if math.hypot((plat - lat), (plon - lon) * cos_lat) * _NM_PER_DEG <= radius_nm:
                best = metres
    return best
