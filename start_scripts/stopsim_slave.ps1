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

KillProcess "PSX.NET.MSFS.Client"
KillProcess "PSX.NET.MSFS2024.Client"
KillProcess "PSX.NET.MSFS.Router"
KillProcess "PSX.NET.WeatherRadar"
KillProcess "PSX.NET.GroundCrew"
KillProcess "PSX.NET.GroundHandling"
KillProcess "PSX.NET.MSFS.Temporary.SimObjectRouter"
KillProcess "PSXSounds"
KillProcess "PSXVibrate"
KillProcess "PSX.NET.EFB.Windows"
KillProcess "vPilot"
KillProcess "GeoVR.PSX.Client.Wpf"
KillProcess "CockpitSimulator"

KillPythonScript "frankenrouter_ident.py"
KillPythonScript "frankencduproxy.py"
KillPythonScript "frankenmsfsbridge.py"
KillPythonScript "frankenprint.py"
KillJavaJar "AcarsPrint.jar"
KillJavaJar "$SrslPsxSlaveDir\SRSL-PSX.jar"

# Ask PSX server to shut down gracefully before killing java.exe
$env:PYTHONPATH = $PsxhacksDevel
& $PsxhacksPython "$PsxhacksDevel\psx_shutdown.py" "--psx-port=$FrankenrouterSlavePort"

# Stopping PSX clients nicely can take a while
Delay 10

# Stop slave sim router
$slaveRouterConfig = ($FrankenrouterSlaveOptions | Where-Object { $_ -like "--config-file=*" }) -replace "^--config-file=", ""
KillPythonScript $slaveRouterConfig

Read-Host -Prompt "Done. Enter to close. Note: MSFS and master sim components not stoppped"
