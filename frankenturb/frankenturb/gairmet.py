"""NOAA AWC G-AIRMET Tango turbulence region fetcher.

Queries the Aviation Weather Center G-AIRMET API for currently active
turbulence AIRMETs, then tests whether the aircraft falls inside any
declared region.  G-AIRMETs cover broad geographic areas with altitude
bands and a severity classification — they are the most authoritative
open signal for widespread turbulence (jet-stream CAT, mountain wave
advisories) that the terrain and CAPE models may under-estimate.

Cache strategy: the full set of active regions is fetched once every
CACHE_SECONDS (30 min) regardless of position.  Point-in-polygon tests
are done in-process with a ray-casting algorithm — no dependencies beyond
the standard library.
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

AWC_GAIRMET_URL = "https://aviationweather.gov/api/data/gairmet"

# Cache the full region list for this long (G-AIRMETs are valid for hours).
CACHE_SECONDS = 1800.0

# Extend altitude check beyond band edges by this many feet.
ALT_BUFFER_FT = 2000.0

# Turbulence intensity for each G-AIRMET severity code.
_SEVERITY_MAP = {
    "LGT": 0.20,
    "LGT-MOD": 0.35,
    "MOD": 0.50,
    "MOD-SEV": 0.65,
    "SEV": 0.75,
    "EXTRM": 1.0,
}


@dataclass
class GairmetRegion:
    """One active G-AIRMET turbulence polygon."""

    polygon: list        # (lat, lon) pairs
    alt_low_ft: float
    alt_high_ft: float
    intensity: float     # 0–1 from severity
    severity: str        # original AWC code, e.g. "MOD-SEV"
    due_to: str          # cause string, e.g. "TURB-HI" or "MNTWAVE"


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _point_in_polygon(lat: float, lon: float, polygon: list) -> bool:
    """Ray-casting even-odd point-in-polygon test."""
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        lat_i, lon_i = polygon[i]
        lat_j, lon_j = polygon[j]
        if ((lon_i > lon) != (lon_j > lon)) and (
                lat < (lat_j - lat_i) * (lon - lon_i) / (lon_j - lon_i) + lat_i):
            inside = not inside
        j = i
    return inside


def _extract_polygon(props: dict, geom) -> list:
    """Return polygon as (lat, lon) pairs from GeoJSON geometry or JSON props."""
    if geom:
        gtype = geom.get("type", "")
        coords = geom.get("coordinates", [])
        if gtype == "Polygon" and coords:
            ring = coords[0]
        elif gtype == "MultiPolygon" and coords:
            ring = coords[0][0]
        else:
            ring = []
        if ring:
            # GeoJSON uses [lon, lat] order.
            return [(float(c[1]), float(c[0])) for c in ring if len(c) >= 2]

    # Flat JSON: try known coordinate field names.
    raw = props.get("coordinates") or props.get("area") or []
    if not raw or len(raw[0]) < 2:
        return []
    # If |first element| > 90 it must be a longitude → [lon, lat] order.
    if abs(float(raw[0][0])) > 90.0:
        return [(float(c[1]), float(c[0])) for c in raw]
    return [(float(c[0]), float(c[1])) for c in raw]


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------

class GairmetFetcher:
    """Fetch and cache active G-AIRMET turbulence regions from NOAA AWC."""

    def __init__(self) -> None:
        """Initialise with an empty cache."""
        self._cache: list[GairmetRegion] = []
        self._cache_time: float = 0.0

    def get_active(
        self, lat: float, lon: float, alt_ft: float
    ) -> Optional[GairmetRegion]:
        """Return the highest-severity G-AIRMET region containing (lat, lon, alt_ft).

        Returns None when the aircraft is not inside any active turbulence
        AIRMET or when no regions have been fetched yet.  Intended to be
        called from a thread-pool executor.
        """
        if time.monotonic() - self._cache_time > CACHE_SECONDS:
            self._cache = self._fetch()
            self._cache_time = time.monotonic()

        best: Optional[GairmetRegion] = None
        best_intensity = 0.0
        for region in self._cache:
            if alt_ft < region.alt_low_ft - ALT_BUFFER_FT:
                continue
            if alt_ft > region.alt_high_ft + ALT_BUFFER_FT:
                continue
            if not _point_in_polygon(lat, lon, region.polygon):
                continue
            if region.intensity > best_intensity:
                best_intensity = region.intensity
                best = region
        return best

    def clear(self) -> None:
        """Invalidate the cache, forcing a fresh fetch on the next call."""
        self._cache_time = 0.0

    def _fetch(self) -> list[GairmetRegion]:
        params = {"type": "turb", "format": "json"}
        try:
            resp = requests.get(AWC_GAIRMET_URL, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # pylint: disable=broad-except
            log.warning("G-AIRMET fetch failed: %s", exc)
            return self._cache
        return self._parse(data)

    def _parse(self, data) -> list[GairmetRegion]:  # pylint: disable=too-many-locals
        if isinstance(data, dict) and "features" in data:
            items = [(f.get("properties", {}), f.get("geometry")) for f in data["features"]]
        else:
            items = [(item, None) for item in (data if isinstance(data, list) else [])]

        regions = []
        now = time.time()

        for props, geom in items:
            hazard = (props.get("hazard") or props.get("airmetType") or "").upper()
            if hazard and "TURB" not in hazard:
                continue

            severity = (props.get("severity") or "").strip().upper()
            intensity = _SEVERITY_MAP.get(severity, 0.0)
            if intensity < 0.05:
                continue

            try:
                vt_str = (props.get("validTimeTo") or "").replace("Z", "+00:00")
                valid_to = datetime.datetime.fromisoformat(vt_str).timestamp()
                if valid_to < now:
                    continue
            except (ValueError, AttributeError):
                pass

            try:
                alt_low = float(props.get("altitudeLow1Ft") or 0)
                alt_high = float(props.get("altitudeHi1Ft") or 60000)
            except (TypeError, ValueError):
                continue

            polygon = _extract_polygon(props, geom)
            if len(polygon) < 3:
                continue

            due_to = str(
                props.get("dueTo") or props.get("hazard") or
                props.get("airmetType") or "TURB"
            )
            regions.append(GairmetRegion(
                polygon=polygon,
                alt_low_ft=alt_low,
                alt_high_ft=alt_high,
                intensity=intensity,
                severity=severity,
                due_to=due_to,
            ))

        log.info("G-AIRMET: %d active turbulence regions", len(regions))
        return regions


# ---------------------------------------------------------------------------
# Turbulence computation
# ---------------------------------------------------------------------------

def compute_gairmet_turbulence(alt_ft: float, region: GairmetRegion) -> TurbulenceState:
    """Convert a GairmetRegion into a TurbulenceState at the current altitude.

    Intensity is the region's severity, faded exponentially when the aircraft
    is outside the declared altitude band.  All directional components are NaN
    — G-AIRMET areas may cover mountain wave, jet-stream CAT, or general
    turbulence without specifying a direction at this scale.
    """
    band_half = max(1000.0, (region.alt_high_ft - region.alt_low_ft) / 2.0)
    mid_alt = (region.alt_low_ft + region.alt_high_ft) / 2.0
    offset = abs(alt_ft - mid_alt)
    if offset > band_half:
        alt_f = math.exp(-(offset - band_half) / ALT_BUFFER_FT)
    else:
        alt_f = 1.0

    intensity = region.intensity * alt_f
    if intensity < 0.01:
        return TurbulenceState()

    fl_lo = region.alt_low_ft / 100.0
    fl_hi = region.alt_high_ft / 100.0
    return TurbulenceState(
        intensity=min(1.0, intensity),
        kind="gairmet",
        reason=f"G-AIRMET {region.severity} FL{fl_lo:.0f}-{fl_hi:.0f} {region.due_to}",
    )
