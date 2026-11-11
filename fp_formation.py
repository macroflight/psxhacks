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
# Climb protection: V/S doesn't protect the speed (a heavy aircraft climbing at
# FL350 stalled and crashed on the test sim, 2026-11-11). In a V/S climb with the
# speed this far below the MCP speed: ALT HOLD; no new climb until the speed is
# back within CLIMB_OK_KT, and not for CLIMB_BLOCK_S anyway.
CLIMB_STOP_KT = 10.0
CLIMB_OK_KT = 3.0
CLIMB_BLOCK_S = 60.0


# ---------------------------------------------------------------- performance
# Rough 747-400 numbers, erring on the safe side (Flight Centre's request
# 2026-11-11: a weight/altitude-aware minimum speed; Mats: also the highest
# altitude we can still climb to at 1,000 ft/min). PSX doesn't expose its
# amber-band speed, so it's worked out here from the gross weight.
WING_AREA_M2 = 511.0
MIN_SPEED_MARGIN_KT = 5.0
BUFFET_G = 1.3
# Highest altitude for a 300 ft/min climb by weight (t), ISA+10ish, roughly the
# 747-400 maximum altitude chart; we keep 2,000 ft below it for 1,000 ft/min.
MAX_ALT_TABLE = ((220, 41000), (240, 40000), (260, 38500), (280, 37000), (300, 35500),
                 (320, 34000), (340, 33000), (360, 32000), (380, 31000), (400, 30000))
MAX_ALT_MARGIN_FT = 2000
NO_SPEED_KT = 999.0          # min_ias_for() when there's no safe speed at all


def isa_pressure(alt_ft: float) -> float:
    """Return the ISA static pressure (Pa) at a pressure altitude."""
    alt_m = alt_ft * 0.3048
    if alt_m <= 11000.0:
        return 101325.0 * (1.0 - 2.25577e-5 * alt_m) ** 5.25588
    return 22632.0 * math.exp(-(alt_m - 11000.0) / 6341.62)


def cas_from_mach(mach: float, pressure: float) -> float:
    """Return the calibrated airspeed (kt) for a Mach number at a static pressure."""
    qc = pressure * ((1.0 + 0.2 * mach * mach) ** 3.5 - 1.0)
    return 661.47 * math.sqrt(5.0 * ((qc / 101325.0 + 1.0) ** (2.0 / 7.0) - 1.0))


def mach_from_cas(cas: float, pressure: float) -> float:
    """Return the Mach number for a calibrated airspeed (kt) at a static pressure."""
    qc = 101325.0 * ((1.0 + 0.2 * (cas / 661.47) ** 2) ** 3.5 - 1.0)
    return math.sqrt(5.0 * ((qc / pressure + 1.0) ** (2.0 / 7.0) - 1.0))


def _buffet_cl(mach: float) -> float:
    """Return a 747-400 1.3 g buffet-onset lift coefficient (flaps up) for a Mach number.

    Calibrated to: at 350 t the low-speed buffet margin is about M0.78-0.80 at
    FL350 (Flight Centre's note) and the 1.3 g ceiling about FL375 at M0.85.
    """
    if mach <= 0.5:
        return 1.2
    if mach <= 0.78:
        return 1.2 - (mach - 0.5) / 0.28 * 0.34       # 0.86 at M0.78
    if mach <= 0.86:
        return 0.86 - (mach - 0.78) / 0.08 * 0.06     # 0.80 at M0.86
    return max(0.3, 0.80 - (mach - 0.86) * 3.0)


def min_ias_for(gw_t: float, press_alt_ft: float) -> float:
    """Return the minimum IAS (kt) to fly for a weight and pressure altitude.

    The higher of 240 kt, VREF30 + 80 kt and the 1.3 g buffet-onset speed, plus a
    margin; NO_SPEED_KT when no speed has a 1.3 g margin.
    """
    vref30 = 150.0 * math.sqrt(max(gw_t, 150.0) / 280.0)
    pressure = isa_pressure(press_alt_ft)
    k = BUFFET_G * gw_t * 1000.0 * 9.80665 / (0.7 * pressure * WING_AREA_M2)
    mach = 0.5
    for _ in range(40):      # M^2 = n W / (0.7 p S CL(M)), CL falling with Mach
        mach = math.sqrt(k / _buffet_cl(mach))
        if mach > 0.9:
            return NO_SPEED_KT          # no 1.3 g margin at any speed: too heavy/high
    buffet = cas_from_mach(mach, pressure)
    return max(MIN_IAS_KT, vref30 + 80.0, buffet) + MIN_SPEED_MARGIN_KT


def max_ias_here(press_alt_ft: float, max_ias_kt: float, max_mach: float) -> float:
    """Return the highest IAS (kt) allowed here: formation limits and VMO/MMO minus margins."""
    pressure = isa_pressure(press_alt_ft)
    mach_cap = min(max_mach, MMO - MMO_MARGIN)
    return min(max_ias_kt, VMO_KT - VMO_MARGIN_KT, cas_from_mach(mach_cap, pressure))


def max_altitude(gw_t: float) -> int:
    """Return the highest altitude (ft) we still climb to at 1,000 ft/min at this weight."""
    table = MAX_ALT_TABLE
    if gw_t <= table[0][0]:
        alt = table[0][1]
    elif gw_t >= table[-1][0]:
        alt = table[-1][1]
    else:
        for (w0, a0), (w1, a1) in zip(table, table[1:]):
            if w0 <= gw_t <= w1:
                alt = a0 + (a1 - a0) * (gw_t - w0) / (w1 - w0)
                break
    return int((alt - MAX_ALT_MARGIN_FT) // 100 * 100)


def gross_weight_t(eicas_grwt: Optional[int], zfw_lb: Optional[int]) -> Optional[float]:
    """Return the gross weight in tonnes, or None.

    From EicasGrWt (0.1 t or 0.1 klb, as the EICAS shows it) and TrueZfw (lb):
    whichever unit doesn't put it below the ZFW (kg if both do).
    """
    if not eicas_grwt:
        return None
    as_kg = eicas_grwt / 10.0
    as_lb = eicas_grwt * 100.0 / 2204.62
    if zfw_lb and as_lb * 1000.0 < zfw_lb / 2204.62 * 1000.0 - 1:
        return as_kg
    return as_kg if eicas_grwt <= 4000 else as_lb


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
              mach_mode: bool, max_ias: float, max_mach: float,
              floor_ias: float = MIN_IAS_KT) -> int:
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
    floor_ratio = min(floor_ias, max_ias) / max(ias, 1.0)
    ratio = max(ratio, floor_ratio)
    # The minimum rounds up and the maximum down
    if mach_mode:
        window = max(int(round(mach * ratio * 1000)), math.ceil(mach * floor_ratio * 1000 - 1e-6))
        return min(window, int(max_mach * 1000 + 1e-6))
    window = max(int(round(ias * ratio)), math.ceil(ias * floor_ratio - 1e-6))
    return min(window, int(max_ias + 1e-6))


def over_limit(window: int, max_ias: float, max_mach: float) -> bool:
    """Return True if an McpWdoSpd value is above the formation limits."""
    if window >= MCP_MACH_FROM:
        return window > int(max_mach * 1000 + 1e-6)
    return window > int(max_ias + 1e-6)


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
        self.climb_blocked_until = -1e9   # climb protection (CLIMB_*)
        # Limits we fly to now, reported in formation_status (Flight Centre 2026-11-11)
        self.limits = {"min_ias_kt": None, "max_ias_kt": None, "speed_limited": None,
                       "max_alt_ft": None, "altitude_limited": None}
        self.events: list = []      # log lines for frankenpush

    # ------------------------------------------------------------ helpers

    def status(self) -> dict:
        """Return a formation_status message for the current state."""
        return {"type": "formation_status", "protocol_version": 1,
                "formation_version": FORMATION_VERSION, "state": self.state,
                "reason": self.reason, "seq": self.last_seq, "ap_engaged": self.ap_engaged,
                **self.limits}

    def _set_state(self, state: str, reason: Optional[str], detail: Optional[str] = None) -> list:
        if (state, reason) == (self.state, self.reason):
            return []
        self.events.append(f"formation autopilot: {self.state} -> {state}"
                           f"{f' ({reason})' if reason else ''}{f': {detail}' if detail else ''}")
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

    def _refusal(self, sim: dict) -> Optional[tuple]:
        """Return (reason, detail for the log) if we won't steer at all, or None."""
        if not self.enabled:
            return "NOT ENABLED", None
        if sim.get("on_ground") is True:
            return "ON THE GROUND", None
        alt, elev = sim.get("alt_ft"), sim.get("elev_ft")
        if alt is not None and elev is not None and alt - elev < MIN_AGL_FT:
            return "TOO LOW", (f"{alt - elev:.0f} ft above the terrain (altitude {alt:.0f} ft, "
                               f"terrain {elev:.0f} ft)")
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
            statuses = self._set_state("refused", *refusal)
            if not statuses and silence >= REENGAGE_SILENCE_S:
                # A new engagement refused for the same reason: say so (the state didn't change)
                detail = f": {refusal[1]}" if refusal[1] else ""
                self.events.append("formation autopilot: new engagement refused "
                                   f"({refusal[0]}){detail}")
                statuses = [self.status()]
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
                return [], self._set_state("disengaged", *override)
        flags = (self.limits["speed_limited"], self.limits["altitude_limited"])
        actions = self._steer(msg, sim, now)
        if statuses:
            statuses[-1] = self.status()     # with the limits this command worked out
        elif flags != (self.limits["speed_limited"], self.limits["altitude_limited"]):
            statuses = [self.status()]
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
                return [], self._set_state("disengaged", *override)
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
                       for name in ("mcp_hdg", "mcp_spd", "mcp_alt", "mcp_vs")}
        self.vs_pressed_at = self.alt_pressed_at = -1e9
        self.climb_blocked_until = -1e9

    def _turning(self, name: str, now: float) -> bool:
        exp = self.expect.get(name)
        return exp is not None and exp[3] is not None and now <= exp[2]

    def _base(self, name: str, sim: dict, now: float):
        """Return where a window is going: our turn's target while one is in progress.

        A new command arriving while the knob clicks of the last one are still being
        sent must start from that target, not from the half-turned window (that
        double counted and overshot: a false PILOT OVERRIDE on the first live test).
        """
        if self._turning(name, now):
            target = self.expect[name][3]
            return target % 360 if name == "mcp_hdg" else target
        return sim.get(name)

    def _expect_turn(self, name: str, actual, target, now: float):
        """Expect the window anywhere between what it shows, an unfinished turn's target and target.

        Headings are kept unwrapped around the actual value (see _inside()).
        """
        points = [actual, target]
        if self._turning(name, now):
            points.append(self.expect[name][3])
        if name == "mcp_hdg":
            points = [actual + wrap180(p - actual) for p in points]
            target = points[1]
        self.expect[name] = (min(points), max(points), now + TURN_GRACE_S, target)

    @staticmethod
    def _inside(name: str, value, lo, hi) -> bool:
        if name == "mcp_hdg":
            mid = (lo + hi) / 2.0
            value = mid + wrap180(value - mid)
        return lo - 1e-6 <= value <= hi + 1e-6

    def _override(self, sim: dict, now: float) -> Optional[tuple]:  # pylint: disable=too-many-return-statements,too-many-branches
        """Return (reason, detail) if steering must stop (a change we didn't make), or None."""
        if not any(self._lit(sim, f"McpPshCmd{s}") for s in "LCR"):
            return "AUTOPILOT DISCONNECTED", None
        if not self._cmd_c_only(sim):
            return "PILOT OVERRIDE", "another autopilot engaged"
        for key, want, text in (("thr", AFDS_SPD, "A/T mode"), ("roll", AFDS_HDG_SEL, "roll mode")):
            if sim.get(key) == -want:
                return "AFDS FAULT", f"{text} {sim.get(key)}"
            if sim.get(key) != want:
                return "PILOT OVERRIDE", f"{text} {sim.get(key)}"
        if sim.get("bank_limit") != 0:
            return "PILOT OVERRIDE", f"bank limit {sim.get('bank_limit')}"
        if sim.get("flap_lever") not in (0, None):
            return "PILOT OVERRIDE", f"flap lever {sim.get('flap_lever')}"
        pitch = sim.get("pitch")
        if pitch in (-AFDS_ALT, -AFDS_VS):
            return "AFDS FAULT", f"pitch mode {pitch}"
        if pitch not in (AFDS_ALT, AFDS_VS):
            return "PILOT OVERRIDE", f"pitch mode {pitch}"
        # ALT -> V/S only when we pressed it; V/S -> ALT is PSX capturing (or our ALT HOLD)
        if pitch == AFDS_VS and self.expect.get("pitch") == AFDS_ALT and \
                now - self.vs_pressed_at > VS_GRACE_S:
            return "PILOT OVERRIDE", "V/S selected"
        self.expect["pitch"] = pitch
        names = ["mcp_hdg", "mcp_spd", "mcp_alt"]
        if pitch == AFDS_VS and now - self.vs_pressed_at > VS_GRACE_S:
            names.append("mcp_vs")
        for name in names:
            value = sim.get(name)
            lo, hi, until, target = self.expect.get(name, (value, value, now, value))
            if value is None or target is None or lo is None:
                self.expect[name] = (value, value, now, value)
                continue
            if name == "mcp_spd" and (value >= MCP_MACH_FROM) != (target >= MCP_MACH_FROM):
                # IAS/Mach changeover (PSX's own or the crew's): harmless, follow it
                self.expect[name] = (value, value, now, value)
                continue
            if now > until:
                lo = hi = target
                self.expect[name] = (lo, hi, until, target)
            if not self._inside(name, value, lo, hi):
                shown = f"{target % 360:g}" if name == "mcp_hdg" else f"{target:g}"
                return "PILOT OVERRIDE", (f"{name.upper().replace('_', ' ')} {value}, expected "
                                          f"{shown}" + ("" if lo == hi else
                                                        f" (between {lo:g} and {hi:g})"))
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
            diff = int(round(wrap180(want - self._base("mcp_hdg", sim, now))))
            if abs(diff) >= HDG_STEP_DEG:
                actions += turns("McpTurnHdg", diff)
                self._expect_turn("mcp_hdg", sim["mcp_hdg"], want, now)
        # Speed
        raw = self._base("mcp_spd", sim, now)
        if None not in (raw, sim.get("gs"), sim.get("tas"), sim.get("ias"), sim.get("oat_c")) \
                and raw <= 950:
            mach_mode = raw >= MCP_MACH_FROM
            max_ias = float(limits.get("max_ias_kt") or 330)
            max_mach = float(limits.get("max_mach") or 0.85)
            press = sim.get("press_alt_ft") or sim.get("baro_alt_ft") or sim.get("alt_ft") or 0.0
            top = max_ias_here(press, max_ias, max_mach)
            floor = min_ias_for(sim["gw_t"], press) if sim.get("gw_t") else MIN_IAS_KT
            floor = min(floor, top)          # no safe speed at all: the top, and say "min"
            wanted = sim["ias"] * max(sim["tas"] + (cmd_gs - sim["gs"]), 1.0) / max(sim["tas"], 1.0)
            self.limits.update(min_ias_kt=int(math.ceil(floor)), max_ias_kt=int(top),
                               speed_limited="min" if wanted < floor else
                               "max" if wanted > top else None)
            want = mcp_speed(cmd_gs, sim["gs"], sim["tas"], sim["ias"], sim["oat_c"], mach_mode,
                             min(max_ias, top), max_mach, floor)
            # Small changes wait for the step, but a window over the limits is always fixed
            if abs(want - raw) >= (SPD_STEP_MACH if mach_mode else SPD_STEP_KT) or \
                    (want != raw and over_limit(raw, max_ias, max_mach)):
                actions += turns("McpTurnSpd", want - raw)
                self._expect_turn("mcp_spd", sim["mcp_spd"], want, now)
        # Altitude: the window, then V/S towards it or ALT HOLD near it
        cmd_alt = self._cap_altitude(cmd_alt, sim)
        target = int(round(cmd_alt / 100.0))
        base = self._base("mcp_alt", sim, now)
        if base is not None and target != base:
            actions += turns("McpTurnAlt", target - base)
            self._expect_turn("mcp_alt", sim["mcp_alt"], target, now)
        actions += self._vertical(cmd_alt, sim, now, limits)
        return actions

    def _cap_altitude(self, cmd_alt: float, sim: dict) -> float:
        """Return the altitude to fly: never above max_altitude() for our weight.

        Above it already, hold where we are rather than climb (never descend for it).
        """
        if not sim.get("gw_t"):
            self.limits.update(max_alt_ft=None, altitude_limited=None)
            return cmd_alt
        top = max_altitude(sim["gw_t"])
        self.limits["max_alt_ft"] = top
        alt = sim.get("baro_alt_ft") or sim.get("alt_ft") or 0.0
        if cmd_alt <= top or cmd_alt <= alt:
            self.limits["altitude_limited"] = None
            return cmd_alt
        self.limits["altitude_limited"] = "max"
        return float(max(top, int(round(alt / 100.0)) * 100))

    def _vertical(self, cmd_alt: float, sim: dict, now: float,  # pylint: disable=too-many-return-statements,too-many-branches
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
        deficit = speed_deficit(sim)
        if deficit is not None and deficit > CLIMB_OK_KT:
            self.climb_blocked_until = max(self.climb_blocked_until, now)
        climbing_ok = now > self.climb_blocked_until and (deficit is None or deficit <= CLIMB_OK_KT)
        if pitch == AFDS_VS and (sim.get("mcp_vs") or 0) > 0 and deficit is not None and \
                deficit > CLIMB_STOP_KT:
            # Climb protection: level off, no climb again for a while
            self.climb_blocked_until = now + CLIMB_BLOCK_S
            if now - self.alt_pressed_at > VS_GRACE_S:
                self.alt_pressed_at = now
                self.events.append(f"formation autopilot: climb stopped, speed {deficit:.0f} kt "
                                   "below the MCP speed: ALT HOLD")
                return [("press", "McpPshAltHld")]
            return []
        if err > ALT_BAND_FT and not climbing_ok:
            return []           # wants to climb, but the speed isn't there
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
            cur = self._base("mcp_vs", sim, now)
            if now - self.vs_pressed_at > VS_GRACE_S and cur is not None and cur != want:
                self._expect_turn("mcp_vs", sim.get("mcp_vs"), want, now)
                return turns("McpTurnVs", want - cur)
        return []

    def set_vs_expected(self, current: int, want: int):
        """Note the V/S knob turn from current to want made after pressing V/S."""
        now = self.clock()
        self.expect["mcp_vs"] = (min(current, want), max(current, want), now + TURN_GRACE_S, want)


def speed_deficit(sim: dict) -> Optional[float]:
    """Return how far (kt IAS) the speed is below the MCP speed, or None if unknown."""
    raw, ias, tas, oat = sim.get("mcp_spd"), sim.get("ias"), sim.get("tas"), sim.get("oat_c")
    if None in (raw, ias) or raw > 950:
        return None
    if raw >= MCP_MACH_FROM:
        if None in (tas, oat):
            return None
        mach = mach_from_tas(tas, oat)
        return ias * (raw / 1000.0) / max(mach, 0.01) - ias
    return raw - ias


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


class TestFormation(unittest.TestCase):  # pylint: disable=too-many-public-methods
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

    def test_refusal_logged_and_cleared(self):
        """TOO LOW is logged with the heights, again on a new engagement, and clears when gone."""
        f, clock = self.make()
        _, st = f.on_steer(_steer(1), _sim(alt_ft=900.0, elev_ft=100.0))
        self.assertEqual(st[0]["reason"], "TOO LOW")
        self.assertIn("800 ft above the terrain", f.events[-1])
        clock.t += 30
        _, st = f.on_steer(_steer(2), _sim(alt_ft=900.0, elev_ft=100.0))
        self.assertEqual(st[0]["state"], "refused")
        self.assertIn("new engagement refused (TOO LOW)", f.events[-1])
        clock.t += 30
        _, st = f.on_steer(_steer(3), _sim())
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

    def test_mach_limit_exact(self):
        """Never above the limit (M.850, 330 kt), and a window over it is pulled back."""
        self.assertEqual(mcp_speed(700, 400, 480, 280, -40, True, 330, 0.85), 850)
        self.assertEqual(mcp_speed(700, 400, 400, 320, 0, False, 330, 0.85), 330)
        f, _ = self.make()
        actions, _ = f.on_steer(_steer(1, spd=700.0), _sim(mcp_spd=853, gs=480.0, tas=480.0))
        self.assertIn(("turn", "McpTurnSpd", -3), actions)

    def test_heading_drift_and_variation(self):
        """True track to MCP heading: minus drift, minus variation (east +) unless TRUE."""
        self.assertEqual(mcp_heading(90, 85, 90, 0, True), 85)
        self.assertEqual(mcp_heading(90, 90, 90, 6.6, False), 83)
        # Canaries, 4.5 W (MiscFltData -45): true track 042, no wind -> MAG 046.5 -> 46
        self.assertEqual(mcp_heading(42, 42, 42, -4.5, False), 46)
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

    def test_overlapping_commands_dont_double_count(self):
        """A command arriving mid-turn starts from the last target (the 18:44 false override)."""
        f, clock = self.make()
        actions, _ = f.on_steer(_steer(1, alt=37000.0), _sim(pitch=AFDS_VS, mcp_vs=10))
        self.assertIn(("turn", "McpTurnAlt", 4), actions)
        clock.t += 3
        actions, _ = f.on_steer(_steer(2, alt=37000.0), _sim(pitch=AFDS_VS, mcp_vs=10, mcp_alt=360))
        self.assertFalse([a for a in actions if a[1] == "McpTurnAlt"])
        clock.t += 1
        self.assertEqual(f.tick(_sim(pitch=AFDS_VS, mcp_vs=10, mcp_alt=368))[1], [])
        clock.t += 1
        self.assertEqual(f.tick(_sim(pitch=AFDS_VS, mcp_vs=10, mcp_alt=370))[1], [])

    def test_heading_reversal_mid_turn(self):
        """90 -> 110 still turning when 95 comes: the window may pass 110 on its way back."""
        f, clock = self.make()
        f.on_steer(_steer(1, hdg=110), _sim())
        clock.t += 3
        actions, _ = f.on_steer(_steer(2, hdg=95), _sim(mcp_hdg=100))
        self.assertIn(("turn", "McpTurnHdg", -15), actions)
        clock.t += 1
        self.assertEqual(f.tick(_sim(mcp_hdg=110))[1], [])
        clock.t += 1
        self.assertEqual(f.tick(_sim(mcp_hdg=95))[1], [])
        clock.t += 1
        self.assertEqual(f.tick(_sim(mcp_hdg=120))[1][0]["reason"], "PILOT OVERRIDE")
        self.assertIn("MCP HDG 120", f.events[-1])

    def test_climb_protection(self):
        """Climbing in V/S 20 kt slow: ALT HOLD, and no new climb while slow."""
        f, clock = self.make()
        actions, _ = f.on_steer(_steer(1, alt=37000.0),
                                _sim(pitch=AFDS_VS, mcp_vs=10, mcp_alt=370, ias=260.0))
        self.assertEqual(actions[-1], ("press", "McpPshAltHld"))
        clock.t += 3
        actions, _ = f.on_steer(_steer(2, alt=37000.0),
                                _sim(mcp_alt=370, mcp_spd=260, ias=279.0, alt_held_ft=35200.0))
        self.assertFalse([a for a in actions if a[0] != "turn"])   # no V/S: blocked for 60 s
        clock.t += 61
        f.last_steer_at = clock.t - 3                       # (keep the guidance fresh)
        actions, _ = f.on_steer(_steer(3, alt=37000.0),
                                _sim(mcp_alt=370, mcp_spd=279, ias=279.0, alt_held_ft=35200.0))
        self.assertEqual(actions[-2:], [("press", "McpPshVs"), ("vs", 10)])

    def test_afds_fault(self):
        """A negative (fault) pitch mode stops it as AFDS FAULT, not PILOT OVERRIDE."""
        f, _ = self.make()
        f.on_steer(_steer(1), _sim())
        _, st = f.tick(_sim(pitch=-AFDS_VS))
        self.assertEqual(st[0]["reason"], "AFDS FAULT")

    def test_min_speed_heavy_and_high(self):
        """350 t at FL350: the minimum is well above 240 kt and reported as limiting."""
        floor = min_ias_for(350, 35000)
        self.assertTrue(255 < floor < 285, floor)
        self.assertEqual(min_ias_for(250, 15000), 245)            # 240 + margin
        self.assertEqual(min_ias_for(400, 41000), NO_SPEED_KT)     # no safe speed
        f, _ = self.make()
        actions, st = f.on_steer(_steer(1, spd=300.0),
                                 _sim(gw_t=350.0, press_alt_ft=35000.0, mcp_spd=280))
        self.assertEqual(st[0]["speed_limited"], "min")
        self.assertEqual(st[0]["min_ias_kt"], math.ceil(floor))
        self.assertIn(("turn", "McpTurnSpd", math.ceil(floor) - 280), actions)

    def test_max_altitude_cap_replays_the_crash(self):
        """315 t: no climb above FL323; at FL349 (the crash) it holds instead of climbing."""
        self.assertEqual(max_altitude(315), 32300)
        f, _ = self.make()
        actions, st = f.on_steer(_steer(1, alt=37000.0),
                                 _sim(gw_t=315.0, baro_alt_ft=31000.0, alt_held_ft=31000.0,
                                      mcp_alt=310))
        self.assertIn(("turn", "McpTurnAlt", 13), actions)
        self.assertEqual((st[0]["max_alt_ft"], st[0]["altitude_limited"]), (32300, "max"))
        f, _ = self.make()
        actions, st = f.on_steer(_steer(1, alt=35700.0),
                                 _sim(gw_t=315.0, baro_alt_ft=34900.0, alt_held_ft=None,
                                      mcp_alt=349))
        self.assertEqual(actions, [])

    def test_gross_weight_units(self):
        """Gross weight from EicasGrWt in 0.1 t or 0.1 klb: whichever is not below the ZFW."""
        self.assertAlmostEqual(gross_weight_t(3149, 352739), 314.9)
        self.assertAlmostEqual(gross_weight_t(6950, 352739), 315.2, 1)
        self.assertIsNone(gross_weight_t(None, 352739))

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
