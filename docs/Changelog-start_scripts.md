# start_scripts changelog

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
