# frankenweather flight reports: format

frankenweather (`fw_fcreport.py`, `Script.flight_centre_report_coro()`) sends
its flight report to PSCC Flight Centre. Agreed with Flight Centre
2026-11-21 (`~/to-psccfc.md`, `~/to-psxhacks.md`).

## Request

- `POST {portal-url}/api/fw/report`, e.g.
  `https://mkro.se/flightcentre/api/fw/report` (test site:
  `https://mkro.se/fctest/api/fw/report`).
- `Authorization: Bearer <the sim's logon code>` (the same as frankenpush's).
- `Content-Type: application/json`, `Content-Encoding: gzip`.
- Body: one batch, or (catching up from disk) a JSON array of up to 100
  batches and 1.5 MB of gzip (Flight Centre allows 500 and 2 MB).

## Batch

Every batch: `{"type", "session_id", "seq", "time", ...}`.

- `session_id`: a UUID per frankenweather run.
- `seq`: 1, 2, 3, ... per session; Flight Centre stores each
  `(sim, session_id, seq)` once, so a re-send after a lost answer is safe.
- `time`: when the batch was made (ISO UTC); a late batch keeps it.

Types:

- `session`: `fw_version`, `flight` (`dep`, `dst` from the FMC),
  `settings`: `options` (the command line) and `config` (the config-file
  settings), both through `fw_fcreport.safe_settings()`: names matching key,
  code, token, password, secret, webhook, proxy, auth, cookie, file, dir,
  path, cache, url or host are left out, and only plain values (bool,
  number, text up to 200 characters without slashes, short lists) are kept.
  Sent at the start and when the settings change (checked every minute), and
  again when the report is switched back on.
- `log`: `lines`: `[[ISO UTC, level, message], ...]`, the INFO and worse log
  lines since the last batch (at most 20,000), every 30 s.
- `snapshot`: `data`: `Script._devel_map_data()` (what `/devel/data.json`
  serves) without its `events` (the log batches carry them) and without the
  SIGMETs' raw text, every 60 s while the aircraft position is known.

## Answers

- `2xx`: done, the batches are deleted.
- `401`: the logon code was refused: kept, tried again after 30 min.
- `429`, `507`: kept, tried again after `Retry-After` seconds.
- `400`, `413`: not acceptable as they are: dropped, with a warning.
- Anything else, or no answer: kept; tried every 10 s for a minute, then
  every 5 min.

Queued batches wait in `~/.cache/frankenweather/fc_reports/` (one gzip file
each, at most 50 MB in total, the oldest dropped first), also across
restarts.
