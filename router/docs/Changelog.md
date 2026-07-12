# Changelog

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
