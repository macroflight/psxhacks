. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenPUSH"
KillPythonScript "frankenpush.py"

$repo = Resolve-AddonRepo $FrankenpushRepo
$env:PYTHONPATH = $repo

# --psx-port-override forces the correct router port for the master sim,
# regardless of any --psx-port set in $FrankenpushOptions.
& $PsxhacksPython "$repo\frankenpush.py" @FrankenpushOptions "--psx-port-override=$FrankenrouterMasterPort"

# Read-Host -Prompt "Press Enter to exit"
