. "$PSScriptRoot\common.ps1"

Set-Location $FrankenRouterDir

$Host.UI.RawUI.WindowTitle = "FrankenRouterIDENT"

$repo = Resolve-AddonRepo $FrankenidentRepo
$env:PYTHONPATH = $repo

# --psx-port-override forces the correct router port for the slave sim,
# regardless of any --psx-port set in $FrankenidentOptions.
Invoke-WindowPosition "frankenident"
& $PsxhacksPython "$repo\frankenrouter_ident.py" @FrankenidentOptions "--psx-port-override=$FrankenrouterSlavePort"
