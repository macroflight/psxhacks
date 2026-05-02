. "$PSScriptRoot\common.ps1"

Write-Output "Starting slave sim router..."
Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_router_slave.ps1"

Read-Host -Prompt "Connect to $FrankenRouterSlaveWeb/upstream and connect to the master sim, then press Enter"

if ($StartIdent) {
    Write-Output "Starting FrankenIDENT..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_frankenident.ps1"
}

Write-Output "Starting PSX main clients..."
Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_psx_clients.ps1"

# Wait for PSX main clients to start (one of which runs the boost
# server which PSX.NET.MSFS.Router needs.
Delay 5

Write-Output "Starting PSX.NET.MSFS.Router..."
Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psx_net_msfs_router.ps1"

if ($StartPsxSounds) {
    Write-Output "Starting PSXSounds..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psxsounds.ps1"
}

if ($StartFrankenusb) {
    Write-Output "Starting FrankenUSB..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_frankenusb.ps1"
}

if ($StartFrankentanker) {
    Write-Output "Starting FrankenTanker..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_frankentanker.ps1"
}

if ($StartFrankenwind) {
    Write-Output "Starting FrankenWind..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_frankenwind.ps1"
}

if ($StartFrankenturb) {
    Write-Output "Starting FrankenTurb..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_frankenturb.ps1"
}

if ($StartAcarsPrint) {
    Write-Output "Starting ACARS Print..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_acarsprint.ps1"
}

if ($StartEfb) {
    Write-Output "Starting PSX.NET.EFB..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_psxnetefb.ps1"
}

if ($StartVpilot) {
    if ($VpilotPlugin -eq "PSX Printer") {
        Write-Output "Starting vPilot (PSX Printer plugin)..."
        Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_vpilot_pushover_to_router.ps1"
    } else {
        Write-Output "Starting vPilot (Pushover plugin)..."
        Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_vpilot_pushover.ps1"
    }
}

if ($StartCsCdu) {
    Write-Output "Starting CS CDU..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_cs_cdu.ps1"
}

Write-Output "Starting non-scripted apps..."
start_nonscripted_apps

Read-Host -Prompt "Now start MSFS and enter free flight, then press Enter"

Write-Output "Starting PSX.NET.MSFS.Client..."
Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psx_net_msfs_client.ps1"

if ($StartFrankenfreeze) {
    Write-Output "Starting Frankenfreeze..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_frankenfreeze.ps1"
}

Read-Host -Prompt "Done. Enter to close. If flying alone (or as VATPRI), remember to disable filters: $FrankenRouterSlaveWeb/filter"
