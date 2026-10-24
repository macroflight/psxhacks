"""Open-Meteo wind profile fetcher with in-memory and on-disk cache.

Fetches multi-level wind (speed, direction, geopotential height,
temperature) from the Open-Meteo free forecast API — no API key required.

Cache strategy
--------------
* The whole 2-day hourly download per 1° square (~111 km; wind varies
  smoothly enough for turbulence modelling) is kept on disk by
  frankenturb.openmeteo.HourlyForecastCache, and every hour is served from
  it: at ~10 Open-Meteo calls per download (104 variables), downloading it
  again every hour for the same data was by far frankenweather's biggest
  use of the daily quota. It's refetched after 6 h (newer forecast runs).
* Parsed profiles are kept in memory per (square, hour).
"""

import logging
import math
import os
from datetime import datetime, timezone
from typing import Optional

import numpy as np  # pylint: disable=import-error
from ..openmeteo import HourlyForecastCache
from .profile import WindProfile

log = logging.getLogger(__name__)

# Default on-disk cache location (whole hourly downloads, see module docstring)
_DEFAULT_CACHE_PATH = os.path.join(
    os.path.expanduser("~"), ".cache", "frankenturb", "om_wind_hourly.json")

# ---------------------------------------------------------------------------
# Pressure levels to request — covers surface to above typical cruise levels.
# Open-Meteo supports all of these for the GFS/ECMWF ensemble.
# 70 hPa (~18.5 km) is there for WindProfile.tropopause(): the tropical
# tropopause sits at or just below 100 hPa, and without a level above it
# the WMO lapse-rate test has nothing to compare 100 hPa against, so no
# tropopause is ever found anywhere in the tropics.
# 275/225/175/125 hPa fill in the cruise/tropopause region (~30-50k ft):
# with only 300/250/200/150/100 the detected tropopause could only take
# one of those levels' heights, 4-6k ft apart, and flipped between them
# from one forecast to the next (2026-10-22 VHHH-EGLL: 33.7k/38.6k/44.4k/
# 45.4k ft within minutes).
# ---------------------------------------------------------------------------
LEVELS_HPA: list[int] = [
    1000, 975, 950, 925, 900, 850, 800, 750, 700,
    650, 600, 550, 500, 450, 400, 350, 300,
    275, 250, 225, 200, 175, 150, 125, 100, 70,
]

# In-memory cache granularity (the on-disk one is HourlyForecastCache's)
CACHE_DEG_GRID = 1.0    # degrees — position bucket size
CACHE_HOURS = 1         # one parsed profile per this many hours

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_hourly_params() -> str:
    """Build the comma-separated list of hourly variable names."""
    names = []
    for lvl in LEVELS_HPA:
        names += [
            f"windspeed_{lvl}hPa",
            f"winddirection_{lvl}hPa",
            f"geopotential_height_{lvl}hPa",
            f"temperature_{lvl}hPa",
        ]
    return ",".join(names)


_HOURLY_PARAMS = _build_hourly_params()


def _pos_bucket(lat: float, lon: float) -> tuple[int, int]:
    return (
        math.floor(lat / CACHE_DEG_GRID),
        math.floor(lon / CACHE_DEG_GRID),
    )


def _time_bucket(dt: datetime) -> tuple[int, int]:
    """Return (year*10000 + month*100 + day, hour // CACHE_HOURS)."""
    d = dt.date()
    bucket = dt.hour // CACHE_HOURS
    return (d.year * 10000 + d.month * 100 + d.day, bucket)


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------

class WindFetcher:  # pylint: disable=too-few-public-methods
    """Fetch and cache Open-Meteo wind profiles.

    Parameters
    ----------
    models : str
        Open-Meteo weather model(s) to use.  'best_match' lets the API pick
        the highest-resolution available model for the location.

    """

    def __init__(
        self,
        models: str = "best_match",
        proxy: Optional[str] = None,
        cache_path: Optional[str] = None,
    ):
        """Initialize the wind fetcher with the specified model."""
        # Cache: (pos_bucket, time_bucket) → WindProfile
        self._cache: dict[tuple, WindProfile] = {}
        self._hourly = HourlyForecastCache(
            "wind", _HOURLY_PARAMS, cache_path or _DEFAULT_CACHE_PATH,
            models=models, extra_params={"wind_speed_unit": "kn"}, proxy=proxy)

    def get(
        self,
        lat: float,
        lon: float,
        sim_time_utc: Optional[datetime] = None,
    ) -> Optional[WindProfile]:
        """Return a WindProfile for (lat, lon) valid at sim_time_utc.

        Served from the cached hourly download of the position's square
        when there is one (see module docstring). Returns None if there's
        no data (fetch failed, or backing off after a failure).

        Parameters
        ----------
        lat :
            Aircraft latitude (decimal degrees).
        lon :
            Aircraft longitude (decimal degrees).
        sim_time_utc :
            UTC datetime representing the current simulation time.
            Defaults to wall-clock UTC now.

        """
        if sim_time_utc is None:
            sim_time_utc = datetime.now(timezone.utc)
        # Ensure timezone-aware
        if sim_time_utc.tzinfo is None:
            sim_time_utc = sim_time_utc.replace(tzinfo=timezone.utc)

        cache_key = (_pos_bucket(lat, lon), _time_bucket(sim_time_utc))
        if cache_key in self._cache:
            return self._cache[cache_key]
        data = self._hourly.get(lat, lon, sim_time_utc)
        if data is None:
            return None
        profile = self._parse(data, lat, lon, sim_time_utc)
        if profile is not None:
            self._cache[cache_key] = profile
            log.info("Wind profile at (%.2f, %.2f): %s", lat, lon, profile)
        return profile

    def _parse(  # pylint: disable=too-many-locals
        self,
        data: dict,
        lat: float,
        lon: float,
        target_time: datetime,
    ) -> Optional[WindProfile]:
        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        if not times:
            log.error("Open-Meteo returned no hourly data")
            return None

        # Find the hour index closest to target_time.
        target_ts = target_time.timestamp()
        times_arr = np.array(times, dtype=float)
        hour_idx = int(np.argmin(np.abs(times_arr - target_ts)))
        valid_at = datetime.fromtimestamp(int(times_arr[hour_idx]), tz=timezone.utc)

        # Collect one value per pressure level for this hour.
        pressures = []
        altitudes = []
        speeds = []
        directions = []
        temperatures = []

        for lvl in LEVELS_HPA:
            spd = _scalar(hourly, f"windspeed_{lvl}hPa", hour_idx)
            dir_ = _scalar(hourly, f"winddirection_{lvl}hPa", hour_idx)
            alt = _scalar(hourly, f"geopotential_height_{lvl}hPa", hour_idx)
            temp = _scalar(hourly, f"temperature_{lvl}hPa", hour_idx)

            pressures.append(float(lvl))
            altitudes.append(alt)       # may be NaN
            speeds.append(spd)          # may be NaN
            directions.append(dir_)     # may be NaN
            temperatures.append(temp)   # may be NaN

        pressures_arr = np.array(pressures, dtype=np.float32)
        altitudes_arr = np.array(altitudes, dtype=np.float32)
        speeds_arr = np.array(speeds, dtype=np.float32)
        directions_arr = np.array(directions, dtype=np.float32)
        temperatures_arr = np.array(temperatures, dtype=np.float32)

        # Sort by altitude ascending (low pressure = high altitude is last).
        order = np.argsort(altitudes_arr)
        pressures_arr = pressures_arr[order]
        altitudes_arr = altitudes_arr[order]
        speeds_arr = speeds_arr[order]
        directions_arr = directions_arr[order]
        temperatures_arr = temperatures_arr[order]

        return WindProfile(
            lat=float(data.get("latitude", lat)),
            lon=float(data.get("longitude", lon)),
            fetched_at=datetime.now(timezone.utc),
            valid_at=valid_at,
            pressures_hpa=pressures_arr,
            altitudes_m=altitudes_arr,
            speeds_kt=speeds_arr,
            directions_deg=directions_arr,
            temperatures_c=temperatures_arr,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _scalar(hourly: dict, key: str, idx: int) -> float:
    """Extract one value from an hourly array, returning NaN for missing/None."""
    arr = hourly.get(key)
    if arr is None or idx >= len(arr) or arr[idx] is None:
        return float("nan")
    return float(arr[idx])
