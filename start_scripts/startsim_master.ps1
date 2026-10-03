Remove-Item Env:\PSXHACKS_NOROUTER -ErrorAction SilentlyContinue

. "$PSScriptRoot\common.ps1"

Write-Output "start_scripts version $(Get-StartScriptsVersion)"

Test-PythonRequirement

Write-Output "Starting PSX main server..."
Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_psx_main_server.ps1"

Delay 1

Write-Output "Starting master sim router..."
Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_router_master.ps1"
Invoke-WindowPosition "frankenrouter master"

Delay 5

if ($StartBacars ) {
    Write-Output "Starting BACARS..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_bacars.ps1"
}

if ($StartCpdlc ) {
    Delay 5
    Write-Output "Starting HAFAP (CPDLC)..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_cpdlc.ps1"
}

if ($StartFrankentanker ) {
    Write-Output "Starting FrankenTanker..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_frankentanker.ps1"
}

if ($StartFrankenweather ) {
    Write-Output "Starting FrankenWeather..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_frankenweather.ps1"
}

if ($StartFrankenpush ) {
    Write-Output "Starting FrankenPush..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_frankenpush.ps1"
}

if ($StartSrslPsxMaster ) {
    Write-Output "Starting SRSL-PSX..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_srsl_psx_master.ps1"
}

if ($StartCmcPsx ) {
    Write-Output "Starting CMC-PSX..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_cmc_psx.ps1"
}

if ($StartPsxSimlinkBridge ) {
    Write-Output "Starting psx_simlink_bridge..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psx_simlink_bridge.ps1"
}
