. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenMSFSBridge"
KillPythonScript "frankenmsfsbridge.py"

$repo = Resolve-AddonRepo $FrankenmsfsbridgeRepo
$env:PYTHONPATH = $repo

& $PsxhacksPython "$repo\frankenmsfsbridge.py" @FrankenmsfsbridgeOptions

# Read-Host -Prompt "Press Enter to exit"
