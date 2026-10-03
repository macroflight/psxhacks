. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenCDUProxy"

$repo = Resolve-AddonRepo $FrankencduproxyRepo
$env:PYTHONPATH = $repo

# --psx-port-override forces the correct router port for the slave sim,
# regardless of any --psx-port set in $FrankencduproxyOptions.
Invoke-WindowPosition "frankencduproxy"
& $PsxhacksPython "$repo\frankencduproxy.py" @FrankencduproxyOptions "--psx-port-override=$FrankenrouterSlavePort"
