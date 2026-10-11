. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Stop PSX.NET.Vibrate"
KillProcess "PSX.NET.Vibrate"
