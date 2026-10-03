. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Stop vPilot"
KillProcess "vPilot"
