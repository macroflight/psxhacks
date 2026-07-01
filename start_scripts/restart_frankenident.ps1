. "$PSScriptRoot\common.ps1"

Set-Location $FrankenRouterDir

$Host.UI.RawUI.WindowTitle = "FrankenRouterIDENT"
KillPythonScript "frankenrouter_ident.py"

$repo = Resolve-AddonRepo $FrankenidentRepo
$env:PYTHONPATH = $repo

& $PsxhacksPython "$repo\frankenrouter_ident.py" @FrankenidentOptions

# Read-Host -Prompt "Press Enter to exit"
