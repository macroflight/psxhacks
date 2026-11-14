"""Realistic anti-icing fluid holdover times for frankenweather (--realistic-holdover).

PSX's AntiIceFluid (Qi178) is a minute timer, 0-45: while it is above 0 no ice
builds up. De-icing tools set it to a fixed time per fluid type (PSX.NET.EFB:
Type I 14 minutes). This module turns the departure airport's METAR into the
holdover time the FAA guidelines give for that fluid and weather, so the
timer frankenweather writes runs out as early as a real fluid would fail.

Source: FAA Holdover Time Guidelines, Winter 2026-2027 (original issue
2026-08-11): Table 2 (Type I, aluminium surfaces), Table 4 (generic Type
II, 100/0), Table 20 (generic Type IV, 100/0), Table 57 (snowfall intensity
from visibility). Times are in minutes (min, max). The lower end of each
range is for the higher precipitation rate.

Pure functions, no PSX connection; frankenweather.py does the detecting
and writing.
"""
import math
import re
from datetime import datetime
from typing import Optional

PSX_MAX_MIN = 45        # AntiIceFluid's range in PSX (Variables.txt: Max=45)
NO_HOT_MIN = 1          # "no holdover time guidelines exist": the fluid fails almost at once

# Conditions (the FAA tables' columns)
FZFG, SN_FZFG = "freezing fog", "snow with freezing fog"
SN_VL, SN_L, SN_M = "very light snow", "light snow", "moderate snow"
FZDZ, FZRA_L = "freezing drizzle", "light freezing rain"
SNRA_M, RA_CSW = "moderate snow mixed with rain", "rain on a cold-soaked wing"
OTHER = "heavy precipitation"
SNOW = (SN_VL, SN_L, SN_M)

# Temperature bands: (lowest OAT in the band, inclusive, C) -> {condition: (min, max)}.
# Bands are checked in order; the first whose lower bound is <= OAT applies.
_TYPE1 = [
    (-3.0, {FZFG: (11, 17), SN_FZFG: (6, 11), SN_VL: (18, 22), SN_L: (11, 18), SN_M: (6, 11),
            FZDZ: (9, 13),
            FZRA_L: (2, 5), SNRA_M: (2, 2), RA_CSW: (2, 5)}),
    (-6.0, {FZFG: (8, 13), SN_FZFG: (5, 8), SN_VL: (14, 17), SN_L: (8, 14), SN_M: (5, 8),
            FZDZ: (5, 9),
            FZRA_L: (2, 5)}),
    (-10.0, {FZFG: (6, 10), SN_FZFG: (4, 6), SN_VL: (11, 13), SN_L: (6, 11), SN_M: (4, 6),
             FZDZ: (4, 7),
             FZRA_L: (2, 5)}),
    (-273.0, {FZFG: (5, 9), SN_FZFG: (2, 4), SN_VL: (7, 8), SN_L: (4, 7), SN_M: (2, 4)}),
]
# Type II's generic table has one snow column (very light to moderate): see _pick()
_TYPE2 = [
    (-3.0, {FZFG: (55, 110), SN_FZFG: (20, 40), "snow": (30, 55), FZDZ: (35, 65), FZRA_L: (25, 35),
            SNRA_M: (5, 5), RA_CSW: (7, 45)}),
    (-8.0, {FZFG: (30, 45), SN_FZFG: (15, 30), "snow": (20, 40), FZDZ: (20, 45), FZRA_L: (15, 20)}),
    (-10.0, {FZFG: (30, 45), SN_FZFG: (10, 25), "snow": (15, 30), FZDZ: (20, 45),
             FZRA_L: (15, 20)}),
    (-14.0, {FZFG: (30, 45), SN_FZFG: (10, 25), "snow": (15, 30)}),    # no FZDZ/FZRA below -10
    (-18.0, {FZFG: (15, 20), SN_FZFG: (1, 5), "snow": (2, 7)}),
    (-25.0, {FZFG: (15, 20), SN_FZFG: (0, 2), "snow": (1, 3)}),
    (-273.0, {FZFG: (15, 20), SN_FZFG: (0, 0), "snow": (0, 1)}),
]
_TYPE4 = [
    (-3.0, {FZFG: (75, 135), SN_FZFG: (20, 40), SN_VL: (110, 135), SN_L: (55, 110), SN_M: (25, 55),
            FZDZ: (35, 70), FZRA_L: (15, 30), SNRA_M: (6, 6), RA_CSW: (8, 65)}),
    (-8.0, {FZFG: (15, 35), SN_FZFG: (20, 35), SN_VL: (95, 120), SN_L: (45, 95), SN_M: (25, 45),
            FZDZ: (25, 60), FZRA_L: (20, 25)}),
    (-10.0, {FZFG: (15, 35), SN_FZFG: (15, 30), SN_VL: (85, 110), SN_L: (45, 85), SN_M: (20, 45),
             FZDZ: (25, 60), FZRA_L: (15, 25)}),
    (-14.0, {FZFG: (15, 35), SN_FZFG: (15, 30), SN_VL: (85, 110), SN_L: (45, 85), SN_M: (20, 45)}),
    (-18.0, {FZFG: (15, 25), SN_FZFG: (1, 6), SN_VL: (30, 45), SN_L: (9, 30), SN_M: (2, 9)}),
    (-25.0, {FZFG: (15, 25), SN_FZFG: (0, 2), SN_VL: (10, 20), SN_L: (3, 10), SN_M: (1, 3)}),
    (-273.0, {FZFG: (15, 25), SN_FZFG: (0, 1), SN_VL: (7, 10), SN_L: (2, 7), SN_M: (0, 2)}),
]
TABLES = {"I": _TYPE1, "II": _TYPE2, "IV": _TYPE4}

# Table 57: visibility upper bound (m) -> (day <= -1 C, day > -1 C, night <= -1 C, night > -1 C)
_H, _M, _L, _VL = "heavy", "moderate", "light", "very light"
_SNOW_BY_VIS = [
    (600, (_H, _H, _H, _H)), (1000, (_M, _H, _H, _H)), (1400, (_M, _M, _M, _H)),
    (2600, (_L, _L, _M, _M)), (3000, (_VL, _L, _L, _L)), (4400, (_VL, _VL, _L, _L)),
    (5200, (_VL, _VL, _VL, _L)), (1e9, (_VL, _VL, _VL, _VL)),
]

_WX_RE = re.compile(r"^(?P<int>[-+]|VC)?(?P<desc>MI|PR|BC|DR|BL|SH|TS|FZ)*"
                    r"(?P<ph>(?:DZ|RA|SN|SG|IC|PL|GR|GS|UP|"
                    r"BR|FG|FU|VA|DU|SA|HZ|PY|PO|SQ|FC|SS|DS)+)$")
_TEMP_RE = re.compile(r"^(M?\d{2})/(M?\d{2})?$")


def metar_visibility_m(tokens: list) -> Optional[float]:
    """Return the prevailing visibility in metres from METAR tokens (None if not found)."""
    for i, tok in enumerate(tokens):
        if tok == "CAVOK":
            return 10000.0
        if re.fullmatch(r"\d{4}", tok) and i > 1:
            return float(tok)
        m = re.fullmatch(r"M?(\d+)?(?:(\d)/(\d+))?SM", tok)
        if m and (m.group(1) or m.group(2)):
            miles = float(m.group(1) or 0)
            if m.group(2):
                miles += float(m.group(2)) / float(m.group(3))
            if not m.group(1) and i > 0 and re.fullmatch(r"\d", tokens[i - 1]):
                miles += float(tokens[i - 1])     # "1 1/2SM"
            return miles * 1609.344
    return None


def metar_temperature_c(tokens: list) -> Optional[float]:
    """Return the METAR temperature (C) or None."""
    for tok in tokens:
        m = _TEMP_RE.match(tok)
        if m:
            t = m.group(1)
            return -float(t[1:]) if t.startswith("M") else float(t)
    return None


def solar_elevation_deg(lat: float, lon: float, when: datetime) -> float:
    """Return the sun's elevation (deg) at lat/lon at the UTC datetime (simple formula, +-1 deg)."""
    day = when.timetuple().tm_yday
    hours = when.hour + when.minute / 60.0 + when.second / 3600.0
    decl = math.radians(-23.44) * math.cos(math.radians(360.0 / 365.0 * (day + 10)))
    hour_angle = math.radians(15.0 * (hours - 12.0) + lon)
    lat_r = math.radians(lat)
    sin_el = (math.sin(lat_r) * math.sin(decl) +
              math.cos(lat_r) * math.cos(decl) * math.cos(hour_angle))
    return math.degrees(math.asin(max(-1.0, min(1.0, sin_el))))


def snow_intensity(vis_m: Optional[float], oat_c: float, day: bool) -> str:
    """Return heavy/moderate/light/very light snowfall from visibility (FAA Table 57)."""
    if vis_m is None:
        return _M
    col = (0 if day else 2) + (0 if oat_c <= -1.0 else 1)
    for limit, row in _SNOW_BY_VIS:
        if vis_m <= limit:
            return row[col]
    return _VL


def _weather_groups(tokens: list) -> list:
    """Return the METAR's present-weather groups (no VC groups, nothing after RMK)."""
    groups = []
    for tok in tokens:
        if tok == "RMK":
            break
        m = _WX_RE.match(tok)
        if m and m.group("int") != "VC":
            groups.append(tok)
    return groups


def _snow_condition(groups: list, tokens: list, oat_c: float, day: bool) -> tuple[str, str]:
    """Return (condition, explanation) for snow: intensity from visibility (FAA Table 57)."""
    text = " ".join(groups)
    vis = metar_visibility_m(tokens)
    intensity = snow_intensity(vis, oat_c, day)
    vis_txt = f"visibility {vis:.0f} m" if vis is not None else "visibility unknown"
    why = (f"{text}, {vis_txt}, {'day' if day else 'night'}, {oat_c:.0f} C: "
           f"{intensity} snow (FAA Table 57)")
    wet = any("RA" in g or "DZ" in g for g in groups)
    if intensity == _H:
        result = OTHER
    elif wet and intensity == _M and oat_c > 0:
        result, why = SNRA_M, why + ", mixed with rain"
    elif wet:
        result, why = FZRA_L, why + ", mixed with rain/drizzle: light freezing rain times"
    elif any("FZFG" in g for g in groups):
        result, why = SN_FZFG, why + ", with freezing fog"
    else:
        result = {_VL: SN_VL, _L: SN_L, _M: SN_M}[intensity]
    return result, why


def condition_from_metar(metar: str, oat_c: Optional[float],
                         day: bool) -> tuple[Optional[str], str]:
    """Return (condition, explanation) for a METAR; None = no precipitation or freezing fog.

    oat_c overrides the METAR temperature if given.
    """
    tokens = metar.split()
    if oat_c is None:
        oat_c = metar_temperature_c(tokens)
    if oat_c is None:
        return None, "no temperature"
    groups = _weather_groups(tokens)
    text = " ".join(groups) or "no weather"

    def has(code):
        return any(code in g for g in groups)

    heavy = any(g.startswith("+") and re.search("SN|RA|DZ", g) for g in groups)
    if heavy or has("PL") or has("GR") or has("GS"):
        result = (OTHER, f"{text}: heavy precipitation, ice pellets or hail")
    elif has("FZRA"):
        result = ((FZRA_L, f"{text}: light freezing rain") if "-FZRA" in text else
                  (OTHER, f"{text}: moderate/heavy freezing rain"))
    elif has("FZDZ"):
        result = (FZDZ, f"{text}: freezing drizzle")
    elif any(re.search("SN|SG", g) for g in groups if not re.fullmatch("(DR|BL)SN", g)):
        result = _snow_condition(groups, tokens, oat_c, day)
    elif has("FZFG") or has("IC") or ((has("BR") or has("FG")) and oat_c <= 0):
        result = (FZFG, f"{text}: freezing fog/mist or ice crystals")
    elif (has("RA") or has("DZ")) and oat_c > 0:
        result = (RA_CSW, f"{text}, {oat_c:.0f} C: rain on a cold-soaked wing")
    elif has("RA") or has("DZ"):
        result = (FZRA_L, f"{text}, {oat_c:.0f} C: rain at or below 0 C, light freezing rain times")
    else:
        result = (None, text)
    return result


def _pick(fluid: str, condition: str, row: dict) -> Optional[tuple]:
    """Return (min, max, chosen) for a condition in a table row, or None if no guideline exists."""
    if fluid == "II" and condition in SNOW:
        lo, hi = row.get("snow", (None, None))
        if lo is None:
            return None
        chosen = {SN_VL: hi, SN_L: (lo + hi) / 2.0, SN_M: lo}[condition]
        return lo, hi, chosen
    if condition not in row:
        return None
    lo, hi = row[condition]
    return lo, hi, (lo + hi) / 2.0


def holdover_minutes(fluid: str, condition: Optional[str],
                     oat_c: float) -> tuple[Optional[int], str]:
    """Return (minutes for AntiIceFluid, explanation); None minutes = leave the timer as it is.

    The middle of the FAA range (Type II snow: by intensity within its one
    snow column), at least NO_HOT_MIN, at most PSX_MAX_MIN.
    """
    if condition is None:
        return None, ("no precipitation or freezing fog: PSX ices nothing on the ground, "
                      "timer left alone")
    if condition == OTHER:
        return NO_HOT_MIN, f"no FAA holdover time exists for {condition}"
    if condition in (SNRA_M, RA_CSW) and oat_c <= 0:
        return NO_HOT_MIN, f"no FAA holdover time exists for {condition} at 0 C or below"
    table = TABLES.get(fluid)
    if table is None:
        return None, f"unknown fluid type {fluid}"
    row = next(r for lower, r in table if oat_c >= lower)
    got = _pick(fluid, condition, row)
    if got is None:
        return NO_HOT_MIN, (f"no FAA holdover time exists for Type {fluid} in {condition} "
                            f"at {oat_c:.0f} C")
    lo, hi, chosen = got
    minutes = int(round(min(max(chosen, NO_HOT_MIN), PSX_MAX_MIN)))
    capped = " (PSX's timer stops at 45)" if chosen > PSX_MAX_MIN else ""
    return minutes, (f"FAA Type {fluid}, {condition}, {oat_c:.0f} C: {lo}-{hi} min -> "
                     f"{minutes} min{capped}")


def parse_fluid_minutes(text: str) -> dict:
    """Parse --holdover-efb-minutes ("14:I,20:II,35:IV") into {minutes: fluid type}."""
    out = {}
    for item in (text or "").split(","):
        item = item.strip()
        if not item:
            continue
        minutes, _, fluid = item.partition(":")
        fluid = fluid.strip().upper()
        if fluid not in TABLES:
            raise ValueError(f"unknown fluid type {fluid!r} (I, II or IV)")
        out[int(minutes)] = fluid
    return out
