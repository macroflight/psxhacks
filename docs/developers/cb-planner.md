# CB placement planner (fw_planner.py)

Status (2026-11-18): in frankenweather behind `--cb-planner` (with `--radar-cbs`),
being A/B tested on the headless test sims. It replaces frankenweather's
separate storm placer, SIGMET placer and the radar part of zone placement with
one scored decision per radar frame (at least every 10 minutes).

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

## Goals, in order

Agreed with the user on 2026-11-18:

1. **Don't startle the pilot.** No new CB where the crew may see it (out of
   the window or on an ND showing the weather radar), and no CB they may see
   moves or disappears suddenly.
1. **Only place CBs where there are CBs in real life** (radar storms, or
   storms made up near lightning and inside TS SIGMETs where there is no
   radar).
1. **Sims in the same area see similar CBs.** The same storms, zones and
   aircraft give the same plan; ties go by storm key and zone number.
1. **At least one CB in each distinct area of CB activity.**
1. **More CBs where there is more activity.**

## How it decides

- **Clusters:** storms within 25 nm of each other, chained, are one cluster.
- **Strength:** the storm's dBZ class (43-44 dBZ lowest, 65 highest), more for a
  large area and for lightning.
- **Relevance:** ahead (within 75 degrees, or within 60 nm of the route ahead)
  counts fully, abeam half, behind almost nothing; holding or vectored, all
  round.
- **One per cluster first:** a cluster's first CB scores fully, each further one
  a quarter of the one before, so a medium cluster gets a CB before a very
  strong cluster gets its second.
- **Spacing:** no CB within 25 nm of another, less score up to 45 nm (PSX
  shrinks a CB's echo near another zone and only the aircraft's own zone's CB
  turbulence counts, measured 2026-11-15/16). A zone the planner leaves with an
  old CB too close to a new one loses that CB (the crew can't see it, or it
  wouldn't be movable).
- **Stability:** a zone's current storm scores 30 % more, so equal storms don't
  swap.
- **Never moved:** fixed zones (the anchor, airports, the focused zone) and
  zones whose CB the crew may see. Their storms count as shown, and a CB a zone
  shows without a storm (a dead storm's CB kept in view) keeps others at a
  distance too (`Zone.cb_at`).
- **No new CB in view:** a storm marked `in_view` gets no new CB; a zone that
  already shows it keeps it.
- **Nothing frankenweather would take away at once:** no storm behind the
  aircraft (frankenweather measures how far behind, along the route if it
  follows one), none more than 75 degrees off the nose and beyond the
  behind limit (behind a minute later), and while holding or vectored none
  beyond the maneuvering placement range (every turn makes a zone farther than
  `--low-alt-dist` "behind"). Without this, a headless hold had 49 planner
  moves in 40 minutes, each CB gone within seconds. Existing CBs on such
  storms still keep other CBs at a distance.

frankenweather decides what the crew may see (window range and angle, the ND's
weather radar range, holding all round) and passes it in as `Storm.in_view` and
`Zone.visible_cb`; the planner itself has no view of PSX.

## Tests

- `test_fw_planner.py`: unit tests, run by `make` (each rule above, including
  the very strong cluster left / medium cluster right example, and that the
  plan doesn't depend on the order of the storms).
- `python3 -m flighttest.plansim`: synthetic flights through made-up storms
  (clusters, lines and singles with a 15-minute growth and decay, moving with a
  wind), cruising, turning or holding, with random ND ranges. The planner
  replans every 10 minutes; every 30 s the rules are checked: no new CB in
  view, no visible CB moved or removed (a dead storm's CB only once out of
  view), no CB placed closer than the spacing minimum, fixed zones never moved.
  1,000 flights of 3 hours (3,000 hours) take a few seconds; any failure can be
  repeated with `--seed`.

The synthetic flights also apply frankenweather's "behind" rule, and flag a
zone it takes away within 10 minutes of being placed off the nose (a
90-degree turn making zones "behind" is counted apart). Storm areas are
spread along the whole flight.

Synthetic results (2026-11-18, 1,000 flights, 3,000 hours): no rule
violations; on average 1.4 CBs shown, 2.6 zone moves an hour, and 50 % of the
clusters within 320 nm ahead have a CB (storms that form where the crew can
already see them get none: priority 1 before 4).

## Headless A/B tests (2026-11-17/18)

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
Open.
