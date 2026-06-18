"""fw_cb.py — FrankenWeather CB (cumulonimbus) prediction logic.

Shared between frankenweather.py (live PSX injection) and fw_scanner.py
(offline quality scanning).  All functions are pure computations with no
external dependencies beyond the standard library.
"""
from datetime import datetime, timezone


# Default PSX Wx semicolon-string field values (24 fields, 0-indexed).
WX_DEFAULTS = [
    "0",        # [0]  hiCloudCov (oktas 0-8)
    "45000",    # [1]  hiCloudTop  ft
    "45000",    # [2]  hiCloudBase ft
    "0",        # [3]  loCloudCov
    "45000",    # [4]  loCloudTop  ft
    "45000",    # [5]  loCloudBase ft
    "0",        # [6]  turbIntensity
    "5000",     # [7]  turbTop     ft
    "0",        # [8]  turbBase    ft
    "0",        # [9]  cbCloudCov
    "35000",    # [10] cbCloudTop  ft
    "3000",     # [11] cbCloudBase ft
    "0",        # [12] microburstMode
    "0",        # [13] microburstRandom
    "400",      # [14] microburstOutflow
    "0",        # [15] inversionOn
    "2320",     # [16] inversionTop ft
    "50",       # [17] inversionTmp
    "0000000",  # [18] arptWindVarDirSpd
    "0",        # [19] arptWindGust kt
    "9999",     # [20] visibMtrs
    "0",        # [21] precipLevel
    "15",       # [22] surfaceTmp  °C
    "2992",     # [23] QNH        inHg×100
]


def cape_to_cb_oktas(cape_jkg: float, cin_jkg: float) -> int:
    """Convert CAPE and CIN to CB cloud coverage in oktas.

    Strong convective inhibition (CIN < -200 J/kg) suppresses CBs even with
    high CAPE. Otherwise coverage scales with CAPE magnitude.
    """
    if cape_jkg < 100.0 or cin_jkg < -200.0:
        return 0
    if cape_jkg < 500.0:
        return 2
    if cape_jkg < 1500.0:
        return 4
    if cape_jkg < 3000.0:
        return 6
    return 8


def cb_base_ft(temp_c: float, dp_c: float) -> int:
    """Estimate CB base in feet from LCL: 125 m per °C of dewpoint depression."""
    return max(1500, int((temp_c - dp_c) * 125.0 * 3.28084))


def cb_tops_ft(cape_jkg: float) -> int:
    """Estimate CB tops in feet from CAPE (J/kg).

    Targets ~40000 ft at CAPE 1000 J/kg (typical tropical CB), saturates at
    55000 ft for severe convection above ~2000 J/kg.
    """
    return max(25000, min(55000, 20000 + int(cape_jkg * 20)))


def om_cb_fields(om: dict, metar_showers: bool = False, metar_ts: bool = False) -> tuple:
    """Return (cb_oktas, cb_tops_ft, cb_base_ft) derived from Open-Meteo data.

    Returns (0, default_tops, default_base) when no convective activity detected.

    metar_showers: METAR observed SH-type precip (SHRA, TCU) — confirms showers.
    metar_ts: METAR observed thunderstorm (TS, GR, LTG, FC) — bypasses precip gate.
    """
    hourly = om.get("hourly") or {}
    hour_idx = datetime.now(timezone.utc).hour

    def _h(key, default):
        lst = hourly.get(key) or []
        return float(lst[hour_idx]) if hour_idx < len(lst) else float(default)

    wmo_code = int((om.get("current") or {}).get("weather_code", 0))
    temp_c = float((om.get("current") or {}).get("temperature_2m", 15))
    cape = _h("cape", 0)
    cin = _h("convective_inhibition", 0)
    h_temp = _h("temperature_2m", temp_c)
    h_dp = _h("dewpoint_2m", h_temp - 5.0)
    showers_mm = _h("showers", 0)
    # Frontal systems report as "precipitation" not "showers" in OM; use total precip
    # as fallback when WMO explicitly codes a thunderstorm.
    if wmo_code in (95, 96, 99) and showers_mm < 0.5:
        showers_mm = _h("precipitation", 0)
    is_ts = wmo_code in (95, 96, 99) or metar_ts

    oktas = cape_to_cb_oktas(cape, cin)

    if is_ts:
        # Thunderstorm directly observed — no precipitation gate needed.
        if oktas == 0 and cin > -200.0:
            oktas = 4
    elif showers_mm >= 0.5 or metar_showers:
        # Showers confirmed; cap coverage by intensity
        if showers_mm < 2.0:
            oktas = min(oktas, 2)
        elif showers_mm < 5.0:
            oktas = min(oktas, 4)
        elif showers_mm < 15.0:
            oktas = min(oktas, 6)
    else:
        oktas = 0

    if oktas == 0:
        return 0, int(WX_DEFAULTS[10]), int(WX_DEFAULTS[11])
    return oktas, cb_tops_ft(cape), cb_base_ft(h_temp, h_dp)


def apply_om_cb(wx_str: str, om: dict,
                metar_showers: bool = False, metar_ts: bool = False) -> str:
    """Replace CB fields in a PSX Wx string with Open-Meteo derived values."""
    oktas, tops, base = om_cb_fields(om, metar_showers=metar_showers, metar_ts=metar_ts)
    fields = wx_str.split(';')
    fields[9] = str(oktas)
    fields[10] = str(tops)
    fields[11] = str(base)
    return ';'.join(fields)


def apply_fake_cb(wx_str: str, oktas: int, base: int, tops: int) -> str:
    """Overwrite the CB fields in a PSX Wx semicolon string."""
    fields = wx_str.split(';')
    fields[9] = str(oktas)
    fields[10] = str(tops)
    fields[11] = str(base)
    return ';'.join(fields)
