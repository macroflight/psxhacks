. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenPrinter"
KillPythonScript "frankenprint.py"

$repo = Resolve-AddonRepo $FrankenprintRepo
$env:PYTHONPATH = $repo

& $PsxhacksPython "$repo\frankenprint.py" @FrankenprintOptions

# Read-Host -Prompt "Press Enter to exit"
