. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenTURB"
KillPythonScript "frankenturb.py"

cd $FrankenTurbDir

$env:PYTHONPATH = $PsxhacksDevel

& $PsxhacksPython "$PsxhacksDevel\frankenturb\frankenturb.py" @FrankenTurbOptions

# Read-Host -Prompt "Press Enter to exit"
