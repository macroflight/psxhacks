# Changelog

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
