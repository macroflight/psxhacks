. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Restart PSX.NET.GroundHandling"
KillProcess "PSX.NET.GroundHandling"

# PSX.NET.GroundHandling is started from startsim_slave.ps1 (it runs in
# slave sims only, not the master sim), and is mutually exclusive with
# PSX.NET and PSX.NET.GroundCrew (see the checks in common.ps1), so it must
# connect to the SLAVE sim's router port.
$configPath = "$PsxNetConfigDir\PSX.NET.GroundHandling.Config.xml"
$xml = New-Object System.Xml.XmlDocument
$xml.Load($configPath)
$xml.SelectSingleNode("//PsxHost").InnerText = "127.0.0.1"
$xml.SelectSingleNode("//PsxPort").InnerText = "$FrankenrouterSlavePort"
$xml.Save($configPath)

Start-Process -WorkingDirectory $PsxNetGroundHandlingDir "$PsxNetGroundHandlingDir\PSX.NET.GroundHandling.exe"

#Read-Host -Prompt "Press Enter to exit"
