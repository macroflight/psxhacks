. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Stop FrankenWEATHER"
KillPythonScript "frankenweather.py"
