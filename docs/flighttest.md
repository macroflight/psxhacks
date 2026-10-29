# Flight testing (psx_flighttest.py)

`psx_flighttest.py` and the `flighttest` package fly a PSX test sim
automatically: reposition the aircraft anywhere, wait until it flies
stably, correct the heading for the wind, and record what PSX sends.
It was written to test frankenweather's turbulence in a given place and
weather, but works for anything that needs the aircraft at a given place,
altitude and track.

It writes to the sim: it repositions the aircraft, presses MCP buttons,
and sets the registration, FMC route and flight number. Use it only on a
test sim, connected to a router port (or a PSX Main Server) of its own.

## How it flies

- **Repositioning** works like PSX itself: motion freeze (`SimPaused=1`),
  `load1`, `load2`, every variable of a situation file (`.situ`, names
  translated to Q-codes), `load3`, release (`SimPaused=0`). The start
  position overrides the situation's `StartPiBaHeAlVsTasYw`. The base
  situation should be an aircraft in cruise with the autopilot and
  autothrottle engaged.
- **After the release** it presses HDG SEL and FLCH like a pilot: the
  situation's lit HOLD modes keep their old heading and altitude, so the
  aircraft would turn back and climb. FLCH captures the MCP altitude and
  ALT HOLD then lights by itself; if FLCH is still active near the
  altitude after 10 seconds, it presses ALT HOLD (FLCH chasing the speed
  in strong gusts once climbed away by 6,000 ft). A heavy 747 needs
  about 270 kt at low level.
- **Stable flight** is checked before recording: for 20 seconds in a row
  within 3 nm of the start point (plus the distance flown), 400 ft of the
  altitude, 10 degrees of the heading, under 5 degrees of bank and under
  300 ft/min. The altitude is PSX's true altitude (`PiBaHeAlTas`), not
  what the altimeter shows, which differs with the QNH and the
  atmosphere's temperature. If no autopilot (CMD L/C/R) is engaged for
  3 seconds it gives up at once; a plan then freezes the sim. A control
  input on the sim's own hardware (which the network never sees) can
  disconnect the autopilot right after the release.
- **Wind correction**: it measures the ground track for 15 seconds and
  turns the heading knob by the difference.
- **Recording** writes `<epoch> <Q-code>=<value>` lines: position and
  attitude (`PiBaHeAlTas`), injected turbulence bursts (`WxBurst`),
  `OnGround`, `SimPaused`, the autopilot servos (`ApServo`, three values
  -999..999 at about 10 Hz, undocumented: taken to be elevator, aileron,
  rudder) and control surface angles (`SurfAilEleRud`, whole degrees),
  both asked for with `demand=`, the pilot's flight controls
  (`FltControls`: elevator, aileron, rudder; they stay 0 on the
  autopilot) and thrust lever angles (`Tla`), and the seven weather zones
  (`Wx1-7`, `WxMode1-7`). How often and how far the autopilot and
  autothrottle move the controls is a measure of the turbulence as the
  pilot feels it.
- **Accelerations** can be recorded alongside: frankenboostproxy's
  `--accel-port` (`.accel`, JSON at 5 Hz) and the raw PSX boost feed
  (`.boost`, about 13 Hz, e.g. frankenboostproxy's relay port 10749).
  Use the raw feed for vertical g: it is worked out from the altitude
  (1/100 ft) on PSX's own millisecond clock. frankenboostproxy's
  accelerations are interpolated from position data and smooth the
  vertical g about 5-10 times too low; they aren't a calibrated g meter.

## Commands

```bash
python3 psx_flighttest.py --psx-port 11001 load ~/autotest-cruise.situ \
    --pos 51.47 -0.19 270 1000 270 --set McpWdoAlt=10 --set McpWdoHdg=270
python3 psx_flighttest.py --psx-port 11001 stable 51.47 -0.19 270 1000
python3 psx_flighttest.py --psx-port 11001 track --correct 270
python3 psx_flighttest.py --psx-port 11001 record leg.rec 150 --boost-port 10749
python3 psx_flighttest.py analyse leg.rec
python3 psx_flighttest.py --psx-port 11001 send Qi129=1
python3 psx_flighttest.py --psx-port 11001 legs flighttest/plans/uk-lowlevel-2026-10-24.toml
```

`legs` needs frankenturb's dependencies (for the terrain check), e.g.
the lint venv's Python.

`analyse` reports, per recording, the turbulence bursts per minute and
how far and how often each control moved (degrees or servo units per
minute, reversals per minute, spread), ignoring the one-degree flicker
of surfaces held between two whole degrees. With a `.accel` or `.boost`
file next to the recording it adds the vertical acceleration: RMS, 95th
percentile and largest deviation from the median, and the share of time
in the bands pilots report turbulence by (light from 0.05 g, moderate
0.2 g, severe 0.5 g, extreme 1.5 g).

## Test plans

A plan is a TOML file of `[[leg]]` tables, flown one after another.
Settings at the top apply to every leg; a leg can override any of them.
See `flighttest/plans/` for an example.

| Setting | Default | Meaning |
|---|---|---|
| `situ` | `~/autotest-cruise.situ` | Base situation |
| `registration` | `G-PING` | Registration shown e.g. on the Flight Centre map |
| `callsign` | `TEST{n}` | Flight number; `{n}` is the leg number |
| `first_number` | 1 | Number of the first leg |
| `route_airport` | `EGLF` | FMC route from and to this airport |
| `lat`, `lon` | (required) | Reference point, e.g. an airport |
| `track` | (required) | True track to fly through it |
| `magvar` | 0 | Magnetic variation (east positive) for the MCP heading |
| `alt_ft` | (required) | Altitude (MCP altitude window) |
| `tas_kt` | 270 | Start speed (also the MCP speed, at most 330) |
| `mcp_speed` | none | MCP speed window instead, e.g. `1822` for Mach 0.822 at cruise |
| `before_nm` | 0 | Start this far before the reference point |
| `check_nm` | 40 | Terrain checked this far along the track from the start |
| `min_clearance_ft` | 1000 | Terrain must be this far below the altitude |
| `reverse_if_terrain` | false | Try the reverse track if the terrain is too high |
| `correct_track` | true | Correct the heading for the wind before recording |
| `record_s` | 150 | Recording length |
| `stable_timeout_s` | 150 | Give up (freeze the sim, stop) if not stable by then |
| `stable_bank_deg`, `stable_vs_fpm`, `stable_hold_s` | 5, 300, 20 | Stability limits (looser for legs in rough air) |
| `output_dir` | `.` | Recordings go to `<output_dir>/<name>.rec` |
| `accel_port`, `boost_port` | none | Also record accelerations (`.accel`, `.boost`) |
| `accel_host` | `127.0.0.1` | Host of both |
| `replay_file`, `replay_time` | none | Set frankenweather's replay clock for the leg |

A leg whose terrain is too high is skipped. A leg that doesn't fly
stably freezes the sim and stops the plan.

## Test procedure

- Run your own frankenweather on the test sim's router port, so its
  settings (turbulence on/off, manual turbulence, intensity) can be
  changed freely; manual turbulence holds a fixed kind and intensity,
  e.g. for calibration.
- For clean turbulence tests set frankenweather's SIGMET preset to Off.
  Without `--sigmet` frankenweather has PSX embed SIGMETs in its planet
  weather (as intended), and PSX then has CBs from TS SIGMETs that
  aren't in the seven weather zones and that a test can't see or avoid.
- Before a test, check the weather zones near the track for CBs.
- Read the gross weight from the Instructor Station: PSX's `EicasGrWt`
  comes from the FMC's ZFW entry, so it doesn't follow a change of the
  true ZFW (`TrueZfw`).
- Turbulence bursts (`WxBurst`) may also drive seat shakers (e.g. the
  Vibrate addon with a ButtKicker): any change to their size or rate
  needs a test in the real sim's seat.

## Replaying a past day's weather

`flighttest/fw_replay.py` runs frankenweather with its turbulence engine
replaying a past time, so a pilot's report can be tested in the same
conditions:

```bash
echo "2026-10-24T17:50:00+00:00 $(date +%s)" > replay_time.txt
python3 -m flighttest.fw_replay --replay-file replay_time.txt \
    --psx-port 11001 --debug
```

The turbulence engine's wind and CAPE data come from Open-Meteo's
historical-forecast API (its own quota), cached next to the replay file,
and its clock runs from the replay time. A plan's `replay_time` moves
it per leg. Everything else, such as zone weather, METARs and SIGMETs,
stays live: PSX's own weather is today's.
