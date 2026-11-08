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
- Readable log texts for the fuel and anti-ice actions; fuel pumps are
  switched one after the other.
