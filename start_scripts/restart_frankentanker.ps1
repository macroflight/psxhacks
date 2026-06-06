. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenTanker"
KillPythonScript "frankentanker.py"

$repo = Resolve-AddonRepo $FrankentankerRepo
$env:PYTHONPATH = $repo

& $PsxhacksPython "$repo\frankentanker.py" @FrankentankerOptions

# Read-Host -Prompt "Press Enter to exit"
