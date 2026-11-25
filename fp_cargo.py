"""frankenpush's cargo loading for Flight Centre's Cargo page (opt-in, --allow-cargo-loading).

Flight Centre plans a 747-400F load and simulates the loading pallet by
pallet. After each step it sends cargo_state (the ZFW, the CG and the cargo
doors it wants); frankenpush writes them to PSX. In its telemetry
frankenpush reports what PSX has: weights, CG, fuel per tank, refuelling,
the cargo doors and whether the cargo handling system has power. Interface:
~/to-psxhacks.md 2026-11-24 (Flight Centre psccfc f31c9f7/e9d6037) and
docs/frankenpush.md. This module is the pure logic (unit-tested here:
python3 -m unittest fp_cargo); frankenpush.py executes the writes.

PSX facts (measured on the headless test sim 2026-11-24, PSX 10.195):
- TrueCg (Qi122) is %MAC x 10, the real CG (the autopilot's stabiliser
  trim follows it). TrueZfw (Qi123) is lb; both stick when written, one
  at a time, and PSX updates EicasGrWt and the FMC's ZFW itself.
- TrueCg is the CG with the fuel on board (PSX's only CG variable): PSX
  keeps a hidden zero fuel CG, moves TrueCg with the fuel, and takes a
  written TrueCg as the CG at its current fuel (wrote 200 with no fuel,
  151 t of fuel -> 178, no fuel again -> 200). Flight Centre therefore
  sends the CG for PSX's actual fuel. TrueCg is clamped at 330.
- PSX never tips onto its tail by itself: a reposition keeps a ground pitch
  of 0. But PiBaHeAlTas pitch written about 20 times a second lifts the nose
  (2026-11-25, stack B): the boost feed (what MSFS shows) pivots about the
  main gear and lags, reaching about 12 deg for 13.8 written (the eye
  height rises about 20 ft). After the last write PSX lets the nose down
  in about 1.7 s, dips to -1.5 deg and settles within about 4 s. Writing 0
  at the end makes it bounce back up; just stopping is smoothest.
- DoorOpenBits (Qi180, Aerowinx's Doors class): a bit set = open. With a
  door preset active (DoorComBits Qi179: CLOSE 1, OPEN 2, PARK 4) PSX puts
  the bits back within seconds; with DoorComBits OFF (32) written bits
  stay, door by door. They switch at once: Flight Centre simulates the
  door travel times.
- The cargo handling bus (doors and handling system) has power on the
  ground when an EXT PWR or APU GEN is available but not on the main buses
  (the user): its P1 button shows AVAIL (256), not ON (128).
- FuelQty (Qs438): "d" + tanks in 0.1 lb: mains 1-4, reserves 2/3, centre
  wing, stabiliser, AUX (ER body tank; PSX zeroes it on other variants).
"""
import math
import unittest
from typing import Optional

from fp_formation import gross_weight_t

LB_KG = 0.45359237
DOOR_BITS = {"nose": 1, "side": 256, "lower_fwd": 2048, "lower_aft": 131072, "bulk": 262144,
             "entry_1l": 8}
UNPOWERED_DOORS = ("entry_1l",)        # the entry door needs no cargo handling bus
DOOR_COM_OFF = 32
LIGHT_AVAIL = 256
LIGHT_ON = 128
# CfgPinProg (Qi30) bit for weights in kg (clear: lb); found 2026-11-25 on stack B: clearing
# it turned EicasGrWt 2542 (254.2 t) into 5605 (560.5 klb), no other bit changed the unit
PIN_WEIGHT_KG = 0x100000
POWER_SOURCES = ("ExtPow1", "ExtPow2", "ApuGen1", "ApuGen2")
TANKS = ("M1", "M2", "M3", "M4", "R2", "R3", "CWT", "STAB", "AUX")
ZFW_KG_RANGE = (160000.0, 290000.0)   # TrueZfw's range (352739-639340 lb), rounded inwards
CG_PCT_RANGE = (8.5, 33.0)            # TrueCg's documented range (85-330)
NOT_ALLOWED = "cargo loading not allowed (frankenpush --allow-cargo-loading)"
# The crew's cue to look at Flight Centre (its simulation tipped the aircraft onto its tail, ...):
# PSX's free EICAS warning, red (FreeMsgW, at most 16 characters; PSX lights MASTER WARNING
# and sounds the warning itself; the user 2026-11-24: "RED! A multimillion dollar incident")
WARNING_TEXT = "FLIGHTCENTRE"


def warning(msg: dict, allowed: bool, current: Optional[str]) -> tuple:
    """Return (FreeMsgW value to write or None, log note) for a flightcentre_warning message.

    "active": true shows WARNING_TEXT; false clears it, but only if it's still
    ours (frankenrouter's ROUTER or a WorldFlight alert stays). Only with the option.
    """
    if not allowed:
        return None, NOT_ALLOWED
    active = msg.get("active")
    if not isinstance(active, bool):
        return None, f"flightcentre_warning: active {active!r} ignored (not true/false)"
    if active:
        return (WARNING_TEXT if current != WARNING_TEXT else None), \
            f"EICAS warning {WARNING_TEXT} ({msg.get('reason') or 'no reason given'})"
    if current == WARNING_TEXT:
        return "", f"EICAS warning {WARNING_TEXT} cleared"
    return None, f"EICAS warning {WARNING_TEXT} not showing: nothing to clear"


# The tail tip's pitch-up (flightcentre_warning with reason "tail tip"): written pitch in deg
# (PSX shows about 12 deg, where the 747's tail meets the ground), a smooth rise, a hold, then
# frankenpush stops writing and PSX settles back down by itself (Mats 2026-11-25).
TIP_PITCH_DEG = 13.8
TIP_RISE_S = 3.0
TIP_HOLD_S = 5.0
TIP_STEP_S = 0.05             # 20 writes a second; fewer let PSX sag between writes
TIP_MAX_GS_KT = 1.0           # only when parked


def is_tail_tip(msg: dict) -> bool:
    """Return True for a flightcentre_warning that raises a tail tip."""
    return msg.get("active") is True and "tail tip" in str(msg.get("reason") or "").lower()


def tail_tip_pitch(elapsed_s: float) -> Optional[float]:
    """Return the pitch (deg) to write elapsed_s into the tail tip, None when done."""
    if elapsed_s < 0:
        return 0.0
    if elapsed_s < TIP_RISE_S:
        return TIP_PITCH_DEG * (1 - math.cos(math.pi * elapsed_s / TIP_RISE_S)) / 2
    if elapsed_s < TIP_RISE_S + TIP_HOLD_S:
        return TIP_PITCH_DEG
    return None


def with_pitch(pibahealtas: Optional[str], pitch_deg: float) -> Optional[str]:
    """Return PSX's PiBaHeAlTas with the pitch field (1e-5 rad) replaced, None if malformed."""
    parts = (pibahealtas or "").split(";")
    if len(parts) != 7:
        return None
    parts[0] = str(round(math.radians(pitch_deg) * 100000))
    return ";".join(parts)


def door_states(open_bits: Optional[int]) -> Optional[dict]:
    """Return {door: "open"/"closed"} from DoorOpenBits, or None before PSX sent it."""
    if open_bits is None:
        return None
    return {name: "open" if open_bits & bit else "closed" for name, bit in DOOR_BITS.items()}


def fuel_kg(fuelqty: Optional[str]) -> Optional[dict]:
    """Return {tank: kg} from PSX's FuelQty, or None."""
    fields = (fuelqty or "").strip().rstrip(";").split(";")
    if len(fields) < len(TANKS) or not fields[0].startswith("d"):
        return None
    try:
        tenths = [int(fields[0][1:])] + [int(x) for x in fields[1:len(TANKS)]]
    except ValueError:
        return None
    return {tank: round(v / 10.0 * LB_KG) for tank, v in zip(TANKS, tenths)}


def power(on_ground: Optional[bool], lights: dict) -> tuple:
    """Return (cargo handling bus powered?, why not) from the EXT PWR / APU GEN buttons."""
    if on_ground is False:
        return False, "AIRBORNE"
    values = [lights.get(name) for name in POWER_SOURCES]
    if all(v is None for v in values):
        return False, "POWER STATE NOT KNOWN YET"
    if any((v or 0) & LIGHT_AVAIL for v in values):
        return True, ""
    if any((v or 0) & LIGHT_ON for v in values):
        return False, "EXT PWR / APU GEN ON THE MAIN BUSES, NONE AVAILABLE"
    return False, "NO GROUND POWER OR APU AVAILABLE"


def engines_running(cutoffs) -> Optional[int]:
    """Return how many engines run (0-4) from FuelCutoff1-4 (1 = RUN, 0 = CUTOFF), or None.

    The fuel control switches, not EngRunt: EngRunt reads 300 while an
    engine runs and 0 after CUTOFF, but 8 on a cold and dark aircraft
    (headless 2026-11-25), so it can't tell "off" reliably.
    """
    if cutoffs is None or len(cutoffs) != 4 or any(c is None for c in cutoffs):
        return None
    return sum(1 for c in cutoffs if int(c) & 1)


def arrival(state: dict) -> dict:
    """Return the fields Flight Centre starts unloading from (sent with or without the option).

    state: on_ground (bool), park_brake_lever (ParkBrkLev 0/1), beacon
    (LtBeacon -1/0/1, 0 = OFF), fuel_cutoffs ([FuelCutoff1-4]). None when not
    known yet.
    """
    brake, beacon = state.get("park_brake_lever"), state.get("beacon")
    return {
        "on_ground": state.get("on_ground"),
        "parking_brake": bool(brake) if brake is not None else None,
        "engines_running": engines_running(state.get("fuel_cutoffs")),
        "beacon_on": beacon != 0 if beacon is not None else None,
    }


def weight_unit(pin_prog: Optional[int]) -> Optional[str]:
    """Return PSX's weight unit setting, "kg" or "lb", from CfgPinProg; None if unknown."""
    if pin_prog is None:
        return None
    return "kg" if pin_prog & PIN_WEIGHT_KG else "lb"


def telemetry(allowed: bool, state: dict) -> dict:  # pylint: disable=too-many-locals
    """Return the cargo fields for frankenpush's telemetry, from PSX's values in state.

    state: on_ground (bool), door_open_bits, true_cg (x10), true_zfw (lb),
    eicas_grwt, fuelqty (str), refueling (0/1), the lights of
    POWER_SOURCES, pin_prog (CfgPinProg) and what arrival() reads. Without
    the option only cargo_loading_allowed (false), weight_unit and the
    arrival() fields.
    """
    unit = {"weight_unit": weight_unit(state.get("pin_prog"))}
    if not allowed:
        return {"cargo_loading_allowed": False, **unit, **arrival(state)}
    ok, why = power(state.get("on_ground"), state)
    zfw_lb = state.get("true_zfw")
    gw_t = gross_weight_t(state.get("eicas_grwt"), zfw_lb)
    cg = state.get("true_cg")
    refueling = state.get("refueling")
    return {
        "cargo_loading_allowed": True,
        "cargo_cg_writable": True,
        "cargo_doors": door_states(state.get("door_open_bits")),
        "cargo_power_ok": ok,
        "cargo_power_text": why,
        "zfw_kg": round(zfw_lb * LB_KG) if zfw_lb else None,
        "gross_weight_kg": round(gw_t * 1000.0) if gw_t else None,
        "cg_pct_mac": cg / 10.0 if cg is not None else None,
        "fuel_kg": fuel_kg(state.get("fuelqty")),
        "refueling": bool(refueling) if refueling is not None else None,
        **unit,
        **arrival(state),
    }


def _number(value, lo: float, hi: float) -> Optional[float]:
    """Return value as a float within [lo, hi], or None (missing, not a number, out of range)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if lo <= value <= hi else None


def apply(msg: dict, allowed: bool, state: dict) -> tuple:  # pylint: disable=too-many-branches,too-many-locals
    """Return (writes, notes) for one cargo_state: [(PSX name, value)] in order, and log notes.

    Only with the option, on the ground. ZFW and CG are written when they
    differ; doors only while the cargo handling bus has power (DoorComBits
    to OFF first, so PSX keeps the bits). "opening"/"open" opens a door,
    "closing"/"closed" closes it; doors not named stay as they are.
    """
    if not allowed:
        return [], [NOT_ALLOWED]
    if state.get("on_ground") is not True:
        return [], ["cargo_state ignored: not on the ground"]
    writes, notes = [], []
    if "zfw_kg" in msg:
        kg = _number(msg["zfw_kg"], *ZFW_KG_RANGE)
        if kg is None:
            notes.append(f"zfw_kg {msg['zfw_kg']!r} ignored (not {ZFW_KG_RANGE[0]:.0f}-"
                         f"{ZFW_KG_RANGE[1]:.0f} kg)")
        else:
            lb = round(kg / LB_KG)
            if lb != state.get("true_zfw"):
                writes.append(("TrueZfw", lb))
    if "cg_pct_mac" in msg:
        pct = _number(msg["cg_pct_mac"], *CG_PCT_RANGE)
        if pct is None:
            notes.append(f"cg_pct_mac {msg['cg_pct_mac']!r} ignored (not {CG_PCT_RANGE[0]}-"
                         f"{CG_PCT_RANGE[1]} % MAC)")
        else:
            tenths = round(pct * 10.0)
            if tenths != state.get("true_cg"):
                writes.append(("TrueCg", tenths))
    doors = msg.get("doors") if isinstance(msg.get("doors"), dict) else {}
    want = {}
    for name, value in doors.items():
        if name not in DOOR_BITS or value not in ("opening", "open", "closing", "closed"):
            notes.append(f"door {name!r}: {value!r} ignored")
            continue
        want[name] = value in ("opening", "open")
    if want:
        writes += _door_writes(want, state, notes)
    return writes, notes


def _door_writes(want: dict, state: dict, notes: list) -> list:
    """Return the DoorComBits/DoorOpenBits writes for {door: open?}; cargo doors need power."""
    bits = state.get("door_open_bits")
    if bits is None:
        notes.append("doors not moved: PSX's door state not known yet")
        return []
    ok, why = power(state.get("on_ground"), state)
    if not ok and any(name not in UNPOWERED_DOORS for name in want):
        notes.append(f"cargo doors not moved: {why}")
        want = {name: v for name, v in want.items() if name in UNPOWERED_DOORS}
    new = bits
    for name, is_open in want.items():
        new = (new | DOOR_BITS[name]) if is_open else (new & ~DOOR_BITS[name])
    if new == bits:
        return []
    writes = [] if state.get("door_com_bits") == DOOR_COM_OFF else [("DoorComBits", DOOR_COM_OFF)]
    return writes + [("DoorOpenBits", new)]


class TestCargo(unittest.TestCase):
    """The cargo logic."""

    GROUND = {"on_ground": True, "ExtPow1": 256, "ExtPow2": 256, "ApuGen1": 0, "ApuGen2": 0,
              "door_open_bits": 0, "door_com_bits": 1, "true_cg": 185, "true_zfw": 529109,
              "eicas_grwt": 2542, "refueling": 0, "park_brake_lever": 1, "beacon": 0,
              "fuel_cutoffs": [0, 0, 0, 0],
              "fuelqty": "d100000;110000;120000;130000;10000;20000;300000;50000;0;359413;2124;"}

    def test_power(self):
        """AVAIL on any EXT PWR / APU GEN powers the cargo handling bus; ON doesn't."""
        self.assertEqual(power(True, {"ExtPow1": 256, "ExtPow2": 0}), (True, ""))
        self.assertEqual(power(True, {"ApuGen2": 256})[0], True)
        self.assertEqual(power(True, {"ExtPow1": 128, "ExtPow2": 128}),
                         (False, "EXT PWR / APU GEN ON THE MAIN BUSES, NONE AVAILABLE"))
        self.assertEqual(power(True, {"ExtPow1": 0, "ApuGen1": 0}),
                         (False, "NO GROUND POWER OR APU AVAILABLE"))
        self.assertEqual(power(False, {"ExtPow1": 256}), (False, "AIRBORNE"))
        self.assertEqual(power(True, {})[0], False)

    def test_doors_and_fuel(self):
        """The door bits and the fuel quantities decoded."""
        self.assertEqual(door_states(1 | 2048 | 8)["nose"], "open")
        self.assertEqual(door_states(1 | 2048 | 8)["side"], "closed")
        self.assertEqual(door_states(2048)["lower_fwd"], "open")
        self.assertIsNone(door_states(None))
        fuel = fuel_kg(self.GROUND["fuelqty"])
        self.assertEqual(fuel["M1"], 4536)
        self.assertEqual(fuel["CWT"], 13608)
        self.assertEqual(fuel["STAB"], 2268)
        self.assertEqual(fuel["AUX"], 0)
        self.assertIsNone(fuel_kg("garbage"))

    def test_telemetry(self):
        """Fields with the option; only the flag without it."""
        self.assertEqual(telemetry(False, self.GROUND),
                         {"cargo_loading_allowed": False, "weight_unit": None, "on_ground": True,
                          "parking_brake": True, "engines_running": 0, "beacon_on": False})
        t = telemetry(True, self.GROUND)
        self.assertTrue(t["cargo_power_ok"])
        self.assertEqual(t["cg_pct_mac"], 18.5)
        self.assertEqual(t["zfw_kg"], 240000)
        self.assertEqual(t["gross_weight_kg"], 254200)
        self.assertEqual(t["cargo_doors"]["nose"], "closed")
        self.assertIs(t["refueling"], False)
        with_unit = telemetry(True, {**self.GROUND, "pin_prog": 87900296})
        self.assertEqual(with_unit["weight_unit"], "kg")

    def test_weight_unit(self):
        """The unit is kg with CfgPinProg's bit 0x100000, lb without, None unknown."""
        self.assertEqual(weight_unit(87900296), "kg")
        self.assertEqual(weight_unit(87900296 ^ PIN_WEIGHT_KG), "lb")
        self.assertIsNone(weight_unit(None))

    def test_arrival(self):
        """Engines from the fuel control switches, beacon on in either ON position, None unknown."""
        self.assertEqual(engines_running([1, 1, 0, 1]), 3)
        self.assertIsNone(engines_running(None))
        self.assertIsNone(engines_running([1, None, 1, 1]))
        a = arrival({"on_ground": False, "park_brake_lever": 0, "beacon": -1,
                     "fuel_cutoffs": [1, 1, 1, 1]})
        self.assertEqual(a, {"on_ground": False, "parking_brake": False, "engines_running": 4,
                             "beacon_on": True})
        self.assertEqual(set(arrival({}).values()), {None})

    def test_apply(self):
        """ZFW, CG and doors written in order; DoorComBits OFF before the doors."""
        msg = {"type": "cargo_state", "zfw_kg": 245000, "cg_pct_mac": 24.6,
               "doors": {"nose": "opening", "side": "open", "bulk": "closed"}}
        writes, notes = apply(msg, True, self.GROUND)
        self.assertEqual(writes, [("TrueZfw", 540133), ("TrueCg", 246), ("DoorComBits", 32),
                                  ("DoorOpenBits", 257)])
        self.assertEqual(notes, [])
        st = dict(self.GROUND, door_com_bits=32, door_open_bits=257, true_cg=246, true_zfw=540133)
        self.assertEqual(apply(msg, True, st), ([], []))     # nothing changed: nothing written
        writes, _ = apply({"doors": {"nose": "closing"}}, True, st)
        self.assertEqual(writes, [("DoorOpenBits", 256)])

    def test_warning(self):
        """FLIGHTCENTRE shown and cleared; someone else's free warning left alone."""
        self.assertEqual(warning({"active": True}, True, "")[0], "FLIGHTCENTRE")
        self.assertIsNone(warning({"active": True}, True, "FLIGHTCENTRE")[0])
        self.assertEqual(warning({"active": False}, True, "FLIGHTCENTRE")[0], "")

    def test_tail_tip(self):
        """A smooth rise to TIP_PITCH_DEG, a hold, then None; PiBaHeAlTas keeps its other fields."""
        self.assertTrue(is_tail_tip({"active": True, "reason": "Tail tip"}))
        self.assertFalse(is_tail_tip({"active": False, "reason": "tail tip"}))
        self.assertFalse(is_tail_tip({"active": True, "reason": "fire"}))
        self.assertFalse(is_tail_tip({"active": True}))
        self.assertEqual(tail_tip_pitch(0), 0)
        self.assertAlmostEqual(tail_tip_pitch(TIP_RISE_S / 2), TIP_PITCH_DEG / 2)
        self.assertEqual(tail_tip_pitch(TIP_RISE_S + 1), TIP_PITCH_DEG)
        self.assertIsNone(tail_tip_pitch(TIP_RISE_S + TIP_HOLD_S))
        raw = "0;8;1.222;127461;0;-0.925;-1.236"
        self.assertEqual(with_pitch(raw, 12), "20944;8;1.222;127461;0;-0.925;-1.236")
        self.assertIsNone(with_pitch("1;2;3", 12))
        self.assertIsNone(with_pitch(None, 12))
        self.assertIsNone(warning({"active": False}, True, "ROUTER")[0])
        self.assertIsNone(warning({"active": "yes"}, True, "")[0])
        self.assertEqual(warning({"active": True}, False, ""), (None, NOT_ALLOWED))

    def test_refusals(self):
        """Not allowed, airborne, no power, bad values."""
        msg = {"zfw_kg": 245000, "doors": {"nose": "open"}}
        self.assertEqual(apply(msg, False, self.GROUND), ([], [NOT_ALLOWED]))
        self.assertEqual(apply(msg, True, dict(self.GROUND, on_ground=False))[0], [])
        writes, notes = apply(msg, True, dict(self.GROUND, ExtPow1=128, ExtPow2=0))
        self.assertEqual(writes, [("TrueZfw", 540133)])
        self.assertIn("doors not moved", notes[0])
        # the entry door needs no cargo handling bus: it moves, the cargo door doesn't
        writes, notes = apply({"doors": {"entry_1l": "opening", "side": "open"}}, True,
                              dict(self.GROUND, ExtPow1=128, ExtPow2=0))
        self.assertEqual(writes, [("DoorComBits", 32), ("DoorOpenBits", 8)])
        self.assertIn("cargo doors not moved", notes[0])
        self.assertEqual(door_states(8)["entry_1l"], "open")
        writes, notes = apply({"zfw_kg": 900000, "cg_pct_mac": "x", "doors": {"cockpit": "open"}},
                              True, self.GROUND)
        self.assertEqual(writes, [])
        self.assertEqual(len(notes), 3)
