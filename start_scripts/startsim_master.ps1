Remove-Item Env:\PSXHACKS_NOROUTER -ErrorAction SilentlyContinue

. "$PSScriptRoot\common.ps1"

Write-Output "start_scripts version $(Get-StartScriptsVersion)"

Test-PythonRequirement

Write-Output "Starting PSX main server..."
Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_psx_main_server.ps1"

Delay $DelayAfterPsxMainServerStart

Write-Output "Starting master sim router..."
Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_router_master.ps1"
Invoke-WindowPosition "frankenrouter master"

Delay $DelayAfterRouterStart

if ($StartBacars ) {
    AddonDelay
    Write-Output "Starting BACARS..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_bacars.ps1"
}

if ($StartCpdlc ) {
    AddonDelay
    Write-Output "Starting HAFAP (CPDLC)..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_cpdlc.ps1"
}

if ($StartFrankentanker ) {
    AddonDelay
    Write-Output "Starting FrankenTanker..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_frankentanker.ps1"
}

if ($StartFrankenweather ) {
    AddonDelay
    Write-Output "Starting FrankenWeather..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_frankenweather.ps1"
}

if ($StartFrankenpush ) {
    AddonDelay
    Write-Output "Starting FrankenPush..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_frankenpush.ps1"
}

if ($StartSrslPsxMaster ) {
    AddonDelay
    Write-Output "Starting SRSL-PSX..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_srsl_psx_master.ps1"
}

if ($StartCmcPsx ) {
    AddonDelay
    Write-Output "Starting CMC-PSX..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_cmc_psx.ps1"
}

if ($StartPsxSimlinkBridge ) {
    AddonDelay
    Write-Output "Starting psx_simlink_bridge..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psx_simlink_bridge.ps1"
}

if ($StartFrankencontrol ) {
    AddonDelay
    Write-Output "Starting FrankenControl..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_frankencontrol.ps1"
}
