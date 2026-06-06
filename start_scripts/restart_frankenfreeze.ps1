. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenFREEZE"
KillPythonScript "frankenfreeze.py"

$repo = Resolve-AddonRepo $FrankenfreezeRepo
$env:PYTHONPATH = $repo

& $PsxhacksPython "$repo\frankenfreeze.py" @FrankenfreezeOptions

# Read-Host -Prompt "Press Enter to exit"
