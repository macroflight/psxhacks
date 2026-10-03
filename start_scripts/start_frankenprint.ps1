. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenPrinter"

$repo = Resolve-AddonRepo $FrankenprintRepo
$env:PYTHONPATH = $repo

# --psx-port-override forces the correct router port for the slave sim,
# regardless of any --psx-port set in $FrankenprintOptions.
Invoke-WindowPosition "frankenprint"
& $PsxhacksPython "$repo\frankenprint.py" @FrankenprintOptions "--psx-port-override=$FrankenrouterSlavePort"
