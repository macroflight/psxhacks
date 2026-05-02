. "$PSScriptRoot\common.ps1"

cd $FrankenRouterDir

$Host.UI.RawUI.WindowTitle = "FrankenRouter MASTER"

$env:PYTHONPATH = $PsxhacksDevel

& $PsxhacksPython "$PsxhacksDevel\router\frankenrouter.py" @FrankenrouterMasterOptions

# Read-Host -Prompt "Press Enter to exit"
