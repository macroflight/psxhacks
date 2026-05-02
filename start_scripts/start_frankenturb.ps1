. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenTURB"
KillPythonScript "frankenturb.py"

cd $FrankenturbDir

$env:PYTHONPATH = $PsxhacksDevel

& $PsxhacksPython "$PsxhacksDevel\frankenturb\frankenturb.py" @FrankenturbOptions

# Read-Host -Prompt "Press Enter to exit"
