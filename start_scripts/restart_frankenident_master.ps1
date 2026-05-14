. "$PSScriptRoot\common.ps1"

Set-Location $FrankenRouterDir

$Host.UI.RawUI.WindowTitle = "FrankenRouterIDENT Master"
KillPythonScript "frankenrouter_ident.py"

$env:PYTHONPATH = $PsxhacksDevel

& $PsxhacksPython "$PsxhacksDevel\frankenrouter_ident.py" @FrankenidentMasterOptions

# Read-Host -Prompt "Press Enter to exit"
