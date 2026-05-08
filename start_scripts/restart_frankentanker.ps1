. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenTanker"
KillPythonScript "frankentanker.py"

$env:PYTHONPATH = $PsxhacksDevel

& $PsxhacksPython "$PsxhacksDevel\frankentanker.py" @FrankentankerOptions

# Read-Host -Prompt "Press Enter to exit"
