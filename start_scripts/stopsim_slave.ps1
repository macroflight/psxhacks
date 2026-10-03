Remove-Item Env:\PSXHACKS_NOROUTER -ErrorAction SilentlyContinue

. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Stop Slave Sim"

Write-Host ""
Write-Host "*** STOP SLAVE SIM ***" -ForegroundColor Yellow
Write-Host ""
Write-Host "This will stop PSX and all slave sim components." -ForegroundColor White
Write-Host ""
if ($StopSimConfirm) {
    $answer = Read-Host "Are you sure you want to stop the slave sim? [y/N]"
    if ($answer -notmatch '^[Yy]') {
        Write-Host "Cancelled." -ForegroundColor Yellow
        Read-Host -Prompt "Enter to close"
        exit 0
    }
}

Write-Host ""

# Stop PSX and all addon processes, then restart background apps

# PSX.NET.MSFS.Client (no "2024") has no restart_*.ps1 of its own
# (legacy/no addon-managed stop script exists), so it's still killed
# directly here rather than via a stop_*.ps1.
KillProcess "PSX.NET.MSFS.Client"

. "$PSScriptRoot\stop_psx_net_msfs_client.ps1"
. "$PSScriptRoot\stop_psx_net_msfs_router.ps1"
. "$PSScriptRoot\stop_psx_net_orchestration.ps1"
. "$PSScriptRoot\stop_sim_object_router.ps1"
. "$PSScriptRoot\stop_psxsounds.ps1"
. "$PSScriptRoot\stop_psxvibrate.ps1"
. "$PSScriptRoot\stop_psx_net_efb.ps1"
. "$PSScriptRoot\stop_vpilot.ps1"
. "$PSScriptRoot\stop_psx_net_vatsim.ps1"
. "$PSScriptRoot\stop_cs_cdu.ps1"
. "$PSScriptRoot\stop_frankenident.ps1"
. "$PSScriptRoot\stop_frankencduproxy.ps1"
. "$PSScriptRoot\stop_frankenprint.ps1"
. "$PSScriptRoot\stop_acarsprint.ps1"
. "$PSScriptRoot\stop_srsl_psx_slave.ps1"

# The stop_*.ps1 scripts above each set their own window title; restore
# ours now that they're done, since this window has more work to do yet.
$Host.UI.RawUI.WindowTitle = "Stop Slave Sim"

# Ask PSX server to shut down gracefully before killing java.exe
$env:PYTHONPATH = $PsxhacksDevel
& $PsxhacksPython "$PsxhacksDevel\psx_shutdown.py" "--psx-port=$FrankenrouterSlavePort"

# Stopping PSX clients nicely can take a while
Delay 10

# Stop slave sim router
$slaveRouterConfig = ($FrankenrouterSlaveOptions | Where-Object { $_ -like "--config-file=*" }) -replace "^--config-file=", ""
KillPythonScript $slaveRouterConfig

Read-Host -Prompt "Done. Enter to close. Note: MSFS and master sim components not stoppped"
