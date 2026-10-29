"""Rare turbulence events: moderate patches, severe clusters and extreme encounters.

Real turbulence is mostly smooth or light; moderate comes in patches a
minute or two long, and severe is a 5-30 s cluster of jolts inside ordinary
chop, something an aircraft hits rather than flies in. At cruise about
0.4 % of the time is moderate or worse and ~0.1 % severe; in the US there
is about one severe pilot report per 3,500 airline hours and one
serious-injury encounter per ~2 million (docs/turbulence-research/
realism-targets.md). frankenweather's testing profile keeps its continuous
turbulence light and makes everything stronger one of these events,
started at random (a Poisson process) at a rate per hour that depends on
the kind of area the aircraft is in (AREA_RATES), times the crew's
multiplier per kind.

An event is a level the continuous turbulence is raised to for a while,
plus scripted WxBurst up/down bursts. Sizes and timings come from PSX
test flights (FL350, 2026-10-26, docs/turbulence-research/): a single
up/down burst of size s gives about 1.25 x (s/99)^2 g (size_for_g()), and
PSX's pitch rings on for ~8 s at a 3.5-4 s period at about 60 % of the
first swing, so one big burst already gives the 2-4 big swings of a real
severe encounter. Two bursts the same way within half a second, or bursts
about one ring period apart, add up to over 2 g: big bursts are spaced at
least SEVERE_SPACING_S apart. The extreme shape is an up-burst then, 0.6 s
later, a full down-burst (-1.4 g, rebound +0.6 g: nz about -0.4, like
UA826's -0.8); one in four is the double down-burst (-2.2 g, nz about
-1.2, like SQ321's -1.5).

No PSX here: the caller sends the bursts and applies the level.
"""
import math
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

KINDS = ("moderate", "severe", "extreme")

# Events per hour of flight in each kind of area (moderate, severe, extreme).
# "clean" is cruise outside any turbulence source; the advisory rows are
# estimates from how much forecast areas concentrate turbulence
# (realism-targets.md, "Default time fractions and event rates").
AREA_RATES = {
    "clean": (0.1, 1 / 2000, 1 / 500000),
    "advisory": (4.0, 1 / 100, 1 / 50000),     # G-AIRMET, MOD SIGMET, MOD PIREP
    "sev_sigmet": (5.0, 0.5, 1 / 1000),        # SEV TURB SIGMET, SEV PIREP
    "mtw": (3.0, 0.2, 1 / 2000),               # MTW SIGMET (wave breaking)
    "cb_near": (7.0, 0.75, 1 / 500),           # near a CB, TS/TC SIGMET area
    "cb_core": (20.0, 8.0, 1 / 20),            # in a CB
    "bl_strong": (7.0, 0.1, 1 / 10000),        # strong low-level wind, rotor
}

MODERATE_LEVEL = (0.35, 0.50)       # continuous level during a moderate patch
MODERATE_MEDIAN_S = 90.0            # log-normal patch length ...
MODERATE_SIGMA = 0.8
MODERATE_S = (20.0, 600.0)          # ... clamped to this
SEVERE_S = (5.0, 30.0)
SEVERE_LEVEL = 0.45                 # the chop around the big bursts
SEVERE_SPACING_S = (6.0, 10.0)
# Planned peak g of a severe cluster's big bursts: (share, low, high)
SEVERE_PEAKS = ((0.87, 0.5, 0.8), (0.13, 0.8, 1.2))
EXTREME_LEAD_S = (12.0, 25.0)       # severe chop before the drop
EXTREME_TAIL_S = 10.0
EXTREME_DOUBLE_SHARE = 0.25
DOWN_SHARE = 0.6                    # severe big bursts: share pushing down


def size_for_g(g: float) -> int:
    """Return the WxBurst up/down size (1-99) whose first swing is about g (PSX, FL350).

    Never 90: PSX answers +90 and -90 alike with -1.3 g then +2.3 g,
    unlike 88-92 (2026-10-26, docs/turbulence-research/
    event-shape-calibration.md).
    """
    size = max(1, min(99, round(99.0 * math.sqrt(max(0.0, g) / 1.25))))
    return 89 if size == 90 else size


@dataclass
class TurbulenceEvent:  # pylint: disable=too-many-instance-attributes
    """One event: a continuous level from start to end plus scripted bursts.

    bursts are (seconds after start, signed WxBurst value), sorted.
    """

    kind: str
    start: float
    duration_s: float
    level: float
    peak_g: float = 0.0
    bursts: list = field(default_factory=list)
    area: str = ""
    triggered: bool = False
    _next: int = 0

    def active(self, now: float) -> bool:
        """Return True until the event is over."""
        return now < self.start + self.duration_s

    def due_bursts(self, now: float) -> list:
        """Return the bursts due by now that weren't returned before."""
        due = []
        while self._next < len(self.bursts) and self.start + self.bursts[self._next][0] <= now:
            due.append(self.bursts[self._next][1])
            self._next += 1
        return due


def make_event(kind: str, now: float, rng: random.Random, area: str = "",
               triggered: bool = False) -> TurbulenceEvent:
    """Return a new event of this kind starting now (see the module docstring)."""
    if kind == "moderate":
        duration = rng.lognormvariate(math.log(MODERATE_MEDIAN_S), MODERATE_SIGMA)
        duration = min(MODERATE_S[1], max(MODERATE_S[0], duration))
        return TurbulenceEvent("moderate", now, duration, rng.uniform(*MODERATE_LEVEL),
                               area=area, triggered=triggered)
    if kind == "severe":
        duration = rng.uniform(*SEVERE_S)
        bursts, t, peak = [], rng.uniform(0.5, 3.0), 0.0
        while t < duration - 2.0 or not bursts:
            _, low, high = rng.choices(SEVERE_PEAKS, weights=[p[0] for p in SEVERE_PEAKS])[0]
            g = rng.uniform(low, high)
            peak = max(peak, g)
            sign = -1 if rng.random() < DOWN_SHARE else 1
            bursts.append((t, sign * size_for_g(g)))
            t += rng.uniform(*SEVERE_SPACING_S)
        return TurbulenceEvent("severe", now, max(duration, bursts[-1][0] + 4.0), SEVERE_LEVEL,
                               peak_g=peak, bursts=bursts, area=area, triggered=triggered)
    lead = rng.uniform(*EXTREME_LEAD_S)
    bursts = [(rng.uniform(1.0, 4.0), -size_for_g(rng.uniform(0.5, 0.8)))]
    up = size_for_g(rng.uniform(0.3, 0.45))
    if rng.random() < EXTREME_DOUBLE_SHARE:
        bursts += [(lead, up), (lead + 0.6, -99), (lead + 0.8, -99)]
        peak = 2.2
    else:
        bursts += [(lead, up), (lead + 0.6, -99)]
        peak = 1.4
    return TurbulenceEvent("extreme", now, lead + EXTREME_TAIL_S, SEVERE_LEVEL, peak_g=peak,
                           bursts=sorted(bursts), area=area, triggered=triggered)


class EventScheduler:
    """Start events at random at AREA_RATES x multiplier; at most one at a time.

    A severe or extreme event may cut a moderate patch short; nothing
    interrupts a severe or extreme one.
    """

    def __init__(self, rng: Optional[random.Random] = None,
                 clock: Callable[[], float] = time.monotonic):
        """Start with no event."""
        self._rng = rng or random.Random()
        self._clock = clock
        self.event: Optional[TurbulenceEvent] = None

    def current(self) -> Optional[TurbulenceEvent]:
        """Return the event in progress, or None."""
        if self.event is not None and not self.event.active(self._clock()):
            self.event = None
        return self.event

    def step(self, dt_s: float, area: str, scale: float, mult: dict) -> Optional[TurbulenceEvent]:
        """Maybe start an event for dt_s of flight in this area; return it if one started.

        scale (0-1) weakens the area's rates (e.g. near its edge); mult is
        the crew's {kind: multiplier}, 0 turning a kind off.
        """
        now = self._clock()
        current = self.current()
        if current is not None and current.kind != "moderate":
            return None
        rates = AREA_RATES.get(area, AREA_RATES["clean"])
        for kind, rate in reversed(list(zip(KINDS, rates))):
            if current is not None and kind == "moderate":
                continue
            p = rate * scale * mult.get(kind, 1.0) * dt_s / 3600.0
            if self._rng.random() < p:
                self.event = make_event(kind, now, self._rng, area)
                return self.event
        return None

    def trigger(self, kind: str, area: str = "") -> TurbulenceEvent:
        """Start an event of this kind now, replacing any in progress (testing, training)."""
        self.event = make_event(kind, self._clock(), self._rng, area, triggered=True)
        return self.event
