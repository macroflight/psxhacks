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
# PSX's CB radar echo is the same size whatever the CB (2-8 oktas, tops
# FL250-450, bases 1,500-6,000 ft; headless tests 2026-11-14): about 6 nm
# across in red and 18 nm in amber, red included (magenta 2 nm)
RADAR_RED_NM = 6.0
RADAR_AMBER_NM = 18.0
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
        "radar_core_nm": RADAR_RED_NM, "radar_amber_nm": RADAR_AMBER_NM,
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
