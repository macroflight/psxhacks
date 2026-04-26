. "$PSScriptRoot\common.ps1"

# Stop background apps that should not run during a sim session
& $SyncTrayzor --shutdown
