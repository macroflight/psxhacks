. "$PSScriptRoot\common.ps1"

function Apply-WindowPosition([string]$addon) {
    if ($ChangeWindowPositions) {
        $name = if ($SimAddonNames.Contains($addon)) { $SimAddonNames[$addon] } else { $addon }
        Write-Output ("Positioning " + $name + "...")
        & "$PSScriptRoot\apply_window_positions.ps1" -Addon $addon
    }
}

Write-Output "Starting PSX main server..."
Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_main_server.ps1"

Delay 5

Write-Output "Starting master sim router..."
Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_router_master.ps1"
Apply-WindowPosition "frankenrouter master"

Delay 5

if ($StartBacars ) {
    Write-Output "Starting BACARS..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_bacars.ps1"
    Apply-WindowPosition "BACARS"
}

if ($StartPsxNet ) {
    Write-Output "Starting PSX.NET..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psxnet.ps1"
    Apply-WindowPosition "PSX.NET"
}

if ($StartFrankenutil ) {
    Write-Output "Starting FrankenUtil..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_frankenutil.ps1"
    Apply-WindowPosition "frankenutil"
}

if ($StartCpdlc ) {
    Delay 5
    Write-Output "Starting HAFAP (CPDLC)..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_cpdlc.ps1"
    Apply-WindowPosition "HAFAP/CPDLC"
}

if ($StartFrankentanker ) {
    Write-Output "Starting FrankenTanker..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_frankentanker.ps1"
    Apply-WindowPosition "frankentanker"
}

if ($StartFrankenturb ) {
    Write-Output "Starting FrankenTurb..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_frankenturb.ps1"
    Apply-WindowPosition "frankenturb"
}
