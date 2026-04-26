. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenUSB"

cd $FrankenUsbDir

$env:PYTHONPATH = $PsxhacksDevel

& $PsxhacksPython "$PsxhacksDevel\frankenusb.py" --config-file=frankenusb-frankensim.conf


# Read-Host -Prompt "Press Enter to exit"
