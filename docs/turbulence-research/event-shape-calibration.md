# Turbulence event shapes in PSX (2026-10-26)

How PSX's 747 answers big scripted `WxBurst` up/down bursts, measured to
set the shapes of frankenweather's turbulence events
(`frankenturb/turbulence_events.py`). Test sim, FL350 near N57 E003,
Mach 0.84, 319.6 t, autopilot CMD with HDG SEL and ALT HOLD,
frankenweather's own turbulence off, SIGMETs off. Vertical acceleration
from the raw boost feed (altitude second difference over ±0.5 s, as in
[turbulence-calibration.md](../turbulence-calibration.md)); Δg is the
change from 1 g. Each test 25-30 s after the one before.

## Sign and size

- A positive burst pushes the aircraft up (positive Δg first), a
  negative one down.
- Single bursts follow the earlier curve, Δg ≈ 1.25 × (size/99)²:

| Burst | -63 | -70 | +70 | -80 | +80 | -99 | +99 |
|---|---|---|---|---|---|---|---|
| First swing (g) | -0.50 | -0.61 | +0.63 | -0.79 | +0.80 | -1.18 | +1.18 |
| Rebound (g) | +0.31 | +0.38 | -0.37 | +0.50 | -0.47 | +0.75 | -0.72 |

**Size 90 is special:** -90 and +90 both give -1.29/+2.28 g, the same
whichever the sign, repeated three times; 88, 89, 91 and 92 follow the
curve (-0.95, -0.96, -1.02, -1.03 g). The events never use size 90. The
stable profile's random bursts can still be size 90 at 91% or more.

## PSX rings on after a burst

A single burst sets off a lightly damped pitch oscillation with a
period of about 3.5-4 s, each swing about 60% of the one before, over
about 8-10 s. Single -99, Δg at 0.5 s steps (±0.25 s smoothing):

```text
t (s)  0.5   1.0   1.5   2.0   2.5   3.0   3.5   4.0   4.5   5.0   5.5   6.0
Δg   -1.31 +0.44 +0.60 +0.73 +0.74 +0.53 -0.37 -1.12 -0.92 -0.37 +0.23 +0.61
```

So one big burst already gives the 2-4 big swings seen in real severe
encounters' recorder data (e.g. UA826: +0.8 g, then -1.8 g 6 s later).

## Bursts add up

| Sequence | Δg | Altitude | Pitch |
|---|---|---|---|
| +99 then -99 after 0.3 s | -0.95/+0.72 | ±8 ft | 3.2..4.1° |
| +99 then -99 after 0.6 s | -1.49/+1.19 | ±13 ft | 2.4..4.8° |
| +99 then -99 after 1.0 s | -1.67/+1.19 | ±19 ft | 1.6..6.0° |
| +70 then -99 after 0.6 s | -1.39/+0.62 | -16 ft | 2.6..5.6° |
| -99 twice, 0.2 s apart | -2.17/+1.36 | -55 ft | 1.0..9.8° |
| -99 twice, 0.4 s apart | -1.73/+1.43 | -52/+40 ft | 0.8..10.6° |
| -99 three times in 1 s | -2.12/+1.95 | ±85 ft | -0.2..14.2° |
| ±70 alternating, 1.5 s apart (4) | -0.90/+0.76 | ±13 ft | 2.3..5.1° |
| ±97 alternating, 1.5 s apart (4) | -1.71/+1.35 | ±22 ft | 0.9..6.5° |
| ±85 alternating, 3.0 s apart (3) | -2.16/+1.99 | -41/+92 ft | -4.4..9.7° |
| -80, then +80 8 s later | -0.80/+0.97 | -14/+26 ft | 0.6..5.8° |

- Two bursts the same way within half a second add up to over 2 g.
- Bursts about one ring period (3-4 s) apart pump the oscillation:
  ±85 every 3 s gave ±2 g. 8 s apart they don't add up.
- An up-burst followed 0.6 s later by a bigger down-burst gives a
  down-heavy swing with a small rebound, like the real drops.

## What the events use

- Severe cluster: big bursts of size 63-97 (0.5-1.2 g), at least 6 s
  apart (6-10 s), 60% of them down, in 45% chop.
- Extreme: an up-burst of 0.3-0.45 g, then 0.6 s later -99 (Δg about
  -1.4, nz about -0.4); one in four adds a second -99 0.2 s later (Δg
  about -2.2, nz about -1.2, SQ321 reached -1.5).

## The events in flight

Triggered at FL350 with frankenweather's testing profile, background in
clean air, autopilot ALT HOLD (40 s windows):

| Event | Planned | Δg | p95 | Altitude | Pitch |
|---|---|---|---|---|---|
| None (background) | - | -0.01/+0.02 | 0.01 | ±1 ft | 3.7° |
| Severe, 2 big bursts | 1.2 g | -0.46/+1.48 | 0.44 | -112 ft | 1.2..5.1° |
| Severe, 4 big bursts | 0.7 g | -0.67/+0.60 | 0.46 | -100 ft | 1.7..5.1° |
| Extreme, double drop | 2.2 g | -2.41/+0.71 | 0.50 | +151 ft | 2.1..5.5° |
| Extreme, single drop | 1.4 g | -1.33/+0.48 | 0.39 | +88 ft | 2.7..5.3° |

The autopilot stayed engaged and held altitude within about 150 ft.
The strongest severe cluster exceeded its plan: the chop adds to the
big bursts.
