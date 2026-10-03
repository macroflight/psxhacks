. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Stop FrankenPUSH"
KillPythonScript "frankenpush.py"
