# CB placement planner (fw_planner.py)

Status (2026-11-18): the planner and its tests exist; frankenweather doesn't use it
yet. It is to replace frankenweather's separate storm placer, SIGMET placer and
route storm view with one scored decision per radar frame, behind a switch at
first, then A/B tested on the headless test sims.

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

First synthetic results (2026-11-18): no rule violations in 3,000 flight
hours; on average 2.7 CBs shown, 4.8 zone moves an hour, and 36 % of the
clusters within 320 nm ahead have a CB (storms that form where the crew can
already see them get none: priority 1 before 4).
