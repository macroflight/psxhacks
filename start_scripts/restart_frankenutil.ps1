. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenUTIL"
KillPythonScript "frankenutil.py"

$env:PYTHONPATH = $PsxhacksDevel

Invoke-WindowPosition "frankenutil"
& $PsxhacksPython "$PsxhacksDevel\frankenutil.py" @FrankenutilOptions

# Read-Host -Prompt "Press Enter to exit"
