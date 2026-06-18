# FrankenWeather, FrankenTurb, and FrankenMSFS Bridge

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

Requires `aiohttp` and `pyproj`:

```
pip install aiohttp pyproj
```

Key options:

```
--psx-host HOST          PSX server hostname (default: 127.0.0.1)
--psx-port PORT          PSX server port (default: 10747)
--msfs-in-cloud-sync     Sync PSX in-cloud state with MSFS (via frankenmsfsbridge)
--msfs-qnh-check CHECK|USE  Warn or correct QNH mismatch vs MSFS (via frankenmsfsbridge)
--msfs-qnh-check-maxdiff HPA  Threshold in hPa (default: 2)
--msfs-wind-sync         Inject MSFS wind into the PSX wind corridor as FWIND waypoint
--cruise-alt FT          Altitude above which cruise zone rules apply (default: 18000)
--arpt-zone-dist NM      Snap dep/dst airport zone within this range (default: 200)
--disable-psx-weather-updates  Dry run: fetch and log but do not write to PSX
--debug                  Verbose logging
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

## frankenturb.py

Wind-driven terrain turbulence simulator for Aerowinx PSX. Fetches
real-time wind data from Open-Meteo and terrain elevation from
Copernicus GLO-30 satellite DEMs, then injects `WxBurst` events into
PSX at up to 5 Hz.

Turbulence kinds modelled: terrain wave, rotor, mechanical, and
wind-shear CAT. The terrain scan looks 80 km upwind for barriers and
evaluates them in priority order. The MCDU C (observer panel CDU) shows
a live status display and allows tuning of the intensity.

Requires `numpy`, `rasterio`, and `requests`:

```
pip install numpy rasterio requests
```

DEM tiles (~26 MB each, 1°×1°) are downloaded on demand from the
Copernicus S3 bucket and cached at `~/.cache/frankenturb/terrain/`
(up to 9 tiles ≈ 235 MB).

Key options:

```
python frankenturb.py --psx-main-server-host 127.0.0.1 --psx-main-server-port 10747 --rate 100
python frankenturb.py --rate 100 --accelerations --boost-server-host 127.0.0.1 --boost-server-port 10749
python frankenturb.py --debug
```

`--rate 100` sets the injection rate to ~5 Hz (the base rate scales
linearly). `--accelerations` enables the optional high-frequency
body-frame acceleration logger via the PSX boost server.

### Building a standalone executable

Run from the psxhacks repo root:

```
pyinstaller frankenturb.spec
```

The spec file bundles rasterio/GDAL/PROJ shared libraries and CA
certificates.
