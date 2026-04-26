. "$PSScriptRoot\common.ps1"

cd $VPilotDir

# Configure Pushover plugin to send directly to Pushover (not via router)
Remove-Item .\Plugins\vPilot-Pushover.ini
Copy-Item .\Plugins\vPilot-Pushover-TOPUSHOVER.ini .\Plugins\vPilot-Pushover.ini

Start-Process .\vPilot.exe
