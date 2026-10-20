# frankenweather changelog

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
