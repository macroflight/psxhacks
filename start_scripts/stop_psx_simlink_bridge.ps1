. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Stop psx_simlink_bridge"
# Matches both psx_simlink_bridge_*.exe and psx_simlink.exe - keep in sync
# with frankencontrol.py's "simlink_bridge" entry.
KillProcess "psx_simlink*"
