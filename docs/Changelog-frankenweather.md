# frankenweather changelog

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
