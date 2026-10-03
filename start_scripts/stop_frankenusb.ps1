. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Stop FrankenUSB"
KillPythonScript "frankenusb.py"
