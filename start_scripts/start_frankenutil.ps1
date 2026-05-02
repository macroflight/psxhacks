. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenUTIL"
KillPythonScript "frankenutil.py"

cd $FrankenUtilDir

$env:PYTHONPATH = $PsxhacksDevel

& $PsxhacksPython "$PsxhacksDevel\frankenutil.py" @$FrankenUtilOptions

# Read-Host -Prompt "Press Enter to exit"
