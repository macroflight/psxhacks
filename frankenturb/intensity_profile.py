"""Slowly (and sometimes suddenly) varying turbulence intensity over time.

Used for turbulence areas that say nothing about how strong the turbulence
is from minute to minute, e.g. a TURB or MTW SIGMET. Real turbulence in such
an area mostly changes slowly, but sometimes very abruptly, and is mostly
light with only short spells of severe turbulence.

The profile is a chain of segments. Each segment picks a random target
level, ramps from the current level to it along an S-curve (usually over a
minute or more, sometimes within seconds) and then stays at the target for
a random time.
Then the next segment is picked. The target is drawn from a few bands with
uneven weights, the higher bands also staying for shorter times. With the
default bands (simulated over 500 hours) the intensity is:

- between 10 % and 25 % about half the time,
- 45 % or more about 10 minutes per hour,
- 60 % or more about 2.5 minutes per hour,
- 70 % or more about half a minute per hour (75 % is the maximum).
"""
import random
import time
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class IntensityBand:
    """One band of target levels: chosen with weight, held for stay_min_s-stay_max_s."""

    low: float
    high: float
    weight: float
    stay_min_s: float
    stay_max_s: float


DEFAULT_BANDS = (
    IntensityBand(0.10, 0.25, 0.45, 120.0, 480.0),
    IntensityBand(0.25, 0.45, 0.30, 60.0, 240.0),
    IntensityBand(0.45, 0.62, 0.17, 60.0, 240.0),
    IntensityBand(0.62, 0.75, 0.08, 30.0, 90.0),
)
ABRUPT_PROBABILITY = 0.2      # share of ramps that are abrupt
ABRUPT_RAMP_S = (2.0, 10.0)   # abrupt ramp time range
SLOW_RAMP_S = (30.0, 180.0)   # normal ramp time range


class IntensityProfile:  # pylint: disable=too-many-instance-attributes
    """A turbulence intensity (0-1) that varies over time, see the module docstring."""

    def __init__(self, bands=DEFAULT_BANDS,
                 rng: Optional[random.Random] = None,
                 clock: Callable[[], float] = time.monotonic):
        """Start at a random level in the lowest band."""
        self._bands = tuple(bands)
        self._rng = rng or random.Random()
        self._clock = clock
        self._from = self._rng.uniform(self._bands[0].low, self._bands[0].high)
        self._target = self._from
        self._ramp_start = self._clock()
        self._ramp_s = 0.0
        self._stay_s = 0.0
        self._abrupt = False
        self.description = ""
        self.segment = 0  # counts segments, so callers can tell a new one started

    def _next_segment(self, now: float) -> None:
        """Pick the next target level, ramp time and stay time, starting at now."""
        band = self._rng.choices(self._bands, weights=[b.weight for b in self._bands])[0]
        abrupt = self._rng.random() < ABRUPT_PROBABILITY
        self._abrupt = abrupt
        self.segment += 1
        self._from = self._target
        self._target = self._rng.uniform(band.low, band.high)
        self._ramp_start = now
        self._ramp_s = self._rng.uniform(*(ABRUPT_RAMP_S if abrupt else SLOW_RAMP_S))
        self._stay_s = self._rng.uniform(band.stay_min_s, band.stay_max_s)
        self.description = (f"{self._from:.0%} -> {self._target:.0%} over "
                            f"{self._ramp_s:.0f}s{' (abrupt)' if abrupt else ''}, "
                            f"then {self._stay_s:.0f}s")

    def value(self) -> float:
        """Return the intensity (0-1) right now."""
        now = self._clock()
        while now >= self._ramp_start + self._ramp_s + self._stay_s:
            # Segments that ended while nobody asked chain on from where
            # they would have ended.
            self._next_segment(self._ramp_start + self._ramp_s + self._stay_s)
        elapsed = now - self._ramp_start
        if elapsed >= self._ramp_s:
            return self._target
        # S-curve (smoothstep): starts and ends gently, no sudden change of
        # rate at either end of the ramp.
        x = elapsed / self._ramp_s
        return self._from + (self._target - self._from) * x * x * (3.0 - 2.0 * x)

    def status(self) -> dict:
        """Return the current segment: levels, times (s) and where we are in it.

        ramp_left_s/hold_left_s are the seconds left of the ramp and of the
        hold after it (the full hold time while still ramping).
        """
        value = self.value()
        elapsed = self._clock() - self._ramp_start
        return {
            "value": value,
            "from": self._from,
            "target": self._target,
            "ramp_s": self._ramp_s,
            "hold_s": self._stay_s,
            "abrupt": self._abrupt,
            "ramp_left_s": max(0.0, self._ramp_s - elapsed),
            "hold_left_s": min(self._stay_s, self._ramp_s + self._stay_s - elapsed),
        }
