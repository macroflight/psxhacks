. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Stop Master Sim"

Write-Host ""
Write-Host "*** STOP MASTER SIM ***" -ForegroundColor Yellow
Write-Host ""
Write-Host "This will stop PSX and all master sim components." -ForegroundColor White
Write-Host "Other slave sims may be connected to this server." -ForegroundColor Red
Write-Host ""
$answer = Read-Host "Are you sure you want to stop the master sim? [y/N]"
if ($answer -notmatch '^[Yy]') {
    Write-Host "Cancelled." -ForegroundColor Yellow
    Read-Host -Prompt "Enter to close"
    exit 0
}

Write-Host ""

KillPythonScript "frankenutil.py"
KillPythonScript "psx-acars.py"
KillPythonScript "frankenrouter_ident.py"
KillPythonScript "frankentanker.py"
KillPythonScript "frankenwind.py"
KillPythonScript "frankenturb.py"
KillPythonScript "frankenfreeze.py"
KillPythonScript "frankencduproxy.py"
KillPythonScript "frankenusb.py"

KillProcess "PSX.Bacars.UI"
KillProcess "PSX.NET"
KillProcess "PSXSounds"
KillProcess "PSX.NET.EFB.Windows"
KillProcess "vPilot"
KillProcess "CockpitSimulator"
KillJavaJar "AcarsPrint.jar"

# Ask PSX server to shut down gracefully before killing java.exe
Write-Output "Shutting down PSX server..."
$env:PYTHONPATH = $PsxhacksDevel
& $PsxhacksPython "$PsxhacksDevel\psx_shutdown.py"

# Stopping PSX server nicely can take a while
Delay 10

KillJavaJar "AerowinxStart.jar"

# Stop master sim router last, after PSX has had time to shut down
$masterRouterConfig = ($FrankenrouterMasterOptions | Where-Object { $_ -like "--config-file=*" }) -replace "^--config-file=", ""
KillPythonScript $masterRouterConfig

Delay 5

Read-Host -Prompt "Done. Enter to close"
