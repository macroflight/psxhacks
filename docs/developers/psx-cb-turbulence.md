# PSX's own CB turbulence (measured)

PSX makes its own turbulence around the CBs it draws on the weather radar,
on the same WxBurst channel (Qi141) that frankenweather uses. The total a
pilot feels near a CB is PSX's turbulence plus frankenweather's. The goal
for tuning frankenweather's CB turbulence: add only enough on top of PSX's
to reach the total we want at each distance from the CB core, inside and
outside the radar echo (see the turbulence realism goal: CB core 55-70 %
is the pilots' sweet spot, rough but rarely severe).

This page records what PSX does on its own, measured on the headless test
sims with frankenweather off.

## How it was measured (2026-11-16)

- PSX 10.195, headless stacks A and B, frankenweather not running.
- FL300 over the open ocean near N30 W150, about 470 kt TAS, autopilot
  in HDG SEL and ALT HOLD.
- One weather zone (zone 1) with a CB from 3,000 to 35,000 ft; the other
  six zones parked far away, planet weather without CBs, no wind corridor.
- PSX draws a zone's CB 7 nm from the zone centre on a bearing that turns
  once an hour. The zone was written once per pass, aimed so the CB sat
  on the wanted spot when the aircraft passed it; the CB's position
  between writes followed PSX's orbit formula.
- Each pass started with the CB 30 nm ahead and ended 15 nm past it.
  Every WxBurst PSX sent was logged with the aircraft's distance from the
  CB centre. Codes above 900 are lightning, not turbulence.
- Scripts and raw data: `~/psx-headless/tests/cbturb-20261116/` on c1
  (`cbturb.py`, `cbwind.py`, `analyse.py`, `*/events.jsonl`).

CB sizes are PSX's radar echo, measured on the ND on 2026-11-15 (see
[frankenweather-cb-messages.md](frankenweather-cb-messages.md)): a big CB
(3/8 or more, at least ~12,000 ft deep, no other zone near) is about
6 nm red and 18.6 nm amber+red across.

## Results

### Big CB (8/8, 3,000-35,000 ft, alone), through the core

Wind at FL300 284/28 (PSX's own planet weather), eastbound.

- 247 turbulence bursts and 14 lightning flashes in about 28 nm.
- First burst 13.9 nm before the CB centre, last 14.0 nm after it: about
  14 nm all round in this light wind, roughly 4-5 nm beyond the amber
  echo's edge (radius ~9.3 nm).
- Bursts by distance from the centre (nm: count): 0-2: 58, 2-4: 29,
  4-6: 91, 6-8: 14, 8-10: 19, 10-12: 21, 12-14: 14, 14-16: 1.
- Kinds: sink/balloon 138, yaw 56, bank 35, speed 18. Strength (value
  mod 100) median 10, max 63; within 3 nm median 10, beyond 8 nm median
  31. Max bank 5.1 degrees. Altitude stayed within a few feet.

### All CB sizes, passes 0-16 nm to the side

Four CBs, each flown past at 0, 3, 6, 9, 12 and 16 nm from its centre
(eastbound, light wind 284/28): big (8/8) and small (1/8), each alone and
with a CB-free neighbour zone 7 nm from its zone centre (PSX's echo then
shrinks to ~30 %). Turbulence bursts per pass (lightning not counted):

| closest | big | big, neighbour | small | small, neighbour |
| --- | --- | --- | --- | --- |
| 0.5 nm | 247 | 243 | 247 | 249 |
| 3.4 nm | 293 | 252 | 249 | 249 |
| 6.4 nm | 153 | 198 | 114 | 106 |
| 9.4 nm | 92 | 99 | 84 | 98 |
| 12.4 nm | 54 | 0 (*) | 62 | 0 (*) |
| 16.4 nm | 0 | 0 | 0 | 0 |

(*) On these two passes the aircraft was inside the neighbour zone the
whole time (FocussedWxZone 2, no CB), not inside the CB's zone.

What that shows:

- **PSX's CB turbulence reaches exactly ~14 nm from the CB centre, in every
  direction, whatever the CB's size.** First and last bursts were at
  13.7-14.4 nm on every pass that had any. Oktas (1/8 or 8/8) and PSX's
  echo size (full or shrunk to ~30 % by a near zone) made no difference.
  The radar echo of a big CB ends at ~9.3 nm, a small one's at ~5 nm, so
  PSX's turbulence reaches well beyond the amber.
- **Only the CB of the zone the aircraft is in counts.** The same pass that
  gave 54-62 bursts 12 nm from the CB gave none when the aircraft was inside
  a neighbouring CB-free zone. Where the zone boundary (halfway between zone
  centres) falls matters as much as the distance.
- **Inside ~6 nm: sink/balloon bursts as well.** About 138 sink bursts on
  every pass within 3.4 nm, some at 6 nm, none beyond. Bank, yaw and speed
  bursts come all the way out to 14 nm.
- **Strength:** value mod 100 median 9-10 inside 6 nm (many small sink
  bumps), 17-34 farther out; maximum 49 everywhere except a few 63-67 in the
  core passes. Max bank 3-6 degrees inside 6 nm, 1-2 degrees beyond 9 nm;
  altitude within a few feet.
- **Rate**, bursts per minute by distance band (all four CBs, rough: 20-60 s
  of flying per band per CB): 0-6 nm 40-170/min, 6-8 nm 40-70/min,
  8-14 nm 20-35/min, beyond 14 nm none. Per-band numbers in
  `summary.json`.
- PSX's turbulence repeats: the same sim time and path gave nearly the same
  bursts (247/138 sink on both stacks with different CBs).
- Lightning flashes (codes 901-909) out to ~26 nm.

### The radar echo's shape and the wind

Same setup, aircraft frozen (SimPaused=2) at FL300, the CB 22 nm away 20
degrees left of the nose, ND MAP 40 nm; the amber+ area fitted with an
ellipse (scripts in `~/psx-headless/tests/cbshape-20261116/`). Each wind
was written three times, interleaved, to tell the wind's effect from
chance; the same wind gave the same shape every time.

- PSX stretches the amber echo **across** the zone's surface wind: long axis
  about 90-100 degrees from the wind direction (wind from 000: axis
  97-103; 045: 138-151; 090: 7-14; 135: 42-54). Symmetric, both ends alike.
- Only the **direction** of the zone's **surface** wind (Wx field 18)
  matters: speed 10-99 kt made no difference, and "calm" (direction 000,
  0 kt) looks like wind from 000. The tropopause wind (WxAloft), 20-200 kt
  from any direction, changed nothing, also when crossed with the surface
  wind.
- Long/short about 1.7-2.7 (amber extent ~25-35 x 13-17 nm, speckled edges
  included). Measured from one viewing angle only, so part of that ratio
  is the radar beam smearing the echo sideways; the red core looked
  stretched across the line of sight (~165 degrees true) in every case,
  which is that smearing, not the wind.

### The green around the echo (no rain, no cloud layers)

Same frozen setup, the zone with only a CB (no cloud layers, no rain),
ND at 40 nm (CB 22 nm away) and 80 nm (CB 40 nm away), share of the ND
area in each colour per 2 nm ring from the red core's centre:

| from centre | big 8/8: amber / green | small 1/8: amber / green |
| --- | --- | --- |
| 0-4 nm | red core (~3 nm radius) | red core (~2 nm) |
| 4-6 nm | 0.89 / 0.01 | 0.04 / 0.81 |
| 6-10 nm | 0.3-0.7 / 0.2-0.6 | 0.1-0.2 / 0.8 |
| 10-18 nm | 0.0-0.2 / 0.6-0.9 | 0-0.1 / 0.8-1.0 |
| 18-22 nm | - / 0.6-0.8 | - / 0.6-0.8 |
| 22-34 nm (80 nm map) | - / 0.5 falling to 0.25 | - / 0.5 falling to 0.27 |

- With no CB (same zone, oktas 0) the radar is black. All this green
  belongs to the CB.
- **Solid green reaches ~18-20 nm from the CB centre for every CB, big or
  small.** Speckled green reaches 30+ nm. Only the red and amber scale
  with the CB (amber to ~9 nm big, a few patches small).
- A crew avoiding solid green (a real 747 pilot's practice: uncomfortable
  for passengers but not dangerous; amber is where danger starts, red and
  magenta definitely avoided) keeps ~20 nm from any PSX CB. That matches
  the FAA's 20 nm guidance and is beyond PSX's 14 nm turbulence reach.
- Scripts and shots: `~/psx-headless/tests/cbshape-20261116/Bgreen*`
  (`CASESET=green`).

### Upwind vs downwind (wind about 270/60 at FL300)

Zone surface wind 270/30, tropopause 270/80, big CB alone. Bursts on the
CB's upwind (west) and downwind (east) side:

| pass | upwind side | downwind side |
| --- | --- | --- |
| core, eastbound (GS 505) | 138 | 142 |
| core, westbound (GS 372) | 145 | 187 |
| 6 nm, north- + southbound | 201 + 202 = 403 | 245 + 151 = 396 |
| 10 nm, north- + southbound | 89 + 85 = 174 | 90 + 99 = 189 |

- The reach is the same on both sides (13.9 nm on every pass): the wind
  doesn't move or stretch PSX's turbulence area.
- The numbers are about even once the heading is averaged out (the
  westbound core pass had more because it spent ~36 % longer near the CB
  at its lower ground speed). Strength median 10 on both sides of the core.
- In 60 kt of wind the core passes had a little more turbulence (280-332
  bursts) than in light wind (247).

### Not measured yet

- Other altitudes (only FL300 so far) and CB tops below the aircraft.
- Whether PSX uses the zone's wind or the aircraft's for anything here
  (the turbulence ignored the wind; the radar echo's shape follows the
  zone's surface wind direction, see above).
