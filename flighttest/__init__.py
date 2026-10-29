"""Automated flight testing on a PSX test sim (see docs/flighttest.md).

- sim: talk to PSX like any other client: load a situation with the
  aircraft repositioned, wait until it flies stably, correct the heading
  for the wind, record what PSX sends.
- geo: small navigation helpers and the terrain along a track.
- legs: fly a list of test legs from a TOML file, one after another.
- analyse: how hard the autopilot works in a recording (turbulence).
- fw_replay: run frankenweather with its turbulence engine replaying a
  past day's weather.
"""
