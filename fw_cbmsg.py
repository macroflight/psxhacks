"""frankenweather's CB messages (addon=FRANKENWEATHER:CBS:<uuid>:<json>).

One message lists every CB frankenweather has in PSX right now, for
add-ons that draw the CBs elsewhere (PSX.NET.Orchestration in MSFS): where
each CB is, its base and tops, how it drifts, its anvil and the surface
wind. Plain JSON, sent on any change and every 60 s; the receiver runs the
positions on from the drift in between. The message format is documented
in docs/developers/frankenweather-cb-messages.md (the contract: change it there too).

Pure functions only: frankenweather.py collects the data and sends.
"""
import math
import unittest
from datetime import datetime, timezone
from typing import Optional

CBS_VERSION = 1
EARTH_NM = 3440.065
CBS_RESEND_S = 60.0          # sent at least this often, and soon after any change
CBS_MIN_GAP_S = 15.0         # but at most this often (a growing CB changes all the time)
CBS_TOP_STEP_FT = 2000       # tops (and bases) count as changed in steps of this
# What PSX paints on its weather radar for a CB, measured (headless tests G2/G3,
# 2026-11-15, ND 40 nm WXR, colours measured): diameters (nm) of red (magenta
# included) and amber+red, by oktas and depth (top - base, thousands of ft).
# Measured on an isolated zone: a big or deep CB is about 6 / 18.6 nm, a small
# or shallow one less (1/8 8,000 ft deep: barely a dot). Between table points
# linear; deeper than the last point: its value; shallower than the first: down
# to nothing at 4,000 ft. Another zone nearby shrinks it (ECHO_DIST_SCALE);
# the zone's radius field makes no difference.
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
# PSX shrinks a zone's echo by the distance from its centre to the nearest
# other zone (WxMode 1-7, any weather, by centre, not by the CB) whose name
# differs in its first 4 characters (same 4: ignored). Measured (headless test
# G5, 2026-11-15, 8 oktas 32,000 ft deep, ND 40 nm): (distance nm, red factor,
# amber factor) against the isolated size; linear between, 1.0 beyond the last.
# PSX recomputes it continuously (a neighbour moved closer: smaller in 10-25 s).
ECHO_DIST_SCALE = [(7.0, 0.37, 0.30), (10.0, 0.37, 0.41), (12.0, 0.45, 0.52),
                   (15.0, 0.53, 0.59), (18.0, 0.59, 0.66), (22.0, 0.75, 0.80),
                   (30.0, 0.75, 0.86), (45.0, 0.75, 0.90), (80.0, 0.84, 0.92),
                   (150.0, 1.0, 1.0)]
ZONE_NAME_PREFIX = 4
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


def echo_scale(nearest_nm: Optional[float]) -> tuple:
    """Return (red, amber) factors on the isolated echo size, by the nearest zone (nm or None)."""
    table = ECHO_DIST_SCALE
    if nearest_nm is None or nearest_nm >= table[-1][0]:
        return 1.0, 1.0
    if nearest_nm <= table[0][0]:
        return table[0][1], table[0][2]
    for (d0, r0, a0), (d1, r1, a1) in zip(table, table[1:]):
        if d0 <= nearest_nm <= d1:
            f = (nearest_nm - d0) / (d1 - d0)
            return r0 + (r1 - r0) * f, a0 + (a1 - a0) * f
    return 1.0, 1.0


def _wxmode_pos_name(mode: str) -> Optional[tuple]:
    """Return (lat deg, lon deg, name prefix) of a WxMode string, or None."""
    fields = (mode or "").split(";")
    try:
        lat, lon = math.degrees(float(fields[0])), math.degrees(float(fields[1]))
    except (IndexError, ValueError):
        return None
    name = fields[5] if len(fields) > 5 else ""
    return lat, lon, name[:ZONE_NAME_PREFIX].upper()


def nearest_zone_nm(modes: dict, zone_num: int) -> Optional[float]:
    """Return the distance (nm) from zone_num's centre to the nearest zone PSX counts for its echo.

    modes: {zone number: WxMode string}. Zones whose name starts with the
    same ZONE_NAME_PREFIX characters don't count (PSX ignores them). None
    if there is no such zone.
    """
    me = _wxmode_pos_name(modes.get(zone_num, ""))
    if me is None:
        return None
    best = None
    for num, mode in modes.items():
        other = _wxmode_pos_name(mode) if num != zone_num else None
        if other is None or other[2] == me[2]:
            continue
        p1, p2 = math.radians(me[0]), math.radians(other[0])
        a = (math.sin((p2 - p1) / 2.0) ** 2 + math.cos(p1) * math.cos(p2) *
             math.sin(math.radians(other[1] - me[1]) / 2.0) ** 2)
        dist = 2.0 * EARTH_NM * math.asin(min(1.0, math.sqrt(a)))
        best = dist if best is None else min(best, dist)
    return best


def echo_diameters(oktas: int, base_ft: float, top_ft: float,  # pylint: disable=too-many-locals
                   nearest_nm: Optional[float] = None) -> tuple:
    """Return (red, amber+red) diameters (nm) PSX paints on its radar for a CB.

    From ECHO_TABLE: by oktas (5 and 7 between their neighbours) and depth,
    times echo_scale() for the nearest other zone (nearest_zone_nm()).
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
    red_f, amber_f = echo_scale(nearest_nm)
    return round(red * red_f, 1), round(amber * amber_f, 1)


def cb_entry(cb_id: str, pos: tuple, cb: dict, elev_ft: float, extra: dict) -> dict:
    """Return one CB of the message.

    pos (lat, lon) of the CB now; cb from wx_cb() (heights above the zone);
    elev_ft the zone's elevation; extra: "zone" (1-7), "top_fw_ft" (the top frankenweather's
    data says, MSL, None: PSX's), "drift", "anvil", "surface_wind", "source",
    "msfs_only", "nearest_zone_nm" (nearest_zone_nm(), for the radar size).
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
                   echo_diameters(cb["oktas"], cb["base_ft"], cb["top_ft"],
                                  extra.get("nearest_zone_nm")))),
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


class TestEcho(unittest.TestCase):
    """Echo size against the nearest zone (G5 measurements)."""

    @staticmethod
    def _mode(lat, lon, name):
        return f"{math.radians(lat):.8f};{math.radians(lon):.8f};320;0;11;{name}00n"

    def test_scale(self):
        """Isolated or far: full size; close: ~30 % amber; between: interpolated."""
        self.assertEqual(echo_scale(None), (1.0, 1.0))
        self.assertEqual(echo_scale(200.0), (1.0, 1.0))
        self.assertEqual(echo_scale(3.0), (0.37, 0.30))
        red_f, amber_f = echo_scale(11.0)
        self.assertAlmostEqual(amber_f, 0.465, places=3)
        self.assertAlmostEqual(red_f, 0.41, places=3)
        self.assertEqual(echo_diameters(8, 3000, 35000, None), (6.0, 18.6))
        self.assertEqual(echo_diameters(8, 3000, 35000, 5.0), (2.2, 5.6))

    def test_nearest_zone(self):
        """Nearest by centre; same 4-character prefix ignored; bad modes skipped."""
        modes = {1: self._mode(30.0, -150.0, "KSFO"), 2: self._mode(30.0, -149.8, "KSFO"),
                 3: self._mode(30.25, -150.0, "KLAX"), 4: "garbage",
                 5: self._mode(40.0, -150.0, "KSEA")}
        self.assertAlmostEqual(nearest_zone_nm(modes, 1), 15.0, delta=0.1)
        self.assertAlmostEqual(nearest_zone_nm(modes, 5), 585.0, delta=1.0)
        self.assertIsNone(nearest_zone_nm({1: modes[1], 2: modes[2]}, 1))
        self.assertIsNone(nearest_zone_nm({1: "x"}, 1))
