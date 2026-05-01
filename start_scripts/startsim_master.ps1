. "$PSScriptRoot\common.ps1"

Write-Output "Starting PSX main server..."
Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_main_server.ps1"

Delay 5

Write-Output "Starting master sim router..."
Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_router_master.ps1"

Delay 5

Write-Output "Starting BACARS..."
Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_bacars.ps1"

Write-Output "Starting PSX.NET..."
Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psxnet.ps1"

Write-Output "Starting frankenutil..."
Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_frankenutil.ps1"

if ($StartCpdlc) {
    Write-Output "Starting HAFAP (CPDLC)..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_cpdlc.ps1"
}
