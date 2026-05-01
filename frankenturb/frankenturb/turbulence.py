"""Terrain-induced turbulence model for PSX.

Call compute() once per simulation tick with current PSX state.
The returned TurbulenceState tells you:
  - intensity   (0–1 scale: 0=calm, 0.25=light, 0.5=moderate, 0.75=severe, 1=extreme)
  - vertical    (-1=strong sink, 0=neutral, +1=strong updraft) — NaN if not deterministic
  - roll        (-1=roll left,  0=neutral, +1=roll right)      — NaN if not deterministic
  - gust        (-1=headwind gust, 0=neutral, +1=tailwind gust)— NaN if not deterministic
  - kind        human-readable label for the dominant mechanism

When a component is NaN the caller should apply random perturbations scaled by intensity.

Physical model summary
----------------------
Three mechanisms are modelled:

1. Mechanical (orographic) turbulence
   Low-level chaotic turbulence caused by airflow over rough terrain.
   - Wind used: profile interpolated to the lowest 500 m above terrain
     (captures the actual surface flow, not the aircraft's cruise level wind).
   - Intensity ∝ terrain roughness × surface wind speed × AGL proximity factor.
   - No deterministic direction: all components are NaN (pure random noise).

2. Mountain wave turbulence
   Atmospheric gravity waves downstream of a ridge aligned roughly
   perpendicular to the wind.
   - Wind used: profile at ridge-top altitude (the layer that actually drives waves).
   - Vertical wind shear across the profile amplifies wave intensity.
   - Detected when: significant upwind barrier + wind speed threshold met.
   - Vertical component follows a sinusoidal wave pattern whose half-wavelength
     is estimated from ridge-top wind speed (λ ≈ U × T_WAVE_S).
   - Roll: small ±0.2 × wave phase.  Gust: small antiphase ±0.15.

3. Lee rotor
   Violent recirculation immediately downwind and below ridge top.
   - Detected when aircraft is within ROTOR_DISTANCE_KM of the ridge and
     below ridge top + margin.
   - All components are NaN (chaotic), intensity boosted by low-level jet if present.

Wind shear CAT contribution
   Vertical wind shear across the layer containing the aircraft adds a
   background CAT component that is blended with the dominant mechanism.
"""

import math
from dataclasses import dataclass, field, replace
import numpy as np  # pylint: disable=import-error

from .terrain.elevation import ElevationGrid
from .wind.profile import WindProfile

# ---------------------------------------------------------------------------
# Constants / tunables
# ---------------------------------------------------------------------------

FT_TO_M = 0.3048
KT_TO_MS = 0.514444

# Minimum surface wind speed (m/s) before terrain turbulence is negligible.
MIN_WIND_MS = 3.0

# Scale heights for AGL intensity decay (metres).
MECHANICAL_SCALE_M = 1_500.0
WAVE_SCALE_M = 12_000.0

# Terrain roughness (std-dev of elevation, m) that saturates mechanical turb.
ROUGHNESS_SATURATION_M = 800.0

# Upwind barrier height above current terrain (m) to count as a significant ridge.
BARRIER_THRESHOLD_M = 500.0

# Mountain-wave half-wavelength: T_wave × ridge-top wind speed / 2.
T_WAVE_S = 600.0  # ~10 min, empirical for typical mid-latitude stability

# Rotor zone parameters.
ROTOR_HEIGHT_FRACTION = 1.2   # rotor ceiling = terrain + barrier * this
ROTOR_DISTANCE_KM = 15.0      # max distance downwind for active rotor

# Vertical wind shear thresholds (kt / 1000 ft).
SHEAR_MODERATE = 6.0
SHEAR_SEVERE = 10.0

# Maximum wind speed (m/s) used for normalisation.
WIND_NORM_MS = 30.0

# Height above terrain used to sample "surface" wind from the profile.
SURFACE_SAMPLE_AGL_M = 300.0


# ---------------------------------------------------------------------------
# Output type
# ---------------------------------------------------------------------------

@dataclass
class TurbulenceState:  # pylint: disable=too-few-public-methods
    """Turbulence estimate for one simulation tick.

    Directional components are in [-1, 1].  NaN means "unknown/random" —
    the caller should substitute random noise scaled by intensity.
    """

    intensity: float = 0.0
    vertical: float = field(default_factory=lambda: float("nan"))
    roll: float = field(default_factory=lambda: float("nan"))
    gust: float = field(default_factory=lambda: float("nan"))
    kind: str = "none"
    reason: str = ""

    def is_random(self) -> bool:
        """Return True when all directional components are NaN (pure random noise)."""
        return (
            math.isnan(self.vertical) and
            math.isnan(self.roll) and
            math.isnan(self.gust)
        )


_CALM = TurbulenceState()


# ---------------------------------------------------------------------------
# Main model
# ---------------------------------------------------------------------------

class TerrainTurbulenceModel:  # pylint: disable=too-few-public-methods
    """Terrain-induced turbulence model driven by a multi-level wind profile.

    Parameters
    ----------
    grid:
        ElevationGrid used for all terrain queries.
    upwind_km:
        How far upwind to scan for barriers (km).
    roughness_radius_km:
        Radius of window used to measure terrain roughness (km).

    """

    def __init__(
        self,
        grid: ElevationGrid,
        upwind_km: float = 80.0,
        roughness_radius_km: float = 20.0,
    ):
        """Initialize the turbulence model with a terrain grid and scan parameters."""
        self._grid = grid
        self._upwind_km = upwind_km
        self._roughness_km = roughness_radius_km

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def compute(  # pylint: disable=too-many-locals
        self,
        lat: float,
        lon: float,
        alt_ft: float,
        wind_profile: WindProfile,
    ) -> TurbulenceState:
        """Compute terrain-induced turbulence for the current PSX state.

        Parameters
        ----------
        lat, lon:
            Aircraft position (decimal degrees).
        alt_ft:
            Aircraft pressure altitude (feet).
        wind_profile:
            Multi-level wind profile at this position from WindFetcher.

        Returns
        -------
        TurbulenceState

        """
        alt_m = alt_ft * FT_TO_M

        # Terrain elevation at current position.
        terrain_m = self._grid.elevation_at(lat, lon) or 0.0
        agl_m = max(0.0, alt_m - terrain_m)

        # Wind at ridge-top level drives mountain waves; surface wind drives
        # mechanical turbulence.  We resolve both from the profile.
        surface_alt_m = terrain_m + SURFACE_SAMPLE_AGL_M
        surface_spd_kt, surface_dir_deg = wind_profile.wind_at(surface_alt_m)
        surface_wind_ms = surface_spd_kt * KT_TO_MS

        if surface_wind_ms < MIN_WIND_MS:
            return _CALM

        # ---- 1. Upwind terrain scan ----------------------------------------
        distances_km, elevations_m = self._grid.upwind_profile(
            lat, lon, surface_dir_deg, self._upwind_km
        )
        valid = np.isfinite(elevations_m)
        if not np.any(valid):
            return _CALM

        max_upwind_m = float(np.nanmax(elevations_m))
        barrier_height_m = max(0.0, max_upwind_m - terrain_m)
        ridge_idx = int(np.nanargmax(elevations_m))
        ridge_dist_km = float(distances_km[ridge_idx])

        # ---- 2. Terrain roughness ------------------------------------------
        roughness_m = self._grid.terrain_roughness(lat, lon, self._roughness_km)

        # ---- 3. Vertical wind shear (CAT contribution) ---------------------
        shear = wind_profile.vertical_wind_shear(alt_m)
        shear_factor = _normalise(shear, SHEAR_MODERATE, SHEAR_SEVERE)

        # ---- 4. Dominant mechanism -----------------------------------------
        rotor = self._rotor_conditions(agl_m, terrain_m, max_upwind_m, ridge_dist_km)
        ridge_top_spd_kt, ridge_top_dir_deg = wind_profile.wind_at_ridge_top(max_upwind_m)
        wave = self._wave_conditions(
            barrier_height_m, ridge_top_spd_kt * KT_TO_MS, agl_m
        )

        ridge_top_m_ft = max_upwind_m * 3.28084

        if rotor["active"]:
            state = self._rotor_state(rotor, surface_wind_ms, wind_profile, terrain_m)
            state.reason = (
                f"Lee rotor: wind {surface_dir_deg:.0f}° {surface_spd_kt:.0f}kt "
                f"hitting {ridge_top_m_ft:.0f}ft terrain bearing "
                f"{surface_dir_deg:.0f}° distance {ridge_dist_km:.0f}km"
            )
        elif wave["active"]:
            state = self._wave_state(
                wave, ridge_top_spd_kt * KT_TO_MS,
                lat, lon, ridge_top_dir_deg, agl_m
            )
            state.reason = (
                f"Mountain wave: wind {ridge_top_dir_deg:.0f}° {ridge_top_spd_kt:.0f}kt "
                f"hitting {ridge_top_m_ft:.0f}ft terrain bearing "
                f"{surface_dir_deg:.0f}° distance {ridge_dist_km:.0f}km"
            )
        else:
            state = self._mechanical_state(roughness_m, surface_wind_ms, agl_m)
            state.reason = (
                f"Mechanical: wind {surface_dir_deg:.0f}° {surface_spd_kt:.0f}kt "
                f"over rough terrain (roughness {roughness_m:.0f}m)"
            )

        # ---- 5. Blend in wind-shear CAT ------------------------------------
        if shear_factor > 0.05 and state.intensity < shear_factor * 0.5:
            # Shear CAT dominates — pure random
            return TurbulenceState(
                intensity=max(state.intensity, shear_factor * 0.5),
                kind=f"{state.kind}+shear" if state.kind != "none" else "shear",
                reason=f"Wind shear CAT: {shear:.1f}kt/1000ft vertical shear",
            )
        # Otherwise just boost existing intensity slightly
        return replace(state, intensity=min(1.0, state.intensity + shear_factor * 0.15))

    # ------------------------------------------------------------------
    # Mechanism evaluators
    # ------------------------------------------------------------------

    def _rotor_conditions(
        self,
        agl_m: float,
        terrain_m: float,
        max_upwind_m: float,
        ridge_dist_km: float,
    ) -> dict:
        barrier_height_m = max(0.0, max_upwind_m - terrain_m)
        if barrier_height_m < BARRIER_THRESHOLD_M:
            return {"active": False}

        if ridge_dist_km > ROTOR_DISTANCE_KM:
            return {"active": False}

        rotor_ceiling_agl_m = barrier_height_m * ROTOR_HEIGHT_FRACTION
        if agl_m > rotor_ceiling_agl_m:
            return {"active": False}

        return {
            "active": True,
            "barrier_height_m": barrier_height_m,
            "ridge_dist_km": ridge_dist_km,
        }

    def _wave_conditions(
        self,
        barrier_height_m: float,
        ridge_top_wind_ms: float,
        agl_m: float,
    ) -> dict:
        if barrier_height_m < BARRIER_THRESHOLD_M:
            return {"active": False}
        if ridge_top_wind_ms < 8.0:
            return {"active": False}

        half_lambda_m = ridge_top_wind_ms * T_WAVE_S / 2.0
        alt_factor = math.exp(-agl_m / WAVE_SCALE_M)

        if alt_factor < 0.02:
            return {"active": False}

        return {
            "active": True,
            "barrier_height_m": barrier_height_m,
            "half_lambda_m": half_lambda_m,
            "alt_factor": alt_factor,
        }

    # ------------------------------------------------------------------
    # State constructors
    # ------------------------------------------------------------------

    def _rotor_state(
        self,
        rotor: dict,
        surface_wind_ms: float,
        wind_profile: WindProfile,
        terrain_m: float,
    ) -> TurbulenceState:
        wind_factor = min(1.0, surface_wind_ms / WIND_NORM_MS)
        barrier_factor = min(1.0, rotor["barrier_height_m"] / 2000.0)
        intensity = 0.6 * wind_factor * barrier_factor

        # Low-level jet amplifies rotor violence.
        jet = wind_profile.low_level_jet(search_top_m=terrain_m + 3000.0)
        if jet is not None:
            _, jet_spd_kt, _ = jet
            intensity = min(1.0, intensity * (1.0 + jet_spd_kt / 60.0))

        return TurbulenceState(
            intensity=min(1.0, intensity),
            kind="rotor",
        )

    def _wave_state(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        wave: dict,
        ridge_top_wind_ms: float,
        lat: float,
        lon: float,
        wind_dir_deg: float,
        _agl_m: float,
    ) -> TurbulenceState:
        wind_factor = min(1.0, ridge_top_wind_ms / WIND_NORM_MS)
        barrier_factor = min(1.0, wave["barrier_height_m"] / 3000.0)
        intensity = 0.7 * wind_factor * barrier_factor * wave["alt_factor"]

        phase = self._wave_phase(lat, lon, wind_dir_deg, wave["half_lambda_m"])
        vertical = math.sin(phase)
        roll = 0.2 * math.cos(phase)
        gust = -0.15 * math.sin(phase)

        return TurbulenceState(
            intensity=min(1.0, intensity),
            vertical=vertical,
            roll=roll,
            gust=gust,
            kind="wave",
        )

    def _mechanical_state(
        self, roughness_m: float, surface_wind_ms: float, agl_m: float
    ) -> TurbulenceState:
        roughness_factor = min(1.0, roughness_m / ROUGHNESS_SATURATION_M)
        wind_factor = min(1.0, surface_wind_ms / WIND_NORM_MS)
        agl_factor = math.exp(-agl_m / MECHANICAL_SCALE_M)
        intensity = roughness_factor * wind_factor * agl_factor

        if intensity < 0.01:
            return TurbulenceState()

        return TurbulenceState(
            intensity=min(1.0, intensity),
            kind="mechanical",
        )

    # ------------------------------------------------------------------
    # Wave phase helper
    # ------------------------------------------------------------------

    @staticmethod
    def _wave_phase(
        lat: float, lon: float, wind_dir_deg: float, half_lambda_m: float
    ) -> float:
        """Return spatially consistent wave phase projected onto the downwind axis.

        Phase is based on position projected onto the downwind axis, modulo wavelength.
        """
        downwind_rad = math.radians((wind_dir_deg + 180.0) % 360.0)
        x_m = lon * 111_320.0 * math.cos(math.radians(lat))
        y_m = lat * 111_320.0
        proj = x_m * math.sin(downwind_rad) + y_m * math.cos(downwind_rad)
        wavelength_m = 2.0 * half_lambda_m
        return (proj % wavelength_m) / wavelength_m * 2.0 * math.pi


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalise(value: float, low: float, high: float) -> float:
    """Map value linearly from [low, high] → [0, 1], clamped."""
    if value <= low:
        return 0.0
    if value >= high:
        return 1.0
    return (value - low) / (high - low)
