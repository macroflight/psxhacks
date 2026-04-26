. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Restart MSFS Router"
taskkill /im PSX.NET.MSFS.Router.exe /t /f

& "$PsxNetMsfsRouterDir\PSX.NET.MSFS.Router.exe"

# Read-Host -Prompt "Press Enter to exit"
