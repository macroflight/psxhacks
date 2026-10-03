. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenUSB"

Set-Location $FrankenusbDir

$repo = Resolve-AddonRepo $FrankenusbRepo
$env:PYTHONPATH = $repo

# --psx-port-override forces the correct router port for the slave sim,
# regardless of any --psx-port set in $FrankenusbOptions.
Invoke-WindowPosition "frankenusb"
& $PsxhacksPython "$repo\frankenusb.py" @FrankenusbOptions "--psx-port-override=$FrankenrouterSlavePort"
