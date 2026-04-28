. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenUSB"
KillPythonScript "frankenusb.py"

cd $FrankenUsbDir

$env:PYTHONPATH = $PsxhacksDevel

& $PsxhacksPython "$PsxhacksDevel\frankenusb.py" $FrankenUsbOptions

# Read-Host -Prompt "Press Enter to exit"
