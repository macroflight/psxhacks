. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Stop FrankenTanker"
KillPythonScript "frankentanker.py"
