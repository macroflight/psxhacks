# start_scripts changelog

## 1.9.0 (2026-10-14)

- **New addon: FrankenBoostProxy** (resurrected from the old `frankenboostproxy.py`
  with its elevation-offset feature dropped and 5 Hz acceleration/position
  telemetry added instead), added to the `start_<x>.ps1`/`stop_<x>.ps1`/
  `restart_<x>.ps1` framework and started from `startsim_slave.ps1` and
  `startsim_norouter.ps1` (opt-in, `$StartFrankenboostproxy`/
  `$FrankenboostproxyOptions`/`$FrankenboostproxyRepo`, all unset/`$false`
  by default - see `psxhacks-start-override-EXAMPLE.ps1`).
  `restart_frankenboostproxy.ps1` points it at the slave sim's router via
  `--psx-port-override`, same as every other `franken*.py` addon.

## 1.8.1 (2026-10-13)

- **Bug fix: the 1.8.0 checkout-integrity preflight check (`Test-PsxhacksManifest`)
  misreported every file in a subdirectory as `(added)`, even right after a
  clean `psxhacks-updater` sync.** `psxhacks-updater`'s manifest stores file
  paths as POSIX-style (forward-slash) relative paths so it stays portable
  across OSes, but the live-tree scan here was comparing against
  `$_.FullName`-derived paths, which use backslashes on Windows -- so no
  file below the top level could ever match, even when nothing had
  changed. Now normalizes to forward slashes before comparing.

## 1.8.0 (2026-10-12)

- **New: checkout-integrity preflight check.** On startup (not on
  stop/restart -- blocking a shutdown over unrelated local file drift
  would be the wrong failure mode), `common.ps1` now re-hashes the live
  psxhacks tree against the manifest the separate `psxhacks-updater.exe`
  tool wrote after its last successful sync (new `Test-PsxhacksManifest`
  in `functions.ps1`), and refuses to start with a clear error listing
  every drifted file (missing/modified/added) if it doesn't match.
  Skipped entirely for a checkout never managed by that tool (a plain
  git clone remains fully supported). Cached via an environment variable
  for the lifetime of one sim-startup process tree, so the SHA256
  tree-walk only runs once even though `common.ps1` is dot-sourced fresh
  by every `start_<addon>.ps1` spawned as its own process.
  `setup_python_venv.ps1` also now prints a machine-parsable
  `VENV_PYTHON=...` marker line, for the same external tool to drive
  venv setup itself.
- **Bug fix: `stop_psxvibrate.ps1` (and the second `KillProcess` call in
  `stopsim_norouter.ps1`) could not actually stop PSX.NET.Vibrate** --
  they killed a process named `PSXVibrate`, which has never been this
  addon's real process name (`PSX.NET.Vibrate`). Renamed throughout to
  match the other `PSX.NET.*` addons' naming convention:
  `start_psxvibrate.ps1`/`restart_psxvibrate.ps1`/`stop_psxvibrate.ps1`
  are now `start_psx_net_vibrate.ps1`/`restart_psx_net_vibrate.ps1`/
  `stop_psx_net_vibrate.ps1`, and the `$StartPsxVibrate`/`$PsxVibrateDir`
  override-file variables and the `Invoke-WindowPosition`/
  `$SimAddonNames` key are now `$StartPsxNetVibrate`/`$PsxNetVibrateDir`/
  `"PSX.NET.Vibrate"`. Breaking change for any override file or saved
  window position still using the old names.
- **Bug fix: `sync_clock.ps1`'s elevated resync could still fail with
  "the service has not been started."** `w32tm /resync /force` assumes
  the Windows Time service is already running, which it often isn't on
  a non-domain-joined machine (`w32time`'s startup type is commonly
  Manual/Trigger Start) -- now runs `Start-Service w32time` first, inside
  the same elevated context, before calling `w32tm`.
- **Removed: FrankenIdent support** (`start_frankenident.ps1`/
  `restart_frankenident.ps1`/`stop_frankenident.ps1` and their wiring in
  `common.ps1`/`startsim_slave.ps1`/`startsim_norouter.ps1`/
  `stopsim_slave.ps1`/`stopsim_norouter.ps1`). Almost every addon now
  sends its own name to the router directly, so the separate
  `frankenrouter_ident.py` identification addon is no longer needed.
- **New: start/stop/restart scripts for CPFlight's FS_COM MCP/EFIS
  driver** (`start_cpflight_fscom.ps1`/`restart_cpflight_fscom.ps1`/
  `stop_cpflight_fscom.ps1`), wired into `startsim_slave.ps1` and the
  override-file example the same way as other optional addons.

## 1.7.0 (2026-10-09)

- **New: `sync_clock.ps1`, a self-elevating Windows clock resync
  script**, plus `$ClockSyncEnabled` (default `$true` in `common.ps1`,
  settable in the override file). Forces `w32tm /resync /force`,
  prompting for UAC elevation if not already running as Administrator
  and giving up after a minute if that's never approved; exits
  immediately with a "time sync not enabled" message if
  `$ClockSyncEnabled` is `$false`. `startsim_master.ps1`/
  `startsim_slave.ps1` now call it at startup (via the new
  `Test-ClockSyncNeeded` in `functions.ps1`) if the Windows clock
  hasn't had a successful NTP sync in the last 24h. Also launched
  directly by the router itself when it detects a clock skew against
  its upstream -- see `router/docs/Changelog.md` version 1.15.0's
  `[performance] clock_sync_script`.

## 1.6.0 (2026-10-07)

- **New: configurable delays in the `startsim_master.ps1`/
  `startsim_slave.ps1` addon startup sequence.** `$DelayAfterPsxMainServerStart`
  (after starting the PSX main server, before the router -- was a fixed 1s)
  and `$DelayAfterRouterStart` (after starting a sim's router, before any
  of that sim's addons -- was a fixed 5s, master only) both default to 5s
  and are now set in one place in `common.ps1`. `startsim_slave.ps1` gets
  `$DelayAfterRouterStart` too, applied only when it isn't instead pausing
  for the interactive `$StopAfterSlaveRouterStart` prompt. Also added
  `$DelayBetweenAddons` (default 0s), a generic delay before every single
  addon start in both scripts (and `start_nonscripted_apps`), via a new
  `AddonDelay` helper in `functions.ps1` that stays silent at the default.
  Removed the special one-off extra delay that used to apply only before
  HAFAP/CPDLC.
- **Fix: `$PsxNetConfigDir`/`$PsxNetEfbConfigDir` could point at a Documents
  folder that doesn't exist.** Both were built from
  `"$env:USERPROFILE\Documents\..."`, which silently stops being correct
  once OneDrive's "Known Folder Move" redirects Documents elsewhere --
  confirmed live, where it broke `start_psx_net_vatsim.ps1`'s config
  rewrite with four confusing cascading PowerShell errors for one real
  cause. Both now derive from `[Environment]::GetFolderPath('MyDocuments')`,
  which Windows already keeps correct across such redirection. Also added
  a `RequireConfigFile` guard (in `functions.ps1`, used by the seven
  `start_*.ps1` scripts that rewrite a PSX.NET-style XML config before
  launching) that fails with one clear, actionable message if the config
  file still isn't where expected, instead of letting a missing file
  cascade into unrelated-looking XML errors.

## 1.5.0 (2026-10-03)

- **New: multiple start profiles, so several people can share one
  checkout.** When `psxhacks-start-override.ps1` doesn't exist,
  `common.ps1` now looks for `psxhacks-start-profile-<name>.ps1` files
  next to it. None found: same error as before. One or more found: a
  start script (but never a stop script, which always reuses whatever
  was last chosen instead) shows an interactive Up/Down/Enter/Escape
  picker, then remembers the choice in `psxhacks-start-profile-selected.txt`
  for the rest of the session. New shared functions in `functions.ps1`:
  `Select-StartProfile`, `Resolve-StartOverrideFile`,
  `Get-StartProfileSelectionFile`. Whether a given dot-source of
  `common.ps1` is a "stop context" (and must therefore never prompt) is
  derived from the filename of whichever script dot-sourced it
  (`$MyInvocation.PSCommandPath`), not a flag threaded through every
  individual script - so this needed no changes to any of the existing
  `start_<addon>.ps1`/`stop_<addon>.ps1` files.

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
