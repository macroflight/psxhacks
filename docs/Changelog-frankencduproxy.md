# frankencduproxy changelog

## Unreleased

- **Change: console log times are UTC**, marked with a Z (e.g. `14:05:31Z`), like every psxhacks log.

## 1.0.1 (2026-10-03)

- The `name=`/`clientName=` messages sent to PSX now include the addon's
  version number, matching every other addon - makes it easier to tell
  what's actually running from the PSX client list.
