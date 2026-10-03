. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Stop FrankenCDUProxy"
KillPythonScript "frankencduproxy.py"
