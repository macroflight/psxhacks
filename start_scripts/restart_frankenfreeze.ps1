. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenFREEZE"
KillPythonScript "frankenfreeze.py"

$env:PYTHONPATH = $PsxhacksDevel

Invoke-WindowPosition "frankenfreeze"
& $PsxhacksPython "$PsxhacksDevel\frankenfreeze.py" @FrankenfreezeOptions

# Read-Host -Prompt "Press Enter to exit"
