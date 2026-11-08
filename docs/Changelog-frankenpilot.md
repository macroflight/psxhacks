# frankenpilot changelog

## Unreleased

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
