. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenMSFSBridge"
KillPythonScript "frankenmsfsbridge.py"

$repo = Resolve-AddonRepo $FrankenmsfsbridgeRepo
$env:PYTHONPATH = $repo

# --psx-port-override forces the correct router port for the slave sim,
# regardless of any --psx-port set in $FrankenmsfsbridgeOptions.
& $PsxhacksPython "$repo\frankenmsfsbridge.py" @FrankenmsfsbridgeOptions "--psx-port-override=$FrankenrouterSlavePort"

# Read-Host -Prompt "Press Enter to exit"
