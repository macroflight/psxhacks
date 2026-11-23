. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Stop FrankenStutter"
KillPythonScript "frankenstutter.py"
