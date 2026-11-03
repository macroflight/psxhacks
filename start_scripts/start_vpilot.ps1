. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "vPilot"

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

# $VpilotOptions from the override file (e.g. "-remote"), plus "/novoice"
# when another app does the radio
$vpilotArgs = @($VpilotOptions)
if ($RadioApp -ne "vPilot" -and $vpilotArgs -notcontains "/novoice") {
    $vpilotArgs += "/novoice"
}
if ($vpilotArgs.Count -gt 0) {
    Start-Process .\vPilot.exe -ArgumentList $vpilotArgs
} else {
    Start-Process .\vPilot.exe
}
Invoke-WindowPosition "vPilot"
