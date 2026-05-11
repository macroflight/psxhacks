. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenWind"
KillPythonScript "frankenwind.py"

$env:PYTHONPATH = $PsxhacksDevel

Invoke-WindowPosition "frankenwind"
& $PsxhacksPython "$PsxhacksDevel\frankenwind.py" @FrankenwindOptions

# Read-Host -Prompt "Press Enter to exit"
