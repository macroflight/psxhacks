. "$PSScriptRoot\common.ps1"

Set-Location $FrankenRouterDir

$Host.UI.RawUI.WindowTitle = "FrankenRouterIDENT Master"
KillPythonScript "frankenrouter_ident.py"

$repo = Resolve-AddonRepo $FrankenidentMasterRepo
$env:PYTHONPATH = $repo

& $PsxhacksPython "$repo\frankenrouter_ident.py" @FrankenidentMasterOptions

# Read-Host -Prompt "Press Enter to exit"
