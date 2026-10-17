# frankenpush changelog

## 1.2.0 (2026-10-16)

- **New feature: print the WorldFlight passenger manifest** (same
  `--worldflight-shop-alerts` opt-in, no new CLI option). Once an FMC
  route matching a WorldFlight leg is loaded, the aircraft is stationary,
  and the final boarding door (L1 or L2) has been open and then closed
  again, fetches the seat/name list from flightofthepenguins.se and
  prints it on the AIDS printer - header, a `"<N> OF 76 SEATS SOLD"`
  line, then one line per sold seat, or `"NO PASSENGERS"` if nobody
  bought a ticket for that leg. Printed once per route load; re-arms when
  the FMC route is cleared.
- **Bug fix: the manifest could print as soon as the route loaded, before
  boarding had even started** - the readiness check only required L1/L2
  to be *currently* closed, which looks identical to "closed because
  boarding hasn't started yet" and "closed because boarding just
  finished." Now also requires having actually observed L1 and/or L2
  open at some point since the route was loaded before a closed reading
  counts as "ready."
- `DoorOpenBits` (`Qi180`) bit mapping (L1=bit3, L2=bit5) confirmed live
  against a real PSX session, door by door.

## 1.1.0 (2026-10-03)

- **New feature: NOOT! shop purchase alerts** (opt-in,
  `--worldflight-shop-alerts`, interval configurable with
  `--worldflight-shop-check-interval`, default 60s). Polls the WorldFlight
  musical web shop's sales API; on startup it remembers the highest
  purchase id already seen so purchases made while the sim was offline
  are ignored. Each new purchase is alerted one at a time: sends a
  master warning (`Qs418`/`FreeMsgW`, e.g. `(MERCH: 41)` or
  `(TICKET: 178)`), then waits for either side's master warning/caution
  reset switch (`MastWarnCp`/`MastWarnFo`, `Qh114`/`Qh115`) to be
  pressed before clearing it.
- The `name=`/`clientName=` messages sent to PSX now include the addon's
  version number, matching every other addon - makes it easier to tell
  what's actually running from the PSX client list.
