. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "PSX.NET.VATSIM"
KillProcess "GeoVR.PSX.Client.Wpf"

Start-Process -WorkingDirectory $PsxNetVatsimDir "$PsxNetVatsimDir\GeoVR.PSX.Client.Wpf.exe"
