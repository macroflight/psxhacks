. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Stop PSXVibrate"
KillProcess "PSXVibrate"
