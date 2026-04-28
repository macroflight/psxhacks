. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenWind"
KillPythonScript "frankenwind.py"

cd $FrankenWindDir

$env:PYTHONPATH = $PsxhacksDevel

& $PsxhacksPython "$PsxhacksDevel\frankenwind.py"

# Read-Host -Prompt "Press Enter to exit"
