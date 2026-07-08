. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Restart MSFS Router"
KillProcess "PSX.NET.MSFS.Router"

# PSX.NET.MSFS.Router is started from startsim_slave.ps1, so it must connect
# to the SLAVE sim's router port.
$configPath = "$PsxNetConfigDir\PSX.NET.MSFS.Router.xml"
$xml = New-Object System.Xml.XmlDocument
$xml.Load($configPath)
$xml.SelectSingleNode("//PsxServerIP").InnerText = "127.0.0.1"
$xml.SelectSingleNode("//PsxServerPort").InnerText = "$FrankenrouterSlavePort"
$xml.Save($configPath)

& "$PsxNetMsfsRouterDir\PSX.NET.MSFS.Router.exe"
