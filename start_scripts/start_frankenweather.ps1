. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenWEATHER"

$repo = Resolve-AddonRepo $FrankenweatherRepo
$env:PYTHONPATH = $repo

# --psx-port-override forces the correct router port for the master sim,
# regardless of any --psx-port set in $FrankenweatherOptions.
Invoke-WindowPosition "frankenweather"
& $PsxhacksPython "$repo\frankenweather.py" @FrankenweatherOptions "--psx-port-override=$FrankenrouterMasterPort"
