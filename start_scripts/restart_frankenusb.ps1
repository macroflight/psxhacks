. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenUSB"
KillPythonScript "frankenusb.py"

cd $FrankenusbDir

$env:PYTHONPATH = $PsxhacksDevel

& $PsxhacksPython "$PsxhacksDevel\frankenusb.py" @FrankenusbOptions

# Read-Host -Prompt "Press Enter to exit"
