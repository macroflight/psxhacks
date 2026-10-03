. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Stop PSX.NET.EFB"
KillProcess "PSX.NET.EFB.Windows"
