Remove-Item Env:\PSXHACKS_NOROUTER -ErrorAction SilentlyContinue

. "$PSScriptRoot\common.ps1"

Write-Output "start_scripts version $(Get-StartScriptsVersion)"

Test-PythonRequirement

if (Test-ClockSyncNeeded) {
    Write-Output "Windows clock hasn't synced in the last 24h, forcing a resync..."
    & "$PSScriptRoot\sync_clock.ps1"
}

Write-Output "Starting slave sim router..."
Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_router_slave.ps1"
Invoke-WindowPosition "frankenrouter slave"

if ($StopAfterSlaveRouterStart) {
    Read-Host -Prompt "Connect to $FrankenRouterSlaveWeb and connect to the master sim, then press Enter"
} else {
    Delay $DelayAfterRouterStart
}

if ($StartFrankenident ) {
    AddonDelay
    Write-Output "Starting FrankenIDENT..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_frankenident.ps1"
}

AddonDelay
Write-Output "Starting PSX main clients..."
Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_psx_main_clients.ps1"

if ($StartPsxNetVatsim ) {
    AddonDelay
    Write-Output "Starting PSX.NET.VATSIM..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psx_net_vatsim.ps1"
}

if ($StartVpilot ) {
    AddonDelay
    Write-Output "Starting vPilot..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_vpilot.ps1"
}

if ($StartPsxNetMsfsRouter ) {
    AddonDelay
    Write-Output "Starting PSX.NET.MSFS.Router..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psx_net_msfs_router.ps1"
}

if ($StartPsxSounds ) {
    AddonDelay
    Write-Output "Starting PSXSounds..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psxsounds.ps1"
}

if ($StartPsxVibrate ) {
    AddonDelay
    Write-Output "Starting PSXVibrate..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psxvibrate.ps1"
}

if ($StartFrankenusb ) {
    AddonDelay
    Write-Output "Starting FrankenUSB..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_frankenusb.ps1"
}

if ($StartSrslPsxSlave ) {
    AddonDelay
    Write-Output "Starting SRSL-PSX..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_srsl_psx_slave.ps1"
}

if ($StartAcarsPrint -and -not $StartFrankenprint ) {
    AddonDelay
    Write-Output "Starting ACARS Print..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_acarsprint.ps1"
}

if ($StartFrankenprint ) {
    AddonDelay
    Write-Output "Starting FrankenPrinter..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_frankenprint.ps1"
}

if ($StartEfb ) {
    AddonDelay
    Write-Output "Starting PSX.NET.EFB..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psx_net_efb.ps1"
}

if ($StartFrankencduproxy ) {
    AddonDelay
    Write-Output "Starting FrankenCDU proxy..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_frankencduproxy.ps1"
}

if ($StartCsCdu ) {
    AddonDelay
    Write-Output "Starting CS CDU..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_cs_cdu.ps1"
}

if ($StartCpflightFsCom ) {
    AddonDelay
    Write-Output "Starting CPFlight FS_COM..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_cpflight_fscom.ps1"
}

if ($StartPsxNetMsfsClient) {
    AddonDelay
    Write-Output "Starting PSX.NET.MSFS.Client..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psx_net_msfs_client.ps1"
}

if ($StartPsxNetOrchestration ) {
    AddonDelay
    Write-Output "Starting PSX.NET.Orchestration..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psx_net_orchestration.ps1"
}

if ($StartSimObjectRouter ) {
    AddonDelay
    Write-Output "Starting SimObjectRouter..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_sim_object_router.ps1"
}

AddonDelay
Write-Output "Starting non-scripted apps..."
start_nonscripted_apps

Read-Host -Prompt "Done. Enter to close. If flying alone (or as VATPRI), remember to disable filters: $FrankenRouterSlaveWeb"
