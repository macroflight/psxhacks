"""frankenweather's CB messages (addon=FRANKENWEATHER:CBS:<uuid>:<json>).

One message lists every CB frankenweather has in PSX right now, for
add-ons that draw the CBs elsewhere (PSX.NET.Orchestration in MSFS): where
each CB is, its base and tops, how it drifts, its anvil and the surface
wind. Plain JSON, sent on any change and every 60 s; the receiver runs the
positions on from the drift in between. The message format is documented
in docs/developers/frankenweather-cb-messages.md (the contract: change it there too).

Pure functions only: frankenweather.py collects the data and sends.
"""
from datetime import datetime, timezone
from typing import Optional

CBS_VERSION = 1
CBS_RESEND_S = 60.0          # sent at least this often, and soon after any change
CBS_MIN_GAP_S = 15.0         # but at most this often (a growing CB changes all the time)
CBS_TOP_STEP_FT = 2000       # tops (and bases) count as changed in steps of this
# What PSX paints on its weather radar for a CB, measured (headless tests G2/G3,
# 2026-11-15, ND 40 nm WXR, colours measured): diameters (nm) of red (magenta
# included) and amber+red, by oktas and depth (top - base, thousands of ft).
# The zone's size and nearby zones make no difference; a big or deep CB is
# about 6 / 18.6 nm, a small or shallow one less (1/8 8,000 ft deep: barely a
# dot). Between table points linear; deeper than the last point: its value;
# shallower than the first: down to nothing at 4,000 ft.
ECHO_TABLE = {   # oktas: [(depth kft, red nm, amber+red nm), ...]
    1: [(8, 0.0, 1.1), (12, 1.2, 3.1), (17, 1.9, 4.7), (22, 2.3, 7.1), (27, 2.3, 7.8),
        (32, 3.2, 9.9)],
    2: [(8, 0.9, 2.3), (12, 1.9, 5.7), (17, 2.5, 9.9), (22, 4.6, 14.7), (27, 5.0, 17.6),
        (32, 5.8, 18.6)],
    3: [(8, 1.1, 3.6), (12, 2.8, 8.8), (17, 4.9, 15.6), (22, 6.0, 18.6)],
    4: [(8, 2.6, 7.8), (12, 5.2, 15.3), (17, 6.0, 18.6)],
    6: [(8, 4.8, 15.8), (12, 6.0, 18.6)],
    8: [(8, 6.0, 18.6)],
}
ECHO_MIN_DEPTH_KFT = 4.0
# Anvil: visible when the top reaches within this of the tropopause, or
# above it, and the CB has at least ANVIL_MIN_OKTAS (or came from radar or
# lightning); length ANVIL_BASE_NM + ANVIL_NM_PER_KT x the wind at the top,
# at most ANVIL_MAX_NM. Downwind of the wind at the top, at the CB.
ANVIL_TROPOPAUSE_MARGIN_FT = 3000
ANVIL_MIN_OKTAS = 6
ANVIL_BASE_NM = 10.0
ANVIL_NM_PER_KT = 0.5
ANVIL_MAX_NM = 80.0
SOURCES = ("radar", "lightning", "model", "metar", "sigmet", "test")


def iso_utc(epoch_s: float) -> str:
    """Return a UTC time as ISO 8601 with a Z, whole seconds."""
    return datetime.fromtimestamp(epoch_s, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def wx_cb(wx: str) -> Optional[dict]:
    """Return a Wx<N> string's CB as {"oktas", "base_ft", "top_ft"} (above the zone), or None."""
    parts = (wx or "").split(';')
    try:
        oktas = int(parts[9])
        if oktas <= 0:
            return None
        return {"oktas": oktas, "base_ft": int(parts[11]), "top_ft": int(parts[10])}
    except (IndexError, ValueError):
        return None


def wx_surface_wind(wx: str) -> Optional[dict]:
    """Return a Wx<N> string's surface wind as {"dir_true", "kt", "gust_kt"}, or None.

    Field 18 is "xxxDDDSS" (direction, speed), field 19 the gust (kt).
    """
    parts = (wx or "").split(';')
    try:
        enc, gust = parts[18], int(parts[19])
        wdir, kt = int(enc[3:6]), int(enc[6:8])
    except (IndexError, ValueError):
        return None
    return {"dir_true": wdir % 360, "kt": kt, "gust_kt": gust if gust > kt else None}


def anvil(top_ft: float, tropopause_ft: Optional[float], oktas: int, source: str,
          top_wind: Optional[tuple]) -> Optional[dict]:
    """Return the CB's anvil {"visible", "hdg_true", "length_nm"}, or None without one.

    top_wind: (dir_deg the wind blows from, kt) at the CB's top, at the CB.
    No tropopause known: no anvil.
    """
    if tropopause_ft is None or top_wind is None:
        return None
    if top_ft < tropopause_ft - ANVIL_TROPOPAUSE_MARGIN_FT:
        return None
    if oktas < ANVIL_MIN_OKTAS and source not in ("radar", "lightning"):
        return None
    wdir, kt = top_wind
    return {"visible": True, "hdg_true": round(wdir + 180.0) % 360,
            "length_nm": round(min(ANVIL_MAX_NM, ANVIL_BASE_NM + ANVIL_NM_PER_KT * kt))}


def _echo_row(oktas: int, depth_kft: float) -> tuple:
    """Return (red, amber) for a measured oktas row (ECHO_TABLE) at depth_kft."""
    row = ECHO_TABLE[oktas]
    if depth_kft <= row[0][0]:
        f = max(0.0, (depth_kft - ECHO_MIN_DEPTH_KFT) / (row[0][0] - ECHO_MIN_DEPTH_KFT))
        return row[0][1] * f, row[0][2] * f
    for (d0, r0, a0), (d1, r1, a1) in zip(row, row[1:]):
        if depth_kft <= d1:
            f = (depth_kft - d0) / (d1 - d0)
            return r0 + (r1 - r0) * f, a0 + (a1 - a0) * f
    return row[-1][1], row[-1][2]


def echo_diameters(oktas: int, base_ft: float, top_ft: float) -> tuple:
    """Return (red, amber+red) diameters (nm) PSX paints on its radar for a CB.

    From ECHO_TABLE: by oktas (5 and 7 between their neighbours) and depth.
    """
    oktas = max(1, min(8, oktas))
    depth = max(0.0, (top_ft - base_ft) / 1000.0)
    rows = sorted(ECHO_TABLE)
    if oktas in ECHO_TABLE:
        red, amber = _echo_row(oktas, depth)
    else:
        lo = max(r for r in rows if r < oktas)
        hi = min(r for r in rows if r > oktas)
        (r0, a0), (r1, a1) = _echo_row(lo, depth), _echo_row(hi, depth)
        f = (oktas - lo) / (hi - lo)
        red, amber = r0 + (r1 - r0) * f, a0 + (a1 - a0) * f
    return round(red, 1), round(amber, 1)


def cb_entry(cb_id: str, pos: tuple, cb: dict, elev_ft: float, extra: dict) -> dict:
    """Return one CB of the message.

    pos (lat, lon) of the CB now; cb from wx_cb() (heights above the zone);
    elev_ft the zone's elevation; extra: "zone" (1-7), "top_fw_ft" (the top frankenweather's
    data says, MSL, None: PSX's), "drift", "anvil", "surface_wind", "source",
    "msfs_only".
    """
    top_psx = int(round(cb["top_ft"] + elev_ft))
    top_fw = extra.get("top_fw_ft")
    return {
        "id": cb_id, "zone": extra.get("zone"),
        "lat": round(pos[0], 5), "lon": round(pos[1], 5),
        "oktas": cb["oktas"],
        "base_ft_msl": int(round(cb["base_ft"] + elev_ft)),
        "top_ft_msl_fw": int(round(max(top_fw, top_psx))) if top_fw is not None else top_psx,
        "top_ft_msl_psx": top_psx,
        **dict(zip(("radar_core_nm", "radar_amber_nm"),
                   echo_diameters(cb["oktas"], cb["base_ft"], cb["top_ft"]))),
        "drift": extra.get("drift"),
        "anvil": extra.get("anvil"),
        "surface_wind": extra.get("surface_wind"),
        "source": extra.get("source") if extra.get("source") in SOURCES else "model",
        "msfs_only": bool(extra.get("msfs_only", False)),
    }


def message(time_s: float, tropopause_ft: Optional[float], cbs: list) -> dict:
    """Return the whole CBS message as a dict, ready for JSON."""
    return {"v": CBS_VERSION, "time": iso_utc(time_s),
            "tropopause_ft": int(round(tropopause_ft)) if tropopause_ft is not None else None,
            "cbs": cbs}


def signature(msg: dict) -> tuple:
    """Return what makes a message new (anything but the positions and the time)."""
    return (msg.get("tropopause_ft"),) + tuple(
        (c["id"], c["oktas"], round(c["base_ft_msl"] / CBS_TOP_STEP_FT),
         round(c["top_ft_msl_fw"] / CBS_TOP_STEP_FT), round(c["top_ft_msl_psx"] / CBS_TOP_STEP_FT),
         None if not c["drift"] else (c["drift"]["hdg_true"], c["drift"]["kt"]),
         None if not c["anvil"] else (c["anvil"]["hdg_true"], c["anvil"]["length_nm"]),
         c["source"])
        for c in msg["cbs"])
