# Forces an immediate Windows Time Service resync (w32tm /resync /force).
#
# The resync itself needs an elevated (Administrator) process, so this
# script re-launches itself elevated via UAC if it isn't already running
# as Administrator, and gives up after $ClockSyncTimeoutSeconds if that
# never completes -- e.g. because the user never clicks "Yes" on the UAC
# prompt. Run in a background job so the wait can actually be bounded:
# Start-Process -Verb RunAs itself blocks until the prompt is resolved
# (there is no process to wait on until then), so a plain
# "$proc.WaitForExit($timeout)" after it would never even be reached in
# that case.
#
# Called both by startsim_master.ps1/startsim_slave.ps1 at startup (see
# Test-ClockSyncNeeded in functions.ps1) and by the router itself
# (frankenrouter.py's maybe_sync_clock_from_upstream_skew(), via
# [performance] clock_sync_script) when it notices a clock skew against
# its upstream. Always best-effort: callers should treat a non-zero exit
# code as "sync did not happen", not as a fatal error.

. "$PSScriptRoot\common.ps1"

if (-not $ClockSyncEnabled) {
    Write-Host "Time sync not enabled (set `$ClockSyncEnabled = `$true in $OverrideFile to enable)"
    exit 0
}

$ClockSyncTimeoutSeconds = 60

function Test-IsAdministrator {
    $principal = New-Object Security.Principal.WindowsPrincipal(
        [Security.Principal.WindowsIdentity]::GetCurrent())
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (Test-IsAdministrator) {
    # Already elevated (e.g. the whole sim was started as Administrator) --
    # just do the resync directly, no UAC prompt needed.
    w32tm /resync /force
    exit $LASTEXITCODE
}

Write-Host "Requesting elevation to resync the Windows clock (w32tm /resync /force)..."
$job = Start-Job -ScriptBlock {
    $proc = Start-Process powershell -Verb RunAs -PassThru -WindowStyle Hidden -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', 'w32tm /resync /force'
    )
    $proc.WaitForExit()
    $proc.ExitCode
}

if (-not (Wait-Job -Job $job -Timeout $ClockSyncTimeoutSeconds)) {
    Write-Warning "Clock sync timed out after ${ClockSyncTimeoutSeconds}s (UAC prompt not approved?) -- giving up"
    Remove-Job -Job $job -Force
    exit 1
}

$exitCode = Receive-Job -Job $job
Remove-Job -Job $job
if ($exitCode -ne 0) {
    Write-Warning "Clock sync failed (exit code $exitCode)"
}
exit $exitCode
