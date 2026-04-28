. "$PSScriptRoot\common.ps1"

cd $FrankenRouterDir

$Host.UI.RawUI.WindowTitle = "FrankenRouter SLAVE"

$env:PYTHONPATH = $PsxhacksDevel

& $PsxhacksPython "$PsxhacksDevel\router\frankenrouter.py" $FrankenRouterSlaveOptions

# Read-Host -Prompt "Press Enter to exit"
