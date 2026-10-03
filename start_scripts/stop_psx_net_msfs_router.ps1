. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Stop MSFS Router"
KillProcess "PSX.NET.MSFS.Router"
