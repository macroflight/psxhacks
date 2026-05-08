. "$PSScriptRoot\common.ps1"

function Apply-WindowPosition([string]$addon) {
    if ($ChangeWindowPositions) {
        $name = if ($SimAddonNames.Contains($addon)) { $SimAddonNames[$addon] } else { $addon }
        Write-Output ("Positioning " + $name + "...")
        & "$PSScriptRoot\apply_window_positions.ps1" -Addon $addon
    }
}

Write-Output "Starting slave sim router..."
Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_router_slave.ps1"
Apply-WindowPosition "frankenrouter slave"

Read-Host -Prompt "Connect to $FrankenRouterSlaveWeb/upstream and connect to the master sim, then press Enter"

if ($StartCpdlc -eq "slave") {
    Write-Output "Starting HAFAP (CPDLC)..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_cpdlc.ps1"
    Apply-WindowPosition "HAFAP/CPDLC"
}

if ($StartFrankenident -eq "slave") {
    Write-Output "Starting FrankenIDENT..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_frankenident.ps1"
    Apply-WindowPosition "frankenident"
}

if ($StartFrankenutil -eq "slave") {
    Write-Output "Starting FrankenUtil..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_frankenutil.ps1"
    Apply-WindowPosition "frankenutil"
}

Write-Output "Starting PSX main clients..."
Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_psx_clients.ps1"

# Wait for PSX main clients to start (one of which runs the boost
# server which PSX.NET.MSFS.Router needs.
Delay 5

Write-Output "Starting PSX.NET.MSFS.Router..."
Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psx_net_msfs_router.ps1"
Apply-WindowPosition "PSX.NET.MSFS.Router"

if ($StartPsxSounds -eq "slave") {
    Write-Output "Starting PSXSounds..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psxsounds.ps1"
    Apply-WindowPosition "PSXSounds"
}

if ($StartFrankenusb -eq "slave") {
    Write-Output "Starting FrankenUSB..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_frankenusb.ps1"
    Apply-WindowPosition "frankenusb"
}

if ($StartFrankentanker -eq "slave") {
    Write-Output "Starting FrankenTanker..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_frankentanker.ps1"
    Apply-WindowPosition "frankentanker"
}

if ($StartFrankenwind -eq "slave") {
    Write-Output "Starting FrankenWind..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_frankenwind.ps1"
    Apply-WindowPosition "frankenwind"
}

if ($StartFrankenturb -eq "slave") {
    Write-Output "Starting FrankenTurb..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_frankenturb.ps1"
    Apply-WindowPosition "frankenturb"
}

if ($StartAcarsPrint -eq "slave") {
    Write-Output "Starting ACARS Print..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_acarsprint.ps1"
    Apply-WindowPosition "ACARS Print App"
}

if ($StartEfb -eq "slave") {
    Write-Output "Starting PSX.NET.EFB..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_psxnetefb.ps1"
    Apply-WindowPosition "PSX.NET.EFB"
}

if ($StartVpilot -eq "slave") {
    if ($VpilotPlugin -eq "PSX Printer") {
        Write-Output "Starting vPilot (PSX Printer plugin)..."
        Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_vpilot_pushover_to_router.ps1"
    } else {
        Write-Output "Starting vPilot (Pushover plugin)..."
        Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_vpilot_pushover.ps1"
    }
    Apply-WindowPosition "vPilot"
}

if ($StartFrankencduproxy -eq "slave") {
    Write-Output "Starting FrankenCDU proxy..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_frankencduproxy.ps1"
    Apply-WindowPosition "frankencduproxy"
}

if ($StartCsCdu -eq "slave") {
    Write-Output "Starting CS CDU..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_cs_cdu.ps1"
}

Write-Output "Starting non-scripted apps..."
start_nonscripted_apps

Read-Host -Prompt "Now start MSFS and enter free flight, then press Enter"

Write-Output "Starting PSX.NET.MSFS.Client..."
Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psx_net_msfs_client.ps1"
Apply-WindowPosition "PSX.NET.MSFS"

if ($StartFrankenfreeze -eq "slave") {
    Write-Output "Starting Frankenfreeze..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_frankenfreeze.ps1"
    Apply-WindowPosition "frankenfreeze"
}

Read-Host -Prompt "Done. Enter to close. If flying alone (or as VATPRI), remember to disable filters: $FrankenRouterSlaveWeb/filter"
