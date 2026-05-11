. "$PSScriptRoot\common.ps1"

Write-Output "Starting slave sim router..."
Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_router_slave.ps1"

if ($StopAfterSlaveRouterStart) {
    Read-Host -Prompt "Connect to $FrankenRouterSlaveWeb and connect to the master sim, then press Enter"
}

if ($StartFrankenident ) {
    Write-Output "Starting FrankenIDENT..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_frankenident.ps1"
}

Write-Output "Starting PSX main clients..."
Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_psx_clients.ps1"

if ($StartPsxNetMsfsRouter ) {
    Write-Output "Starting PSX.NET.MSFS.Router..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psx_net_msfs_router.ps1"
}

if ($StartPsxSounds ) {
    Write-Output "Starting PSXSounds..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psxsounds.ps1"
}

if ($StartFrankenusb ) {
    Write-Output "Starting FrankenUSB..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_frankenusb.ps1"
}

if ($StartFrankenwind ) {
    Write-Output "Starting FrankenWind..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_frankenwind.ps1"
}

if ($StartAcarsPrint ) {
    Write-Output "Starting ACARS Print..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_acarsprint.ps1"
}

if ($StartEfb ) {
    Write-Output "Starting PSX.NET.EFB..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psxnetefb.ps1"
}

if ($StartVpilot ) {
    Write-Output "Starting vPilot..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_vpilot.ps1"
}

if ($StartPsxNetVatsim ) {
    Write-Output "Starting PSX.NET.VATSIM..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psx_net_vatsim.ps1"
}

if ($StartFrankencduproxy ) {
    Write-Output "Starting FrankenCDU proxy..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_frankencduproxy.ps1"
}

if ($StartCsCdu ) {
    Write-Output "Starting CS CDU..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_cs_cdu.ps1"
}

if ($StopBeforeMsfsStart) {
    Read-Host -Prompt "Now start MSFS and enter free flight, then press Enter"
}

Write-Output "Starting PSX.NET.MSFS.Client..."
Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psx_net_msfs_client.ps1"

if ($StartPsxNetWeatherRadar ) {
    Write-Output "Starting PSX.NET.WeatherRadar..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psx_net_weather_radar.ps1"
}

if ($StartPsxNetGroundCrew ) {
    Write-Output "Starting PSX.NET.GroundCrew..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psx_net_ground_crew.ps1"
}

if ($StartSimObjectRouter ) {
    Write-Output "Starting SimObjectRouter..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_sim_object_router.ps1"
}

if ($StartFrankenfreeze ) {
    Write-Output "Starting Frankenfreeze..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_frankenfreeze.ps1"
}

Write-Output "Starting non-scripted apps..."
start_nonscripted_apps

Read-Host -Prompt "Done. Enter to close. If flying alone (or as VATPRI), remember to disable filters: $FrankenRouterSlaveWeb"
