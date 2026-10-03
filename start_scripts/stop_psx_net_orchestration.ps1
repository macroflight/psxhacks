. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Stop PSX.NET.Orchestration"
KillProcess "PSX.NET.Orchestration"
