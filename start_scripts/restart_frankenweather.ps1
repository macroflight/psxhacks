. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenWEATHER"
KillPythonScript "frankenweather.py"

$repo = Resolve-AddonRepo $FrankenweatherRepo
$env:PYTHONPATH = $repo

# --psx-port-override forces the correct router port for the master sim,
# regardless of any --psx-port set in $FrankenweatherOptions.
& $PsxhacksPython "$repo\frankenweather.py" @FrankenweatherOptions "--psx-port-override=$FrankenrouterMasterPort"

# Read-Host -Prompt "Press Enter to exit"
