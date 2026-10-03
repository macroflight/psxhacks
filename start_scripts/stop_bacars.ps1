. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Stop BACARS"
KillProcess "PSX.Bacars.UI"
