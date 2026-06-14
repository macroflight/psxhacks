"""NOAA AWC PIREP turbulence fetcher.

Queries the Aviation Weather Center PIREP API for turbulence reports within
SEARCH_RADIUS_NM of the aircraft.  Results are cached for CACHE_SECONDS to
avoid hammering the API (the cache is invalidated when the aircraft moves by
more than BUCKET_DEG degrees).

Usage::

    fetcher = PirepFetcher()
    rec = fetcher.find_relevant(lat, lon, alt_ft)   # call in an executor
    if rec is not None:
        state = compute_pirep_turbulence(alt_ft, rec)
"""

import datetime
import logging
import math
import time
from dataclasses import dataclass
from typing import Optional

import requests  # pylint: disable=import-error

from .turbulence import TurbulenceState

log = logging.getLogger(__name__)

AWC_PIREP_URL = "https://aviationweather.gov/api/data/pirep"

# Search radius around the aircraft.
SEARCH_RADIUS_NM = 150

# Maximum PIREP age to consider.
MAX_AGE_H = 2

# Cache duration (s).  A 30-min cache is generous — PIREPs are sparse in time.
CACHE_SECONDS = 1800.0

# Invalidate cache when the aircraft moves this many degrees.
BUCKET_DEG = 1.0

# Altitude half-window for PIREP altitude matching (ft).
ALT_WINDOW_FT = 3_000.0

# AWC turbulence intensity string → 0–1
_INTENSITY_MAP = {
    "NEG": 0.0,
    "SMTH": 0.0,
    "SMTH-LGHT": 0.10,
    "LGHT": 0.20,
    "LGHT-MOD": 0.35,
    "MOD": 0.50,
    "MOD-SEV": 0.65,
    "SEV": 0.75,
    "SEV-EXTM": 0.875,
    "EXTM": 1.0,
    "EXTRM": 1.0,
}


@dataclass
class PirepRecord:  # pylint: disable=too-many-instance-attributes
    """Parsed turbulence PIREP."""

    lat: float
    lon: float
    alt_ft: float        # layer midpoint
    alt_top_ft: float
    alt_base_ft: float
    intensity: float     # 0–1
    age_min: float       # minutes since observation
    distance_nm: float   # distance from query point when fetched
    raw_int: str         # original AWC intensity code (e.g. "MOD-SEV")


def _approx_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Equirectangular great-circle approximation in nautical miles."""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2.0))
    return math.degrees(math.sqrt(dlat ** 2 + dlon ** 2)) * 60.0


def _parse_record(
    item: dict,
    query_lat: float,
    query_lon: float,
    now: float,
) -> Optional[PirepRecord]:
    """Parse one AWC JSON item into a PirepRecord, or None if unusable."""
    raw_int = (item.get("tbInt") or "").strip()
    if not raw_int:
        return None
    intensity = _INTENSITY_MAP.get(raw_int.upper(), 0.0)
    if intensity < 0.05:
        return None

    try:
        plat = float(item["latitude"])
        plon = float(item["longitude"])
    except (KeyError, TypeError, ValueError):
        return None

    try:
        raw_top = item.get("altitudeFtMslTop") or item.get("altitudeFtMsl")
        raw_base = item.get("altitudeFtMslBase") or item.get("altitudeFtMsl")
        alt_top = float(raw_top)
        alt_base = float(raw_base)
    except (TypeError, ValueError):
        return None
    if alt_top == 0 and alt_base == 0:
        return None

    try:
        obs_str = (item.get("obsTime") or "").replace("Z", "+00:00")
        obs_dt = datetime.datetime.fromisoformat(obs_str)
        age_min = (now - obs_dt.timestamp()) / 60.0
    except (ValueError, AttributeError):
        age_min = 60.0

    return PirepRecord(
        lat=plat,
        lon=plon,
        alt_ft=(alt_top + alt_base) / 2.0,
        alt_top_ft=alt_top,
        alt_base_ft=alt_base,
        intensity=intensity,
        age_min=age_min,
        distance_nm=_approx_nm(query_lat, query_lon, plat, plon),
        raw_int=raw_int,
    )


class PirepFetcher:
    """Fetch and cache turbulence PIREPs from NOAA AWC."""

    def __init__(self) -> None:
        """Initialise with an empty cache."""
        self._cache: list[PirepRecord] = []
        self._cache_lat: Optional[float] = None
        self._cache_lon: Optional[float] = None
        self._cache_time: float = 0.0

    def _stale(self, lat: float, lon: float) -> bool:
        if self._cache_lat is None:
            return True
        if time.monotonic() - self._cache_time > CACHE_SECONDS:
            return True
        return (abs(lat - self._cache_lat) > BUCKET_DEG or
                abs(lon - self._cache_lon) > BUCKET_DEG)

    def _fetch(self, lat: float, lon: float) -> list[PirepRecord]:
        params = {
            "lat": f"{lat:.2f}",
            "lon": f"{lon:.2f}",
            "distance": str(SEARCH_RADIUS_NM),
            "age": str(MAX_AGE_H),
            "format": "json",
        }
        try:
            resp = requests.get(AWC_PIREP_URL, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 400:
                log.debug("PIREP: no coverage at (%.2f, %.2f)", lat, lon)
                return []  # out-of-coverage area, not a transient error
            log.warning("PIREP fetch failed: %s", exc)
            return self._cache
        except Exception as exc:  # pylint: disable=broad-except
            log.warning("PIREP fetch failed: %s", exc)
            return self._cache

        now = time.time()
        records = []
        for item in (data if isinstance(data, list) else []):
            rec = _parse_record(item, lat, lon, now)
            if rec is not None:
                records.append(rec)

        log.info("PIREP: %d turbulence reports within %d nm", len(records), SEARCH_RADIUS_NM)
        return records

    def clear(self) -> None:
        """Invalidate the cache, forcing a fresh fetch on the next call."""
        self._cache_lat = None

    def find_relevant(self, lat: float, lon: float, alt_ft: float) -> Optional[PirepRecord]:
        """Return the highest-scoring nearby PIREP for the current position.

        Scoring weights intensity by distance and age; only reports within
        ALT_WINDOW_FT of the aircraft altitude (or the reported layer) are
        considered.  Intended to be called from a thread-pool executor.
        """
        if self._stale(lat, lon):
            self._cache = self._fetch(lat, lon)
            self._cache_lat = lat
            self._cache_lon = lon
            self._cache_time = time.monotonic()

        best: Optional[PirepRecord] = None
        best_score = 0.0
        for rec in self._cache:
            if alt_ft > rec.alt_top_ft + ALT_WINDOW_FT:
                continue
            if alt_ft < rec.alt_base_ft - ALT_WINDOW_FT:
                continue
            dist_f = max(0.0, 1.0 - rec.distance_nm / SEARCH_RADIUS_NM)
            age_f = max(0.0, 1.0 - rec.age_min / (MAX_AGE_H * 60.0))
            score = rec.intensity * dist_f * age_f
            if score > best_score:
                best_score = score
                best = rec

        return best


def compute_pirep_turbulence(alt_ft: float, rec: PirepRecord) -> TurbulenceState:
    """Convert a PirepRecord into a TurbulenceState at the current altitude.

    Intensity is the raw PIREP intensity decayed by distance, altitude offset,
    and age.  All directional components are NaN — PIREP reports do not carry
    directional information useful at this spatial scale.
    """
    dist_f = max(0.0, 1.0 - rec.distance_nm / SEARCH_RADIUS_NM)

    if rec.alt_base_ft <= alt_ft <= rec.alt_top_ft:
        alt_f = 1.0
    else:
        excess_ft = min(abs(alt_ft - rec.alt_top_ft), abs(alt_ft - rec.alt_base_ft))
        alt_f = math.exp(-excess_ft / ALT_WINDOW_FT)

    age_f = max(0.0, 1.0 - rec.age_min / (MAX_AGE_H * 60.0))

    intensity = rec.intensity * dist_f * alt_f * age_f
    if intensity < 0.01:
        return TurbulenceState()

    fl_str = f"FL{rec.alt_ft / 100:.0f}"
    return TurbulenceState(
        intensity=min(1.0, intensity),
        kind="pirep",
        reason=(
            f"PIREP {rec.raw_int} at {rec.distance_nm:.0f}nm "
            f"{fl_str} {rec.age_min:.0f}min ago"
        ),
    )
