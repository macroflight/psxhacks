# frankenpush

frankenpush connects PSX (through a frankenrouter or directly) to PSCC
Flight Centre: it pushes the aircraft's telemetry over Flight Centre's
`/ws/push` WebSocket and handles what Flight Centre sends back (doorbell,
crew messages, printer text, VATSIM status). See the changelog for the
details of each feature; this page so far describes PinguEye and the
formation autopilot.

## PinguEye: vPilot traffic for Flight Centre's radar display (opt-in)

PinguEye is Flight Centre's air-defence-radar style display. With
`--vpilot-api`, frankenpush sends it the traffic vPilot knows about around
the aircraft.

```text
frankenpush.py ... --vpilot-api ws://192.168.1.20:10781/api/v1/events
```

- **Where the traffic comes from:** the PSX.NET.vPilot plugin's event
  stream on the PC that runs vPilot (default port 10781, path
  `/api/v1/events`). frankenpush keeps the latest state of every aircraft
  from its `aircraft.added` / `aircraft.updated` / `aircraft.deleted`
  events, and our own callsign from `network.connected` / `api.status`
  (left out of what it sends). Units are converted: the plugin's heading is
  in radians and its speed is ground speed in knots (checked against the
  VATSIM data feed), altitude is true altitude in feet, pitch and bank are
  degrees.
- **When it's sent:** only while all of these hold: `--vpilot-api` is
  given; Flight Centre has asked for it on this connection
  (`pingueye_request` with `send: true`, which Flight Centre sends while
  someone has PinguEye open); and the aircraft has been airborne for 10 s
  (the "radar" is on; the delay ignores PSX's on-ground flag flickering
  for a few seconds on the ground). If PinguEye's operator switches the
  radar on while the aircraft is on the ground (`on_ground: true` in the
  request), it's sent on the ground too, at once. Every `interval_s`
  Flight Centre asks for, never more often than every 2 s.
- **What's sent:** `pingueye_traffic`, a complete snapshot each time:
  every aircraft vPilot knows (no radar filtering; Flight Centre does that),
  nearest first, at most `max_aircraft`, with callsign, type, position,
  altitude (true and pressure), ground speed, heading, pitch, bank and the
  age of the position. Aircraft vPilot hasn't updated for 60 s are left out.
  When sending stops (landed, Flight Centre says stop), one last message with
  `radar_on: false` and no aircraft.
- **Log:** "Flight Centre: PinguEye wants traffic", "PinguEye: sending vPilot
  traffic", "PinguEye: stopped sending traffic (on the ground)", and the
  connection to the vPilot plugin.

The interface itself (v1, `pingueye_version` 1) is defined by Flight Centre.

## Formation autopilot: PinguEye flies the MCP into formation (opt-in)

PinguEye's airborne station guides our aircraft into formation with another
one (in trail, or off its left or right wing, 0.2-2 nm). With its formation
autopilot engaged, Flight Centre sends the heading, ground speed and
altitude to fly about every 3 seconds (`formation_steer`), and with
`--formation-autopilot` frankenpush flies them with PSX's MCP. Without the
option it answers "refused, NOT ENABLED" and never touches the MCP.

```text
frankenpush.py ... --formation-autopilot
```

- **Only when the crew has set it up:** the centre autopilot **CMD C**
  engaged (and not L or R), A/T mode **SPD**, roll mode **HDG SEL**, pitch
  mode **ALT** or **V/S**, bank limit **AUTO**, flaps up, airborne and at
  least 1,000 ft above the ground. frankenpush never selects any of these
  itself. Until they hold it reports "standby" with what's missing (e.g.
  "NOT HDG SEL, NOT ALT OR V/S") and starts as soon as they do.
- **What it turns:** only the MCP knobs, the way a pilot would.
  - **Heading:** the commanded track, minus the drift (measured from the
    track over the ground with the wings level) and the magnetic variation
    (unless the heading reference is TRUE). It changes by 1 degree or more.
  - **Speed:** the commanded ground speed turned into IAS or Mach (the MCP's
    IAS/MACH mode as it is) from our own TAS and ground speed. Never more
    than 330 kt IAS or Mach 0.85 (whichever is lower), VMO/MMO minus a
    margin; changes of 2 kt / 0.005 Mach or more (a window outside the
    limits is always corrected).
  - **Minimum speed** for the weight and altitude (PSX doesn't expose its
    amber band): the highest of 240 kt, VREF30 + 80 kt and the 1.3 g
    buffet-onset speed, plus 5 kt. From PSX's EICAS gross weight; a rough
    747-400 model that errs high (e.g. 350 t: 253 kt low down, about
    270 kt at FL350).
  - **Altitude:** the MCP altitude; a new altitude is flown in **V/S at
    1,000 ft/min** (frankenpush presses V/S and sets the V/S wheel) and PSX
    captures it in ALT. frankenpush presses ALT HOLD only if V/S is still on
    within 100 ft of it.
  - **Maximum altitude:** never above the altitude where the weight still
    allows a 1,000 ft/min climb (roughly the 747-400 maximum altitude chart
    minus 2,000 ft: about FL323 at 315 t, FL305 at 350 t). Above it already,
    it holds the altitude instead of climbing.
  - **Climb protection:** V/S doesn't protect the speed. In a V/S climb with
    the speed more than 10 kt below the MCP speed it presses ALT HOLD and
    doesn't climb again for a minute, and not until the speed is back. (On
    the first live test a heavy aircraft left climbing in V/S at FL349
    stalled and crashed.)
- **Pilot override:** any MCP heading, speed, altitude or V/S change, or
  mode change, frankenpush didn't make (except PSX's own ALT capture and
  IAS/Mach changeover), or the autopilot disconnecting: it stops at once
  ("disengaged, PILOT OVERRIDE" or "AUTOPILOT DISCONNECTED") and touches
  nothing more. Engage again from the station. The log says what changed
  ("PILOT OVERRIDE: MCP HDG 109, expected 104"). A mode in PSX's fault form
  (e.g. V/S failing near a stall) stops it as "AFDS FAULT".
- **No guidance:** no command for 10 s, or the connection to Flight Centre
  lost: it stops, the MCP stays as it is, and PSX's printer says
  "FORMATION AUTOPILOT / DISENGAGED hhmmZ / NO GUIDANCE / MCP LEFT AS IT
  WAS". A new engagement needs Flight Centre to have gone quiet for 6 s
  first (it stops sending when it hears "disengaged").
- **Reports:** `formation_status` (engaged / standby / disengaged / refused,
  with the reason) on every change and with every 5th command, with the
  limits in use: `min_ias_kt`, `max_ias_kt`, `speed_limited` ("min"/"max"
  while the commanded speed is clamped), `max_alt_ft` and `altitude_limited`
  ("max" while the commanded altitude is above it). The log has
  a line for every state change and for every command that turns a knob
  ("Formation autopilot (seq 105): Hdg +15, Alt +10, press Vs, V/S +1000").
- **Not for close formation:** Flight Centre's positions are a few seconds
  old (vPilot) or older (the VATSIM data feed).

The telemetry also carries `gs_kt` (PSX's ground speed), `trk_deg` (the
true track over the ground, from our positions; none below 30 kt) and
`mag_var_deg` (PSX's magnetic variation here, east positive), which Flight
Centre uses for the formation geometry and to show pilots flying by hand
the magnetic heading to set.

The interface (v1, `formation_version` 1) is defined by Flight Centre. The
decisions are in `fp_formation.py` (unit tests: `python3 -m unittest
fp_formation`).
