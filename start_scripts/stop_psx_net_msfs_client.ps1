. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Stop MSFS Client"
KillProcess "PSX.NET.MSFS2024.Client"
