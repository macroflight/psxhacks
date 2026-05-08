. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Show USB"

$env:PYTHONPATH = $PsxhacksDevel
& $PsxhacksPython "$PsxhacksDevel\show_usb.py"
