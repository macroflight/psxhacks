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

if ($StartFrankenutil -eq "master") {
    Write-Output "Starting FrankenUtil..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_frankenutil.ps1"
}

if ($StartCpdlc -eq "master") {
    Write-Output "Starting HAFAP (CPDLC)..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_cpdlc.ps1"
}

if ($StartFrankenident -eq "master") {
    Write-Output "Starting FrankenIDENT..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_frankenident.ps1"
}

if ($StartPsxSounds -eq "master") {
    Write-Output "Starting PSXSounds..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psxsounds.ps1"
}

if ($StartFrankenusb -eq "master") {
    Write-Output "Starting FrankenUSB..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_frankenusb.ps1"
}

if ($StartFrankentanker -eq "master") {
    Write-Output "Starting FrankenTanker..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_frankentanker.ps1"
}

if ($StartFrankenwind -eq "master") {
    Write-Output "Starting FrankenWind..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_frankenwind.ps1"
}

if ($StartFrankenturb -eq "master") {
    Write-Output "Starting FrankenTurb..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_frankenturb.ps1"
}

if ($StartFrankenfreeze -eq "master") {
    Write-Output "Starting FrankenFreeze..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_frankenfreeze.ps1"
}

if ($StartAcarsPrint -eq "master") {
    Write-Output "Starting ACARS Print..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_acarsprint.ps1"
}

if ($StartEfb -eq "master") {
    Write-Output "Starting PSX.NET.EFB..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_psxnetefb.ps1"
}

if ($StartVpilot -eq "master") {
    if ($VpilotPlugin -eq "PSX Printer") {
        Write-Output "Starting vPilot (PSX Printer plugin)..."
        Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_vpilot_pushover_to_router.ps1"
    } else {
        Write-Output "Starting vPilot (Pushover plugin)..."
        Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_vpilot_pushover.ps1"
    }
}

if ($StartCsCdu -eq "master") {
    Write-Output "Starting CS CDU..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_cs_cdu.ps1"
}
