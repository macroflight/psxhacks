# frankenpilot changelog

## Unreleased

- **Change: console log times are UTC**, marked with a Z (e.g. `14:05:31Z`), like every psxhacks log.

- **New add-on: frankenpilot**, a configurable virtual Pilot Monitoring that
  replaces the silent tasks of PSX's own virtual pilot (PSX keeps the
  call-outs). See docs/frankenpilot.md.
- Tested on the headless test sim with PSX's Basic situations: runway entry
  (also on runways with a displaced threshold), liftoff, thrust reduction,
  10,000 ft lights both ways, autobrake at 5,000 ft, landing, runway exit
  flow with stab trim and APU, F/D follow, the router page (master and
  slave), shared-cockpit auto on/off, fuel (>FUEL LOW CTR on a step climb),
  anti-ice (in cloud at TAT -1 C on, off 3.5 min after leaving cloud, on
  again at >ICING NAC). Not yet tested on the sim: runway crossing strobes.
- Keeps PSX's silent tasks off while active: a situation brings its own
  virtual pilot setting (every PSX Basic situation switches them on), so
  after loading one PSX's virtual pilot and frankenpilot did the same tasks.
- Anti-ice on the ground only once all four engines are running (seen on
  the real sim: engine anti-ice went ON in fog before engine start).
- Anti-ice switched on stays on through ground ops and takeoff (also with an
  engine failure): on the ground only TAT above 10 C ends icing conditions,
  the clear-of-cloud time starts at liftoff, >ANTI-ICE NAC only in flight.
- Anti-ice needs enough cloud: an auto system in flight is left in AUTO for a
  few whiffs (60 s in cloud and 60 % of the last 3 min, 2,000 ft of cloud,
  or 30 s of dense cloud), a manual system 30 s; no anti-ice in climb and
  cruise at SAT -40 C or colder or on a descent about to reach TAT 10 C.
  Tested on the headless sim (auto system, faked MSFS cloud).
- Anti-ice from the departure ATIS after engine start (fog/low visibility,
  precipitation, BKN/OVC below 3,000 ft at 10 C or colder). Tested on the
  headless sim.
- Log file per run in ~/.cache/frankenpilot/logs (10 kept) with a readable
  DECISION line for every decision (what was seen, why) and the flight
  state with every action.
- Own web page (port 9757): state and switches like the router page, the
  current log and the log files to download.
- One pair of hands: flows running at the same time move their switches one
  after the other.
- Stab trim after landing sometimes chased across 6 units and gave up (when
  it chose to overshoot first, like a pilot).
- New action `altimeters`: frankenpilot's PFD follows the pilot flying's QNH
  and STD; the standby altimeter follows each QNH, goes to STD only above
  the departure MSA and to the arrival QNH below the destination MSA (MSAs
  from frankenweather; without them it follows the pilot flying's PFD).
  Tested on the headless sim (PFD follow, standby with and without the
  departure MSA); the climb above and descent below an MSA not yet.
- Switch timing like a hand on the panel: neighbouring switches within half
  a second, further ones 1-2 s apart (packs unchanged).
- Runway entry flow as soon as the aircraft enters the FMC departure runway
  (any angle), not only when lined up; any other runway is a crossing
  (strobes only) unless you line up on it. Seen on the real sim (16R at
  YSSY was taken for a crossing while turning onto it); tested on the
  headless sim.
- Departure anti-ice check uses the departure's PSX zone weather when its
  METAR has no weather groups (frankenweather writes "RMK FRANKENWX MODEL
  WX"), seen on the real sim; tested on the headless sim.
- New action `flap_speeds` (opt-in): on flap extension the MCP speed goes to
  the flap manoeuvre speed from the selected REF (FmcAppRef) + 60/40/20/10,
  the landing flap REF + wind correction (ApproachNg), like the PFD flap
  bugs (checked against PSX's PFD for flaps 30 and 25 landings). Tested on
  the headless sim.
- New action `step_climb` (NG FMC): at VNAV's "STEP TO FLxxx / AT NOW",
  MCP altitude to that level and the altitude knob pushed, only level in
  VNAV PTH cruise, STEP not 0, MCP altitude at cruise altitude and not
  online on VATSIM. Reads VNAV CRZ 2/3 on its own side's CDU and puts the
  page back (the step isn't on the network). Tested on the headless sim
  (NG FMC): read via page switch and back to LEGS, read without switching,
  climb FL310 -> FL350, no climb while the VATSIM status was unknown.
- The master sim's VATSIM status is ignored (never on VATSIM); offline when
  a slave sim reports offline and none online.
- Knows whether the flight is online on VATSIM (any sim connected, as pilot
  or observer, per the routers' VATSIM status or the vPilot plugin); in the
  log and on its web page, for step climbs later.
- Readable log texts for the fuel and anti-ice actions; fuel pumps are
  switched one after the other.
- The thrust reduction decision says in plain words why the packs and the
  gear lever were (or weren't) moved.
- A config file that isn't valid TOML gives one readable error line (the
  file, the position and the offending line, e.g. `flap_speeds = True`)
  instead of a traceback.
- Runway exit only once the aircraft is off every runway for 3 s (PSX's
  runway data); turning onto another runway or backtracking is still on a
  runway (seen on the real sim at SCCI: the flow started while still on
  runway 07). Without runway data the old turn rule.
- After-landing flow: both inboard landing lights as taxi light when there
  is no taxi light (only the left came on), autobrake OFF, MCP speed 200 kt
  (a safe speed for the next takeoff), both F/Ds and the A/T arm switch off, radio and
  baro minimums off on both sides (new actions `exit_autobrake`,
  `exit_mcp`, `exit_minimums`). Tested on the headless sim.
- Altimeters: a QNH preselected while in STD (PFD mode 3) was taken for QNH
  and moved our PFD and the standby altimeter to QNH (seen on the real
  sim); and one sync task now follows the pilot flying's current setting,
  so quick STD/QNH flicks or clicking the baro knob no longer leave a queue
  of stale presses. Tested on the headless sim.
- Fuel paths tested on the headless sim: >FUEL PMP STB (with the
  stabilizer tank configured), >FUEL TANK/ENG; anti-ice with an auto
  system and the SAT -40 C rule. >FUEL LO STAB and >ICING WING not yet seen
  (PSX shows no >ICING WING even with heavy wing ice).
- Stab trim after landing goes to 6.0 as the pedestal indicator shows it
  (it went to StabTrim 6.1, which the indicator shows as 6.4, seen on the
  real sim). Measured on the headless sim: indicator = 0.925 x StabTrim/1000
  plus 0.71; after a test landing the indicator read 5.93. The log shows
  both.
- The runway exit decision names the last runway the aircraft was on (it
  tracks the runway through the whole rollout; it said "?" when the
  aircraft was already turning off below 30 kt). Stab trim stops within
  about 0.07 units of 6.0 on the indicator (it stopped at 6.1).
- Runway exit: our own F/D is switched off first, so F/D follow no longer
  switches it a second time (a duplicate log line).
- Fix: frankenpilot's own requests for PSX's DEMAND variables (EngParam,
  MiscFltData, GroundSpeed) never worked: they were sent before PSX's
  Lexicon arrived, as names, which PSX ignores. It only got them while
  another client (frankenweather) asked; without one it saw "not all
  engines running" with the engines at idle and skipped the
  departure-weather anti-ice check (headless test). Now asked at load3, the
  end of every welcome and situation load.
- Fix: no rules run while a situation is being loaded (between load1 and
  load3). The new situation's values arrived while the old flight's state
  was still there: loading an approach after a cruise gave "descending
  through 1150 ft = 10000 ft above the destination" and switched the
  landing lights on (headless test).
- A master sim with no slave sim connected counts as not on VATSIM (the
  master sim is never connected itself), so an unattended master sim does
  its step climbs. Before, the status stayed "unknown" and no step climb
  was done.
