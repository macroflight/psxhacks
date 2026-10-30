# frankenweather changelog

## Unreleased

- **Fix: during an Open-Meteo outage the enroute wind importer replaced
  the flight plan's winds with calm wind.** After a datalink wind uplink
  (or a reroute) the importer downloads the winds again; when that
  failed with nothing cached, it still sent PSX a corridor, with calm
  wind and ISA temperatures at every waypoint. Now PSX keeps its current
  corridor when Open-Meteo gave no winds at all, and a waypoint without
  data gets the nearest route waypoint's winds. After a failed download
  the importer retries in 2 minutes instead of an hour (2026-10-30: a
  single 15 s timeout right after the route was loaded), and the
  Enroute wind page says that data is missing.

## 2.1.2 (2026-10-29)

- **Fix: alongside a TS SIGMET area, a zone could hop in and out every
  minute.** With `--sigmet` a zone is moved into a nearby TS/TC area so
  its CBs show on the radar, but the spot chosen could be behind the
  aircraft, and a zone that far behind is moved away again at once: 3
  seconds later it was gone, and a minute later it was put back, for as
  long as the aircraft flew past the area. A zone is now only moved into
  the part of the area that isn't behind the aircraft.

- **Removed: the Feedback button on the weather map page.** The page
  reloads every 30 seconds, which closed the form and lost what had
  been typed, and its copy button didn't work when the page was opened
  from another computer. The `Flight:` lines in the log now show what
  was injected and why: to report a problem, send the log and the time
  (UTC).

- **Change: inside a TS or TC SIGMET area a new CB is only kept small
  within 40 nm.** Elsewhere a zone within 100 nm of the aircraft that gets
  a CB still only gets a small one (2 oktas, 15,000 ft), so that none pops
  up close ahead. Inside a TS/TC area, where CBs are expected everywhere,
  that limit cut the TS CBs of zones placed 60-100 nm away while
  maneuvering (a hold, a deviation) down to 15,000 ft. There it now only
  applies within 40 nm.

## 2.1.1 (2026-10-29)

- **Fix: the "QNH mismatch" warning flooded the log.** With MSFS sync in
  "check" mode, a QNH difference between MSFS and the PSX zone was
  logged on every cloud sync, 179 times in 40 minutes. It's now logged
  once per zone, and again only when the difference changes by 0.5 hPa
  or more, or after 10 minutes.

## 2.1.0 (2026-10-29)

- **Change: zones are spread out inside TS and TC SIGMET areas.** With
  `--sigmet`, inside a TS/TC SIGMET area the zones are now at least 70
  nm apart (otherwise 50), and the all-around placement (startup,
  reposition, maneuvering) keeps them at least 60 nm from the aircraft
  (otherwise anywhere from 0 nm). PSX draws a CB's echo, and
  frankenweather sizes its turbulence, by the distance to the nearest
  other zone: in a typhoon, packed zones gave small echoes and cores,
  and new zones 70 nm away got the small-CB limit. Spread out, the CBs
  are bigger and cover more of the area ahead. At startup the zones are
  placed before the SIGMETs are in, so if the aircraft turns out to be
  inside a TS/TC area within the first 2 minutes, they are placed once
  more with these rules.

- **Change (testing profile): inside a CB's cloud the turbulence is
  severe all the time.** The testing profile capped all continuous
  turbulence at light chop (34%) and left moderate and worse to events,
  so a CB core with no event running was calmer than its edge during a
  moderate patch: flying through a typhoon's CB core at FL340 the
  autopilot had no trouble at all. Inside a CB's cloud the turbulence is
  now held at 55-70% (scaled by the intensity sliders), with the core's
  severe events on top and, like events, without the limit on up/down
  bursts in one direction. Expect to hand-fly in a CB core. Outside CB
  cores and in the stable profile nothing changes. The testing profile's
  log labels now call up to 54% moderate (a moderate patch's 50% read
  "severe").

- **Fix: restarting frankenweather near CBs shrank them.** A CB that
  comes within 100 nm of the aircraft is kept from growing, and a zone
  that had none there may only get a small one (2 oktas, 15,000 ft), so
  that no full-size CB pops up right ahead. Right after a restart
  frankenweather didn't know which zones already had CBs, so every zone
  near the aircraft counted as CB-free: in a typhoon's CB area the
  44,999 ft CBs PSX was showing were cut to 15,000 ft until the zones
  moved. Now no zone is limited in the first 2 minutes after a start,
  while the SIGMET, METAR and CAPE CBs arrive; after that a zone near the
  aircraft keeps the CB it has by then.

## 2.0.1 (2026-10-29)

- **Fix: in the testing profile, the PIREP button said "rough" at every
  level of a TURB SIGMET.** The ride rating counted 25% as moderate,
  but in the testing profile a SEV TURB SIGMET's continuous turbulence
  is light chop at 22-34%, with moderate and worse coming as events. So
  every level came out rough while the aircraft flew through light chop
  and smooth spells. In the testing profile a level is now smooth or
  light by how often it's at light chop, moderate near a CB and rough
  inside one; a SEV TURB SIGMET's levels read light. The log's
  turbulence labels follow the same calibration (up to 34% is light, not
  moderate). The stable profile is unchanged, and so is the turbulence
  itself.

## 2.0.0 (2026-10-29)

- **Fix: with `--sigmet`, no SIGMETs at all when the US had none.**
  aviationweather.gov answers "no content" (HTTP 204) when a list is
  empty, and frankenweather took that for a failed download of both
  lists, keeping no SIGMETs, international ones included. An empty list
  is now just empty.
- **Fix: no CBs in the planet zone.** PSX gives the planet zone (the
  weather outside the 7 weather zones) 4 CB cells of its own, placed
  wherever PSX likes, with the planet zone's CB coverage. frankenweather
  could give the planet zone CBs (copying zone 1's weather into it on the
  ground, manual weather), and a situ or another client could too; they
  then showed up as unexpected CBs, with their turbulence, whenever the
  aircraft left frankenweather's zones. Now the planet zone's CB coverage
  is always 0: frankenweather never writes CBs there, and sets the
  coverage back to 0 whenever anything else does (only that field changes,
  also in flight).
  MSFS sync (clouds, QNH) no longer writes the planet zone at all, only
  frankenweather's 7 weather zones.
- **New: `--turb-profile stable|testing`** chooses the turbulence profile
  from the command line (e.g. in a start script). It overrides the
  configuration file's `[turbulence] profile`; the file only changes if
  you save the settings, and the /efb page can still switch for the
  session.
- **Removed: MSFS wind sync.** It put MSFS's wind at the aircraft into
  PSX's wind corridor as an extra `FWIND` waypoint, rewriting the corridor
  up to once a minute. Each corridor rewrite can make PSX pick new random
  wind deviations, so instead of matching MSFS it could add sudden wind
  shifts, and hardly anyone used it. The enroute wind importer (on by
  default) gives forecast winds instead. A config file with `[msfs]
  wind_sync = true` is ignored with a note in the log; if you used wind
  sync, check that `[enroute_wind] enabled` is true.
- **Fix: the wind corridor settings were resent on every refresh.** With
  the enroute wind importer on, frankenweather sent PSX's Corridor page
  selections (`Qs497`: embedded, random deviation) with every corridor
  refresh, even when PSX already had them. Now they're only sent when
  PSX's value differs, e.g. after a situ load or a change on the Corridor
  page. Rewriting them may make PSX pick new random wind deviations.
- **Fix: memory peaks of about 700 MB.** Compressing the SIGMET data
  frankenweather broadcasts to the router and the web pages used LZMA's
  strongest preset, whose 64 MB dictionary made every broadcast allocate
  about 700 MB (on Windows committed straight away). The dictionary is
  now only as large as the data (~125 KB): the same compressed size, in
  half the time, and receivers no longer reserve 64 MB to decompress it.
  Older receivers read the new messages unchanged.
- **New: the random wind/OAT deviation can be set to 0.** The enroute
  wind importer's deviation (PSX's Random slider on the Corridor page)
  now goes from 0 to 80; at 0, PSX's winds are exactly the injected
  forecast, which is unnaturally accurate for normal flying but useful
  for testing. The default stays 30. The docs also describe `Qs497`
  correctly now: its digits are "embedded", the Random slider position
  and the Format menu selection, as Aerowinx describes them.
- **Fix: frankenweather's wind corridor came back late after a situation
  load.** When another client or a situ replaced it, frankenweather
  rebuilt the same corridor but didn't resend it until the wind data
  changed (up to an hour). Now it's back within seconds.
- **New: the log shows PSX's own turbulence.** Once a minute, next to
  frankenweather's burst summary, a line counts the turbulence bursts
  frankenweather didn't send (normally PSX's own, in and near CBs, in
  gusts, wind shear and CAT) and lightning flashes, e.g. `Other bursts
  (PSX's own) in the last 60 s: 6 (SINK 1 +0/-1, GUST 4 +3/-1), size avg
  22 max 50, lightning 2`. The turbulence felt is both together.
- **Change: terrain uses half the memory.** Terrain tiles are held in
  memory as whole metres (int16) instead of float32: 26 MB a tile instead
  of 52, up to about 235 MB instead of 470 for the 9 tiles kept. Loading a
  tile no longer briefly needs about 110 MB extra. Elevations differ by at
  most half a metre; the terrain is sampled every 400-600 m. Tiles already
  downloaded are used as they are.
- **Testing profile: realistic turbulence, with rare events.** The
  continuous turbulence is now at most light chop; in a SEV TURB SIGMET
  it is mostly light chop (about 0.1 g), smooth about a fifth of the
  time. Moderate patches,
  severe clusters (5-30 s, 0.5-1.2 g jolts) and extreme encounters
  (a drop below zero g, like SQ321) happen at random, at about
  real-world odds for the kind of area (clean air, G-AIRMET, SEV TURB
  SIGMET, MTW, near or in a CB, strong low-level wind). For example, a
  30-minute SEV TURB SIGMET transit has about a one in five chance of
  a severe cluster. Events are not softened for the autopilot. New
  likelihood multipliers per kind (Turbulence page, config file
  `event_mult`; presets Realistic/Eventful/Training) make them more
  likely, never stronger, and a trigger starts one right away. Based
  on the research in docs/turbulence-research. The stable profile is
  unchanged.
- **Testing profile: fix: a turbulence event's big jolts could be lost.**
  A background burst sent in the same 0.2 s as one replaced it (PSX
  keeps only the last WxBurst of several sent at once): about a third
  gave a fraction of their effect, e.g. 0.45 g instead of 1.3 g.
- **Testing profile: fix: inside a CB's cloud the aircraft counts as "in a
  CB"** (8 severe clusters an hour instead of 0.75). It used to need 60%
  CB turbulence, which the CB interior (38-66%) mostly didn't reach.
- **Testing profile: a situation load ends a running turbulence event**,
  which used to carry on at the new place.
- **New: a once-a-minute `Flight:` line in the log**, also without
  `--debug` and also when it's smooth: position, altitude, heading,
  TAS, attitude, UTC and PSX time, wind, MSFS cloud, the turbulence
  level and its source, and the profile, so a pilot's report can be
  checked from a normal log. Every turbulence setting change is now
  logged with its value.
- **Change: the weather map shows every valid SIGMET area**, not just
  those within 640 nm (zoom out to see them), and its legend lists only
  the types with an area in view, updated as you pan and zoom. It used
  to list types whose areas were off screen. Areas across the date line
  are drawn in one piece. Needs `--sigmet` (frankenweather's SIGMET
  download); without it the map shows the nearby areas as before.
- **Fix: false "changed by another client" warnings** for frankenweather's
  own zone placements (PSX rounds zone positions a little more than
  frankenweather allowed for), and the extra rewrites they caused.
- **Testing profile: up/down bursts one way within 8 seconds add up to
  at most the largest single one**, for all continuous turbulence (not
  events): a run of small ones pumped the autopilot's pitch oscillation
  in ALT HOLD to over 1.5 g in mountain wave, and to ±2 g with pitch
  swinging -6 to +14 degrees in light wind-shear turbulence.

## 1.27.0 (2026-10-25)

- **Fix: frankenweather keeps its weather when something else changes
  it.** A situation load brings the situation's own weather zones (from
  wherever it was saved), and frankenweather didn't notice: the zones
  stayed there, often far away, until a zone happened to be relocated.
  Now every weather variable frankenweather sets (the zones' positions,
  weather and METARs, `WxBasic`, `WxAutoSet`) is watched, and its own
  value is written back about 2 seconds after a change. A situation
  load is logged as one line, any other client as a warning with the
  value it wrote. A variable that has to be restored more than 5 times
  in 2 minutes is left alone (error logged) until the changes stop.
- **New: a once-a-minute burst summary in the log**, also without
  `--debug`: how many turbulence bursts of each kind and direction (e.g.
  `SPD 8 +3/-5`), how big, and from which source, so a turbulence event
  can be understood from a normal log afterwards.
- **Testing profile: mountain wave that doesn't drive the autopilot into
  oscillation** (terrain wave and MTW SIGMETs). In a FLCH descent through
  a SEV MTW SIGMET near Glasgow, the wave's one-way bursts swung the
  descent rate between a few hundred and over 4,000 ft/min. Testing
  gives up/down bursts that follow how fast the wave changes rather than
  its direction, lets an MTW SIGMET lift the terrain model's own wave to
  at most twice it plus 20%, and in a wave no gust, bank or yaw bursts,
  and only a quarter of the speed bursts. Gust bursts weather-vane the
  aircraft: the yaw a 747 pilot found unrealistic in mountain wave (test
  sim, 30% wave: heading swings 1.7° and banks 6.6° p95 with them, none
  without). The speed bursts give the airspeed fluctuation pilots expect
  there (about ±12 kt p95 at 45%); in V/S the autothrottle takes it and
  the descent stays steady, while FLCH chases the speed with pitch (45%
  wave: 14 swings of over 2,000 ft/min in a descent from FL300 to FL150).
  The stable profile is unchanged.
- **Testing profile: the turbulence level shown for a TS/TC SIGMET area**
  (console, router /efb) now includes its minimum, not just the part
  that varies.
- Zone weather PSX already has is no longer sent again just because PSX
  writes the wind speed without a leading zero (winds under 10 kt).

## 1.26.0 (2026-10-25)

- **New: a turbulence profile, "stable" or "testing"**, chosen on the
  router's /efb page (frankenrouter 1.30.0), in the configuration file
  (`[turbulence]` `profile`) or on the turbulence API. Stable (the
  default) is the turbulence as before. Testing also turns on changes
  being tried out before they may become the default, so they can be
  compared in the sim (also with seat shakers) without changing anyone's
  normal turbulence. Testing now adds:
  - **Boundary-layer turbulence**: light bumps on an ordinary breezy or
    sunny day below the boundary layer's top (often 3,000-5,000 ft by
    day), from the wind (up to 15%) and the sunshine (up to 20%), at
    most 20%. The stable profile is smooth there.
  - **Turbulence inside TS and TC SIGMET areas** (`--sigmet`): the air
    between the cells, which the stable profile leaves smooth between
    the zones' CBs. Mostly light and never smooth: ISOL 3-14%, OCNL
    6-25%, FRQ/EMBD 10-36%, SQL and TC 12-46%.

## 1.25.0 (2026-10-25)

Found by more automated test flights around the world.

- **Change: inside a TS or TC SIGMET area the track ahead has CBs**
  (`--sigmet`). One zone used to be kept CB-free 40-50 nm ahead on track,
  so flying into a cyclone's thunderstorm area gave no CB and no
  turbulence. Now that "anchor" zone is only used when every zone is
  more than 150 nm away, in every mode: one zone goes 50 nm behind the
  aircraft, CB-free, so the local weather (QNH, wind) comes from a zone
  nearby.
- **Change: a CB appearing close to the aircraft is small.** A zone
  within 100 nm that gets a CB (a new or moved TS SIGMET, CAPE, a METAR)
  gets at most 2 oktas and 15,000 ft until it's moved, instead of e.g.
  a 45,000 ft CB appearing 10 nm ahead. A CB it already had doesn't grow.
  The departure and destination airports keep their METAR's CB.
- **Fix: after a reposition or situation load, turbulence starts
  afresh** at the new position. The old position's turbulence used to
  fade out over about a minute.

## 1.24.0 (2026-10-25)

Found by automated test flights near EGLL, EGPH and EINN in the stormy
weather of 2026-10-24 (a pilot's "needs tuning down below 3000 ft").

- **Fix: low-level wind shear's 30% limit no longer leaks into other
  turbulence.** Where mechanical turbulence took over from it near the
  ground, it could briefly reach 70% (severe), fading over about a
  minute: 900 ft near Shannon in a 49 kt wind.
- **Change: low-level wind shear varies below its 30% limit** (about
  15-30%) instead of sitting at 30% nearly all the time on a windy day.
- **Change: rotor and mechanical turbulence vary less** (gustiness
  0.5-1.2 like wind shear, was 0.5-1.5): mechanical turbulence in a
  strong wind could reach 75% on an approach, now at most 60%.

## 1.23.0 (2026-10-24)

- **Change: CBs fade away instead of vanishing**, also without
  `--cb-growth`. When a CB's forecast shrinks or disappears (a TEMPO
  group switching off, a new METAR, a SIGMET expiring), it shrinks over
  up to about 15 minutes instead of disappearing from the radar, and its
  turbulence with it, at once. New CBs still appear at once. "Remove CB
  near dep/arr" still removes them at once, and a zone that moves starts
  fresh, so this never keeps a zone in place.
- **Fix: the manual turbulence override obeys the turbulence on/off
  switch.** It injected turbulence even with turbulence switched off.

## 1.22.0 (2026-10-24)

- **Change: mountain waves weaken downwind of the ridge**, to about a
  third every 50 km. They used to be just as strong 80 km downwind (e.g.
  moderate at FL370 over the plains east of the Rockies).
- **Change: no more sudden steps in terrain and wind shear turbulence**
  where the wind data changes (each 1° square, about every 7 minutes at
  cruise, and each hour): its intensity changes over about 30 seconds.
  Wind and CAPE data now come from the nearest forecast hour (it could be
  up to an hour old).
- **Change: no step in low-level turbulence at a coast.** It rises with
  the roughness of the terrain, from the sea's level over water and flat
  land.
- **Change: over high ground, wind shear and the low-level wind no
  longer come from pressure levels well below the ground** (extrapolated
  by the weather model).
- **Fix: a thunderstorm the Open-Meteo forecast itself predicts falls back
  to its total precipitation** as intended (it was never downloaded).

## 1.21.0 (2026-10-24)

- **Fix: weather zones at high airports are at the right elevation.**
  PSX takes a zone's elevation in feet (live-tested: PSX's own zone for
  SKBO says 8360), but frankenweather gave it in metres, and 0 for zones
  at an airport with a METAR. A zone at Bogotá was at 0 or 2,548 ft
  instead of 8,360 ft. Airports now get their own elevation.
- **Fix: CB tops over high ground.** PSX's cloud heights are above the
  zone (a METAR's FEW030 is a base of 3000), but CB tops are worked out
  above sea level, so a CB at Bogotá topped out about 8,000 ft too high.
  A CB is now also always at least 5,000 ft deep: a thunderstorm forced
  by a METAR or SIGMET in hot, dry air with little CAPE could have its
  base above its top, and then no turbulence.

## 1.20.0 (2026-10-24)

- **Fix: Gazipaşa (LTGP) no longer has a permanent thunderstorm**, and
  no station code is read as weather any more (LTGP starts with "LTG",
  lightning; codes like SHTS look like showers plus thunderstorm).
- **Fix: a thunderstorm the METAR reports is never made smaller by the
  Open-Meteo forecast**, only bigger. E.g. Singapore's `+TSRA` (8 oktas,
  44,999 ft) became 2 oktas topping at about 24,500 ft when the model had
  little CAPE.
- **Fix: low-level wind shear stays capped where mechanical turbulence
  takes over.** It could jump from 30% to 60% there.
- **Fix: mountain waves stay smooth** when the wind data changes (each
  1° square, each forecast hour) or another peak takes over: their phase
  now advances with the aircraft. It could still jump from an up- to a
  downdraft there.
- **Fix: a mountain wave SIGMET no longer takes over the terrain model's
  own mountain wave or rotor** (since 1.19.0): the stronger of the two,
  after their intensity settings, is used with its own settings.
- **Fix: the turbulence intensity shown follows what's injected**: also
  when only the settings or MSFS cloud change it, at once when manual
  turbulence is switched off, and capped on the ground like the
  turbulence there. "mechanical+shear" and the like show as wind shear
  CAT.

## 1.19.0 (2026-10-24)

- **Change: no sudden steps between rotor, mountain wave and mechanical
  turbulence.** The rotor now fades out smoothly towards 15 km from the
  ridge, towards its ceiling and for low barriers, and the strongest of
  the three is used. E.g. climbing through the rotor ceiling it jumped
  from 30% (rotor) to 45% (wave) at once.
- **Change: stricter low-level jet detection.** Almost any wind profile
  counted as a low-level jet, which makes rotor turbulence up to about
  1.5-1.8 times stronger. Now the wind must drop by 8 kt within 1,500 m
  above the maximum, and levels below the ground don't count.
- **Change: mountain wave SIGMET turbulence follows the SIGMET settings**
  (on/off and intensity) instead of the mountain wave ones. It shows as
  "Mountain wave SIGMET".
- **Change (`--cb-growth`): CBs also grow and decay gradually in
  maneuvering mode** (holds, vectoring), where e.g. a destination METAR's
  TEMPO CB used to appear and vanish at once.
- **New: the weather map shows each CB's anvil turbulence zone** above
  its top as a dashed outline: reaching furthest downwind, a third as far
  upwind (all round when the wind isn't known).

## 1.18.0 (2026-10-24)

- **Change: mountain wave turbulence moves the aircraft in real waves.**
  Its up/down motion is now measured from the ridge: sinking just
  downwind of it, then rising and sinking again every wavelength (about
  16 km in a 45 kt wind). It used to come from the absolute position, so
  a 1° wind change moved it by several wavelengths: noise, not waves.
- **Change: mountain waves need a real cross-ridge wind.** Only the wind
  blowing across the ridge counts, ramping in from about 20 kt to full
  at about 43 kt, and from a 200 m to a 1,500 m barrier. They used to
  switch on at 15 kt and a 500 m barrier, straight to about moderate.
- **Change: mechanical (low-level) turbulence comes from the wind, also
  over flat land and the sea.** Light from about 30 kt (wind 300 m above
  the ground), moderate from about 50 kt, more over rough terrain, fading
  with height above the ground. It used to be at most about 4% over flat
  land even in a 46 kt wind, and none at all over the sea.
- **Change: PIREP turbulence.** A MOD PIREP is now moderate (37%) and a
  SEV one severe (60%); they were one category higher. A PIREP's
  turbulence now fades out within 40 nm (was 150 nm, and from a distance
  up to 30 minutes old) and varies over time like a SIGMET's, instead of
  being steady.

## 1.17.0 (2026-10-24)

- **Change: wind-shear clear-air turbulence at cruise.** Above FL200 it
  now starts at a vertical wind shear of 5 kt/1000 ft and is at its
  strongest from 12 (was 8 and 14; still so below FL100, blended in
  between). Shear from forecast data is smoothed, so even on a day with
  a 163 kt jet stream over the UK cruise was almost never turbulent; that
  day it now gives about 12% light or more and 2% moderate at FL300-410.
- **Change: CB tops are limited by the tropopause for the latitude and
  season**, at most 2,000 ft above it and never above 44,999 ft (PSX's
  limit): e.g. London about 39,400 ft in July and 33,600 ft in January,
  44,999 ft in the tropics. Tops rise with CAPE from 15,000 ft (was
  45,000 ft from 1,250 J/kg anywhere, never below 25,000 ft). A METAR
  thunderstorm's CB no longer always tops at 40,000-45,000 ft, also in
  winter.
- **Change: turbulence inside a CB is severe to extreme whatever its
  coverage**, mostly from the cloud's depth: mid-cloud in a 40,000 ft CB
  it's 77% with 2 oktas, 100% with 8 (was 33% and 67%).
- **Change: anvil turbulence above a CB fades faster with height and
  reaches out mostly downwind**: a third as far upwind as downwind, from
  the wind at the cloud top. 12,000 ft above a small CB it's now about
  1% (was 9%).

## 1.16.0 (2026-10-24)

- **Change: being in MSFS cloud makes turbulence at most 1.3 times
  stronger** (1.6 with the MSFS influence at 200%). It was up to about
  2.8 times in any cloud, stratus and cirrus too, on top of everything
  else. Out of cloud turbulence is no longer reduced (it was 0.8 times),
  and being near a PSX CB no longer counts as being in cloud (a sudden
  jump when the CB took over from another source).
- **Change: the turbulence intensity shown is what's injected**, with
  the intensity settings and the MSFS factor applied, on the status and
  turbulence pages and (frankenrouter 1.28.0 or later) /efb. The
  turbulence state message has a new `effective_intensity` for it.

## 1.15.0 (2026-10-24)

- **Fix: airports with "TS" in their code no longer have a permanent
  thunderstorm.** The station code was read as weather, so e.g. VTSP
  (Phuket), VTSS, VTSM, UTSS (Samarkand) and KTSP always got a 4-okta CB
  topping at 40,000 ft. Only real weather groups count now. Also:
  lightning only from remarks starting with LTG (not e.g. ALTG), a
  thunderstorm that has ended (RETS) counts as one nearby (2 oktas
  instead of 4), and GS (small hail/snow pellets) as showers, not hail.
- **Fix: strong convective inhibition (CIN over 200 J/kg) now suppresses
  CBs.** Open-Meteo gives CIN as a positive number, and the check never
  fired.
- **Fix: the wind shear on/off switch and bias now also apply when wind
  shear outweighs terrain turbulence** ("mechanical+shear", "wave+shear",
  "rotor+shear" in the log). Switching wind shear CAT off didn't stop
  those, and they also got yaw and speed bursts.
- **Fix: wind-shear clear-air turbulence over the open sea and in light
  low-level wind.** There was none at all when there was no terrain
  upwind or the wind near the ground was under 6 kt, e.g. jet-stream CAT
  over the North Atlantic.
- **Fix: missing showers data no longer makes CAPE turbulence full
  strength**; it counts as no showers (CAPE alone in clear air is
  smooth).
- **Change: turbulence on the takeoff and landing roll is at most 30%
  and only yaws and rolls the aircraft** (no sink or speed bursts). A CB
  overhead gave "severe" bursts during the roll. Live-tested on a heavy
  747: PSX makes a yaw jolt grow with airspeed, from about 0.2° of
  heading at 80 kt to about 0.6° near rotation.

## 1.14.0 (2026-10-24)

- **New: "Avoid CB near departure/destination" is now "CB near dep/arr"
  with three modes: Off, Move zone and Remove CB.** Move zone is the
  old behaviour: the airport's zone moves 23 nm north, so its CB stays
  at least 15 nm away. New, Remove CB removes the CB of every zone
  centred within 22 nm of the departure or arrival airport, so no CB
  comes within 15 nm of it, from any zone. The zones stay put (no PFD
  jump when switching it on in flight) and the airport keeps its real
  weather. TS and CB are taken out of these zones' METARs given to PSX,
  since PSX makes a CB of its own from those (live-tested which METAR
  codes do: TS, VCTS, recent TS and CB clouds; showers, TCU, trends and
  remarks don't). Config files with the old setting switched on get Move
  zone. Loading a config file now also moves the zone right away.
- **Change: wind-shear clear-air turbulence is much less violent.** Its
  gustiness multiplier is now 0.5-1.2 instead of 0.4-1.6, so it peaks at
  60% instead of 80% (more than a severe TURB SIGMET, which peaks at
  75%) after a pilot found it far too strong.
- **Change: wind shear near the ground is capped at 30%.** Below 3,000 ft
  above the ground, rising to no limit at 5,000 ft. A windy departure
  from EGLL got wind-shear "CAT" at 100% from the normal increase of the
  wind with height near the ground (with the MSFS in-cloud multiplier).
- **Change: far fewer Open-Meteo calls, and the zones keep updating
  while Open-Meteo is unreachable.** On 2026-10-24 testing used up the
  free API's daily limit (10,000 calls). Reviewed every Open-Meteo request:
  - The turbulence wind profile (~10 calls per download) downloaded 2
    days of forecast every hour for the same place and used one hour of
    it. The whole download is now kept on disk and serves every hour,
    refreshed after 6 hours; the same for CAPE.
  - Zone weather was fetched for all 7 zones every 5 minutes and
    whenever any zone moved. It now counts as fresh for 15 minutes and
    only zones without fresh data are fetched.
  - Enroute wind (~70 calls for a long route) was fetched again after
    every restart and for the whole route when any waypoint changed. It
    is now kept on disk (2 days) and only new waypoints are fetched.
  - One shared backoff: after a refusal nobody asks again for 5 minutes.
  - While Open-Meteo can't be reached, older data is used instead of
    stopping: zone updates (new METARs, setting changes such as CB near
    dep/arr) no longer stop, and turbulence no longer switches off.
    Even with no Open-Meteo data at all, zones keep being placed (including
    at the departure/destination airport) and are updated from METARs: a
    zone with no METAR of its own goes on the nearest METAR station.
  - New: Open-Meteo calls in the last hour/24 hours on the Weather zones
    page, and in the log once an hour.
- **New (testing): `--fake-cb-only=ICAO[,ICAO...]`** limits `--fake-cb`
  to the zones of those airports (`DEP`/`DST` for the FMC departure and
  destination), e.g. `--fake-cb=6:3000:40000 --fake-cb-only=DST`.
- **New: METARs given to PSX that aren't real say so** in a remark:
  `RMK FRANKENWX MODEL WX` (Open-Meteo zones, station and time only),
  `RMK FRANKENWX CB REMOVED` (TS/CB removed, see above) and
  `RMK FRANKENWX QNH SYNC` (QNH changed by MSFS QNH sync).

## 1.13.0 (2026-10-24)

- **New (`--sigmet`): the parsed SIGMETs go to the routers too**, for
  shared cockpit: after every download and setting change and every 5
  minutes, frankenweather sends `/api/sigmet/latest/json` to the PSX
  network as `addon=FRANKENWEATHER:SIGMETJSON:<uuid>:<payload>` (compact
  JSON, lzma-compressed and base64-encoded, about 30 KB for 180
  SIGMETs). Every frankenrouter (1.26.0 or later) serves it as
  `/api/sigmet/latest/json` on its own web port.
- **Change: `/api/sigmet/latest/json` has `generated_at` and
  `generated_at_utc`**: when it was put together, so "expired" and
  "used_by" are as of then.
## 1.12.1 (2026-10-24)

- **New (`--sigmet`): the downloaded SIGMETs on frankenweather's own web
  port** (not the router's), as text in PSX's own SIGMET layout:
  `/api/sigmet/latest/raw` (every SIGMET of the last download, including
  expired, unknown and unparseable ones), `/api/sigmet/latest/sanitized`
  (only the usable ones) and `/api/sigmet/latest/topsx` (exactly what
  frankenweather last gave PSX). `/api/sigmet/latest/json` has the whole
  download parsed: download time, next download, last error, settings,
  counts, and every SIGMET with its area, levels, validity, raw text,
  whether it is usable (and why not), whether it has expired, and who
  uses it.

## 1.12.0 (2026-10-24)

- **New: turbulence comes in altitude layers, and a smooth level stays
  smooth for a while.** Like real turbulence, each 2,000 ft layer
  (FL255-275, FL275-295 and so on; boundaries at x500 ft, so every
  flight level is in one layer) has its own, independent intensity over
  time, blended over 300 ft either side of a boundary, and its own
  lasting roughness (30-90 minutes, then slowly changing). A climb or
  descent of 2,000 ft therefore always gives a different ride, and a
  good level found that way tends to stay good. MTW SIGMETs and mountain
  wave use 6,000 ft layers, since a mountain wave is much deeper.
- **Change (`--sigmet`): TURB/MTW SIGMET turbulence now has smooth
  spells**: 0-75% instead of 10-75%, smooth air about 14 minutes per
  hour on average. A smooth layer is at 45% or more about 1 minute per
  hour, a rough one about 25 minutes.
- **New: gustiness for the turbulence frankenweather works out from
  physics.** Mountain wave, rotor, mechanical turbulence, wind shear and
  CAPE turbulence are multiplied by a layered "gustiness" that averages
  about 1 (wind shear 0.4-1.6, mountain wave 0.7-1.3, rotor and
  mechanical and CAPE 0.5-1.5), varying over time and per layer like the
  SIGMET turbulence. The physics still decides where turbulence is and
  how strong it is on average. The turbulence reason shows the
  multiplier, e.g. `(gust x1.24)`.
- **Change: G-AIRMET turbulence varies like a TURB SIGMET** instead of
  staying at the area's fixed intensity: the same layered level, scaled
  by severity (a MOD area gets two thirds of it), so a moderate G-AIRMET
  is mostly light with moderate spells.
- **New: PIREPs.** frankenweather rates the ride at every whole flight
  level up to 4,000 ft above and below the aircraft (FL010-FL470) from
  every source of turbulence, by how often each level is at moderate
  turbulence or worse (smooth under 10% of the time, light under 20%,
  moderate under 35%, rough above), refreshed every minute, on a flight
  level change and when entering or leaving a turbulence area. The
  router's /efb page shows them with its PIREP button (router 1.25.0).
- **New: the console shows the turbulence's intensity ramp**: a
  `[  ramp     ]` line under the driving source and a line whenever a
  new ramp or layer starts, e.g. `Turbulence intensity (TURB SIGMET):
  FL275-295 rising 16% → 55%, 41 s left, then holds 3:19`. The router's
  /efb page shows the same ("Intensity trend").
- **New: G-AIRMET turbulence areas on the weather map** (within 640 nm,
  dashed when G-AIRMET turbulence is switched off). The legend says that
  weather areas are only shown within 640 nm.
- **Fix: the turbulence status could show a ramp that had already
  ended** as "rising … 0 s left" every other time (rounding).
- The turbulence engine and data fetchers are now called under locks,
  since the new PIREP task calls them from a second thread.

## 1.11.0 (2026-10-24)

- **Change (`--sigmet`): TURB and MTW SIGMET turbulence varies over
  time** instead of staying at a fixed 75% (severe, with large bank and
  speed excursions) for the whole area. The intensity now moves between
  10% and 75% and is mostly light: 10-25% about half the time, 45% or
  more about 10 minutes per hour, 70% or more less than a minute per
  hour. It ramps to a new random level along a smooth S-curve, usually
  over 30 seconds to 3 minutes but one time in five within seconds,
  holds there for a random time, and then picks the next level. TURB and
  MTW each have their own varying level, and a SIGMET marked MOD gets
  two thirds of it. The fade-in near the area's edges is unchanged.
- **New (`--sigmet`): SIGMET presets** on the SIGMETs page and the
  router's /efb page:
  - **Off**: no SIGMETs at all. Neither frankenweather nor PSX uses any.
  - **Normal**: frankenweather handles TS, TC, TURB and MTW, PSX gets
    VA and ICE (the default).
  - **PSX**: frankenweather's SIGMET system is switched off, as without
    `--sigmet`. PSX handles every SIGMET itself, and frankenweather only
    asks it to download fresh ones regularly.

  Off and PSX keep the per-type choice on the SIGMETs page for when the
  system is switched back on. Saved in the config file as `[sigmet]`
  `own`.
- **New: the weather map shows which SIGMET areas frankenweather uses.**
  Areas it doesn't use with the current settings (PSX gets them, or
  nobody does) are drawn with a dashed outline, and hovering over an
  area says which. Without `--sigmet`, or with the Off or PSX preset,
  the map now shows the SIGMETs PSX downloaded, all dashed.
- **New: `--log-web-requests`.** The web UI's per-request log lines are
  no longer printed unless this option is given.
- **Fix: the turbulence status sent to the router and web UI followed
  gradual changes late.** A new status was only sent when the intensity
  changed by more than 0.05 from one 200 ms tick to the next, so a
  gradual change could go unreported for up to a minute. It is now
  compared with the last status sent.
- The turbulence status includes the SIGMET intensity's current ramp,
  shown by the router's /efb page (router 1.24.0).

## 1.10.0 (2026-10-23)

- **Fix: CBs in frankenweather's own zones no longer vanish.**
  frankenweather wrote a METAR it generated from Open-Meteo into each of
  its zones. PSX rebuilds a zone's weather from its METAR about a second
  after the METAR's weather changes, which removed SIGMET CBs and changed
  cloud layers and tops, so the radar, PSX's zones and frankenweather's
  turbulence disagreed. Zones whose weather comes from Open-Meteo now get
  a METAR with only the zone's name and the time it was placed (e.g.
  `X328 231230Z`), written once per placement. PSX's Instructor shows that
  instead of an old zone's METAR. Zones with a real METAR still get it.
- **Fix: CB turbulence is centred on the CB itself**, where PSX draws it
  on the radar, 7 nm from the zone's centre. Its core was centred on the
  zone's centre, so with a core of under 7 nm, flying straight through
  the CB on the radar only gave the weaker flank turbulence.
- **Fix: a zone isn't moved while its CB turbulence can reach the
  aircraft** (its core plus flank zone, or plus the anvil zone above the
  cloud top), whatever would otherwise move it: falling behind, becoming
  the anchor or a SIGMET zone, an airport needing a zone, or entering
  maneuvering mode. The turbulence used to stop the moment a zone moved.
- **Fix: leaving maneuvering mode no longer re-places all zones.** It
  re-placed them at random all around the aircraft, taking away the CB
  ahead (or the one the aircraft was in). Now only entering maneuvering
  mode does that, and leaving it lets the ordinary relocation rules move
  zones as they fall behind.
- **Fix (`--sigmet`): a new anchor zone is kept away from the other
  zones.** It went 40-50 nm straight ahead even if another zone was right
  there, and a zone close to a CB shrinks that CB's radar return and
  turbulence (one anchor 9 nm from a CB left it a 2 nm core). Now it goes
  40-50 nm ahead, within 20 nm of track, at least 50 nm
  (`--new-zone-notnear`) from the other zones when possible, else as far
  from them as possible, and never gets another zone's name.
- **New: CB turbulence on the weather map.** Each CB's turbulence area is
  drawn as rings around where PSX draws the CB: the core (red) and the
  inner (yellow) and outer (green) half of the flank zone, at cloud
  levels. (Also in the router's web UI, with a router that has it.)

## 1.9.0 (2026-10-23)

- **New (experimental): frankenweather's SIGMET system (`--sigmet`).**
  Off by default. frankenweather downloads SIGMETs from aviationweather.gov
  itself, turns off PSX's own SIGMET download, and handles the SIGMET
  types chosen on the new SIGMETs page itself (all four by default, can
  be saved in the configuration file). PSX gets the other types it
  could parse, and always volcanic ash and icing:
  - **Thunderstorms (TS) and tropical cyclones (TC)** become CBs in
    frankenweather's weather zones, so CB turbulence (and MSFS, through
    PSX.NET.Orchestration) sees every thunderstorm the radar shows.
    Zones are placed in the nearest part of TS/TC areas within 300 nm, at
    least 100 nm away (2 per TS area, a cluster of 3 per TC). CB coverage
    follows the SIGMET's wording (ISOL 2/8, OCNL 4/8, EMBD 5/8, FRQ 6/8,
    SQL 7/8, TC 6-8/8), the top comes from the SIGMET, the base from
    Open-Meteo. CAPE can only make a CB bigger. A CB-free "anchor" zone
    is always kept near the aircraft for the local QNH, so no CB can
    appear right next to the aircraft.
  - **Turbulence (TURB) and mountain wave (MTW)** become frankenweather's
    own turbulence, a new "SIGMET" source: severe (0.75) inside the
    area and between its base and top, fading in near the edges. MTW
    strengthens the terrain model's own mountain wave or rotor, keeping
    its pattern.
  - At startup, the SIGMETs are downloaded right away, and zones get
    their SIGMET CBs as soon as they arrive.
  - SIGMETs that can't be parsed are left out and saved to the log
    directory's `sigmet-unparsed` folder.
  See [SIGMETs](frankenweather.md#sigmets).
- **Change: without `--sigmet`, frankenweather no longer uses SIGMETs.**
  It only has PSX download them regularly. Previously it also read PSX's
  SIGMET text to force CBs into zones inside TS SIGMET areas and to draw
  TS areas on the map.
- **Change: "PSX SIGMETs Off" (router /efb)** with `--sigmet` gives PSX
  no SIGMETs at all (frankenweather still handles its chosen types).
  Without `--sigmet` it still turns off PSX's "SIGMETs embedded in the
  planet weather model".
- **New: SIGMETs web page** (with `--sigmet`; also in the router's web UI,
  with a router that has it): which types frankenweather handles,
  download status (with the next automatic download and a "Download
  SIGMETs now" button), the number of SIGMETs of each type that
  could and couldn't be parsed, the nearest area of each type and every
  other one within 320 nm (with the weather zones inside TS/TC areas),
  unparseable SIGMETs, and the raw text and the text given to PSX as
  downloads.
- **New: weather map additions.**
  - Range rings at 40, 80, 160, 320 and 640 nm around the aircraft.
  - SIGMET areas of every type within 640 nm, one colour per type, with
    a legend (previously only TS areas near a weather zone).
  - Each zone's CB marked at its actual position (7 nm from the zone
    centre, at the bearing PSX uses).
  - With `--sigmet`, zones whose CB comes from a SIGMET are marked (also
    in the log and on the weather zones page, naming the SIGMET), and so
    is the CB-free anchor zone.
  - The map refits to the aircraft and zones on its periodic reload,
    unless you've panned or zoomed in the last 2 minutes.
- **Change: the web UI is reorganised into six pages:** Map, Weather
  zones, Enroute wind, SIGMETs (with `--sigmet`), Extra turbulence, and
  Config and logs. Every page has the same buttons at the top linking to
  all of them, plus Refresh (and, in the router's UI, a button back to
  the router's start page).
  - Weather zones has an Auto/Manual switch at the top (plus Pause and
    Disable). Auto shows the weather zones; Manual shows the manual
    weather controls, which used to be their own page, and puts that
    weather into all seven zones.
  - Config and logs has the configuration file's Save/Load/Reset buttons
    (moved from the weather zones page), the time sync settings and, in
    frankenweather's own web UI, the log files (formerly their own page).
  - The old `/weather/manual` and `/weather/logs` addresses redirect to
    the new pages.
- **Fix: G-AIRMET turbulence never applied.** aviationweather.gov's
  G-AIRMET data has a different layout than frankenweather expected, so
  no G-AIRMET area was ever found ("G-AIRMET: 0 active turbulence
  regions"). G-AIRMETs only cover the US.

## 1.8.6 (2026-10-23)

- **Change: `--sync-tropopause` only sets the tropopause from reliable
  data.** PSX's own automatic tropopause is fine for most purposes, so
  frankenweather now leaves it on auto for the first 15 minutes after
  starting, and until it has at least 3 tropopause values for the
  current area. It then uses their median, on the ground as well as in
  flight. After a situ load or reposition (a jump of more than 150 nm),
  the earlier values are discarded and the tropopause goes back to auto
  until there are 3 new ones.
- **Fix: a tropopause above 50,000 ft was written again and again.** PSX
  doesn't accept a tropopause higher than 50,000 ft, which is common in
  the tropics, so it stored 50,000 ft instead, and frankenweather kept
  trying to correct it. The tropopause is now capped at 50,000 ft.

## 1.8.5 (2026-10-22)

- **Fix: `--sync-tropopause` caused PFD altitude jumps in flight.** On
  the VHHH-EGLL flight, 7 of the 17 cruise weather updates that changed
  the tropopause made the PFD altitude jump (up to 81 ft), and none of
  the 45 updates that didn't. The tropopause was rewritten about every
  5 minutes. In flight, frankenweather now uses the median of the last
  hour's tropopause values, and only changes PSX's tropopause when that
  differs by at least 3,000 ft, 5°C or 30 kt of wind, at most every 30
  minutes. On the ground it is still updated straight away.
- **Fix: the detected tropopause jumped between a few fixed heights.**
  It could only ever be the height of one of the Open-Meteo pressure
  levels around cruise (about 30,000, 34,000, 39,000, 45,000 or
  53,000 ft), and flipped between them from one forecast to the next.
  frankenweather now also fetches the levels in between (275, 225, 175
  and 125 hPa) and interpolates the tropopause height between levels.
  Where Open-Meteo's model has no data for the extra levels, the old
  levels are still used. The extra levels also give the turbulence
  engine a finer wind profile around cruise altitude.

## 1.8.4 (2026-10-21)

- **Change: the CB squeeze no longer places new zones closer than 100 nm
  ahead.** When CBs are around, `--cape-squeeze` moves new zones closer
  to the aircraft. With the old default (`500:50`) they landed as close
  as 30 nm, which real crews would already be deviating around, and
  zones were relocated again within 3-11 minutes (about one relocation a
  minute on the DNMM-VVDN flight). The default is now `500:150`: new
  zones go 150-250 nm ahead normally and 100-150 nm ahead when fully
  squeezed.
- **Fix: zones were repositioned in PSX without actually moving.** A
  zone's position is now only sent to PSX when the zone is really
  relocated. On the DNMM-VVDN flight there were about 2,200 position
  writes for 321 real moves:
  - PSX echoes positions back slightly rewritten (rounded coordinates,
    a different name ending), so frankenweather never recognised them as
    unchanged and resent every zone on every update. These are now
    treated as unchanged.
  - The elevation sent with a zone's position varied between updates
    (Open-Meteo's value changes between fetches, and METAR zones send 0).
    A zone now keeps the elevation it was first sent with until it moves.
  - Zones hopped between nearby airports (up to about 40 nm) whenever a
    METAR went stale or fresh, or another zone changed airport. A zone
    now keeps its airport until it is relocated; if that airport's METAR
    goes stale, it uses Open-Meteo data at the same spot instead.

## 1.8.3 (2026-10-21)

- **Fix: `--sync-tropopause` never set a tropopause in the tropics.**
  The tropical tropopause sits at about 100 hPa, the highest level
  frankenweather fetched from Open-Meteo, and the highest level can never
  be confirmed as the tropopause because there is nothing above it to
  compare with. 70 hPa is now fetched as well; on the DNMM-VVDN flight
  this gives about 54,800 ft / -80°C over India, where nothing was set
  before.
- **Fix: tropopause sync now hands the tropopause back to PSX when it
  has no data.** If no tropopause can be determined (or there is no wind
  profile at all), WxAloft is set back to PSX's own automatic modes
  instead of leaving an earlier fixed value in place. Tropopause changes
  are now logged.
- **Fix: NOAA METARs failed completely if the file contained a single
  garbled report** ("'utf-8' codec can't decode byte 0xed ..."), and
  frankenweather fell back to VATSIM. Undecodable bytes are now replaced,
  so only that one report is affected.
- **Fix: NOAA METARs were not always the newest report.** NOAA's hourly
  files repeat each station many times and are not in time order, and
  frankenweather simply used the last line it saw. It now picks the
  newest observation, prefers corrected (COR) reports over the
  originals, and never replaces a full report with a copy whose remarks
  were cut off. The previous hour's file is read too, since early in each
  hour the current one has very few stations.
- **Fix: per-run logs in `.cache/frankenweather/logs` could look out of
  date when copied during a flight.** The log files are now forced to
  disk every 60 seconds.

## 1.8.2 (2026-10-20)

- **Fix: cruise PFD altitude jumps caused by writing WxBasic (planet
  weather).** Flight logs showed every cruise PFD altitude jump of 9 ft
  or more coincided with a WxBasic write, and none without one: PSX
  appears to turn pending smooth weather transits into instant ones when
  WxBasic is written. WxBasic is now only written on the ground (below
  50 kt TAS) or while the aircraft is actually in it. Verified on a 6.5 h
  cruise with no jumps (about 22 expected before the fix). Tropopause
  sync (`--sync-tropopause`, WxAloft) is unaffected.
- **Fix: PSX silently corrected some cloud values frankenweather sent**,
  rewriting them about 10-40 ms later. A second write on the same
  variable that soon was linked to PFD altitude jumps, and it also
  defeated the "don't resend values PSX already has" check. Values are
  now kept within what PSX accepts:
  - "No cloud" layers use 44950 ft (high layer) and 44900 ft (low layer)
    instead of 45000 ft for both.
  - CB tops are capped at 44999 ft instead of 55000 ft, so the
    tallest CB is now reached at about 1250 J/kg CAPE instead of
    about 2000 J/kg.
  - MSFS cloud sync no longer sends a negative cloud base near the
    ground (e.g. fog during taxi or initial climb); the base is floored
    at 100 ft, PSX's own minimum.
- **Fix: crash and restart loop on a stale METAR without an observation
  time.** If a METAR was too old to use and its observation time couldn't
  be read, building the zone status text crashed the weather update and
  frankenweather restarted it every ~15 s until the zone moved on. The
  age is now shown as "undated".

## 1.8.1 (2026-10-18)

- Cleanup: `_web_cache_get()` used `__import__('re').match(...)` twice
  instead of the `re` module already imported at the top of the file -
  no behavior change, just dropped the redundant inline import.

## 1.8.0 (2026-10-17)

- **Fix: `self.ac_tas_kt` (true airspeed, from `PiBaHeAlTas`) was missing
  the same `/1000.0` unscaling already correctly applied to altitude on
  the line above it** — PSX sends this field as `tas_kt*1e3` (confirmed
  against `frankenpush.py`/`frankenturb/__init__.py`'s independent
  parsers of the same field), so `ac_tas_kt` has always held TAS×1000.
  This silently broke `_update_maneuvering_mode()`'s "true airspeed below
  200 kt forces maneuvering mode" check (it could never fire), and was
  caught via `--cb-growth`'s new reliance on this field's actual magnitude
  for its ETA estimate (see below).
- **New (experimental, command-line only): `--cb-growth`** — active in
  cruise, inactive in maneuvering mode (independent of altitude; see
  "Entering/exiting maneuvering mode" above), ramps a CB's coverage
  (oktas) and top height gradually toward its forecast target instead of
  snapping instantly, so a CB doesn't appear to pop into view the moment
  a zone is placed ahead of the aircraft or a new CB appears in an
  existing zone. Starting size is a random percentage of forecast size,
  picked within a range chosen by the CB's distance to the aircraft when
  it first appears (farther out allows a higher percentage without
  looking like a pop, since it's too far away to assess precisely):
  70-100% beyond 200nm, 50-80% beyond 150nm, 30-60% beyond 100nm, 10-40%
  beyond 50nm, 5-20% at or inside 50nm. The growth rate is sized to finish by
  roughly the time the aircraft gets there, but hard-capped regardless of
  distance — avoiding a pop right next to the aircraft matters more than
  a close-in CB finishing growth in time, so one with little runway left
  simply arrives still under-grown. The top-growth cap scales with the
  zone's forecast CAPE (real storm updrafts are stronger at higher CAPE),
  bounded between a 1000 ft/min floor (weak/no-CAPE CBs) and a 4000 ft/min
  ceiling, matching the ~1500-4000 ft/min range meteorology references
  give for an actively-growing CB -- without the ceiling, CAPE's
  unbounded sqrt(CAPE) scaling would keep accelerating the rate even past
  the point (~1750 J/kg) where `cb_tops_ft()`'s own 55000ft forecast
  ceiling stops giving it any more height left to climb towards. Oktas
  growth stays at a flat cap. Shrinking/removal always uses a fixed,
  distance- and CAPE-independent decay rate instead, since a departing
  zone doesn't need to hurry. Base height is never ramped. Off by
  default. See
  [CB growth and decay](frankenweather.md#cb-growth-and-decay).
- **`--cb-growth`: the forecast target and ramp rate are now visible,
  not just the live (ramped) value.** The web UI's weather map popup and
  the `/weather/settings` zone cards both show the CB's current oktas/top
  alongside its forecast target (only when they actually differ) and its
  current growth/decay rate — this applies to the frankenrouter web UI
  too, since it serves the same `fw_webui.py` pages. The per-zone console
  log line (`Zone N [SRC]: ICAO @ lat/lon  CAPE=... CB n oktas base=...
  tops=...`) gains a `[CB ramp: ...]` suffix with the signed oktas/min
  and ft/min rate and the target, whenever that zone's CB is actively
  growing or decaying.
- **TEMPORARY: now sends `demand=LeftPfdAlt` (Qs562) once whenever PSX
  resumes (including the initial connect).** PSX never sends this field
  unprompted (`Mode=DEMAND`); a single demand makes PSX stream it on
  every change for the rest of that connection, which the router logs
  alongside `Qs121`/`PiBaHeAlTas` -- giving direct evidence of the PFD's
  own indicated altitude for the ongoing altitude-jump investigation
  (see `project_frankenweather_altitude_jump` memory) via log analysis,
  instead of only inferring a jump from QNH/weather writes.
  frankenweather doesn't otherwise track or analyze this value itself.
  Remove the `demand=LeftPfdAlt` send once that investigation is closed.

## 1.7.0 (2026-10-15)

- **Fix: arm `Qi243` (`WxSlowTransit`) before `SigmetOn` writes** — found
  via forensic log analysis of a real flight where an instant PFD
  altitude snap landed ~17 seconds after the last `Qi243` arm had
  expired, right as a `SigmetOn` write landed. Aerowinx (Hardy) later
  confirmed PSX already auto-smooths SIGMET-driven changes regardless of
  `Qi243`'s state, so this isn't strictly necessary, but it's cheap,
  harmless insurance either way.
- **New (experimental, command-line only): `--sync-tropopause`** — keeps
  PSX's Planet Weather page tropopause altitude/temperature/wind set to
  "Constantly" with real values, computed from Open-Meteo's
  pressure-level data at the aircraft's current position (WMO
  lapse-rate definition), refreshed on the same cadence as the weather
  zones. `WxAloft`'s on-the-wire format was reverse-engineered live
  against a real PSX instance. Wind variability is always written as 0
  (no ensemble-spread data available from Open-Meteo) and gust is always
  set equal to speed. Off by default.

## 1.6.0 (2026-10-15)

- **New (experimental, command-line only): `--primary-metar-source
  {vatsim,noaa}`** (default `vatsim`) and **`--metar-live-refresh`**.
  `--primary-metar-source` picks which bulk METAR source (VATSIM's `/all`
  feed, or NOAA's hourly cycle file) is refreshed on the normal 30-minute
  schedule and wins precedence when both have data for the same station;
  only one is fetched regularly — if that fetch fails, the other is
  fetched immediately as a stand-in, and falls back to the primary
  automatically as soon as it succeeds again, with no extra state to
  track. `--metar-live-refresh` additionally tries a short-timeout
  (30s), single-batch live fetch straight from VATSIM
  (`metar.vatsim.net` accepts a comma-separated ICAO list) for just the
  airports already selected as zones each cycle, falling back silently
  to the already-cached METAR on failure or timeout — it never changes
  which airport a zone snaps to, only how fresh that airport's text is.
  Both default to today's exact behavior (VATSIM bulk feed only, 30-minute
  cache, no live per-station fetch) unless explicitly enabled. Zone source
  reporting (web UI, console log) now also distinguishes "VATSIM" from
  "NOAA" per zone instead of always saying "VATSIM" whenever any real
  METAR was used.

## 1.5.0 (2026-10-14)

- **Fix: never re-send a weather variable PSX already has** — found via
  forensic log analysis of a real KLAX-EGLL flight where the PFD altitude
  instantly dropped ~500ft at FL150 during an entirely ordinary,
  unremarkable 5-minute zone refresh. The write that preceded the jump
  turned out to be byte-for-byte identical to what had been sent 5 minutes
  earlier for the same (currently-focused) zone — strong evidence that
  re-sending data PSX already has can itself trigger PSX to apply the
  injection as an instant snap instead of a smooth transit. `WxMode{N}`,
  `Wx{N}`/`WxBasic`, `Metar{N}`, and `WxAutoSet` are now only actually sent
  to PSX when the value differs from what PSX's own subscribed-variable
  state already shows — previously every one of these was resent
  unconditionally on every zone-update cycle regardless of whether
  anything changed. Self-heals after a PSX reconnect or after FrankenWeather
  spends time in "disabled" mode with PSX's own GUI/manual weather controls
  in use, since both naturally refresh the underlying cache without any
  extra logic. `Qi243` (the slow-transit arm) and `WxBurst` (turbulence
  injections) are unaffected — both are one-shot commands, not persistent
  state, and always still transmit.

## 1.4.0 (2026-10-06)

- **New: the SIGMET parser now extracts every hazard type** (TS, TURB,
  ICE, VA, TC, MTW), not just thunderstorms. Several real-world format
  gaps were fixed along the way, found against two real downloaded SIGMET
  feeds: inconsistent `Hazard:`/`HAZARD:` casing, entries that omit the
  `WI` polygon anchor entirely, missing whitespace between the lat/lon
  groups, and altitude ranges in several formats the old TOP-FL-only
  parser didn't handle. An entry describing an open boundary (e.g. `N OF
  LINE ...`) is deliberately skipped rather than approximated, since
  connecting a boundary line's own endpoints into a polygon would draw a
  shape nothing like the real (unbounded) hazard area. CB generation
  stays TS-only as before; the new hazard types are additive.
- **New: a "disable PSX SIGMETs" control**, exposed on the router's `/efb`
  page next to a new "in SIGMET area" indicator (showing every hazard
  type the aircraft's current position is inside, not just TS).
  `SigmetOn` (`Qi262`) is a 0-15 bitmask with only two documented bits
  (embed in the planet weather model; trigger an immediate download), so
  the write now tracks PSX's own live value and read-modify-writes just
  those two bits instead of ever sending a bare literal that could
  clobber the other, undocumented bits. FrankenWeather keeps downloading
  and parsing SIGMETs for its own CB logic regardless of this toggle; it
  only controls whether PSX's own weather engine also consumes them.
- **Fix: the "zone protected from relocation" log line could repeat
  dozens of times for one ongoing situation** — a protected zone can stay
  due-for-relocation for many minutes as the aircraft continues past it.
  Now throttled to once per 5 minutes per zone.

## 1.3.0 (2026-10-06)

- **Bug fix: a long cruise descent could relocate every weather zone in
  the same pass**, including whichever one was actually influencing the
  aircraft — discovered via forensic log analysis of a real flight where
  the PFD altitude briefly jumped ~95 ft with no corresponding desync
  anywhere in the router/network layer. `_check_and_relocate()` now always
  leaves the single nearest zone and PSX's own reported `FocussedWxZone`
  in place, even when they'd otherwise qualify for relocation, and logs
  when that protection actually blocks a due relocation.
- **Bug fix: a zone still ahead of (or abeam) the aircraft could be
  relocated while maneuvering** (a hold, vectoring, or below cruise
  altitude) — the "must have passed behind first" rule previously only
  applied in cruise. Real CBs only drift slowly with the wind, so a zone
  that might be holding one and is still visible ahead must never
  disappear or jump; this is now enforced in both flight phases.
- **New: `_update_zones()` logs when a zone's QNH or wind content changes
  materially** (≥1 hPa or ≥3 kt/10°) between update cycles, instead of
  only ever printing the current snapshot — reconstructing the incident
  above required diffing raw METAR traffic by hand because the log never
  said anything had changed.
- Documented the zone placement/relocation rules (cruise vs. maneuvering,
  and the phase-switch thresholds) in `docs/frankenweather.md`.

## 1.2.4 (2026-10-03)

- **Fix: no console output while retrying the PSX connection** (e.g. PSX
  not started yet at launch) - `psx.Client`'s default logger is a no-op,
  and frankenweather never wired it up, so a down/not-yet-started PSX
  produced total silence every ~12s instead of any visible retry
  feedback. Its connect/retry/disconnect messages are now logged at INFO
  by default; everything else it logs (every TX/RX, every subscription)
  stays at DEBUG.

## 1.2.3 (2026-09-27)

- **Bug fix: a zone anchored on a real airport's own METAR could use an
  arbitrarily old observation** (discovered live: UKLL's VATSIM METAR was
  ~18 days old, giving QNH 1011 vs. ~1026 at every neighboring
  Open-Meteo-synthesized zone) if that airport's real-world reporting had
  gone dark — as is the case for several closed Ukrainian airports since
  the 2022 airspace closure, where VATSIM keeps echoing the last real
  report indefinitely, sometimes years old. A METAR whose own observation
  time is more than 2 hours old is now discarded in favor of Open-Meteo
  for that zone, with the discard logged (console) and shown in the
  zone's reason string (web UI).

## 1.2.2 (2026-09-25)

- **New feature: broadcast `addon=FRANKENWEATHER:CORRIDOR_CHANGED:<uuid>`
  whenever the enroute wind importer actually writes a new wind corridor
  to PSX** (genuinely new wind data, or a reroute) — not on every hourly
  poll that ends up resending nothing. Lets other addons react to a wind
  corridor update without polling `WxCorridorTxt` themselves.

## 1.2.1 (2026-09-19)

- **Bug fix: the per-run event log (see 1.2.0) was written in the wrong
  encoding.**

## 1.2.0 (2026-09-13)

- Always log frankenweather's output to
  ~/.cache/frankenweather/logs. Keep the last 10 logs, and make them
  easily downloadable via the web UI

- Coarsen zone-weather Open-Meteo cache to a 1° grid. This makes it a
  little more likely we can still update or move a weather zone even
  during a (brief) OpenMeteo outage.

## 1.1.0 (2026-09-12)

- **New feature: fall back to our own METAR cache instead of ceding to
  PSX's automatic weather when Open-Meteo can't be reached.** Previously,
  a failed Open-Meteo fetch handed weather control back to PSX's own
  METAR-based `WxAutoSet`, which is likely a contributor to the "PFD
  altitude jump" issue. Now, frankenweather keeps placing its own weather
  zones, but repositioned onto real stations from our own cached VATSIM
  METARs, falling back further to NOAA METARs if VATSIM is also
  unavailable. Also improves general handling of a failed Open-Meteo
  fetch.

- **New feature: option to shift a weather zone so PSX's CB placement
  can't land on top of the departure/destination airport.**

- **Improvement: METAR `TEMPO`/`BECMG`/`PROBnn` trend groups are now
  modeled separately for CB generation**, instead of being folded into
  the current observation like every other token. Previously a trend
  group's CB content (e.g. `TEMPO ... BKN008CB`) was applied as if it
  described current conditions. A bare `PROBnn` now rolls once per
  distinct METAR observation and holds for that observation's full
  validity if it hits; a `TEMPO` (bare or `PROBnn`-gated) fluctuates on
  an irregular 10-30 minute timer with reduced, randomized coverage
  while "on", avoiding flicker tied to the normal weather refresh cycle.

- **Bug fix: PSX wasn't downloading SIGMETs when frankenweather manages
  the weather zones**, since PSX only fetches METARs (and SIGMETs
  alongside them) once at startup, before frankenweather has taken over.
  frankenweather now triggers a SIGMET download (and makes sure PSX
  actually uses the data) every 30 minutes while it's managing the
  weather zones.

- **Bug fix: D-ATIS requests could come back `UNAVAIL`** when
  frankenweather is managing the weather zones (since no METARs are
  downloaded and PSX's own D-ATIS falls back to the 7 zones, which don't
  cover every airport). Fixed for both request paths: the ACARS-menu
  request (detected via the `Qs464`/`MetarsUp` field in the 5-second
  window before it's available to print) and the ALTN page request
  (detected by spying on the printer buffer `Qs119`, replacing the
  `UNAVAIL` message with a supplemental print).

- **The enroute wind updates feature is now enabled by default**, having
  proven mature enough in testing.

- **The web UI is now enabled by default**, on port 9747.

- Removed `frankenmsfsbridge.py` - MSFS-sync data (in-cloud, QNH, wind)
  is now provided by the third-party PSX.NET.MSFS.Client addon instead,
  so a separate psxhacks-native sender is no longer needed.
