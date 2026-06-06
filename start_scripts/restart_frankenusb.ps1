. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenUSB"
KillPythonScript "frankenusb.py"

Set-Location $FrankenusbDir

$repo = Resolve-AddonRepo $FrankenusbRepo
$env:PYTHONPATH = $repo

& $PsxhacksPython "$repo\frankenusb.py" @FrankenusbOptions

# Read-Host -Prompt "Press Enter to exit"
