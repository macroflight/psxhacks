. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Stop FrankenRouterIDENT"
KillPythonScript "frankenrouter_ident.py"
