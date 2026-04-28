. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Restart PSXSounds"
KillProcess "PSXSounds"

$configPath = "$PsxSoundsDir\Config.xml"
$xml = New-Object System.Xml.XmlDocument
$xml.Load($configPath)
$xml.SelectSingleNode("//RB211").InnerText = $PsxSoundsRb211
$xml.Save($configPath)

Start-Process -WorkingDirectory $PsxSoundsDir "$PsxSoundsDir\PSXSounds.exe"

#Read-Host -Prompt "Press Enter to exit"
