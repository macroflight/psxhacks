"""Satellite cloud tops outside Meteosat's view, for frankenweather (--radar-cbs, --sat-storms).

fw_cloudtop reads Meteosat's (0 degrees) Cloud Top Height images over
Europe, Africa and the Atlantic. This module gives the same thing, a grid
of cloud top heights in metres over a box round the aircraft, elsewhere:

- The Americas and the eastern Pacific: NOAA's GOES-19 (GOES-East, 75.2 W)
  and GOES-18 (GOES-West, 137.0 W) Cloud Top Height product
  (ABI-L2-ACHAF: the full disk every 10 minutes, about 10 km pixels, a
  file of about 1.6 MB about 4 minutes after the scan ends) on NOAA's
  public cloud buckets, free and without an account. Reading it needs h5py.
- Asia, the western Pacific and Australia: Himawari-9's 10.4 micrometre
  infrared image (band 13) from NASA's GIBS map service (free, no key),
  every 10 minutes but about 45 minutes late. The image's colours are
  brightness temperatures (GIBS colour map); a cold top is a high top, its
  height estimated from a standard tropical-to-midlatitude temperature drop
  with height (bt_height_m()).

A deep, cold top over thunderstorm air (the forecast's CAPE, and its
showers) is where thunderstorms may be when there is no radar and no
lightning (storm_chance()). Only roughly: checked against radar, such a
point was a radar storm one time in five at best, so these storms are made
up near the cold tops (fw_storms), not on them.

Pure functions only: frankenweather.py fetches.
"""
import io
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

# --- GOES Cloud Top Height (ABI-L2-ACHAF) ---------------------------------
GOES = {"G19": ("noaa-goes19", -75.2), "G18": ("noaa-goes18", -137.0)}
GOES_PRODUCT = "ABI-L2-ACHAF"
GOES_ARC_DEG = 70.0          # usable this far (degrees of arc) from the point under the satellite
GOES_DEG_PER_PX = 0.1        # the product's pixels are about 10 km
_GRS80_A, _GRS80_B, _GOES_H = 6378137.0, 6356752.31414, 42164160.0   # metres

# --- Himawari band 13 from NASA GIBS --------------------------------------
HIMAWARI_SUB_LON = 140.7
HIMAWARI_ARC_DEG = 70.0
GIBS_WMS = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"
GIBS_LAYER = "Himawari_AHI_Band13_Clean_Infrared"
GIBS_COLORMAP = ("https://gibs.earthdata.nasa.gov/colormaps/v1.3/"
                 "Clean_Longwave_Infrared_Window_Band.xml")
GIBS_STEP_MIN = 10
GIBS_LATE_MIN = (40, 50, 60, 70)   # images are this many minutes late: try these, newest first
GIBS_DEG_PER_PX = 0.05

BOX_DEG = 7.0                # like fw_cloudtop: images reach this far round the aircraft
MAX_AGE_S = 4800             # an image older than this (80 min) isn't used

# --- deep convection -------------------------------------------------------
# A top this cold (brightness temperature) is a deep convective top: an
# anvil or overshooting top, glaciated (-40 C is where satellite convective
# cloud algorithms start; operational warnings watch -52 C and colder)
DEEP_BT_C = -40.0
CAPE_MIN = 200.0             # J/kg: below this the forecast has no thunderstorm air there
SHOWERS_MIN = 0.1            # mm/h: the forecast's convective precipitation there
# fw_storms chance per grid point and hour, and peak dBZ, by tier (a lightning storm: 0.9, 55).
# Checked against RainViewer radar storms (43 dBZ within 20 nm) on 2026-11-20 at 0.5 degree
# points, ~/fw-research/ir/validate.py: a cold top over CAPE with forecast showers was a radar
# storm 21-22 % of the time in southern Brazil (recall 65-70 %); a colder top over CAPE without
# showers 12-16 % near Darwin, where the forecast had no showers at all but radar had storms;
# over the Gulf of Mexico, deep winter cirrus over CAPE without showers: 11 points at -50 C, no
# storm. A made-up storm lives about 3/4 of its hour, so these chances give about as many
# storms as the radar had.
SAT_TIERS = ((DEEP_BT_C, SHOWERS_MIN, 0.25, 50),   # cold top, CAPE, forecast showers
             (-50.0, 0.0, 0.15, 48))                # colder top, CAPE, no showers needed


def _arc_deg(lat: float, lon: float, sub_lon: float) -> float:
    return math.degrees(math.acos(max(-1.0, min(1.0, math.cos(math.radians(lat)) *
                                                math.cos(math.radians(lon - sub_lon))))))


def best_arc(lat: float, lon: float) -> float:
    """Return how far (degrees of arc) (lat, lon) is from the nearest GOES or Himawari sub-point."""
    return min([_arc_deg(lat, lon, sub) for _, sub in GOES.values()] +
               [_arc_deg(lat, lon, HIMAWARI_SUB_LON)])


def source(lat: float, lon: float) -> Optional[str]:
    """Return the satellite whose cloud tops this module reads at (lat, lon), or None.

    "G19"/"G18" (GOES, the nearer one) or "H9" (Himawari) when it sees the
    point; None where neither does. frankenweather uses fw_cloudtop's
    Meteosats instead where one of them is nearer overhead (best_arc()).
    """
    best = min([(_arc_deg(lat, lon, sub), name) for name, (_, sub) in GOES.items()] +
               [(_arc_deg(lat, lon, HIMAWARI_SUB_LON), "H9")])
    limit = HIMAWARI_ARC_DEG if best[1] == "H9" else GOES_ARC_DEG
    return best[1] if best[0] <= limit else None


def box(lat: float, lon: float, deg_per_px: float) -> tuple:
    """Return the area round a point (lat_min, lon_min, lat_max, lon_max) and its grid size."""
    lat_min, lat_max = max(-89.0, lat - BOX_DEG), min(89.0, lat + BOX_DEG)
    half_lon = min(60.0, BOX_DEG / max(0.2, math.cos(math.radians(lat))))
    lon_min, lon_max = lon - half_lon, lon + half_lon
    return ((lat_min, lon_min, lat_max, lon_max),
            (max(1, round((lon_max - lon_min) / deg_per_px)),
             max(1, round((lat_max - lat_min) / deg_per_px))))


def bt_height_m(bt_c: float, lat: float) -> float:
    """Return a cloud top height (m) for a brightness temperature, from a standard atmosphere.

    Surface 28 C in the tropics, 0.45 C cooler per degree of latitude beyond
    15 degrees, falling 6.5 C per km up to the tropopause (17 km in the
    tropics, about 11 km at 60 degrees). An estimate: a real sounding can
    differ by a kilometre or two.
    """
    surface = 28.0 - 0.45 * max(0.0, abs(lat) - 15.0)
    tropopause = 17000.0 - 130.0 * max(0.0, abs(lat) - 15.0)
    return max(0.0, min(tropopause, (surface - bt_c) / 6.5 * 1000.0))


def deep_top(top_m: Optional[float], lat: float, bt_c: float = DEEP_BT_C) -> bool:
    """Return True if a cloud top this high is as cold as bt_c (default: a deep convective top)."""
    return top_m is not None and top_m >= bt_height_m(bt_c, lat)


def storm_chance(top_m: Optional[float], lat: float, cape: Optional[float],
                 showers: Optional[float]) -> Optional[tuple]:
    """Return fw_storms' (chance per hour, peak dBZ) for a satellite cloud top, or None.

    top_m: the highest cloud top near the grid point; cape (J/kg) and showers
    (mm/h): the forecast's there (None: unknown, no storm).
    """
    if cape is None or cape < CAPE_MIN:
        return None
    for bt_c, shw, chance, dbz in SAT_TIERS:
        if deep_top(top_m, lat, bt_c) and (showers or 0.0) >= shw:
            return chance, dbz
    return None


# --- GOES ------------------------------------------------------------------

def goes_listing_url(sat: str, hour: datetime) -> str:
    """Return the bucket listing URL of one hour of a GOES satellite's Cloud Top Height files."""
    return (f"https://{GOES[sat][0]}.s3.amazonaws.com/?list-type=2&prefix="
            f"{GOES_PRODUCT}/{hour:%Y/%j/%H}/")


def goes_file_url(sat: str, key: str) -> str:
    """Return the download URL of a file key from the listing."""
    return f"https://{GOES[sat][0]}.s3.amazonaws.com/{key}"


def goes_latest_key(listing_xml: str) -> Optional[str]:
    """Return the newest file key in a bucket listing, or None."""
    keys = sorted(re.findall(r"<Key>([^<]+\.nc)</Key>", listing_xml))
    return keys[-1] if keys else None


def goes_time(key: str) -> Optional[datetime]:
    """Return a file's scan start time (UTC) from its name (..._s20263241230216_...), or None."""
    m = re.search(r"_s(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})", key)
    if not m:
        return None
    year, doy, hh, mm, ss = (int(x) for x in m.groups())
    return datetime(year, 1, 1, hh, mm, ss, tzinfo=timezone.utc) + timedelta(days=doy - 1)


def _goes_scan_angles(lat: float, lon: float, sub_lon: float) -> Optional[tuple]:
    """Return the GOES fixed-grid scan angles (x, y) in radians of a point, or None (not seen).

    The GOES-R Product User's Guide's navigation (geostationary projection,
    sweep axis x, GRS80 ellipsoid).
    """
    phi, lam = math.radians(lat), math.radians(lon - sub_lon)
    e2 = 1.0 - (_GRS80_B / _GRS80_A) ** 2
    phi_c = math.atan((_GRS80_B / _GRS80_A) ** 2 * math.tan(phi))
    r_c = _GRS80_B / math.sqrt(1.0 - e2 * math.cos(phi_c) ** 2)
    s_x = _GOES_H - r_c * math.cos(phi_c) * math.cos(lam)
    s_y = -r_c * math.cos(phi_c) * math.sin(lam)
    s_z = r_c * math.sin(phi_c)
    # the satellite sees the point only if it isn't behind the Earth's limb
    if _GOES_H * (_GOES_H - s_x) < s_y ** 2 + (_GRS80_A / _GRS80_B) ** 2 * s_z ** 2:
        return None
    return (math.asin(-s_y / math.sqrt(s_x ** 2 + s_y ** 2 + s_z ** 2)),
            math.atan(s_z / s_x))


def goes_grid(data: bytes, bbox: tuple, size: tuple) -> list:  # pylint: disable=too-many-locals
    """Return a Cloud Top Height file's heights (m) on the bbox grid, row by row, None = none.

    Rows run north to south, like fw_cloudtop.decode(). Raises ImportError
    without h5py.
    """
    import h5py  # pylint: disable=import-outside-toplevel,import-error

    def attr(var, name):
        return float(var.attrs[name][0])  # pylint: disable=unsubscriptable-object

    with h5py.File(io.BytesIO(data), "r") as f:
        ht = f["HT"]
        fill, scale = int(attr(ht, "_FillValue")), attr(ht, "scale_factor")
        heights = ht[:]
        x_scale, x_off = attr(f["x"], "scale_factor"), attr(f["x"], "add_offset")
        y_scale, y_off = attr(f["y"], "scale_factor"), attr(f["y"], "add_offset")
        sub_lon = attr(f["goes_imager_projection"], "longitude_of_projection_origin")
    n_rows, n_cols = heights.shape  # pylint: disable=no-member
    lat_min, lon_min, lat_max, lon_max = bbox
    width, height = size
    dlat, dlon = (lat_max - lat_min) / height, (lon_max - lon_min) / width
    rows = []
    for y in range(height):
        lat = lat_max - (y + 0.5) * dlat
        row = []
        for x in range(width):
            angles = _goes_scan_angles(lat, lon_min + (x + 0.5) * dlon, sub_lon)
            value = None
            if angles is not None:
                col = round((angles[0] - x_off) / x_scale)
                line = round((angles[1] - y_off) / y_scale)
                if 0 <= line < n_rows and 0 <= col < n_cols:
                    raw = int(heights[line, col])
                    if raw != fill:
                        value = raw * scale
            row.append(value)
        rows.append(row)
    return rows


# --- Himawari via GIBS -----------------------------------------------------

def gibs_colormap(xml: str) -> dict:
    """Return {(r, g, b): brightness temperature C} from the GIBS colour map XML."""
    out: dict = {}
    for rgb, lo, hi in re.findall(r'rgb="(\d+,\d+,\d+)"[^>]*?value="\(([-\d.]+),([-\d.]+)\]"',
                                  xml):
        out.setdefault(tuple(int(c) for c in rgb.split(",")), (float(lo) + float(hi)) / 2.0)
    return out


def gibs_times(now: datetime) -> list:
    """Return the image times to try (newest first), on the 10-minute clock, GIBS_LATE_MIN back."""
    base = now.replace(second=0, microsecond=0)
    base -= timedelta(minutes=base.minute % GIBS_STEP_MIN)
    return [(base - timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%SZ") for m in GIBS_LATE_MIN]


def gibs_params(bbox: tuple, size: tuple, when: str) -> dict:
    """Return the GIBS WMS 1.3.0 GetMap query (EPSG:4326 axis order: lat, lon)."""
    return {"SERVICE": "WMS", "REQUEST": "GetMap", "VERSION": "1.3.0", "LAYERS": GIBS_LAYER,
            "CRS": "EPSG:4326", "BBOX": ",".join(f"{v:.4f}" for v in bbox),
            "WIDTH": str(size[0]), "HEIGHT": str(size[1]), "FORMAT": "image/png",
            "TIME": when}


def gibs_grid(img, bbox: tuple, colormap: dict) -> Optional[list]:
    """Return a GIBS IR image's cloud top heights (m) row by row (None = no data), or None.

    img: a PIL RGBA image. None if it has no data at all (an image time GIBS
    doesn't have yet comes back blank).
    """
    width, height = img.size
    lat_max, dlat = bbox[2], (bbox[2] - bbox[0]) / height
    data = list(img.getdata())
    rows, found = [], False
    for y in range(height):
        lat = lat_max - (y + 0.5) * dlat
        row = []
        for pixel in data[y * width:(y + 1) * width]:
            bt = colormap.get(pixel[:3]) if pixel[3] >= 128 else None
            row.append(None if bt is None else bt_height_m(bt, lat))
            found = found or bt is not None
        rows.append(row)
    return rows if found else None
