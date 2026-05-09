. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "vPilot"
KillProcess "vPilot"

Set-Location $VPilotDir

$iniSource = if ($VpilotPlugin -eq "PSX Printer") {
    ".\Plugins\vPilot-Pushover-TOROUTER.ini"
} else {
    ".\Plugins\vPilot-Pushover-TOPUSHOVER.ini"
}
Copy-Item $iniSource .\Plugins\vPilot-Pushover.ini -Force

$vPilotArgs = if ($RadioApp -ne "vPilot") { @("/novoice") } else { @() }
Start-Process .\vPilot.exe -ArgumentList $vPilotArgs
