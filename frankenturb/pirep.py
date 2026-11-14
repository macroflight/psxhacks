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

import dataclasses
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

# Search radius around the aircraft (what's fetched).
SEARCH_RADIUS_NM = 150

# How far a PIREP's turbulence reaches from where it was reported, fading
# out linearly. A PIREP is one aircraft's experience at one point; it used
# to count (fading) out to SEARCH_RADIUS_NM, and from the distance at fetch
# time, not the aircraft's current one.
INFLUENCE_NM = 40.0

# Maximum PIREP age to consider.
MAX_AGE_H = 2

# Cache duration (s).  A 30-min cache is generous — PIREPs are sparse in time.
CACHE_SECONDS = 1800.0

# Invalidate cache when the aircraft moves this many degrees.
BUCKET_DEG = 1.0

# Altitude half-window for PIREP altitude matching (ft).
ALT_WINDOW_FT = 3_000.0

# AWC turbulence intensity string → 0–1, matching frankenweather's labels
# (light from 0.10, moderate from 0.25, severe from 0.50, extreme from
# 0.75). MOD used to be 0.50 ("severe") and SEV 0.75 ("extreme").
_INTENSITY_MAP = {
    "NEG": 0.0,
    "SMTH": 0.0,
    "SMTH-LGHT": 0.08,
    "SMTH-LGT": 0.08,     # aviationweather.gov's spellings (LGT, not LGHT)
    "LGHT": 0.17,
    "LGT": 0.17,
    "LGHT-MOD": 0.25,
    "LGT-MOD": 0.25,
    "MOD": 0.37,
    "MOD-SEV": 0.50,
    "SEV": 0.60,
    "SEV-EXTM": 0.75,
    "SEV-EXTRM": 0.75,
    "EXTM": 0.85,
    "EXTRM": 0.85,
}
SEV_INTENSITY = _INTENSITY_MAP["SEV"]


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
    distance_nm: float   # distance from the aircraft (find_relevant() updates it)
    raw_int: str         # original AWC intensity code (e.g. "MOD-SEV")


def bbox_around(lat: float, lon: float, radius_nm: float) -> str:
    """Return aviationweather.gov's bbox ("minLat,minLon,maxLat,maxLon") around a point."""
    dlat = radius_nm / 60.0
    dlon = radius_nm / (60.0 * max(0.1, math.cos(math.radians(lat))))
    return (f"{max(-90.0, lat - dlat):.2f},{max(-180.0, lon - dlon):.2f},"
            f"{min(90.0, lat + dlat):.2f},{min(180.0, lon + dlon):.2f}")


def _approx_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Equirectangular great-circle approximation in nautical miles."""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2.0))
    return math.degrees(math.sqrt(dlat ** 2 + dlon ** 2)) * 60.0


def _hundreds_ft(value) -> Optional[float]:
    """Return a level given in hundreds of feet (AWC's fltLvl, tbBas1, tbTop1) in feet, or None."""
    try:
        return float(value) * 100.0
    except (TypeError, ValueError):
        return None


def _intensity(item: dict) -> tuple[float, str]:
    """Return (0-1 intensity, AWC code) of a PIREP's turbulence.

    aviationweather.gov's JSON has tbInt1 (and tbInt2 for a second group,
    e.g. "CONS LGT CHOP OCNL MOD CHOP": tbInt1 LGT, tbInt2 MOD -- halfway
    between the two); older feeds had tbInt.
    """
    first = (item.get("tbInt1") or item.get("tbInt") or "").strip().upper()
    second = (item.get("tbInt2") or "").strip().upper()
    intensity = _INTENSITY_MAP.get(first, 0.0)
    other = _INTENSITY_MAP.get(second, 0.0)
    if other > intensity:
        return (intensity + other) / 2.0, f"{first} OCNL {second}"
    return intensity, first


def _layer_ft(item: dict) -> tuple[Optional[float], Optional[float]]:
    """Return (base_ft, top_ft) of a PIREP's turbulence (MSL), or (None, None).

    The turbulence layer (tbBas1/tbTop1, hundreds of feet) if reported,
    else the flight level (fltLvl); older feeds had altitudeFtMsl(Base/Top).
    """
    base, top = _hundreds_ft(item.get("tbBas1")), _hundreds_ft(item.get("tbTop1"))
    if base is None and top is None:
        level = _hundreds_ft(item.get("fltLvl"))
        if level is None:
            try:
                top = float(item.get("altitudeFtMslTop") or item.get("altitudeFtMsl"))
                base = float(item.get("altitudeFtMslBase") or item.get("altitudeFtMsl"))
            except (TypeError, ValueError):
                return None, None
        else:
            base = top = level
    base = base if base is not None else top
    top = top if top is not None else base
    return min(base, top), max(base, top)


def _age_min(item: dict, now: float) -> float:
    """Return the PIREP's age in minutes (obsTime: epoch seconds, or ISO text in older feeds)."""
    obs = item.get("obsTime")
    if isinstance(obs, (int, float)):
        return (now - float(obs)) / 60.0
    try:
        obs_dt = datetime.datetime.fromisoformat((obs or "").replace("Z", "+00:00"))
        return (now - obs_dt.timestamp()) / 60.0
    except (ValueError, AttributeError, TypeError):
        return 60.0


def _parse_record(
    item: dict,
    query_lat: float,
    query_lon: float,
    now: float,
) -> Optional[PirepRecord]:
    """Parse one AWC JSON item into a PirepRecord, or None if unusable.

    Field names as aviationweather.gov sends them since its 2025 API
    (tbInt1, lat/lon, fltLvl, obsTime in epoch seconds); the older names
    (tbInt, latitude/longitude, altitudeFtMsl*, ISO obsTime) are still
    read. Until 2026-11-14 only the old names were, so no turbulence
    PIREP was ever used from the new API.
    """
    intensity, raw_int = _intensity(item)
    if intensity < 0.05:
        return None
    try:
        plat = float(item["lat"] if "lat" in item else item["latitude"])
        plon = float(item["lon"] if "lon" in item else item["longitude"])
    except (KeyError, TypeError, ValueError):
        return None
    alt_base, alt_top = _layer_ft(item)
    if alt_top is None or (alt_top == 0 and alt_base == 0):
        return None
    return PirepRecord(
        lat=plat,
        lon=plon,
        alt_ft=(alt_top + alt_base) / 2.0,
        alt_top_ft=alt_top,
        alt_base_ft=alt_base,
        intensity=intensity,
        age_min=_age_min(item, now),
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
        # A box around the aircraft: the API refuses lat/lon/distance
        # ("Must specify bounding box or stations ID and radial distance",
        # HTTP 400, which used to look like "no coverage" here)
        params = {
            "bbox": bbox_around(lat, lon, SEARCH_RADIUS_NM),
            "age": str(MAX_AGE_H),
            "format": "json",
        }
        try:
            resp = requests.get(AWC_PIREP_URL, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json() if resp.text.strip() else []
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 400:
                log.warning("PIREP fetch refused: %s", exc.response.text[:120])
                return []  # a request the API doesn't accept: retrying won't help
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
            rec = dataclasses.replace(rec, distance_nm=_approx_nm(lat, lon, rec.lat, rec.lon))
            dist_f = max(0.0, 1.0 - rec.distance_nm / INFLUENCE_NM)
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
    dist_f = max(0.0, 1.0 - rec.distance_nm / INFLUENCE_NM)

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
