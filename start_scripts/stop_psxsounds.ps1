. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Stop PSXSounds"
KillProcess "PSXSounds"
