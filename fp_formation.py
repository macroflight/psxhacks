"""frankenpush's formation autopilot: fly Flight Centre's formation_steer with PSX's autopilot.

Flight Centre's PinguEye airborne station works out the heading, ground
speed and altitude to join another aircraft in trail or echelon and sends
them as formation_steer (interface v1, 2026-11-11, docs/frankenpush.md).
This module decides, from a snapshot of PSX's state, what to do with each
command: which MCP knobs to turn and when to switch between ALT and V/S.
frankenpush.py executes the actions and sends the formation_status
messages. Pure logic, unit-tested here (python3 -m unittest fp_formation).

Rules (from the user and Flight Centre's interface):
- Opt-in (frankenpush --formation-autopilot); otherwise "refused".
- Only steers while the centre autopilot CMD C (and no other) is engaged,
  the A/T mode is SPD, the roll mode HDG SEL, the pitch mode ALT or V/S,
  the bank limit AUTO, flaps up, airborne and 1,000 ft AGL or more. It
  never selects any of these itself: the crew sets them up. Otherwise
  "standby" (before steering) or "disengaged" (while steering).
- Turns only the MCP HDG, SPD, ALT and V/S knobs, and switches between
  ALT HOLD and V/S (at 1,000 ft/min) for altitude changes.
- Any MCP or mode change it didn't make itself is a pilot override: stop
  at once, touch nothing more.
"""

import math
import time
import unittest
from typing import Optional

FORMATION_VERSION = 1

# Afds (Qs434) mode numbers (frankenpush._AFDS_MODE_NAMES; checked on the
# headless test sim 2026-11-11: HDG SEL 3, HDG HOLD 2, ALT 9, V/S 13, SPD 19)
AFDS_SPD = 19
AFDS_HDG_SEL = 3
AFDS_ALT = 9
AFDS_VS = 13

# MCP knob clicks: HDG 1 deg, SPD 1 kt or 0.001 Mach, ALT 100 ft, V/S 100 ft/min.
# PSX takes DELTA events of up to 16 clicks, at up to 20 Hz.
MAX_CLICKS = 16
MCP_MACH_FROM = 400          # McpWdoSpd 400-950 is Mach x 1000, below that IAS kt

MIN_AGL_FT = 1000.0
VS_HUNDREDS = 10             # climb/descent rate, 1,000 ft/min (overridden by limits.vs_fpm)
ALT_BAND_FT = 100.0          # within this of the command: ALT HOLD
MIN_IAS_KT = 240.0           # clean minimum we steer to (flaps must be up anyway)
VMO_KT, MMO = 365.0, 0.92    # 747-400
VMO_MARGIN_KT, MMO_MARGIN = 15.0, 0.02
HDG_STEP_DEG = 1
LEVEL_BANK_DEG = 2.0         # measure the drift only with the wings this level
SPD_STEP_KT, SPD_STEP_MACH = 2, 5     # Mach in window units (0.005)

# A knob we turned may show any value between where it was and our target
# for this long; after that it must show the target.
TURN_GRACE_S = 6.0
# After pressing V/S PSX shows the current V/S in the window until we set ours
VS_GRACE_S = 4.0
# After a disengage, a new engagement needs Flight Centre to have gone quiet
# this long first (it stops sending once it hears "disengaged")
REENGAGE_SILENCE_S = 6.0
# Send a formation_status with every Nth command even without a change
STATUS_EVERY = 5


def wrap180(deg: float) -> float:
    """Return deg wrapped into -180..180."""
    return (deg + 180.0) % 360.0 - 180.0


def mach_from_tas(tas_kt: float, oat_c: float) -> float:
    """Return the Mach number for a true airspeed and outside air temperature."""
    return tas_kt / (38.967854 * math.sqrt(max(oat_c + 273.15, 150.0)))


def track_from_samples(samples) -> Optional[float]:
    """Return the true track (deg) from [(t, lat, lon), ...] oldest first, or None.

    Uses the newest sample and the newest one at least 4 s older (positions
    jitter over shorter spans).
    """
    if len(samples) < 2:
        return None
    t1, lat1, lon1 = samples[-1]
    old = next((s for s in reversed(samples) if t1 - s[0] >= 4.0), None)
    if old is None:
        return None
    _, lat0, lon0 = old
    p0, p1 = math.radians(lat0), math.radians(lat1)
    dlon = math.radians(lon1 - lon0)
    y = math.sin(dlon) * math.cos(p1)
    x = math.cos(p0) * math.sin(p1) - math.sin(p0) * math.cos(p1) * math.cos(dlon)
    if abs(x) < 1e-12 and abs(y) < 1e-12:
        return None
    return math.degrees(math.atan2(y, x)) % 360.0


def mcp_heading(cmd_track: float, heading_true: float, track_true: Optional[float],
                magvar: float, hdg_true_ref: bool) -> int:
    """Return the MCP heading (0-359) that flies the commanded true track.

    Flight Centre's heading is a true track (no wind, no variation): take off
    the drift (track - heading) and, unless the heading reference is TRUE,
    the magnetic variation (east positive: magnetic = true - variation).
    """
    drift = wrap180(track_true - heading_true) if track_true is not None else 0.0
    hdg = cmd_track - drift - (0.0 if hdg_true_ref else magvar)
    return int(round(hdg)) % 360


def mcp_speed(cmd_gs: float, gs: float, tas: float, ias: float, oat_c: float,  # pylint: disable=too-many-arguments,too-many-positional-arguments
              mach_mode: bool, max_ias: float, max_mach: float) -> int:
    """Return the McpWdoSpd value (IAS kt, or Mach x 1000 in Mach mode) for a ground speed.

    The wanted TAS is our TAS plus the missing ground speed; IAS and Mach scale
    with it. Clamped to the formation limits, VMO/MMO minus a margin, and a
    clean minimum speed.
    """
    mach = mach_from_tas(tas, oat_c)
    ratio = max(tas + (cmd_gs - gs), 1.0) / max(tas, 1.0)
    top = min(min(max_ias, VMO_KT - VMO_MARGIN_KT) / max(ias, 1.0),
              min(max_mach, MMO - MMO_MARGIN) / max(mach, 0.01))
    ratio = min(ratio, top)
    ratio = max(ratio, MIN_IAS_KT / max(ias, 1.0))
    if mach_mode:
        return int(round(mach * ratio * 1000))
    return int(round(ias * ratio))


class Formation:  # pylint: disable=too-many-instance-attributes
    """The formation autopilot's state machine (see the module docstring).

    on_steer(msg, sim) and tick(sim) return (actions, statuses): actions are
    ("turn", knob, clicks) and ("press", button) for frankenpush to send in
    order; statuses are formation_status dicts to send to Flight Centre.
    sim is a dict snapshot of PSX (see frankenpush._formation_snapshot()).
    """

    def __init__(self, enabled: bool, clock=time.monotonic):
        """Make the state machine; enabled is the --formation-autopilot opt-in."""
        self.enabled = enabled
        self.clock = clock
        self.state = "off"          # off / standby / engaged / disengaged / refused
        self.reason: Optional[str] = None
        self.last_seq: Optional[int] = None
        self.last_steer_at: Optional[float] = None
        self.valid_for = 10.0
        self.count = 0
        self.ap_engaged = False
        self.expect: dict = {}      # window -> (lo, hi, until, target)
        self.vs_pressed_at = -1e9
        self.alt_pressed_at = -1e9
        self.timed_out = False      # disengaged by NO GUIDANCE: tell the crew
        self.drift = 0.0            # last drift (track - heading) measured wings level
        self.events: list = []      # log lines for frankenpush

    # ------------------------------------------------------------ helpers

    def status(self) -> dict:
        """Return a formation_status message for the current state."""
        return {"type": "formation_status", "protocol_version": 1,
                "formation_version": FORMATION_VERSION, "state": self.state,
                "reason": self.reason, "seq": self.last_seq, "ap_engaged": self.ap_engaged}

    def _set_state(self, state: str, reason: Optional[str]) -> list:
        if (state, reason) == (self.state, self.reason):
            return []
        self.events.append(f"formation autopilot: {self.state} -> {state}"
                           f"{f' ({reason})' if reason else ''}")
        self.state, self.reason = state, reason
        if state != "engaged":
            self.expect.clear()
        return [self.status()]

    @staticmethod
    def _lit(sim: dict, name: str) -> bool:
        return bool(sim.get(name, 0) & (8 + 8192))

    def _cmd_c_only(self, sim: dict) -> bool:
        return (self._lit(sim, "McpPshCmdC") and not self._lit(sim, "McpPshCmdL") and
                not self._lit(sim, "McpPshCmdR"))

    def _not_ready(self, sim: dict) -> Optional[str]:
        """Return why we can't steer now (standby reason), or None."""
        if sim.get("on_ground") is None or sim.get("thr") is None or sim.get("alt_ft") is None:
            return "NO DATA FROM PSX YET"
        if not self._cmd_c_only(sim):
            return "AUTOPILOT NOT ENGAGED" if not any(
                self._lit(sim, f"McpPshCmd{s}") for s in "LCR") else "CMD C ONLY"
        wrong = []
        if sim.get("thr") != AFDS_SPD:
            wrong.append("A/T NOT SPD")
        if sim.get("roll") != AFDS_HDG_SEL:
            wrong.append("NOT HDG SEL")
        if sim.get("pitch") not in (AFDS_ALT, AFDS_VS):
            wrong.append("NOT ALT OR V/S")
        if sim.get("bank_limit") != 0:
            wrong.append("BANK LIMIT NOT AUTO")
        if sim.get("flap_lever") not in (0, None):
            wrong.append("FLAPS NOT UP")
        return ", ".join(wrong) or None

    def _refusal(self, sim: dict) -> Optional[str]:
        if not self.enabled:
            return "NOT ENABLED"
        if sim.get("on_ground") is True:
            return "ON THE GROUND"
        alt, elev = sim.get("alt_ft"), sim.get("elev_ft")
        if alt is not None and elev is not None and alt - elev < MIN_AGL_FT:
            return "TOO LOW"
        return None

    # ------------------------------------------------------- Flight Centre

    def on_steer(self, msg: dict, sim: dict):  # pylint: disable=too-many-return-statements,too-many-branches
        """Handle one formation_steer; return (actions, statuses)."""
        now = self.clock()
        try:
            seq = int(msg.get("seq"))
        except (TypeError, ValueError):
            return [], []
        if self.last_seq is not None and seq <= self.last_seq:
            return [], []
        silence = (now - self.last_steer_at) if self.last_steer_at is not None else 1e9
        self.last_seq, self.last_steer_at = seq, now
        self.ap_engaged = any(self._lit(sim, f"McpPshCmd{s}") for s in "LCR")
        self.count += 1
        statuses = []
        if not msg.get("engaged"):
            if self.state in ("standby", "engaged"):
                statuses = self._set_state("disengaged", "FLIGHT CENTRE DISENGAGED")
            self.state = "off" if self.state != "refused" else self.state
            self.timed_out = False
            return [], statuses or [self.status()]
        try:
            self.valid_for = float(msg.get("valid_for_s") or 10.0)
        except (TypeError, ValueError):
            self.valid_for = 10.0
        refusal = self._refusal(sim)
        if refusal:
            statuses = self._set_state("refused", refusal)
            return [], statuses or self._every_nth()
        if self.state == "refused":
            self.state = "off"                    # its reason has gone (checked just above)
        if self.state == "disengaged":
            if silence < REENGAGE_SILENCE_S:
                return [], self._every_nth()     # Flight Centre hasn't heard yet
            self.state = "off"                    # a new engagement
        not_ready = self._not_ready(sim)
        if self.state in ("off", "standby"):
            if not_ready:
                return [], self._set_state("standby", not_ready) or self._every_nth()
            statuses = self._set_state("engaged", None)
            self._baseline(sim, now)
        elif self.state == "engaged":
            override = self._override(sim, now)
            if override:
                return [], self._set_state("disengaged", override)
        actions = self._steer(msg, sim, now)
        return actions, statuses or self._every_nth()

    def _every_nth(self) -> list:
        return [self.status()] if self.count % STATUS_EVERY == 0 else []

    def tick(self, sim: dict):
        """Check for timeouts and pilot overrides (call about twice a second)."""
        now = self.clock()
        self.ap_engaged = any(self._lit(sim, f"McpPshCmd{s}") for s in "LCR")
        if self.state in ("standby", "engaged") and self.last_steer_at is not None and \
                now - self.last_steer_at > self.valid_for:
            self.timed_out = True
            return [], self._set_state("disengaged", "NO GUIDANCE")
        if self.state == "engaged":
            override = self._override(sim, now)
            if override:
                return [], self._set_state("disengaged", override)
        return [], []

    def on_connection_lost(self):
        """Stop steering because the /ws/push connection dropped (as NO GUIDANCE)."""
        if self.state in ("standby", "engaged"):
            self.timed_out = True
            self._set_state("disengaged", "NO GUIDANCE")
        self.last_seq = None    # a new connection may start seq from anywhere

    # ------------------------------------------------------------ overrides

    def _baseline(self, sim: dict, now: float):
        """Start watching the MCP windows from what they show now."""
        self.expect = {name: (sim.get(name), sim.get(name), now, sim.get(name))
                       for name in ("mcp_hdg", "mcp_spd", "mcp_alt")}
        self.vs_pressed_at = self.alt_pressed_at = -1e9
        self.expect["mcp_vs"] = (sim.get("mcp_vs"), sim.get("mcp_vs"), now, sim.get("mcp_vs"))

    def _expect_turn(self, name: str, old, target, now: float):
        lo, hi = (old, target) if old <= target else (target, old)
        self.expect[name] = (lo, hi, now + TURN_GRACE_S, target)

    def _override(self, sim: dict, now: float) -> Optional[str]:  # pylint: disable=too-many-return-statements,too-many-branches
        """Return why steering must stop (a change we didn't make), or None."""
        if not any(self._lit(sim, f"McpPshCmd{s}") for s in "LCR"):
            return "AUTOPILOT DISCONNECTED"
        if not self._cmd_c_only(sim) or sim.get("thr") != AFDS_SPD or \
                sim.get("roll") != AFDS_HDG_SEL or sim.get("bank_limit") != 0 or \
                sim.get("flap_lever") not in (0, None):
            return "PILOT OVERRIDE"
        pitch = sim.get("pitch")
        if pitch not in (AFDS_ALT, AFDS_VS):
            return "PILOT OVERRIDE"
        # ALT -> V/S only when we pressed it; V/S -> ALT is PSX capturing
        if pitch == AFDS_VS and self.expect.get("pitch") == AFDS_ALT and \
                now - self.vs_pressed_at > VS_GRACE_S:
            return "PILOT OVERRIDE"
        self.expect["pitch"] = pitch
        for name in ("mcp_hdg", "mcp_spd", "mcp_alt"):
            value = sim.get(name)
            lo, hi, until, target = self.expect.get(name, (value, value, now, value))
            if value is None or lo is None:
                self.expect[name] = (value, value, now, value)
                continue
            if name == "mcp_spd" and (value >= MCP_MACH_FROM) != (target >= MCP_MACH_FROM):
                # IAS/Mach changeover (PSX's own or the crew's): harmless, follow it
                self.expect[name] = (value, value, now, value)
                continue
            if now > until:
                lo = hi = target
                self.expect[name] = (lo, hi, until, target)
            if name == "mcp_hdg":
                inside = value == lo if lo == hi else _hdg_between(value, lo, hi, target)
            else:
                inside = lo <= value <= hi
            if not inside:
                return "PILOT OVERRIDE"
        if pitch == AFDS_VS and now - self.vs_pressed_at > VS_GRACE_S:
            value = sim.get("mcp_vs")
            lo, hi, until, target = self.expect.get("mcp_vs", (value, value, now, value))
            if lo is None or now > until:
                lo = hi = target
            if value is not None and target is not None and not lo <= value <= hi:
                return "PILOT OVERRIDE"
        return None

    # ------------------------------------------------------------- steering

    def _steer(self, msg: dict, sim: dict, now: float) -> list:  # pylint: disable=too-many-locals
        cmd = msg.get("command") or {}
        limits = msg.get("limits") or {}
        actions = []
        try:
            cmd_hdg, cmd_gs, cmd_alt = (float(cmd["heading_deg"]), float(cmd["speed_kt"]),
                                        float(cmd["altitude_ft"]))
        except (KeyError, TypeError, ValueError):
            return actions
        # Heading
        if sim.get("heading_true") is not None and sim.get("mcp_hdg") is not None:
            # The track from positions lags the heading in a turn: drift only wings level
            level = abs(sim.get("bank_deg") or 0.0) < LEVEL_BANK_DEG
            if sim.get("track_true") is not None and level:
                self.drift = wrap180(sim["track_true"] - sim["heading_true"])
            want = mcp_heading(cmd_hdg, sim["heading_true"], sim["heading_true"] + self.drift,
                               sim.get("magvar") or 0.0, bool(sim.get("hdg_true_ref")))
            diff = int(round(wrap180(want - sim["mcp_hdg"])))
            if abs(diff) >= HDG_STEP_DEG:
                actions += turns("McpTurnHdg", diff)
                self._expect_turn("mcp_hdg", sim["mcp_hdg"], want, now)
        # Speed
        raw = sim.get("mcp_spd")
        if None not in (raw, sim.get("gs"), sim.get("tas"), sim.get("ias"), sim.get("oat_c")) \
                and raw <= 950:
            mach_mode = raw >= MCP_MACH_FROM
            want = mcp_speed(cmd_gs, sim["gs"], sim["tas"], sim["ias"], sim["oat_c"], mach_mode,
                             float(limits.get("max_ias_kt") or 330),
                             float(limits.get("max_mach") or 0.85))
            if abs(want - raw) >= (SPD_STEP_MACH if mach_mode else SPD_STEP_KT):
                actions += turns("McpTurnSpd", want - raw)
                self._expect_turn("mcp_spd", raw, want, now)
        # Altitude: the window, then V/S towards it or ALT HOLD near it
        target = int(round(cmd_alt / 100.0))
        if sim.get("mcp_alt") is not None and target != sim["mcp_alt"]:
            actions += turns("McpTurnAlt", target - sim["mcp_alt"])
            self._expect_turn("mcp_alt", sim["mcp_alt"], target, now)
        actions += self._vertical(cmd_alt, sim, now, limits)
        return actions

    def _vertical(self, cmd_alt: float, sim: dict, now: float,  # pylint: disable=too-many-return-statements
                  limits: dict) -> list:
        """Return V/S / ALT HOLD actions for the commanded altitude."""
        alt = sim.get("baro_alt_ft")
        if alt is None:
            alt = sim.get("alt_ft")
        if alt is None:
            return []
        err = cmd_alt - alt
        rate = max(1, min(60, int(round(float(limits.get("vs_fpm") or 1000) / 100.0))))
        pitch = sim.get("pitch")
        if pitch == AFDS_ALT:
            # Afds field 7: the altitude ALT HOLD was pressed at, or -9999 (alt_held_ft
            # None) while ALT captures/holds the MCP altitude: leave that to PSX unless
            # the window is moving to a new altitude (ALT doesn't follow the window)
            held = sim.get("alt_held_ft")
            if held is None:
                off = 0.0 if sim.get("mcp_alt") == int(round(cmd_alt / 100.0)) else abs(err)
            else:
                off = abs(cmd_alt - held)
            if off > ALT_BAND_FT and now - self.vs_pressed_at > VS_GRACE_S:
                self.vs_pressed_at = now
                self.expect["mcp_vs"] = (None, None, now + VS_GRACE_S, None)
                return [("press", "McpPshVs"), ("vs", rate if err > 0 else -rate)]
            return []
        if pitch == AFDS_VS:
            if abs(err) <= ALT_BAND_FT:
                if now - self.alt_pressed_at > VS_GRACE_S:
                    self.alt_pressed_at = now
                    return [("press", "McpPshAltHld")]
                return []
            want = rate if err > 0 else -rate
            cur = sim.get("mcp_vs")
            if now - self.vs_pressed_at > VS_GRACE_S and cur is not None and cur != want:
                self.expect["mcp_vs"] = (min(cur, want), max(cur, want), now + TURN_GRACE_S, want)
                return turns("McpTurnVs", want - cur)
        return []

    def set_vs_expected(self, current: int, want: int):
        """Note the V/S knob turn from current to want made after pressing V/S."""
        now = self.clock()
        self.expect["mcp_vs"] = (min(current, want), max(current, want), now + TURN_GRACE_S, want)


def _hdg_between(value: int, lo: int, hi: int, target: int) -> bool:
    """Return True if a heading is on the short arc from where a turn started to its target."""
    start = hi if target == lo else lo
    span = wrap180(target - start)
    part = wrap180(value - start)
    return (0 <= part <= span) if span >= 0 else (span <= part <= 0)


def turns(knob: str, clicks: int) -> list:
    """Split a knob turn into DELTA events of at most MAX_CLICKS clicks."""
    out = []
    while clicks:
        step = max(-MAX_CLICKS, min(MAX_CLICKS, clicks))
        out.append(("turn", knob, step))
        clicks -= step
    return out


# ---------------------------------------------------------------- tests

def _sim(**kw):
    base = {"McpPshCmdC": 8, "McpPshCmdL": 0, "McpPshCmdR": 0, "thr": AFDS_SPD,
            "roll": AFDS_HDG_SEL, "pitch": AFDS_ALT, "bank_limit": 0, "flap_lever": 0,
            "on_ground": False, "alt_ft": 35000.0, "baro_alt_ft": 35000.0, "elev_ft": 100.0,
            "alt_held_ft": 35000.0, "heading_true": 90.0, "track_true": 90.0, "magvar": 0.0,
            "hdg_true_ref": True, "mcp_hdg": 90, "mcp_spd": 280, "mcp_alt": 350, "mcp_vs": 0,
            "gs": 480.0, "tas": 480.0, "ias": 280.0, "oat_c": -54.0}
    base.update(kw)
    return base


def _steer(seq, hdg=90.0, spd=480.0, alt=35000.0, engaged=True):
    return {"type": "formation_steer", "engaged": engaged, "seq": seq, "valid_for_s": 10,
            "command": {"heading_deg": hdg, "speed_kt": spd, "altitude_ft": alt},
            "limits": {"max_ias_kt": 330, "max_mach": 0.85, "vs_fpm": 1000}}


class _Clock:  # pylint: disable=too-few-public-methods
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class TestFormation(unittest.TestCase):
    """Formation autopilot decisions."""

    def make(self, enabled=True):
        """Return (formation, clock)."""
        clock = _Clock()
        return Formation(enabled, clock), clock

    def test_not_enabled(self):
        """Without the opt-in every command is refused."""
        f, _ = self.make(False)
        actions, statuses = f.on_steer(_steer(1), _sim())
        self.assertEqual(actions, [])
        self.assertEqual((statuses[0]["state"], statuses[0]["reason"]), ("refused", "NOT ENABLED"))

    def test_standby_until_cmd_c(self):
        """CMD L or no autopilot: standby; CMD C: engaged and steering."""
        f, clock = self.make()
        _, st = f.on_steer(_steer(1), _sim(McpPshCmdC=0))
        self.assertEqual((st[0]["state"], st[0]["reason"]), ("standby", "AUTOPILOT NOT ENGAGED"))
        clock.t += 3
        _, st = f.on_steer(_steer(2), _sim(McpPshCmdC=0, McpPshCmdL=8))
        self.assertEqual(st[0]["reason"], "CMD C ONLY")
        clock.t += 3
        actions, st = f.on_steer(_steer(3, hdg=100), _sim())
        self.assertEqual(st[0]["state"], "engaged")
        self.assertIn(("turn", "McpTurnHdg", 10), actions)

    def test_modes_required(self):
        """FLCH, LNAV, A/T not SPD, bank limit not AUTO: standby listing them."""
        f, _ = self.make()
        _, st = f.on_steer(_steer(1), _sim(pitch=11, roll=4, thr=20, bank_limit=2))
        self.assertEqual(st[0]["state"], "standby")
        for word in ("A/T NOT SPD", "NOT HDG SEL", "NOT ALT OR V/S", "BANK LIMIT NOT AUTO"):
            self.assertIn(word, st[0]["reason"])

    def test_no_psx_data_is_standby(self):
        """Before PSX has sent anything: standby, not refused; steering once data arrives."""
        f, clock = self.make()
        _, st = f.on_steer(_steer(1), _sim(on_ground=None, thr=None))
        self.assertEqual((st[0]["state"], st[0]["reason"]), ("standby", "NO DATA FROM PSX YET"))
        clock.t += 3
        _, st = f.on_steer(_steer(2), _sim())
        self.assertEqual(st[0]["state"], "engaged")

    def test_refused_low_and_ground(self):
        """Below 1,000 ft AGL or on the ground: refused."""
        f, _ = self.make()
        _, st = f.on_steer(_steer(1), _sim(alt_ft=900.0, elev_ft=0.0))
        self.assertEqual(st[0]["reason"], "TOO LOW")
        f, _ = self.make()
        _, st = f.on_steer(_steer(1), _sim(on_ground=True))
        self.assertEqual(st[0]["reason"], "ON THE GROUND")

    def test_climb_in_vs(self):
        """A higher altitude: ALT window up, V/S pressed at +1000 ft/min."""
        f, _ = self.make()
        actions, _ = f.on_steer(_steer(1, alt=37000.0), _sim())
        self.assertIn(("turn", "McpTurnAlt", 16), actions)
        self.assertIn(("turn", "McpTurnAlt", 4), actions)
        self.assertEqual(actions[-2:], [("press", "McpPshVs"), ("vs", 10)])

    def test_capture_left_to_psx(self):
        """ALT capturing/holding the MCP altitude (field 7 -9999): no V/S even 150 ft off."""
        f, _ = self.make()
        actions, _ = f.on_steer(_steer(1, alt=35000.0),
                                _sim(alt_held_ft=None, baro_alt_ft=35150.0))
        self.assertEqual(actions, [])
        f, _ = self.make()
        actions, _ = f.on_steer(_steer(1, alt=36000.0), _sim(alt_held_ft=None))
        self.assertEqual(actions[-2:], [("press", "McpPshVs"), ("vs", 10)])

    def test_drift_only_wings_level(self):
        """In a turn the last wings-level drift is used, not the lagging track."""
        f, clock = self.make()
        actions, _ = f.on_steer(_steer(1, hdg=100), _sim(heading_true=95.0, track_true=100.0))
        self.assertIn(("turn", "McpTurnHdg", 5), actions)      # drift +5: MCP 95
        clock.t += 3
        actions, _ = f.on_steer(_steer(2, hdg=100), _sim(heading_true=80.0, track_true=70.0,
                                                         bank_deg=20.0, mcp_hdg=95))
        self.assertEqual(actions, [])                           # still 95, not 110

    def test_alt_hold_near_target(self):
        """In V/S within 100 ft of the command: ALT HOLD."""
        f, clock = self.make()
        f.on_steer(_steer(1, alt=35000.0), _sim(pitch=AFDS_VS, mcp_vs=10, baro_alt_ft=34000.0))
        clock.t += 3
        actions, _ = f.on_steer(_steer(2, alt=35000.0),
                                _sim(pitch=AFDS_VS, mcp_vs=10, baro_alt_ft=34950.0))
        self.assertIn(("press", "McpPshAltHld"), actions)

    def test_speed_ground_speed_and_limits(self):
        """Ground speed to IAS; never above 330 kt / M0.85."""
        self.assertEqual(mcp_speed(485, 480, 480, 280, -54, False, 330, 0.85), 283)
        self.assertEqual(mcp_speed(700, 480, 480, 280, -54, False, 330, 0.85), 286)  # M0.85 caps
        self.assertEqual(mcp_speed(700, 480, 400, 300, 0, False, 330, 0.85), 330)    # 330 caps low
        self.assertEqual(mcp_speed(100, 480, 480, 280, -54, False, 330, 0.85), 240)  # floor
        mach = mcp_speed(500, 480, 480, 280, -54, True, 330, 0.85)
        self.assertEqual(mach, 850)      # M0.85 cap in Mach mode

    def test_heading_drift_and_variation(self):
        """Track to MCP heading: minus drift, minus variation unless TRUE."""
        self.assertEqual(mcp_heading(90, 85, 90, 0, True), 85)
        self.assertEqual(mcp_heading(90, 90, 90, 6.6, False), 83)
        self.assertEqual(mcp_heading(2, 358, 3, 0, True), 357)

    def test_pilot_override(self):
        """The crew turning a knob we didn't, or FLCH, or AP off: disengaged."""
        f, clock = self.make()
        f.on_steer(_steer(1), _sim())
        clock.t += 1
        _, st = f.tick(_sim(mcp_hdg=120))
        self.assertEqual((st[0]["state"], st[0]["reason"]), ("disengaged", "PILOT OVERRIDE"))
        f, clock = self.make()
        f.on_steer(_steer(1), _sim())
        _, st = f.tick(_sim(McpPshCmdC=0))
        self.assertEqual(st[0]["reason"], "AUTOPILOT DISCONNECTED")
        f, clock = self.make()
        f.on_steer(_steer(1), _sim())
        _, st = f.tick(_sim(pitch=11))
        self.assertEqual(st[0]["reason"], "PILOT OVERRIDE")

    def test_own_turns_are_not_overrides(self):
        """Our own heading turn passing through intermediate values is fine."""
        f, clock = self.make()
        f.on_steer(_steer(1, hdg=110), _sim())
        clock.t += 0.5
        self.assertEqual(f.tick(_sim(mcp_hdg=100))[1], [])
        clock.t += 0.5
        self.assertEqual(f.tick(_sim(mcp_hdg=110))[1], [])
        clock.t += 7      # past the turn's grace: must now show the target
        self.assertEqual(f.tick(_sim(mcp_hdg=110))[1], [])
        clock.t += 0.5
        self.assertEqual(f.tick(_sim(mcp_hdg=105))[1][0]["reason"], "PILOT OVERRIDE")

    def test_capture_is_not_override_but_crew_vs_is(self):
        """V/S -> ALT (capture) is fine; ALT -> V/S without our press is an override."""
        f, clock = self.make()
        f.on_steer(_steer(1), _sim(pitch=AFDS_VS, mcp_vs=10))
        clock.t += 5
        self.assertEqual(f.tick(_sim(pitch=AFDS_ALT, mcp_vs=10))[1], [])
        clock.t += 5
        _, st = f.tick(_sim(pitch=AFDS_VS, mcp_vs=10))
        self.assertEqual(st[0]["reason"], "PILOT OVERRIDE")

    def test_timeout_and_reengage(self):
        """No command for 10 s: NO GUIDANCE; a new engagement needs a pause first."""
        f, clock = self.make()
        f.on_steer(_steer(1), _sim())
        clock.t += 11
        _, st = f.tick(_sim())
        self.assertEqual((st[0]["state"], st[0]["reason"]), ("disengaged", "NO GUIDANCE"))
        self.assertTrue(f.timed_out)
        clock.t += 1
        actions, st = f.on_steer(_steer(2), _sim())   # silence was 12 s: a new engagement
        self.assertEqual(f.state, "engaged")
        f, clock = self.make()
        f.on_steer(_steer(1), _sim())
        clock.t += 1
        f.tick(_sim(mcp_spd=300))                      # override
        clock.t += 3
        actions, st = f.on_steer(_steer(2), _sim(mcp_spd=300))
        self.assertEqual((f.state, actions), ("disengaged", []))

    def test_seq_and_disengage(self):
        """Old seq ignored; engaged false stops at once."""
        f, clock = self.make()
        f.on_steer(_steer(5), _sim())
        self.assertEqual(f.on_steer(_steer(5, hdg=200), _sim()), ([], []))
        clock.t += 3
        _, st = f.on_steer(_steer(6, engaged=False), _sim())
        self.assertEqual((st[0]["state"], st[0]["reason"]),
                         ("disengaged", "FLIGHT CENTRE DISENGAGED"))

    def test_turn_chunks(self):
        """Knob turns are split into DELTA events of at most 16 clicks."""
        self.assertEqual(turns("McpTurnHdg", -40), [("turn", "McpTurnHdg", -16)] * 2 +
                         [("turn", "McpTurnHdg", -8)])

    def test_track(self):
        """Track from positions: due east."""
        self.assertAlmostEqual(track_from_samples([(0, 59.0, 18.0), (5, 59.0, 18.01)]), 90.0, 0)
        self.assertIsNone(track_from_samples([(0, 59.0, 18.0), (2, 59.0, 18.01)]))


if __name__ == "__main__":
    unittest.main()
