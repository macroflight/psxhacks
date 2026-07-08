. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "PSX.NET.GroundCrew"
KillProcess "PSX.NET.GroundCrew"

# PSX.NET.GroundCrew is started from startsim_slave.ps1, so it must connect
# to the SLAVE sim's router port.
$configPath = "$PsxNetConfigDir\PSX.NET.GroundCrew.xml"
$xml = New-Object System.Xml.XmlDocument
$xml.Load($configPath)
$xml.SelectSingleNode("//PsxIpAddress").InnerText = "127.0.0.1"
$xml.SelectSingleNode("//PsxPort").InnerText = "$FrankenrouterSlavePort"
$xml.Save($configPath)

Start-Process -WorkingDirectory $PsxNetGroundCrewDir "$PsxNetGroundCrewDir\PSX.NET.GroundCrew.exe"
