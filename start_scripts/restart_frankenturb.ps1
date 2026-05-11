. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenTURB"
KillPythonScript "frankenturb.py"

$env:PYTHONPATH = $PsxhacksDevel

Invoke-WindowPosition "frankenturb"
& $PsxhacksPython "$PsxhacksDevel\frankenturb\frankenturb.py" @FrankenturbOptions

# Read-Host -Prompt "Press Enter to exit"
