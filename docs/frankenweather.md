# FrankenWeather

Real-world weather and turbulence injection for Aerowinx PSX.

## Who this is for

The goal is better "real weather" (i.e. not manually set or historical
weather) in PSX using all available data sources (including MSFS). If
you are looking for something that will set MSFS weather to be what the
PSX weather is, FrankenWeather is not the answer.

In short: if you want PSX's weather to track true, live real-world
conditions continuously as you fly — rather than a situation someone
set once (by hand, or baked into a saved situ) and never updated again
— this is for you.

### Major features at a glance

- Live weather zones, sourced from real airport METARs and Open-Meteo
  forecasts, placed and continuously updated around the aircraft as you fly.
- Realistic CB (cumulonimbus) placement driven by actual atmospheric
  CAPE/CIN data and TS SIGMETs, not hand-set oktas, checked against live
  weather radar (RainViewer) and satellite lightning. The lightning comes
  from EUMETSAT's MTG Lightning Imager (© EUMETSAT) over Europe, Africa,
  the Atlantic, the western Indian Ocean and northern South America (it
  doesn't see southern Brazil, Paraguay, Uruguay or Argentina: measured
  against GOES 2026-11-20), and with `--glm-lightning`
  (testing, needs h5py) also from the GOES satellites' Geostationary
  Lightning Mappers (NOAA, free) over the Americas, the Atlantic and the
  eastern Pacific: one file of about 250 KB a minute, turned into the same
  flash pixels as MTG's (0.05°, per 5 minutes, the latest 15 minutes). Live
  2026-11-20 over north-east Argentina MTG had no flashes while GOES had
  about 2,000 in 15 minutes within 300 nm. Asia and Australia have none.
- A dedicated terrain-based turbulence engine — mountain wave, rotor,
  mechanical, wind-shear CAT, plus CB/CAPE/PIREP/G-AIRMET-driven bumps —
  using real wind and satellite terrain data.
- Optional sync with MSFS (in-cloud state, QNH, wind), when using MSFS
  for visuals, fed by PSX.NET.MSFS.Client.
- A simulated enroute wind uplink using real current forecast data,
  compared against your flight plan.
- Real-world clock sync, and a web UI for live status, tuning, and a
  weather map.

See the [full feature list](#frankenweatherpy) below.

### Adding it to an existing PSX sim

FrankenWeather connects to PSX like any other add-on (BACARS, PSX.NET,
etc.) — it does not require frankenrouter, a particular sim topology, or
any change to how you already run PSX.

A standalone Windows EXE is available, no Python required — download it
from the project's
[GitHub Releases page](https://github.com/macroflight/psxhacks/releases).
Look for a tag named `frankenweather-v<version>` (e.g.
`frankenweather-v1.2.0`) — built from the `main` branch, the stable,
officially released version. A tag named
`frankenweather-testing-v<version>` instead (marked "Pre-release" on
GitHub) is built from the `testing` branch, for trying out changes
before they're officially released; see
[docs/developers/BuildSystem.md](developers/BuildSystem.md) for the full details.
For most users — a single PC running PSX on its default port — just start
the EXE and then control everything from
[http://localhost:9747/](http://localhost:9747/); no setup or arguments
needed.

Otherwise, to run it from source:

1. Install Python 3.13 and the packages listed below (`pip install aiohttp
   pyproj numpy rasterio requests`).
1. Run `python frankenweather.py`, pointing `--psx-host`/`--psx-port` at
   wherever PSX (or your frankenrouter, if you use one) is listening.
   Both default to `127.0.0.1:10747`, so if PSX is running on the same PC
   on its default port, no arguments are needed at all.
1. That's it. A web UI is on by default at `http://localhost:9747` (see
   `--web-port`/`--no-web-ui` below) for live status and tuning.

If you already use `start_scripts` (see `start_scripts/README.md`), set
`$StartFrankenweather = $true` in your override file instead of running it
by hand.

### If you use MSFS for visuals

- **MSFS sync requires PSX.NET.MSFS.Client version 10.5.1 or later** —
  download it from <https://planning.simfest.co.uk/downloads>. Without it,
  everything else in FrankenWeather still works; you just won't get the
  in-cloud/QNH sync described above.
- **Recommended: the "Mawea Design - Storm Package" addon for MSFS**, also
  from <https://planning.simfest.co.uk/downloads>. FrankenWeather generally
  places more CBs, and in locations (e.g. over the ocean) where PSX's own
  weather logic rarely puts any, than you may be used to — this addon gives
  MSFS itself the visual storm assets to actually show them.

## frankenweather.py

Replaces PSX's built-in weather with real-world data fetched from
[Open-Meteo](https://open-meteo.com/) and VATSIM live METARs. Up to 7
PSX weather zones are placed and maintained around the aircraft, and
updated every few minutes as the aircraft moves.

Features:

- Zones snap to the nearest real airport when one is within 25 nm; the
  weather for that zone is taken from the airport's live METAR when
  available (NOAA by default, VATSIM as the fallback; `--primary-metar-source
  vatsim` swaps them), otherwise from Open-Meteo.

- Within 100 nm of the FMC departure or destination airport, that
  airport's METAR is fetched from the active METAR source every 10
  minutes (the bulk METAR download is only refreshed every 30 minutes),
  so the weather there follows a new METAR or SPECI within minutes.

- A zone whose weather comes from Open-Meteo gets a METAR with only its
  name, the time it was placed and a remark saying why
  (e.g. `X328 231230Z RMK FRANKENWX MODEL WX`), so PSX's Instructor
  doesn't keep showing an old zone's METAR. It has no weather in it on
  purpose: PSX rebuilds a zone's weather from its METAR whenever the
  METAR's weather changes, which would undo frankenweather's CBs, cloud
  layers and tops. Every METAR frankenweather gives PSX that isn't a
  real one says so in a `RMK FRANKENWX ...` remark.

- CAPE-based CB (cumulonimbus) generation: computes CB coverage and
  tops from Open-Meteo CAPE/CIN data, with convective inhibition
  suppression when CIN is high. CB tops are at most 2,000 ft above a
  typical tropopause for the latitude and season (about 33,000-39,000 ft
  in the mid-latitudes, 30,000 ft in a polar winter) and never above
  44,999 ft, PSX's limit. With [the SIGMET system](#sigmets) (on by default),
  frankenweather also places zones in TS/TC SIGMET areas and gives them
  CBs, since TS SIGMETs report observed thunderstorms. METAR convective indicators (TS, TSRA,
  SHRA, GR, LTG, FC, CB sky groups) are also used and can override or
  refine the Open-Meteo prediction. See [CB growth and
  decay](#cb-growth-and-decay) below for the CB life cycle switches.

- METAR TEMPO/BECMG/PROBnn trend groups are parsed out and excluded from
  the current-observation weather (they describe conditions that *may*
  occur during a 2-hour trend period, not what's happening now) — but
  their CB content is not simply discarded:
  - A bare `PROBnn` group (e.g. `PROB40 BKN030CB`) is rolled once per
    distinct METAR observation; if the roll hits, that CB coverage is
    applied at full value for as long as that exact observation remains
    current (i.e. until the station's METAR text next changes).
  - A `TEMPO` group (bare, or gated by a `PROBnn`, e.g.
    `PROB40 TEMPO ... BKN008CB`) fluctuates: once armed (a bare `TEMPO` is
    always armed; a `PROBnn`-gated one only if its own roll hits), it
    randomly toggles on and off on an irregular 10–30 minute timer — not
    tied to the normal weather refresh cycle, so the transitions don't
    look driven by it — using a reduced, randomized CB coverage each time
    it's "on" rather than always the group's full stated value.
  - `BECMG` groups are excluded from the current observation but are not
    otherwise modeled.
  - This state is tracked per airport (not per weather zone), so it
    survives a zone relocating to point at a different station and
    persists correctly if the same station comes back into scope later.

- Zone placement and relocation adapt to flight phase (cruise vs.
  maneuvering), and a zone currently ahead of the aircraft is never moved
  out from under a CB the pilot might be watching. The FMC departure and
  destination airports always get their own dedicated zone. See
  [Zone placement and relocation](#zone-placement-and-relocation) below.

- **CB near dep/arr: Off / Move zone / Remove CB** (Off by default;
  Weather zones page, or `/efb` on the router): keeps CBs at least 15 nm
  from the FMC departure and arrival airports, e.g. so an approach on
  VATSIM doesn't go through a CB nobody else has. PSX always puts a
  zone's CB 7 nm from the zone's centre, at a direction that keeps
  turning with the time. Changing the setting in flight takes effect at
  once.
  - **Move zone** moves the airport's weather zone 23 nm north of the
    airport, so its CB is always 16-30 nm from it. The zone keeps the
    airport's real METAR. Moving the zone in flight can make the PFD
    jump, and the airport's QNH and wind come from whichever zone is
    nearest. Other zones' CBs aren't moved.
  - **Remove CB** removes the CB of every zone centred within 22 nm of
    either airport, whatever it came from (METAR, CAPE, radar,
    lightning or a TS SIGMET). The zones stay where they are, so the
    airport keeps its real weather. PSX makes a CB of its own from a
    METAR with TS (also VCTS and recent TS) or CB clouds, so for these
    zones frankenweather takes those out of the METAR it gives PSX
    (`+TSRA` becomes `+RA`, `BKN020CB` becomes `BKN020`) and adds
    `RMK FRANKENWX CB REMOVED`. Showers, TCU, trend groups and remarks
    don't make PSX CBs and are left alone. The D-ATIS still shows the
    real METAR.

- Terrain turbulence (from the `frankenturb` engine, now integrated):
  fetches real-time wind from Open-Meteo and terrain elevation from
  Copernicus GLO-30 DEMs, then injects `WxBurst` events into PSX at up
  to 5 Hz. Once a minute the log sums up the bursts (also without
  `--debug`): how many per kind and in which direction (e.g. `SPD 8
  +3/-5`: eight speed bursts, three faster), how big, and from which
  turbulence source. Turbulence kinds modelled: terrain wave, rotor, mechanical,
  wind-shear CAT, CB, CAPE-driven convective, PIREPs, and G-AIRMET
  regions. The MCDU C (observer panel CDU) shows a live status display
  and allows tuning of intensity per turbulence type. CB turbulence is
  centred on the CB itself, where PSX draws it on the radar (7 nm from
  its zone's centre): a core (CB interior) of half the distance to the
  nearest other zone but at most 10 nm, times the CB lateral size bias
  (5 nm at the default 50 %) -- about the size PSX draws on the radar,
  which is smaller when another weather zone is near (red about 6 nm,
  amber about 19 nm across with no zone within 45 nm, 30 % of that with
  one 7 nm away). Around a weather-zone CB, at the CB's cloud levels,
  the turbulence beyond the core follows the colours PSX's weather radar
  shows there: moderate in the amber echo (held at 42-50 %, with more
  frequent moderate and severe events), light to moderate in the solid
  green that PSX draws around every CB out to about 20 nm (32 % at the
  amber's edge to 24 % at 20 nm), and fading to nothing in the speckled
  green by 30 nm. That matches how a real 747 pilot reads the radar:
  solid green is uncomfortable for passengers but not dangerous, amber
  is where it starts to get dangerous, red and magenta are avoided.
  PSX adds its own CB turbulence within about 14 nm (see
  `docs/developers/psx-cb-turbulence.md`). Planet-weather CBs keep a
  flank zone of up to 20 nm, depending on how big the CB is. DEM tiles (about
  33 MB each, 1°×1°) are downloaded on demand and cached at
  `~/.cache/frankenturb/terrain/`, up to 10 GB in total
  (`--terrain-cache-gb`, 0 for no limit). Above the limit the least
  recently used tiles are deleted, and downloaded again if needed. Up
  to 9 tiles are held in memory. For scale: WorldFlight 2026 passes
  near about 1,000-1,700 tiles (30-55 GB) around the world, but a
  single sector needs at most about 130 (4.3 GB).

- MSFS in-cloud sync: reads the MSFS in-cloud state and adjusts the
  active PSX weather zone's cloud layers so that PSX shows the aircraft
  as in-cloud when MSFS does. This keeps PSX icing in sync with MSFS
  conditions. Enabled by default; toggleable from the web UI.

- MSFS QNH sync: compares the MSFS sea-level pressure with the active
  PSX zone QNH. In **CHECK** mode (default) a warning is logged when
  they differ by more than 1 hPa. In **SYNC** mode the PSX QNH and
  METAR are also updated to match MSFS (an updated METAR gets
  `RMK FRANKENWX QNH SYNC`). QNH is never updated for zones
  whose weather comes from a real METAR (the METAR is authoritative for
  those zones). Toggleable between CHECK and SYNC from the web UI.

- Enroute wind importer: simulates requesting an updated enroute wind
  forecast via datalink mid-flight, using Open-Meteo instead of a real
  dispatch link. On by default; toggleable from the `/weather/enroute-wind`
  web page. See [Enroute wind importer](#enroute-wind-importer) below.

- The MSFS in-cloud, QNH and wind data is provided by PSX.NET.MSFS.Client
  over the PSX network (`addon=FRANKENMSFSBRIDGE:{...}` messages). The
  MSFS sync features have no effect unless it is connected.

- Smooth weather transitions: every zone weather, MSFS sync, or wind
  corridor write is preceded by PSX's `Qi243` ("WxSlowTransit") smooth-
  transit hint, so a zone relocation, a new area becoming active, or an
  MSFS QNH sync never shows up on the instruments as an abrupt jump
  (e.g. the altimeter snapping to a new value). This does not apply to
  turbulence `WxBurst` injections, which are deliberately abrupt.

- Web UI: a standalone HTTP server is started by default on port 9747;
  pass `--web-port PORT` to use a different port, or `--no-web-ui` to
  disable it entirely. The same UI is also available through the
  frankenrouter web interface. Every page has the same buttons at the
  top linking to all of them (plus Refresh, and in the router's UI a
  button back to the router's start page):
  - **Map**: the live weather map. Each CB is marked where PSX actually
    draws it, with its turbulence area as rings: the core (red), the
    inner (yellow) and outer (green) half of the flank zone.
  - **Weather zones**: an Auto/Manual switch (plus Pause and Disable).
    In Auto, the details of all seven weather zones; in Manual, the
    manual weather controls, whose weather goes into all seven zones.
  - **Enroute wind**: the enroute wind importer.
  - **SIGMETs**: with the SIGMET system (on by default), see [SIGMETs](#sigmets).
  - **Extra turbulence**: turbulence tuning.
  - **Cold weather**: the fuel temperature, fuel cooling, Advanced Icing
    and their log, see [Cold weather page](#cold-weather-page).
  - **Config and logs**: the configuration file (save/load/reset), the
    time sync toggles, and (in frankenweather's own UI) its log files.

- Settings persistence: pass `--config-file PATH` to load every setting
  the web UI can change from a TOML file at startup, and to enable the
  "Save current settings to file" / "Load settings from file" buttons
  on the settings page. See [Configuration file](#configuration-file)
  below.

- Open-Meteo usage: the free API allows 10,000 calls a day per internet
  connection (Open-Meteo counts one call per location per 10 variables),
  so frankenweather keeps what it downloads on disk under
  `~/.cache/frankenweather/` and `~/.cache/frankenturb/` and asks again
  only when it needs to:
  - Zone weather counts as fresh for 15 minutes (Open-Meteo's current
    conditions change every 15 minutes), and only the zones without
    fresh data are fetched.
  - The turbulence engine's wind and CAPE, and enroute wind, are 2-day
    hourly forecasts: every hour is served from the download, which is
    refreshed after 6 hours (newer forecast runs) or for a new 1° square
    or new waypoints.
  - While Open-Meteo can't be reached (or refuses, e.g. after the daily
    limit), older downloads are used instead (zone weather up to 90
    minutes: it's the weather of when it was downloaded, so a zone with
    older data takes a METAR from an airport within 120 nm, or keeps its
    weather; forecasts
    as long as they cover the hour), so the zones still
    get new METARs and setting changes, and turbulence keeps working.
    The console banner says how old the data is.
  - After a refusal or failure nothing asks Open-Meteo again for 5
    minutes (30 seconds after other failures).
  - The calls made in the last hour and 24 hours are shown on the
    Weather zones page and logged once an hour (kept across restarts).
  - Only one request is sent at a time, and one that Open-Meteo doesn't
    accept within 5 seconds fails at once.
  - The terrain turbulence only uses an older wind profile while it's
    less than 100 nm away and less than an hour old.

- A paid Open-Meteo subscription (e.g. for WorldFlight or a lot of
  testing; the Standard plan's 1 million calls a month is plenty): give
  its API key with `--om-api-key KEY` or `--om-api-key-file FILE` (a
  file holding just the key). frankenweather then uses Open-Meteo's
  customer API (reserved servers, no daily limit), and the usage on the
  Weather zones page and in the log says "paid API". The key is never
  written to the log. If Open-Meteo refuses the key (e.g. the
  subscription has expired or used up its monthly calls; Open-Meteo
  doesn't enforce the monthly limit yet), frankenweather logs a large
  warning and uses
  the free API until it's restarted; the Weather zones page says so too.

Requires `aiohttp`, `pyproj`, `numpy`, `rasterio`, and `requests`:

```bash
pip install aiohttp pyproj numpy rasterio requests
```

Key options:

```text
--psx-host HOST      PSX server hostname (default: 127.0.0.1)
--psx-port PORT      PSX server port (default: 10747)
--web-port PORT      Port for the standalone web UI (default: 9747)
--no-web-ui          Disable the standalone web UI
--log-web-requests   Log every web UI request (off by default)
--config-file PATH   Load/save web-UI settings from this TOML file
                     (default: ~/.frankenweather.toml)
--save-logs DIR      [DEVELOPMENT] Save enroute wind diff data per flight
--debug              Verbose logging
```

### Zone placement and relocation

Up to 7 PSX weather zones are placed around the aircraft and relocated as
needed. Two things don't depend on flight phase: a zone serving the FMC
departure or destination airport is never relocated (the destination's
within 200 nm, the departure's until 50 nm after takeoff: then it's an
ordinary zone again, free for the weather ahead; a return to the departure
airport would be entered as the new destination), and the single zone
nearest the aircraft — which is almost always also the one PSX itself
reports as its active weather zone (`FocussedWxZone`) — is never relocated
either, even when it would otherwise qualify below. Relocating either would
strip away the zone actually influencing the aircraft and force an abrupt
handoff to unrelated weather with no gradual transition. Nor is a zone
whose CB turbulence can still reach the aircraft (its core plus flank
zone, or plus the anvil zone above the cloud top), for any reason: the
turbulence would stop at once.

When frankenweather is restarted, it keeps the zones PSX already has, if
frankenweather placed them (PSX isn't running its own weather) and all
seven are within 500 nm, so the CBs on the weather radar stay where they
are; it logs "Zone N: kept from before restart". Otherwise (a new
session, a situ somewhere else) it places them all fresh.

PSX takes the local weather (QNH, wind) from the nearest zone. When every
zone is more than 150 nm away (zones placed far ahead, or in TS/TC SIGMET
areas), one zone becomes an "anchor" 50 nm behind the aircraft, without
any CB: out of sight of the radar, and too far from the zones ahead to
shrink their CBs. It moves back to 50 nm behind once it's 100 nm behind,
and is an ordinary zone again as soon as another zone (e.g. the
departure or destination airport's) is within 150 nm, or in maneuvering
mode, where the zones are all around the aircraft anyway.

Everything else follows two separate rules: *where* a new or relocated zone
is placed depends only on the placement mode (cruise or maneuvering, see
below); *when* a zone may be relocated depends on the aircraft's altitude.

- **Where, cruise mode** (not maneuvering, at any altitude): new or
  relocated zones are placed ahead of the aircraft, in a forward-biased fan
  (a range of distances ahead, with some lateral spread either side of
  track).
- **Where, maneuvering mode** (true airspeed below 200 kt, or a heading
  change greater than 180° within 5 minutes, see below): new or relocated
  zones are scattered all around the aircraft at a random bearing instead
  of ahead of it, since the aircraft's track is no longer a reliable
  predictor of where it's headed next, and no further than 50 nm inside
  `low_alt_dist` (150 nm by default; also zones in TS/TC SIGMET areas), so
  a hold doesn't carry the aircraft out of range of a zone just placed.
  The zones placed at startup are also all around the aircraft.
- **When:** a zone only becomes eligible for relocation once the aircraft
  has actually flown past it (the zone is more than 90° off the nose, in
  both modes), and then only once it's more than `cruise_behind_dist`
  (default 50 nm) behind at or above 18,000 ft in cruise mode, or more
  than `low_alt_dist` (default 200 nm) behind below 18,000 ft or while
  maneuvering.

In both modes, a zone that is still ahead of (or abeam) the aircraft is
never relocated — real CBs only ever drift slowly with the wind, so
nothing currently in front of the aircraft, where it could be visible on
radar, is allowed to disappear or jump.

**Entering/exiting maneuvering mode:** true airspeed below 200 kt (taxi,
takeoff roll, approach, landing) always forces maneuvering mode. Above
200 kt, frankenweather tracks the aircraft's total heading change over a
trailing 5-minute window: accumulating more than 180° of change enters
maneuvering mode (a hold or vectoring pattern), and dropping back below
60° of change exits it back to normal cruise placement. The 180°/60° gap
is deliberate hysteresis so the mode doesn't flap back and forth near the
boundary. Within 10 nm of the FMC route (route zones on, see below) only
the turns the route doesn't explain count: the heading change minus the
course change of the route leg going the aircraft's way, summed with its
sign. A SID or STAR that turns back on itself, or vectors a few miles off
the route, add next to nothing, while a hold adds 360° a circuit (a 180°
departure turn re-placed every zone and took away CBs the crew could see,
live flight 2026-11-20). Entering maneuvering mode re-places all zones around the
aircraft, except those whose CB turbulence can still reach it; leaving it
doesn't move any zone, the ordinary relocation rules take over.

**Route-based zones (`--route-zones`, on by default since 2026-11-19;
`--no-route-zones` or the web UI's CB test page switch it off):** the zones
follow the FMC route instead of the heading:

- **On the route** means within 10 nm of a leg and tracking within 30° of
  it, for 2 minutes. While on it, new and relocated zones go 150-250 nm
  ahead *along the route*, through its turns, with the lateral spread
  measured from the route. A zone counts as behind by its distance along
  the route. A zone just beyond a turn is behind by the heading before the
  turn, but it isn't moved.
- **Off the route** means more than 10 nm away for 3 minutes, for example
  a deviation around weather or a direct routing the FMC doesn't know.
  Zones are then planned from the heading as described above, and the
  route takes over again once the aircraft is back on it. Within 60 nm of
  the route a zone still counts as behind by its distance along the route,
  so a deviation doesn't move any zone: one ahead along the route stays.
- **Vectors near the destination**: when the aircraft has been following
  the route and then, within 60 nm of the destination, goes more than 5 nm
  or 45° off it, frankenweather switches to maneuvering mode. It stays
  there until landing or a new route (a diversion, a new arrival).
- **A new route** counts once the aircraft follows it; until then zones
  are planned from the heading.
- The track is the aircraft's track over the ground over the last 30-60 s,
  not its heading, which a crosswind can turn 10° or more.
- The route is every waypoint of the flight, passed ones included, plus the
  destination airport. The log shows each change ("Route zones: following
  the FMC route", "off the FMC route", "vectors near ...").

**Radar decides where CBs are (`--radar-cbs`, testing):** with this option,
or switched on on the web UI's CB test page, the weather radar (RainViewer,
the same picture VATSIM Radar can show) decides where the CBs are.

- Every new radar frame (every 10 minutes), frankenweather finds the storm
  cells within 250 nm of the aircraft: areas of 40 dBZ or more, with large
  areas split into their stronger cores. Each cell's motion comes from the
  three newest frames. A cell peaking at 43 dBZ or more counts as a CB;
  weaker ones count as towering cumulus or heavy showers.
- **A CB the crew may see stays.** A CB within 200 nm (`--cb-visible-dist`,
  how far CBs show out of the window), or within the longest range either ND
  has shown the weather radar at in the last 10 minutes (WXR on, terrain off,
  not PLN) if that is farther (at most 320 nm, the weather radar's reach; a short zoom-in doesn't count; off with
  `--no-cb-visible-nd`), and not behind the aircraft (holding or vectored:
  all round) is never moved to make room for
  another storm, however strong: zones without a CB, or with one behind or
  farther away, are used for new storms. A CB goes once it is behind, or
  when its own storm dies on the radar: then it stays while the crew may see
  it (in that range, within 90° of the heading, also while turning), at most
  30 minutes, and goes once it is out of view (shrinking away only with the
  CB oktas life cycle on).
- **No new CB appears where the crew may see it:** within `--cb-visible-dist`
  (200 nm) and 120° either side of the nose (the side windows too;
  `--cb-visible-angle`, as many sims show less), or within
  the range an ND showed the weather radar at in the last 10 minutes and 90°;
  holding or vectored, anywhere within that range. New CBs are born just
  beyond (the radar storms are looked for out to 120 nm past it: 320 nm, or
  440 nm with an ND on 320 nm). With the CB planner, see below. In the first 2 minutes after a start or a
  situ load CBs may appear anywhere.
- **The CB's size comes from the radar and the satellite.** Its oktas go by
  the cell's strength (3 below 50 dBZ, 4 to 54, 6 to 59, 7 from 60), one
  more for a cell of 200 km² or more. Its top is the highest cloud top within
  10 nm of the cell in EUMETSAT's Meteosat Cloud Top Height image (one every
  15 minutes, about 20-25 minutes old; Europe, Africa, the Atlantic and the
  Middle East), at least 15,000 ft and at most PSX's 44,999 ft. Without an
  image under 45 minutes old (elsewhere, or a download failure), the top
  goes by the cell's strength: 25,000 ft at 45 dBZ and 1,500 ft more per
  dBZ, up to the tropopause plus 2,000 ft. Where a high cloud deck covers
  the storm, the satellite sees the deck; the CB then reaches that high.
- **Every sim sees a storm's CB in the same place.** Two or more PSX sims
  flying in the same area put the CB on a radar storm at the same position,
  to well under a mile. Each frankenweather works it out from the radar
  frames alone, never from how long it has run or where its aircraft is:
  - the motion comes from the same three frames;
  - a cell cut off at the edge of the area one frankenweather looked at is
    left out;
  - a new frame takes over three minutes after its time, on the UTC clock
    every sim shares.

  The sims don't necessarily show the same set of storms: each has only
  seven weather zones, so an aircraft further ahead may use them for other
  storms.
- **Inside radar coverage** (RainViewer's own coverage map), a zone only
  keeps a CB when a radar CB lies within 20 nm of it. When the forecast has
  none there, the cell gives the zone one. The radar alone sets the CB's
  oktas and top, from the cell's strength and size, so every sim has the
  same CB. METARs and SIGMETs no longer create CBs there. A zone keeps its
  cell while the cell stays within 28 nm. New zones go onto the strongest
  unclaimed radar CBs ahead, from 40 nm ahead: a storm the pilot already
  sees on the radar is no surprise.
- **A CB on a radar storm sits exactly on it.** PSX draws a zone's CB 7 nm
  from the zone centre, on a bearing that turns once an hour, so
  frankenweather moves the zone centre to put the CB on the storm. The CB
  starts there at once and moves with the cell's tracked motion (once the
  cell has been tracked over the three frames, 20 minutes; otherwise it
  stays where the frame has it). On a new frame the CB moves smoothly, over
  10 minutes, from where the frame before puts the storm to where the new
  one does, so it never jumps. This works at any altitude, even while
  maneuvering. The CB stays alive as long as the radar has the cell, and
  fades when the cell is gone (or more than 12 nm away).
- **Outside radar coverage** (oceans, most of Africa, Russia, polar areas),
  inside TS/TC SIGMET areas and near satellite lightning (MTG),
  frankenweather makes up the storms, but every sim makes up the same ones:
  - storms can only be born on a fixed grid of points about 30 nm apart,
    with one chance per point per UTC hour;
  - whether a storm is born, when, exactly where near the point and how
    long it lives (30-60 minutes) come from the grid point and the hour,
    never from a random number of this frankenweather's own;
  - the chance comes from the SIGMET's qualifier (ISOL about one storm per
    7 points, OCNL one per 3, FRQ one per 2), lightning nearby (MTG, or
    GOES with `--glm-lightning`), or with `--sat-storms` (testing) a deep,
    cold satellite cloud top over thunderstorm air (below);
  - a storm moves with the 850-300 hPa mean wind at its grid point, from
    the same forecast hour.

  CBs go onto these storms exactly as onto radar storms (at the storm,
  moving with it, fading at the end of its life), and the SIGMET's other
  points get no CB. Elsewhere outside radar coverage nothing changes yet:
  the forecast places the CBs, and they differ between sims.
- **Satellite storms (`--sat-storms`, testing).** Outside radar coverage
  and away from lightning, a cold cloud top is the next best sign of a
  thunderstorm. The cloud tops come from the satellite nearest overhead:
  Meteosat over Europe, Africa and the Atlantic, and its Indian Ocean
  service (Meteosat-9, 45.5° E) over Central Asia, the Middle East and
  India (EUMETSAT, about 20-25 minutes late), GOES-East and GOES-West
  over the Americas and the eastern Pacific (NOAA's Cloud Top Height
  product, about 15 minutes late, needs h5py), and Himawari over Asia,
  Australia and the western Pacific (its infrared image from NASA's GIBS,
  about 45 minutes late; the height comes from a standard temperature drop
  with height). A grid point gets a storm chance when the highest top within
  10 nm is
  - as cold as -40 C and the forecast has thunderstorm energy (CAPE
    200 J/kg or more) and convective showers (0.1 mm/h or more) there:
    about one storm per 4 points;
  - as cold as -50 C with that CAPE but no forecast showers: about one per
    7 points (the forecast misses some storms the satellite sees).

  The CAPE and showers are the forecast's at the whole degree and hour.
  This is a rough guide, so the storms are made up near the cold tops, not
  on them. Checked against real radar on 2026-11-20 (0.5° points, radar
  storm of 43 dBZ or more within 20 nm): such a point had a radar storm
  21-22 % of the time over southern Brazil, 12-16 % near Darwin, and over
  the Gulf of Mexico the November cirrus (cold tops over CAPE, no showers,
  no storms) got almost no storms. Without the forecast check the satellite
  alone was wrong almost everywhere: 118 cold points over the Gulf, none
  with a storm.
- The log shows each frame ("Radar cells: ..."), and a zone's reason shows
  what the radar did ("radar: 52 dBZ cell 140 km2", "radar: no storm
  cell"). Needs Pillow.
- When RainViewer doesn't send all of a frame's tiles (it slows down
  clients asking for many at once, as at a start), frankenweather asks for
  the missing ones again a minute later and keeps using the frame it had
  (at a start: no radar decision yet) until it has them all, at most three
  tries ("Radar: 153 of 405 tiles not received"). Before, a start could
  run on a frame with no storms in it for up to 10 minutes. The same for
  the radar coverage map's tiles ("Radar: 4 of 12 coverage tiles not
  received"): without them no place counts as covered, so the radar has
  no storms (a headless sim had none for its first 8 minutes while its
  pair sim placed CBs, 2026-11-19).
- The radar and coverage tiles are kept on disk (`--radar-tile-cache DIR`,
  default `~/.cache/frankenweather/radar`, a few MB), so a restart reads
  the tiles it already has instead of asking RainViewer for them all again.
  A frame's tiles never change and are kept for an hour; coverage tiles
  for 6 hours. Several frankenweathers on one computer may share the
  folder (the headless test sims do). `--radar-tile-cache ""` keeps none.
- **CB placement planner (`--cb-planner`, testing, with `--radar-cbs`):**
  instead of filling free zones with the strongest storms ahead one at a
  time, frankenweather makes one scored plan for all movable zones every
  minute (rules agreed 2026-11-19):
  - each significant area of storms (at least a quarter as intense as the
    most intense one: dBZ, area and lightning) gets a CB before a strong
    area gets a second one; small weak areas only get zones left over;
    CBs stay at least 25 nm apart;
  - not startling the crew is a cost that grows the closer the CB is,
    instead of the rules above: within 40 nm (60 nm for a CB of 6/8 or
    more or with tops above 40,000 ft; `--cb-startle-dist 40:60`) no CB
    appears, goes or moves; farther out, where the crew may see it, a CB
    may appear, go or move for a clearly better one (3 times better at
    60 nm, 1.5 times at 100 nm, 1.2 times at the edge of view), and a CB
    the crew sees changes at most once in 10 minutes; out of view anything
    better will do;
  - in HDG SEL with the selected heading more than 5° off, it plans for the
    heading after the turn, and no new CB goes where the crew will see it
    on the roll-out;
  - a zone moved before the first radar frame after the start gets no
    forecast CB (it would follow whichever storm lay near it once the radar
    came).
  The log shows each plan that moves a zone or has a new radar frame ("CB
  planner: 55 storms in 11 clusters, 4 zones movable, 4 move(s)") and each
  move's storm and score. How it decides:
  [docs/developers/cb-planner.md](developers/cb-planner.md).

**Status map (`/devel` on frankenweather's web UI, read-only):** a map of
what frankenweather bases its CB placement on, served by frankenweather
itself (it needs no extra PSX data). Each layer can be switched on and off
(the choice is remembered in the browser):

- the radar picture, radar storm cells (weak ones apart), MTG lightning,
  the satellite cloud top image and the radar coverage;
- PSX's CBs as PSX shows them: red core, yellow radar echo, dashed circle
  for the CB turbulence, violet anvil, and an arrow for a drifting CB;
- the weather zones (a dot for zones without a CB), the crew's view
  (window and ND weather radar range), the aircraft, the route, TS
  SIGMETs and the planner's storm clusters.

Click a CB for why it is there (which storm or forecast placed the zone,
its strength, top and motion) and a SIGMET for its text. "Zone changes"
lists the last 100 zone moves and CB decisions with their reasons. "Why no
CB here?" then a click on the map lists the reasons there is no CB at that
point (no storm on the radar, behind, in view, too close to another CB,
the planner's score, and so on).

**Keeping the zones:** frankenweather watches every weather variable it
sets (the zones' positions, weather and METARs, `WxBasic`, `WxAutoSet`).
If something else changes one, it writes its own value back about 2
seconds later and logs it: a situation load (expected, it brings the
situation's own zones) as one line, any other client as a warning with
the value it wrote. If the same variable has to be restored more than 5
times within 2 minutes, someone keeps changing it: frankenweather logs
an error and leaves that variable alone until the changes stop.

### CB growth and decay

**CB life cycle switches (default off).** Two switches decide whether CBs
grow and shrink at all. They're on the web UI's CB test page (box "CB
features"), on the command line (`--cb-height-cycle`, `--cb-oktas-cycle`)
and in the config file (section `cb_life_cycle`, keys `height` and
`oktas`):

- **CB height life cycle:** on, a CB starts a few thousand feet tall,
  grows to its full height and shrinks back before it's removed; off (the
  default), it's created at its full height and keeps it until it's
  removed.
- **CB oktas life cycle:** on, a CB starts at 1 okta, steps up to its full
  oktas and back down to 1 before it's removed; off (the default), it's
  created at its full oktas and keeps them until it's removed.

With both off, a CB appears and goes at its full size. That's what drawing
the CBs in MSFS needs for now; the growing and shrinking described below
only happens with a switch on.

With a switch on, a CB grows at fixed rates (oktas half an okta a
minute, its top 2,500 ft a minute, from 1 okta and 5,000 ft deep) and
shrinks more slowly (a quarter okta and 1,000 ft a minute). "Remove CB
near dep/arr" and CB-free zones remove CBs at once, and a zone that's
moved starts fresh. The old `--cb-growth` option (a distance-based
growth) was retired on 2026-11-19: it's still accepted but does nothing.

Inside a TS or TC SIGMET area (with the SIGMET system), zones are kept at least
70 nm apart, and the all-around placement at startup, after a
reposition and while maneuvering keeps them at least 60 nm from the
aircraft. PSX draws a CB's echo by the distance to the nearest other
zone, so packed zones looked like small, harmless cells; spread out,
the CBs are bigger and cover more of the area ahead.

New zones are never placed within 100 nm of the aircraft, but a zone the
aircraft flies towards can still get a CB there: a new or moved TS
SIGMET, CAPE, or a METAR. Such a CB is small, at most 2 oktas and 15,000
ft, until the zone is moved (usually once it's behind the aircraft), so
no 45,000 ft CB appears out of nowhere 10 nm ahead. Inside a TS or TC
SIGMET area, where CBs are expected everywhere, this only applies within
40 nm. A CB the zone already
had when it came within 100 nm stays, but doesn't grow. In the first 2
minutes after frankenweather starts no zone is limited (PSX may already
be showing its CB, and the SIGMET, METAR and CAPE CBs arrive over the
first updates). The departure and destination airport zones keep
their METAR's CB.

Both the forecast target and the live ramp rate are visible, not just the
current (ramped) value: the web UI's weather map popup and the
Weather zones page's zone cards show the CB's current oktas/top next to
its target (only when they differ) and its current growth/decay rate —
this applies to the frankenrouter web UI too, since it serves the same
pages. The console's per-zone log line gains a `[CB ramp: ...]` suffix
with the signed oktas/min and ft/min rate and the target whenever that
zone's CB is actively changing.

### CBs drifting with the wind (advanced zones)

Advanced zones are on by default (`--no-advanced-zones` turns them off; the
CB test page switches them at runtime). CBs move with the wind, as real thunderstorm cells do, so a
pilot deviating around one has to think about where it's going: upwind
is usually the better side.

- **Which CBs drift:** a zone that gets a CB while the aircraft is at or
  above 10,000 ft (not in maneuvering mode) starts drifting, if it's a
  grid zone or an airport zone other than the departure or destination,
  and more than 50 nm from both. The anchor zone and the dep/dst airport
  zones never drift.
- **How it moves:** steadily, with the 850-300 hPa mean wind (about
  5,000-30,000 ft, from Open-Meteo at the cell's position when the drift
  starts), turned 5 degrees to the right, at its speed. Below 10,000 ft,
  or while maneuvering, the cells stand still.
- **Where the CB is:** PSX always draws a zone's CB 7 nm from the zone
  centre, on a bearing that goes round clockwise once an hour (see
  [PSX issues](developers/psx-issues.md#a-zones-cb-circles-its-centre-once-an-hour)).
  The zone elevation adds to that bearing (1 ft = 1 degree), so every 15
  seconds frankenweather sets a drifting zone's elevation (`WxMode<n>`)
  0-359 ft above its own, to keep the bearing the CB had when the drift
  started (6 ft lower each minute, 360 ft higher once an hour), and puts
  the zone centre 7 nm from the CB, opposite it. The CB moves only with
  the wind (or stands still), and its zone centre with it. The raised
  elevation lifts the zone's clouds and CB by the same 0-359 ft and warms
  the air aloft a little (+500 ft: OAT +0.6 C, PFD altitude unchanged, on
  the test sim); the dep/dst airport zones never drift. Not more often
  than every 15 s: with zone positions written every 5 s, PSX stopped
  redrawing the radar picture. The radar echo, the CB turbulence and the
  weather map follow the CB. On the map an orange arrow from each drifting
  CB shows where it's going: as long as the distance it drifts in an hour
  (12 nm at least), labelled with its heading and speed ("041°/10 kt");
  grey and dashed while it's decaying.
- **What it carries:** the weather the zone had when the drift started,
  never refreshed on the way (no new Open-Meteo data, no snapping to a
  nearby airport, the METAR left as it was, so PSX doesn't rebuild it).
- **How long:** 30-60 minutes (random), then the CB fades out over 5
  minutes. The zone stays where it ended, without a CB, until normal
  placement moves it (usually once it's behind the aircraft). A drift also
  ends when something else moves the zone, and the CB fades early instead
  of entering a "Remove CB near dep/arr" circle.
- **Other zones in the way:** if a drifting zone's centre comes within
  10 nm of the anchor or a dep/dst airport zone, that zone steps aside to
  12 nm from it, and the cell takes its weather (keeping its CB), so the
  local QNH and wind are the same whichever of the two PSX treats as
  nearest. (PSX draws both CBs smaller on the radar while the zones are
  this close: about half size at 12 nm, measured 2026-11-15.) An airport zone goes at
  most 15 nm from its field (otherwise the cell fades instead) and goes
  back as soon as no drifting zone is within 12 nm of the field.
- **Where to see it:** the log has a `CB drift:` line for every start,
  step-aside and end, and the web UI's zone reason shows e.g. "drifting
  095°/40kt, 23min left". The `/efb` page's "CBs move" shows how CBs
  drift at the aircraft (the 850-300 hPa mean wind there, worked out every
  2 minutes), which is usually not the wind at the aircraft's level.
- **PSX.NET.Orchestration (CBs in MSFS):** it moves a CB at once when a
  zone changes, while PSX's weather radar takes several seconds, and
  between frankenweather's 15 s writes it follows PSX's turning CB (three
  small steps one way, one back). So frankenweather tells the routers a
  delay (`--orchestration-delay`, default 10 s), and they give
  Orchestration every weather-zone change that late; and frankenweather
  describes each drifting CB to them (position, drift, every 5 s), from
  which each router works out the zone's position for Orchestration,
  `--orchestration-rate` times a second (1-20, default 5), instead of the
  15 s writes. Needs a router with `[filtering] orchestration_names`
  (on by default); an older router passes the writes on as before.
  frankenweather's CB turbulence sees the zones with the same delay, so a
  CB that appears close by (a test CB, say) starts shaking the aircraft
  when MSFS shows it, not 10 s before.
- **CB messages for other simulators (`FRANKENWEATHER:CBS`):** once a
  minute, and soon after any change, frankenweather sends a message
  listing every CB it has in PSX. Each CB comes with its position now, base
  and tops (including a top above PSX's 44,999 ft limit), drift, anvil
  (direction and length) and surface wind, so an add-on such as
  PSX.NET.Orchestration can draw it in MSFS. The routers pass it only to
  the other routers and to the `[filtering] orchestration_names` clients.
  The format is in [frankenweather-cb-messages.md](developers/frankenweather-cb-messages.md).
- **CB test page** (`/weather/cbtest`, in frankenweather's own web UI and
  every router's):
  switches advanced zones on and off (they start on, or off with
  `--no-advanced-zones`) and sets the Orchestration delay and rate; and puts test CBs where you ask, for checking how
  they look on the radar and in MSFS. Each takes a weather zone and gets a
  fake 8/8 CB (3,000-40,000 ft) that stays until removed, fixed to the
  ground (or drifting) as the aircraft flies on. Buttons: remove the test
  CBs; one dead ahead at 10, 20, 40, 80 or 160 nm; one 40 nm ahead and
  20 nm to the side of the track the wind comes from, drifting with the
  850-300 hPa mean wind (at least 20 kt; with the wind along the track it
  hardly closes on it); one each at 315 and 045 degrees relative, 30 nm;
  one at a distance (1-500 nm) and bearing you enter, relative to the nose
  or true (for finding how far out MSFS shows a CB);
  a CB smiley (two eyes and a three-CB smile, 40 nm ahead, upright on the
  ND); a drift speed for all test CBs (static ones then move with the
  mean wind's direction) and a reset to the motion they were placed with.
  Every other zone keeps 30 nm from a test CB's zone, so its echo shows at
  full size (except between the smiley's own CBs).

### SIGMETs

frankenweather's SIGMET system is on by default (`--no-sigmet` turns it
off). Without it, frankenweather doesn't use SIGMETs at all: it
only has PSX download them regularly, and PSX handles them itself. The
router's /efb SIGMET presets are then Off (turns off PSX's "SIGMETs
embedded in the planet weather model", so no SIGMETs are used at all)
and PSX.

With the SIGMET system on:

- frankenweather downloads SIGMETs from aviationweather.gov itself
  (international and US) every 10 minutes, or right away with the
  SIGMETs page's "Download now" button, and PSX's own SIGMET download is
  turned off. SIGMETs frankenweather can't parse (no usable area,
  unknown type) are left out and saved, one file each, to the
  `sigmet-unparsed` folder in the log directory.
- The web UI's SIGMETs page (and its button on the other weather pages)
  appears. On it you choose which SIGMET types frankenweather handles
  itself: thunderstorms (TS), tropical cyclones (TC), turbulence (TURB)
  and mountain wave (MTW), all four by default. The choice can be saved
  in the configuration file (`[sigmet]` `handle`) from the Config page.
- Three presets, on the SIGMETs page and the router's /efb page:
  - **Off**: no SIGMETs at all. Neither frankenweather nor PSX uses
    any: frankenweather's SIGMET system is switched off, and so is PSX's
    "SIGMETs embedded in the planet weather model".
  - **Normal**: frankenweather's SIGMET system handles TS, TC, TURB and
    MTW, PSX gets the rest (VA and ICE). This is the default. With
    Advanced Icing on (see below), frankenweather keeps the ICE ones too.
  - **PSX**: frankenweather's SIGMET system is switched off, as with
    `--no-sigmet`: PSX handles every SIGMET itself, and frankenweather only
    asks it to download fresh ones regularly.

  With Normal, a different choice of types on the SIGMETs page shows as
  a custom choice (no preset highlighted). Off and PSX keep that choice
  for when the system is switched back on, and the setting is saved in
  the configuration file (`[sigmet]` `own`).
- PSX gets every SIGMET frankenweather could parse of the types it
  doesn't handle, and always the volcanic ash (VA) ones, and the icing
  (ICE) ones unless Advanced Icing is on.
  If anything else changes PSX's SIGMET data (a situ load, another
  addon), frankenweather writes its own back.
- TS and TC (when handled) become CBs in frankenweather's weather
  zones, so CB turbulence and MSFS (through PSX.NET.Orchestration) see
  them too. TS/TC areas within 300 nm that aren't behind the aircraft
  get 2 zones each (a TC: a cluster of 3 around the cyclone's centre),
  in the part nearest the aircraft but at least 100 nm away. CB coverage
  comes from the SIGMET's wording: ISOL 2/8, OCNL 4/8, EMBD 5/8, FRQ 6/8,
  SQL 7/8, TC 6-8/8. The top comes from the SIGMET, the base from the
  zone's own surface temperature and dew point. CAPE can only make a CB
  bigger.
- The track ahead is never kept free of CBs: inside a TS or TC area
  the aircraft flies into the SIGMET's CBs, which were on the radar
  long before. (The "anchor" zone that keeps the local weather nearby
  is behind the aircraft, see Zone placement and relocation.)
- TURB and MTW (when handled) become frankenweather's own turbulence, a
  "SIGMET" source with its own setting on the turbulence page. It
  applies inside the SIGMET's area and between its base and top, fading
  in over the first 10 nm inside the area and 1,000 ft inside its base
  and top. A SIGMET that says MOD gets two thirds of the intensity. An
  MTW SIGMET strengthens the terrain model's own mountain wave or rotor
  to at least that intensity, keeping its pattern of up- and
  downdrafts; where the terrain model finds none, it gives mountain wave
  turbulence of its own.
- The intensity varies over time between 0% and 75%. It ramps smoothly
  to a new random level, usually over half a minute to three minutes
  but sometimes within seconds, stays there for a random time, and then
  picks the next level. It is mostly light or smooth: on average below
  5% (smooth air) about 14 minutes per hour, 10-25% about 23 minutes per
  hour, 45% or more about 11 minutes per hour, 70% or more less than a
  minute per hour.
- Like real turbulence it comes in layers. Every 2,000 ft layer
  (FL255-275, FL275-295 and so on) has its own, independent level,
  blended over 300 ft either side of a boundary, so every flight level
  is in one layer and a 2,000 ft climb or descent always gives a
  different ride. MTW uses 6,000 ft layers (FL235-295, FL295-355 and so
  on, blended over 500 ft), since a mountain wave is much deeper. Each
  layer also has its own lasting roughness, from smooth to rough, that
  stays for 30-90 minutes and then slowly changes: a smooth layer is at
  45% or more only about a minute per hour, a rough one about 25
  minutes. So a good level found by climbing or descending tends to
  stay good for a while.
- The console and the router's /efb page show the current layer and
  where its level is heading ("Intensity trend").

frankenweather's own web port (not the router's) also serves the SIGMETs
for download, as text in PSX's own SIGMET layout:

- `/api/sigmet/latest/raw`: every SIGMET of the last download, including
  expired, unknown and unparseable ones
- `/api/sigmet/latest/sanitized`: only the ones frankenweather could use
- `/api/sigmet/latest/topsx`: exactly what frankenweather last gave PSX
- `/api/sigmet/latest/json`: the whole download parsed, as JSON: when
  it was put together (`generated_at`), the download time, the next download, the last error, the current
  settings, counts per type, and every SIGMET with its area, levels,
  validity and raw text, whether it is usable (and why not), whether it
  has expired, and who uses it (frankenweather, PSX or nobody)

For shared cockpit, frankenweather also sends the JSON to the PSX
network (`addon=FRANKENWEATHER:SIGMETJSON:`, lzma-compressed and
base64-encoded, about 30 KB) after every download and setting change and
every 5 minutes. The routers pass it on only to other frankenrouters,
and every frankenrouter serves the latest one on its own web port as
`/api/sigmet/latest/json`, the same as frankenweather's but up to 5
minutes old (its `generated_at` says when it was put together).

The SIGMETs page also shows the download status, the number of SIGMETs
of each type that could and couldn't be parsed, the nearest area of each
type and every other one within 320 nm (with the weather zones inside
TS/TC areas), the SIGMETs that couldn't be parsed, and the raw text and
the text given to PSX. The weather map shows every valid SIGMET area of
every type (zoom out to see the ones further away), one colour per type,
with a legend for the types in view, and marks each zone's CB at its
actual position. Areas frankenweather doesn't use with the current
settings (PSX gets them, or nobody does with the Off preset) are drawn
with a dashed outline; hovering over an area says which. With
`--no-sigmet`, or with the Off or PSX preset, the map shows the SIGMETs PSX
downloaded, all dashed.

### Turbulence versions: default and next

The turbulence has a version number. The default is **version 2**
(until 2026-11-10 the "testing" profile; version 1 was "stable", which
is gone). A "next" version, when there is one, turns on changes being
tried out before they become the default, so they can be compared in the
sim (also with seat shakers) without changing anyone's normal turbulence.
Choose it on the router's /efb page (Turbulence Version: Default/Next;
Next is greyed out while there is no next version, as now), in the
configuration file (`[turbulence]` `profile`), with `profile` on the
turbulence API, or with `--turb-profile default|next` on the command
line. `--turb-profile` overrides the configuration file at startup and
whenever the settings are reloaded or reset; switching on the /efb page
still works for the session, and the file only changes when you save
the settings. The old names `stable` and `testing` mean default. The log
shows the version in effect ("turbulence v2"). What version 2 changed
from version 1:

- **Realistic turbulence: light chop, with moderate, severe and extreme
  as rare events** (see [Turbulence events](#turbulence-events-version-2)
  below). The continuous turbulence is at most light chop (34%), except
  inside a CB's cloud, where it's severe.
  Moderate patches, severe clusters and extreme encounters happen at
  random, at about real-world odds for the kind of area the aircraft is
  in, and crews can make each kind more likely.
- **Boundary-layer turbulence**: the light bumps of an ordinary breezy
  or sunny day, which the stable profile doesn't have (its mechanical
  turbulence starts at a 20 kt wind and stays near nothing over flat
  land). From Open-Meteo's 10 m wind (up to 15%) and the sunshine (up
  to 20%), within the boundary layer (Open-Meteo's boundary layer
  height: by day often 3,000-5,000 ft, at night 1,000 ft or less), fading
  out above it. At most 20%, light bumps: PSX's own gusts (from the
  METAR) already shake the aircraft on a windy day.
  The console's reason says "Boundary layer: ... (testing)".
- **Turbulence inside TS/TC SIGMET areas** (with the SIGMET system, TS/TC
  handled): the zones' CBs only cover part of a big thunderstorm area,
  so flying through one was mostly smooth. Now, everywhere inside the
  area below its top, faded in near its edges, a minimum plus a level
  that varies like a TURB SIGMET's, both by the SIGMET's wording:
  ISOL 3% + 15% of it, OCNL 6% + 25%, FRQ/EMBD 10% + 35% (10-36%), SQL
  and TC 12% + 45%: the air between the cells, mostly light and never
  smooth. The cells themselves are the zones' CBs, whose own turbulence still applies
  where it's stronger. It uses the SIGMET turbulence settings; the reason
  says "... SIGMET ... (convective, testing)".
- **Mountain wave that doesn't drive the autopilot into oscillation**
  (the terrain model's wave and MTW SIGMETs). In the stable profile the
  wave's bursts push one way for long stretches: up or down with the
  wave, faster or slower with its gust. On a descent in FLCH (speed held
  with pitch) through a SEV MTW SIGMET that swung the descent rate
  between a few hundred and over 4,000 ft/min (EGPF, 2026-10-25). Testing:
  - Up/down bursts follow how fast the wave is changing, not which way
    it goes, so they don't add up to a long push.
  - No gust, bank or yaw bursts in a wave, and only a quarter of the
    speed bursts. Gust bursts weather-vane the aircraft, a yaw that real
    pilots don't feel in mountain wave; the speed bursts give the
    airspeed fluctuation they do (about ±12 kt at 45%). A light wave
    (under 25%), whose bursts were only speed and gust ones, gets up/down
    bursts for the rest, so it isn't glassy smooth.
  - Up/down bursts one way within 8 seconds add up to at most the
    largest single one (for all continuous turbulence, see below).
  - An MTW SIGMET lifts the terrain model's own wave to at most twice it
    plus 20%, instead of up to the SIGMET's full severity.

  It's still rough. FLCH holds the speed with pitch, so it chases the
  speed bursts: in a 45% wave, a descent from FL300 to FL150 swung by
  over 2,000 ft/min 14 times (without speed bursts: none). In V/S the
  autothrottle takes the speed and the descent stays steady, as real
  crews fly it. In severe wave, fly by hand.

### Turbulence events (version 2)

Real turbulence is mostly smooth or light. Moderate comes in patches a
minute or two long, and severe is something an aircraft hits, not
something it flies in: a 5-30 second cluster of jolts. At cruise about
0.4% of the time is moderate or worse and 0.1% or less severe, and in
the US there is about one severe pilot report per 3,500 airline hours
(see [turbulence-research](developers/turbulence-research/README.md)). The stable
profile instead holds one level per source for minutes, which in a SEV
TURB SIGMET gave severe-class shaking (peaks over 2 g) for minutes at a
time.

In turbulence version 2:

- **The continuous turbulence is at most light chop** (34%, about
  0.09 g at cruise). A TURB SIGMET is mostly light chop (22-34%, about
  0.06-0.09 g), smooth about a fifth of the time; the G-AIRMET, PIREP
  and TS-area levels vary between smooth and light; physically computed
  turbulence (terrain, CB, CAPE, boundary layer, wind shear) is capped
  at light.
- **Inside a CB's cloud it's severe all the time** (55-70%,
  scaled by the intensity sliders), with the CB core's severe events on
  top and, like events, without the up/down burst limit. Capped at light
  chop, a CB core was calmer than its edge during a moderate patch.
  Expect to hand-fly in a CB core: the autopilot may not cope.
- **No pitch oscillation from the continuous turbulence**: its up/down
  bursts one way within 8 seconds add up to at most the largest single
  one, whatever the kind of turbulence. PSX's pitch rings for 3.5-4
  seconds after every up/down burst, and a run of small ones a second
  or two apart can pump that up: in mountain wave to over 1.5 g (NZ MTW
  SIGMET, FL250), and in light wind-shear turbulence to ±2 g with pitch
  swinging from -6 to +14 degrees (SCIZ SEV TURB SIGMET, FL330). Events
  are not limited.
- **Moderate patches**: the turbulence level goes up to 38-50% for 20
  seconds to 10 minutes (typically a minute and a half). Like real
  clear-air turbulence, a patch starts with light chop and builds up over
  10-20 seconds (too short to slow down first), and dies away the same
  way.
- **Severe clusters**: 5-30 seconds of moderate chop with one to four
  big up/down jolts of 0.5-1.2 g, at least 6 seconds apart. In PSX one
  big jolt sets off a few more swings by itself, as in real recordings.
- **Extreme encounters**: 12-25 seconds of moderate chop, then an
  updraft and a sudden drop to below zero g (about -0.4 g, one in four
  about -1.2 g, like the SQ321 and UA826 accidents), then a rebound.

None of these is softened for the autopilot. If an event makes the
aircraft hard to control, that's realistic. Test flights at FL350 on
autopilot (ALT HOLD): severe 0.6-1.5 g and about 100 ft; extreme -1.3
to -2.4 g and up to 150 ft.

How often each happens depends on the kind of area the aircraft is in
(events per hour of flight):

| Area | Moderate | Severe | Extreme |
|---|---|---|---|
| Clean air (no turbulence source) | 0.1 | 1 in 2,000 | 1 in 500,000 |
| G-AIRMET, MOD SIGMET or PIREP | 4 | 1 in 100 | 1 in 50,000 |
| SEV TURB SIGMET, SEV PIREP | 5 | 0.5 | 1 in 1,000 |
| MTW SIGMET | 3 | 0.2 | 1 in 2,000 |
| Near a CB, TS/TC SIGMET area | 7 | 0.75 | 1 in 500 |
| In a CB (inside its cloud) | 20 | 8 | 1 in 20 |
| Strong low-level wind, rotor | 7 | 0.1 | 1 in 10,000 |

So a 30-minute transit through a SEV TURB SIGMET is mostly light chop
with a few moderate patches, and has about a one in five chance of a
severe cluster. The area is whichever source present (switched on, at
least 5%) has the highest severe rate; switched-off turbulence types
don't count. Only while flying at 30 kt or more, and turbulence is on.

**Likelihood** (Turbulence page, "Turbulence events"; configuration
file `[turbulence]` `event_mult`; API `event_mult`): a multiplier per
kind (moderate up to 10, severe up to 100, extreme up to 1,000), which
makes it more likely but never stronger. 0 turns a kind off; 1 (the
default) is real-world odds. Presets: Realistic (1/1/1), Eventful
(3/20/50) and Training (5/100/500). At real-world odds severe is rare in
a few hours of flying; Eventful makes it likely on a flight through a
SEV TURB SIGMET.

**Trigger** (Turbulence page; API `trigger`:
`moderate`, `severe` or `extreme`) starts an event right away, for
testing and training.

Every event is logged: its start (kind, area and what made it, position
and altitude, length, planned peak, and the likelihood if not 1), each
big jolt, and its end. The Turbulence page shows the last 20.

### The log: checking a pilot's report

Every run writes a log file (Config and logs page), and the normal log
(without `--debug`) is meant to be enough to check a pilot's report of
too much, too little or just right weather or turbulence afterwards:

- Once a minute a `Flight:` line: position, altitude, heading, TAS,
  pitch and bank, on the ground, UTC and PSX time, the wind at the
  aircraft, in MSFS cloud, the turbulence level and what makes it, the
  turbulence profile and (testing) the area, any event and the
  likelihoods. Also when it's smooth, so "it was smooth here" can be
  checked too.
- Once a minute (when there were bursts) the burst summary: how many of
  each kind, direction and size, and from which source.
- Once a minute (when there were any) the other bursts: turbulence
  bursts frankenweather didn't send, normally PSX's own (it makes its own
  in and near CBs, in gusts, wind shear and CAT) and its lightning
  flashes. They add to frankenweather's, so the turbulence felt is the
  two together.
- Every 10 seconds while there is turbulence, the level and its sources.
- Every turbulence event, every change of a turbulence setting (with its
  value) and every zone update, SIGMET download and reposition.

### Fewer up/down bumps high up

Above FL200 frankenweather sends fewer up/down (SINK) bursts of random
turbulence, down to a quarter from FL350; the bursts themselves stay the
same size, and the bank, yaw and speed bumps all stay. PSX's up/down burst
costs the aircraft energy whichever way it pushes (the autopilot pays for
getting the altitude back with speed), while real gusts average out: real
moderate turbulence makes the speed fluctuate, it doesn't drain it. With
all of them, 50% clear-air turbulence at FL370 took a 747 from 278 kt to
the stall in a few minutes at full thrust; with a quarter it holds its
speed. Mountain waves keep all their up/down bursts: their downdrafts
really cost energy, and there the answer is to descend or turn away.

### PSX autothrottle workaround (`--no-psx-at-workaround`)

PSX 10.191 and later have an autothrottle bug: after a weak gust or a weak
"speed down" turbulence burst, the A/T in SPD mode leaves the thrust levers
where they are for 8 to 12 seconds, however far the speed then falls below
the MCP speed (docs/developers/psx-issues.md). Stronger bursts and the up/down, bank
and yaw bursts don't do it. So in flight frankenweather sends its gust
bursts as speed bursts, and replaces weak "speed down" bursts by fewer
stronger ones (the same speed kicks per minute, so the turbulence feels the
same). `--no-psx-at-workaround` sends them unchanged. This goes once
Aerowinx fixes the bug. PSX's own gust bursts from a gusty surface wind
can't be changed: expect the A/T to lag on gusty approaches.

### Turbulence gustiness and PIREPs

The turbulence frankenweather works out itself from physics (the
terrain model's mountain wave, rotor and mechanical turbulence, wind
shear and CAPE) gets the same kind of layers, as a
"gustiness" multiplier that averages about 1: it varies over time with
smooth ramps, every altitude layer has its own, and each layer has a
lasting roughness, so some levels are smoother than others for a while.
The physics still decides where turbulence is and how strong it is on
average:

| Kind | Layers | Multiplier |
|---|---|---|
| Wind shear (clear-air turbulence) | 2,000 ft | 0.5-1.2 |
| Mountain wave | 6,000 ft | 0.7-1.3 |
| Rotor and mechanical | 2,000 ft | 0.5-1.2 |
| CAPE | 2,000 ft | 0.5-1.5 |

Wind shear clear-air turbulence starts at a vertical wind shear of 5
kt/1000 ft and is at its strongest from 12 kt/1000 ft above FL200 (8 and
14 below FL100, blended in between): shear from forecast data is
smoothed, and with the higher figures a strong jet stream hardly ever
gave any turbulence at cruise. It is computed over the sea too.

Wind shear near the ground (on a windy day the wind normally increases
steeply in the lowest few thousand feet) is not clear-air turbulence:
below 3,000 ft above the ground it gives at most 30%, rising to no limit
at 5,000 ft, and its gustiness varies it below that (about 15-30%).
The turbulence reason then says "Low-level wind shear".
The limit also holds while it fades into another kind of turbulence,
e.g. mechanical turbulence where the terrain gets rougher.

Terrain and wind shear turbulence changes over about 30 seconds where the
wind data changes (each 1° square and forecast hour). After a
reposition or a situation load it starts afresh at the new position
instead of fading out the old position's turbulence.

On the ground (takeoff and landing roll, from 30 kt) turbulence is at
most 30% and only yaws and rolls the aircraft: PSX makes a yaw jolt grow
with airspeed, from about 0.2° of heading at 80 kt to about 0.6° near
rotation, which has to be steered out.

G-AIRMET turbulence areas (US) vary like a TURB SIGMET instead: the
same varying level in 2,000 ft layers, scaled by the area's severity (a
MOD area gets two thirds of it, like a MOD SIGMET), so a moderate
G-AIRMET is mostly light with moderate spells. The weather map shows the
G-AIRMET turbulence areas within 640 nm too (in the legend while in view).

CB turbulence and real PIREPs are not changed. The console shows the
multiplier in the turbulence reason, e.g. `(gust x1.24)`.

The router's /efb page has a PIREP button, a bit like asking ATC for
ride reports. It shows the reported ride at each whole flight level up
to 4,000 ft above and below the current one (at most FL470), from every
source of turbulence and each layer's lasting roughness, not the
moment's turbulence. The rating says how often a level is bumpy: the
share of time it is at moderate turbulence (25%) or worse is under 10%
for smooth, under 20% for light, under 35% for moderate, and more for
rough. So even a smooth level can have the odd bump. The reports are
refreshed every minute, when the flight level changes and when entering
or leaving a turbulence area. The popup closes by itself after a few
seconds.

In turbulence version 2 the continuous turbulence is at most light chop
and moderate or worse comes as events, so the rating follows that: a
level is smooth when it's at light chop (22%) or more less than a
quarter of the time, otherwise light; moderate near a CB and rough
inside one, where the events are most frequent. A SEV TURB SIGMET's
levels are light chop most of the time, so they read light. The log's
turbulence labels use the same calibration: up to 34% is light.

### Minimum safe altitude for frankenpilot

Near the FMC departure (60 nm) and destination (120 nm, before top of
descent), frankenweather works out the airport's minimum safe altitude from
its terrain data: the highest terrain within 25 nm plus 1,000 ft, rounded
up to 100 ft. It announces it once a minute, and at once after a change of
the FMC destination (a diversion), as
`addon=FRANKENWEATHER:MSA:<id>:{"role":"departure","icao":"YSSY","msa_ft":2500,"radius_nm":25}`.
frankenpilot uses it for the standby altimeter (STD only above the
departure MSA, the arrival QNH below the destination MSA). Each airport is
worked out once (a few seconds, in the background); in cruise nothing is
done.

### Advanced Icing (`--advanced-icing`, testing)

Makes in-flight icing realistic. Off by default; `--advanced-icing`
switches it on at startup, and the web UI's Cold weather page and the
router's /efb page (Advanced Icing, under SIGMETs) have an on/off toggle
with a line saying what it's doing right now. The setting is saved with
the other settings in the config file (`--advanced-icing` wins over it).

**What PSX does by itself** (measured on the test sim, PSX 10.180 and
10.195, 2026-11-13/14):

- It ices at one rate, about 11 kg per wing and 3 kg per nacelle per
  minute, whenever its weather has any cloud layer at the aircraft (even
  1/8, and the CB layer), fog or precipitation, and TAT is below about
  +9 C. The rate doesn't change with coverage, layer, precipitation or
  temperature, down to about -35 C; PSX stops icing between -35 and -43 C.
- From the 747's size and speed and typical cloud water that rate is
  about **moderate** icing (an estimate). Real icing is mostly trace or
  light; moderate mainly at about -3 to -12 C in thick cloud; severe in
  freezing rain or drizzle and convective cloud; and none when the
  aircraft's speed warms the leading edge above freezing. So in most
  cloud PSX ices too much, and at higher speeds near 0 C it ices when
  reality doesn't.
- Inside an ICE SIGMET PSX ices at the same rate, cloud or no cloud, MOD
  or SEV alike, and lowers the outside air temperature by 5-8 C (about
  3 C a minute), which fights frankenweather's temperatures.

**What frankenweather does with Advanced Icing on** (in flight only; on
the ground PSX's icing, the de-icing and the holdover stay as they are):

- Every 10 seconds it looks at PSX's ice (Icing: kg on each wing and
  nacelle) and works out how much of PSX's growth since the last look is
  realistic, as a factor on PSX's rate. Where PSX added ice, it writes
  back a lower value (factor below 1) or a higher one (factor above 1).
  PSX applies the write at once and carries on from there; its anti-ice,
  ice detectors (>ICING NAC) and the AUTO anti-ice keep working as
  before, because PSX is still icing.
- The factor, from the weather at the aircraft:
  - **Kinetic heating**: the leading edge is taken to be SAT plus 40 % of
    the rise from SAT to TAT (estimate, from NACA's finding of heavy glaze
    still at -7 C at M0.5 and -21 C at M0.9). Above +1 C: no ice; full ice
    from -1 C. 40 % matches the physics: a wet surface in cloud gets about
    half the dry rise, because the water on it evaporates (Hardy, NACA ARR
    5G13). Behind the leading edge, though, the air speeds up over the wing
    and cools, so in cloud light ice still builds when the leading edge
    is above freezing. That ice is at most light (0.3 of PSX's rate) at
    SAT -3 C, falling to none at SAT 0 C (FAA AC 91-51A). At 250 kt that
    gives light ice at SAT -3 to -2 C and a trace at -1 C, where PSX ices
    to about SAT -2 C. Clear air gets none of it.
  - **Which cloud**: the layer in PSX's weather at the aircraft. Layer
    cloud starts at 0.6 of PSX's rate, convective cloud at 2 (inside the
    CB cell, where frankenweather's CB turbulence core is: PSX's CB layer
    is one cell, and PSX doesn't ice in it away from the cell), freezing
    fog at 0.3.
  - **Temperature** (after the US icing forecast's temperature map):
    most water at -4 to -8 C, half at 0 C, falling to 0.15 at -25 C and
    below (trace), none below -40 C.
  - **Cloud-top temperature** for layer cloud (estimated from the layer's
    top and 2 C per 1,000 ft): tops colder than -12 C have more ice
    crystals, down to 0.6.
  - **Coverage** for layer cloud: 1-2 oktas 0.4, 3-4 oktas 0.7 (the
    aircraft is in cloud only part of the time).
  - Precipitation in cloud warmer than -10 C: x1.3.
  - Freezing rain or drizzle (FZRA/FZDZ) in a METAR within 30 nm, below
    12,000 ft: severe (2.5).
- Reports beat the model:
  - **Icing PIREPs** (aviationweather.gov, US and some others) at the
    aircraft's level (2,000 ft beyond the reported layer), up to 90
    minutes old: within 20 nm the PIREP's intensity sets the factor
    (trace 0.1, light 0.3, light-moderate 0.6, moderate 1, moderate-severe
    1.7, severe 2.5; NEG = none), fading back to the model by 40 nm.
  - **ICE SIGMETs**: while Advanced Icing is on (with frankenweather's own
    SIGMETs, the Normal preset), PSX doesn't get them, so no clear-air
    icing and no cooled air. Inside one (1,000 ft beyond its levels) in
    cloud the factor is at least severe (2.5; moderate if the SIGMET says
    MOD).
  - **US G-AIRMET ICE** areas (forecasts of moderate icing; base "FZL" =
    the lowest freezing level in the area): in cloud, at least 0.6.
  - A forecast area isn't iced everywhere, so SIGMETs and G-AIRMETs only
    raise the factor in cloud. Where PSX's weather has no cloud but a
    moderate-or-worse PIREP within 40 nm or a SEV ICE SIGMET says it
    ices, frankenweather adds ice itself at half that strength (patchy
    cloud PSX's zone doesn't have). PSX's detectors don't see that ice
    (no >ICING NAC from it), but its anti-ice melts it. Only with TAT at
    or below +3 C: out of cloud PSX melts any ice within seconds above
    some TAT between +1 and +6 C (though in cloud it ices up to TAT +9 C).
- The log says once a minute what it did, e.g. `Advanced Icing: light
  (0.35 x PSX's rate): layer cloud 8/8, SAT -12 C, top -16 C; last minute
  left wing PSX +11 kg, kept +4 kg`.

**Fuel temperature** (fuel cooling Realistic, on the Cold weather page or
`--fuel-cooling realistic`; PSX default for now; in flight and on the
ground, with or without Advanced Icing):

- PSX's own fuel temperature doesn't behave like a tank. On the test
  sim (2026-11-14), with 76 t in the wing tanks, 90 minutes at FL370
  (TAT -25 C) took it from +2.4 to -7.3 C, a straight line of about 0.6 C
  per hour that never levels off; parked at +8 C it warmed at 5.6 C per
  hour. With 31 t at FL372 it cooled from +20 C at about 34 C per hour
  and warmed from -45 C at about 11 C per hour. Real wing tanks follow the skin temperature with a
  lag of a few hours: after a long cruise the fuel is often well below
  zero (the cause of frost over the tanks after landing, even above
  freezing, and of FUEL TEMP LOW on long cold flights).
- frankenweather runs its own wing tank model and keeps PSX's fuel
  temperature on it (FuelQty's last field, hundredths of a degree C, as
  the EICAS shows it). PSX keeps a written fuel temperature and carries
  on from it. The fuel tends towards the skin temperature (in flight SAT
  plus 95 % of the rise to TAT, on the ground the outside air) with a
  time constant of 6 hours with full wing tanks, shorter as they empty
  (at least 3 hours), the same on the ground. Refuelling mixes in fuel at
  half the outside air temperature plus half the forecast's soil
  temperature at 54 cm (fuel from underground tanks and hydrant pipes is
  nearer the ground's temperature), plus a little randomness (normal,
  2 C, never more than 4 C), between -15 and +35 C; one refuelling uses
  one supply temperature, and the log shows it. Without the soil
  temperature (not fetched yet) the air's alone. Example: Stockholm in
  November, air +1 C and soil +5 C: fuel at about +3 C; Dubai, air +28 C
  and soil +29 C: about +28 C. Example: 76 t
  in the wing tanks at FL370 and TAT -25 C cool from +2 C to about -4 C
  in the first hour and -16 C after four.
- Calibration: Boeing's "Polar Route Operations" (AERO, 2001) says the
  fuel tends towards the boundary layer temperature over the wing skin,
  slightly below TAT; a change in TAT takes 15 minutes to an hour to show;
  the fuel cools about 3 C per hour, at most 12 C per hour in the most
  extreme cold; fuller tanks change more slowly, and the 747-400's long,
  narrow outboard mains (main 1 has the temperature probe, near the
  bottom, where the fuel is coldest) cool faster than a 777's. Secondary
  sources quote about 10 minutes per 1 C for the 747-400 when far off, and
  Boeing's Chicago to Hong Kong polar trials saw tank temperatures of -38
  to -41 C at an OAT of -70 C (the model's target there: about -42 C).
  With these settings full wing tanks 50 C above the target cool about
  8 C per hour, and no fuel load cools faster than about 13 C per hour.
  On the ground the time constants are the same as in flight: the FAA's
  fuel tank thermal model (the Fuel Tank Flammability Assessment Method,
  DOT/FAA/AR-05/8, user's manual for version 11, 2005,
  <https://rosap.ntl.bts.gov/view/dot/57849>) uses the same constants on
  the ground, engines off or on, as in flight (its example: 200 minutes
  near empty, 400 near full, like this model's 3 and 6 hours), and loads
  the fuel "at ambient temperature". Boeing (I. Thomas, "An Overview of
  Fuel and Ambient Temperatures", ASTM STP, 1979,
  <https://store.astm.org/stp35048s.html>, abstract) found airport storage
  temperatures reaching about 38 C at most, more typically 27-32 C at the
  most. The half-and-half blend of air and soil temperature, the
  randomness, and leaving out the sun warming a parked wing are estimates:
  no published figures were found for them.
- It starts from PSX's own value (a situation file carries one) and
  starts over when PSX's value jumps or the fuel drops suddenly (a
  situation load). It writes when PSX is 0.5 C or more off, at most once
  a minute. FuelQty also carries the tank quantities, so each write sends
  them as just read from PSX.
- The fuel temperature doesn't change PSX's fuel burn: an A/B in cruise
  with it written to +20, -30 and -45 C burned the same (within 0.3 %,
  falling smoothly with the weight). Below the FMC's minimum (-37 C by
  default) PSX shows FUEL TEMP LOW, as it should.
- The Cold weather page shows the fuel temperature, the rate and when it
  reaches FUEL TEMP LOW and the freezing point; the /efb Advanced Icing
  line shows the model's temperature and where it's heading.

**Limits of this first version**:

- Tested on the test sim (2026-11-14): with Advanced Icing off, PSX got
  a test SEV ICE SIGMET and iced in clear air (63 kg per wing in 5
  minutes) and cooled the air to 8 C below the real temperature; with it
  on, PSX got no ICE SIGMET, didn't ice and kept the real temperature,
  and frankenweather added ice at FL190 (about 9 kg per wing per minute).
  With flaps 20 and AUTO anti-ice in cloud, the wing ice built at 0.6 of
  PSX's rate while the nacelles stayed clean.
- With AUTO anti-ice (the normal setting) PSX's own detectors switch the
  nacelle anti-ice on within about 30 seconds and the wing anti-ice after
  about a minute and a half of PSX icing, and the ice stays near 0 kg either
  way. Advanced Icing then mostly matters when anti-ice is off, manual or
  inhibited (wing anti-ice is off with the leading-edge flaps out).
- PSX's detectors still react to PSX's icing even where frankenweather
  takes all of the ice away (e.g. high speed near 0 C), so the AUTO
  anti-ice may come on where real detectors would see nothing.
- The factors are estimates from the research, not measured on a real
  747; they will be tuned in testing.
- Not yet: frost from cold-soaked fuel on the ground (the fuel
  temperature is there now), ground icing rates by
  precipitation intensity, engine (fan) ice in long idle descents.

### Cold weather page

The web UI's **Cold weather** page (`/weather/cold`, also in the
router's web UI) brings the cold weather settings together. It updates
every 20 seconds.

- **Fuel temperature**: PSX's fuel temperature (main tank 1, as on the
  EICAS), how fast it's warming or cooling right now, where the
  realistic model is taking it, and when it reaches FUEL TEMP LOW (the
  FMC's minimum, -37 C by default) and the freezing point of the fuel
  type set on the Instructor Station (Situation > Service; the
  freezing points are PSX's: Jet A -40, Jet A-1 -47, TS-1 about -55, Jet B
  -60 C). With realistic fuel cooling the times follow the model; with
  PSX's own cooling they are at the rate measured over the last 10
  minutes (it needs 2 minutes without a jump first).
- **Prevent fuel freezing** (on by default; `--no-prevent-fuel-freezing` at
  startup): keeps PSX's fuel at least 2 C above the freezing point of the
  fuel type, in both fuel cooling modes (and the Chill button stops there).
  PSX starves the engines once the fuel is a little below its freezing
  point (test sim 2026-11-14, Jet A: 1 C above normal; 2 C below a large
  thrust loss, 484 kt to 294 kt and a descent from FL372; 10 C below all
  four engines flamed out; they relit by themselves within a minute of the
  fuel being warm again). Real fuel still flows to about 6 C below its
  freezing point (Boeing), so this keeps PSX nearer reality. FUEL TEMP LOW
  still comes at the FMC's minimum (-37 C), and with realistic fuel cooling
  a long cold flight still calls for a descent or more speed to keep the
  fuel above it. The log says when it holds the fuel ("kept at ...").
- **Heat +10 C / Chill -10 C**: changes PSX's fuel temperature at once,
  e.g. to try a cold fuel situation. With realistic fuel cooling the
  model carries on from the new temperature.
- **Fuel cooling**: PSX default (PSX's own fuel temperature) or
  Realistic (frankenweather's wing tank model, see Fuel temperature
  under [Advanced Icing](#advanced-icing---advanced-icing-testing)).
  PSX default for now; `--fuel-cooling` sets it at startup.
- **Advanced Icing**: on/off and what it's doing right now.
- **Log**: what Advanced Icing, the fuel temperature and the realistic
  holdover did (ice kept or added and why, Advanced Icing on/off, ICE
  SIGMETs, PIREP and G-AIRMET downloads, fuel temperature writes,
  refuelling, model restarts, the buttons, holdover changes), newest
  first, times UTC. The same lines are in frankenweather's log file.

Fuel cooling, prevent fuel freezing and Advanced Icing are saved with the
other settings in the config file (section `cold_weather`).

### Realistic de-icing holdover times (`--realistic-holdover`, testing)

PSX's anti-icing fluid is a holdover timer (AntiIceFluid, 0-45 minutes):
while it runs, no ice builds up; when it reaches 0, PSX ices again at its
usual rate. De-icing tools set it to a fixed time per fluid type
(PSX.NET.EFB 10.2.70's De-Icing page predicts Type I 14, Type II 28 and
Type IV 40 minutes, the same in snow at -3 C as in clear weather:
checked on a real sim 2026-11-14, where it also wrote AntiIceFluid=14
for Type I in the snow).

With `--realistic-holdover`, frankenweather replaces that time with the
holdover time the FAA guidelines (winter 2026-2027) give for the fluid in
the airport's current weather:

- It notices the de-icing: on the ground, the timer going up (PSX only ever
  counts it down). The value says the fluid type: `--holdover-efb-minutes`,
  default `14:I,28:II,40:IV` (PSX.NET.EFB 10.2.70); other values are left
  alone.
- The weather is the METAR of the airport the aircraft is at (within
  5 nm), from the active METAR source: snow, with its intensity from the
  visibility, day or night and temperature (FAA Table 57, so `1SM -SN` at
  night is moderate snow), snow with freezing fog, freezing drizzle, light
  freezing rain, freezing fog or mist, rain on a cold-soaked wing, snow
  mixed with rain.
- The time is the middle of the FAA range for Type I (aluminium surfaces),
  generic Type II or generic Type IV (undiluted), at most 45 minutes (PSX's
  limit, so Type IV in light snow simply gets 45). Heavy snow, moderate or
  heavy freezing rain, ice pellets and hail have no FAA holdover time:
  1 minute. With no precipitation or fog the tool's time is kept (PSX
  doesn't ice on the ground then anyway).
- After takeoff there is no protection, as in reality: PSX itself runs the
  timer down to 0 during the takeoff run, from about 70 kt (tested
  2026-11-14), and empties any value written in flight within seconds.
- If the de-icing tool writes its own time back right after
  frankenweather replaced it, frankenweather leaves it alone for that
  de-icing rather than fight it.

Each decision is logged in plain words, e.g. `Holdover: Type I fluid
applied, the de-icing tool set 14 min; ESPA METAR ...; -SN, visibility
1800 m, night, -3 C: moderate snow (FAA Table 57); FAA Type I, moderate
snow, -3 C: 6-11 min -> 8 min -> AntiIceFluid 8`. The de-icing itself (the
ice removed) is not changed. Off by default.

### Time sync

Keeps PSX's clocks (`TimeEarth`, and both cockpit clocks `TimeClockL`/
`TimeClockR`) matched to your computer's real-world time. Controlled by
three independent toggles on the Config and logs web page (or the
`[timesync]` section of the [config file](#configuration-file)):

- **`on_startup`** (default on) — sync every time frankenweather connects
  or reconnects to PSX.
- **`on_situ_load`** (default on) — sync whenever PSX finishes loading a
  situation. There's no dedicated "situ loaded" event in the PSX wire
  protocol; this is detected via PSX's own `load1`/`load3` pause/resume
  message pair, which brackets every full situ (re)load (as well as the
  very first connection's own initial load). Turn this off if you'd
  rather keep the aircraft clock a loaded situ brings with it — e.g.
  flying on VATSIM or with MSFS Live Weather, where the situ's own time
  may matter more than your PC's clock.
- **`periodic`** (default off) — every 60 seconds, resync if PSX's clock
  has drifted more than 5 seconds from your computer's clock. Useful when
  flying with time acceleration: leave this on and the clock snaps back
  in sync automatically once you return to 1x, instead of staying however
  far ahead the acceleration left it.

`on_startup` and `on_situ_load` look 5 seconds after the event (by then
PSX has sent its time) and only sync if PSX is more than 5 seconds off,
so restarting frankenweather in flight doesn't rewrite a clock that is
already right (each clock write nudges the positions of PSX's orbiting CB
cells). All three overwrite
`TimeEarth`/`TimeClockL`/`TimeClockR` with the current real-world time —
none of them account for time zone or the aircraft's own local time, only
real-world UTC-equivalent wall clock time as read from the machine running
frankenweather.

### Enroute wind importer

Simulates the real-world behaviour of a crew requesting an updated
enroute wind/temperature forecast via datalink mid-flight — except the
forecast comes from Open-Meteo instead of a real dispatch link. On by
default; toggleable from the `/weather/enroute-wind` web page (or the
`[enroute_wind]` section of the [config file](#configuration-file)).

- **Flight-plan snapshot.** As soon as PSX's wind corridor (`WxCorridorTxt`)
  changes to something FrankenWeather didn't write itself — loading a
  route, pasting an OFP wind corridor into the Instructor station, or a
  situ load — that corridor is captured as the "flight plan" snapshot.
  This capture runs whether or not the importer is enabled, so the
  comparison below is available as soon as a route is loaded. The
  snapshot is parsed regardless of which of PSX's five documented
  corridor formats it's written in (see the format table in
  `wind_corridor.py`); if the format can't be parsed, or the corridor
  is empty, the web page falls back to showing the raw text and skips
  the diff, but still shows the downloaded Open-Meteo winds on their own.
  Matching a live FMC waypoint to its snapshot entry is normally by name,
  but falls back to matching by coordinates (within 5 nm) when the names
  don't line up — this happens on NAT track points in Format D/E, where
  PSX's own corridor entry carries a lat/lon-derived name (e.g. `48N05`)
  that can differ from the FMC's own name for the same point (e.g.
  `4850N`), since PSX itself only matches those formats by coordinates,
  not name. When a coordinate match is used, the page shows both names.

- **Waypoint list.** Built from the FMC's active route: every entry
  with a real position, SID/STAR/approach fixes and lat/lon waypoints
  included (on a long SID or STAR their winds matter). Pseudo-waypoints
  without a reliable position (`(__600)` constraints, intercepts,
  `VECTORS`) are left out. The list only grows or hard-resets on an
  actual reroute; waypoints PSX itself trims from the front of the
  route once passed stay on the page (dimmed) instead of disappearing.

- **Fetching.** Once enabled, wind and temperature for the remaining
  waypoints are pulled from Open-Meteo's pressure-level forecast, once
  an hour in the background and immediately whenever the route
  genuinely changes (a reroute, so a current, non-excluded waypoint may
  now be missing wind data). Just the aircraft passing a waypoint
  neither contacts Open-Meteo nor resends the corridor to PSX — the
  wind data for every remaining waypoint is unchanged, so PSX's
  existing corridor is left in place rather than making PSX recalculate
  for no reason. The same dedup applies to the hourly refetch: if it
  happens to return identical wind data, the corridor isn't resent
  either. Six levels are fetched: the flight-plan snapshot's own 6 when
  that snapshot is a valid grid (so the diff compares like-for-like),
  otherwise a fixed default set (10 000 / 18 000 / 24 000 / 30 000 /
  34 000 / 39 000 ft).

- **Open-Meteo outages.** If the download fails, older cached data that
  still covers the current hour is used. A waypoint still without data
  gets the nearest route waypoint's winds; with no data for any
  waypoint, nothing is sent and PSX keeps its current corridor (the
  flight plan's, or the last one sent). Either way the Enroute wind
  page shows a warning, and the download is retried every 2 minutes
  until it works.

- **Writing to PSX.** The fetched data is written back to PSX as a
  **Format-D** wind corridor: each point carries its own latitude and
  longitude, and PSX doesn't look the names up in its navigation
  database. In Format A one name PSX can't find (`VECTORS`, `(__600)`,
  a runway, an approach fix like `CF21`, an FMC lat/lon point `LL01`, or
  a fix missing from an older AIRAC) made PSX reject the whole corridor:
  the flight then ran in the planet weather, and when a later corridor
  parsed, the OAT jumped (+42 °C at FL400 on WorldFlight1, 2026-11-10;
  several smaller jumps during WorldFlight). Format D takes 5 levels per
  point: points within 150 nm of the route's first or last point get the
  lowest five, the others drop the second lowest. Points closer than
  25 nm to the previous one are left out (they'd get the same Open-Meteo
  wind), and the last two passed waypoints stay at the start of the
  corridor, also after a reroute or DIRECT TO, so the aircraft is never
  ahead of the corridor's first point. Names are reduced to letters and
  digits (PSX still parses them: `LIV/07` broke a corridor). In flight,
  frankenweather compares PSX's wind and OAT with the corridor's at the
  aircraft, and logs a WARNING ("PSX doesn't seem to use our wind
  corridor") when they're more than 25 kt or 8 °C apart for 2 minutes.
  `Qs497` (the PSX variable controlling
  wind corridor use and simulated forecast inaccuracy) is set so its
  first digit is always `2` (embedded: PSX uses the corridor data), the
  second is PSX's Random slider position from the deviation chosen on the
  web page (0-80 in steps of 10, e.g. 30 = position 3, 0 = no random
  deviation; it simulates that a forecast is never 100% accurate), and
  the third (the Format menu selection, irrelevant here) is 0. To
  avoid making PSX recalculate for nothing, a rebuilt corridor that's
  byte-identical to the last one actually sent is not resent — except
  right after enabling (or re-enabling) the importer, which always
  forces a resend even if the freshly fetched data happens to match:
  otherwise PSX could be left showing the restored flight-plan corridor
  (see "Turning it off" below) indefinitely, mistaken for up to date.
  Whenever a corridor is actually resent (genuinely new wind data, or a
  reroute — never a no-op hourly poll), an
  `addon=FRANKENWEATHER:CORRIDOR_CHANGED:<instance-uuid>` message is
  broadcast on the PSX network, so other addons can react to a wind
  corridor update without polling `WxCorridorTxt` themselves.

- **Turning it off.** Disabling the importer — or enabling MSFS wind
  sync, which is mutually exclusive since both write the wind corridor
  — restores the original flight-plan snapshot to PSX instead of
  leaving the last generated corridor in place.

- **Web page** (`/weather/enroute-wind`): enable/disable toggle,
  deviation slider, last/next fetch time, and a per-waypoint,
  per-flight-level table comparing the flight plan to the latest
  Open-Meteo fetch (direction, speed, OAT, and the diff), with passed
  waypoints dimmed.

- **`--save-logs DIR`** (development use): writes one JSON and one
  human-readable `.txt` file per flight — fixed filename, continuously
  overwritten on every corridor refresh — containing the full enroute
  wind state including the diff. Reset when the FMC route is cleared,
  so the next flight starts a fresh pair of files.

### Configuration file

`--config-file PATH` points frankenweather at a TOML file holding every
setting that can also be changed at runtime from the web UI: MSFS sync
toggles, the enroute wind importer, time sync, manual weather parameters,
and turbulence tuning. It does **not** include the `--xxx` command-line
options for zone placement, CB overrides, etc. — those remain
command-line-only.

Defaults to `~/.frankenweather.toml` (`%USERPROFILE%\.frankenweather.toml`
on Windows), so it works out of the box on both Linux and Windows without
passing the option at all. Pass `--config-file PATH` to use a different
location, e.g. for multiple named configurations.

Behaviour:

- If the file exists at startup, it's loaded and its values replace the
  built-in defaults. If it doesn't exist yet (the common case on first
  run), frankenweather starts with its built-in defaults and does
  **not** create the file — nothing is written until you explicitly
  click "Save current settings to file" on the Config and logs page
  (or the file is created by hand).
- The Config and logs page also shows the file's path and whether it currently
  exists, plus a "Load settings from file" button (re-reads the file,
  discarding any unsaved runtime changes) and a "Reset settings to
  default" button (resets everything below to its built-in default —
  this never touches the config file).

Every key is optional; a partial file only overrides the keys it sets,
the rest keep their built-in default. Example, showing every key and
its default value:

```toml
[general]
mode = "enabled"          # enabled | paused | disabled | manual

[msfs]
in_cloud_sync = true
qnh_check = "CHECK"       # CHECK | SYNC

[enroute_wind]
enabled = true
deviation = 30            # 0-80, in steps of 10 (0: no deviation)

[timesync]
on_startup = true
on_situ_load = true
periodic = false

[manual_weather]
hi_oktas = 0
hi_top = 45000
hi_base = 45000
lo_oktas = 0
lo_top = 45000
lo_base = 45000
cb_oktas = 0
cb_top = 35000
cb_base = 3000
turb_severity = 0
turb_top = 5000
turb_base = 0
mb_mode = 0
mb_chance = 0
mb_outflow = 400
inv_on = false
inv_top = 2320
inv_tmp = 5
wind_dir = 0
wind_spd = 0
wind_gust = 0
wind_var = 0
precip = 0
vis_m = 9999
surf_temp = 15
qnh_hpa = 1013.25

[turbulence]
enabled = true
manual_turb_enabled = false
manual_turb_kind = "mechanical"
manual_turb_intensity = 0.3
intensity_bias = 100
lateral_size_bias = 50
wind_mode = "live"        # live | psx | manual
manual_wind_dir = 0
manual_wind_spd = 0
msfs_turb_magnitude = 100

[turbulence.type_enabled]
wave = true
rotor = true
mechanical = true
shear = true
cb = true
pirep = true
cape = true
gairmet = true

[turbulence.type_biases]
wave = 100
rotor = 100
mechanical = 100
shear = 100
cb = 100
pirep = 100
cape = 100
gairmet = 100
```
