. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Stop SimObjectRouter"
KillProcess "PSX.NET.MSFS.Temporary.SimObjectRouter"
