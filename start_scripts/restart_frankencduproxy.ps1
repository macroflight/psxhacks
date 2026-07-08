. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenCDUProxy"
KillPythonScript "frankencduproxy.py"

$repo = Resolve-AddonRepo $FrankencduproxyRepo
$env:PYTHONPATH = $repo

# --psx-port-override forces the correct router port for the slave sim,
# regardless of any --psx-port set in $FrankencduproxyOptions.
& $PsxhacksPython "$repo\frankencduproxy.py" @FrankencduproxyOptions "--psx-port-override=$FrankenrouterSlavePort"

# Read-Host -Prompt "Press Enter to exit"
