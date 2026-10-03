. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Stop FrankenPrinter"
KillPythonScript "frankenprint.py"
