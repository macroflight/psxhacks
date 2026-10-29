"""WindProfile: multi-level atmospheric wind state at one position and time.

Stores wind speed, direction, and geometric altitude for each available
pressure level and provides interpolation and derived quantities used by
the turbulence model.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import numpy as np  # pylint: disable=import-error

KT_TO_MS = 0.514444
MS_TO_KT = 1.0 / KT_TO_MS

# Low-level jet (WindProfile.low_level_jet()): the speed must drop by at
# least this much within this height above the maximum.
LLJ_DROP_KT = 8.0
# WindProfile._levels(): pressure levels this far below the ground still count
BELOW_GROUND_TOLERANCE_M = 250.0
LLJ_DROP_WITHIN_M = 1500.0


# ---------------------------------------------------------------------------
# Helpers for circular wind-direction arithmetic
# ---------------------------------------------------------------------------

def _to_uv(speed_kt: np.ndarray, direction_deg: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Convert met-convention wind (FROM direction) to u/v components in m/s.

    u > 0  →  blowing eastward
    v > 0  →  blowing northward
    """
    rad = np.radians(direction_deg)
    speed_ms = speed_kt * KT_TO_MS
    u = -speed_ms * np.sin(rad)
    v = -speed_ms * np.cos(rad)
    return u, v


def _from_uv(u: float, v: float) -> tuple[float, float]:
    """Convert u/v (m/s) back to (speed_kt, direction_deg)."""
    speed_ms = math.hypot(u, v)
    if speed_ms < 1e-6:
        return 0.0, 0.0
    direction = (math.degrees(math.atan2(-u, -v)) + 360.0) % 360.0
    return speed_ms * MS_TO_KT, direction


def _is_tropopause_level(alts: np.ndarray, temps: np.ndarray, i: int) -> bool:
    """Return True if level i qualifies as the tropopause (WMO lapse-rate test).

    The lapse rate from level i to i+1 must be <=2 degC/km, AND the
    average lapse rate from level i to every level within the next 2 km
    above it must also stay <=2 degC/km. alts/temps must already be
    sorted ascending by altitude.
    """
    dz_km = (alts[i + 1] - alts[i]) / 1000.0
    if dz_km <= 0 or (temps[i] - temps[i + 1]) / dz_km > 2.0:
        return False
    for j in range(i + 1, len(alts)):
        dz_total_km = (alts[j] - alts[i]) / 1000.0
        if dz_total_km > 2.0:
            break
        avg_lapse = (temps[i] - temps[j]) / dz_total_km if dz_total_km > 0 else 0.0
        if avg_lapse > 2.0:
            return False
    return True


# ---------------------------------------------------------------------------
# WindProfile
# ---------------------------------------------------------------------------

@dataclass
class WindProfile:  # pylint: disable=too-many-instance-attributes
    """Atmospheric wind profile at a single horizontal position and UTC hour.

    All arrays are sorted by altitude ascending (surface first).
    NaN entries indicate missing data at that pressure level.

    Attributes
    ----------
    lat, lon        : query position (degrees)
    fetched_at      : wall-clock UTC time of the HTTP request
    valid_at        : UTC hour this profile represents
    pressures_hpa   : pressure level labels (hPa)
    altitudes_m     : geometric height of each pressure surface (m MSL)
    speeds_kt       : wind speed at each level (kt)
    directions_deg  : wind direction FROM at each level (degrees, met)

    """

    lat: float
    lon: float
    fetched_at: datetime
    valid_at: datetime

    pressures_hpa: np.ndarray   # shape (N,)
    altitudes_m: np.ndarray     # shape (N,)  — ascending
    speeds_kt: np.ndarray       # shape (N,)
    directions_deg: np.ndarray  # shape (N,)
    # Temperature at each level (degC), or None/all-NaN if not fetched —
    # optional because older callers (make_fixed_wind_profile(), disk-cache
    # entries written before this field existed) don't supply it.
    temperatures_c: Optional[np.ndarray] = None

    # u/v in m/s — computed once in __post_init__ for fast interpolation
    _u_ms: np.ndarray = field(init=False, repr=False)
    _v_ms: np.ndarray = field(init=False, repr=False)
    # Mask of levels where all three of alt, speed, direction are finite
    _valid: np.ndarray = field(init=False, repr=False)

    def __post_init__(self):
        """Initialize derived UV components and validity mask."""
        if self.temperatures_c is None:
            self.temperatures_c = np.full_like(self.altitudes_m, np.nan, dtype=np.float32)
        self._valid = (
            np.isfinite(self.altitudes_m) &
            np.isfinite(self.speeds_kt) &
            np.isfinite(self.directions_deg)
        )
        if not np.any(self._valid):
            self._u_ms = np.array([])
            self._v_ms = np.array([])
            return
        self._u_ms, self._v_ms = _to_uv(
            self.speeds_kt[self._valid],
            self.directions_deg[self._valid],
        )

    # ------------------------------------------------------------------
    # Core interpolation
    # ------------------------------------------------------------------

    def _levels(self, bottom_m: float) -> tuple:
        """Return (alts, u, v) of the valid levels not well below bottom_m (the ground).

        Levels more than BELOW_GROUND_TOLERANCE_M below the ground are model
        extrapolations, e.g. 925 hPa at Denver; one just below it (850 hPa
        there) is about the surface wind. All valid levels if fewer than 2
        are left.
        """
        alts = self.altitudes_m[self._valid]
        keep = alts >= bottom_m - BELOW_GROUND_TOLERANCE_M
        if np.sum(keep) < 2:
            return alts, self._u_ms, self._v_ms
        return alts[keep], self._u_ms[keep], self._v_ms[keep]

    def wind_at(self, alt_m: float, bottom_m: float = -math.inf) -> tuple[float, float]:
        """Interpolate wind to a geometric altitude, ignoring levels below bottom_m.

        Returns
        -------
        (speed_kt, direction_deg)
        Returns (0.0, 0.0) if no valid levels are available.

        """
        if not np.any(self._valid):
            return 0.0, 0.0

        alts, us, vs = self._levels(bottom_m)
        u = float(np.interp(alt_m, alts, us))
        v = float(np.interp(alt_m, alts, vs))
        return _from_uv(u, v)

    def wind_at_pressure(self, hpa: float) -> tuple[float, float]:
        """Interpolate wind to a pressure level (log-pressure interpolation).

        Returns (speed_kt, direction_deg).
        """
        if not np.any(self._valid):
            return 0.0, 0.0

        pressures = self.pressures_hpa[self._valid]
        log_p = np.log(pressures)
        log_q = math.log(hpa)
        u = float(np.interp(-log_q, -log_p, self._u_ms))
        v = float(np.interp(-log_q, -log_p, self._v_ms))
        return _from_uv(u, v)

    # ------------------------------------------------------------------
    # Derived quantities used by the turbulence model
    # ------------------------------------------------------------------

    def vertical_wind_shear(self, alt_m: float, dz_m: float = 500.0,
                            bottom_m: float = -math.inf) -> float:
        """Return vertical wind shear magnitude at a given altitude (kt / 1000 ft).

        Computed as |Δv⃗| / Δz over a layer of ±dz_m centred on alt_m.
        See frankenturb/turbulence.py (SHEAR_MODERATE, SHEAR_ALOFT_MODERATE)
        for how it's turned into turbulence.
        """
        if not np.any(self._valid):
            return 0.0
        alts, us, vs = self._levels(bottom_m)
        lo_hi = [alt_m - dz_m, alt_m + dz_m]
        u_lo, u_hi = np.interp(lo_hi, alts, us)
        v_lo, v_hi = np.interp(lo_hi, alts, vs)
        dv_ms = math.hypot(float(u_hi - u_lo), float(v_hi - v_lo))
        dz_ft = dz_m * 2.0 / 0.3048   # full layer in feet
        return (dv_ms * MS_TO_KT) / (dz_ft / 1000.0)

    def max_shear_in_layer(self, alt_bottom_m: float, alt_top_m: float) -> float:
        """Return maximum vertical wind shear within [alt_bottom_m, alt_top_m].

        Evaluates shear at each valid pressure level within the layer.
        Returns kt / 1000 ft.
        """
        if not np.any(self._valid):
            return 0.0
        alts = self.altitudes_m[self._valid]
        in_layer = alts[(alts >= alt_bottom_m) & (alts <= alt_top_m)]
        if len(in_layer) == 0:
            # Layer thinner than level spacing — check midpoint.
            in_layer = np.array([(alt_bottom_m + alt_top_m) / 2.0])
        return float(max(self.vertical_wind_shear(a) for a in in_layer))

    def wind_at_ridge_top(self, ridge_alt_m: float) -> tuple[float, float]:
        """Return the wind at the height of a terrain barrier.

        This is the key input for mountain wave detection: waves are driven
        by the wind that actually flows over the ridge, not the wind at the
        aircraft's (potentially much higher) altitude.
        """
        return self.wind_at(ridge_alt_m)

    def direction_change_across_layer(
        self, alt_bottom_m: float, alt_top_m: float
    ) -> float:
        """Return the absolute wind direction change (degrees) across an altitude layer.

        Backing (direction decreasing with altitude) or veering (increasing)
        both contribute to directional shear. Uses the shorter arc of the
        circular difference.
        """
        spd_lo, dir_lo = self.wind_at(alt_bottom_m)
        spd_hi, dir_hi = self.wind_at(alt_top_m)
        if spd_lo < 2.0 or spd_hi < 2.0:
            return 0.0  # calm wind — direction is meaningless
        diff = abs((dir_hi - dir_lo + 180.0) % 360.0 - 180.0)
        return diff

    def low_level_jet(
        self, search_top_m: float = 3000.0, bottom_m: float = -math.inf
    ) -> Optional[tuple[float, float, float]]:
        """Detect a low-level jet in the lower troposphere.

        A low-level jet is a wind-speed maximum with the speed dropping by
        at least LLJ_DROP_KT within LLJ_DROP_WITHIN_M above it. Levels below
        bottom_m (the ground: pressure levels below it are extrapolated)
        don't count. Returns (jet_alt_m, jet_speed_kt, jet_dir_deg) or None.
        A low-level jet amplifies both rotor and mountain-wave turbulence.
        It used to compare against the slowest level anywhere above,
        stratosphere included, so almost any profile had a "jet".
        """
        if not np.any(self._valid):
            return None
        alts = self.altitudes_m[self._valid]
        mask = (alts <= search_top_m) & (alts >= bottom_m)
        if np.sum(mask) < 2:
            return None

        speeds = np.hypot(self._u_ms, self._v_ms) * MS_TO_KT
        sub_alts = alts[mask]
        sub_speeds = speeds[mask]

        peak_idx = int(np.argmax(sub_speeds))
        if peak_idx == len(sub_speeds) - 1:
            return None  # monotonically increasing — not a jet

        jet_spd = float(sub_speeds[peak_idx])
        jet_alt = float(sub_alts[peak_idx])
        _, jet_dir = self.wind_at(jet_alt)

        above = np.interp(jet_alt + np.arange(250.0, LLJ_DROP_WITHIN_M + 1.0, 250.0),
                          alts, speeds)
        if jet_spd - float(np.min(above)) < LLJ_DROP_KT:
            return None

        return jet_alt, jet_spd, jet_dir

    def tropopause(self) -> Optional[tuple[float, float, float, float]]:
        """Return (alt_m, temp_c, wind_speed_kt, wind_dir_deg) at the tropopause.

        Uses the WMO definition: the lowest level at which the lapse rate
        decreases to 2 degC/km or less, provided the average lapse rate
        between that level and every level within the next 2 km does not
        exceed 2 degC/km either. The search starts above 5000 m (~500 hPa)
        to skip low-level inversions, which are common and not the
        tropopause. Wind at the found altitude is read via wind_at(), so
        it benefits from the same u/v interpolation as everything else.

        The altitude is then refined between pressure levels (as in
        Reichler et al. 2003): each layer's lapse rate is assigned to its
        midpoint, and the tropopause is placed where that lapse rate
        crosses 2 degC/km between the layer below the qualifying level and
        the one above it. Without this, the result could only ever be one
        of the pressure levels' own heights, and would jump a whole level
        whenever the forecast nudged the test across a level boundary.

        Returns None if there are too few valid temperature levels, or no
        qualifying level is found in the available profile. The top level
        can never qualify itself (nothing above it to test against), which
        is why the fetcher requests 70 hPa: the tropical tropopause is
        typically at ~100 hPa/16-17 km.
        """
        valid = self._valid & np.isfinite(self.temperatures_c)
        if np.sum(valid) < 3:
            return None
        order = np.argsort(self.altitudes_m[valid])
        alts = self.altitudes_m[valid][order]
        temps = self.temperatures_c[valid][order]

        for i in range(len(alts) - 1):
            if alts[i] < 5000.0 or not _is_tropopause_level(alts, temps, i):
                continue
            alt_m = float(alts[i])
            if i > 0:
                lapse_below = (temps[i - 1] - temps[i]) / ((alts[i] - alts[i - 1]) / 1000.0)
                lapse_above = (temps[i] - temps[i + 1]) / ((alts[i + 1] - alts[i]) / 1000.0)
                if lapse_below > 2.0 >= lapse_above:
                    mid_below = (alts[i - 1] + alts[i]) / 2.0
                    mid_above = (alts[i] + alts[i + 1]) / 2.0
                    frac = (lapse_below - 2.0) / (lapse_below - lapse_above)
                    alt_m = float(mid_below + frac * (mid_above - mid_below))
            temp_c = float(np.interp(alt_m, alts, temps))
            speed_kt, dir_deg = self.wind_at(alt_m)
            return alt_m, temp_c, speed_kt, dir_deg
        return None

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def surface_wind(self) -> tuple[float, float]:
        """Wind at the lowest available pressure level."""
        if not np.any(self._valid):
            return 0.0, 0.0
        alts = self.altitudes_m[self._valid]
        return self.wind_at(float(alts[0]))

    def n_levels(self) -> int:
        """Return the number of valid pressure levels in this profile."""
        return int(np.sum(self._valid))

    def altitude_range_m(self) -> tuple[float, float]:
        """Return (min_alt_m, max_alt_m) of valid levels."""
        alts = self.altitudes_m[self._valid]
        return float(alts[0]), float(alts[-1])

    def __repr__(self) -> str:
        """Return a compact string representation of this wind profile."""
        lo, hi = self.altitude_range_m()
        return (
            f"WindProfile(lat={self.lat:.2f}, lon={self.lon:.2f}, "
            f"valid_at={self.valid_at.strftime('%Y-%m-%dT%H:%M')}Z, "
            f"levels={self.n_levels()}, alt={lo:.0f}–{hi:.0f} m)"
        )


# ---------------------------------------------------------------------------
# Synthetic profile from a fixed surface wind
# ---------------------------------------------------------------------------

# Altitude levels (m MSL) used for extrapolation — 10 m to 15 km.
_FIXED_LEVELS_M: np.ndarray = np.array(
    [10, 100, 300, 500, 800, 1000, 1500, 2000, 3000, 4000,
     5000, 7000, 9000, 11000, 13000, 15000],
    dtype=np.float32,
)


def make_fixed_wind_profile(surface_dir_deg: float, surface_speed_kt: float) -> WindProfile:
    """Create a WindProfile from a fixed surface wind, extrapolated through the troposphere.

    Speed increases by approximately 5 % per km, representing a typical
    free-troposphere gradient.  Direction veers clockwise up to 20° between
    the surface and 1 000 m (Northern-hemisphere Ekman spiral approximation),
    then stays constant above.

    Parameters
    ----------
    surface_dir_deg :
        Wind direction at the surface, meteorological convention (degrees FROM).
    surface_speed_kt :
        Wind speed at the surface (knots).

    Returns
    -------
    WindProfile
        Synthetic multi-level profile covering 10 m to 15 000 m MSL.

    """
    n = len(_FIXED_LEVELS_M)
    speeds = np.empty(n, dtype=np.float32)
    directions = np.empty(n, dtype=np.float32)

    for i, z in enumerate(_FIXED_LEVELS_M):
        speeds[i] = surface_speed_kt * (1.0 + 0.05 * z / 1000.0)
        veer = min(20.0, z / 50.0)
        directions[i] = (surface_dir_deg + veer) % 360.0

    now = datetime.now(timezone.utc)
    return WindProfile(
        lat=0.0,
        lon=0.0,
        fetched_at=now,
        valid_at=now,
        pressures_hpa=np.zeros(n, dtype=np.float32),
        altitudes_m=_FIXED_LEVELS_M.copy(),
        speeds_kt=speeds,
        directions_deg=directions,
    )
