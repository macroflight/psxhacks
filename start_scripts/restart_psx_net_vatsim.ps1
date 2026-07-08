. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "PSX.NET.VATSIM"
KillProcess "GeoVR.PSX.Client.Wpf"

# PSX.NET.VATSIM is started from startsim_slave.ps1, so it must connect to
# the SLAVE sim's router port.
$configPath = "$PsxNetConfigDir\PSX.NET.VATSIM.xml"
$xml = New-Object System.Xml.XmlDocument
$xml.Load($configPath)
$xml.SelectSingleNode("//PsxIP").InnerText = "127.0.0.1"
$xml.SelectSingleNode("//PsxPort").InnerText = "$FrankenrouterSlavePort"
$xml.Save($configPath)

Start-Process -WorkingDirectory $PsxNetVatsimDir "$PsxNetVatsimDir\GeoVR.PSX.Client.Wpf.exe"
