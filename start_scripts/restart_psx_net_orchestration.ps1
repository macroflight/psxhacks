. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Restart PSX.NET.Orchestration"
KillProcess "PSX.NET.Orchestration"

# PSX.NET.Orchestration is started from startsim_slave.ps1 (it runs in
# slave sims only, not the master sim), and is mutually exclusive with
# PSX.NET and PSX.NET.GroundCrew (see the checks in common.ps1), so it must
# connect to the SLAVE sim's router port.
$configPath = "$PsxNetConfigDir\PSX.NET.Orchestration.Config.xml"
$xml = New-Object System.Xml.XmlDocument
$xml.Load($configPath)
$xml.SelectSingleNode("//PsxHost").InnerText = "127.0.0.1"
$xml.SelectSingleNode("//PsxPort").InnerText = "$FrankenrouterSlavePort"
$xml.Save($configPath)

Start-Process -WorkingDirectory $PsxNetOrchestrationDir "$PsxNetOrchestrationDir\PSX.NET.Orchestration.exe"

#Read-Host -Prompt "Press Enter to exit"
