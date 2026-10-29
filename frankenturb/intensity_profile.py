"""Slowly (and sometimes suddenly) varying turbulence intensity over time and altitude.

Used for turbulence areas that say nothing about how strong the turbulence
is from minute to minute, e.g. a TURB or MTW SIGMET. Real turbulence in such
an area mostly changes slowly, but sometimes very abruptly, comes in layers
(a climb or descent often gets out of it, and a smooth level tends to stay
smooth for a while), and is mostly light or absent with only short spells
of severe turbulence.

IntensityProfile is one timeline: a chain of segments. Each segment picks a
random target level, ramps from the current level to it along an S-curve
(usually over a minute or more, sometimes within seconds) and then stays at
the target for a random time. Then the next segment is picked. The target is
drawn from a few bands with uneven weights, the higher bands also staying
for shorter times.

The weights depend on a roughness (0 smooth to 1 rough): each band has a
weight for a smooth and one for a rough timeline, and the roughness slides
between them. LayeredIntensityProfile gives each altitude layer (LAYER_FT
deep by default) its own independent timeline and its own roughness
(LayerRoughness: random, lasting ROUGHNESS_LIFE_S, then drifting to a new
random value), blended smoothly across the layer boundaries. Simulated over
500 hours, averaged over all layers, the intensity is:

- below 5 % (smooth air) about 14 minutes per hour,
- between 10 % and 25 % about 23 minutes per hour,
- 45 % or more about 11 minutes per hour,
- 70 % or more under a minute per hour (75 % is the maximum),

while a smooth layer (roughness 0) is at 45 % or more only about 1 minute
per hour and a rough one (roughness 1) about 25 minutes per hour.
"""
import math
import random
import time
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class IntensityBand:
    """One band of target levels, held for stay_min_s-stay_max_s.

    Chosen with smooth_weight on a smooth timeline (roughness 0) and
    rough_weight on a rough one (roughness 1), in between in between.
    """

    low: float
    high: float
    smooth_weight: float
    rough_weight: float
    stay_min_s: float
    stay_max_s: float

    def weight(self, roughness: float) -> float:
        """Return the band's weight at this roughness (0-1)."""
        return self.smooth_weight + (self.rough_weight - self.smooth_weight) * roughness


DEFAULT_BANDS = (
    IntensityBand(0.00, 0.05, 0.42, 0.04, 90.0, 420.0),   # smooth air
    IntensityBand(0.10, 0.25, 0.44, 0.20, 120.0, 420.0),
    IntensityBand(0.25, 0.45, 0.115, 0.27, 60.0, 240.0),
    IntensityBand(0.45, 0.62, 0.02, 0.29, 90.0, 300.0),
    IntensityBand(0.62, 0.75, 0.005, 0.17, 30.0, 100.0),
)
# Testing profile: smooth and light only. Moderate and stronger come as
# rare events instead (frankenturb/turbulence_events.py): real turbulence
# areas are mostly smooth or light, with moderate in patches and severe a
# few seconds now and then (docs/turbulence-research/realism-targets.md).
# 0.30 is light chop (p95 about 0.07 g, docs/turbulence-calibration.md).
REALISTIC_BANDS = (
    IntensityBand(0.00, 0.05, 0.50, 0.25, 90.0, 420.0),   # smooth air
    IntensityBand(0.12, 0.22, 0.35, 0.40, 120.0, 420.0),
    IntensityBand(0.22, 0.30, 0.15, 0.35, 60.0, 300.0),
)
ABRUPT_PROBABILITY = 0.2      # share of ramps that are abrupt
ABRUPT_RAMP_S = (2.0, 10.0)   # abrupt ramp time range
SLOW_RAMP_S = (30.0, 180.0)   # normal ramp time range

# Altitude layers (defaults). Boundaries at LAYER_OFFSET_FT + k * layer_ft,
# so every whole flight level is 500 ft from a boundary (unblended with a
# blend of up to 500 ft), and with 2000 ft layers a 2000 ft level change
# (the usual same-direction RVSM step) always changes layer.
LAYER_FT = 2000.0
LAYER_OFFSET_FT = -500.0
LAYER_BLEND_FT = 300.0        # blended this far either side of a boundary

# A layer's roughness lasts this long, then drifts to a new random value
# over ROUGHNESS_DRIFT_S.
ROUGHNESS_LIFE_S = (1800.0, 5400.0)
ROUGHNESS_DRIFT_S = 600.0


def expected_intensity(roughness: float, bands=DEFAULT_BANDS) -> float:
    """Return the long-run mean intensity of a timeline at this roughness.

    Time-weighted over the bands (weight x mean hold time, at the band's
    middle), ignoring the ramps between them. Used to tell pilots which
    levels are smooth or rough.
    """
    num = den = 0.0
    for band in bands:
        share = band.weight(roughness) * (band.stay_min_s + band.stay_max_s) / 2.0
        num += share * (band.low + band.high) / 2.0
        den += share
    return num / den if den else 0.0


def share_at_least(roughness: float, minimum: float, bands=DEFAULT_BANDS) -> float:
    """Return the long-run share of time (0-1) a timeline at this roughness is at minimum or more.

    Time-weighted over the bands (weight x mean hold time), each band's
    levels spread evenly between low and high, ignoring the ramps between
    them. Used to tell pilots how often a level is bumpy (PIREPs).
    """
    num = den = 0.0
    for band in bands:
        share = band.weight(roughness) * (band.stay_min_s + band.stay_max_s) / 2.0
        if minimum <= band.low:
            above = 1.0
        elif minimum >= band.high:
            above = 0.0
        else:
            above = (band.high - minimum) / (band.high - band.low)
        num += share * above
        den += share
    return num / den if den else 0.0


class LayerRoughness:  # pylint: disable=too-few-public-methods
    """A roughness (0-1) that stays for ROUGHNESS_LIFE_S, then drifts to a new random one."""

    def __init__(self, rng: random.Random, clock: Callable[[], float]):
        """Start at a random roughness, part way through its life."""
        self._rng = rng
        self._clock = clock
        self._from = self._to = self._rng.random()
        now = self._clock()
        self._drift_start = now - ROUGHNESS_DRIFT_S
        self._until = now + self._rng.uniform(0.0, ROUGHNESS_LIFE_S[1])

    def value(self) -> float:
        """Return the roughness right now."""
        now = self._clock()
        while now >= self._until:
            self._from, self._to = self._to, self._rng.random()
            self._drift_start = self._until
            self._until += self._rng.uniform(*ROUGHNESS_LIFE_S)
        x = min(1.0, (now - self._drift_start) / ROUGHNESS_DRIFT_S)
        return self._from + (self._to - self._from) * x


class IntensityProfile:  # pylint: disable=too-many-instance-attributes
    """A turbulence intensity (0-1) that varies over time, see the module docstring."""

    def __init__(self, bands=DEFAULT_BANDS,
                 rng: Optional[random.Random] = None,
                 clock: Callable[[], float] = time.monotonic,
                 roughness: Callable[[], float] = lambda: 0.5):
        """Start somewhere in a random segment, as if it had been running for a while.

        roughness is asked for each time a new segment is picked.
        """
        self._bands = tuple(bands)
        self._rng = rng or random.Random()
        self._clock = clock
        self._roughness = roughness
        self._abrupt = False
        self.description = ""
        self.segment = 0  # counts segments, so callers can tell a new one started
        self._target = self._rng.uniform(self._bands[0].low, self._bands[0].high)
        now = self._clock()
        self._next_segment(now)
        # Move the segment's start back by a random part of its length
        self._ramp_start = now - self._rng.uniform(0.0, self._ramp_s + self._stay_s)

    def _next_segment(self, now: float) -> None:
        """Pick the next target level, ramp time and stay time, starting at now."""
        roughness = self._roughness()
        band = self._rng.choices(self._bands, weights=[b.weight(roughness) for b in self._bands])[0]
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
            "segment": self.segment,
            "ramp_left_s": max(0.0, self._ramp_s - elapsed),
            "hold_left_s": min(self._stay_s, self._ramp_s + self._stay_s - elapsed),
        }


def _smoothstep(x: float) -> float:
    """Return the S-curve 0..1 for x in 0..1 (clamped)."""
    x = min(1.0, max(0.0, x))
    return x * x * (3.0 - 2.0 * x)


class LayeredIntensityProfile:
    """An independent IntensityProfile and roughness per altitude layer (module docstring)."""

    def __init__(self, bands=DEFAULT_BANDS,  # pylint: disable=too-many-arguments
                 rng: Optional[random.Random] = None,
                 clock: Callable[[], float] = time.monotonic,
                 *, layer_ft: float = LAYER_FT, blend_ft: float = LAYER_BLEND_FT):
        """Create layers lazily, the first time an altitude in them is asked for."""
        self._bands = bands
        self._layer_ft = layer_ft
        self._blend_ft = blend_ft
        self._rng = rng or random.Random()
        self._clock = clock
        self._layers: dict = {}      # layer -> (IntensityProfile, LayerRoughness)

    def set_bands(self, bands) -> None:
        """Use these bands from now on; the layers start over (e.g. a turbulence profile switch)."""
        if tuple(bands) != tuple(self._bands):
            self._bands = bands
            self._layers = {}

    def layer_of(self, alt_ft: float) -> int:
        """Return the index of the layer alt_ft is in."""
        return math.floor((alt_ft - LAYER_OFFSET_FT) / self._layer_ft)

    def layer_range(self, layer: int) -> tuple:
        """Return (base_ft, top_ft) of a layer."""
        base = LAYER_OFFSET_FT + layer * self._layer_ft
        return base, base + self._layer_ft

    def _layer(self, layer: int) -> tuple:
        """Return the layer's (timeline, roughness), creating them if needed."""
        if layer not in self._layers:
            roughness = LayerRoughness(random.Random(self._rng.random()), self._clock)
            profile = IntensityProfile(self._bands, rng=random.Random(self._rng.random()),
                                       clock=self._clock, roughness=roughness.value)
            self._layers[layer] = (profile, roughness)
        return self._layers[layer]

    def _weights(self, alt_ft: float) -> list:
        """Return [(layer, weight)]: the layer at alt_ft, blended near its boundaries."""
        layer = self.layer_of(alt_ft)
        base, top = self.layer_range(layer)
        if alt_ft - base < self._blend_ft:
            other, dist = layer - 1, alt_ft - base
        elif top - alt_ft < self._blend_ft:
            other, dist = layer + 1, top - alt_ft
        else:
            return [(layer, 1.0)]
        # 50/50 right at the boundary, all this layer blend_ft away
        w_other = 0.5 * (1.0 - _smoothstep(dist / self._blend_ft))
        return [(layer, 1.0 - w_other), (other, w_other)]

    def value(self, alt_ft: float) -> float:
        """Return the intensity (0-1) at alt_ft right now."""
        return sum(w * self._layer(layer)[0].value() for layer, w in self._weights(alt_ft))

    def roughness(self, alt_ft: float) -> float:
        """Return how rough (0 smooth to 1 rough) the layer(s) at alt_ft are right now."""
        return sum(w * self._layer(layer)[1].value() for layer, w in self._weights(alt_ft))

    def expected(self, alt_ft: float) -> float:
        """Return the long-run mean intensity at alt_ft for its current roughness."""
        return expected_intensity(self.roughness(alt_ft), self._bands)

    def share_at_least(self, alt_ft: float, minimum: float) -> float:
        """Return the long-run share of time at alt_ft at minimum or more (current roughness)."""
        return share_at_least(self.roughness(alt_ft), minimum, self._bands)

    def status(self, alt_ft: float) -> dict:
        """Return the status of the layer at alt_ft (see IntensityProfile), plus its range.

        "value" is the blended value at alt_ft; the rest describes the layer
        alt_ft is in (the one with the larger weight).
        """
        layer = self.layer_of(alt_ft)
        base, top = self.layer_range(layer)
        return {**self._layer(layer)[0].status(), "value": self.value(alt_ft),
                "roughness": self.roughness(alt_ft),
                "layer": layer, "layer_base_ft": base, "layer_top_ft": top}


# Gustiness (GustinessProfile): bands of a 0-1 value that is mapped onto a
# multiplier around 1. GUST_MEAN is the value's long-run mean over all
# layers (simulated), so the multiplier averages about 1.
GUST_BANDS = (
    IntensityBand(0.00, 0.20, 0.45, 0.01, 90.0, 420.0),
    IntensityBand(0.20, 0.45, 0.40, 0.06, 120.0, 420.0),
    IntensityBand(0.45, 0.65, 0.12, 0.20, 60.0, 240.0),
    IntensityBand(0.65, 0.85, 0.025, 0.38, 60.0, 240.0),
    IntensityBand(0.85, 1.00, 0.005, 0.35, 30.0, 120.0),
)
GUST_MEAN = 0.43


class GustinessProfile:
    """A layered multiplier (low-high, about 1 on average) for physically computed turbulence.

    Same timelines, layers and lasting roughness as LayeredIntensityProfile
    (GUST_BANDS), with each value mapped onto the multiplier: smooth layers
    are mostly below 1, rough ones mostly above.
    """

    def __init__(self, low: float, high: float, *,  # pylint: disable=too-many-arguments
                 layer_ft: float = LAYER_FT, blend_ft: float = LAYER_BLEND_FT,
                 rng: Optional[random.Random] = None,
                 clock: Callable[[], float] = time.monotonic):
        """Multiplier between low and high, in layers layer_ft deep."""
        self.low, self.high = low, high
        self.profile = LayeredIntensityProfile(GUST_BANDS, rng=rng, clock=clock,
                                               layer_ft=layer_ft, blend_ft=blend_ft)

    def multiplier(self, value: float) -> float:
        """Map a 0-1 gustiness value onto the multiplier."""
        return min(self.high, max(self.low,
                                  1.0 + (value - GUST_MEAN) * (self.high - self.low)))

    def value(self, alt_ft: float) -> float:
        """Return the multiplier at alt_ft right now."""
        return self.multiplier(self.profile.value(alt_ft))

    def expected(self, alt_ft: float) -> float:
        """Return the long-run mean multiplier at alt_ft for its layer's current roughness."""
        return self.multiplier(self.profile.expected(alt_ft))

    def share_at_least(self, alt_ft: float, minimum: float) -> float:
        """Return the long-run share of time at alt_ft the multiplier is at minimum or more."""
        if minimum <= self.low:
            return 1.0
        if minimum > self.high:
            return 0.0
        # The multiplier is linear in the value (clipped at low/high)
        value = GUST_MEAN + (minimum - 1.0) / (self.high - self.low)
        return self.profile.share_at_least(alt_ft, value)

    def status(self, alt_ft: float) -> dict:
        """Return the layer status, with from/target/value as multipliers."""
        st = self.profile.status(alt_ft)
        return {**st, "from": self.multiplier(st["from"]),
                "target": self.multiplier(st["target"]),
                "value": self.multiplier(st["value"])}
