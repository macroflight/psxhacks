. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Stop psx_simlink_bridge"
KillProcess "psx_simlink_bridge*"
