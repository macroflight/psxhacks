"""How hard the autopilot and autothrottle work in a recording: a turbulence measure.

From a recording (Sim.record()) made while flying stably, per control:
how far it moved in total (degrees per minute), how often it reversed
direction (per minute), both ignoring flicker within DEAD_BAND, and its
spread (standard deviation, degrees).
Ailerons are the roll command (inboard left minus right, half), elevator
the mean of the four, rudder the mean of upper and lower, all from the
surface angles (SurfAilEleRud, whole degrees); thrust the mean thrust
lever angle (Tla). Also the autopilot servo commands (ApServo, three
values -999..999 at ~10 Hz, undocumented: taken to be elevator;aileron;
rudder like FltControls) in servo units instead of degrees, and the
turbulence bursts injected (WxBurst) per minute.
"""
import dataclasses
import statistics
from typing import Optional

import os

from . import accel
from .sim import qcode


@dataclasses.dataclass
class Activity:
    """One control's activity over a recording."""

    moved_per_min: float      # total movement, degrees per minute
    reversals_per_min: float  # direction reversals per minute
    stdev: float              # spread, degrees

    def __str__(self) -> str:
        """Return e.g. "12.0 deg/min, 3.1 rev/min, sd 0.8"."""
        return (f"{self.moved_per_min:5.1f} deg/min {self.reversals_per_min:4.1f} rev/min "
                f"sd {self.stdev:4.2f}")


# Changes up to this size (degrees) count as no movement: surfaces come in
# whole degrees, so an elevator holding a trim between two degrees flips
# between them all the time, even in calm air (1 deg; the aileron roll
# command, half the difference of two, moves in 0.5 deg steps)
DEAD_BAND = {"aileron": 0.5, "elevator": 1.0, "rudder": 0.5, "thrust": 0.5,
             "servo_elevator": 0.0, "servo_aileron": 0.0, "servo_rudder": 0.0}
SERVOS = ("servo_elevator", "servo_aileron", "servo_rudder")


def activity(samples: list, dead_band: float = 0.0) -> Optional[Activity]:
    """Return the Activity of [(time, value)], or None with too little data.

    A move counts once the value is more than dead_band from where the
    last counted move ended.
    """
    if len(samples) < 3 or samples[-1][0] <= samples[0][0]:
        return None
    minutes = (samples[-1][0] - samples[0][0]) / 60.0
    moved = 0.0
    reversals = 0
    last_dir = 0
    level = samples[0][1]
    for _, value in samples[1:]:
        step = value - level
        if abs(step) <= dead_band:
            continue
        moved += abs(step)
        level = value
        direction = 1 if step > 0 else -1
        reversals += last_dir and direction != last_dir
        last_dir = direction
    return Activity(moved / minutes, reversals / minutes,
                    statistics.pstdev(v for _, v in samples))


def _surfaces(value: str) -> Optional[tuple]:
    if len(value) != 20 or not value.isdigit():
        return None
    deg = [int(value[i:i + 2]) for i in range(0, 20, 2)]
    return ((deg[1] - deg[2]) / 2.0,          # roll: inboard ailerons
            sum(deg[4:8]) / 4.0,              # elevators
            (deg[8] + deg[9]) / 2.0)          # rudders


def _read(path: str, start: float, end: float) -> tuple:  # pylint: disable=too-many-locals
    """Return ({control: [(time, degrees)]}, bursts, minutes) for path's start-end epochs."""
    surf_q, tla_q, burst_q = qcode("SurfAilEleRud"), qcode("Tla"), qcode("WxBurst")
    servo_q = qcode("ApServo")
    series = {name: [] for name in DEAD_BAND}
    bursts = 0
    times = []
    with open(path, encoding="utf-8") as rec:
        for line in rec:
            stamp, _, keyvalue = line.rstrip("\n").partition(" ")
            now = float(stamp)
            if not start <= now <= end:
                continue
            times.append(now)
            key, _, value = keyvalue.partition("=")
            if key == surf_q and (surf := _surfaces(value)):
                for name, deg in zip(("aileron", "elevator", "rudder"), surf):
                    series[name].append((now, deg))
            elif key == tla_q:
                angles = [int(v) for v in value.split(";")[:4]]
                series["thrust"].append((now, sum(angles) / len(angles) / 100.0))
            elif key == servo_q:
                for name, units in zip(SERVOS, value.split(";")):
                    series[name].append((now, float(units)))
            elif key == burst_q:
                bursts += 1
    minutes = (times[-1] - times[0]) / 60.0 if len(times) > 1 else 0.0
    return series, bursts, minutes


def analyse(path: str, start: float = 0.0, end: float = float("inf")) -> dict:
    """Return {control: Activity or None, "bursts_per_min": float} for path's start-end epochs."""
    series, bursts, minutes = _read(path, start, end)
    result = {name: activity(samples, DEAD_BAND[name]) for name, samples in series.items()}
    result["bursts_per_min"] = bursts / minutes if minutes else 0.0
    return result


def report(path: str) -> str:
    """Return a one-recording summary, one control per line."""
    result = analyse(path)
    lines = [f"{path}: {result.pop('bursts_per_min'):.0f} bursts/min"]
    for name, act in result.items():
        text = str(act) if act else "no data"
        if name in SERVOS:
            text = text.replace("deg/min", "u/min  ")
        lines.append(f"  {name:14s} {text}")
    accel_path = path[:-4] + ".accel" if path.endswith(".rec") else ""
    if os.path.exists(accel_path):
        lines.append("  " + accel.report(accel_path).split(": ", 1)[1])
    boost_path = path[:-4] + ".boost" if path.endswith(".rec") else ""
    if os.path.exists(boost_path):
        lines.append("  " + accel.report(boost_path, raw=True).split(": ", 1)[1])
    return "\n".join(lines)
