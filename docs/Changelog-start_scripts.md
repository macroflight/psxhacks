# start_scripts changelog

## 1.4.0 (2026-10-03)

- **New addon: PSXVibrate (PSX.NET.Vibrate)**, added to the
  `start_<x>.ps1`/`stop_<x>.ps1`/`restart_<x>.ps1` framework and started
  from `startsim_slave.ps1` (opt-in, `$StartPsxVibrate`/`$PsxVibrateDir`,
  both unset by default - see `psxhacks-start-override-EXAMPLE.ps1`).
  `start_psxvibrate.ps1` rewrites `PSXServerIP`/`PSXPort` in its config to
  point at the slave sim's router, same as `start_psxsounds.ps1`.
  `stopsim_slave.ps1` now stops it via the new `stop_psxvibrate.ps1`
  instead of its previous bare `KillProcess "PSXVibrate"` call.

## 1.3.1 (2026-10-03)

- **Fix: PSX.NET.MSFS.Client and PSXSounds windows sometimes not
  positioned on sim startup.** Both were slow enough to create their
  window that the normal positioning retry window (5s) could pass
  before it appeared, so positioning silently gave up - running
  `apply_window_positions.ps1` again later (once the window existed)
  would then move it correctly. Both now get a `Delay 5` before
  positioning, the same fix previously applied to PSX.NET.VATSIM and
  SimObjectRouter. `start_psx_net_msfs_client.ps1` also switches from a
  blocking `&` launch to `Start-Process`, since it launches a separate
  GUI .exe (not a console-hosted Python addon) - the blocking launch
  meant positioning ran *before the process had even started*, not
  just before its window appeared.

## 1.3.0 (2026-10-03)

- **New addon: FrankenControl**, added to the `start_<x>.ps1`/
  `stop_<x>.ps1`/`restart_<x>.ps1` framework like every other master-sim
  addon, with one difference - `$StartFrankencontrol` defaults to `$true`
  in `common.ps1` instead of `$false`, since it's meant to be always
  running rather than opt-in; set it to `$false` in the override file to
  opt out. Started from `startsim_master.ps1` and stopped from
  `stopsim_master.ps1`, same as the other master-sim addons.

## 1.2.0 (2026-10-03)

- **New: every addon now has a `start_<addon>.ps1` and `stop_<addon>.ps1`,
  not just `restart_<addon>.ps1`.** Each `restart_<addon>.ps1` is now a
  thin wrapper that dot-sources `stop_<addon>.ps1` then
  `start_<addon>.ps1` - no behavior change for existing callers, but stop
  and start are now independently usable (e.g. by a future addon-control
  tool that wants to stop a service without immediately restarting it, or
  start one that isn't running without first trying to kill it).
  `stopsim_master.ps1`/`stopsim_slave.ps1` now call the new
  `stop_<addon>.ps1` scripts instead of duplicating the same
  `KillProcess`/`KillPythonScript`/`KillJavaJar` calls inline.
- **Window positioning moved into each `start_<addon>.ps1`**, called right
  after that addon's process is launched, instead of from
  `startsim_master.ps1`/`startsim_slave.ps1` after each `Start-Process`.
  `Invoke-WindowPosition` itself moved from a function duplicated in both
  `startsim_*.ps1` files into the shared `functions.ps1`. This means
  positioning now happens consistently no matter what started the addon,
  not just when `startsim_*.ps1` did it. Two addons (`PSX.NET.VATSIM`,
  `SimObjectRouter`) keep their extra startup delay before positioning,
  now inside their own `start_*.ps1` instead of at the call site.

## 1.1.1 (2026-10-01)

- **Bug fix: "Use standard match" in `configure_window_positions.ps1` didn't
  actually make future window-title changes future-proof.** It saved the
  literal title text of whichever window matched the known-title pattern at
  config time, instead of the pattern itself -- so it broke exactly like a
  manual pick the next time the addon's window title changed (e.g. a
  version-number bump), despite the whole point of offering a regex-based
  shortcut being to survive that. Now saves the pattern (with a new
  `TitleIsRegex` flag) when the shortcut is used, and
  `apply_window_positions.ps1` matches via regex (`-match`) instead of the
  usual exact/starts-with/contains-anywhere chain when that flag is set.

## 1.1.0 (2026-09-13)

- **Bug fix: `configure_window_positions.ps1` now automatically removes
  saved window positions for addons it no longer manages**, checked once
  at startup. A leftover entry left in place after an addon is removed or
  renamed isn't just clutter: `apply_window_positions.ps1` falls back to
  a "contains anywhere" title match when nothing matches exactly, so a
  stale entry with a short/generic `Title` can silently grab some other,
  completely unrelated addon's window. Confirmed live: a leftover
  `PSX.NET` entry (`Title = 'PSX.NET'`, left over from before
  `PSX.NET.Orchestration` replaced it) was matching BACARS's window -
  since BACARS is now part of the PSX.NET suite - and repositioning/
  minimizing it, silently overriding BACARS's own correct "do not
  position" entry.

- **New feature: "Use standard match" shortcut when picking a window** in
  `configure_window_positions.ps1`. Addons with a well-known window title
  (a new `$KnownWindowTitlePatterns` table in `common.ps1`, seeded for
  every addon with a confirmed title substring) now get a one-key shortcut
  pinned at the top of the picker whenever that pattern currently
  identifies exactly one visible window, instead of having to search for
  it by hand every time. Falls back to the normal search-and-pick flow
  whenever the pattern matches zero or more than one window (e.g. running
  both SRSL-PSX master and slave at once, which share the same title).

## 1.0.0 (2026-09-12)

- **New feature: start_scripts now has its own version number**, printed
  by `startsim_master.ps1`/`startsim_slave.ps1`/`startsim_norouter.ps1`
  when they start.

- **New feature: start/stop a sim without any frankenrouter at all**
  (`startsim_norouter.ps1`/`stopsim_norouter.ps1`) - a bare PSX main
  server plus its main client(s), mainly intended for development: an
  easy way to rule the router in or out when tracking down a suspected
  bug.

- **Removed support for deprecated PSX.NET addons**: `PSX.NET`,
  `PSX.NET.GroundCrew`, and `PSX.NET.WeatherRadar` have all been replaced
  by `PSX.NET.Orchestration` (itself renamed from
  `PSX.NET.GroundHandling`). The old `restart_psx_net*.ps1` scripts and
  their `$StartPsxNet*` config variables are gone.

- **Removed the interactive pause before starting `PSX.NET.MSFS.Client`**
  (`$StopBeforeMsfsStart`) - every addon is now safe to start before
  MSFS, so the confirmation prompt was just unnecessary friction.

- **New feature: mark an addon as "do not position"** in
  `configure_window_positions.ps1`, for apps (e.g. BACARS) that don't
  like being moved/resized and already have their own "start minimized"
  option. `apply_window_positions.ps1` now treats this as an informational
  skip rather than an error.

- **PSXSounds' config file has moved** - `restart_psxsounds.ps1` now
  reads/writes `Config.xml` in the PSX.NET config directory instead of
  PSXSounds' own directory (same filename, new location).

- Removed leftover `frankenmsfsbridge` references from `common.ps1` -
  the addon itself was removed since PSX.NET.MSFS.Client now provides
  the same MSFS-sync data.
