. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenWEATHER"
KillPythonScript "frankenweather.py"

$repo = Resolve-AddonRepo $FrankenweatherRepo
$env:PYTHONPATH = $repo

& $PsxhacksPython "$repo\frankenweather.py" @FrankenweatherOptions

# Read-Host -Prompt "Press Enter to exit"
