Remove-Item Env:\PSXHACKS_NOROUTER -ErrorAction SilentlyContinue
$env:PSXHACKS_NOROUTER = "1"

. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Stop Sim"

Write-Host ""
Write-Host "*** STOP SIM ***" -ForegroundColor Yellow
Write-Host ""
Write-Host "This will stop PSX and all sim components (no-router setup)." -ForegroundColor White
Write-Host ""
if ($StopSimConfirm) {
    $answer = Read-Host "Are you sure you want to stop the sim? [y/N]"
    if ($answer -notmatch '^[Yy]') {
        Write-Host "Cancelled." -ForegroundColor Yellow
        Read-Host -Prompt "Enter to close"
        exit 0
    }
}

Write-Host ""

# Tell restart_cpdlc.ps1's window (if still open) that this stop is
# expected, so it doesn't mistake the forced kill below for a crash.
New-Item -Path $CpdlcExpectedStopFlag -ItemType File -Force | Out-Null
KillPythonScript "psx-acars.py"
KillPythonScript "frankentanker.py"
KillPythonScript "frankenweather.py"
KillPythonScript "frankenpush.py"

KillProcess "PSX.Bacars.UI"
KillProcess "PSX.NET"
KillJavaJar "$SrslPsxMasterDir\SRSL-PSX.jar"
KillJavaJar "$SrslPsxSlaveDir\SRSL-PSX.jar"
KillJavaJar "$CmcPsxDir\CMC-PSX.jar"
KillProcess "psx_simlink_bridge*"

KillProcess "PSX.NET.MSFS.Client"
KillProcess "PSX.NET.MSFS2024.Client"
KillProcess "PSX.NET.MSFS.Router"
KillProcess "PSX.NET.WeatherRadar"
KillProcess "PSX.NET.GroundCrew"
KillProcess "PSX.NET.Orchestration"
KillProcess "PSX.NET.MSFS.Temporary.SimObjectRouter"
KillProcess "PSXSounds"
KillProcess "PSXVibrate"
KillProcess "PSX.NET.EFB.Windows"
KillProcess "vPilot"
KillProcess "GeoVR.PSX.Client.Wpf"
KillProcess "CockpitSimulator"

KillPythonScript "frankenrouter_ident.py"
KillPythonScript "frankencduproxy.py"
KillPythonScript "frankenprint.py"
KillJavaJar "AcarsPrint.jar"

# Ask PSX server to shut down gracefully before killing java.exe
Write-Output "Shutting down PSX server..."
$env:PYTHONPATH = $PsxhacksDevel
& $PsxhacksPython "$PsxhacksDevel\psx_shutdown.py" "--psx-port=$FrankenrouterMasterPort"

# Stopping PSX server nicely can take a while
Delay 10

KillJavaJar "AerowinxStart.jar"

Read-Host -Prompt "Done. Enter to close."
