# frankenpilot

frankenpilot is a configurable virtual Pilot Monitoring for PSX. It takes
over the "silent tasks" of PSX's own virtual pilot (Instructor >
Situation > Human > Pilot), which it switches off, and does them the way
a crew member would. PSX keeps doing the voice call-outs and checklists.

## When it is active

- **Shared cockpit** (connected to the master frankenrouter): on by default
  when 0 or 1 slave sims are connected, off with 2 or more. It sits in the
  pilot seat nobody is in, as assigned on the routers' seat buttons
  (`/efb`, `/utils/sims`). The router's frankenpilot page can force it on or
  off regardless of the number of sims. Run it in the master sim.
- **Local** (a plain PSX main server, or a standalone frankenrouter): on by
  default; `--enabled off` switches it off. Its seat is `--seat left|right`,
  or by default the seat of PSX's own Human Pilot (Instructor > Situation >
  Human > Pilot, frankenusb's seat button sets it too).

While active it shows its seat on the Instructor and switches off PSX's own
"Performs silent tasks" and "Sets S/C alt if VNAV PTH engaged" (the
call-outs setting is left as it is). It keeps them off while it's active:
a situation brings its own virtual pilot settings, and if those (or a
click on the Instructor) switch the silent tasks back on, frankenpilot
switches them off again, so two pilots never do the same tasks; to use
PSX's own virtual pilot, switch frankenpilot off. When it goes inactive again (switched
off on the router page, or a second slave sim joins) it puts PSX's virtual
pilot back the way it was. In a shared cockpit it waits about 12 seconds
after connecting before deciding, so that every slave sim has reported in.

Loading a situation starts a new flight: anything frankenpilot was still
doing for the previous one is dropped. A situation loaded in the air is not
a takeoff (no after-liftoff or thrust reduction flows).

## How it behaves

- Every action happens **once, when its trigger occurs** (lining up on the
  runway, liftoff, passing 10,000 ft above the airport, an EICAS message
  appearing...), after a small random delay like a person. If you change a
  switch afterwards it leaves it alone until that action's next trigger:
  turn the landing lights back on at 2,000 ft over a high airfield and they
  stay on.
- It only uses what a pilot can see: TAT, clouds out of the window (MSFS
  in-cloud data from PSX.NET.MSFS), EICAS messages, the aircraft's own
  state. It never uses frankenweather's internal weather data.

## What it does

Each action can be switched off on the router's frankenpilot page or in the
config file (`[actions]`, names in brackets).

- **Runway entry** (entering the FMC departure runway before takeoff, at
  any angle, or lined up on another runway): strobes, taxi light and all
  other lights on except the outboard landing lights, which are the pilot
  flying's after takeoff clearance (`runway_entry_lights`); autobrake RTO if
  not set (`runway_entry_rto`); transponder TA/RA if not set
  (`runway_entry_tcas`).
- **Runway crossing** (on a runway that isn't the FMC departure runway,
  not lined up): strobes on while crossing, off again afterwards
  (`crossing_strobes`).
- **After liftoff**: runway turnoff, taxi and wing lights off
  (`liftoff_lights`).
- **Thrust reduction**: packs on one at a time, a few seconds apart, if any
  was off (`packs_after_takeoff`); gear lever OFF with the gear up
  (`gear_lever_off`).
- **10,000 ft above the airport** (the departure airport on the climb, the
  destination on the descent, not 10,000 ft above sea level): landing and
  logo lights off / on (`lights_10000`).
- **5,000 ft above the destination**: autobrake 4 if none is set
  (`autobrake_5000`; level in `autobrake_setting`).
- **After runway exit** (landed, slower than 30 kt and off every runway for
  3 seconds, using PSX's runway data: turning onto another runway or
  backtracking is still on a runway; without runway data, turned more than
  40 degrees from the landing heading): flaps up and speedbrake down
  (`exit_flaps_speedbrake`); landing lights off, taxi light on (or both
  inboard landing lights if the aircraft has no taxi light), strobes off
  (`exit_lights`); autobrake OFF (`exit_autobrake`); MCP speed 200 kt (a
  safe speed for the next takeoff if the crew forgets to set V2), both F/Ds
  and the A/T arm switch off (`exit_mcp`); radio and baro minimums off on both
  EFIS control panels (`exit_minimums`); weather radar and terrain off on
  any ND where they're on (`exit_wxr_terr`); transponder from TA/RA (or TA)
  to XPDR (`exit_xpdr`); stabilizer trim to 6 units
  as the pedestal indicator shows them (`exit_stab_trim`; PSX's network
  value `StabTrim` reads about 0.3 units lower there); APU start 30 s
  later (`exit_apu`).
- **Fuel** in flight, standard Boeing 747 procedure, following the EICAS messages (`fuel`):
  center pumps off at >FUEL LOW CTR and on at >FUEL OVD CTR; stabilizer
  pumps on at >FUEL PMP STB and off at >FUEL LO STAB; AUX pumps off at
  >FUEL AUX; at >FUEL TANK/ENG override pumps 2/3 off, outer crossfeeds
  1/4 closed, main pumps on. Before engine start the fuel panel is yours.
- **Anti-ice** (`anti_ice`): at >ICING NAC / >ICING WING engine / wing
  anti-ice to ON, also an automatic system set to AUTO or OFF. Engine
  anti-ice also goes on in icing conditions, TAT 10 C or colder and enough
  cloud (what the crew sees: MSFS in-cloud and cloud density from
  PSX.NET.MSFS, the departure ATIS):
  - Auto system in flight: a few whiffs of cloud are left to AUTO (the ice
    detectors). It goes ON after 60 s in a row in cloud with 60 % of the
    last 3 minutes in cloud (more than about 3 oktas on the way), or in
    cloud through 2,000 ft of climb or descent (a deep layer), or 30 s in
    dense cloud (MSFS cloud density 0.1 or more).
  - Manual system, and fog on the ground: 30 s in a row in cloud.
  - Not in climb and cruise at SAT -40 C or colder (Boeing), and not on a
    descent where TAT will be above 10 C within a minute.
  - On the ground, once all four engines are running (never before engine
    start), also when the departure weather means icing conditions right
    after takeoff at 10 C or colder: visibility 1,600 m or less,
    precipitation, or BKN/OVC (5 oktas or more) below 3,000 ft (FEW/SCT
    alone don't count). The departure weather is its METAR in PSX, or, when
    that has no weather groups (frankenweather's "RMK FRANKENWX MODEL WX"),
    the PSX weather zone the METAR belongs to. Wing anti-ice is only switched in flight.

  Back to AUTO (or off) when TAT has been above 10 C for a minute, and in
  flight at >ANTI-ICE NAC/WING or a while after leaving cloud (longer after
  a long time in icing conditions; the time counts from liftoff, so fog
  patches on the ground don't end it). An engine failure never switches
  anti-ice off, and if you switch it off or to AUTO yourself it stays that
  way until the next trigger.
- **F/D**: your F/D switch moves, the other one follows (`fd_follow`).
- **Altimeters** (`altimeters`): frankenpilot never decides when to go to
  STD or QNH (ATC does: STD when cleared to a flight level, QNH when cleared
  to an altitude). When the pilot flying sets QNH or STD on their PFD,
  frankenpilot's PFD follows. The standby altimeter gets every QNH the pilot
  flying sets, but stays on the departure QNH until the aircraft is above
  the departure area's minimum safe altitude (the terrain heights on
  departure charts are relative to it), then goes to STD (the ISFD's STD
  button, or turned to 1013 on the older standby altimeter). On the way
  down it is set to the arrival airport's QNH (its METAR in PSX, or its PSX
  weather zone) as soon as the aircraft descends below the destination MSA,
  while the PFDs stay on STD. The MSAs come from frankenweather; without
  them the standby altimeter simply follows the pilot flying's PFD. A QNH
  preselected while in STD is not a change to QNH. frankenpilot always
  works towards what the pilot flying's PFD shows now, so flicking STD/QNH
  or turning the baro knob click by click doesn't leave it behind on a
  stale setting.
- **Minimums** (`minimums`): when the pilot flying sets their radio or baro
  minimums, or moves the MINS selector, frankenpilot's PFD follows: both
  values (the selector picks which one the knob turns), then the selector
  to where the pilot flying left theirs. Turned off goes off too. Like the
  altimeters it always works towards what the pilot flying shows now.

frankenpilot uses one pair of hands: when two flows run at the same time
their switches are moved one after the other. Switches right next to each
other (an L/R pair, the four landing lights, engine anti-ice 1-4) follow
each other within half a second; moving to a switch further away takes 1-2
seconds; the packs keep their own few seconds apart (bleed air demand).

frankenpilot knows whether the flight is online on VATSIM: online when any
slave sim is connected (as pilot or observer), from the routers' VATSIM
status (the PSX.NET.vPilot plugin) or, without a router, the plugin itself;
offline when a sim reports offline and none online (the master sim is never
on VATSIM and doesn't count). A master sim with no slave sim connected
counts as offline (it is often left to fly unattended); a sim without a
router and without the plugin's status is unknown. Its log and web page show
it.

- **Step climbs** (`step_climb`, NG FMC): when VNAV says a step climb is
  due ("STEP TO FL350 / AT NOW" on VNAV CRZ page 2/3), frankenpilot sets
  the MCP altitude to VNAV's STEP TO level and pushes the altitude knob
  once. Whether the step comes from the LEGS page or from VNAV's automatic
  steps (STEP size) doesn't matter. Only when all of this holds: level in
  cruise in VNAV PTH, STEP not 0, the MCP altitude still at the cruise
  altitude, and not online on VATSIM (online, ATC gives the step climb).
  PSX doesn't put VNAV's next step on the network (docs/psx-issues.md), so
  frankenpilot reads the page on its own side's CDU: when that CDU already
  shows VNAV CRZ 2/3 every 30 s; otherwise 5 minutes after levelling off,
  shortly before the step point VNAV shows, and every 10 minutes, each time
  pressing VNAV and then putting the page that was showing back (its key,
  then NEXT PAGE to the same page number). It never types into the
  scratchpad, leaves a CDU alone that someone used in the last minute, and
  doesn't look at all from a page it can't bring back (INIT REF pages,
  DEP/ARR, other subsystems).

- **Flap speeds** (`flap_speeds`, off by default, opt-in): when the pilot
  flying extends the flaps, frankenpilot sets the MCP speed to that flap's
  manoeuvre speed, the number the pilot reads off the PFD speed tape: the
  REF speed selected on APPROACH REF + 60 (flaps 1), 40 (5), 20 (10), 10
  (20); at the landing flap (25 or 30, as selected) REF + the wind
  correction (5 kt unless another is entered). It waits until the lever has
  rested for 2 seconds, never acts on retraction (takeoff, go-around), and
  sets nothing without a selected REF speed, with the speed window closed
  (VNAV SPD), or at flaps 25 on the way to a flaps 30 landing (no bug
  there).

## Log files and web UI

Every run writes a log file to `~/.cache/frankenpilot/logs/`
(`frankenpilot_<UTC start time>.log`, times in UTC; the 10 newest are
kept; `--log-dir` changes the directory). For each decision it says in
plain words what frankenpilot saw and why it acted (a `DECISION:` line,
e.g. the METAR or the minutes in cloud that switched anti-ice on), each
switch it moved (`ACTION:`), and with each of them the flight state
(phase, altitude, speed, TAT, SAT, cloud, engines); once a minute a
`STATE:` line. That is what to send along when frankenpilot did something
unexpected.

frankenpilot has its own web page, by default on port 9757
(`http://<sim>:9757/`, `--web-port`, 0 = none): the same state and
switches as the router's frankenpilot page, the current log, and every log
file to download.

## Running it

```text
python frankenpilot.py --psx-host 127.0.0.1 --psx-port 10748
```

In a shared cockpit start it in the master sim (start scripts:
`$StartFrankenpilot = $true`). Options: `--enabled auto|on|off`,
`--seat auto|left|right` (local mode), `--config-file FILE`,
`--web-port PORT` (default 9757, 0 = no web page), `--log-dir DIR`,
`--debug`.

Config file (TOML), all optional:

```text
[frankenpilot]
enabled = "auto"        # auto | on | off
seat = "auto"           # local mode: auto (PSX Human Pilot seat) | left | right

[actions]
exit_apu = false        # don't start the APU after runway exit

[params]
lights_agl_ft = 10000.0
autobrake_agl_ft = 5000.0
autobrake_setting = "4"   # 1, 2, 3, 4 or MAX
exit_speed_kt = 30.0      # runway exit: slower than this ...
exit_turn_deg = 40.0      # ... and turned this far from the landing heading
apu_delay_s = 30.0
stab_trim_units = 6.0     # as read on the stab trim indicator
# anti-ice: how much cloud makes icing conditions (TAT 10 C or colder)
icing_auto_cont_s = 60.0     # auto system: in cloud this long in a row ...
icing_auto_frac = 0.6        # ... and this much of the last 3 minutes
icing_auto_depth_ft = 2000.0 # auto system: or in cloud through this much climb/descent
icing_dense_cont_s = 30.0    # auto system: or this long in dense cloud ...
icing_dense = 0.1            # ... of at least this MSFS cloud density
icing_manual_cont_s = 30.0   # manual system and fog on the ground
icing_atis_base_ft = 3000.0  # departure ATIS: BKN/OVC below this
```

Airport elevations (for "10,000 ft above the airport") come from the UCAR
stations list, shared with frankenweather's cache or downloaded once.
