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
- PSX never tips onto its tail and keeps a ground pitch of 0: Flight
  Centre simulates the tip.
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
import unittest
from typing import Optional

from fp_formation import gross_weight_t

LB_KG = 0.45359237
DOOR_BITS = {"nose": 1, "side": 256, "lower_fwd": 2048, "lower_aft": 131072, "bulk": 262144}
DOOR_COM_OFF = 32
LIGHT_AVAIL = 256
LIGHT_ON = 128
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


def telemetry(allowed: bool, state: dict) -> dict:  # pylint: disable=too-many-locals
    """Return the cargo fields for frankenpush's telemetry, from PSX's values in state.

    state: on_ground (bool), door_open_bits, true_cg (x10), true_zfw (lb),
    eicas_grwt, fuelqty (str), refueling (0/1), and the lights of
    POWER_SOURCES. Without the option only cargo_loading_allowed (false).
    """
    if not allowed:
        return {"cargo_loading_allowed": False}
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
        ok, why = power(state.get("on_ground"), state)
        bits = state.get("door_open_bits")
        if not ok:
            notes.append(f"doors not moved: {why}")
        elif bits is None:
            notes.append("doors not moved: PSX's door state not known yet")
        else:
            new = bits
            for name, is_open in want.items():
                new = (new | DOOR_BITS[name]) if is_open else (new & ~DOOR_BITS[name])
            if new != bits:
                if state.get("door_com_bits") != DOOR_COM_OFF:
                    writes.append(("DoorComBits", DOOR_COM_OFF))
                writes.append(("DoorOpenBits", new))
    return writes, notes


class TestCargo(unittest.TestCase):
    """The cargo logic."""

    GROUND = {"on_ground": True, "ExtPow1": 256, "ExtPow2": 256, "ApuGen1": 0, "ApuGen2": 0,
              "door_open_bits": 0, "door_com_bits": 1, "true_cg": 185, "true_zfw": 529109,
              "eicas_grwt": 2542, "refueling": 0,
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
        self.assertEqual(telemetry(False, self.GROUND), {"cargo_loading_allowed": False})
        t = telemetry(True, self.GROUND)
        self.assertTrue(t["cargo_power_ok"])
        self.assertEqual(t["cg_pct_mac"], 18.5)
        self.assertEqual(t["zfw_kg"], 240000)
        self.assertEqual(t["gross_weight_kg"], 254200)
        self.assertEqual(t["cargo_doors"]["nose"], "closed")
        self.assertIs(t["refueling"], False)

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
        writes, notes = apply({"zfw_kg": 900000, "cg_pct_mac": "x", "doors": {"cockpit": "open"}},
                              True, self.GROUND)
        self.assertEqual(writes, [])
        self.assertEqual(len(notes), 3)
