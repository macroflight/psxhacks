. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenUTIL"
KillPythonScript "frankenutil.py"

$repo = Resolve-AddonRepo $FrankenutilRepo
$env:PYTHONPATH = $repo

& $PsxhacksPython "$repo\frankenutil.py" @FrankenutilOptions

# Read-Host -Prompt "Press Enter to exit"
