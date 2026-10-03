. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Stop PSX.NET.VATSIM"
KillProcess "GeoVR.PSX.Client.Wpf"
