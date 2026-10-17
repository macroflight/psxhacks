# frankenprint changelog

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
