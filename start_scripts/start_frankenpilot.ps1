. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenPILOT"

$repo = Resolve-AddonRepo $FrankenpilotRepo
$env:PYTHONPATH = $repo

# --psx-port-override forces the correct router port for the master sim,
# regardless of any --psx-port set in $FrankenpilotOptions.
Invoke-WindowPosition "frankenpilot"
& $PsxhacksPython "$repo\frankenpilot.py" @FrankenpilotOptions "--psx-port-override=$FrankenrouterMasterPort"
