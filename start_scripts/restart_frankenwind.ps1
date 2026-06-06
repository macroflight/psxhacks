. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenWind"
KillPythonScript "frankenwind.py"

$repo = Resolve-AddonRepo $FrankenwindRepo
$env:PYTHONPATH = $repo

& $PsxhacksPython "$repo\frankenwind.py" @FrankenwindOptions

# Read-Host -Prompt "Press Enter to exit"
