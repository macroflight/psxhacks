. "$PSScriptRoot\common.ps1"

Write-Output "Starting PSX main server..."
Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_main_server.ps1"

Delay 5

Write-Output "Starting master sim router..."
Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_router_master.ps1"

Delay 5

if ($StartBacars ) {
    Write-Output "Starting BACARS..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_bacars.ps1"
}

if ($StartPsxNet ) {
    Write-Output "Starting PSX.NET..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psxnet.ps1"
}

if ($StartFrankenutil ) {
    Write-Output "Starting FrankenUtil..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_frankenutil.ps1"
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

if ($StartFrankenturb ) {
    Write-Output "Starting FrankenTurb..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_frankenturb.ps1"
}
