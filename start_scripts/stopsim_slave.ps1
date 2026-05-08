. "$PSScriptRoot\common.ps1"

# Stop PSX and all addon processes, then restart background apps

KillProcess "PSX.NET.MSFS.Client"
KillProcess "PSX.NET.MSFS2024.Client"
KillProcess "PSX.NET.MSFS.Router"
KillProcess "PSX.NET.WeatherRadar"
KillProcess "SimObjectRouter"
KillProcess "PSXSounds"
KillProcess "PSXVibrate"
KillProcess "PSX.NET.EFB.Windows"
KillProcess "vPilot"
KillProcess "CockpitSimulator"

KillPythonScript "frankenfreeze.py"
KillPythonScript "frankenrouter_ident.py"
KillPythonScript "frankenturb.py"
KillPythonScript "frankencduproxy.py"
KillJavaJar "AcarsPrint.jar"

# Ask PSX server to shut down gracefully before killing java.exe
$env:PYTHONPATH = $PsxhacksDevel
& $PsxhacksPython "$PsxhacksDevel\psx_shutdown.py"

# Stopping PSX clients nicely can take a while
Delay 10

# Stop slave sim router
$slaveRouterConfig = ($FrankenrouterSlaveOptions | Where-Object { $_ -like "--config-file=*" }) -replace "^--config-file=", ""
KillPythonScript $slaveRouterConfig

& "$PSScriptRoot\start_things_that_should_not_run_while_simming.ps1"

Delay 5

Read-Host -Prompt "Done. Enter to close. Note: MSFS and master sim components not stoppped"
