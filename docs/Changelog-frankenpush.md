# frankenpush changelog

## 1.3.0 (2026-10-17)

- **New feature: flight deck door status for Flight Centre's status
  display.** Watches `FltDkDoorTurn` (`Qh419`) for a switch movement to
  UNLOCK (`0`) or LOCK (`2`) - `1` (the spring-back to neutral) is
  ignored - and pushes `{"type": "flight_deck_door", "locked": ...}` to
  Flight Centre immediately over the existing `/ws/push` connection (same
  immediate-delivery path as `checklist_toggle`/`scratchpad_update`),
  every time, not only on a change (Flight Centre treats a LOCK arriving
  while a doorbell request is pending as a rejection of that request,
  even if the door was already locked). `locked` is a tri-state integer
  (`0`=unknown, `1`=unlocked, `2`=locked, frankenpush's own encoding, not
  a passthrough of `Qh419`'s raw wire values), also sent as
  `flight_deck_door_locked` in every regular telemetry update for resync
  after a reconnect. Also sends the master router's own simulator name
  as `master_sim_name` (delta-tracked, alongside `connected_sim_names`),
  since Flight Centre's status-display name lookup needs it and
  `connected_sim_names` deliberately excludes it.
- **New feature: "ring the flight deck doorbell" from Flight Centre's
  status display.** On a `{"type": "flight_deck_doorbell"}` message from
  Flight Centre, places a fake cabin call in PSX (`Qi102=07`, shows
  "PARTY LINE" on the cabin call display) - the crew clears it as usual
  by switching their mic selector away from CAB. Once `Qi102` is seen
  reset back to `0` (confirming the call was cleared), sends
  `{"type": "flight_deck_doorbell_resolved"}` to Flight Centre 10s later
  so its status display can reset the "ring the doorbell" button. A
  rejection (crew denies via the lock switch instead) is handled
  entirely by Flight Centre from the `flight_deck_door` event above, so
  nothing extra is sent for that case. See `~/to-psxhacks.md` for the
  full Flight Centre side of this contract.
- **New: seatbelt sign state for Flight Centre's status display.**
  Watches `Seatbelts` (`Qh421`) and, whenever the derived on/off state
  actually changes (unknown -> known counts as a change), pushes
  `{"type": "seatbelt_signs", "on": ...}` immediately, plus the current
  state as `seatbelts_on` in every regular telemetry update. `on`/
  `seatbelts_on` are `true` when `Qh421==2`, else `false`.
- **New: initial status sent right on connecting (or reconnecting) to
  Flight Centre.** The currently-known door lock and seatbelt sign
  state (possibly still "unknown"/`null` if PSX hasn't reported anything
  yet) now goes out immediately on the push WebSocket as soon as it's
  established, instead of only with the first regular telemetry update -
  which additionally doesn't happen at all until PSX has reported a
  position fix. Any outstanding doorbell request is also forgotten on
  every (re)connect, matching Flight Centre resetting its own button
  state the same way - otherwise a stale request from a previous
  connection could fire a `flight_deck_doorbell_resolved` nobody is
  tracking anymore.
- **New feature: simulated "flight deck curtain" for freighter variants.**
  The Freighter and Freighter ER variants (`CfgVariant`/`Qi0` `1`/`3` -
  confirmed live across all 5 variants: `0`=Combi, `1`=Freighter,
  `2`=Passenger, `3`=Freighter ER, `4`=Passenger ER) have no flight deck
  door lock and a limited intercom. Sends `is_freighter` (delta-tracked
  in telemetry, plus an immediate `{"type": "is_freighter", ...}` event,
  though it shouldn't change mid-flight) so Flight Centre can show a
  curtain indicator instead of the door lock status for these. The crew
  opens the curtain by pressing "U/D" on the limited intercom panel
  (`CallPanel`/`Qh413==0`, confirmed live to be a momentary pulse and
  never seen during the normal `flight_deck_doorbell` call-clearing
  sequence); frankenpush simulates it auto-closing 60s after the most
  recent press (each press restarts the countdown; there's no described
  way to close it early). Sent as `flight_deck_curtain_open` in
  telemetry and an immediate `{"type": "flight_deck_curtain", "open":
  ...}` event, following the same immediate+telemetry+initial-status
  pattern as the door lock and seatbelt signs.
- **New feature: "send a message to the crew" from Flight Centre's
  status display.** On a `{"type": "crew_message", "sim_name": ...,
  "text": ...}` message, broadcasts `addon=FRANKENPRINT:<sim
  name>:<text>` onto the PSX network (newlines in `text` become `^`,
  matching `PrinterText`'s line format) - printed by `frankenprint.py`
  1.1.0's new `--sim-name` option. Both fields are required; the
  message is dropped (logged, not queued/retried) if either is empty or
  there's no live PSX connection.

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
