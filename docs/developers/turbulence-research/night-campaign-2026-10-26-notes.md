# Night campaign findings (2026-10-25/26)

## F1: false "changed by another client" warnings (1.27.0) -- WxMode tolerance too tight

- PSX rounds zone positions to ~8 significant digits: sent 0.25237693;2.87744964 comes
  back 0.25237694;2.8774498 (lon diff 1.6e-7 rad). `_WXMODE_POS_EPS_RAD` = 1e-7, so
  `_wxmode_equivalent()` says "different".
- Effect since 1.27.0: on zone placements a WARNING "WxModeN changed by another
  client" + a restore (the same value written again), 2x per placement; 19 such
  warnings in block 1 (21:10-22:35). Also psx_send_and_set()'s skip never matched for
  those zones (older, harmless). Safety valve could trip if one zone is placed 3x in
  2 min (e.g. maneuvering mode).
- Fix suggestion (not applied): `_WXMODE_POS_EPS_RAD = 1e-6` (~6 m).

## F2: SIGMET random intensity makes a SEV area anything from glassy to upset

- SEV TURB LFMM U03 FL320: stable leg 55 % (p95 0.53 g, max 2.1 g, 4 V/S swings in
  ALT HOLD), testing leg 4 min later 10 % (p95 0.027 g).
- SEV MTW EGTT 11 FL100 stable: up to 66 %, p95 0.49 g, max 1.41 g, 3 V/S swings
  (level flight); testing capped 20 % (lift cap), p95 0.05 g.

## F3: reposition into a TC/TS area at cruise: calm for minutes (stable profile)

- KZAK TC FL350: all 7 zones got TC CBs, but the 3 within 100 nm capped at 2 oktas /
  15,000 ft (1.25.0 close-CB rule) -> nothing at FL350, turbulence "none". Normal
  flight is fine (zones ahead placed >100 nm). Testing adds the area's own 25 %.

## Harness notes

- Raw boost relay 10749 dead after the sim restarts; boost server now on 20749 (user).
- Stability heading tolerance 10 deg too small for cruise crosswinds (KZWY/NZZC/FAJO
  needed 15-24 deg drift): runner now 40 deg (from block 2). Re-fly those pairs.

## F4: Glasgow case reproduced fresh, testing profile holds (block 2)

- EGPX 13 MTW, FLCH FL230->FL80. Stable: up to 74 %, V/S -3673 fpm, 6 swings >2000,
  p95 0.31 g. Testing: up to 34 %, V/S -2249, 1 swing, p95 0.04 g, 8 bursts/min (54).
- EGPX V/S -1500: stable no swings (V/S -2572..+35) but p95 0.23 g; testing -1520..-11.
- EGTT FLCH stable at ~1 % still had 1 swing to -2191 (FLCH + PSX's own gusts/wind):
  baseline noise for the swing count.
- EGTT FLCH testing at 22-46 %: no swings, p95 0.07 g.
- KZWY TS FLCH: stable "none" (F3 again), testing area 14-24 %: 1 borderline swing.

## Harness: FL390 too high for the situ's weight

- FAJO K01 FL390: TAS 441 kt, stalled in the 14 deg track-correction turn, spun in
  (alt 21,000 ft at record start, sea at ~23:44). Not frankenweather (11-21 %). The
  harness recorded anyway ("UNSETTLED after correction"); the runner now re-checks
  stability after the correction and skips the leg. FAJO testing leg not flown.

## Block 3 hops (12 repositions, 00:10-00:28): restore works, F1 noise

- Every situ load: zones reinitialized ("Aircraft repositioned"), our 21 zone vars
  restored ("changed by a situ load"), 91 initial placements, no ERROR/Traceback, no
  safety-valve trip.
- 32 "changed by another client" warnings, all F1 (own WxMode with PSX's 8-digit
  rounding), 2 per affected zone placement.
- ENOR FLCH stable (recomputed, midnight leg): 9-35 %, 12.4 bursts/min, p95 0.08 g, no
  swings; testing 13-41 %, p95 0.05 g, no swings.

## F5: 30 min at FL350 inside EMBD TS areas (Brazil): no CB turbulence at all

- B3_LONG_SBAZ (testing), 00:29-00:59, ~250 nm north from S14.7 W058.9: only the TS
  area's own turbulence (18-24 %, p95 0.03 g, 10 bursts/min), never any zone CB
  turbulence. Zones relocated "inside TS SIGMET" were placed 290-300 nm ahead, the CB
  zones (5 oktas, tops ~44,000 ft) ended up at S08-S09, not reached. Track never
  within a CB's turbulence radius (core = half the distance to the next zone + flank
  <= 20 nm).
- So with the stable profile a long flight through a big TS area is glassy unless
  the track happens to pass a zone CB: the area turbulence (testing) is what makes it
  feel like a TS area. Close-CB rule behaved (kept existing 5-okta CBs, capped one new
  one to 2 oktas/15,000 ft); no CB pops seen, no warnings.

## Maneuvering mode (box hold FL150 in KKCI TS area, 90 deg/min turns)

- Entered at 01:04:10 after 181 deg in 290 s ("zones redistributed"), zone updates
  [MANEUVERING] after; no warnings/errors. CB/CAPE turbulence up to 49 %, p95 0.01 g.

## Block 4 boundary layer (testing) vs stable, daytime Australia/Japan

- Stable at 1,500-3,500 ft: glassy everywhere (0 bursts, p95 0.003-0.004 g).
- Testing: Perth 14-15 % p95 0.019 g; Sydney 19 % 0.024 g; Brisbane 17-19 % 0.022 g;
  Alice Springs 23-24 % 0.034 g (wind 13 kt, sun 965 W/m2). Light chop, never more.
- Tokyo (overcast 96 W/m2, wind 14 kt, BL top 475 m): nothing at 2,200 ft -- above
  the layer's fade-out (1,870 ft), correct.
- F6 (doc nit): "at most 20 %" isn't true: the low-level gustiness (x0.5-1.2) applies
  on top, Alice Springs 0.20 x 1.13 = 23 %. Either cap after gustiness or say ~24 %.

## F7: same-direction sink bursts compound (MTW, both profiles; testing still to 1.6 g)

- NZZC 6 MTW FL250. Stable 27-46 %: 54 bursts/min, p95 0.69 g, max 1.5 g, 4 V/S
  swings in ALT HOLD -- far more g per % than TURB (YMMM B01 47 %: p95 0.22 g).
  Testing 41-46 %: 18.7 bursts/min (1/3), no swings, but p95 0.41 g, max 1.6 g.
- The big g (163 samples > 0.5 g in testing) sit on clusters of same-sign sink bursts
  (-41, -31 within 1 s; -28, -34, -25 within 4 s). A single burst that size is ~0.2 g
  by the calibration (1.25 (s/99)^2); back to back they add up.
- Testing's sink direction follows the sign of the wave's rate of change, constant
  for half a wave cycle, so clusters are one-sided. Ideas (not done): minimum spacing
  between sink bursts, or limit consecutive same-sign sinks / their summed size.

## Evaluation caveat

- "FW level p50/max" covers the whole leg incl. ~1-2 min load/settle, bursts/min and
  g only the recording: trust bursts and g, the level is indicative.
- KZWY HOTEL 4 pair: track 270 left the 77-nm-deep area within seconds -- not
  comparable.
- SEV TURB at cruise vs level (stable+testing, same treatment): 10 % p95 0.03 g,
  31 % 0.16, 44-47 % 0.22-0.24, 55 % 0.53 (max 2.1 g), 61 % 0.46 (3 swings).
