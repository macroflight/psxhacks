. "$PSScriptRoot\common.ps1"

function Apply-WindowPosition([string]$addon) {
    if ($ChangeWindowPositions) {
        $name = if ($SimAddonNames.Contains($addon)) { $SimAddonNames[$addon] } else { $addon }
        Write-Output ("Positioning " + $name + "...")
        & "$PSScriptRoot\apply_window_positions.ps1" -Addon $addon
    }
}

Write-Output "Starting PSX main server..."
Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_main_server.ps1"

Delay 5

Write-Output "Starting master sim router..."
Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_router_master.ps1"
Apply-WindowPosition "frankenrouter master"

Delay 5

Write-Output "Starting BACARS..."
Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_bacars.ps1"
Apply-WindowPosition "BACARS"

Write-Output "Starting PSX.NET..."
Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psxnet.ps1"
Apply-WindowPosition "PSX.NET"

if ($StartFrankenutil -eq "master") {
    Write-Output "Starting FrankenUtil..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_frankenutil.ps1"
    Apply-WindowPosition "frankenutil"
}

if ($StartCpdlc -eq "master") {
    Write-Output "Starting HAFAP (CPDLC)..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_cpdlc.ps1"
    Apply-WindowPosition "HAFAP/CPDLC"
}

if ($StartFrankenident -eq "master") {
    Write-Output "Starting FrankenIDENT..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_frankenident.ps1"
    Apply-WindowPosition "frankenident"
}

if ($StartPsxSounds -eq "master") {
    Write-Output "Starting PSXSounds..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\restart_psxsounds.ps1"
    Apply-WindowPosition "PSXSounds"
}

if ($StartFrankenusb -eq "master") {
    Write-Output "Starting FrankenUSB..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_frankenusb.ps1"
    Apply-WindowPosition "frankenusb"
}

if ($StartFrankentanker -eq "master") {
    Write-Output "Starting FrankenTanker..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_frankentanker.ps1"
    Apply-WindowPosition "frankentanker"
}

if ($StartFrankenwind -eq "master") {
    Write-Output "Starting FrankenWind..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_frankenwind.ps1"
    Apply-WindowPosition "frankenwind"
}

if ($StartFrankenturb -eq "master") {
    Write-Output "Starting FrankenTurb..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_frankenturb.ps1"
    Apply-WindowPosition "frankenturb"
}

if ($StartFrankenfreeze -eq "master") {
    Write-Output "Starting FrankenFreeze..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_frankenfreeze.ps1"
    Apply-WindowPosition "frankenfreeze"
}

if ($StartAcarsPrint -eq "master") {
    Write-Output "Starting ACARS Print..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_acarsprint.ps1"
    Apply-WindowPosition "ACARS Print App"
}

if ($StartEfb -eq "master") {
    Write-Output "Starting PSX.NET.EFB..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_psxnetefb.ps1"
    Apply-WindowPosition "PSX.NET.EFB"
}

if ($StartVpilot -eq "master") {
    if ($VpilotPlugin -eq "PSX Printer") {
        Write-Output "Starting vPilot (PSX Printer plugin)..."
        Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_vpilot_pushover_to_router.ps1"
    } else {
        Write-Output "Starting vPilot (Pushover plugin)..."
        Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_vpilot_pushover.ps1"
    }
    Apply-WindowPosition "vPilot"
}

if ($StartFrankencduproxy -eq "master") {
    Write-Output "Starting FrankenCDU proxy..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_frankencduproxy.ps1"
    Apply-WindowPosition "frankencduproxy"
}

if ($StartCsCdu -eq "master") {
    Write-Output "Starting CS CDU..."
    Start-Process powershell -ArgumentList "-File", "$PSScriptRoot\start_cs_cdu.ps1"
}
