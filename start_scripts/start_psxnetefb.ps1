. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Start PSX.NET.EFB"

$configPath = "$PsxNetEfbConfigDir\PSX.NET.EFB.Windows.Config.xml"
$xml = New-Object System.Xml.XmlDocument
$xml.Load($configPath)
$xml.SelectSingleNode("//AirlineCode").InnerText    = $AirlineIcao
$xml.SelectSingleNode("//PlanningPortalEmail").InnerText = $SimfestEmail
$xml.Save($configPath)

KillProcess "PSX.NET.EFB.Windows"

Start-Process -WorkingDirectory $PsxNetEfbDir "$PsxNetEfbDir\PSX.NET.EFB.Windows.exe"

#Read-Host -Prompt "Press Enter to exit"
