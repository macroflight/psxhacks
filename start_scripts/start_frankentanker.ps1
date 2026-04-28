. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenTanker"
KillPythonScript "frankentanker.py"

cd $FrankenTankerDir

$env:PYTHONPATH = $PsxhacksDevel

& $PsxhacksPython "$PsxhacksDevel\frankentanker.py"

# Read-Host -Prompt "Press Enter to exit"
