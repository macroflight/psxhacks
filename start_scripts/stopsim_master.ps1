Remove-Item Env:\PSXHACKS_NOROUTER -ErrorAction SilentlyContinue

. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Stop Master Sim"

Write-Host ""
Write-Host "*** STOP MASTER SIM ***" -ForegroundColor Yellow
Write-Host ""
Write-Host "This will stop PSX and all master sim components." -ForegroundColor White
Write-Host "Other slave sims may be connected to this server." -ForegroundColor Red
Write-Host ""
if ($StopSimConfirm) {
    $answer = Read-Host "Are you sure you want to stop the master sim? [y/N]"
    if ($answer -notmatch '^[Yy]') {
        Write-Host "Cancelled." -ForegroundColor Yellow
        Read-Host -Prompt "Enter to close"
        exit 0
    }
}

Write-Host ""

. "$PSScriptRoot\stop_cpdlc.ps1"
. "$PSScriptRoot\stop_frankentanker.ps1"
. "$PSScriptRoot\stop_frankenweather.ps1"
. "$PSScriptRoot\stop_frankenpush.ps1"
. "$PSScriptRoot\stop_bacars.ps1"
. "$PSScriptRoot\stop_srsl_psx_master.ps1"
. "$PSScriptRoot\stop_cmc_psx.ps1"
. "$PSScriptRoot\stop_psx_simlink_bridge.ps1"

# The stop_*.ps1 scripts above each set their own window title; restore
# ours now that they're done, since this window has more work to do yet.
$Host.UI.RawUI.WindowTitle = "Stop Master Sim"

# Ask PSX server to shut down gracefully before killing java.exe
Write-Output "Shutting down PSX server..."
$env:PYTHONPATH = $PsxhacksDevel
& $PsxhacksPython "$PsxhacksDevel\psx_shutdown.py" "--psx-port=$FrankenrouterMasterPort"

# Stopping PSX server nicely can take a while
Delay 10

KillJavaJar "AerowinxStart.jar"

# Stop master sim router last, after PSX has had time to shut down
$masterRouterConfig = ($FrankenrouterMasterOptions | Where-Object { $_ -like "--config-file=*" }) -replace "^--config-file=", ""
KillPythonScript $masterRouterConfig
