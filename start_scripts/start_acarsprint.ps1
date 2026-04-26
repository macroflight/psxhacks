. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "ACARS Print"

Start-Process java -ArgumentList "-jar", "AcarsPrint.jar" -WorkingDirectory $AcarsPrintDir -WindowStyle Hidden
