# frankenpush changelog

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
