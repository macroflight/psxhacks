"""frankenweather's Advanced Icing (--advanced-icing): how much ice, compared with PSX's own.

PSX ices at one rate, about 11 kg per wing per minute (and about a quarter
of that per nacelle), wherever its weather has cloud (any layer, any
coverage), fog or precipitation at the aircraft and TAT is below about
+9 C, down to about -35 C (fading out by -43 C). Measured on the test sim
2026-11-13/14 (~/fw-research/advanced-icing-research.md section 7). From
the 747's size and speed that rate is roughly moderate icing in cloud with
typical liquid water (estimate). Real icing is mostly trace or light,
moderate in a band of about -3 to -12 C in thick cloud, severe in freezing
rain/drizzle and convective cloud near the freezing level, and nothing
once the leading edge is above freezing.

This module only computes. icing_factor() turns the conditions at the
aircraft into a factor on PSX's rate (0 = no ice, 1 = PSX's own rate, 2.5 =
severe); frankenweather then keeps that share of PSX's growth (writes back
a lower Icing value) or adds to it. Reports beat the model: an icing PIREP
near the aircraft, a SEV ICE SIGMET (frankenweather takes ICE SIGMETs from
PSX while Advanced Icing is on) or a US G-AIRMET ICE (moderate icing
forecast) set the factor where they apply.

Pure functions plus IcingReports, which fetches icing PIREPs and G-AIRMETs
from aviationweather.gov (call its methods in an executor).
"""
import logging
import math
import time
from dataclasses import dataclass
from typing import Optional

import requests  # pylint: disable=import-error

from frankenturb.pirep import bbox_around

log = logging.getLogger(__name__)

# PSX's own accretion (test sim, 10.195): kg per minute per wing, and the
# nacelle share of it (~8 of ~31 kg in 3 minutes).
PSX_WING_KG_MIN = 11.0
PSX_NACELLE_SHARE = 0.27

# Factors on PSX's rate per intensity (PSX's rate is about moderate).
TRACE, LIGHT, LIGHT_MOD, MODERATE, MOD_SEV, SEVERE = 0.1, 0.3, 0.6, 1.0, 1.7, 2.5

# Kinetic heating: the wing's leading edge is warmed by this share of the
# rise from SAT to TAT (estimate: NACA found heavy glaze still at SAT -7 C
# for M0.5 and -21 C for M0.9, where the full rise is about 13 and 41 C,
# i.e. about half; a bit less here, as lighter icing goes on a little
# warmer). Ice builds fully below LE_COLD_C, not at all above LE_WARM_C. PSX
# itself ices up to TAT +9 C (at 250 kt, SAT about -2 C).
KINETIC_SHARE = 0.4
LE_COLD_C = -1.0
LE_WARM_C = 1.0
# Out of cloud PSX melts ice within seconds above some TAT between +1 C
# (FL190: ice kept) and +6 C (FL140: gone after every write; test sim
# 2026-11-14); +3 C assumed, PSX's limit for icing on the ground.
CLEAR_AIR_MELT_TAT_C = 3.0
# Below this SAT (C) there is no supercooled water (PSX stops about here too).
SAT_MIN_C = -40.0

# Liquid water by cloud type, as a factor on PSX's rate before the
# temperature and cloud-top factors: layer cloud is light to moderate,
# convective cloud moderate to severe, fog light.
LAYER_BASE = 0.6
CONVECTIVE_BASE = 2.0
FOG_BASE = 0.3

PIREP_URL = "https://aviationweather.gov/api/data/pirep"
GAIRMET_URL = "https://aviationweather.gov/api/data/gairmet"
PIREP_RADIUS_NM = 150        # fetched around the aircraft
PIREP_FULL_NM = 20.0         # a PIREP sets the factor this close ...
PIREP_REACH_NM = 40.0        # ... fading back to the model out to here
PIREP_ALT_WINDOW_FT = 2000.0  # beyond the reported layer
PIREP_MAX_AGE_MIN = 90.0
CACHE_S = 1800.0
BUCKET_DEG = 1.0

_PIREP_INTENSITY = {
    "NEG": 0.0, "NEGCLR": 0.0, "TRC": TRACE, "TRACE": TRACE, "TRC-LGT": 0.2, "LGT": LIGHT,
    "LGT-MOD": LIGHT_MOD, "MOD": MODERATE, "MOD-SEV": MOD_SEV, "SEV": SEVERE, "HVY": SEVERE,
}


def intensity_label(factor: float) -> str:
    """Return the pilot's word for a factor on PSX's rate."""
    if factor < 0.05:
        return "none"
    if factor < 0.2:
        return "trace"
    if factor < 0.6:
        return "light"
    if factor < 1.6:
        return "moderate"
    return "severe"


def tat_c(sat_c: float, tas_kt: float) -> float:
    """Return TAT (C) from SAT and true airspeed (full recovery)."""
    tas_ms = tas_kt * 0.5144
    return sat_c + tas_ms * tas_ms / 2010.0


def leading_edge_c(sat_c: float, tat_c_: float) -> float:
    """Return the leading edge's temperature (C): SAT plus KINETIC_SHARE of the rise to TAT."""
    return sat_c + KINETIC_SHARE * (tat_c_ - sat_c)


def warm_factor(sat_c: float, tat_c_: float) -> float:
    """Return 0-1: 0 once kinetic heating keeps the leading edge above freezing."""
    le = leading_edge_c(sat_c, tat_c_)
    return max(0.0, min(1.0, (LE_WARM_C - le) / (LE_WARM_C - LE_COLD_C)))


def temperature_factor(sat_c: float) -> float:
    """Return 0-1: supercooled water by temperature (after NCAR FIP's temperature map).

    Most liquid at -4 to -8 C; less towards 0 C (warm drops freeze slower
    and run back); little below -20 C where cloud is mostly ice; a trace
    floor down to SAT_MIN_C.
    """
    if sat_c >= 0.0 or sat_c <= SAT_MIN_C:
        return 0.0
    if sat_c > -3.0:
        return 0.5 + 0.5 * (-sat_c / 3.0)
    if sat_c >= -8.0:
        return 1.0
    if sat_c >= -25.0:
        return max(0.15, 1.0 - (-8.0 - sat_c) * 0.85 / 17.0)
    return 0.15


def cloud_top_factor(top_temp_c: Optional[float]) -> float:
    """Return 0.6-1: tops colder than -12 C turn more of the cloud to ice crystals (FIP)."""
    if top_temp_c is None or top_temp_c >= -12.0:
        return 1.0
    return max(0.6, 1.0 - (-12.0 - top_temp_c) * 0.4 / 18.0)


def coverage_factor(oktas: int) -> float:
    """Return 0.4-1: in broken/scattered cloud the aircraft is in cloud only part of the time."""
    if oktas <= 2:
        return 0.4
    if oktas <= 4:
        return 0.7
    return 1.0


def parse_wx_layers(wx: str, elev_ft: float) -> dict:
    """Return the cloud layers and weather of a PSX Wx/WxBasic string.

    {"high": (oktas, base_ft_msl, top_ft_msl), "low": ..., "cb": ...,
    "vis_m": int, "precip": int}; PSX's heights are above the zone
    elevation. Layers with 0 oktas are left out.
    """
    out = {}
    try:
        f = [int(float(x)) for x in wx.split(";")]
    except (ValueError, AttributeError):
        return out
    if len(f) < 24:
        return out
    for name, i in (("high", 0), ("low", 3), ("cb", 9)):
        oktas, top, base = f[i], f[i + 1], f[i + 2]
        if oktas > 0 and top > base:
            out[name] = (oktas, base + elev_ft, top + elev_ft)
    out["vis_m"], out["precip"] = f[-4], f[-3]
    return out


def layer_at(layers: dict, alt_ft: float, agl_ft: Optional[float],
             in_cb_cell: bool = False) -> Optional[tuple]:
    """Return (kind, oktas, top_ft_msl) of the cloud the aircraft is in, or None.

    kind: "convective" (in the CB cell: in_cb_cell, within the CB layer's
    heights), "layer" (low/high layer) or "fog" (visibility under 1,000 m,
    below 1,000 ft above the ground). PSX's CB layer is a cell 7 nm from
    the zone centre, not cloud all over the zone: away from the cell PSX
    doesn't ice in it (test sim 2026-11-14), so it only counts in the cell.
    """
    for name, kind in (("cb", "convective"), ("low", "layer"), ("high", "layer")):
        lay = layers.get(name)
        if name == "cb" and not in_cb_cell:
            continue
        if lay and lay[1] <= alt_ft <= lay[2]:
            return kind, lay[0], lay[2]
    if layers.get("vis_m", 9999) < 1000 and agl_ft is not None and agl_ft < 1000:
        return "fog", 8, alt_ft + 500
    return None


@dataclass
class Conditions:  # pylint: disable=too-many-instance-attributes
    """What icing_factor() needs to know about the aircraft's surroundings."""

    sat_c: float
    tat_c: float
    alt_ft: float
    cloud: Optional[tuple] = None          # layer_at() result
    precip: int = 0                        # PSX precipitation level 0-3 at the aircraft
    freezing_precip: str = ""              # "FZRA"/"FZDZ" from a METAR nearby, low down
    pirep: Optional[tuple] = None          # (factor, weight 0-1, text)
    sigmet: Optional[tuple] = None         # (factor, text): an ICE SIGMET here
    gairmet: Optional[tuple] = None        # (factor, text): a G-AIRMET ICE here


def model_factor(c: Conditions) -> tuple:
    """Return (factor, why) from the weather alone (cloud, temperature, precipitation)."""
    warm = warm_factor(c.sat_c, c.tat_c)
    if warm <= 0.0:
        return 0.0, (f"SAT {c.sat_c:.0f} C, TAT {c.tat_c:+.0f} C: kinetic heating keeps the "
                     "leading edge above freezing")
    if c.sat_c <= SAT_MIN_C:
        return 0.0, f"SAT {c.sat_c:.0f} C: no supercooled water"
    if c.freezing_precip and c.sat_c <= 0.0 and c.alt_ft < 12000:
        return SEVERE * warm, f"{c.freezing_precip} (supercooled large drops)"
    if c.cloud is None:
        return 0.0, "not in cloud"
    kind, oktas, top_ft = c.cloud
    temp_f = temperature_factor(c.sat_c)
    top_temp = c.sat_c - 2.0 * (top_ft - c.alt_ft) / 1000.0
    if kind == "convective":
        factor = CONVECTIVE_BASE * temp_f
        why = f"convective cloud, SAT {c.sat_c:.0f} C"
    elif kind == "fog":
        factor = FOG_BASE * temp_f
        why = f"freezing fog, SAT {c.sat_c:.0f} C"
    else:
        factor = LAYER_BASE * temp_f * cloud_top_factor(top_temp) * coverage_factor(oktas)
        why = f"layer cloud {oktas}/8, SAT {c.sat_c:.0f} C, top {top_temp:.0f} C"
    if c.precip > 0 and c.sat_c > -10.0 and kind != "fog":
        factor *= 1.3
        why += ", precipitation"
    if warm < 1.0:
        factor *= warm
        why += f", leading edge {leading_edge_c(c.sat_c, c.tat_c):+.1f} C"
    return factor, why


def icing_factor(c: Conditions) -> tuple:
    """Return (factor on PSX's rate, why): the model, overruled by reports where they apply.

    A PIREP sets the factor (weighted by distance), a SIGMET or G-AIRMET
    raises it to at least theirs -- but only while the air can ice at all
    (TAT, SAT) and, for the forecasts, in cloud: a forecast area isn't
    iced everywhere.
    """
    factor, why = model_factor(c)
    warm = warm_factor(c.sat_c, c.tat_c)
    if warm <= 0.0 or c.sat_c <= SAT_MIN_C:
        return factor, why
    if c.pirep is not None:
        p_factor, weight, text = c.pirep
        factor = factor + (p_factor * warm - factor) * weight
        why += f"; PIREP {text}"
    in_cloud = c.cloud is not None or bool(c.freezing_precip)
    for forecast in (c.sigmet, c.gairmet):
        if forecast is not None and in_cloud and forecast[0] * warm > factor:
            factor = forecast[0] * warm
            why += f"; {forecast[1]}"
    return factor, why


def clear_air_factor(c: Conditions) -> tuple:
    """Return (factor, why) for ice PSX doesn't make (no cloud in PSX's weather here).

    Only from reports: a PIREP of moderate or worse close by, or a SEV ICE
    SIGMET -- at half strength (patchy cloud that PSX's zone doesn't have).
    Not with TAT above CLEAR_AIR_MELT_TAT_C: out of cloud PSX melts any
    ice within seconds then, though in cloud it keeps icing up to TAT +9 C.
    """
    warm = warm_factor(c.sat_c, c.tat_c)
    if warm <= 0.0 or c.sat_c <= SAT_MIN_C or c.tat_c > CLEAR_AIR_MELT_TAT_C:
        return 0.0, ""
    best, why = 0.0, ""
    if c.pirep is not None and c.pirep[0] >= MODERATE:
        best, why = 0.5 * c.pirep[0] * c.pirep[1], f"PIREP {c.pirep[2]}"
    if c.sigmet is not None and c.sigmet[0] >= SEVERE and 0.5 * c.sigmet[0] > best:
        best, why = 0.5 * c.sigmet[0], c.sigmet[1]
    return best * warm * temperature_factor(c.sat_c) ** 0.5, why


def scaled_icing(current: list, previous: list, factor: float) -> Optional[list]:
    """Return new Icing kg (6 fields) keeping factor x PSX's growth since previous, or None.

    Per surface: only where PSX added ice (anti-ice melting or de-icing
    are left alone); factor < 1 takes some of PSX's growth back, > 1 adds.
    """
    if len(current) < 6 or len(previous) < 6:
        return None
    new = list(current[:6])
    changed = False
    for i in range(6):
        growth = current[i] - previous[i]
        if growth > 0.0:
            new[i] = max(0.0, current[i] + (factor - 1.0) * growth)
            changed = changed or abs(new[i] - current[i]) >= 0.5
    return new if changed else None


def added_icing(current: list, factor: float, seconds: float) -> list:
    """Return Icing kg (6 fields) with clear-air ice added (clear_air_factor()) for seconds.

    Added whatever the anti-ice does: PSX's own anti-ice then melts it, as
    it melts its own.
    """
    wing = factor * PSX_WING_KG_MIN * seconds / 60.0
    return [kg + (wing if i < 2 else wing * PSX_NACELLE_SHARE) for i, kg in enumerate(current[:6])]


# ---------------------------------------------------------------------------
# Reports: icing PIREPs and US G-AIRMET ICE, and ICE SIGMETs
# ---------------------------------------------------------------------------

def _nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians((lon2 - lon1 + 180.0) % 360.0 - 180.0) * math.cos(
        math.radians((lat1 + lat2) / 2.0))
    return math.degrees(math.hypot(dlat, dlon)) * 60.0


def point_in_polygon(lat: float, lon: float, polygon: list) -> bool:
    """Return True if (lat, lon) is inside polygon ((lat, lon) pairs; even-odd rule)."""
    inside, j = False, len(polygon) - 1
    for i, (lat_i, lon_i) in enumerate(polygon):
        lat_j, lon_j = polygon[j]
        if (lon_i > lon) != (lon_j > lon) and \
                lat < (lat_j - lat_i) * (lon - lon_i) / (lon_j - lon_i) + lat_i:
            inside = not inside
        j = i
    return inside


@dataclass
class IcingPirep:
    """One icing report (aviationweather.gov PIREP JSON)."""

    lat: float
    lon: float
    base_ft: float
    top_ft: float
    factor: float
    obs_time: float
    text: str       # e.g. "LGT RIME FL155-180"


def _hundreds(value) -> Optional[float]:
    try:
        return float(value) * 100.0
    except (TypeError, ValueError):
        return None


def parse_icing_pirep(item: dict) -> Optional[IcingPirep]:
    """Return the icing part of one PIREP (icgInt1/icgBas1/icgTop1 or fltLvl), or None."""
    raw = (item.get("icgInt1") or "").strip().upper()
    if not raw:
        return None
    factor = _PIREP_INTENSITY.get(raw.replace(" ", ""))
    if factor is None:
        return None
    try:
        lat, lon = float(item["lat"]), float(item["lon"])
        obs = float(item.get("obsTime"))
    except (KeyError, TypeError, ValueError):
        return None
    level = _hundreds(item.get("fltLvl"))
    base = _hundreds(item.get("icgBas1"))
    top = _hundreds(item.get("icgTop1"))
    if base is None and top is None:
        if level is None or level <= 0:
            return None
        base = top = level
    base = base if base is not None else top
    top = top if top is not None else base
    base, top = min(base, top), max(base, top)
    kind = (item.get("icgType1") or "").strip().upper()
    text = f"{raw}{' ' + kind if kind else ''} FL{base / 100:03.0f}" + (
        f"-{top / 100:03.0f}" if top != base else "")
    return IcingPirep(lat, lon, base, top, factor, obs, text)


@dataclass
class IcingArea:
    """One US G-AIRMET ICE area (moderate icing forecast)."""

    polygon: list
    base_ft: float
    top_ft: float
    expires: float
    text: str


def _gairmet_level(value, fzl) -> Optional[float]:
    """Return a G-AIRMET level (ft) from hundreds of ft; SFC = 0, FZL = lowest freezing level."""
    value = (str(value) if value is not None else "").strip().upper()
    if value == "FZL":
        value = (str(fzl) if fzl is not None else "SFC").strip().upper()
    if value in ("SFC", ""):
        return 0.0
    return _hundreds(value)


def parse_gairmet_ice(item: dict, now: float) -> Optional[IcingArea]:
    """Return one G-AIRMET ICE area, or None (not ICE, expired or unreadable)."""
    if (item.get("hazard") or "").upper() != "ICE":
        return None
    expires = item.get("expireTime")
    if isinstance(expires, (int, float)) and expires < now:
        return None
    try:
        polygon = [(float(p["lat"]), float(p["lon"])) for p in item.get("coords") or []]
    except (KeyError, TypeError, ValueError):
        return None
    base = _gairmet_level(item.get("base"), item.get("fzlbase"))
    top = _gairmet_level(item.get("top"), None)
    if len(polygon) < 3 or base is None or top is None:
        return None
    return IcingArea(polygon, base, top, float(expires or now + 3600),
                     f"G-AIRMET ICE {item.get('severity') or 'MOD'} "
                     f"{'SFC' if base <= 0 else f'FL{base / 100:03.0f}'}-FL{top / 100:03.0f}")


def sigmet_ice(entries: list, lat: float, lon: float, alt_ft: float) -> Optional[tuple]:
    """Return (factor, text) for the worst usable ICE SIGMET at the aircraft, or None.

    entries are fw_sigmet's normalized SIGMETs. ICAO SIGMETs only exist for
    severe icing (SEV ICE, SEV ICE (FZRA)); a US SIGMET for icing is severe
    too. A "MOD" qualifier counts as moderate.
    """
    best: Optional[tuple] = None
    best_factor = 0.0
    for e in entries:
        if not e.get("ok") or e.get("hazard") != "ICE":
            continue
        base = e.get("base_ft") or 0
        top = e.get("top_ft") if e.get("top_ft") is not None else 30000
        if not base - 1000 <= alt_ft <= top + 1000:
            continue
        if not any(point_in_polygon(lat, lon, p) for p in e.get("polygons") or []):
            continue
        raw = (e.get("raw") or "").upper()
        factor = MODERATE if (e.get("qualifier") or "").upper().startswith("MOD") else SEVERE
        text = (f"{'MOD' if factor < SEVERE else 'SEV'} ICE{' (FZRA)' if 'FZRA' in raw else ''} "
                f"SIGMET {e.get('fir', '?')} {e.get('series', '')}".rstrip())
        if best is None or factor > best_factor:
            best, best_factor = (factor, text), factor
    return best


class IcingReports:
    """Icing PIREPs near the aircraft and G-AIRMET ICE areas, cached (call in an executor)."""

    def __init__(self) -> None:
        """Start with nothing fetched."""
        self._pireps: list = []
        self._pirep_at: Optional[tuple] = None   # (monotonic, lat, lon)
        self._areas: list = []
        self._areas_time = 0.0

    def _get(self, url: str, params: dict):
        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json() if resp.text.strip() else []
            return data if isinstance(data, list) else []
        except Exception as exc:  # pylint: disable=broad-except
            log.warning("Advanced Icing: %s fetch failed: %s", url.rsplit("/", 1)[-1], exc)
            return None

    def pirep(self, lat: float, lon: float, alt_ft: float) -> Optional[tuple]:
        """Return (factor, weight 0-1, text) of the most relevant icing PIREP, or None."""
        now_m = time.monotonic()
        at = self._pirep_at
        if (at is None or now_m - at[0] > CACHE_S or abs(lat - at[1]) > BUCKET_DEG or
                abs(lon - at[2]) > BUCKET_DEG):
            data = self._get(PIREP_URL, {"bbox": bbox_around(lat, lon, PIREP_RADIUS_NM),
                                         "age": "2", "format": "json"})
            if data is not None:
                self._pireps = [p for p in (parse_icing_pirep(i) for i in data) if p]
                log.info("Advanced Icing: %d icing PIREPs within %d nm", len(self._pireps),
                         PIREP_RADIUS_NM)
            self._pirep_at = (now_m, lat, lon)
        best, best_weight = None, 0.0
        now = time.time()
        for p in self._pireps:
            age_min = (now - p.obs_time) / 60.0
            if age_min > PIREP_MAX_AGE_MIN:
                continue
            if not p.base_ft - PIREP_ALT_WINDOW_FT <= alt_ft <= p.top_ft + PIREP_ALT_WINDOW_FT:
                continue
            dist = _nm(lat, lon, p.lat, p.lon)
            if dist >= PIREP_REACH_NM:
                continue
            weight = 1.0 if dist <= PIREP_FULL_NM else (PIREP_REACH_NM - dist) / (
                PIREP_REACH_NM - PIREP_FULL_NM)
            weight *= 1.0 - 0.5 * age_min / PIREP_MAX_AGE_MIN
            if weight > best_weight:
                best_weight = weight
                best = (p.factor, weight, f"{p.text} {dist:.0f} nm, {age_min:.0f} min ago")
        return best

    def gairmet(self, lat: float, lon: float, alt_ft: float) -> Optional[tuple]:
        """Return (factor, text) if the aircraft is in a G-AIRMET ICE area, or None."""
        if time.monotonic() - self._areas_time > CACHE_S:
            data = self._get(GAIRMET_URL, {"type": "ice", "format": "json"})
            if data is not None:
                now = time.time()
                self._areas = [a for a in (parse_gairmet_ice(i, now) for i in data) if a]
                log.info("Advanced Icing: %d G-AIRMET ICE areas", len(self._areas))
            self._areas_time = time.monotonic()
        now = time.time()
        for a in self._areas:
            if a.expires >= now and a.base_ft - 1000 <= alt_ft <= a.top_ft + 1000 and \
                    point_in_polygon(lat, lon, a.polygon):
                return LIGHT_MOD, a.text
        return None
