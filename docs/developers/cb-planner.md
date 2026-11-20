# CB placement planner (fw_planner.py)

Status (2026-11-19): in frankenweather behind `--cb-planner` (with `--radar-cbs`),
being tested. It replaces frankenweather's separate storm placer, SIGMET placer
and the radar part of zone placement with one scored decision every minute.
The rules were agreed afresh with the user on 2026-11-19 (below); the earlier
version tried to make sims flying together show the same CBs, which is no
longer a goal (see History).

## Data

frankenweather passes the planner:

- **Storms:** RainViewer radar cells (43 dBZ and more, inside radar coverage,
  moved on by their tracked motion) and, outside radar coverage, frankenweather's
  made-up storms near MTG lightning and inside TS SIGMET areas. Per storm: its
  position, peak dBZ, area, the MTG lightning flash pixels of the last 15
  minutes within 10 nm, whether the crew would see a new CB there, and (with
  `--route-zones`) its distance from the route ahead.
- **Zones:** which storm each zone's CB is on; fixed (the anchor, airports,
  PSX's focused zone, CB-free zones, a CB whose turbulence reaches the
  aircraft); whether the crew may see its CB; a CB it shows without a storm
  (a dead storm's CB kept in view, a model CB).
- **The aircraft:** position, heading, holding or vectored.

What the crew may see comes from `--cb-visible-dist` and PSX's two NDs (range,
mode, WXR and TERR). The CB's size, once placed, comes from the radar and the
Meteosat cloud top heights as without the planner.

## Rules (user, 2026-11-19)

1. **Don't startle the pilot**, as a cost that grows the closer a CB is,
   not a wall. The problem it came from: CBs appearing or going 10-30 nm
   away on approach.
   - Within 40 nm (60 nm for a big CB: 6/8 or more, or tops above 40,000 ft;
     `--cb-startle-dist`) no CB appears, disappears or moves, in view or not.
   - From there out to what the crew may see, a CB may appear, go or move if
     the new placement is clearly better: by 3 times at 60 nm, 1.5 times at
     100 nm, down to 1.2 times at the edge of what the crew may see
     (`CHANGE_COST`, `EDGE_COST`). A CB appearing in view must also be worth
     at least that times a 45-50 dBZ storm (`APPEAR_BASE`): for a free zone
     the alternative is no CB.
   - A CB the crew may see changes at most once in 10 minutes
     (`CHANGE_GAP_S`).
   - Beyond what the crew may see, a clear gain will do (50 % against
     flapping, `HIDDEN_KEEP`; 10 % let zones swap storms whose scores moved
     between frames: plansim on recorded radar 2026-11-20, zone moves
     4.3/h with 10 %, 3.8/h with 50 %, the same coverage). A zone whose CB
     the planner turned off (too close to another CB) stays CB-free until
     it moves: a storm near it gave it a CB again every minute (CBs living
     under 90 s 380-420 nm out, headless 2026-11-20).
   - What the crew may see: out of the window within `--cb-visible-dist`
     (200 nm) and `--cb-visible-angle` (120 degrees of the nose), and on an
     ND showing the weather radar (its range, up to 320 nm). The user will
     test how far a CB really startles, in MSFS and on the PSX ND; the
     numbers are settings for that.
1. **CBs where the world thinks they are:** strong radar returns; without
   radar, lightning, then (later) the satellite's high cold cloud tops, then
   TS SIGMETs, and the forecast only as the last fallback.
1. **Sims in the same area showing the same CBs: no longer a goal** (20-50
   PSX sims on VATSIM; two in the same area almost never). Maybe a Flight
   Centre add-on later. The plan is still deterministic.
1. **Each area of storms gets a CB first**, but only areas at least 25 %
   (`WEAK_CLUSTER_FRAC`) as intense as the most intense one, the intensity
   being the sum of its storms' scores (dBZ, area, lightning). A small weak
   area next to a large intense one only gets zones left over.
1. **More CBs where there is more activity.**

Also agreed: new CBs may go up to 120 nm past what the crew may see
(`_CB_NEW_BEYOND_NM`; every zone whose CB the crew can't see is movable, so
a stronger storm takes a weaker one's zone before it comes into view);
zones follow the FMC route by default (`--route-zones`, now on;
`--no-route-zones`); in HDG SEL, with the selected heading more than 5
degrees off, the planner plans for the heading after the turn, and no new
CB goes where the crew will see it on the roll-out either.

## How it decides

- **Clusters:** storms within 25 nm of each other, chained, are one cluster.
- **Score:** the storm's strength (dBZ class, 43-44 lowest, 65 highest; more
  for a large area and for lightning, in steps) times its relevance (ahead
  within 75 degrees, or within 60 nm of the route ahead, fully; abeam half;
  behind almost nothing; holding or vectored, all round), times a quarter
  for each CB its cluster has already (`DIVERSITY`; a weak cluster starts as
  if it had six), times the spacing (no CB within 25 nm of another, less up
  to 45 nm: PSX shrinks a CB's echo near another zone).
- **Best first:** each step takes the most valuable of keeping a movable
  zone's CB (its score times its change cost if the crew may see it, else
  times `HIDDEN_KEEP`) and a new CB on a storm (its score divided by its
  change cost if the crew would see it appear). A new CB goes to a zone
  without a CB, else to the zone whose CB is worth least, if worth less.
- **Locked:** fixed zones (the anchor, airports, PSX's focused zone), CBs
  within the startle distance, and CBs the crew may see that changed in the
  last 10 minutes. A CB without a storm (a dead storm's CB kept in view, a
  model CB) is worth `DEAD_SCORE`.
- **Nothing frankenweather would take away at once, nor what the crew
  will never meet:** no storm behind the aircraft, none more than 75
  degrees off the nose and beyond the behind limit unless within 60 nm of
  the route ahead, and while holding or vectored none beyond the
  maneuvering placement range.
- **No churn for nothing:** a zone showing a CB makes way only for a new
  CB worth at least `MIN_VALUE` (0.25: a 45-49 dBZ storm ahead whose
  cluster already has a CB or two); a zone without a CB takes any. The log
  line of each move says what the zone's old CB was worth.
- **Only the planner gives a zone a radar CB**, and a zone moved before the
  first radar frame after the start gets no forecast CB (it would follow
  whichever storm lay near it once the radar came).
- **When:** every minute on the UTC clock, on every new radar frame, and
  soon after the behind rule frees a zone.

frankenweather decides what the crew may see and passes it in
(`Storm.in_view`, `Zone.visible_cb`); the planner has no view of PSX.

## Tests

- `test_fw_planner.py`: unit tests, run by `make` (each rule above).
- `python3 -m flighttest.plansim`: synthetic flights, cruising, turning or
  holding, through made-up storms (clusters, lines, singles, `--fields`:
  big fields) or, with `--wxlib`, real radar storms recorded by
  `~/fw-weatherlib/capture.py` (every 10-minute frame, tracked and moved as
  frankenweather does). Every 30 s the rules are checked (nothing within the
  startle distance, a visible CB at most once in 10 minutes, spacing, fixed
  zones). It counts the changes the crew may see by distance, zone moves,
  CBs shown, significant clusters ahead with a CB, and strong storms in view
  without a CB while a weaker one had a CB, split into "every zone's CB in
  view" (a weaker CB took the zone) and "a zone free" (the change cost kept
  it from appearing). `--band NM` changes how far past the crew's view
  storms count; `--route` gives cruise and turn flights a route (the path
  they fly; storms get their distance from it, as with `--route-zones`);
  `--nd NM` fixes every flight's ND range; `--planner FILE` runs another
  planner version.

Results 2026-11-19 (no rule violations in any):

| run | CBs shown | zone moves/h | significant clusters ahead with a CB | appearing in view/h | going in view/h |
| --- | --- | --- | --- | --- | --- |
| 300 made-up flights (900 h) | 0.8 | 1.9 | 45 % | 0.36 | 0.02 |
| 300 with big fields (900 h) | 1.5 | 5.5 | 43 % | 0.94 | 0.55 |
| old planner, made-up (180 h) | 0.4 | 1.1 | 27 % | 0 | 0 |

Real radar storms (9 recorded areas, 2-hour flights): how far past the
crew's view storms count made little difference: 54 % (60 nm), 57 %
(120 nm) and 56 % (180 nm) of the significant clusters ahead had a CB.
A strong storm missed because a weaker CB had the only zone happened 0-0.13
times an hour; almost all strong storms in view without a CB (about 6 an
hour, in fields of 100 and more storms) were ones the change cost kept from
appearing where the crew could already see them. So the startle limits are
what decides how many storms get a CB.

First live A/B (2026-11-19 22:07Z, Biscay, 90 minutes, FMC route loaded):
no errors, but 83 planner moves against 16 storm placer moves without the
planner, and 22 CBs gone within 90 s against 4, all 235-410 nm away and
mostly 80-107 degrees off the nose. With a route, every storm had a
distance from it, so far-abeam storms escaped the behind-limit filter; the
zones then moved every minute between leftover storms scoring 0-0.4.
Both fixed (the route filter, `MIN_VALUE`). With `--route` on real radar
Two more live bugs came out of the next runs. A zone checked whether it
still had its storm by measuring from the storm's position in its radar
frame (up to 30 minutes old, 20 nm for a 40 kt storm), so it lost the
planner's storm at once, took the nearest cell, and the planner moved it
back a minute later; now measured from where the storm is now. And a
planner-placed zone skipped the "no new CB in view" check for 300 s even
after the maneuvering redistribution had moved it elsewhere (a forecast CB
appeared 85 nm ahead); now only while the zone is within 30 nm of where
the planner put it. Q1 (the same Biscay flight, both fixes but the last):
30 planner moves in 90 minutes (R1 83; the storm placer without the
planner 17), no errors, one CB appearing in view (the redistribution
bug), CBs per message 5.9 against 5.4.

plansim's flights then still started at random within the recorded areas
and mostly flew out of them (1.3 CBs shown; live 5.9); now cruise and turn
flights start near an area's edge heading across it (`--nd 320` as the
live tests): 3.6 CBs shown, and the fix took zone moves from 18.7 to 14.5
an hour and CBs going in view from 2.6 to 2.0 an hour, with 60 % of the
significant clusters ahead covered (58 % before), no rule violations.

## History

The first planner (2026-11-18) and the work of 2026-11-19 tried to make two
sims flying together show the same CBs (one storm list for every sim,
replanning on the UTC clock, near-equal scores counted as equal, lightning
fetched on the clock). Repeat runs of the same code differed by 30 points
and more (planner 57 % and 25 %, the code without it 40 % and 39 % of the
CBs both crews could see within 1 nm), and the user dropped the goal. The
fixes that help every sim (radar tiles and coverage tiles asked for again,
the tile cache, lightning on the clock) stayed.

### Headless A/B tests (2026-11-17/18)

Stack A with `--cb-planner`, stack B without, the same flight and weather
(`--radar-cbs` on both). Per run: new CBs first seen where the crew could
see them, CBs gone while visible (within 200 nm and 90 degrees), new CBs gone
again within 90 s, and the largest jump of a CB within 90 s.

| run | new in view A/B | gone visible A/B | short-lived A/B | max jump A/B | CBs shown A/B |
| --- | --- | --- | --- | --- | --- |
| Y1 Europe, ND 320 | 1 / 0 | 0 / 0 | 0 / 2 | 1.0 / 6.8 nm | 4.8 / 4.9 |
| Y2 Europe | 0 / 0 | 0 / 5 | 2 / 1 | 1.1 / 2.0 nm | 5.0 / 6.5 |
| Y3 US west coast | 0 / 0 | 6 / 1 | 0 / 0 | 0.8 / 2.1 nm | 4.3 / 4.5 |
| Y4 Europe, 2 h | 0 / 0 | 4 / 2 | 0 / 1 | 1.5 / 8.8 nm | 3.6 / 3.6 |
| Y5 hold | 0 / 1 | 1 / 1 | 0 / 0 | 0.0 / 1.0 nm | 4.7 / 3.8 |
| X5 route turn (before the holding fix) | 1 / 0 | 4 / 4 | 3 / 0 | 1.2 / 2.2 nm | 4.3 / 5.1 |

No errors. Four of Y3's six "gone while visible" are model CBs the
ordinary zone update removed 6 minutes after the start (no showers, no
radar), before the planner had any movable zone; Y4's four are drifting CBs
at 87-89 degrees off the nose that the behind rule took away (both modes do
this).

Pair tests (both sims with the planner, 8 nm apart in trail): 25 % (cruise)
and 33 % (Y6) of the CBs both sims could show had the other sim's CB within
1 nm. Earlier pair tests without the planner, mostly 0-2 nm apart, had 60-94
%. Not yet comparable; seen so far: the two sims replan at different times
(zones freed by the behind rule a minute apart), and a storm's key carries
the frame in which each sim first saw it, so ties may break differently.

2026-11-19, 2 h pairs from the same start (8 nm in trail, ND 320 nm), with
the per-zone radar rule fix and the radar tile retry: storm keys turned out
the same in both sims (the same frames), and many moves were the same
storm at different times; what differed was which storms each sim caught in
the narrow band where new CBs may go (see "When it plans"). Z5 (frames
only): 22 % overall, 43-57 % in minutes 30-50, then 0 %. Z6 (current code,
no planner): 40 % within 1 nm, 95 % within 30 nm. Z7 (replans every
minute): 34 %, 0 % for the first 80 minutes, then 93-100 %; one sim had no
radar storms for its first 8 minutes (its radar coverage tiles had not
come and were not asked for again until the next frame) while the other
filled its zones, and the zones' history (`STABILITY`) kept them apart
until the storms had passed. Coverage tiles are now asked for again a
minute later, like the frame's tiles. Z9 (everything: the list for every
sim, near-equal scores, lightning on the clock, the coverage retry): 57 %
within 1 nm (Z6 40 %), 61 % within 30 nm (Z6 95 %); no new CB in view, no
errors; 100 % from minute 90. What differed was the start: each sim's own
zone layout and forecast CBs, two of which (in one sim) sat near weak
radar cells (44-46 dBZ) and stayed while the crew could see them; the
other sim may not put a new CB there. The current code matches less
exactly (its CBs sit on the zones near a storm) but nearly always within
30 nm. Z10 (the same code again, 4 hours later): 25 %, 0-11 % for the
first 40 minutes. Before the first radar frame (1-2 minutes after the
start) each sim moved its zones 140-230 nm ahead, each to its own places,
with forecast CBs; when the radar came, each of those CBs followed the
storm nearest to it and stayed as the crew could see it. Now, with the
planner, a zone moved before the first radar frame gets no forecast CB;
only the planner gives it one.
