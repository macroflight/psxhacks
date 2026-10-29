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
   - Intensity from the wind (light from ~25-30 kt at 300 m above the
     ground, moderate from ~50 kt, also over flat land and the sea), more
     over rough terrain, decaying with height above the ground.
   - No deterministic direction: all components are NaN (pure random noise).

2. Mountain wave turbulence
   Atmospheric gravity waves downstream of a ridge aligned roughly
   perpendicular to the wind.
   - Wind used: profile at ridge-top altitude (the layer that actually drives waves).
   - Vertical wind shear across the profile amplifies wave intensity.
   - Ramps in with the wind across the ridge at ridge top (from ~20 kt,
     full at ~43 kt) and the barrier height (200-1500 m).
   - Vertical component follows a sinusoidal wave pattern anchored to the
     ridge (sinking just downwind of it), whose wavelength is estimated
     from ridge-top wind speed (λ ≈ U × T_WAVE_S).
   - Roll: small ±0.2 × wave phase.  Gust: small antiphase ±0.15.

3. Lee rotor
   Violent recirculation immediately downwind and below ridge top.
   - Within ROTOR_DISTANCE_KM of the ridge and below ridge top + margin,
     fading out smoothly towards those limits.
   The strongest of the three mechanisms is used.
   - All components are NaN (chaotic), intensity boosted by low-level jet if present.

Wind shear CAT contribution
   Vertical wind shear across the layer containing the aircraft adds a
   background CAT component that is blended with the dominant mechanism.
"""

import math
import time
from typing import Optional
from dataclasses import dataclass, field, replace

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
MECHANICAL_SCALE_M = 1_000.0
WAVE_SCALE_M = 12_000.0

# Mechanical turbulence (_mechanical_state()): the wind 300 m above the
# ground ramps it in from MECH_WIND_MIN_KT to MECH_MAX at MECH_WIND_FULL_KT
# over very rough terrain, rising continuously with log-scaled roughness
# (std-dev of elevation, MECH_ROUGHNESS_REF_M up to ROUGHNESS_SATURATION_M)
# from half x MECH_SEA_FACTOR of that over the sea or flat water (about
# 0.57 of it over flat land like the London area, 44 m). It used to
# be roughness/800 m x wind/58 kt, so ~4% at most over flat land even in a
# 46 kt wind (EGLL, 2026-10-24), and none at all over the sea.
MECH_WIND_MIN_KT = 20.0
MECH_WIND_FULL_KT = 55.0
MECH_MAX = 0.5
MECH_ROUGHNESS_REF_M = 10.0
MECH_SEA_FACTOR = 0.6

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
# 7 kt/1000ft (just above FAA "significant" threshold) produced VSI swings of
# ~700 fpm in testing — too strong for what should be light chop.  Raising the
# onset to 8 kt/1000ft and severe to 14 kt/1000ft calibrates 10 kt → light and
# 14 kt → moderate, matching real-world reports.
SHEAR_MODERATE = 8.0
SHEAR_SEVERE = 14.0
# Aloft, shear from pressure-level forecast data is smoothed out: on a day
# with a 163 kt jet over the UK (2026-10-24) shear at FL250-410 was 6.5
# kt/1000ft or less 90% of the time, 8.5 or less 98%, so 8/14 left cruise
# almost always smooth. From SHEAR_ALOFT_FT (blended in from
# SHEAR_LOW_FT) these give ~10% light or more and ~2% moderate that day.
SHEAR_ALOFT_MODERATE = 5.0
SHEAR_ALOFT_SEVERE = 12.0
SHEAR_LOW_FT = 10000.0
SHEAR_ALOFT_FT = 20000.0

# Near the ground the wind normally increases steeply with height on a windy
# day; that shear isn't clear-air turbulence. It's capped at
# LOW_LEVEL_SHEAR_CAP (after gustiness) below LOW_LEVEL_SHEAR_AGL_FT, the
# cap rising linearly to none at LOW_LEVEL_SHEAR_BLEND_FT. A pilot departing
# EGLL on a windy day got 100% (2026-10-24).
LOW_LEVEL_SHEAR_CAP = 0.30
# Most the caller's gust multiplier raises terrain turbulence (frankenweather
# _TURB_GUST "lowlevel" 0.5-1.2)
TERRAIN_GUST_MAX = 1.2
LOW_LEVEL_SHEAR_AGL_FT = 3000.0
LOW_LEVEL_SHEAR_BLEND_FT = 5000.0

# Maximum wind speed (m/s) used for normalisation.
# Rotor uses surface wind; wave uses ridge-top wind with a lower norm so that
# strong-wind / moderate-barrier scenarios (e.g. Greenland ice cap: BGTL
# chart calls for severe turbulence at 30kt from 125-225°T) reach the
# moderate-to-severe range rather than staying in the light range.
WIND_NORM_MS = 30.0        # rotor: normalise at 58 kt (high threshold OK for rotors)
# Mountain wave: the wind across the ridge at ridge top ramps it in from
# WAVE_WIND_MIN_MS (~20 kt; real waves need ~25 kt+) to full at
# WAVE_WIND_NORM_MS (~43 kt), and the barrier from WAVE_BARRIER_MIN_M to
# WAVE_BARRIER_NORM_M. It used to switch on at 8 m/s (15 kt) and a 500 m
# barrier, as a step to ~moderate (2026-10-24 review).
WAVE_DECAY_KM = 50.0     # wave strength e-folding distance downwind of the ridge
WAVE_RESET_S = 120.0     # see TerrainTurbulenceModel._wave_phase_at()
WAVE_RESET_KM = 20.0
WAVE_WIND_MIN_MS = 10.0
WAVE_WIND_NORM_MS = 22.0
WAVE_BARRIER_MIN_M = 200.0
WAVE_BARRIER_NORM_M = 1500.0

# Height above terrain used to sample "surface" wind from the profile.
SURFACE_SAMPLE_AGL_M = 300.0


# ---------------------------------------------------------------------------
# Output type
# ---------------------------------------------------------------------------

@dataclass
class TurbulenceState:  # pylint: disable=too-few-public-methods,too-many-instance-attributes
    """Turbulence estimate for one simulation tick.

    Directional components are in [-1, 1].  NaN means "unknown/random" —
    the caller should substitute random noise scaled by intensity.
    source_lat/source_lon: lat/lon of terrain peak driving turbulence (NaN if not terrain).
    max_intensity: ceiling for intensity after the caller's gustiness
    multiplier (low-level wind shear, see LOW_LEVEL_SHEAR_CAP).
    """

    intensity: float = 0.0
    vertical: float = field(default_factory=lambda: float("nan"))
    roll: float = field(default_factory=lambda: float("nan"))
    gust: float = field(default_factory=lambda: float("nan"))
    kind: str = "none"
    reason: str = ""
    source_lat: float = field(default_factory=lambda: float("nan"))
    source_lon: float = field(default_factory=lambda: float("nan"))
    max_intensity: float = 1.0

    def is_random(self) -> bool:
        """Return True when all directional components are NaN (pure random noise)."""
        return (
            math.isnan(self.vertical) and
            math.isnan(self.roll) and
            math.isnan(self.gust)
        )


_CALM = TurbulenceState()


def _dest_point(lat_deg: float, lon_deg: float, bearing_deg: float, dist_km: float):
    """Return (lat, lon) of a point at given bearing and distance from origin."""
    r_earth = 6371.0
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    brng = math.radians(bearing_deg)
    d = dist_km / r_earth
    lat2 = math.asin(
        math.sin(lat) * math.cos(d) + math.cos(lat) * math.sin(d) * math.cos(brng)
    )
    lon2 = lon + math.atan2(
        math.sin(brng) * math.sin(d) * math.cos(lat),
        math.cos(d) - math.sin(lat) * math.sin(lat2),
    )
    return math.degrees(lat2), math.degrees(lon2)


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
        # Mountain wave phase (radians) and where/when it was last advanced
        # (lat, lon, monotonic time), see _wave_phase_at()
        self._wave_phase: Optional[float] = None
        self._wave_last: Optional[tuple] = None

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
        surface_spd_kt, surface_dir_deg = wind_profile.wind_at(surface_alt_m, bottom_m=terrain_m)
        surface_wind_ms = surface_spd_kt * KT_TO_MS

        # No terrain turbulence in light low-level wind or with no terrain
        # upwind (open sea), but there can still be wind-shear CAT aloft
        # (it used to return calm before looking at the shear).
        if surface_wind_ms < MIN_WIND_MS:
            return self._blend_shear(_CALM, wind_profile, alt_m, agl_m)

        # ---- 1. Upwind terrain scan (fan ±30° to catch off-bearing peaks) ---
        barrier_result = self._grid.max_upwind_barrier(
            lat, lon, surface_dir_deg, self._upwind_km
        )
        if barrier_result is None:
            # Open sea: no terrain, but the wind still makes it bumpy low down
            state = self._mechanical_state(None, surface_spd_kt, agl_m)
            state.reason = (f"Mechanical: wind {surface_dir_deg:.0f}° {surface_spd_kt:.0f}kt "
                            "over the sea")
            return self._blend_shear(state, wind_profile, alt_m, agl_m)

        max_upwind_m, ridge_dist_km, ridge_bearing_deg = barrier_result
        peak_lat, peak_lon = _dest_point(lat, lon, ridge_bearing_deg, ridge_dist_km)
        raw_barrier_m = max(0.0, max_upwind_m - terrain_m)
        # Reduce effective barrier for terrain that is off-axis relative to the
        # wind: a peak 30° to the side generates weaker waves than one directly
        # upwind.  cos(offset) provides a smooth, physically-motivated reduction.
        angle_offset = abs((ridge_bearing_deg - surface_dir_deg + 180.0) % 360.0 - 180.0)
        barrier_height_m = raw_barrier_m * math.cos(math.radians(angle_offset))

        # ---- 2. Terrain roughness ------------------------------------------
        roughness_m = self._grid.terrain_roughness(lat, lon, self._roughness_km)

        # ---- 3. Dominant mechanism -----------------------------------------
        rotor = self._rotor_conditions(agl_m, terrain_m, max_upwind_m, ridge_dist_km)
        ridge_top_spd_kt, ridge_top_dir_deg = wind_profile.wind_at_ridge_top(max_upwind_m)
        # Only the wind blowing across the ridge (towards the peak) makes waves
        cross_ms = ridge_top_spd_kt * KT_TO_MS * max(0.0, math.cos(math.radians(
            (ridge_bearing_deg - ridge_top_dir_deg + 180.0) % 360.0 - 180.0)))
        wave = self._wave_conditions(barrier_height_m, cross_ms,
                                     ridge_top_spd_kt * KT_TO_MS, agl_m, ridge_dist_km)

        ridge_top_m_ft = max_upwind_m * 3.28084

        # The strongest of the three. Each fades in and out smoothly, so the
        # turbulence doesn't jump when another takes over (it used to be
        # rotor, else wave, else mechanical, each switching on at hard
        # thresholds: e.g. a 0.30 rotor just below the rotor ceiling, a
        # 0.45 wave just above it).
        candidates = []
        if rotor["active"]:
            state = self._rotor_state(rotor, surface_wind_ms, wind_profile, terrain_m)
            state.reason = (
                f"Lee rotor: wind {surface_dir_deg:.0f}° {surface_spd_kt:.0f}kt "
                f"hitting {ridge_top_m_ft:.0f}ft terrain bearing "
                f"{ridge_bearing_deg:.0f}° distance {ridge_dist_km:.0f}km"
            )
            candidates.append(state)
        if wave["active"]:
            state = self._wave_state(
                wave, self._wave_phase_at(lat, lon, (peak_lat, peak_lon), ridge_top_dir_deg,
                                          2.0 * wave["half_lambda_m"]))
            state.reason = (
                f"Mountain wave: wind {ridge_top_dir_deg:.0f}° {ridge_top_spd_kt:.0f}kt "
                f"hitting {ridge_top_m_ft:.0f}ft terrain bearing "
                f"{ridge_bearing_deg:.0f}° distance {ridge_dist_km:.0f}km"
            )
            candidates.append(state)
        state = self._mechanical_state(roughness_m, surface_spd_kt, agl_m)
        state.reason = (
            f"Mechanical: wind {surface_dir_deg:.0f}° {surface_spd_kt:.0f}kt "
            f"over rough terrain (roughness {roughness_m:.0f}m)"
        )
        candidates.append(state)
        state = max(candidates, key=lambda c: c.intensity)
        state.source_lat = peak_lat
        state.source_lon = peak_lon

        return self._blend_shear(state, wind_profile, alt_m, agl_m)

    @staticmethod
    def _blend_shear(state: TurbulenceState, wind_profile: WindProfile,
                     alt_m: float, agl_m: float) -> TurbulenceState:
        """Return state with vertical wind shear (CAT) blended in."""
        # Not from pressure levels below the ground (extrapolated)
        shear = wind_profile.vertical_wind_shear(alt_m, bottom_m=alt_m - agl_m)
        low, high = _shear_thresholds(alt_m / FT_TO_M)
        shear_factor = _normalise(shear, low, high)
        # The shear's own turbulence, and a boost to the other mechanism's
        # that grows to 0.15 x shear_factor as that one reaches the shear's,
        # so the result is continuous where one takes over from the other.
        # Near the ground (cap < 1) the boost may not lift the other above
        # the cap either: it used to jump from the capped 0.30 to 0.40 x
        # gust (up to 0.60) as soon as mechanical turbulence outweighed
        # the shear. The shear's own turbulence is capped there too, so
        # the caller's gustiness varies it below the cap: it used to stay
        # uncapped (e.g. 0.53) with only max_intensity capping it after
        # the gust, so it was a flat 0.30 nearly all the time (flight
        # test 2026-10-25).
        cap = _low_level_shear_cap(agl_m / FT_TO_M)
        shear_int = min(shear_factor * 0.5, cap)
        boost = shear_factor * 0.15 * (min(1.0, state.intensity / shear_int)
                                       if shear_int > 0.0 else 0.0)
        if shear_factor > 0.05 and state.intensity < shear_int:
            # Shear CAT dominates — pure random
            reason = f"Wind shear CAT: {shear:.1f}kt/1000ft vertical shear"
            if cap < 1.0:
                reason = (f"Low-level wind shear: {shear:.1f}kt/1000ft vertical shear "
                          f"(at most {cap:.0%})")
            return TurbulenceState(
                intensity=min(1.0, cap, shear_int + boost),
                kind=f"{state.kind}+shear" if state.kind != "none" else "shear",
                reason=reason,
                # The other mechanism keeps its own gusts' headroom
                max_intensity=max(cap, state.intensity * TERRAIN_GUST_MAX),
            )
        if cap < 1.0:
            boost = min(boost, max(0.0, cap - state.intensity))
        return replace(state, intensity=min(1.0, state.intensity + boost))

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
        """Return the rotor conditions; "fade" (0-1) ramps it out smoothly.

        Out towards ROTOR_DISTANCE_KM from the ridge (from 5 km before it),
        up towards the rotor ceiling (from 3/4 of it), and down to a barrier
        200 m below BARRIER_THRESHOLD_M. These used to be hard cut-offs.
        """
        barrier_height_m = max(0.0, max_upwind_m - terrain_m)
        rotor_ceiling_agl_m = barrier_height_m * ROTOR_HEIGHT_FRACTION
        fade = (_normalise(barrier_height_m, BARRIER_THRESHOLD_M - 200.0,
                           BARRIER_THRESHOLD_M + 100.0) *
                (1.0 - _normalise(ridge_dist_km, ROTOR_DISTANCE_KM - 5.0, ROTOR_DISTANCE_KM)) *
                (1.0 - _normalise(agl_m, 0.75 * rotor_ceiling_agl_m, rotor_ceiling_agl_m)))
        if fade <= 0.0:
            return {"active": False}
        return {
            "active": True,
            "barrier_height_m": barrier_height_m,
            "ridge_dist_km": ridge_dist_km,
            "fade": fade,
        }

    def _wave_conditions(
        self,
        barrier_height_m: float,
        cross_wind_ms: float,
        wind_ms: float,
        agl_m: float,
        ridge_dist_km: float,
    ) -> dict:
        """Return the mountain wave conditions.

        cross_wind_ms (across the ridge) drives the strength, wind_ms the
        wavelength.

        The wavelength comes from the whole wind, not the part across the
        ridge: that jumps whenever another peak in the upwind fan becomes
        the highest, and with the aircraft several wavelengths downwind any
        wavelength change moves the phase a lot.
        """
        wind_factor = _normalise(cross_wind_ms, WAVE_WIND_MIN_MS, WAVE_WIND_NORM_MS)
        barrier_factor = _normalise(barrier_height_m, WAVE_BARRIER_MIN_M, WAVE_BARRIER_NORM_M)
        if wind_factor <= 0.0 or barrier_factor <= 0.0:
            return {"active": False}

        half_lambda_m = wind_ms * T_WAVE_S / 2.0
        # Waves are strongest near ridge height and weaker below it (aircraft is under
        # the wave, not in it).  Scale linearly from zero at ground to 1.0 at the
        # barrier summit, then let the exponential handle high-altitude decay above.
        below_factor = min(1.0, agl_m / barrier_height_m)
        # Waves die out downwind of the ridge (WAVE_DECAY_KM): it used to
        # be moderate at FL370 5-80 km and more east of the Rockies alike
        alt_factor = (below_factor * math.exp(-agl_m / WAVE_SCALE_M) *
                      math.exp(-ridge_dist_km / WAVE_DECAY_KM))

        if alt_factor < 0.02:
            return {"active": False}

        return {
            "active": True,
            "barrier_height_m": barrier_height_m,
            "half_lambda_m": half_lambda_m,
            "alt_factor": alt_factor,
            "wind_factor": wind_factor,
            "barrier_factor": barrier_factor,
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
        intensity = 0.6 * wind_factor * barrier_factor * rotor["fade"]

        # Low-level jet amplifies rotor violence.
        jet = wind_profile.low_level_jet(search_top_m=terrain_m + 3000.0, bottom_m=terrain_m)
        if jet is not None:
            _, jet_spd_kt, _ = jet
            intensity = min(1.0, intensity * (1.0 + jet_spd_kt / 60.0))

        return TurbulenceState(
            intensity=min(1.0, intensity),
            kind="rotor",
        )

    def _wave_phase_at(self, lat: float, lon: float, peak: tuple, wind_dir_deg: float,
                       wavelength_m: float) -> float:
        """Return the mountain wave phase (radians) at lat/lon.

        On entering a wave (or after WAVE_RESET_S without one, or a jump of
        more than WAVE_RESET_KM) it's anchored to the peak: sinking just
        downwind of it. From then on it only advances with the aircraft's
        downwind movement divided by the current wavelength, so it stays
        continuous when the detected peak (which jumps as the aircraft
        moves), the wind or the wavelength changes -- e.g. at each 1-degree
        wind square or hourly forecast. Computing it from the distance to
        the peak each time made it jump from an up- to a downdraft there.
        """
        now = time.monotonic()
        last = self._wave_last
        moved_m = (math.hypot(_downwind_m(last[0], last[1], lat, lon, 0.0),
                              _downwind_m(last[0], last[1], lat, lon, 90.0))
                   if last is not None else math.inf)
        if (self._wave_phase is None or now - last[2] > WAVE_RESET_S or
                moved_m > WAVE_RESET_KM * 1000.0):
            phase = 2.0 * math.pi * _downwind_m(peak[0], peak[1], lat, lon,
                                                wind_dir_deg) / wavelength_m
        else:
            phase = self._wave_phase + 2.0 * math.pi * _downwind_m(
                last[0], last[1], lat, lon, wind_dir_deg) / wavelength_m
        self._wave_phase = phase % (2.0 * math.pi)
        self._wave_last = (lat, lon, now)
        return self._wave_phase

    @staticmethod
    def _wave_state(wave: dict, phase: float) -> TurbulenceState:
        """Return the mountain wave state at a phase (see _wave_phase_at()).

        Just downwind of the ridge the air sinks, then rises and sinks
        again every wavelength. It used to be the absolute position modulo
        the wavelength, so a 1° wind change moved it by several
        wavelengths: noise, not waves.
        """
        intensity = 0.7 * wave["wind_factor"] * wave["barrier_factor"] * wave["alt_factor"]

        vertical = -math.sin(phase)
        roll = 0.2 * math.cos(phase)
        gust = 0.15 * math.sin(phase)

        return TurbulenceState(
            intensity=min(1.0, intensity),
            vertical=vertical,
            roll=roll,
            gust=gust,
            kind="wave",
        )

    @staticmethod
    def _mechanical_state(
        roughness_m: Optional[float], surface_wind_kt: float, agl_m: float
    ) -> TurbulenceState:
        """Return low-level mechanical turbulence (see MECH_MAX); roughness_m None: over the sea."""
        # From the sea's (also roughness 0: flat land or water) to 1 for
        # very rough terrain, continuously: it used to jump from the sea's
        # 0.3 to 0.5 where the scan first reached a land tile at a coast
        sea = 0.5 * MECH_SEA_FACTOR
        rough = 0.0 if roughness_m is None else min(1.0, max(0.0, math.log1p(
            roughness_m / MECH_ROUGHNESS_REF_M) / math.log1p(
                ROUGHNESS_SATURATION_M / MECH_ROUGHNESS_REF_M)))
        terrain_factor = sea + (1.0 - sea) * rough
        wind_factor = _normalise(surface_wind_kt, MECH_WIND_MIN_KT, MECH_WIND_FULL_KT)
        agl_factor = math.exp(-agl_m / MECHANICAL_SCALE_M)
        intensity = MECH_MAX * wind_factor * terrain_factor * agl_factor

        if intensity < 0.01:
            return TurbulenceState()

        return TurbulenceState(
            intensity=min(1.0, intensity),
            kind="mechanical",
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _downwind_m(from_lat: float, from_lon: float, lat: float, lon: float,
                wind_dir_deg: float) -> float:
    """Return how far (m) lat/lon is downwind of from_lat/from_lon (negative: upwind)."""
    downwind_rad = math.radians((wind_dir_deg + 180.0) % 360.0)
    x_m = (lon - from_lon) * 111_320.0 * math.cos(math.radians(lat))
    y_m = (lat - from_lat) * 111_320.0
    return x_m * math.sin(downwind_rad) + y_m * math.cos(downwind_rad)


def _shear_thresholds(alt_ft: float) -> tuple:
    """Return the (onset, full) wind shear in kt/1000ft at alt_ft (see SHEAR_ALOFT_MODERATE)."""
    frac = min(1.0, max(0.0, (alt_ft - SHEAR_LOW_FT) / (SHEAR_ALOFT_FT - SHEAR_LOW_FT)))
    return (SHEAR_MODERATE + (SHEAR_ALOFT_MODERATE - SHEAR_MODERATE) * frac,
            SHEAR_SEVERE + (SHEAR_ALOFT_SEVERE - SHEAR_SEVERE) * frac)


def _low_level_shear_cap(agl_ft: float) -> float:
    """Return the most wind-shear turbulence there may be at agl_ft (see LOW_LEVEL_SHEAR_CAP)."""
    if agl_ft <= LOW_LEVEL_SHEAR_AGL_FT:
        return LOW_LEVEL_SHEAR_CAP
    if agl_ft >= LOW_LEVEL_SHEAR_BLEND_FT:
        return 1.0
    frac = (agl_ft - LOW_LEVEL_SHEAR_AGL_FT) / (LOW_LEVEL_SHEAR_BLEND_FT - LOW_LEVEL_SHEAR_AGL_FT)
    return LOW_LEVEL_SHEAR_CAP + (1.0 - LOW_LEVEL_SHEAR_CAP) * frac


def _normalise(value: float, low: float, high: float) -> float:
    """Map value linearly from [low, high] → [0, 1], clamped."""
    if value <= low:
        return 0.0
    if value >= high:
        return 1.0
    return (value - low) / (high - low)
