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
- **After runway exit** (landed, slowed down and turned off the runway):
  flaps up and speedbrake down (`exit_flaps_speedbrake`); landing lights off,
  taxi light on (or the inboard landing light if the aircraft has no taxi
  light), strobes off (`exit_lights`); weather radar and terrain off on any
  ND where they're on (`exit_wxr_terr`); stabilizer trim to 6 units
  (`exit_stab_trim`); APU start 30 s later (`exit_apu`).
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

frankenpilot uses one pair of hands: when two flows run at the same time
their switches are moved one after the other.

frankenpilot knows whether the flight is online on VATSIM: online when any
sim of the cockpit is connected (as pilot or observer), from the routers'
VATSIM status (the PSX.NET.vPilot plugin) or, without a router, the plugin
itself; offline only when every sim reports offline. Its log and web page
show it. Step climbs will only happen offline.

Not yet: step climbs (only when not online on VATSIM), MCP speed on flap
selection (opt-in).

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
stab_trim_units = 6.0
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
