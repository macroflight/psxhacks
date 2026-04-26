. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenFREEZE"

cd $FrankenFreezeDir

$env:PYTHONPATH = $PsxhacksDevel

& $PsxhacksPython "$PsxhacksDevel\frankenfreeze.py"

# Read-Host -Prompt "Press Enter to exit"
