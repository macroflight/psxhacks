. "$PSScriptRoot\common.ps1"

cd $FrankenRouterDir

$Host.UI.RawUI.WindowTitle = "FrankenRouterIDENT"
KillPythonScript "frankenrouter_ident.py"

$env:PYTHONPATH = $PsxhacksDevel

& $PsxhacksPython "$PsxhacksDevel\frankenrouter_ident.py" @FrankenidentOptions

# Read-Host -Prompt "Press Enter to exit"
