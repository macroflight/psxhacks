# Turbulence calibration (2026-10-25)

How strongly PSX moves the 747 for frankenweather's turbulence, measured
on a test sim: what a single `WxBurst` does, and what frankenweather's
intensity (%) gives as vertical acceleration, compared with how pilots
report turbulence. Measured with `psx_flighttest.py`
([flighttest.md](flighttest.md)); the full report, data and scripts are
in PAIB/2026/017 (Part 2). Nothing here has changed frankenweather yet.

## How it was measured

- **Vertical acceleration** from the raw PSX boost feed (about 13 Hz,
  altitude in 1/100 ft, PSX's own millisecond clock): the altitude's
  second difference over half a second either side. frankenboostproxy's
  5 Hz accelerations are interpolated from position data and smooth the
  vertical g about 5-10 times too low, so they were only used as a trend.
- **Pilot report bands** (largest deviation from 1 g, the commonly quoted
  figures): light from 0.05 g, moderate 0.2 g, severe 0.5 g, extreme
  1.5 g. The 95th percentile (p95) of the deviation is used as "the
  typical strong bump". EDR was not measured.
- **Single bursts**: frankenweather's turbulence off, single sink, bank,
  yaw and speed bursts of size 5-99, both signs, repeated, in random
  order, 15-25 s apart, each compared with the 2 s before it.
- **frankenweather sweeps**: manual turbulence (kind mechanical) at 0-100%
  in 14 steps, 100 s each after 15 s to settle.
- **Conditions**: 3,000 ft and 280 kt with GW 319.6 t; FL310 at Mach 0.82,
  319.6 t; 3,000 ft with GW 244.9 t (Instructor Station). Autopilot (CMD,
  HDG SEL, ALT HOLD) and autothrottle on, over the North Sea, frankenweather's
  SIGMET preset Off (no CBs from SIGMETs embedded by PSX).

## What a single WxBurst does

| Sink burst size | 20 | 35 | 50 | 70 | 99 |
|---|---|---|---|---|---|
| 3,000 ft, 319.6 t | 0.08 | 0.19 | 0.34 | 0.68 | 1.26 |
| FL310, 319.6 t | 0.06 | 0.18 | 0.33 | 0.65 | 1.24 |
| 3,000 ft, 244.9 t | 0.07 | 0.18 | 0.35 | 0.64 | 1.24 |

(peak vertical g change; below size 20 it is close to the background.)

- From about size 30 the g grows with the square of the size, about
  1.25 x (size/99)^2 g.
- **PSX's response doesn't depend on airspeed, altitude or weight.** A
  real gust gives more g at a higher airspeed and a lower weight.
- Only sink bursts make vertical g. Size 99 rolls the aircraft about 7
  degrees (bank), turns it about 2.5 degrees (yaw) or changes the TAS by
  about 9 kt (speed); these scale roughly with the square of the size too.

## What frankenweather's intensity gives

frankenweather injects a burst with probability I^1.5 every 200 ms
(about 300 x I^1.5 per minute) of a random size from 1 to I x 99, on a
random channel. p95 vertical g measured:

| % | 3,000 ft, 319.6 t | FL310, 319.6 t | 3,000 ft, 244.9 t |
|---|---|---|---|
| 0 | 0.007 | 0.003 | 0.003 |
| 10 | 0.016 | 0.018 | 0.028 |
| 20 | 0.036 | 0.023 | 0.029 |
| 25 | 0.048 | 0.059 | 0.096 |
| 30 | 0.115 | 0.072 | 0.059 |
| 40 | 0.182 | 0.243 | 0.201 |
| 50 | 0.242 | 0.332 | 0.226 |
| 60 | 0.431 | 2.144 (upset) | 0.340 |
| 70 | 0.545 | 1.687 (upset) | 0.531 |
| 80 | 0.789 | 0.669 | 0.812 |
| 90 | 0.990 | 1.964 (upset) | 0.938 |
| 100 | 1.350 | 2.448 (upset) | 1.311 |

The p95 reaches the pilot report bands at about:

| Band | 3,000 ft, 319.6 t | FL310 | 3,000 ft, 244.9 t | frankenweather's own label |
|---|---|---|---|---|
| light (0.05 g) | 25% | 24% | 22% | light from 10% |
| moderate (0.2 g) | 43% | 37% | 40% | moderate from 25% |
| severe (0.5 g) | 66% | 51% | 68% | severe from 50% |

- **frankenweather's labels are about one category too strong below
  50%**: its "light" (10-25%) is practically smooth, and the 30%
  low-level wind shear limit is light chop.
- **At cruise, 60% and more can upset the aircraft**: at FL310 the
  autopilot repeatedly lost the altitude by up to about 2,000 ft with
  the pitch between about -10 and +28 degrees, and the speed bled off
  (to about 360 kt TAS at the end). The bumps themselves are no bigger
  than at 3,000 ft, where the same levels stayed under control. It comes
  in episodes (80% happened to stay calm).
- In every sweep the true altitude sank by about 100 ft per level, also
  in smooth air, flying south on ALT HOLD -- probably towards lower
  pressure (not checked).

## Next steps (not done)

- An **opt-in burst profile** that maps frankenweather's % onto these
  bands (about 10 -> 27, 25 -> 45, 50 -> 67, 75 -> 90), keeping the mix of
  burst channels so seat shakers keep their balance, and perhaps scaling
  with weight and airspeed like real gusts.
- `WxBurst` also drives seat shakers (the Vibrate addon with a ButtKicker):
  any new profile needs a test in the real sim's seat, then Vibrate's
  sliders adjusted to it.
- Whether severe turbulence at cruise should be able to upset the
  aircraft this much.
- Real-world figures (pilot report band, maybe EDR) next to the % on the
  router's /efb page.
- Mechanical turbulence in an ordinary strong wind is weak: 7% at
  Copenhagen in a 28 kt wind gusting 38 (practically smooth).
