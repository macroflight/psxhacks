. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "PSX.NET.WeatherRadar"
KillProcess "PSX.NET.WeatherRadar"

# PSX.NET.WeatherRadar is started from startsim_slave.ps1, so it must
# connect to the SLAVE sim's router port.
$configPath = "$PsxNetConfigDir\PSX.NET.WeatherRadar.xml"
$xml = New-Object System.Xml.XmlDocument
$xml.Load($configPath)
$xml.SelectSingleNode("//PsxServerIP").InnerText = "127.0.0.1"
$xml.SelectSingleNode("//PsxServerPort").InnerText = "$FrankenrouterSlavePort"
$xml.Save($configPath)

Start-Process -WorkingDirectory $PsxNetWeatherRadarDir "$PsxNetWeatherRadarDir\PSX.NET.WeatherRadar.exe"
