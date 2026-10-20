# frankenprint changelog

## 1.1.1 (2026-10-18)

- **Fix: a multi-page Pushover notification stalled the whole process.**
  `_send_pushover()` sleeps 3s between pages and makes blocking HTTPS
  calls, but ran synchronously inside the PSX subscribe callback - on
  the same event loop that also handles the PSX connection - so a long
  bulletin split into several pages would stop PSX message processing
  for the entire pagination delay. It now runs on a worker thread via
  `asyncio.run_in_executor()` instead (see the new `_run_in_background()`
  helper), which also logs any exception instead of letting it vanish
  silently.

## 1.1.0 (2026-10-17)

- **New feature: print a message sent as `addon=FRANKENPRINT:<sim
  name>:<text>`** (new `--sim-name` option, off by default - without
  it, `addon=` messages are ignored entirely). `<sim name>` is matched
  case-insensitively against `--sim-name`; `<text>` uses the same
  `^`-delimited line format as `PrinterText` (`Qs119`) and goes through
  the same printing/Pushover path. Used by Flight Centre's "send a
  message to the crew" feature, relayed onto the PSX network by
  frankenpush - see frankenpush's changelog and `~/to-psxhacks.md`.

## 1.0.1 (2026-10-03)

- The `name=`/`clientName=` messages sent to PSX now include the addon's
  version number, matching every other addon - makes it easier to tell
  what's actually running from the PSX client list.
