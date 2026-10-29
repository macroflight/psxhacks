# frankenusb changelog

## 1.4.3 (2026-10-29)

- **Fix: thrust lever handling depended on the config listing `Tla`.**
  The autothrottle and thrust/reverse lever code reads the thrust lever
  angles (`Tla`), but frankenusb only subscribed to it when a config
  action named it, so with other configs the reads came back empty and
  failed. It's now always subscribed.

## 1.4.2 (2026-10-18)

- **Fix: the 3 call sites that fire a flight-phase setup or
  centre-ailerons-and-tiller task
  (`asyncio.create_task(self.flight_phase_setup(...))`/
  `self.centre_ailerons_and_tiller()`) never kept a reference to the
  resulting Task** - an exception inside one would be silently dropped
  until (and unless) the Task was garbage-collected. Routed through a
  new `_track_task()` helper instead, which keeps the Task alive in
  `self._fire_and_forget_tasks` and logs any exception via a
  done-callback.

## 1.4.1 (2026-10-18)

- **Fix: shutting down on a PSX `exit`/`pleaseBeSoKindAndQuit` blocked
  the event loop for a full second.** The shutdown handler used
  `time.sleep(1.0)` to give the outgoing `exit` message time to reach
  the socket before the process terminates, which stalled everything
  else running on the loop for that second too. Now scheduled via
  `loop.call_later(1.0, ...)` instead - asyncio deliberately re-raises
  `SystemExit`/`KeyboardInterrupt` out of a scheduled callback rather
  than swallowing them, so the process still terminates the same way,
  just without blocking in the meantime.

## 1.4.0 (2026-10-16)

- **New: a `THROTTLE_WITH_REVERSE_BUTTON` axis's tracked reverse state
  now re-syncs from PSX's real `Tla` on every (re)connect** -- including
  a situ load, which looks identical to a disconnect/reconnect from
  frankenusb's own PSX client. Previously, frankenusb's own
  `axis_reverse_mode` tracking always restarted at `'normal'`, so if the
  real reversers were already deployed (frankenusb restarted mid-flight,
  a different aircraft/sim, or a freshly loaded situation), the next
  button press was interpreted relative to the wrong starting point.
  Classifies the real commanded `Tla` against each axis's own configured
  idle/reverse-idle/reverse-full values, correctly distinguishing
  `STEPPED_REVERSE_TOGGLE`'s four-state model from
  `REVERSE_LEVER_UP`/`REVERSE_LEVER_DOWN`/`REVERSE_TOGGLE`'s two-state
  one.

## 1.3.0 (2026-10-13)

- **New: a `NORMAL` axis mapped to `Brakes` now enforces a safe 0-1000
  range.** Prompted by a real-world "turbo takeoff" incident where a
  miscalibrated brake-pedal axis sent PSX a negative `Brakes` (`Qs357`)
  value; PSX's own physics model turns that into unbounded extra forward
  thrust instead of rejecting or clamping it (see PAIB/2026/016).
  `psx min`/`psx max` now default to 0/1000 when left unset on such an
  axis, and frankenusb refuses to start if a config explicitly sets
  either to anything else, naming the offending joystick and axis.

## 1.2.2 (2026-10-12)

- **Bug fix: a backtrace in `psx_axis_sender()` could prevent startup.**
  `self.psx.get(variable)` can return `None` (e.g. right after
  (re)connecting, or if PSX drops mid-flight -- the same race already
  handled by `autothrottle_active()`'s own `None` check), but the code
  immediately called `.split(';')` on the result without checking for
  that first, raising `AttributeError: 'NoneType' object has no
  attribute 'split'`. Now skips that variable for this loop iteration
  and retries on the next one instead of crashing.
- **Bug fix: a flight-phase button/trigger (e.g. "after landing") could
  freeze every other button press and axis motion for several
  seconds.** `handle_pygame_events()` is the sole, serial consumer of
  the joystick event queue, awaiting each handler to completion before
  pulling the next; `flight_phase_setup()`'s `EXITED_RUNWAY` phase
  contains two `await asyncio.sleep(2.0)` calls for its APU-start
  sequence, and was being awaited directly from that same loop, so
  every other input queued up untouched for 4+ seconds. The two call
  sites (`ACTION_FLIGHT_PHASE_TRIGGER`/`ACTION_FLIGHT_PHASE_BUTTON`) now
  launch it via `asyncio.create_task()` instead, matching the pattern
  already used elsewhere (e.g. the groundspeed-triggered tiller
  auto-disable).
- **Change: removed the "TILLER ACTIVE" EICAS message** -- it doesn't
  work well in a shared cockpit (traced to a case where a frankenusb
  instance hung with tiller mode on, leaving the message stuck,
  surviving even a full situ save/reload since it got baked into the
  saved situ file itself).
- **New: explicit `TILLER_TOGGLE` `set: on`/`set: off` button-config
  options**, alongside the existing toggle-only behavior, for a config
  that wants a dedicated "tiller on" and a dedicated "tiller off"
  button instead of one that flips state.

## 1.2.1 (2026-10-01)

- **Bug fix: the PSX "Human Pilot" seat (`PnfMode`/`Qi217` bit 0) was being
  set to the same seat as the real/sim pilot, instead of the opposite
  one.** "Human Pilot" is PSX's own automation that acts as a human pilot
  in whichever seat is otherwise empty -- it must always be in the seat the
  real pilot is *not* sitting in. The bit 0 <-> seat mapping confirmed live
  in 1.1.0/1.2.0 (bit0=1 -> "Human pilot: LEFT" on the Instructor Station)
  was correct; only the direction `_set_psx_human_pilot_seat()` chose it
  for was backwards. Selecting RIGHT now sets bit 0 (Human Pilot -> LEFT);
  selecting LEFT now clears it (Human Pilot -> RIGHT).

## 1.2.0 (2026-10-01)

- **New feature: on (re)connect, infer the real current seat from PSX and
  sync frankenusb's own seat-dependent state to match**, instead of always
  assuming the left seat. Restarting frankenusb after the sim was already
  running and seated in the right seat (or after `SEAT_SELECT` had
  previously toggled it) left `self.right_seat`, the PSX Human Pilot seat
  bit, and the PSX.NET.VATSIM ACP selection all silently out of sync with
  reality until the next manual seat-select press. Only engages when a
  `SEAT_SELECT` button with `seat: TOGGLE` is configured (a fixed
  `seat: RIGHT`/`LEFT` button is unambiguous and needs no inference): reads
  PSX's current `layout` value against that button's `layout left right`
  pair to determine the seat (falling back to left if it matches neither),
  then brings `select frankenusb left right swap`, `select psxnetvatsim
  acp`, and `select psx human pilot seat` in line with it, honoring each
  flag's own on/off setting exactly as a normal button press would.

## 1.1.0 (2026-10-01)

- **Bug fix: `SEAT_SELECT`'s "select psx human pilot seat" handling could
  silently clobber unrelated PSX Human Pilot settings.** It blindly
  overwrote the entire `PnfMode` (`Qi217`) bitmask with a hardcoded value on
  every button press, resetting bits that have nothing to do with seat
  selection -- callouts, silent tasks, and step climbs. In a long
  shared-cockpit flight, this meant a deliberately-configured Human Pilot
  setting could silently reappear after any routine seat-select press. Now
  reads the current value first and only changes bit 0, the single bit this
  feature actually owns (`1` = left seat, `0` = right seat), preserving
  everything else untouched. Verified live against a real PSX instance and
  the Instructor Station display: `Qi217=13` (bit 0 set) shows "Human
  pilot: LEFT"; `Qi217=12` (bit 0 clear) shows "Human pilot: RIGHT", with
  callouts/silent tasks/S-C unaffected in both directions. Also found along
  the way: `router/frankenrouter/variables.py`'s `PNF_MODE_BITS` doc
  (masks 1/2/4/16/256) doesn't match reality -- `Qi217`'s real range is
  0-15 (confirmed via `~/Variables.txt`: `Min=0; Max=15`), so masks 16 and
  256 can't exist; not fixed here since it wasn't needed for this bug.
