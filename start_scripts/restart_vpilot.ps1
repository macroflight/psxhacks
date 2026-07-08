. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "vPilot"
KillProcess "vPilot"

Set-Location $VPilotDir

switch ($VpilotPlugin) {
    "none" {
        # Do nothing - leave vPilot-Pushover.ini as-is.
    }
    "PSX Printer" {
        Copy-Item ".\Plugins\vPilot-Pushover-TOROUTER.ini" .\Plugins\vPilot-Pushover.ini -Force
    }
    "Pushover" {
        Copy-Item ".\Plugins\vPilot-Pushover-TOPUSHOVER.ini" .\Plugins\vPilot-Pushover.ini -Force
    }
    default {
        Write-Host "Unknown VpilotPlugin value: $VpilotPlugin" -ForegroundColor Yellow
    }
}

if ($RadioApp -ne "vPilot") {
    Start-Process .\vPilot.exe -ArgumentList "/novoice"
} else {
    Start-Process .\vPilot.exe
}
