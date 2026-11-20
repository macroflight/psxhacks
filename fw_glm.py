"""Satellite lightning for frankenweather: the GOES Geostationary Lightning Mappers (GLM).

GOES-19 (GOES-East, 75.2 W) sees the Americas and the Atlantic, GOES-18
(GOES-West, 137.0 W) the eastern Pacific and western North America. NOAA
publishes every flash in NetCDF files of 20 seconds each (GLM-L2-LCFA,
about 150-300 KB) on its public cloud buckets, about 20-40 seconds after
the fact, free and without an account (NOAA Open Data Dissemination).

frankenweather fetches one file a minute (a third of the flashes is plenty
to tell where it's lightning) and turns the flashes into the same "flash
pixels" as MTG's Lightning Imager (fw_lightning): one 0.05 degree pixel per
5-minute step that had flashes in it, over the latest 15 minutes. Reading
the files needs h5py (optional: without it, no GOES lightning).

Pure functions only: frankenweather.py fetches.
"""
import io
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

# satellite -> (public bucket, sub-satellite longitude)
SATS = {"G19": ("noaa-goes19", -75.2), "G18": ("noaa-goes18", -137.0)}
PRODUCT = "GLM-L2-LCFA"
# GLM sees up to about 55 degrees of arc from the point under the satellite
# (the lens: roughly 54 N to 54 S under it, less towards the disk's edge)
COVERAGE_ARC_DEG = 55.0
DEG_PER_PX = 0.05            # the same pixels as MTG's Lightning Imager (fw_lightning)
STEP_MIN = 5
STEPS = 3                    # 15 minutes of flashes
KEEP_S = STEP_MIN * STEPS * 60 + 120   # files older than this are dropped


def _arc_deg(lat: float, lon: float, sub_lon: float) -> float:
    """Return the great-circle angle from (lat, lon) to the point under a satellite at sub_lon."""
    return math.degrees(math.acos(max(-1.0, min(1.0, math.cos(math.radians(lat)) *
                                                math.cos(math.radians(lon - sub_lon))))))


def satellite(lat: float, lon: float) -> Optional[str]:
    """Return the GLM ("G19" or "G18") that sees (lat, lon) best, or None if neither does."""
    arc, sat = min((_arc_deg(lat, lon, sub), s) for s, (_, sub) in SATS.items())
    return sat if arc <= COVERAGE_ARC_DEG else None


def listing_url(sat: str, hour: datetime) -> str:
    """Return the bucket listing URL of one hour of a satellite's GLM files."""
    return (f"https://{SATS[sat][0]}.s3.amazonaws.com/?list-type=2&prefix="
            f"{PRODUCT}/{hour:%Y/%j/%H}/")


def file_url(sat: str, key: str) -> str:
    """Return the download URL of a file key from the listing."""
    return f"https://{SATS[sat][0]}.s3.amazonaws.com/{key}"


def keys(listing_xml: str) -> list:
    """Return the file keys in a bucket listing (oldest first)."""
    return sorted(re.findall(r"<Key>([^<]+\.nc)</Key>", listing_xml))


def start_time(key: str) -> Optional[datetime]:
    """Return a file's start time (UTC) from its name (..._s20263241200200_...), or None."""
    m = re.search(r"_s(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})", key)
    if not m:
        return None
    year, doy, hh, mm, ss = (int(x) for x in m.groups())
    return datetime(year, 1, 1, hh, mm, ss, tzinfo=timezone.utc) + timedelta(days=doy - 1)


def one_per_minute(file_keys: list, since: datetime) -> list:
    """Return the first file of every UTC minute that starts at or after since (oldest first).

    The first file of a minute (seconds 00-20) in every sim, so sims nearby
    count the same flashes.
    """
    picked, last = [], None
    for key in file_keys:
        t = start_time(key)
        if t is None or t < since:
            continue
        minute = t.replace(second=0, microsecond=0)
        if minute != last:
            picked.append(key)
            last = minute
    return picked


def flashes(data: bytes) -> list:
    """Return the (lat, lon) of every good flash in a GLM-L2-LCFA file (quality flag 0).

    Raises ImportError without h5py.
    """
    import h5py  # pylint: disable=import-outside-toplevel,import-error
    with h5py.File(io.BytesIO(data), "r") as f:
        lats, lons, quality = f["flash_lat"][:], f["flash_lon"][:], f["flash_quality_flag"][:]
    return [(float(la), float(lo)) for la, lo, q in zip(lats, lons, quality) if q == 0]


def pixels(files: dict, now: datetime, bbox: tuple) -> list:  # pylint: disable=too-many-locals
    """Return the flash pixels [(lat, lon), ...] of the latest STEPS 5-minute steps inside bbox.

    files: {key: (start time, [(lat, lon), ...])}. One pixel per 5-minute
    step with flashes in it (pixel centres on the fw_lightning grid), like
    MTG's Accumulated Flash Area images.
    """
    lat_min, lon_min, lat_max, lon_max = bbox
    step_s = STEP_MIN * 60
    newest_step = int(now.timestamp() // step_s)
    seen = set()
    for t, points in files.values():
        step = int(t.timestamp() // step_s)
        if not newest_step - STEPS < step <= newest_step:
            continue
        for lat, lon in points:
            if not lat_min <= lat <= lat_max:
                continue
            dlon = ((lon - lon_min + 180.0) % 360.0) - 180.0
            if not 0.0 <= dlon <= lon_max - lon_min:
                continue
            seen.add((step, math.floor(lat / DEG_PER_PX), math.floor(lon / DEG_PER_PX)))
    return [((y + 0.5) * DEG_PER_PX, (((x + 0.5) * DEG_PER_PX + 180.0) % 360.0) - 180.0)
            for _, y, x in sorted(seen)]
