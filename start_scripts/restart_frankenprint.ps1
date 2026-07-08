. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenPrinter"
KillPythonScript "frankenprint.py"

$repo = Resolve-AddonRepo $FrankenprintRepo
$env:PYTHONPATH = $repo

# --psx-port-override forces the correct router port for the slave sim,
# regardless of any --psx-port set in $FrankenprintOptions.
& $PsxhacksPython "$repo\frankenprint.py" @FrankenprintOptions "--psx-port-override=$FrankenrouterSlavePort"

# Read-Host -Prompt "Press Enter to exit"
