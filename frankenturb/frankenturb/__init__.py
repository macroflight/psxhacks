"""frankenturb — terrain-induced turbulence for Aerowinx PSX.

Quickstart
----------
    from frankenturb import TurbulenceEngine
    from datetime import datetime, timezone

    engine = TurbulenceEngine()           # uses ~/.cache/frankenturb/terrain

    # Parse a PiBaHeAlTas string from PSX:
    lat, lon, alt_ft = engine.parse_psx_position("0;0;3.14159;350000;450000;0.82418;0.19895")

    # Call once per PSX tick (~5 Hz):
    state = engine.compute(lat, lon, alt_ft)

    print(state.intensity, state.kind)
    # 0.43, 'wave'
    # state.vertical = -0.71  → aircraft is in a downdraft
    # state.roll     = nan    → randomise roll perturbation externally
"""

import math
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .terrain import TileCache, ElevationGrid
from .turbulence import TerrainTurbulenceModel, TurbulenceState
from .wind import WindProfile, WindFetcher, make_fixed_wind_profile
from .cb_turbulence import compute_cb_turbulence

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PiBaHeAlTas parser
# ---------------------------------------------------------------------------

def parse_pibahealtas(raw: str) -> tuple[float, float, float, float, float, float, float]:
    """Parse a PSX PiBaHeAlTas string into its seven components.

    Format (semicolon-separated, no trailing semicolon):
      pitch_rad_1e5 ; bank_rad_1e5 ; heading_rad ; alt_ft_1e3 ; tas_kt_1e3
      ; lat_rad ; lon_rad

    Returns
    -------
    (pitch_rad, bank_rad, heading_rad, alt_ft, tas_kt, lat_deg, lon_deg)

    """
    parts = raw.strip().split(";")
    if len(parts) != 7:
        raise ValueError(f"PiBaHeAlTas expects 7 fields, got {len(parts)}: {raw!r}")

    pitch_rad = int(parts[0]) / 100_000.0
    bank_rad = int(parts[1]) / 100_000.0
    heading_rad = float(parts[2])
    alt_ft = int(parts[3]) / 1_000.0
    tas_kt = int(parts[4]) / 1_000.0
    lat_rad = float(parts[5])
    lon_rad = float(parts[6])

    lat_deg = math.degrees(lat_rad)
    lon_deg = math.degrees(lon_rad)

    return pitch_rad, bank_rad, heading_rad, alt_ft, tas_kt, lat_deg, lon_deg


# ---------------------------------------------------------------------------
# TurbulenceEngine
# ---------------------------------------------------------------------------

class TurbulenceEngine:
    """Single entry point combining terrain, wind, and turbulence computation.

    Wraps a TileCache, ElevationGrid, WindFetcher, and TerrainTurbulenceModel
    with sensible defaults.

    Parameters
    ----------
    cache_dir:
        Override the tile cache directory (default: ~/.cache/frankenturb/terrain).
    upwind_km:
        How far upwind to scan for terrain barriers (km).
    roughness_radius_km:
        Radius for terrain roughness window (km).
    max_tiles:
        Maximum terrain tiles held in RAM (~26 MB each; default 9).
    wind_models:
        Open-Meteo model(s) to use for wind profiles ('best_match' by default).

    """

    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        cache_dir: Optional[Path] = None,
        upwind_km: float = 80.0,
        roughness_radius_km: float = 20.0,
        max_tiles: int = 9,
        wind_models: str = "best_match",
    ):
        """Initialize the TurbulenceEngine, setting up all subsystems."""
        kwargs = {"cache_dir": cache_dir} if cache_dir else {}
        self.cache = TileCache(**kwargs)
        self.grid = ElevationGrid(self.cache, max_tiles=max_tiles)
        self.model = TerrainTurbulenceModel(
            self.grid,
            upwind_km=upwind_km,
            roughness_radius_km=roughness_radius_km,
        )
        self.wind_fetcher = WindFetcher(models=wind_models)
        self._last_profile: Optional[WindProfile] = None
        self._fixed_profile: Optional[WindProfile] = None

    # ------------------------------------------------------------------
    # Fixed wind override
    # ------------------------------------------------------------------

    def set_fixed_wind(self, dir_deg: float, speed_kt: float) -> None:
        """Override live weather with a fixed surface wind extrapolated by altitude."""
        self._fixed_profile = make_fixed_wind_profile(dir_deg, speed_kt)

    def clear_fixed_wind(self) -> None:
        """Revert to live Open-Meteo wind data."""
        self._fixed_profile = None

    # ------------------------------------------------------------------
    # PSX helpers
    # ------------------------------------------------------------------

    @staticmethod
    def parse_psx_position(pibahealtas: str) -> tuple[float, float, float]:
        """Extract (lat_deg, lon_deg, alt_ft) from a PSX PiBaHeAlTas string.

        The remaining fields (pitch, bank, heading, TAS) are ignored here
        but available via the standalone parse_pibahealtas() function.
        """
        _, _, _, alt_ft, _, lat_deg, lon_deg = parse_pibahealtas(pibahealtas)
        return lat_deg, lon_deg, alt_ft

    # ------------------------------------------------------------------
    # Tile prefetch
    # ------------------------------------------------------------------

    def prefetch(
        self,
        lat_min: float,
        lat_max: float,
        lon_min: float,
        lon_max: float,
    ) -> list:
        """Pre-download terrain tiles for a bounding box.

        Call this when the destination airport is known so tiles arrive
        before the approach begins.
        """
        return self.cache.prefetch(lat_min, lat_max, lon_min, lon_max)

    # ------------------------------------------------------------------
    # Main compute
    # ------------------------------------------------------------------

    def compute(
        self,
        lat: float,
        lon: float,
        alt_ft: float,
        sim_time_utc: Optional[datetime] = None,
    ) -> TurbulenceState:
        """Compute terrain turbulence for the current position and altitude.

        Wind is sourced from the Open-Meteo multi-level profile (fetched
        and cached automatically).  If the wind fetch fails the previous
        profile is reused; if none is available a calm state is returned.

        Parameters
        ----------
        lat :
            Aircraft latitude (decimal degrees).
        lon :
            Aircraft longitude (decimal degrees).
        alt_ft :
            Pressure altitude (feet).
        sim_time_utc :
            UTC time for selecting the wind forecast hour.
            Defaults to wall-clock UTC.

        Returns
        -------
        TurbulenceState
            .intensity  float  0–1
            .vertical   float  -1…+1 (sink/updraft)  or NaN
            .roll       float  -1…+1 (left/right)     or NaN
            .gust       float  -1…+1 (head/tailwind)  or NaN
            .kind       str    'wave'|'rotor'|'mechanical'|'shear'|'none'

        """
        if self._fixed_profile is not None:
            return self.model.compute(lat, lon, alt_ft, self._fixed_profile)

        profile = self.wind_fetcher.get(lat, lon, sim_time_utc)
        if profile is None:
            if self._last_profile is None:
                log.warning("No wind profile available — returning calm")
                return TurbulenceState()
            log.warning("Wind fetch failed — reusing last profile")
            profile = self._last_profile
        else:
            self._last_profile = profile

        return self.model.compute(lat, lon, alt_ft, profile)

    def compute_from_psx(
        self,
        pibahealtas: str,
        sim_time_utc: Optional[datetime] = None,
    ) -> TurbulenceState:
        """Parse a raw PiBaHeAlTas string and compute turbulence.

        Parameters
        ----------
        pibahealtas :
            Raw value string from PSX subscription.
        sim_time_utc :
            UTC time for wind forecast selection.

        Returns
        -------
        TurbulenceState

        """
        lat, lon, alt_ft = self.parse_psx_position(pibahealtas)
        return self.compute(lat, lon, alt_ft, sim_time_utc)


__all__ = [
    "TurbulenceEngine",
    "TurbulenceState",
    "TerrainTurbulenceModel",
    "WindProfile",
    "WindFetcher",
    "make_fixed_wind_profile",
    "TileCache",
    "ElevationGrid",
    "parse_pibahealtas",
    "compute_cb_turbulence",
]
