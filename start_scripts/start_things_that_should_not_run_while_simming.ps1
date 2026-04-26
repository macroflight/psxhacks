. "$PSScriptRoot\common.ps1"

# Restart background apps that were stopped before the sim session
Start-Process -FilePath $SyncTrayzor -ArgumentList "--start-syncthing"
