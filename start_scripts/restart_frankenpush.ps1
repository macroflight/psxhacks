. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenPUSH"
KillPythonScript "frankenpush.py"

$repo = Resolve-AddonRepo $FrankenpushRepo
$env:PYTHONPATH = $repo

& $PsxhacksPython "$repo\frankenpush.py" @FrankenpushOptions

# Read-Host -Prompt "Press Enter to exit"
