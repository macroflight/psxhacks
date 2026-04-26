. "$PSScriptRoot\common.ps1"

cd $FrankenRouterDir

$Host.UI.RawUI.WindowTitle = "FrankenRouter SLAVE"

$env:PYTHONPATH = $PsxhacksDevel

# Use localhost PSX main server
& $PsxhacksPython "$PsxhacksDevel\router\frankenrouter.py" `
  --config-file=frankensim-client.toml
#  --enable-variable-stats

# Read-Host -Prompt "Press Enter to exit"
