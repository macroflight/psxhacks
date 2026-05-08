. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenCDUProxy"
KillPythonScript "frankencduproxy.py"

$env:PYTHONPATH = $PsxhacksDevel

& $PsxhacksPython "$PsxhacksDevel\frankencduproxy.py" @FrankencduproxyOptions

# Read-Host -Prompt "Press Enter to exit"
