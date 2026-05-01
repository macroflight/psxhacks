"""Open-Meteo wind profile fetcher with in-memory cache.

Fetches multi-level wind (speed, direction, geopotential height) from the
Open-Meteo free forecast API — no API key required.

Cache strategy
--------------
* Position bucket : rounded to CACHE_DEG_GRID degrees.  Wind varies smoothly
  enough that a 1° grid (~111 km) is adequate for turbulence modelling.
* Time bucket     : rounded down to CACHE_HOURS hours.  Open-Meteo updates
  forecasts hourly; there is no benefit refreshing more often.

A cached entry is served as long as both the position bucket and the time
bucket match the current request.  On a mismatch the full hourly array for
the new bucket is fetched and parsed once; subsequent calls within the same
bucket hit the cache.
"""

import logging
import math
from datetime import datetime, timezone
from typing import Optional

import numpy as np  # pylint: disable=import-error
import requests  # pylint: disable=import-error

from .profile import WindProfile

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pressure levels to request — covers surface to above typical cruise levels.
# Open-Meteo supports all of these for the GFS/ECMWF ensemble.
# ---------------------------------------------------------------------------
LEVELS_HPA: list[int] = [
    1000, 975, 950, 925, 900, 850, 800, 750, 700,
    650, 600, 550, 500, 450, 400, 350, 300,
    250, 200, 150, 100,
]

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
REQUEST_TIMEOUT_S = 15

# Cache granularity
CACHE_DEG_GRID = 1.0    # degrees — position bucket size
CACHE_HOURS = 1         # re-fetch after this many hours

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

    def __init__(self, models: str = "best_match"):
        """Initialize the wind fetcher with the specified model."""
        self._models = models
        # Cache: (pos_bucket, time_bucket) → WindProfile
        self._cache: dict[tuple, WindProfile] = {}

    def get(
        self,
        lat: float,
        lon: float,
        sim_time_utc: Optional[datetime] = None,
    ) -> Optional[WindProfile]:
        """Return a WindProfile for (lat, lon) valid at sim_time_utc.

        Uses cached data when the position and time bucket match.
        Returns None if the fetch fails (network error, etc.).

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

        pos_key = _pos_bucket(lat, lon)
        time_key = _time_bucket(sim_time_utc)
        cache_key = (pos_key, time_key)

        if cache_key in self._cache:
            log.debug("Wind cache hit for %s", cache_key)
            return self._cache[cache_key]

        log.info(
            "Fetching wind profile at (%.2f, %.2f) valid %s …",
            lat, lon, sim_time_utc.strftime("%Y-%m-%dT%H:%M")
        )
        profile = self._fetch(lat, lon, sim_time_utc)
        if profile is not None:
            self._cache[cache_key] = profile
            log.info("Wind profile fetched: %s", profile)

        return profile

    # ------------------------------------------------------------------
    # HTTP fetch + parse
    # ------------------------------------------------------------------

    def _fetch(
        self, lat: float, lon: float, target_time: datetime
    ) -> Optional[WindProfile]:
        params = {
            "latitude": round(lat, 4),
            "longitude": round(lon, 4),
            "hourly": _HOURLY_PARAMS,
            "wind_speed_unit": "kn",
            "timeformat": "unixtime",
            "forecast_days": 2,     # ensure target_time is always covered
            "models": self._models,
        }

        try:
            r = requests.get(
                OPEN_METEO_URL, params=params, timeout=REQUEST_TIMEOUT_S
            )
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as exc:
            log.error("Wind fetch failed: %s", exc)
            return None
        except ValueError as exc:
            log.error("Wind JSON parse error: %s", exc)
            return None

        return self._parse(data, lat, lon, target_time)

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

        for lvl in LEVELS_HPA:
            spd = _scalar(hourly, f"windspeed_{lvl}hPa", hour_idx)
            dir_ = _scalar(hourly, f"winddirection_{lvl}hPa", hour_idx)
            alt = _scalar(hourly, f"geopotential_height_{lvl}hPa", hour_idx)

            pressures.append(float(lvl))
            altitudes.append(alt)       # may be NaN
            speeds.append(spd)          # may be NaN
            directions.append(dir_)     # may be NaN

        pressures_arr = np.array(pressures, dtype=np.float32)
        altitudes_arr = np.array(altitudes, dtype=np.float32)
        speeds_arr = np.array(speeds, dtype=np.float32)
        directions_arr = np.array(directions, dtype=np.float32)

        # Sort by altitude ascending (low pressure = high altitude is last).
        order = np.argsort(altitudes_arr)
        pressures_arr = pressures_arr[order]
        altitudes_arr = altitudes_arr[order]
        speeds_arr = speeds_arr[order]
        directions_arr = directions_arr[order]

        return WindProfile(
            lat=float(data.get("latitude", lat)),
            lon=float(data.get("longitude", lon)),
            fetched_at=datetime.now(timezone.utc),
            valid_at=valid_at,
            pressures_hpa=pressures_arr,
            altitudes_m=altitudes_arr,
            speeds_kt=speeds_arr,
            directions_deg=directions_arr,
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
