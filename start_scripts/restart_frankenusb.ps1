. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenUSB"
KillPythonScript "frankenusb.py"

Set-Location $FrankenusbDir

$repo = Resolve-AddonRepo $FrankenusbRepo
$env:PYTHONPATH = $repo

# --psx-port-override forces the correct router port for the slave sim,
# regardless of any --psx-port set in $FrankenusbOptions.
& $PsxhacksPython "$repo\frankenusb.py" @FrankenusbOptions "--psx-port-override=$FrankenrouterSlavePort"

# Read-Host -Prompt "Press Enter to exit"
