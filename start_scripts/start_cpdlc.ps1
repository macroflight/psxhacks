. "$PSScriptRoot\common.ps1"

cd $CpdlcDir

$Host.UI.RawUI.WindowTitle = "Hoppie PSX CPDLC"

$env:PYTHONPATH = $PsxhacksDevel

& $PsxhacksPython psx-acars.py --stealth --psx-port=10748 --min-interval=15 --max-interval=30 $HoppieLogonCode

# Read-Host -Prompt "Press Enter to exit"
