"""CB storm cell turbulence model for PSX.

Computes turbulence intensity and type based on aircraft position relative
to the nearest CB storm cell returned by find_nearest_cb().

Physical model
--------------
Five distinct hazard zones are recognised, each driven by PSX meteorological
data (coverage in oktas and cloud base/top altitude):

1. Interior  — inside the lateral boundary, between cloud base and top.
               Severe to extreme chaotic turbulence; peaks at mid-cloud and
               cell centre. Mostly from cloud depth, not coverage: oktas are
               how much of the area has CBs, not how strong one cell is.
2. Inflow    — inside or within the gust-front zone, below cloud base.
               Strong updrafts feeding the cell; intensity decays with height
               below cloud base over an 8 000 ft scale.
3. Anvil     — inside or within the anvil CAT zone, above cloud top.
               Clear-air turbulence; decays exponentially above the visible
               cloud top with a 4 000 ft scale height, and reaches out
               mostly downwind (when the wind at the top is known).
4. Outflow   — outside the lateral boundary at cloud-layer altitudes.
               Gust-front / flank turbulence; width scales with cell severity.
5. Anvil CAT — outside the lateral boundary near the cloud-top altitude.
               Moderate CAT in the anvil outflow; lateral zone up to ~60 nm
               for an extreme cell.

Severity is estimated as the geometric mean of coverage (0–8 oktas,
normalised to 0–1) and cloud depth (0–30 000 ft, normalised to 0–1).

All turbulence generated here uses kind="cb" and NaN directional components
(turbulence inside and around CBs is chaotic and not deterministically
directional at the scale modelled here).
"""

import math
from typing import Optional

from .cb import CbInfo
from .turbulence import TurbulenceState

# Minimum coverage (oktas) below which no CB turbulence is generated.
_MIN_COVERAGE = 1

# Cloud depth (ft) at which storm severity saturates.
_DEPTH_SAT_FT = 30_000.0

# Scale height for anvil CAT decay above cloud top (ft). Was 12 000, which
# still gave ~9% 12 000 ft above a 2-okta CB; air more than ~5 000 ft above
# a top is normally smooth.
_ANVIL_DECAY_FT = 4_000.0

# Anvil CAT reach upwind of the cell, as a fraction of the downwind reach
# (the anvil blows downwind); crosswind halfway between.
_ANVIL_UPWIND_FRACTION = 1.0 / 3.0

# Maximum gust-front / outflow zone beyond cell edge at severity=1 (nm).
_OUTFLOW_MAX_NM = 20.0

# Maximum anvil CAT zone beyond cell edge at severity=1 (nm).
_ANVIL_MAX_NM = 60.0

# Scale height for inflow intensity decay below cloud base (ft).
_INFLOW_DECAY_FT = 8_000.0


def compute_cb_turbulence(alt_ft: float, cb: CbInfo,  # pylint: disable=too-many-return-statements
                          top_wind_dir_deg: Optional[float] = None) -> TurbulenceState:
    """Return turbulence intensity caused by proximity to a CB storm cell.

    Parameters
    ----------
    alt_ft :
        Aircraft pressure altitude (feet MSL).
    cb :
        Nearest active CB storm cell from find_nearest_cb().
    top_wind_dir_deg :
        Direction the wind blows from at the cloud top, or None: the anvil
        CAT zone then reaches equally far all round.

    Returns
    -------
    TurbulenceState with kind ``"cb"``, or zero-intensity calm when the
    aircraft is far enough from the cell to be unaffected.

    """
    if cb.coverage < _MIN_COVERAGE:
        return TurbulenceState()

    severity = _severity(cb)
    if severity < 0.01:
        return TurbulenceState()

    above_top = alt_ft > cb.cloud_top_ft_msl
    below_base = alt_ft < cb.cloud_base_ft_msl
    in_cloud = not above_top and not below_base
    inside = cb.range_edge_nm < 0

    depth_ft = max(0.0, cb.cloud_top_ft_msl - cb.cloud_base_ft_msl)
    outflow_nm = _OUTFLOW_MAX_NM * severity
    anvil_nm = _ANVIL_MAX_NM * severity * _anvil_direction_factor(cb, top_wind_dir_deg)

    if in_cloud and inside:
        return _interior(alt_ft, cb, depth_ft)

    if below_base and (inside or 0.0 <= cb.range_edge_nm <= outflow_nm):
        return _inflow_outflow(alt_ft, cb, severity, inside, outflow_nm)

    if above_top and (inside or 0.0 <= cb.range_edge_nm <= anvil_nm):
        return _anvil(alt_ft, cb, severity, inside, anvil_nm)

    if in_cloud and not inside and 0.0 <= cb.range_edge_nm <= outflow_nm:
        return _outflow(cb, severity, outflow_nm)

    return TurbulenceState()


# ---------------------------------------------------------------------------
# Zone helpers
# ---------------------------------------------------------------------------

def cb_flank_width_nm(coverage: int, top_ft: float, base_ft: float,
                      anvil: bool = False) -> float:
    """Return how far (nm) CB turbulence reaches beyond a CB's lateral radius.

    The flank (outflow) zone at and below cloud levels, or with anvil=True
    the anvil CAT zone above the cloud top, which reaches three times as far.
    """
    if coverage < _MIN_COVERAGE:
        return 0.0
    return ((_ANVIL_MAX_NM if anvil else _OUTFLOW_MAX_NM) *
            _severity_of(coverage, top_ft - base_ft))


def _severity(cb: CbInfo) -> float:
    """Geometric mean of coverage fraction and cloud-depth fraction."""
    return _severity_of(cb.coverage, cb.cloud_top_ft_msl - cb.cloud_base_ft_msl)


def _severity_of(coverage: int, depth_ft: float) -> float:
    """Geometric mean of coverage fraction and cloud-depth fraction."""
    coverage_f = coverage / 8.0
    depth_f = min(1.0, max(0.0, depth_ft) / _DEPTH_SAT_FT)
    return math.sqrt(coverage_f * depth_f)


def _anvil_direction_factor(cb: CbInfo, top_wind_dir_deg: Optional[float]) -> float:
    """Return how far the anvil CAT zone reaches towards the aircraft (1 downwind)."""
    if top_wind_dir_deg is None:
        return 1.0
    cell_to_aircraft = (cb.bearing_deg + 180.0) % 360.0
    downwind = (top_wind_dir_deg + 180.0) % 360.0
    cos_off = math.cos(math.radians(cell_to_aircraft - downwind))
    return _ANVIL_UPWIND_FRACTION + (1.0 - _ANVIL_UPWIND_FRACTION) * (1.0 + cos_off) / 2.0


def _interior(alt_ft: float, cb: CbInfo, depth_ft: float) -> TurbulenceState:
    """Severe to extreme chaotic turbulence inside the CB cloud column.

    Strength from cloud depth (0.55 for a shallow cell, 1 from
    _DEPTH_SAT_FT), a little less with fewer oktas (0.7 at 0, as one cell
    in the area is no weaker for being alone). It used to be the coverage
    x depth severity, so inside a 2-okta CB was only light to moderate.
    """
    depth_f = min(1.0, depth_ft / _DEPTH_SAT_FT)
    strength = (0.55 + 0.45 * depth_f) * (0.7 + 0.3 * cb.coverage / 8.0)
    depth_inside = min(1.0, -cb.range_edge_nm / cb.lateral_radius_nm)
    lateral_f = 0.6 + 0.4 * depth_inside

    if depth_ft > 0.0:
        alt_frac = (alt_ft - cb.cloud_base_ft_msl) / depth_ft
        alt_f = 1.0 - abs(alt_frac - 0.5) * 0.8
    else:
        alt_f = 0.8

    return TurbulenceState(
        intensity=min(1.0, strength * lateral_f * alt_f),
        kind="cb",
        reason=(
            f"CB interior: {cb.source} inside cloud column "
            f"base={cb.cloud_base_ft_msl:.0f}ft top={cb.cloud_top_ft_msl:.0f}ft"
        ),
    )


def _inflow_outflow(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    alt_ft: float,
    cb: CbInfo,
    severity: float,
    inside: bool,
    outflow_nm: float,
) -> TurbulenceState:
    """Updraft inflow below cloud base, or gust-front outflow ahead of the cell."""
    if inside:
        depth_inside = min(1.0, -cb.range_edge_nm / cb.lateral_radius_nm)
        prox_f = 0.5 + 0.5 * depth_inside
        zone = "inflow"
    else:
        prox_f = (1.0 - cb.range_edge_nm / outflow_nm) ** 2
        zone = "outflow"

    # Intensity decays with height below cloud base.
    height_below_base = cb.cloud_base_ft_msl - alt_ft
    height_f = math.exp(-height_below_base / _INFLOW_DECAY_FT)

    return TurbulenceState(
        intensity=min(1.0, severity * 0.65 * prox_f * height_f),
        kind="cb",
        reason=(
            f"CB {zone}: {cb.source} "
            f"base={cb.cloud_base_ft_msl:.0f}ft rng={cb.range_center_nm:.0f}nm"
        ),
    )


def _anvil(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    alt_ft: float,
    cb: CbInfo,
    severity: float,
    inside: bool,
    anvil_nm: float,
) -> TurbulenceState:
    """Anvil CAT and overshooting-top turbulence above cloud top."""
    excess_ft = alt_ft - cb.cloud_top_ft_msl
    alt_f = math.exp(-max(0.0, excess_ft) / _ANVIL_DECAY_FT)

    prox_f = 1.0 if inside else (1.0 - cb.range_edge_nm / anvil_nm) ** 1.5

    return TurbulenceState(
        intensity=min(1.0, severity * 0.55 * prox_f * alt_f),
        kind="cb",
        reason=(
            f"CB anvil CAT: {cb.source} top={cb.cloud_top_ft_msl:.0f}ft "
            f"+{max(0.0, excess_ft):.0f}ft rng={cb.range_center_nm:.0f}nm"
        ),
    )


def _outflow(cb: CbInfo, severity: float, outflow_nm: float) -> TurbulenceState:
    """Flank turbulence outside the lateral boundary at cloud-layer altitudes."""
    prox_f = (1.0 - cb.range_edge_nm / outflow_nm) ** 2
    return TurbulenceState(
        intensity=min(1.0, severity * 0.4 * prox_f),
        kind="cb",
        reason=(
            f"CB flank: {cb.source} rng={cb.range_center_nm:.0f}nm "
            f"edge=+{cb.range_edge_nm:.0f}nm"
        ),
    )
