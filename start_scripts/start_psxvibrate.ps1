. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "PSXVibrate"

# PSXVibrate is started from startsim_slave.ps1, so it must connect to the
# SLAVE sim's router port.
$configPath = "$PsxNetConfigDir\PSX.NET.Vibrate.xml"
RequireConfigFile $configPath '$PsxNetConfigDir'
$xml = New-Object System.Xml.XmlDocument
$xml.Load($configPath)
$xml.SelectSingleNode("//PSXServerIP").InnerText = "127.0.0.1"
$xml.SelectSingleNode("//PSXPort").InnerText = "$FrankenrouterSlavePort"
$xml.Save($configPath)

Start-Process -WorkingDirectory $PsxVibrateDir "$PsxVibrateDir\PSX.NET.Vibrate.exe"
Invoke-WindowPosition "psxvibrate"
