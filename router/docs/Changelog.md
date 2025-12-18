# Changelog

## 2025-12-18: version 1.1.2

- Minor bug fixes, e.g fixing situ load and save that was broken when
  we optimized some things in 1.1.0
- Remove some unwanted debug output.

## 2025-12-08: version 1.1.0

- Stop sending (the rather long) FDRP ROUTERINFO and SHAREDINFO
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
