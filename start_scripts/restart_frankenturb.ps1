. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenTURB"
KillPythonScript "frankenturb.py"

$repo = Resolve-AddonRepo $FrankenturbRepo
$env:PYTHONPATH = $repo

& $PsxhacksPython "$repo\frankenturb\frankenturb.py" @FrankenturbOptions

# Read-Host -Prompt "Press Enter to exit"
