. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenCDUProxy"
KillPythonScript "frankencduproxy.py"

$repo = Resolve-AddonRepo $FrankencduproxyRepo
$env:PYTHONPATH = $repo

& $PsxhacksPython "$repo\frankencduproxy.py" @FrankencduproxyOptions

# Read-Host -Prompt "Press Enter to exit"
