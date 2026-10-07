# Changelog

## 2026-10-07: version 1.12.1

- Update frankenrouter.toml and include in EXE ZIP

## 2026-10-07: version 1.12.0

- **New: "Global BANG" button on `/utils`**, for master and slave routers
  (not standalone), guarded by a confirmation prompt warning that it can
  trigger sound playback etc. A master (or standalone) router sends
  `bang` straight to its own upstream (the real PSX Main Server), which
  makes it re-send every variable -- a blunt but sometimes useful way to
  resync a shared cockpit. A slave router has no such connection, so it
  instead sends a new FRDP message, `addon=FRANKENROUTER:<version>:
  MASTER_BANG`, which bubbles upstream (via the same forwarding logic
  already used for `ELEVATION_SOURCE`/`TRAFFIC_SOURCE`) until it reaches
  the router that actually owns the upstream connection to the real Main
  Server, which then sends the real `bang`.

## 2026-10-06: version 1.11.0

- **New: `/efb` shows an "in SIGMET area" indicator and a "PSX SIGMETs
  On/Off" toggle.** The indicator (in the Current Location Weather card)
  reports every hazard type (TS, TURB, ICE, VA, TC, MTW) FrankenWeather
  currently finds the aircraft's position inside, not just thunderstorms.
  The toggle, next to the existing CB-avoidance toggle, flips
  FrankenWeather's new `disable_psx_sigmets` setting through the same
  generic `/api/efb/toggle` route already used for other settings.
- **Fix: `/efb` and `/api/efb/status` could serve a stale page after
  reload.** Neither response had any `Cache-Control` header, so a browser
  was free to serve a cached copy instead of re-fetching current state —
  even though the underlying data (FrankenWeather's state broadcast → the
  router's cache → this page) was already up to date. Both now send
  `Cache-Control: no-store`.

## 2026-10-03: version 1.10.0

- **New: a non-flying sim can now arm the speedbrake.** The flight-control
  input filter (which normally drops a slave sim's own `SpdBrkLever`
  updates entirely when it isn't the pilot-flying sim) now lets an
  increase through as long as it doesn't exceed the lever's armed
  position (value 44) - so the pilot not flying can arm the speedbrake
  to help the pilot flying. Decreasing, or any value past 44, is still
  dropped as before - once armed, only the flying sim can change it
  further. No config option; this applies automatically wherever
  `SpdBrkLever` was already being filtered.
- **Fix: a flight-control input dropped by this filter now gets its own
  local PSX resynced.** A sim's own hardware/PSX window applies a
  throttle, speedbrake, etc. change locally regardless of whether the
  router lets it reach the network, so when the filter above (or the
  all-control-locks or armed-exception-overshoot case) drops it, that
  sim's instruments/levers would silently drift out of sync with what
  the rest of the shared cockpit actually sees. The router now sends
  that one sim's own cached (last authoritative) value straight back to
  it, snapping its controls back into sync.
- **Change: flight control inputs (ailerons/elevator/rudder, brakes,
  throttle levers, speedbrake, tiller) are no longer written to the
  router event log at all, filtered or not.** These were either noisy
  (continuous axis input) or, for the speedbrake, redundant with the
  resync behavior above - removed the dedicated `spdbrk_change` event
  type entirely rather than just hiding it.

## 2026-10-03: version 1.9.0

- **New: Weather Mode preset on the `/efb` page.** A 3-way toggle - Full,
  PSX Auto, PSX Manual - sets FrankenWeather's mode, turbulence, and
  enroute wind in one tap instead of three. Full turns everything on;
  PSX Auto turns FrankenWeather off and lets PSX fetch its own
  automatic METAR-based weather; PSX Manual turns FrankenWeather off
  and freezes weather at its last state for manual control from the
  instructor station (addresses PSX not immediately updating zone
  weather after switching away from FrankenWeather mid-flight). Reuses
  FrankenWeather's existing `enabled`/`paused`/`disabled` modes - no
  frankenweather.py changes were needed. The turbulence half of each
  preset goes out as its own `addon=FRANKENWEATHER:TURBCOMMAND:`
  message (`mode`/`enroute_wind_enabled` use a separate `COMMAND:`
  message) - they're handled by two different functions in
  frankenweather.py and aren't interchangeable, which an earlier build
  of this feature got wrong (the chip never highlighted after switching
  away from Full, since turbulence silently didn't follow the preset).

## 2026-10-03: version 1.8.1

- **Change: the master caution text for a router/filter-state error is
  now `(ROUTER)` instead of `FRANKENROUTER`** (Qs418/FreeMsgW), so it
  reads as an obvious addon-generated message rather than something
  that could pass for a genuine Boeing EICAS caution.
- **Quieter logging for `addon=MSFS.CLIENT` messages** (PSX.NET.MSFS
  Client's jetway control traffic, e.g. `QUERYJETWAY`/`TOGGLEJETWAY`/
  `STOPJETWAY`) - now logged at debug instead of info, like the other
  known-noisy addons.

## 2026-10-03: version 1.8.0

- **New feature: forced sim disconnect.** A new FRDP `DISCONNECT_SIM`
  addon message (`addon=FRANKENROUTER:<ver>:DISCONNECT_SIM:<json>`,
  carrying `from_sim`/`target_sim`/`reason`) is flooded to every
  frankenrouter in the topology, the same way `ROUTERINFO`/`SHAREDINFO`
  already are. Only the *edge* router of a `slave` sim (the one whose
  own upstream connects to a router in a different sim, rather than
  another router within the same sim) matching `target_sim` acts on it,
  closing its upstream connection. The new `on_forced_disconnect`
  setting in `[[upstream]]` controls what happens next: `disconnect`
  (default) stays with no upstream at all, `switch` immediately falls
  back to the configured upstream instead, and `ignore` opts a router
  out of DISCONNECT_SIM entirely. The start page now shows a banner
  with who disconnected us and why, plus a "Reconnect to last upstream"
  button (`POST /api/upstream/reconnect`).
- **New Utils page: Connected sims** (`/utils/sims`), listing every sim
  currently visible via `ROUTERINFO` (excluding our own), each with a
  "Disconnect sim" button that requires a free-text reason and
  confirmation before sending the `DISCONNECT_SIM` message. The page
  auto-refreshes every 5s.

## 2026-10-03: version 1.7.0

- **New feature: `/services` page**, showing the status of sim-support
  services monitored by the new `frankencontrol.py` addon (BACARS,
  HAFAP/CPDLC, FrankenWeather, SRSL-PSX, CMC-PSX, FrankenTanker,
  FrankenPush, psx_simlink_bridge), with confirm-gated Start/Stop/Restart
  buttons per service. The router caches FrankenControl's
  `addon=FRANKENCONTROL:1:STATUS:<json>` broadcasts unconditionally (not
  gated on any forwarding restriction), and the page itself is driven
  entirely by whatever that status contains - no service list is
  hardcoded on the router side, so it can't drift out of sync with
  `frankencontrol.py`'s own list. Button presses send
  `addon=FRANKENCONTROL:1:COMMAND:<json>` both upstream and to all
  clients, the same pattern as the existing HAFAP/CPDLC reset button.

## 2026-10-01: version 1.6.7

- **Bug fix: `PNF_MODE_BITS` (the `PnfMode`/`Qi217` bit-label table used for
  sim-event logging) didn't match reality.** It listed masks 16 and 256 for
  "silent tasks"/"S/C alt", which can never occur since `Qi217`'s real
  range is 0-15 (4 bits), and had masks 2 and 4 mislabeled too. Verified
  live against a real PSX instance and its Instructor Station display
  (alongside the related `frankenusb.py` `SEAT_SELECT` fix): mask 1 = seat
  (set = left, clear = right), mask 2 = callouts, mask 4 = silent tasks,
  mask 8 = "Sets S/C alt if VNAV PTH engaged" (step climbs).

## 2026-09-29: version 1.6.6

- **New feature: "HAFAP(CPDLC) reset" button on the `/utils` page**, sending
  `addon=HAFAP:1:RESET` (both upstream and to all connected clients, so it
  reaches Hoppie PSX CPDLC regardless of where in the router mesh it's
  connected).

## 2026-09-28: version 1.6.5

- **Bug fix: a "FRANKENROUTER" master caution could get permanently stuck**
  after an upstream reconnect. `_housekeeping_enable_master_caution()` could
  run before the upstream's welcome handshake (`load1`/`load2`/`load3`)
  finished, while `self.routerinfo` was still empty -- a guaranteed false
  "no sim is sending elevation/vPilot data" positive on every (re)connect. If
  that false positive wrote `Qs418=FRANKENROUTER` to PSX while its own
  resync was still in flight, PSX's resync could land moments later and
  silently reset the cached `Qs418` back to empty behind our back -- which
  then permanently broke the clear check (`mcmessage == message`), since the
  cache never again read back exactly what we'd sent, so the router never
  issued the explicit `Qs418=` clear again. Diagnosed from a real incident:
  a reconnect during a 50+-client mass-simultaneous-connect event left the
  caution stuck for the rest of the session. Fixed by not evaluating/acting
  on error state until `upstream_ever_welcomed` is true.

## 2026-09-28: version 1.6.4

- **New feature: on Windows, closing the router's console window (the X
  button), logging off, or a system shutdown now also trigger a graceful
  shutdown**, matching `SIGTERM`/Ctrl-C/the web UI button. Windows delivers
  these as `CTRL_CLOSE_EVENT`/`CTRL_LOGOFF_EVENT`/`CTRL_SHUTDOWN_EVENT`
  through a console-control mechanism that's entirely separate from POSIX
  signals, so none of the existing handling caught them. Requires `pywin32`
  (a silent no-op if it's not installed, or on non-Windows platforms).
  Windows still forcibly terminates the process after ~5 seconds regardless,
  so this is a best-effort grace window, not a guarantee -- and it still
  doesn't cover `kill -9`/Task Manager "End Task" (`SIGKILL`/
  `TerminateProcess`), which no process can catch.

## 2026-09-28: version 1.6.3

- **New feature: `SIGTERM` now triggers the same graceful shutdown as the
  web UI's "Shutdown router" button** (sends `"exit"` to every connected
  client and the upstream connection, then closes sockets cleanly), instead
  of killing the process outright. Covers a plain `kill <pid>`, a process
  manager, or a `systemd stop`. Does not cover a forceful `kill -9`/Task
  Manager "End Task" (not catchable by any process), or closing a Windows
  console window (a separate `CTRL_CLOSE_EVENT` mechanism Python doesn't
  expose without an extra dependency).
- **Bug fix: the web UI's "Shutdown router" button never actually performed
  a graceful shutdown.** It reset `SIGINT`'s handler to `signal.SIG_DFL`
  before re-raising it — but `SIG_DFL` means "let the OS terminate the
  process", not "raise `KeyboardInterrupt`" (that's `signal.default_int_handler`,
  a different thing). The button has been hard-killing the router since
  this code was written, skipping the "exit" messages and clean socket
  closes entirely. Found and fixed while adding the `SIGTERM` handler above.

## 2026-09-26: version 1.6.2

- Add In-flight music link to /efb page

## 2026-09-23: version 1.6.1

- **Raised the default message-rate-limit thresholds** (`[performance]`
  `received_messages_per_second_warning_limit`/`_critical_limit` and the
  `sent_*` equivalents) from 80/120 to 140/175. Forensic analysis of a
  real EKCH go-around showed the previous limits tripping the cockpit
  "FRANKENROUTER" master caution during entirely normal, expected
  traffic: PSX's own flight-control variables (`FltControls` etc.)
  legitimately run at 20-80 Hz inside PSX's internal high-speed physics
  loop during active hand-flying, and a go-around itself is a
  high-event-density maneuver (TOGA, flap/gear retraction, several FMA
  mode changes in quick succession). There is no evidence the previous
  limits reflected an actual capacity problem for the router or any
  addon; this is a false-alarm fix, not a performance change.

## 2026-09-19: version 1.6.0

- **New feature: cross-sim PTT presses are now translated into a
  synthetic `addon=GROUND.HANDLING` event for PSX.NET.Orchestration.**
  Controlled by the new `[filtering] ptt_ground_handling_translation`
  config option, on by default.

## 2026-09-12: version 1.5.0

- **Bug fix / config safety: `[[access]]` rules now reject unknown
  keys.** A typo'd key (e.g. a misspelled `password`) was previously
  silently ignored rather than raising an error. For a rule like
  `match_ipv4 = ["ANY"]`, that meant the intended password check
  simply never happened, silently granting full access to anyone who
  connected. Any unrecognized key in an `[[access]]` rule now fails
  the router at startup instead.

- **New feature: EFB-friendly `/efb` web page.** A compact,
  single-screen status/control page designed to be embedded in an EFB
  app: router status, weather status and controls, and current
  location weather, with live polling and a persistent errors banner.

- **New feature: the "Shutdown router" button is now opt-in.** The web
  UI's shutdown button (and its `/shutdown` confirmation page) is
  hidden by default; enable it with the new `rest_api_shutdown_enabled`
  config option under `[listen]`. This only gates the human-facing
  button/confirmation page - the `POST /api/shutdown/yes` API used by
  scripts/automation to shut the router down directly is unaffected
  either way.

- **New feature: FRANKENWEATHER addon messages are now filtered
  per-client.** These messages are large but infrequent. Only clients
  that actually need them - frankenweather itself (to detect other
  running instances) and every frankenrouter (for the weather web UI)
  - now receive them, reducing load on slow or limited clients such as
  Arduino-based hardware.

- **New feature: GROUND.HANDLING addon messages are now filtered
  per-client**, for the same reason as the FRANKENWEATHER filtering
  above - these are small but frequent, so only clients that need them
  now receive them. Also suppresses a spurious warning about
  PSX.NET.GroundHandling's own addon messages.

- **Bug fix / workaround: ingress-filter `Qs546` for 5 seconds after a
  client connects.** Some PSX clients send `Qs546` right after
  connecting - apparently when their current situ differs from what
  they received in the welcome message - which wipes CG and
  runway/position data from the master server's FMC. Filtering it out
  for the first 5 seconds after connection works around this.

- **New feature: non-router clients can now subscribe to FRDP
  broadcasts** (`ROUTERINFO`/`SHAREDINFO`), needed to give addons like
  frankenpush access to that data without themselves being a router.

- **New feature: per-addon version numbers.** Every major addon
  (frankencduproxy, frankenprint, frankenpush, frankenrouter,
  frankentanker, frankenusb, frankenweather) now has its own version
  number - frankenrouter_ident always mirrors frankenrouter's - sent
  as part of the `name=`/`clientName=` messages every addon sends to
  PSX. A new `release.py` script manages version bumps and changelog
  reminders across all of them.

## 2026-07-14: version 1.4.3

- **New feature: work around PSX not always broadcasting the
  jettison selector position (`Qh274`) after its MLW configuration
  (`Qi25`) changes.** PSX recomputes `Qh274` internally to keep the
  jettison switch position consistent whenever `Qi25` changes (e.g.
  when loading a situ with a different jettison switch type), but
  doesn't always send the network the recomputed value (see [Aerowinx
  forum topic](https://aerowinx.com/board/index.php/topic,7861.0.html)).
  Rather than hardcode PSX's undocumented remap table (an earlier,
  removed attempt at that got the mapping wrong), the router now asks
  its own upstream for a `bang` whenever it sees `Qi25` change, and
  forwards only whatever in that private reply actually differs from
  what it already had cached - so an unsolicited full resync never
  floods already-connected clients. Sending `bang` can have side
  effects of its own (some addons react to a variable arriving even
  when its value is unchanged), so this is controlled by the new
  `[psx] jettison_resync_fix` config setting, defaulting to enabled.

## 2026-07-14: version 1.4.2

- **Bug fix: the 1.4.1 START-leak fix was still incomplete for chains
  of three or more routers.** A router relaying a `start` request on
  behalf of a chained child frankenrouter (rather than one it sent for
  its own directly-connected client) never updated its own
  `router.start_sent_at`. When the response then arrived from
  upstream, the private-response window check on that hop compared
  against a stale timestamp, failed, and let the value fall through to
  a normal broadcast - leaking it to that router's own unrelated local
  clients. `handle_start()` now refreshes `router.start_sent_at`
  whenever it relays *any* `start` upstream, own-client or chained, so
  every hop in the chain correctly treats the response as private.

## 2026-07-12: version 1.4.1

- **Bug fix: a new client connecting to a master router could cause
  visible aircraft repositioning on already-connected clients of a
  chained slave router.** The master's private "start" response
  (e.g. `Qs122`) was forwarded unconditionally to any connected
  frankenrouter, regardless of whether that router was actually
  welcoming one of its own clients. The receiving router had no way
  to tell the message was meant to stay private, and would
  re-broadcast it locally using its own unrelated timing state. Fixed
  by tracking, per connection, when it last relayed a `start` request
  upstream, and only forwarding the private response to a
  frankenrouter that has one currently in flight.
- **Bug fix: the previous START-mode fix (1.4.0) didn't actually
  work.** Its window check was keyed off `router.last_load3`, which
  is only updated when a `load3` message is itself routed - but the
  `load3` sent to a newly-welcomed client bypasses routing entirely,
  so the check was effectively always false. Now keyed off
  `router.start_sent_at`, which is set at the moment the router
  actually sends `start` upstream.

## 2026-07-12: version 1.4.0

- **Flight Info page now driven by Flight Centre.** A planned flight's
  data is sent to the router automatically from Flight Centre (via
  frankenpush). It is no longer possible to edit planned flight
  information (crew, airline, route, etc.) directly in the router
  control panel; the in-flight scratchpad and checklist toggles are
  still available there. Related config file entries that are no
  longer needed were removed.
- **GPS jamming/spoofing improvements.**
  - The spoofed GPS position is now propagated to Navigraph Charts'
    moving map via the PSX SimLink Bridge, by feeding it fake Qs121
    data calculated from the true position plus the GPS drift.
  - While jamming is active, the SimLink Bridge now sees a
    slowly-changing mix of the true position and several implausible
    decoy positions, rather than a frozen position (which was itself
    a giveaway that something was wrong).
  - The Qs121 keepalive that re-broadcasts stale data for a stationary
    aircraft is now inhibited unless the aircraft is confirmed on the
    ground, since it could otherwise interfere with gate
    repositioning while airborne but momentarily stationary (e.g.
    paused).
- **MSFS weather bridge & wind corridor automation.** The router web
  UI's weather zones page gained a live MSFS bridge status section
  (in-cloud, QNH, wind vertical, precip) with sync toggles, and the
  wind corridor can now be refreshed automatically from hourly
  OpenMeteo forecast data during flight.
- **Bug fix: Qs122 (a START mode variable) was incorrectly filtered
  from upstream outside of an active client welcome.** This meant
  e.g. an EFB-initiated repositioning never reached the PSX main
  clients (one of which runs the boost server), desyncing shared
  cockpit instances. Qs121 would normally paper over this, but PSX
  does not send Qs121 while stationary.
- **Router type handling made more robust.** Elevation, traffic and
  flight control filters are now explicitly forced off for `master`
  and `standalone` routers; those router types now shut down if they
  end up connected to a frankenrouter upstream (which should never
  happen); the router type is now shown in the web UI.
- **Code review pass: several correctness bugs fixed and dead code
  removed**, including:
  - A missing exception guard around the parking-brake-release fix's
    `Qh397` cache lookup that could crash the message forwarder task
    entirely.
  - An unbound/stale `reader`/`writer` reuse on unexpected upstream
    connect errors.
  - `addon=FRANKENMSFSBRIDGE` was incorrectly filtered even when
    relayed legitimately from upstream, unlike the equivalent
    `Qi198` filter.
  - The FRDP SHAREDINFO "should never happen" invariant guard now
    also covers `standalone` routers, not just `master`.
  - An unescaped `.` in the frankenrouter self-identification regex,
    two unused `RulesCode` enum members, and a couple of latent
    missing-`continue` bugs in the connection retry/read loops were
    also cleaned up.
- **Command line option cleanup.** Removed a number of command line
  options that were unused, redundant with the config file, or better
  handled as fixed internal values: `--forward-please-be-so-kind-and-quit-upstream`,
  `--read-buffer-size`, `--housekeeping-interval`, `--upstream-interactive`,
  and the entire router state-cache-to-disk feature
  (`--use-state-cache`, `--state-cache-file`, `--no-state-cache-file`).
  The router's variable cache is now always in-memory only for the
  lifetime of the process and is never read from or written to disk.
  Removed options are still accepted but now print a deprecation
  warning instead of failing outright.
- **Master addon duplicate-check patterns updated:** removed the
  little-used `TURB`/`UTIL` patterns and the separate "at least one
  BACARS client" warning check; added a `WEATHER` pattern to match
  frankenweather's client ID.
- The router now flushes its log and traffic log files every 60
  seconds, to help with mid-flight log analysis.
- Added a button under *Util* in the web UI to force aircraft wheels
  to ground level.
- Own (potentially large) `addon=` messages in psxhacks' own
  namespaces are no longer sent to `nolong` clients regardless of
  their current length, matching how other long messages are already
  withheld.
- Fixed a broken EXE build.

## 2026-07-04: version 1.3.8

- **Event log.** The router now maintains a log of significant in-flight
  events, written to a file alongside the router log and viewable live in
  the web UI under *Util > Event log*. Events include changes to MCP window
  content, PSX human pilot settings, and shared cockpit state transitions.
- **Fix: master sim router elevation filter causing PSX to use built-in
  elevation database.** When a slave sim was stationary the Qi198 value
  sent by its MSFS Router did not change, causing every received Qi198 on
  the master router to take the "send upstream only" code path in the
  rules engine. That path forwarded the value to PSX but did not update
  the router cache age for Qi198. After 60 seconds the housekeeping check
  concluded no elevation was being received and sent `Qi198=-999999` to
  PSX, switching it back to its internal elevation database. The cache is
  now updated on every received Qi198 regardless of whether the value
  changed, keeping the housekeeping check satisfied.
- **Fix: master sim router incorrectly enabling elevation, traffic and
  flight control filters on upstream connect.** All router types enabled
  these filters on every upstream reconnect. Master sim routers must never
  block elevation or traffic data from reaching PSX, so the filter enable
  is now skipped for `type = master` routers. The flight control filter
  status display also now always shows `off` for master routers.

## 2026-06-30: version 1.3.7

- **Hold client connections until upstream is ready (default on).** The
  router now waits for the upstream PSX main server or master router to
  complete its welcome sequence (i.e. send `load3`) before it opens its
  listening port to clients. This guarantees that every connecting client
  receives a full and current set of PSX variables, rather than an empty
  or stale cache. Once the upstream has welcomed the router at least once,
  the port stays open even if the upstream later disconnects and
  reconnects. Opt out by setting `wait_for_upstream_welcome = false` in
  the `[listen]` section of the config file.
- **FRDP password hashing.** Passwords are no longer sent in cleartext
  over the network. When connecting to an updated upstream router, the
  downstream now authenticates with an HMAC-SHA256 challenge-response
  (the upstream sends a one-time nonce; the downstream replies with
  `AUTH:hmac-sha256:<HMAC-SHA256(password, nonce)>`). Old routers that
  do not issue a challenge still receive the cleartext password, so
  mixed-version networks continue to work during the transition.
- **Bad-password handling.** When authentication fails, the upstream
  router now sends an explicit `AUTH_FAILED:<reason>` message before
  closing the connection. The downstream router intercepts this, logs a
  prominent error (with `!` separator lines), stops the reconnect loop,
  and prompts the user to press a key before exiting. Previously the
  router would silently retry the bad password in an endless loop.
- **Password character validation.** Passwords in the config file
  (`match_password` and `[[upstream]] password`) are now validated on
  startup; only printable ASCII characters (`!` through `~`, no spaces)
  are accepted. The same check applies to passwords entered interactively
  in dumb-client mode or with `--upstream-interactive`.

## 2026-06-18: version 1.3.6

- Added weather control panel to the router web UI

## 2026-06-06: version 1.3.5

- Document how to configure the flight info page
- Make it more clear that observer mode is passive observer mode (you
  cannot be observer and e.g do pushback)
- Add new flight info toggle "Captain is VATPRI"
- Make the state cache file opt-in. We probably don't want or need the
  variable cache to be persistent. Make it opt-in and consider
  removing later.

## 2026-05-24: version 1.3.4

- Remove temporary SRSL filter (SRSL 0.3 now supports shared cockpit)
- Add possible workaround for IRS alignment failures

## 2026-05-20: version 1.3.3

- Observer mode controllable from web interface
- Show critical errors in web interface
- Check network for new errors (e.g more than one BACARS)
- Disconnect clients if write buffer too big
- Improve message rate monitoring
- Exponential backoff delay when reconnecting to upstream
- Drop PTT presses from other sims
- Use UTC in logs
- Possible workaround for "rubberband bug" (send Qs121 when main server is not)

## 2026-05-14: version 1.3.2

- Added flight information page in the router web UI. This can be used
  as a scratchpad for shared cockpit data, e.g who sits in which seat,
  which route we are flying, airframe, etc.
- Added a "session password" feature - the master sim owner can now
  generate a random session password and shared that with the crew,
  who can use that instead of a normal static password to connect to
  the master sim router. This is intended both to make it easier to
  handle multiple master sims, but also to make it less likely that
  people accidentally connect to an active master sim that is in use
  without realizing it.
- Router web UI revamped
- Elevation and traffic filters easier to use

## 2026-05-03: version 1.3.0

- Router network error reporting: each router now includes an `errors`
  list in its FRDP ROUTERINFO message. The master sim router collects
  errors from all routers and triggers the FRANKENROUTER master caution
  if any router has an active error. Errors are also shown in the
  status display of every router in the network.
- The following conditions are now reported as errors:
  - Write buffer for a connection exceeds `write_buffer_critical_limit`
    (renamed from `write_buffer_warning`)
  - Received or sent messages per second for a connection exceeds the
    new `received_messages_per_second_critical_limit` /
    `sent_messages_per_second_critical_limit` settings (default: 60/s)
  - More than one sim sending MSFS elevation data to PSX
  - No sim sending MSFS elevation data to PSX (master sim router only)
  - More than one sim sending vPilot traffic data
  - No sim sending vPilot traffic data (master sim router only)
- Configurable per-sim keyword filtering: `filter_from_other_sim`
  drops listed keywords when received from a frankenrouter in a
  different simulator; `filter_to_other_sim` suppresses listed keywords
  when forwarding to frankenrouters in other simulators. Useful for
  cockpit lighting variables (Qh6–Qh12) that should not bleed between
  simulators in a shared cockpit setup.
- Removed non-functional Alt-F4 / window-close protection (it was
  advertised in 1.2.0 but could not be made to work reliably on modern
  Windows). Ctrl-C protection is still in place.
- Bug fix: master router no longer incorrectly enables its own
  elevation/traffic filters when broadcasting SHAREDINFO.
- Bug fix: jettison selector workaround no longer crashes when the
  router is not connected to upstream.
- Maintenance: replaced deprecated aiohttp `make_handler()` API with
  the `AppRunner`/`TCPSite` API.

## 2026-05-02: version 1.2.0

- Single-click setting of elevation and traffic filters. Now only one
  person needs to do this, and the other routers change their filters
  automatically.
- Make it more difficult to accidentally stop the router with e.g
  Control-C or Alt-F4
- Workaround for jettison selector bug
- Warn if routers in the network run different versions
- frankenrouter_ident.py will now send both ID and display name

## 2026-04-24: version 1.1.7

- Major changes to improve latency. We now batch forward messages that
  are in the queue (so we don't add latency, we just batch the
  messages to each recipient and then send them in one go)

## 2026-04-24: version 1.1.6

- Minor improvement to "basic mode" on-screen info

## 2026-04-12: version 1.1.5

- Performance improvements, including using TCP_NODELAY
- Include the last 10 FRDP RTT measurements in routerinfo, allows all
  routers to see how the other router-to-router connections in the
  network are doing

## 2026-03-26: version 1.1.4

- A/P disconnect button will now enable the flight controls in your
  sim (i.e no need to use frankenusb just for this)
- Performance improvements

## 2026-03-25: version 1.1.3

- add per-client message/s and bytes/s to API
- minor improvements to /api/stats
- bug fixes

## 2025-12-18: version 1.1.2

- Minor bug fixes, e.g fixing situ load and save that was broken when
  we optimized some things in 1.1.0
- Remove some unwanted debug output.

## 2025-12-08: version 1.1.0

- Stop sending (the rather long) FRDP ROUTERINFO and SHAREDINFO
  messages to non-frankenrouter clients. This caused problems for some
  embedded clients with limited cpu or memory. However, this also
  means that you now need to be a little careful when using multiple
  routers in your sim, see the "PSX network topology" section of
  [README.md](../README.md)
- Changes to client name handling to be more like other PSX routers
  (message on the format name=X:Y are now interpreted as X being a
  short client identifier that is different if you have multiple
  copies of that addon running, while Y is a longer more descriptive
  name).
- Various changes to improve how the router works in more complex
  simulators with many (tested with >50) clients.
- More performance data available in API (messages/second, etc.)
- Log files (both the traffic log and the status output log) can now
  be rotated when they reach a certain size (and a configurable number
  of old versions kept on disk).

## 2025-12-01: version 1.0.4

- Show router version in status display, and from now on - update the
  router version number for each publicly available release. Not all
  router versions might a changelog entry, though.

## 2025-??-??: version 1.0

- Selectively filter Qs119 to avoid unwanted printouts, e.g after
  "bang"
- Add toggleable filter for elevation injection from MSFS (for shared
  cockpit - avoids more than one sim trying to control the shared
  aircraft's elevation).
- Add toggleable filter for traffic data from the vPilot plugin (for
  shared cockpit - avoids having more than one sim injecting other
  aircraft's position into PSX)
- Improve filtering to PSX.Sound to avoid nuisance sounds after "bang".
- Filter most CPDLC messages so they won't be printed by BACARS
- Various API improvemends (better documentation, disconnect client,
  IP blocklist, ...)
- Improved flight control lock for shared cockpit, filtering moved
  from frankenusb to the router to handle even flight controls
  connected via PSX or other I/O solutions.
- Add API call to print messages (used in shared cockpit to route
  vPilot private messages to the shared sim printer)
- Can now switch to another shared cockpit master sim (or local PSX
  main server) using the API or a simple web page.

## 2025-09-19: version 0.9

- Support for the PSX 10.184 clientName keyword
- Improve multi-router support

## 2025-08-03: version 0.8

- Simplify shared cockpit slave sim setup - no config file needed
- Binary frankenrouter.exe available

## 2025-07-20: version 0.7

- Now has a single set of forwarding rules in a unit-testable module
  (rules.py)
- Various minor improvements

## 2025-07-12: version 0.6

- Improve documentation
- Add basic REST API
- Improve performance monitoring
- Use TOML for config file
- Start tracking variable stats
- Use addon= prefix for FRDP messages instead of frankenrouter=
- Start moving parts of the code into separate modules

## 2025-06-30: version 0.5: first proper release

- All addons (at least in my sim) are working when connected to the router
- Stable enough for long flights
- Useable for shared cockpit
