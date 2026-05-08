. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenRouter MASTER"

cd $FrankenRouterDir

$env:PYTHONPATH = $PsxhacksDevel

& $PsxhacksPython "$PsxhacksDevel\router\frankenrouter.py" @FrankenrouterMasterOptions

# Read-Host -Prompt "Press Enter to exit"
