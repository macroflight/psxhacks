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
- Readable log texts for the fuel and anti-ice actions; fuel pumps are
  switched one after the other.
