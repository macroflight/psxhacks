. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenRouter MASTER"

Set-Location $FrankenRouterDir

$env:PYTHONPATH = $PsxhacksDevel

Invoke-WindowPosition "frankenrouter master"
& $PsxhacksPython "$PsxhacksDevel\router\frankenrouter.py" @FrankenrouterMasterOptions

# Read-Host -Prompt "Press Enter to exit"
