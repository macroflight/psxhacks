. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Stop FrankenBoostProxy"
KillPythonScript "frankenboostproxy.py"
