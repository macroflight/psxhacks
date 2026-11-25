# frankenpush

frankenpush connects PSX (through a frankenrouter or directly) to PSCC
Flight Centre: it pushes the aircraft's telemetry over Flight Centre's
`/ws/push` WebSocket and handles what Flight Centre sends back (doorbell,
crew messages, printer text, VATSIM status). See the changelog for the
details of each feature; this page so far describes PinguEye and the
formation autopilot.

## Logon code

frankenpush logs on with the sim's logon code from Flight Centre's My sim
page (`--logon-code CODE`, or `--logon-code-file FILE` to keep it out of the
process list and start scripts; the same file frankenweather's
`--logon-code-file` reads; remembered per `--portal-url` in
`~/.frankenpush_cache.json`, so a test site's code never goes to the live
Flight Centre; a code remembered before 2026-11-21 belongs to the portal
frankenpush is first started with after the update) at
`--portal-url` (default the live Flight Centre). If Flight Centre refuses
the code, the log says "Portal rejected logon code" and frankenpush retries
every 30 s. If a code that worked earlier in the same run stops working (the
sim was deleted, or its logon code renewed, on Flight Centre; the Flight
Centre test site drops every sim when it is updated), the log says "Portal NO
LONGER accepts the logon code": get the new code and restart frankenpush
with it.

## Weather for Flight Centre

frankenpush no longer uploads frankenweather's weather (since 2026-11-21):
frankenweather sends its own flight report to Flight Centre, with the same
zones and CBs and much more (its settings, every decision with its reason, a
status snapshot a minute), using the same logon code. See
[frankenweather](frankenweather.md).

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
  engaged (and not L or R), A/T mode **SPD** (THR or HOLD in FLCH), roll
  mode **HDG SEL**, pitch mode **ALT**, **V/S** or **FLCH**, bank limit
  **AUTO**, flaps up, airborne and at
  least 1,000 ft above the ground (true altitude minus PSX's terrain
  elevation under the aircraft). frankenpush never selects any of these
  itself. Until they hold it reports "standby" with what's missing (e.g.
  "NOT HDG SEL, NOT ALT, V/S OR FLCH") and starts as soon as they do.
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
  - **Altitude:** the MCP altitude, flown the way Flight Centre says
    (`command.vertical_mode`, 2026-11-15):
    - **FLCH** for a change of 1,000 ft or more and for breaking away
      downwards: PSX's best rate with the speed held by pitch, so it can't
      run away. frankenpush keeps setting the MCP speed as usual.
    - **V/S at Flight Centre's rate** (`command.vs_fpm`, 500-3,000 ft/min)
      for small changes and for following a lead that climbs or descends.
      If the speed runs away anyway (descending: IAS 10 kt above the MCP
      speed; climbing: 10 kt below it), it switches to FLCH until the speed
      has been held for 30 s, then back to V/S ("vs_limited" meanwhile).
    - **ALT HOLD** within 100 ft; PSX captures the MCP altitude.
    - Without these fields (an older Flight Centre): V/S at 1,000 ft/min as
      before. Every status while engaged reports the actual V/S, the pitch
      mode flown and whether it's limited.
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
  mode change (FLCH or V/S included), frankenpush didn't make (except PSX's own ALT capture and
  IAS/Mach changeover), or the autopilot disconnecting: it stops at once
  ("disengaged, PILOT OVERRIDE" or "AUTOPILOT DISCONNECTED") and touches
  nothing more. Engage again from the station. The log says what changed
  ("PILOT OVERRIDE: MCP HDG 109, expected 104"). A mode in PSX's fault form
  (e.g. V/S failing near a stall) stops it as "AFDS FAULT".
- **Lead too slow:** if the lead's ground speed would need an IAS below our
  minimum speed (by more than 2 kt), frankenpush won't fly formation behind
  it: a new engagement is "refused, LEAD TOO SLOW", and while steering it
  stops after 20 s of that ("disengaged, LEAD TOO SLOW"; a moment of it, e.g.
  in a turn, is fine). The log gives the speeds.
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
true track over the ground, from our positions; none below 30 kt),
`mag_var_deg` (PSX's magnetic variation here, east positive) and
`pressure_alt_ft` (the altimeter on STD; `alt_ft` is the true altitude), which Flight
Centre uses for the formation geometry and to show pilots flying by hand
the magnetic heading to set.

Every full telemetry message (the first after connecting, then periodically)
also carries `frankenpush_version`, which Flight Centre shows for the sim.

The interface (v1, `formation_version` 1) is defined by Flight Centre. The
decisions are in `fp_formation.py` (unit tests: `python3 -m unittest
fp_formation`).

## Cargo loading: Flight Centre's Cargo page loads the freighter (opt-in)

With `--allow-cargo-loading`, Flight Centre's Cargo page (for a master sim
with the Freighter variant) may load the aircraft. It plans the load and
simulates the loading pallet by pallet. After each step it sends the zero
fuel weight, the CG and the cargo doors it wants, and frankenpush writes
them to PSX:

- **ZFW and CG** (PSX's `TrueZfw` and `TrueCg`, the real CG: the
  autopilot's stabiliser trim follows it). PSX updates the EICAS gross
  weight and the FMC's ZFW itself. `TrueCg` is the CG *with the fuel on
  board*, and PSX's only CG: PSX keeps a zero fuel CG of its own, moves
  `TrueCg` as fuel goes in or out, and takes a written `TrueCg` as the
  CG at the fuel it has then. So Flight Centre sends the CG for PSX's
  actual fuel (from the fuel per tank frankenpush reports), not the
  zero fuel CG; during refuelling the CG it sends changes with the fuel.
  PSX limits `TrueCg` to 8.5-33.0 % MAC.
- **The nose, side, lower forward, lower aft and bulk cargo doors**, one
  by one. frankenpush sets PSX's door preset (the Instructor's
  CLOSE/OPEN/PARK) to OFF for this, so PSX keeps each door as set. PSX
  switches a door at once; Flight Centre simulates the travel times.
- **Entry door 1L**, the ground crew's way in: Flight Centre opens it when
  loading starts and closes it once the captain has accepted the
  loadsheet, and opens it again when unloading starts. It needs no cargo
  handling bus power.

Only on the ground, and the cargo doors only while the cargo handling bus has
power: an EXT PWR or APU GEN available but not connected to the main
buses (its button shows AVAIL, not ON). Otherwise the doors stay as they
are and the log says why. Values outside PSX's ranges are ignored.

In its telemetry frankenpush reports `cargo_loading_allowed`, the doors
(`cargo_doors`), whether the cargo handling bus has power and why not
(`cargo_power_ok`, `cargo_power_text`), the ZFW, gross weight, CG, fuel per
tank (mains 1-4, reserves 2/3, centre, stabiliser, AUX) and refuelling.
While parked, a change goes out at once (normally every 60 s then).
Without the option frankenpush ignores Flight Centre's loading messages and
reports only `"cargo_loading_allowed": false` and the four arrival fields
below.

For unloading at the destination, Flight Centre starts once the aircraft
has flown, landed and parked with the parking brake set, all engines off
and the beacon off (the ground crew doesn't come near before that). With
or without the option, frankenpush reports, and sends at once while parked
when one changes:

- `on_ground`: PSX's own on-ground state (`OnGround`).
- `parking_brake`: the parking brake lever set (`ParkBrkLev`).
- `engines_running`: 0-4, the engines whose fuel control switch is in RUN
  (`FuelCutoff1`-`4`): an engine stops counting the moment its switch goes
  to CUTOFF.
- `beacon_on`: the beacon switch in either ON position (`LtBeacon`).

Each is `null` until PSX has sent it.

PSX doesn't tip onto its tail with the CG far aft by itself. Flight Centre
simulates it, and tells the crew: it sends `flightcentre_warning`, and
frankenpush puts a red **FLIGHTCENTRE** EICAS warning on PSX (with MASTER
WARNING and the aural), the cue to look at Flight Centre's page, which
tells the rest of the story.

When the warning's `reason` is "tail tip" and the aircraft is parked (on
the ground, ground speed under 1 kt), frankenpush also lifts the nose:
it writes PSX's pitch about 20 times a second, rising smoothly to about
12 degrees (where the 747's tail meets the ground) over 3 s, holds it for
5 s, then stops. PSX pivots about the main gear (the flight deck rises
about 20 ft), and the outside view (MSFS, through PSX's boost feed)
follows smoothly. After the last write PSX lets the nose down by itself
in about 2 s, with a small dip below level, and is settled about 4 s
later. A second tail tip during the lift is ignored; the lift stops early
if the aircraft leaves the ground or starts moving. The pitch-up happens
once per warning; the aircraft doesn't stay on its tail. It stays until Flight Centre clears it; the
crew can cancel the MASTER WARNING lights as usual. frankenpush clears it
only while it is still its own text, so another add-on's free warning
(frankenrouter's ROUTER, a WorldFlight alert) is never wiped. Also only
with `--allow-cargo-loading`.

The decisions are in `fp_cargo.py` (unit tests: `python3 -m unittest
fp_cargo`).
