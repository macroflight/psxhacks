Remove-Item Env:\PSXHACKS_NOROUTER -ErrorAction SilentlyContinue
$env:PSXHACKS_NOROUTER = "1"

. "$PSScriptRoot\common.ps1"

Test-PythonRequirement

function Invoke-WindowPosition([string]$addon) {
    if ($ChangeWindowPositions) {
        $name = if ($SimAddonNames.Contains($addon)) { $SimAddonNames[$addon] } else { $addon }
        Write-Output ("Positioning " + $name + "...")
        & "$PSScriptRoot\apply_window_positions.ps1" -Addon $addon
    }
}

# No-router setup: a PSX main server plus its main client(s) (the actual
# flyable cockpit instance(s) - the server alone has no visual interface),
# no frankenrouter of any kind. Every addon below that connects to PSX at
# all already does so via $FrankenrouterMasterPort/$FrankenrouterSlavePort
# (common.ps1 aliases the latter to the former in this mode), so they all
# end up pointed at this same server with no changes needed to any
# individual addon script. The main client(s) themselves connect to the
# server the same way they always do (configured in their own .pref
# file(s)/PSX connection settings, pointed at 127.0.0.1:$FrankenrouterMasterPort)
# - that's outside this script's control, same as in router-based mode.
Write-Output "Starting PSX main server..."
Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_psx_main_server.ps1"

Delay 5

if ($StartFrankenident ) {
    Write-Output "Starting FrankenIDENT..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_frankenident.ps1"
    Invoke-WindowPosition "frankenident"
}

Write-Output "Starting PSX main clients..."
Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_psx_main_clients.ps1"

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

if ($StartPsxNetVatsim ) {
    Write-Output "Starting PSX.NET.VATSIM..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psx_net_vatsim.ps1"
    # Note: we position this window at the end since the app takes long to start sometimes
}

if ($StartVpilot ) {
    Write-Output "Starting vPilot..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_vpilot.ps1"
    Invoke-WindowPosition "vPilot"
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

if ($StartPsxNetMsfsRouter ) {
    Write-Output "Starting PSX.NET.MSFS.Router..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psx_net_msfs_router.ps1"
    Invoke-WindowPosition "PSX.NET.MSFS.Router"
}

if ($StartPsxSounds ) {
    Write-Output "Starting PSXSounds..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psxsounds.ps1"
    Invoke-WindowPosition "PSXSounds"
}

if ($StartFrankenusb ) {
    Write-Output "Starting FrankenUSB..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_frankenusb.ps1"
    Invoke-WindowPosition "frankenusb"
}

if ($StartSrslPsxMaster ) {
    Write-Output "Starting SRSL-PSX..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_srsl_psx_master.ps1"
    Invoke-WindowPosition "SRSL-PSX master"
}

if ($StartSrslPsxSlave ) {
    Write-Output "Starting SRSL-PSX..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_srsl_psx_slave.ps1"
    Invoke-WindowPosition "SRSL-PSX slave"
}

if ($StartCmcPsx ) {
    Write-Output "Starting CMC-PSX..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_cmc_psx.ps1"
    Invoke-WindowPosition "CMC-PSX"
}

if ($StartAcarsPrint -and -not $StartFrankenprint ) {
    Write-Output "Starting ACARS Print..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_acarsprint.ps1"
    Invoke-WindowPosition "ACARS Print App"
}

if ($StartFrankenprint ) {
    Write-Output "Starting FrankenPrinter..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_frankenprint.ps1"
    Invoke-WindowPosition "frankenprint"
}

if ($StartEfb ) {
    Write-Output "Starting PSX.NET.EFB..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psx_net_efb.ps1"
    Invoke-WindowPosition "PSX.NET.EFB"
}

if ($StartFrankencduproxy ) {
    Write-Output "Starting FrankenCDU proxy..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_frankencduproxy.ps1"
    Invoke-WindowPosition "frankencduproxy"
}

if ($StartFrankenmsfsbridge ) {
    Write-Output "Starting FrankenMSFSBridge..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_frankenmsfsbridge.ps1"
    Invoke-WindowPosition "frankenmsfsbridge"
}

if ($StartCsCdu ) {
    Write-Output "Starting CS CDU..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_cs_cdu.ps1"
}

if ($StartPsxSimlinkBridge ) {
    Write-Output "Starting psx_simlink_bridge..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psx_simlink_bridge.ps1"
    Invoke-WindowPosition "psx_simlink_bridge"
}

Delay 10
Invoke-WindowPosition "PSX.NET.VATSIM"

if ($StartPsxNetMsfsClient) {
    if ($StopBeforeMsfsStart) {
        Read-Host -Prompt "Now start MSFS and enter free flight, then press Enter"
    }

    Write-Output "Starting PSX.NET.MSFS.Client..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psx_net_msfs_client.ps1"
    Invoke-WindowPosition "PSX.NET.MSFS"
}

if ($StartPsxNetWeatherRadar ) {
    Write-Output "Starting PSX.NET.WeatherRadar..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psx_net_weather_radar.ps1"
    Invoke-WindowPosition "PSX.NET.WeatherRadar"
}

if ($StartPsxNetGroundCrew ) {
    Write-Output "Starting PSX.NET.GroundCrew..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psx_net_ground_crew.ps1"
    Invoke-WindowPosition "PSX.NET.GroundCrew"
}

if ($StartPsxNetOrchestration ) {
    Write-Output "Starting PSX.NET.Orchestration..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psx_net_orchestration.ps1"
    Invoke-WindowPosition "PSX.NET.Orchestration"
}

if ($StartSimObjectRouter ) {
    Write-Output "Starting SimObjectRouter..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_sim_object_router.ps1"
    Delay 5
    Invoke-WindowPosition "SimObjectRouter"
}

Write-Output "Starting non-scripted apps..."
start_nonscripted_apps

Read-Host -Prompt "Done. Enter to close."
