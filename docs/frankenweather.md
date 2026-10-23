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
  CAPE/CIN data and TS SIGMETs, not hand-set oktas.
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
[docs/BuildSystem.md](BuildSystem.md) for the full details.
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
  in-cloud/QNH/wind sync described above.
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
  weather for that zone is taken from the live VATSIM METAR for that
  airport when available, otherwise from Open-Meteo.

- CAPE-based CB (cumulonimbus) generation: computes CB coverage and
  tops from Open-Meteo CAPE/CIN data, with convective inhibition
  suppression when CIN is high. With [`--sigmet`](#sigmets),
  frankenweather also places zones in TS/TC SIGMET areas and gives them
  CBs, since TS SIGMETs report observed thunderstorms. METAR convective indicators (TS, TSRA,
  SHRA, GR, LTG, FC, CB sky groups) are also used and can override or
  refine the Open-Meteo prediction. See [CB growth and
  decay](#cb-growth-and-decay) below for `--cb-growth`, an opt-in,
  cruise-only ramp of a CB's coverage/top instead of an instant snap to
  its forecast value.

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

- Terrain turbulence (from the `frankenturb` engine, now integrated):
  fetches real-time wind from Open-Meteo and terrain elevation from
  Copernicus GLO-30 DEMs, then injects `WxBurst` events into PSX at up
  to 5 Hz. Turbulence kinds modelled: terrain wave, rotor, mechanical,
  wind-shear CAT, CB, CAPE-driven convective, PIREPs, and G-AIRMET
  regions. The MCDU C (observer panel CDU) shows a live status display
  and allows tuning of intensity per turbulence type. DEM tiles (~26 MB
  each, 1°×1°) are downloaded on demand and cached at
  `~/.cache/frankenturb/terrain/` (up to 9 tiles ≈ 235 MB).

- MSFS in-cloud sync: reads the MSFS in-cloud state and adjusts the
  active PSX weather zone's cloud layers so that PSX shows the aircraft
  as in-cloud when MSFS does. This keeps PSX icing in sync with MSFS
  conditions. Enabled by default; toggleable from the web UI.

- MSFS QNH sync: compares the MSFS sea-level pressure with the active
  PSX zone QNH. In **CHECK** mode (default) a warning is logged when
  they differ by more than 1 hPa. In **SYNC** mode the PSX QNH and
  METAR are also updated to match MSFS. QNH is never updated for zones
  whose weather comes from a real METAR (the METAR is authoritative for
  those zones). Toggleable between CHECK and SYNC from the web UI.

- MSFS wind corridor sync: reads the MSFS wind direction, speed and OAT
  at the current aircraft altitude and injects them into the PSX wind
  corridor as a synthetic `FWIND` waypoint near the aircraft position.
  Supports PSX wind corridor Formats A and E; Formats B, C and D are
  left unchanged. Off by default; toggleable from the web UI. Mutually
  exclusive with the enroute wind importer below (both write PSX's
  wind corridor).

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
  frankenrouter web interface. The web UI provides a live weather map,
  zone details, manual weather entry, turbulence tuning, the MSFS bridge
  settings toggles, and (on `/weather/config`) the time sync toggles.

- Settings persistence: pass `--config-file PATH` to load every setting
  the web UI can change from a TOML file at startup, and to enable the
  "Save current settings to file" / "Load settings from file" buttons
  on the settings page. See [Configuration file](#configuration-file)
  below.

- Open-Meteo responses (zone weather, enroute wind, and the terrain
  turbulence engine's wind/CAPE fetches) are cached on disk under
  `~/.cache/frankenweather/` and `~/.cache/frankenturb/`, in addition to
  the existing in-memory caches. Each cache's on-disk lifetime matches
  its own existing refresh cadence, so this never serves data staler
  than a continuously-running instance already would — it only avoids
  re-fetching identical data across a restart (e.g. during development).

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
--config-file PATH   Load/save web-UI settings from this TOML file
                     (default: ~/.frankenweather.toml)
--save-logs DIR      [DEVELOPMENT] Save enroute wind diff data per flight
--debug              Verbose logging
```

### Zone placement and relocation

Up to 7 PSX weather zones are placed around the aircraft and relocated as
needed. Two things don't depend on flight phase: a zone serving the FMC
departure or destination airport is never relocated, and the single zone
nearest the aircraft — which is almost always also the one PSX itself
reports as its active weather zone (`FocussedWxZone`) — is never relocated
either, even when it would otherwise qualify below. Relocating either would
strip away the zone actually influencing the aircraft and force an abrupt
handoff to unrelated weather with no gradual transition.

Everything else follows the current flight phase:

- **Cruise** (aircraft at or above 18 000 ft, and not maneuvering): new or
  relocated zones are placed ahead of the aircraft, in a forward-biased fan
  (a range of distances ahead, with some lateral spread either side of
  track). A zone only becomes eligible for relocation once the aircraft has
  actually flown past it — the zone must be more than 90° off the nose —
  and then only once it's more than `cruise_behind_dist` (default 50 nm)
  behind.
- **Maneuvering** (a hold, vectoring, or any heading change greater than
  180° within 5 minutes — see below — or below cruise altitude): new or
  relocated zones are scattered all around the aircraft at a random bearing
  instead of ahead of it, since the aircraft's track is no longer a
  reliable predictor of where it's headed next. The same "must be behind"
  rule still applies before a zone becomes eligible, but the distance
  threshold is `low_alt_dist` (default 200 nm) instead.

In both phases, a zone that is still ahead of (or abeam) the aircraft is
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
boundary.

### CB growth and decay

`--cb-growth` (experimental, off by default) is a cruise-only option that
ramps a CB's coverage (oktas) and top height gradually toward its forecast
target instead of snapping to it the instant a zone is placed ahead of the
aircraft or a new CB appears in an existing zone. Base height is never
ramped — it always reflects the live forecast, since it moves slowly and
smoothly on its own (it's derived from temperature/dewpoint, not CAPE).

The starting size and growth rate are both picked from the CB's distance
to the aircraft at the moment it first appears:

- **Starting percentage**: a random value within a range picked by
  distance (never less than 1 okta/a few thousand feet above base) —
  70-100% beyond 200 nm, 50-80% beyond 150 nm, 30-60% beyond 100 nm,
  10-40% beyond 50 nm, 5-20% at or inside 50 nm. Randomized (not a single
  fixed value per distance) so CBs don't all look identically "grown" at
  a given range. Counterintuitive at first, but the point is
  *noticeability*, not realism: a CB 200+ nm out is too far away to
  assess precisely, so starting it already mostly grown doesn't look
  like a "pop"; the same jump at 50 nm or less, where the pilot can
  actually judge its size, would.
- **Growth rate**: fixed at that moment so the CB reaches its forecast
  size at roughly the same time the aircraft reaches the zone (using true
  airspeed as the speed estimate), so a CB doesn't finish forming only
  after it's long gone, nor is there too little visible development by
  the time the aircraft arrives — **but capped**, regardless of distance:
  avoiding the "CB just popped up right next to the aircraft" effect
  matters more than a close-in CB finishing growth in time, so one
  created with little runway left (e.g. appearing in an already-nearby
  zone) simply arrives still under-grown rather than rushing to finish in
  a minute or two. The cap itself scales with the zone's forecast CAPE —
  real storm updrafts (and so convective growth) are stronger at higher
  CAPE, so a high-CAPE CB is allowed to grow faster than the 1000 ft/min
  floor used for weak/no-CAPE CBs (e.g. one forced by a TS SIGMET/METAR
  override) — but only up to a 4000 ft/min ceiling, matching real-world
  references for an actively-growing CB; CAPE's own scaling has no
  ceiling of its own, and would otherwise keep accelerating the rate even
  past the point (~1750 J/kg) where the forecast top height itself has
  already saturated at its own 55000ft ceiling. Coverage (oktas) growth
  is capped at a flat rate regardless of CAPE.

Once a CB is fully grown, if its forecast later shrinks or disappears
entirely, it decays at a fixed rate rather than reusing the (now
irrelevant) growth rate — removal isn't time-critical, since the zone
will be left behind soon regardless, and a real CB doesn't collapse
instantly either. This also means a CB that forms, then forecasts away,
then comes back doesn't flicker in and out at the boundary.

Ramping state is per-zone and resets whenever a zone is (re)placed
(relocation, a new dep/dst airport zone, or the CB-near-airport-avoidance
offset) — any CB in the newly-assigned zone starts fresh from its own
distance at that moment. The feature is active whenever frankenweather is
*not* in maneuvering mode (see "Entering/exiting maneuvering mode" above)
— this is independent of altitude, unlike [Zone placement and
relocation](#zone-placement-and-relocation)'s own separate cruise/
maneuvering distinction, so a climb or descent still ramps CBs as long as
it isn't also a hold/vectoring pattern. In maneuvering mode, or with the
flag off, CBs snap to forecast instantly, as before.

Both the forecast target and the live ramp rate are visible, not just the
current (ramped) value: the web UI's weather map popup and the
`/weather/settings` zone cards show the CB's current oktas/top next to
its target (only when they differ) and its current growth/decay rate —
this applies to the frankenrouter web UI too, since it serves the same
pages. The console's per-zone log line gains a `[CB ramp: ...]` suffix
with the signed oktas/min and ft/min rate and the target whenever that
zone's CB is actively changing.

### SIGMETs

`--sigmet` (experimental, off by default) turns on frankenweather's
SIGMET system. Without it, frankenweather doesn't use SIGMETs at all: it
only has PSX download them regularly, and PSX handles them itself. The
router's /efb "PSX SIGMETs Off" setting then turns off PSX's "SIGMETs
embedded in the planet weather model".

With `--sigmet`:

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
- PSX gets every SIGMET frankenweather could parse of the types it
  doesn't handle, and always the volcanic ash (VA) and icing (ICE) ones.
  If anything else changes PSX's SIGMET data (a situ load, another
  addon), frankenweather writes its own back. The router's /efb "PSX
  SIGMETs Off" setting gives PSX no SIGMETs at all.
- TS and TC (when handled) become CBs in frankenweather's weather
  zones, so CB turbulence and MSFS (through PSX.NET.Orchestration) see
  them too. TS/TC areas within 300 nm that aren't behind the aircraft
  get 2 zones each (a TC: a cluster of 3 around the cyclone's centre),
  in the part nearest the aircraft but at least 100 nm away. CB coverage
  comes from the SIGMET's wording: ISOL 2/8, OCNL 4/8, EMBD 5/8, FRQ 6/8,
  SQL 7/8, TC 6-8/8. The top comes from the SIGMET, the base from the
  zone's own surface temperature and dew point. CAPE can only make a CB
  bigger.
- While TS or TC is handled, one "anchor" zone is always kept near the
  aircraft without any CB, so the zone PSX uses for the local weather
  (QNH) is close by and no CB can appear right next to the aircraft.
  Near the departure or destination airport, the airport's own zone
  does this instead.
- TURB and MTW (when handled) become frankenweather's own turbulence, a
  "SIGMET" source with its own setting on the turbulence page. It
  applies inside the SIGMET's area and between its base and top, at the
  same intensity as a severe G-AIRMET (0.75; 0.5 if the SIGMET says
  MOD), fading in over the first 10 nm inside the area and 1,000 ft
  inside its base and top. An MTW SIGMET strengthens the terrain model's
  own mountain wave or rotor to at least that intensity, keeping its
  pattern of up- and downdrafts; where the terrain model finds none, it
  gives mountain wave turbulence of its own.

The SIGMETs page also shows the download status, the number of SIGMETs
of each type that could and couldn't be parsed, the nearest area of each
type and every other one within 320 nm (with the weather zones inside
TS/TC areas), the SIGMETs that couldn't be parsed, and the raw text and
the text given to PSX. The weather map shows SIGMET areas of every type
within 640 nm, one colour per type, and marks each zone's CB at its
actual position.

### Time sync

Keeps PSX's clocks (`TimeEarth`, and both cockpit clocks `TimeClockL`/
`TimeClockR`) matched to your computer's real-world time. Controlled by
three independent toggles on the `/weather/config` web page (or the
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
  has drifted more than 1 second from your computer's clock. Useful when
  flying with time acceleration: leave this on and the clock snaps back
  in sync automatically once you return to 1x, instead of staying however
  far ahead the acceleration left it.

All three call the same sync routine, which unconditionally overwrites
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

- **Waypoint list.** Built from the FMC's active route, but any
  waypoint that's part of a SID, STAR, or approach procedure — and any
  `(...)`-style pseudo-waypoint PSX generates for those procedures —
  is excluded, since real dispatch wind corridors don't cover them
  either. The list only grows or hard-resets on an actual reroute;
  waypoints PSX itself trims from the front of the route once passed
  stay on the page (dimmed) instead of disappearing.

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
  either. PSX's Format A always needs exactly 6
  flight levels per waypoint, or it may reject the whole corridor: the
  levels used are the flight-plan snapshot's own 6 levels when that
  snapshot is a valid Format-A grid (so the diff compares like-for-like),
  otherwise a fixed default set (10 000 / 18 000 / 24 000 / 30 000 /
  34 000 / 39 000 ft).

- **Writing to PSX.** The fetched data is written back to PSX as a
  Format-A wind corridor, and `Qs497` (the PSX variable controlling
  wind corridor use and simulated forecast inaccuracy) is set so its
  first digit is always `2` (use the corridor data) and the other two
  digits are the deviation percentage chosen on the web page (10-80 in
  steps of 10 — simulates that a forecast is never 100% accurate). To
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
  click "Save current settings to file" on the `/weather/settings` page
  (or the file is created by hand).
- The settings page also shows the file's path and whether it currently
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
wind_sync = false

[enroute_wind]
enabled = false
deviation = 30            # 10-80, in steps of 10

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
