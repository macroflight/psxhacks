. "$PSScriptRoot\common.ps1"

Set-Location $FrankenRouterDir

$Host.UI.RawUI.WindowTitle = "FrankenRouterIDENT"
KillPythonScript "frankenrouter_ident.py"

$repo = Resolve-AddonRepo $FrankenidentRepo
$env:PYTHONPATH = $repo

# --psx-port-override forces the correct router port for the slave sim,
# regardless of any --psx-port set in $FrankenidentOptions.
& $PsxhacksPython "$repo\frankenrouter_ident.py" @FrankenidentOptions "--psx-port-override=$FrankenrouterSlavePort"

# Read-Host -Prompt "Press Enter to exit"
