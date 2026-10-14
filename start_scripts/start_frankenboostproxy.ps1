. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenBoostProxy"

$repo = Resolve-AddonRepo $FrankenboostproxyRepo
$env:PYTHONPATH = $repo

# --psx-port-override forces the correct router port for the slave sim,
# regardless of any --psx-port set in $FrankenboostproxyOptions.
Invoke-WindowPosition "frankenboostproxy"
& $PsxhacksPython "$repo\frankenboostproxy.py" @FrankenboostproxyOptions "--psx-port-override=$FrankenrouterSlavePort"
