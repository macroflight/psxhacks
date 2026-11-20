"""Satellite lightning for frankenweather: EUMETSAT's MTG Lightning Imager.

Meteosat-12's Lightning Imager sees Europe, Africa, the Atlantic and the
western Indian Ocean. EUMETSAT's EUMETView WMS serves its "Accumulated
Flash Area" (layer mtg_fd:li_afa) as images, one per 5 minutes, about
10-15 minutes after the fact, without an account (© EUMETSAT). A coloured
pixel means flashes there in those 5 minutes; frankenweather only needs
"lightning near here", so every coloured pixel counts as a flash position.
It replaced Blitzortung, whose terms don't allow this use (2026-11-15).
Elsewhere (the Americas, Asia-Pacific) there is no free near-real-time
lightning that's practical here; the radar, SIGMETs and the model decide.

Pure functions only: frankenweather.py fetches.
"""
import math
import re
from datetime import datetime, timedelta
from typing import Optional

WMS_URL = "https://view.eumetsat.int/geoserver/mtg_fd/ows"
LAYER = "li_afa"
STEP_MIN = 5                 # one image per 5 minutes
STEPS = 3                    # the latest 3 images: 15 minutes of flashes
DEG_PER_PX = 0.05            # about 3 nm a pixel
BOX_DEG = 6.0                # images reach this far around the aircraft (zones up to ~250 nm)
# Meteosat-12 at 0 degrees: usable up to about 65 degrees of arc from the
# point under it (the view gets too oblique beyond)
SUBSAT_LON = 0.0
COVERAGE_ARC_DEG = 65.0


# Measured blind area inside that arc (2026-11-20, against GOES GLM over the same 15 minutes,
# ~/fw-research/mtg/edge.py): south of 24 S and west of 48.5 W (southern Brazil, Paraguay,
# Uruguay, north-east Argentina) MTG had no flash where GLM had up to 1,121 per 2-degree box,
# while it saw the Amazon out to 70 W and the South Atlantic at 38 S 38 W
BLIND = ((-90.0, -24.0, -180.0, -48.5),)   # (lat_min, lat_max, lon_min, lon_max)


def covers(lat: float, lon: float) -> bool:
    """Return True if MTG's Lightning Imager sees (lat, lon)."""
    if any(a <= lat <= b and c <= lon <= d for a, b, c, d in BLIND):
        return False
    p, dl = math.radians(lat), math.radians(lon - SUBSAT_LON)
    return math.degrees(math.acos(max(-1.0, min(1.0, math.cos(p) * math.cos(dl))))) <= \
        COVERAGE_ARC_DEG


def latest_time(capabilities_xml: str) -> Optional[str]:
    """Return the newest image time ("2026-11-15T10:10:00Z") from the WMS GetCapabilities XML."""
    pattern = r"<Name>" + LAYER + r"</Name>.*?<Dimension name=\"time\"[^>]*default=\"([^\"]+)\""
    m = re.search(pattern, capabilities_xml, re.S)
    return m.group(1) if m else None


def box(lat: float, lon: float) -> tuple:
    """Return the image area around a point (lat_min, lon_min, lat_max, lon_max) and its px size.

    Centred on the whole degree nearest the point, edges on whole pixels: every sim nearby
    gets the same pixels, so it counts the same flashes near a storm (the CB planner).
    """
    lat, lon = float(round(lat)), float(round(lon))
    lat_min, lat_max = max(-89.0, lat - BOX_DEG), min(89.0, lat + BOX_DEG)
    # wider in longitude away from the equator, to reach as far east and west
    half_lon = min(60.0, BOX_DEG / max(0.2, math.cos(math.radians(lat))))
    half_lon = round(half_lon / DEG_PER_PX) * DEG_PER_PX
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


def flash_points(img, bbox: tuple) -> list:
    """Return [(lat, lon), ...] of the coloured pixels of a GetMap image (a PIL RGBA image)."""
    lat_min, lon_min, lat_max, lon_max = bbox
    width, height = img.size
    dlat, dlon = (lat_max - lat_min) / height, (lon_max - lon_min) / width
    points = []
    for i, pixel in enumerate(img.getdata()):
        if pixel[3] >= 128:
            y, x = divmod(i, width)
            points.append((lat_max - (y + 0.5) * dlat,
                           ((lon_min + (x + 0.5) * dlon + 180.0) % 360.0) - 180.0))
    return points


def step_times(latest: str) -> list:
    """Return the latest STEPS image times, newest first."""
    t = datetime.strptime(latest[:19], "%Y-%m-%dT%H:%M:%S")
    return [(t - timedelta(minutes=STEP_MIN * k)).strftime("%Y-%m-%dT%H:%M:%SZ")
            for k in range(STEPS)]
