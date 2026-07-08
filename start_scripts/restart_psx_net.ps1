. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Restart PSX.NET"
KillProcess "PSX.NET"

# PSX.NET is started from startsim_master.ps1, so it must connect to the
# MASTER sim's router port on the local machine (where frankenrouter and the
# PSX main server also run).
$configPath = "$PsxNetDir\Settings\PSX.NET.xml"
$xml = New-Object System.Xml.XmlDocument
$xml.Load($configPath)
$xml.Settings.PsxServerIP = "127.0.0.1"
$xml.Settings.PsxServerPort = "$FrankenrouterMasterPort"
$xml.Save($configPath)

Start-Process -WorkingDirectory $PsxNetDir "$PsxNetDir\PSX.NET.exe"

#Read-Host -Prompt "Press Enter to exit"
