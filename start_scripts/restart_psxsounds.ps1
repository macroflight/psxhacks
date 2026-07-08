. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Restart PSXSounds"
KillProcess "PSXSounds"

$configPath = "$PsxSoundsDir\Config.xml"
$xml = New-Object System.Xml.XmlDocument
$xml.Load($configPath)
$xml.SelectSingleNode("//RB211").InnerText = $PsxSoundsRb211
# PSXSounds is started from startsim_slave.ps1, so it must connect to the
# SLAVE sim's router port.
$xml.SelectSingleNode("//PSXServerIP").InnerText = "127.0.0.1"
$xml.SelectSingleNode("//PSXPort").InnerText = "$FrankenrouterSlavePort"
$xml.Save($configPath)

Start-Process -WorkingDirectory $PsxSoundsDir "$PsxSoundsDir\PSXSounds.exe"

#Read-Host -Prompt "Press Enter to exit"
