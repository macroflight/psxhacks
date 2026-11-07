. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Stop FrankenPILOT"
KillPythonScript "frankenpilot.py"
