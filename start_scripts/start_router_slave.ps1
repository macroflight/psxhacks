. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenRouter SLAVE"

Set-Location $FrankenRouterDir

$env:PYTHONPATH = $PsxhacksDevel

Invoke-WindowPosition "frankenrouter slave"
& $PsxhacksPython "$PsxhacksDevel\router\frankenrouter.py" @FrankenrouterslaveOptions

# Read-Host -Prompt "Press Enter to exit"
