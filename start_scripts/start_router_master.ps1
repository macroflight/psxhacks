. "$PSScriptRoot\common.ps1"

cd $FrankenRouterDir

$Host.UI.RawUI.WindowTitle = "FrankenRouter MASTER"

$env:PYTHONPATH = $PsxhacksDevel

# Use localhost PSX main server
& $PsxhacksPython "$PsxhacksDevel\router\frankenrouter.py" `
  --config-file=frankensim-core.toml --housekeeping-interval=10

# Read-Host -Prompt "Press Enter to exit"
