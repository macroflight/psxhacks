. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Start PSX.NET.EFB"

$configPath = "$PsxNetEfbConfigDir\PSX.NET.EFB.Windows.Config.xml"
$xml = New-Object System.Xml.XmlDocument
$xml.Load($configPath)
# PSX.NET.EFB is started from startsim_slave.ps1, so it must connect to the
# SLAVE sim's router port.
$xml.SelectSingleNode("//psxIP").InnerText = "127.0.0.1"
$xml.SelectSingleNode("//psxPort").InnerText = "$FrankenrouterSlavePort"

# RouterControlPageURL must point at the SLAVE router's web UI. Older
# configs may not have this element yet, so create it if missing.
$routerUrlNode = $xml.SelectSingleNode("//RouterControlPageURL")
if ($null -eq $routerUrlNode) {
    $routerUrlNode = $xml.CreateElement("RouterControlPageURL")
    $xml.DocumentElement.AppendChild($routerUrlNode) | Out-Null
}
$routerUrlNode.InnerText = $FrankenrouterSlaveWeb

$xml.Save($configPath)

KillProcess "PSX.NET.EFB.Windows"

Start-Process -WorkingDirectory $PsxNetEfbDir "$PsxNetEfbDir\PSX.NET.EFB.Windows.exe"

#Read-Host -Prompt "Press Enter to exit"
