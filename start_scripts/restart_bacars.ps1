. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Restart BACARS"
KillProcess "PSX.Bacars.UI"

# BACARS (now part of the PSX.NET suite) keeps its config alongside the
# other PSX.NET.*.xml files rather than next to its own .exe.
$configPath = "$PsxNetConfigDir\PSX.NET.BACARS.xml"
$xml = New-Object System.Xml.XmlDocument
$xml.Load($configPath)
$xml.SelectSingleNode("//HoppieLogon").InnerText = $HoppieLogonCode
# BACARS always runs on the same host as frankenrouter and the PSX
# main server, so the server address is always the loopback address.
# The port must match the MASTER sim's PSX port, since BACARS is
# started from startsim_master.ps1.
$xml.SelectSingleNode("//ServerAddress").InnerText = "127.0.0.1"
$xml.SelectSingleNode("//ServerPort").InnerText    = "$FrankenrouterMasterPort"
$xml.Save($configPath)

Start-Process -WorkingDirectory $BacarsDir "$BacarsDir\PSX.Bacars.UI.exe"
#Read-Host -Prompt "Press Enter to exit"
