# Changelog

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
