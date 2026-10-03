. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenPUSH"

$repo = Resolve-AddonRepo $FrankenpushRepo
$env:PYTHONPATH = $repo

# --psx-port-override forces the correct router port for the master sim,
# regardless of any --psx-port set in $FrankenpushOptions.
Invoke-WindowPosition "frankenpush"
& $PsxhacksPython "$repo\frankenpush.py" @FrankenpushOptions "--psx-port-override=$FrankenrouterMasterPort"
