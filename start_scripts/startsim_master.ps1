. "$PSScriptRoot\common.ps1"

Test-PythonRequirement

function Invoke-WindowPosition([string]$addon) {
    if ($ChangeWindowPositions) {
        $name = if ($SimAddonNames.Contains($addon)) { $SimAddonNames[$addon] } else { $addon }
        Write-Output ("Positioning " + $name + "...")
        & "$PSScriptRoot\apply_window_positions.ps1" -Addon $addon
    }
}

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
    Invoke-WindowPosition "BACARS"
}

if ($StartPsxNet ) {
    Write-Output "Starting PSX.NET..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psx_net.ps1"
    Invoke-WindowPosition "PSX.NET"
}

if ($StartPsxNetGroundHandling ) {
    Write-Output "Starting PSX.NET.GroundHandling..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psx_net_groundhandling.ps1"
    Invoke-WindowPosition "PSX.NET.GroundHandling"
}

if ($StartCpdlc ) {
    Delay 5
    Write-Output "Starting HAFAP (CPDLC)..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_cpdlc.ps1"
    Invoke-WindowPosition "HAFAP/CPDLC"
}

if ($StartFrankentanker ) {
    Write-Output "Starting FrankenTanker..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_frankentanker.ps1"
    Invoke-WindowPosition "frankentanker"
}

if ($StartFrankenweather ) {
    Write-Output "Starting FrankenWeather..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_frankenweather.ps1"
    Invoke-WindowPosition "frankenweather"
}

if ($StartFrankenpush ) {
    Write-Output "Starting FrankenPush..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_frankenpush.ps1"
    Invoke-WindowPosition "frankenpush"
}

if ($StartSrslPsxMaster ) {
    Write-Output "Starting SRSL-PSX..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_srsl_psx_master.ps1"
    Invoke-WindowPosition "SRSL-PSX master"
}

if ($StartCmcPsx ) {
    Write-Output "Starting CMC-PSX..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_cmc_psx.ps1"
    Invoke-WindowPosition "CMC-PSX"
}

if ($StartPsxSimlinkBridge ) {
    Write-Output "Starting psx_simlink_bridge..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psx_simlink_bridge.ps1"
    Invoke-WindowPosition "psx_simlink_bridge"
}
