# frankenpush

frankenpush connects PSX (through a frankenrouter or directly) to PSCC
Flight Centre: it pushes the aircraft's telemetry over Flight Centre's
`/ws/push` WebSocket and handles what Flight Centre sends back (doorbell,
crew messages, printer text, VATSIM status). See the changelog for the
details of each feature; this page so far describes PinguEye.

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
  for a few seconds on the ground). Every `interval_s` Flight Centre asks for, never more often than
  every 2 s.
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
