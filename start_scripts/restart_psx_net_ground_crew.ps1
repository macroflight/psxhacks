. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "PSX.NET.GroundCrew"
KillProcess "PSX.NET.GroundCrew"

Start-Process -WorkingDirectory $PsxNetGroundCrewDir "$PsxNetGroundCrewDir\PSX.NET.GroundCrew.exe"
