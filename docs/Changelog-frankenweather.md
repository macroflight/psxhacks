# frankenweather changelog

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
