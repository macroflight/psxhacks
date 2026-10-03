. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenControl"

$repo = Resolve-AddonRepo $FrankencontrolRepo
$env:PYTHONPATH = $repo

# --psx-port-override forces the correct router port for the master sim,
# regardless of any --psx-port set in $FrankencontrolOptions.
Invoke-WindowPosition "frankencontrol"
& $PsxhacksPython "$repo\frankencontrol.py" @FrankencontrolOptions "--psx-port-override=$FrankenrouterMasterPort"
