. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenUTIL"
KillPythonScript "frankenutil.py"

cd $FrankenUtilDir

$env:PYTHONPATH = $PsxhacksDevel

& $PsxhacksPython "$PsxhacksDevel\frankenutil.py" --psx-main-server-port=10748 --cdus=L,R --menu-row=6

# Read-Host -Prompt "Press Enter to exit"
