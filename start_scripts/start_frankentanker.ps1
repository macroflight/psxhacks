. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenTanker"

$repo = Resolve-AddonRepo $FrankentankerRepo
$env:PYTHONPATH = $repo

# --psx-port-override forces the correct router port for the master sim,
# regardless of any --psx-port set in $FrankentankerOptions.
Invoke-WindowPosition "frankentanker"
& $PsxhacksPython "$repo\frankentanker.py" @FrankentankerOptions "--psx-port-override=$FrankenrouterMasterPort"
