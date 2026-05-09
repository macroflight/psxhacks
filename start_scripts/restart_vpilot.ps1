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

if ($RadioApp -ne "vPilot") {
    Start-Process .\vPilot.exe -ArgumentList "/novoice"
} else {
    Start-Process .\vPilot.exe
}
