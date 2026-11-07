# psxutils changelog

## Unreleased

- New: `show_boost_rate.py`, a diagnostic tool that measures a PSX boost
  server's data rate and longest gap every 10 seconds and logs it to a
  file, to spot a PSX instance that can't keep up (nominally about
  20 Hz in flight).

## 1.1.0 (2026-10-21)

- New: `compare_metars.py`, a diagnostic tool that downloads the current
  METARs for all stations from both VATSIM and NOAA (the two sources
  frankenweather uses) and reports how they differ: stations only one
  source has, report age, which source is newer, and which fields (QNH,
  wind, visibility, weather, clouds, temperature) disagree. `--detail`
  shows a per-station diff; `--icao` and `--prefix` limit the
  comparison. Included in psxutils.zip as `compare_metars.exe`.

## 1.0.0 (2026-09-12)

- Initial versioned release. Groups show_hid, show_psx, show_usb, and
  temporary_weather_logger under one shared version number, built and
  published together as a single psxutils.zip release asset.
