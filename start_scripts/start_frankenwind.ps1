. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenWind"
KillPythonScript "frankenwind.py"

cd $FrankenwindDir

$env:PYTHONPATH = $PsxhacksDevel

& $PsxhacksPython "$PsxhacksDevel\frankenwind.py" @FrankenwindOptions

# Read-Host -Prompt "Press Enter to exit"
