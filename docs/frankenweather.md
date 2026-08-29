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
from [Google Drive](https://drive.google.com/drive/folders/1Eu1uJCNUiLkFg9Qq8YwPCiPd9V7D5FbA?usp=drive_link).
For most users — a single PC running PSX on its default port — just start
the EXE and then control everything from
[http://localhost:9747/](http://localhost:9747/); no setup or arguments
needed.

Otherwise, to run it from source:

1. Install Python 3.13 and the packages listed below (`pip install aiohttp
   pyproj numpy rasterio requests`).
2. Run `python frankenweather.py`, pointing `--psx-host`/`--psx-port` at
   wherever PSX (or your frankenrouter, if you use one) is listening.
   Both default to `127.0.0.1:10747`, so if PSX is running on the same PC
   on its default port, no arguments are needed at all.
3. That's it. A web UI is on by default at `http://localhost:9747` (see
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
  suppression when CIN is high. TS SIGMETs downloaded by PSX are
  honoured: if the aircraft is in a TS SIGMET area, CBs are always
  generated (minimum 4 oktas) even when CAPE is zero, since TS SIGMETs
  report observed thunderstorms. METAR convective indicators (TS, TSRA,
  SHRA, GR, LTG, FC, CB sky groups) are also used and can override or
  refine the Open-Meteo prediction.

- Zone placement adapts to flight phase: in cruise (above 18 000 ft)
  stale zones behind the aircraft are relocated ahead; at low altitude
  all zones are kept within a tighter radius. The FMC departure and
  destination airports always get their own dedicated zone.

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
  dispatch link. Off by default; opt-in from the `/weather/enroute-wind`
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

```
pip install aiohttp pyproj numpy rasterio requests
```

Key options:

```
--psx-host HOST      PSX server hostname (default: 127.0.0.1)
--psx-port PORT      PSX server port (default: 10747)
--web-port PORT      Port for the standalone web UI (default: 9747)
--no-web-ui          Disable the standalone web UI
--config-file PATH   Load/save web-UI settings from this TOML file
                     (default: ~/.frankenweather.toml)
--save-logs DIR      [DEVELOPMENT] Save enroute wind diff data per flight
--debug              Verbose logging
```

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
forecast comes from Open-Meteo instead of a real dispatch link. Off by
default; opt-in from the `/weather/enroute-wind` web page (or the
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

