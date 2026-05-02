. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenUTIL"
KillPythonScript "frankenutil.py"

cd $FrankenutilDir

$env:PYTHONPATH = $PsxhacksDevel

& $PsxhacksPython "$PsxhacksDevel\frankenutil.py" @FrankenutilOptions

# Read-Host -Prompt "Press Enter to exit"
