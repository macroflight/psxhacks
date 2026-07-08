. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Restart BACARS"
KillProcess "PSX.Bacars.UI"

$configPath = "$BacarsDir\PSX.Bacars.UI.exe.Config"
$xml = New-Object System.Xml.XmlDocument
$xml.Load($configPath)
foreach ($node in $xml.configuration.appSettings.add) {
    switch ($node.key) {
        "AirlineCode"     { $node.value = $AirlineIata }
        "LongAirlineCode" { $node.value = $AirlineIcao }
        "CloudUserName"   { $node.value = $SimfestEmail }
        "ACARSLogonCode"  { $node.value = $HoppieLogonCode }
        # BACARS always runs on the same host as frankenrouter and the PSX
        # main server, so the server address is always the loopback
        # address. The port must match the MASTER sim's PSX port, since
        # BACARS is started from startsim_master.ps1.
        "ServerAddress"   { $node.value = "127.0.0.1" }
        "Port"            { $node.value = "$FrankenrouterMasterPort" }
    }
}
$xml.Save($configPath)

Start-Process -WorkingDirectory $BacarsDir "$BacarsDir\PSX.Bacars.UI.exe"
#Read-Host -Prompt "Press Enter to exit"
