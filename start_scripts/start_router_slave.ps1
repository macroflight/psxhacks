. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenRouter SLAVE"

cd $FrankenRouterDir

$env:PYTHONPATH = $PsxhacksDevel

& $PsxhacksPython "$PsxhacksDevel\router\frankenrouter.py" @FrankenrouterslaveOptions

# Read-Host -Prompt "Press Enter to exit"
