# Overnight test campaign, frankenweather 1.27.0 (2026-10-25 21:10 - 10-26 03:04 UTC)

Test sim, router 11001, frankenweather 1.27.0 (`--debug --sigmet`, SIGMET preset
Normal), stable vs testing profile flown back to back at the same spot. 71 legs in 5
blocks: A/B pairs at live SIGMET areas, FLCH/V/S descents, 12 rapid repositions, a
30-minute cruise through TS areas, a box hold, low-level boundary-layer legs in
daytime Australia/Asia. g from the raw boost feed (port 20749). No code changed.

## Health

- frankenweather: no ERROR/Traceback/CRITICAL all night, no restart needed, no
  safety-valve trip. Open-Meteo: 3,361 weighted calls in 24 h (limit 10,000).
- Situ loads (every leg): our zone variables restored every time ("changed by a situ
  load"), zones reinitialized after every reposition.
- Maneuvering mode entered in the hold (181 deg in 290 s, zones redistributed).
- 6 legs failed, all harness: 5x the 10 deg heading check before the wind correction
  (cruise crosswinds need up to 24 deg; fixed in the runner, pairs re-flown in block 5),
  and FAJO FL390, where the 747 stalled in the correction turn and spun into the sea
  (FL390 too high for the situ's weight -- not turbulence, 11-21 %).

## Findings, most important first

1. **F1, false "changed by another client" warnings (1.27.0).** 113 overnight. PSX
   rounds zone positions to ~8 significant digits (sent lon 2.87744964, back
   2.8774498: 1.6e-7 rad), `_WXMODE_POS_EPS_RAD` is 1e-7, so our own placement looks
   like someone else's: a WARNING plus a redundant restore, 2 per affected placement.
   Could trip the safety valve if one zone is placed 3x in 2 minutes. Suggested fix:
   1e-6 rad (~6 m). Not applied.
1. **F7, same-direction sink bursts compound.** MTW NZZC 6 FL250 stable 27-46 %:
   p95 0.69 g, max 1.5 g, 4 V/S swings in ALT HOLD -- far more g per % than TURB (47 %:
   p95 0.22 g). Testing 41-46 %: a third of the bursts, no swings, but still p95 0.41 g,
   max 1.6 g: the big g sit on clusters of same-sign sinks (-41, -31 within 1 s; -28,
   -34, -25 within 4 s), each ~0.2 g alone. Testing's sink direction follows the sign
   of the wave's rate of change, constant for half a cycle. Ideas: minimum spacing or
   a cap on consecutive same-sign sinks.
1. **The Glasgow case, fresh: testing profile works.** EGPX MTW FLCH FL230->FL80:
   stable up to 74 %, V/S -3673 fpm, 6 swings >2000 fpm, p95 0.31 g; testing up to
   34 %, -2249, 1 swing, p95 0.04 g. EGTT testing 22-46 %: no swings. V/S -1500: no
   swings in either profile (stable p95 0.23 g). Note: EGTT stable at ~1 % still had a
   swing to -2191 -- FLCH plus PSX's own gusts; one swing is noise.
1. **SEV TURB SIGMETs: anything from glassy to upset.** Same treatment in both
   profiles; the random level decides. At cruise: 10 % p95 0.03 g, 31 % 0.16, 44-47 %
   0.22-0.24, 55 % 0.53 (max 2.1 g, 4 swings), 61 % 0.46 (3 swings, 133 bursts/min).
   LFMM U03: 55 % for one leg, 10 % four minutes later. Above ~50 % at cruise the
   autopilot can't hold level: worth a look whether SEV should reach that high.
1. **F3/F5, TS/TC areas at cruise are calm in the stable profile.** After a reposition
   the zones within 100 nm get small CBs (1.25.0 rule), so nothing reaches FL350 (KZAK
   TC, KZWY TS: turbulence "none"). In 30 minutes through EMBD TS areas over Brazil the
   track never came within a zone CB's turbulence radius (CB zones were placed 290-300
   nm ahead). Only the testing area turbulence (14-25 %, p95 0.03-0.08 g) made it feel
   like a TS area. Near a CB (Florida) both profiles were alike (CB 42-48 %, p95 0.24-
   0.25 g).
1. **Boundary layer (testing): light chop, as intended.** Stable glassy at 1,500-
   3,500 ft; testing Perth 14-15 % p95 0.019 g, Sydney 19 % 0.024, Brisbane 17-19 %
   0.022, Alice Springs 23-24 % 0.034. Tokyo (overcast, layer top 475 m) correctly
   nothing at 2,200 ft. F6, doc nit: "at most 20 %" isn't true, the low-level gustiness
   (x0.5-1.2) applies on top (Alice Springs 0.20 x 1.13 = 23 %).

## Follow-up (2026-10-26)

- F1 fixed in b1131ec: `_WXMODE_POS_EPS_RAD` 1e-6. Replaying the night's log, all
  113 "changed by another client" warnings count as unchanged (PSX's rounding was
  at most 2.1e-7 rad).
- F7 fixed in dfad905 (testing profile): wave/MTW up/down bursts one way within
  8 s add up to at most the largest single burst. The cause was runs of sink
  bursts 1-3 s apart pumping the autopilot's ~8 s ALT HOLD pitch oscillation
  (pitch 0-6.6 deg, AoA -1.4-7 deg, altitude only +-25 ft). Replay of the NZZC
  leg's 54 bursts at the same spot, frankenweather turbulence off:

| Replay | p95 g | max g | Pitch | V/S fpm |
|---|---|---|---|---|
| As recorded | 0.37 | 2.05 | -0.8..5.8 deg | -675..+1614 |
| With the limit | 0.09 | 0.18 | 1.9..2.8 deg | -116..+317 |

- The SEV TURB level and the general realism question led to the research in
  realism-targets.md.

## Notes on the method

- The "FW level" columns cover the whole leg including 1-2 minutes of loading and
  settling; bursts/min and g cover the recording only and are the reliable measures.
- The KZWY HOTEL 4 pair left its 77-nm-deep area within seconds: not comparable.
- ENOR stable descent crossed midnight (runner bug, fixed): recomputed 9-35 %, no
  swings, p95 0.08 g.
- Running notes from the night: night-campaign-2026-10-26-notes.md. The raw
  recordings (*.rec/*.boost/*.accel), night.log and fw.out were not kept.

## Tables

### b1_ab

| Leg | Mode | Kinds | FW level p50/max | Bursts/min | p95 g | max g | V/S min..max | Swings |
|---|---|---|---|---|---|---|---|---|
| B1_MTW_EGPX11_FL120_S | level | wave 17, mtw 7 | 9%/17% | 6.3 | 0.026 | 0.122 | -139..53 | 0 |
| B1_MTW_EGPX11_FL120_T | level | mtw 24 | 34%/35% | 11.7 | 0.087 | 0.19 | -123..65 | 0 |
| B1_MTW_EGTT11_FL100_S | level | mtw 24 | 25%/66% | 44.7 | 0.494 | 1.409 | -1841..1405 | 3 |
| B1_MTW_EGTT11_FL100_T | level | mtw 24 | 20%/20% | 23.7 | 0.048 | 0.119 | -169..139 | 0 |
| B1_TURB_EGPX12_5000_S | level | sigmet 24 | 34%/61% | 67.4 | 0.126 | 0.305 | -408..472 | 0 |
| B1_TURB_EGPX12_5000_T | level | wave 14, sigmet 10 | 16%/62% | 27.4 | 0.081 | 0.5 | -791..13 | 0 |
| B1_MTW_ENOR_10000_S | level | mtw 19, wave 5 | 37%/37% | 48.7 | 0.101 | 0.218 | -761..480 | 0 |
| B1_MTW_ENOR_10000_T | level | mtw 12, wave 12 | 14%/32% | 9.0 | 0.052 | 0.137 | -105..69 | 0 |
| B1_TURB_LFMM_FL320_S | level | sigmet 23, wave 1 | 55%/56% | 107.7 | 0.534 | 2.098 | -1880..1442 | 4 |
| B1_TURB_LFMM_FL320_T | level | sigmet 24 | 10%/39% | 10.0 | 0.027 | 0.045 | -91..205 | 0 |
| B1_TURB_KZWY_FL320_S | FAILED | B1_TURB_KZWY_FL320_S not stable | | | | | | |
| B1_TURB_KZWY_FL320_T | FAILED | B1_TURB_KZWY_FL320_T not stable | | | | | | |
| B1_TS_KKCI26E_FL350_S | level | cb 22, shear 1 | 42%/48% | 70.7 | 0.238 | 1.182 | -497..799 | 0 |
| B1_TS_KKCI26E_FL350_T | level | cb 23, sigmet 2 | 43%/48% | 75.4 | 0.252 | 0.555 | -1377..996 | 0 |
| B1_TC_KZAK_FL350_S | level | none | -/- | 0.7 | 0.009 | 0.022 | -25..18 | 0 |
| B1_TC_KZAK_FL350_T | level | sigmet 26 | 25%/25% | 30.7 | 0.082 | 0.159 | -129..518 | 0 |
| B1_MTW_NZZC36_FL200_S | FAILED | B1_MTW_NZZC36_FL200_S not stable | | | | | | |
| B1_MTW_NZZC36_FL200_T | FAILED | B1_MTW_NZZC36_FL200_T not stable | | | | | | |
| B1_TURB_FAJO_FL300_S | FAILED | B1_TURB_FAJO_FL300_S not stable | | | | | | |
| B1_TURB_FAJO_FL300_T | FAILED | B1_TURB_FAJO_FL300_T not stable | | | | | | |

### b2_descent

| Leg | Mode | Kinds | FW level p50/max | Bursts/min | p95 g | max g | V/S min..max | Swings |
|---|---|---|---|---|---|---|---|---|
| B2_MTW_EGPX13_FLCH_S | descent | mtw 29, wave 18, shear 1 | 17%/74% | 54.0 | 0.311 | 0.788 | -3673..25 | 6 |
| B2_MTW_EGPX13_FLCH_T | descent | mtw 32, wave 15 | 25%/34% | 8.0 | 0.042 | 0.157 | -2249..12 | 1 |
| B2_MTW_EGPX13_VS_S | descent | mtw 30, wave 18 | 18%/39% | 33.3 | 0.225 | 0.671 | -2572..35 | 0 |
| B2_MTW_EGPX13_VS_T | descent | wave 37, mtw 11 | 5%/39% | 3.6 | 0.019 | 0.263 | -1520..-11 | 0 |
| B2_MTW_EGTT13_FLCH_S | descent | wave 20, mtw 12 | 1%/12% | 1.7 | 0.045 | 0.096 | -2191..10 | 1 |
| B2_MTW_EGTT13_FLCH_T | descent | mtw 32, wave 13 | 22%/46% | 12.0 | 0.067 | 0.252 | -1988..13 | 0 |
| B2_TS_KZWY_FLCH_S | descent | none | -/- | 1.6 | 0.027 | 0.245 | -2236..37 | 0 |
| B2_TS_KZWY_FLCH_T | descent | sigmet 49 | 14%/24% | 13.4 | 0.065 | 0.138 | -2315..61 | 1 |
| B2_TURB_FAJOK01_FLCH_S | descent | shear 11, sigmet 9 | 11%/21% | 0.6 | 0.228 | 12.706 | -15101..0 | 2 |
| B2_MTW_ENOR_FLCH_S | descent | none | -/- | 12.4 | 0.076 | 0.199 | -1985..-64 | 0 |
| B2_MTW_ENOR_FLCH_T | descent | wave 32, mtw 16 | 13%/41% | 3.7 | 0.048 | 0.202 | -1526..17 | 0 |

### b3_robust

| Leg | Mode | Kinds | FW level p50/max | Bursts/min | p95 g | max g | V/S min..max | Swings |
|---|---|---|---|---|---|---|---|---|
| B3_LONG_SBAZ_FL350_T | level | sigmet 97 | 18%/24% | 10.0 | 0.027 | 0.11 | -291..187 | 0 |
| B3_HOLD_KKCI_FL150_T | hold | cb 36, cape 29, sigmet 2 | 3%/49% | 5.6 | 0.011 | 0.118 | -173..325 | 0 |

### b4_lowlevel

| Leg | Mode | Kinds | FW level p50/max | Bursts/min | p95 g | max g | V/S min..max | Swings |
|---|---|---|---|---|---|---|---|---|
| B4_BL_YPPH_1500_S | level | none | -/- | 0 | 0.004 | 0.009 | -68..2 | 0 |
| B4_BL_YPPH_1500_T | level | mechanical 25 | 14%/15% | 15.7 | 0.019 | 0.051 | -143..105 | 0 |
| B4_BL_YSSY_2000_S | level | none | -/- | 0 | 0.003 | 0.009 | -65..-28 | 0 |
| B4_BL_YSSY_2000_T | level | mechanical 26 | 19%/19% | 20.7 | 0.024 | 0.057 | -77..96 | 0 |
| B4_BL_YBBN_1500_S | level | none | -/- | 0 | 0.003 | 0.009 | -60..-22 | 0 |
| B4_BL_YBBN_1500_T | level | mechanical 26 | 17%/19% | 23.7 | 0.022 | 0.038 | -123..157 | 0 |
| B4_BL_RJTT_2000_S | level | mechanical 1 | 19%/19% | 0 | 0.003 | 0.008 | -70..2 | 0 |
| B4_BL_RJTT_2000_T | level | none | -/- | 0 | 0.003 | 0.008 | -52..3 | 0 |
| B4_BL_YBAS_3000_S | level | none | -/- | 0 | 0.003 | 0.007 | -75..3 | 0 |
| B4_BL_YBAS_3000_T | level | mechanical 25 | 23%/24% | 30.7 | 0.034 | 0.084 | -189..153 | 0 |
| B4_WSSS_TS_3000_S | level | none | -/- | 0 | 0.003 | 0.008 | -42..-15 | 0 |
| B4_WSSS_TS_3000_T | level | none | -/- | 0 | 0.002 | 0.008 | -32..2 | 0 |
| B4_WMKK_TS_3000_S | level | cb 1 | 30%/30% | 0 | 0.003 | 0.009 | -64..-1 | 0 |
| B4_WMKK_TS_3000_T | level | none | -/- | 0 | 0.002 | 0.007 | -34..6 | 0 |

### b5_refly

| Leg | Mode | Kinds | FW level p50/max | Bursts/min | p95 g | max g | V/S min..max | Swings |
|---|---|---|---|---|---|---|---|---|
| B5_TURB_KZWYH4_FL320_S | level | sigmet 9, cb 1 | 9%/61% | 0 | 0.01 | 0.022 | -42..61 | 0 |
| B5_TURB_KZWYH4_FL320_T | level | sigmet 10 | 42%/42% | 39.0 | 0.142 | 0.224 | -555..405 | 0 |
| B5_TURB_FAJOE01_FL320_S | level | sigmet 27 | 5%/42% | 3.3 | 0.019 | 0.041 | -106..159 | 0 |
| B5_TURB_FAJOE01_FL320_T | level | sigmet 27 | 5%/55% | 38.7 | 0.168 | 0.559 | -644..1011 | 0 |
| B5_TURB_YMMMC01_FL320_S | level | sigmet 25 | 61%/61% | 133.4 | 0.461 | 0.757 | -1903..1523 | 3 |
| B5_TURB_YMMMC01_FL320_T | level | sigmet 25 | 31%/61% | 53.4 | 0.164 | 0.475 | -360..920 | 0 |
| B5_TURB_YMMMB01_FL300_S | level | sigmet 25 | 44%/47% | 83.0 | 0.236 | 0.609 | -752..966 | 0 |
| B5_TURB_YMMMB01_FL300_T | level | sigmet 26 | 47%/47% | 97.4 | 0.219 | 0.468 | -859..1187 | 0 |
| B5_MTW_NZZC6_FL250_S | level | mtw 29 | 27%/46% | 54.0 | 0.692 | 1.513 | -1996..1139 | 4 |
| B5_MTW_NZZC6_FL250_T | level | mtw 30 | 41%/46% | 18.7 | 0.408 | 1.623 | -535..738 | 0 |
| B5_MTW_SACF1_FL300_S | level | mtw 14, wave 12 | 2%/35% | 1.3 | 0.023 | 0.054 | -114..182 | 0 |
| B5_MTW_SACF1_FL300_T | level | mtw 21, wave 4 | 3%/3% | 0.7 | 0.018 | 0.041 | -88..56 | 0 |
