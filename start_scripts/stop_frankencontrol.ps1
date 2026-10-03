. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Stop FrankenControl"
KillPythonScript "frankencontrol.py"
