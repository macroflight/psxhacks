. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Stop CS CDU"
KillProcess "CockpitSimulator*"
