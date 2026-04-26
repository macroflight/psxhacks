. "$PSScriptRoot\common.ps1"

cd $VPilotDir

# Configure Pushover plugin to send via the FrankenRouter
Remove-Item .\Plugins\vPilot-Pushover.ini
Copy-Item .\Plugins\vPilot-Pushover-TOROUTER.ini .\Plugins\vPilot-Pushover.ini

Start-Process .\vPilot.exe
