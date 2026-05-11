. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "PSX.NET.WeatherRadar"
KillProcess "PSX.NET.WeatherRadar"

Start-Process -WorkingDirectory $PsxNetWeatherRadarDir "$PsxNetWeatherRadarDir\PSX.NET.WeatherRadar.exe"
