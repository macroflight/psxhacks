"""Boundary-layer turbulence: the light bumps of an ordinary breezy or sunny day.

Testing profile only (frankenweather's "testing" turbulence profile). The
terrain model's mechanical turbulence only starts at a 20 kt wind 300 m
above the ground and stays near nothing over flat land, and CAPE
turbulence needs showers, so an ordinary breezy or sunny afternoon below
3,000 ft was glassy smooth (flight tests 2026-10-25: Poitiers 15 kt
sunny, Amazon 5 kt 32 C, Copenhagen 28G38 kt all 0-7 %), where real
aircraft get light chop.

From the hourly Open-Meteo data the CAPE fetcher already downloads
(CapeSample): the 10 m wind (mechanical) and the sunshine (thermals),
within the boundary layer (Open-Meteo's boundary layer height), fading
out above it. At most _BL_MAX. Kept small, the wind part most: PSX's
own gusts (a zone's METAR gusts) already shake the aircraft, and on top
of them 24-27 % here was more than light chop (EHAM 28027G39KT, p95
0.16 g, jolts to 0.85 g, 2026-10-25); PSX has no thermals of its own.
"""

import math

from .turbulence import TurbulenceState

# Mechanical part: the 10 m wind from _BL_WIND_MIN_KT, full at _BL_WIND_FULL_KT
_BL_WIND_MIN_KT = 5.0
_BL_WIND_FULL_KT = 30.0
_BL_MECH_MAX = 0.15
# Thermal part: sunshine (shortwave radiation) from _BL_SUN_MIN_WM2, full at _BL_SUN_FULL_WM2
_BL_SUN_MIN_WM2 = 50.0
_BL_SUN_FULL_WM2 = 600.0
_BL_THERMAL_MAX = 0.20
_BL_MAX = 0.20
# Height: full up to _BL_FULL_FRACTION of the boundary layer top, none above
# _BL_FADE_FRACTION of it; a top of at least _BL_MIN_TOP_FT (by day without
# data _BL_DAY_TOP_FT, by night _BL_MIN_TOP_FT)
_BL_FULL_FRACTION = 0.7
_BL_FADE_FRACTION = 1.2
_BL_MIN_TOP_FT = 1000.0
_BL_DAY_TOP_FT = 3000.0
_FT_PER_M = 1.0 / 0.3048


def _ramp(value: float, low: float, high: float) -> float:
    """Return 0 at or below low, 1 at or above high, linear in between (0 for NaN)."""
    if math.isnan(value) or value <= low:
        return 0.0
    return min(1.0, (value - low) / (high - low))


def compute_boundary_layer_turbulence(agl_ft: float, sample) -> TurbulenceState:
    """Return the boundary-layer turbulence at agl_ft above the ground from a CapeSample.

    Kind "mechanical" (random bursts, low-level gustiness), with its own
    reason. None (an empty state) without data or above the layer.
    """
    wind = sample.wind_10m_kt
    sun = sample.sun_wm2
    mech = _BL_MECH_MAX * _ramp(wind, _BL_WIND_MIN_KT, _BL_WIND_FULL_KT)
    thermal = _BL_THERMAL_MAX * _ramp(sun, _BL_SUN_MIN_WM2, _BL_SUN_FULL_WM2)
    intensity = min(_BL_MAX, math.hypot(mech, thermal))
    if math.isnan(sample.bl_height_m):
        top_ft = _BL_DAY_TOP_FT if thermal > 0.0 else _BL_MIN_TOP_FT
    else:
        top_ft = max(_BL_MIN_TOP_FT, sample.bl_height_m * _FT_PER_M)
    if agl_ft > _BL_FULL_FRACTION * top_ft:
        intensity *= max(0.0, 1.0 - (agl_ft - _BL_FULL_FRACTION * top_ft) /
                         ((_BL_FADE_FRACTION - _BL_FULL_FRACTION) * top_ft))
    if intensity < 0.01:
        return TurbulenceState()

    def _fmt(value, unit):
        return "?" if math.isnan(value) else f"{value:.0f}{unit}"

    return TurbulenceState(
        intensity=intensity,
        kind="mechanical",
        reason=(f"Boundary layer: wind {_fmt(wind, 'kt')}, sun {_fmt(sun, 'W/m2')}, "
                f"top {top_ft:.0f}ft (testing)"),
    )
