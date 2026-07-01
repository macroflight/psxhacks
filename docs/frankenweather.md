# FrankenWeather and FrankenMSFS Bridge

Real-world weather and turbulence injection for Aerowinx PSX.

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

- Zone placement adapts to flight phase: in cruise (above
  `--cruise-alt`, default 18 000 ft) stale zones behind the aircraft
  are relocated ahead; at low altitude all zones are kept within a
  tighter radius. The FMC departure and destination airports always get
  their own dedicated zone.

- Terrain turbulence (from the `frankenturb` engine, now integrated):
  fetches real-time wind from Open-Meteo and terrain elevation from
  Copernicus GLO-30 DEMs, then injects `WxBurst` events into PSX at up
  to 5 Hz. Turbulence kinds modelled: terrain wave, rotor, mechanical,
  wind-shear CAT, CB, CAPE-driven convective, PIREPs, and G-AIRMET
  regions. The MCDU C (observer panel CDU) shows a live status display
  and allows tuning of intensity per turbulence type. DEM tiles (~26 MB
  each, 1°×1°) are downloaded on demand and cached at
  `~/.cache/frankenturb/terrain/` (up to 9 tiles ≈ 235 MB). Use
  `--no-turbulence` to disable the subsystem entirely.

- Optional MSFS in-cloud sync (`--msfs-in-cloud-sync`): reads the MSFS
  in-cloud state and adjusts the active PSX weather zone's cloud layers
  so that PSX shows the aircraft as in-cloud when MSFS does. This keeps
  PSX icing in sync with MSFS conditions.

- Optional MSFS QNH sync (`--msfs-qnh-check CHECK|USE`): compares the
  MSFS sea-level pressure with the active PSX zone QNH. `CHECK` logs a
  warning when they differ by more than `--msfs-qnh-check-maxdiff` hPa
  (default 2 hPa); `USE` also updates the PSX QNH and METAR to match.
  QNH is never updated for zones whose weather comes from a real METAR
  (the METAR is authoritative for those zones).

- Optional MSFS wind corridor sync (`--msfs-wind-sync`): reads the MSFS
  wind direction, speed and OAT at the current aircraft altitude and
  injects them into the PSX wind corridor as a synthetic `FWIND` waypoint
  near the aircraft position. Supports PSX wind corridor Formats A and E;
  Formats B, C and D are left unchanged.

- The MSFS in-cloud, QNH and wind data is provided by `frankenmsfsbridge.py`
  running on the MSFS slave sim.

Requires `aiohttp`, `pyproj`, `numpy`, `rasterio`, and `requests`:

```
pip install aiohttp pyproj numpy rasterio requests
```

Key options:

```
--psx-host HOST                PSX server hostname (default: 127.0.0.1)
--psx-port PORT                PSX server port (default: 10747)
--msfs-in-cloud-sync           Sync PSX in-cloud state with MSFS (via frankenmsfsbridge)
--msfs-qnh-check CHECK|USE     Warn or correct QNH mismatch vs MSFS (via frankenmsfsbridge)
--msfs-qnh-check-maxdiff HPA   Threshold in hPa (default: 2)
--msfs-wind-sync               Inject MSFS wind into the PSX wind corridor as FWIND waypoint
--cruise-alt FT                Altitude above which cruise zone rules apply (default: 18000)
--arpt-zone-dist NM            Snap dep/dst airport zone within this range (default: 200)
--disable-psx-weather-updates  Dry run: fetch and log but do not write to PSX
--no-turbulence                Disable the turbulence subsystem entirely
--turb-rate 0-100              Scale turbulence injection frequency (100 = normal, up to 5 Hz)
--turb-intensity-bias 0-999    Global turbulence intensity bias percentage (100 = normal)
--turb-config-file PATH        JSON file for persisting turbulence settings
--debug                        Verbose logging
```

## frankenmsfsbridge.py

Companion to `frankenweather.py` for setups where frankenweather runs
on the PSX master sim but MSFS runs on a separate slave sim. The bridge
runs on the slave sim, reads `AMBIENT_IN_CLOUD`, `SEA_LEVEL_PRESSURE`,
`AMBIENT_TEMPERATURE`, `AMBIENT_WIND_DIRECTION` and
`AMBIENT_WIND_VELOCITY` from MSFS via SimConnect, and publishes them to
the PSX network as `addon=FRANKENMSFSBRIDGE:{...}` messages every time
the data changes, and at least every 60 seconds as a heartbeat.
Frankenweather picks up these messages and uses them in place of a
local SimConnect connection. Bridge data that has not been refreshed
for more than 5 minutes is considered stale and is not applied.

Requires SimConnect on the machine it runs on (the MSFS slave).

Key options:

```
--psx-host HOST   PSX server hostname (default: 127.0.0.1)
--psx-port PORT   PSX server port (default: 10747)
--interval SEC    SimConnect poll interval in seconds (default: 5)
--debug           Verbose logging
```
