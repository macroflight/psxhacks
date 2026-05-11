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
    }
}
$xml.Save($configPath)

Start-Process -WorkingDirectory $BacarsDir "$BacarsDir\PSX.Bacars.UI.exe"
#Read-Host -Prompt "Press Enter to exit"
Invoke-WindowPosition "BACARS"
