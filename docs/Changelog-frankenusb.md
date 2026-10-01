# frankenusb changelog

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
