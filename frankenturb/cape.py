"""CAPE and Lifted Index convective turbulence estimator.

Queries Open-Meteo for surface-level CAPE (J/kg) and Lifted Index (°C) and
converts them into a TurbulenceState.  CAPE-driven instability produces
convective turbulence even when no explicit CB is configured in PSX.

Cache strategy mirrors WindFetcher: 1° position bucket, 1-hour time bucket,
persisted to disk (see frankenturb.disk_cache) so restarts reuse it.
"""

import logging
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import requests  # pylint: disable=import-error

from .disk_cache import DiskCache
from .turbulence import TurbulenceState

log = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
REQUEST_TIMEOUT_S = 15
RATE_LIMIT_BACKOFF_S = 300.0  # pause all fetches after a 429 for this long
# Pause fetches after any other failure (503, timeout, connection error, bad
# JSON) for this long. Without this, a fetch failure at a bucket that never
# got cached leaves the next compute() tick (every 200ms, see turbulence_coro
# in frankenweather.py) free to retry immediately, hammering Open-Meteo at up
# to 5Hz until it succeeds -- observed live during a transient OM 503.
GENERIC_FAILURE_BACKOFF_S = 30.0

CACHE_DEG_GRID = 1.0
CACHE_HOURS = 1

# Default on-disk cache location — see frankenturb.disk_cache for why a
# generous max age here is just disk hygiene, not a freshness change: the
# cache key already encodes the hour bucket.
_DEFAULT_CACHE_PATH = os.path.join(
    os.path.expanduser("~"), ".cache", "frankenturb", "om_cape.json")
_DISK_CACHE_MAX_AGE_S = 6 * 3600.0

# CAPE (J/kg) thresholds for turbulence intensity segments.
# Raised to avoid over-triggering in regions with moderate convective
# instability that do not produce significant in-flight turbulence.
_CAPE_MIN_J_KG = 1000.0
_CAPE_MOD_J_KG = 2500.0
_CAPE_SEV_J_KG = 4500.0
_CAPE_EXT_J_KG = 7000.0

# Altitude above which CAPE turbulence decays.  CAPE is a surface instability
# signal; in-flight turbulence from it is primarily a low/mid-troposphere
# phenomenon.  Active CBs at cruise altitude are handled by the CB source.
_CAPE_ALT_DECAY_FT = 15_000.0   # FL150: start of altitude decay
_CAPE_ALT_SCALE_FT = 7_000.0    # e-folding scale above FL150


@dataclass
class CapeSample:
    """CAPE and Lifted Index snapshot from Open-Meteo."""

    lat: float
    lon: float
    cape_j_kg: float
    lifted_index_c: float
    showers_mm_h: float
    valid_at: datetime


def _pos_bucket(lat: float, lon: float) -> tuple[int, int]:
    return (int(math.floor(lat / CACHE_DEG_GRID)),
            int(math.floor(lon / CACHE_DEG_GRID)))


def _time_bucket(dt: datetime) -> tuple[int, int]:
    d = dt.date()
    return (d.year * 10000 + d.month * 100 + d.day, dt.hour // CACHE_HOURS)


class CapeFetcher:
    """Fetch and cache CAPE + Lifted Index from Open-Meteo."""

    def __init__(
        self,
        models: str = "best_match",
        proxy: Optional[str] = None,
        cache_path: Optional[str] = None,
    ) -> None:
        """Initialise with an empty cache."""
        self._models = models
        self._proxy = proxy
        self._cache: dict[tuple, CapeSample] = {}
        self._disk_cache = DiskCache(
            cache_path or _DEFAULT_CACHE_PATH, max_age_s=_DISK_CACHE_MAX_AGE_S)
        self._backoff_until: float = 0.0

    @staticmethod
    def _encode(sample: CapeSample) -> dict:
        """Encode a CapeSample as a JSON-serializable dict."""
        return {
            "lat": sample.lat,
            "lon": sample.lon,
            "cape_j_kg": sample.cape_j_kg,
            "lifted_index_c": sample.lifted_index_c,
            "showers_mm_h": sample.showers_mm_h,
            "valid_at": sample.valid_at.isoformat(),
        }

    @staticmethod
    def _decode(data: dict) -> CapeSample:
        """Decode a CapeSample from its JSON-serializable dict form."""
        return CapeSample(
            lat=data["lat"],
            lon=data["lon"],
            cape_j_kg=data["cape_j_kg"],
            lifted_index_c=data["lifted_index_c"],
            showers_mm_h=data["showers_mm_h"],
            valid_at=datetime.fromisoformat(data["valid_at"]),
        )

    def get(
        self,
        lat: float,
        lon: float,
        sim_time_utc: Optional[datetime] = None,
    ) -> Optional[CapeSample]:
        """Return a CapeSample for (lat, lon) valid at sim_time_utc.

        Uses cached data when the position and time bucket match.
        Returns None if the fetch fails.
        """
        if sim_time_utc is None:
            sim_time_utc = datetime.now(timezone.utc)
        if sim_time_utc.tzinfo is None:
            sim_time_utc = sim_time_utc.replace(tzinfo=timezone.utc)

        pos_key = _pos_bucket(lat, lon)
        time_key = _time_bucket(sim_time_utc)
        cache_key = (pos_key, time_key)

        if cache_key in self._cache:
            return self._cache[cache_key]

        disk_key = f"{pos_key}|{time_key}"
        disk_hit = self._disk_cache.get(disk_key)
        if disk_hit is not None:
            log.debug("CAPE disk-cache hit for %s", cache_key)
            sample = self._decode(disk_hit)
            self._cache[cache_key] = sample
            return sample

        remaining = self._backoff_until - time.monotonic()
        if remaining > 0:
            log.debug("CAPE fetch skipped — backing off (%.0fs remaining)", remaining)
            return None

        log.info("Fetching CAPE at (%.2f, %.2f) …", lat, lon)
        sample = self._fetch(lat, lon, sim_time_utc)
        if sample is not None:
            self._cache[cache_key] = sample
            self._disk_cache.set(disk_key, self._encode(sample))
        return sample

    def clear(self) -> None:
        """Invalidate the cache, forcing a fresh fetch on the next call."""
        self._cache.clear()

    def _fetch(
        self, lat: float, lon: float, target_time: datetime
    ) -> Optional[CapeSample]:
        params = {
            "latitude": round(lat, 4),
            "longitude": round(lon, 4),
            "hourly": "cape,lifted_index,showers",
            "timeformat": "unixtime",
            "forecast_days": 2,
            "models": self._models,
        }
        proxies = {'http': self._proxy, 'https': self._proxy} if self._proxy else None
        try:
            r = requests.get(OPEN_METEO_URL, params=params, timeout=REQUEST_TIMEOUT_S,
                             proxies=proxies)
            r.raise_for_status()
            data = r.json()
        except requests.exceptions.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 429:
                self._backoff_until = time.monotonic() + RATE_LIMIT_BACKOFF_S
                log.warning("CAPE fetch: OM rate limited — backing off %.0fs", RATE_LIMIT_BACKOFF_S)
            else:
                self._backoff_until = time.monotonic() + GENERIC_FAILURE_BACKOFF_S
                log.error("CAPE fetch failed: %s — backing off %.0fs",
                          exc, GENERIC_FAILURE_BACKOFF_S)
            return None
        except requests.RequestException as exc:
            self._backoff_until = time.monotonic() + GENERIC_FAILURE_BACKOFF_S
            log.error("CAPE fetch failed: %s — backing off %.0fs", exc, GENERIC_FAILURE_BACKOFF_S)
            return None
        except ValueError as exc:
            self._backoff_until = time.monotonic() + GENERIC_FAILURE_BACKOFF_S
            log.error("CAPE JSON parse error: %s — backing off %.0fs",
                      exc, GENERIC_FAILURE_BACKOFF_S)
            return None
        return self._parse(data, lat, lon, target_time)

    def _parse(
        self,
        data: dict,
        lat: float,
        lon: float,
        target_time: datetime,
    ) -> Optional[CapeSample]:
        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        if not times:
            log.error("Open-Meteo CAPE: no hourly data")
            return None

        target_ts = target_time.timestamp()
        hour_idx = min(range(len(times)), key=lambda i: abs(times[i] - target_ts))

        def _scalar(arr, idx):
            if not arr or idx >= len(arr) or arr[idx] is None:
                return float("nan")
            return float(arr[idx])

        cape = _scalar(hourly.get("cape", []), hour_idx)
        li = _scalar(hourly.get("lifted_index", []), hour_idx)
        showers = _scalar(hourly.get("showers", []), hour_idx)
        valid_at = datetime.fromtimestamp(int(times[hour_idx]), tz=timezone.utc)

        log.info("CAPE: %.0f J/kg LI=%+.1f°C showers=%.2fmm/h at (%.2f, %.2f)",
                 cape, li, showers, lat, lon)
        return CapeSample(
            lat=float(data.get("latitude", lat)),
            lon=float(data.get("longitude", lon)),
            cape_j_kg=cape,
            lifted_index_c=li,
            showers_mm_h=showers,
            valid_at=valid_at,
        )


def _precip_scale(showers_mm_h: float) -> float:
    """Scale factor from precipitation rate — CAPE is potential, showers are reality."""
    if math.isnan(showers_mm_h):
        return 1.0
    if showers_mm_h < 0.5:
        return 0.10
    if showers_mm_h < 2.0:
        return 0.40
    if showers_mm_h < 5.0:
        return 0.70
    return 1.0


def compute_cape_turbulence(alt_ft: float, sample: CapeSample) -> TurbulenceState:
    """Convert a CapeSample into a TurbulenceState at the current altitude.

    Intensity is derived from CAPE (primary signal) modulated by Lifted Index.
    Positive LI indicates a capping inversion that suppresses surface-based
    convection; strongly negative LI amplifies it.  CAPE-driven turbulence
    decays exponentially above the tropopause.  All directional components
    are NaN — convective turbulence is chaotic.
    """
    cape = sample.cape_j_kg
    if math.isnan(cape) or cape < _CAPE_MIN_J_KG:
        return TurbulenceState()

    # Map CAPE to 0–1 intensity across four linear segments.
    if cape < _CAPE_MOD_J_KG:
        intensity = 0.25 * (cape - _CAPE_MIN_J_KG) / (_CAPE_MOD_J_KG - _CAPE_MIN_J_KG)
    elif cape < _CAPE_SEV_J_KG:
        intensity = 0.25 + 0.25 * (cape - _CAPE_MOD_J_KG) / (_CAPE_SEV_J_KG - _CAPE_MOD_J_KG)
    elif cape < _CAPE_EXT_J_KG:
        intensity = 0.50 + 0.25 * (cape - _CAPE_SEV_J_KG) / (_CAPE_EXT_J_KG - _CAPE_SEV_J_KG)
    else:
        intensity = min(1.0, 0.75 + 0.25 * (cape - _CAPE_EXT_J_KG) / 3000.0)

    # Lifted Index modifier.
    li = sample.lifted_index_c
    if not math.isnan(li):
        if li > 2.0:
            intensity *= 0.10
        elif li > 0.0:
            intensity *= 1.0 - (li / 2.0) * 0.90
        elif li < -4.0:
            intensity = min(1.0, intensity * 1.10)

    intensity *= _precip_scale(sample.showers_mm_h)
    showers = sample.showers_mm_h

    # Decay above FL150 — convective turbulence from surface instability
    # diminishes rapidly through the mid-troposphere.
    if alt_ft > _CAPE_ALT_DECAY_FT:
        intensity *= math.exp(-(alt_ft - _CAPE_ALT_DECAY_FT) / _CAPE_ALT_SCALE_FT)

    if intensity < 0.01:
        return TurbulenceState()

    li_str = f"{li:+.1f}°C" if not math.isnan(li) else "N/A"
    showers_str = f"{showers:.2f}mm/h" if not math.isnan(showers) else "N/A"
    return TurbulenceState(
        intensity=min(1.0, intensity),
        kind="cape",
        reason=f"CAPE {cape:.0f} J/kg LI {li_str} showers {showers_str}",
    )
