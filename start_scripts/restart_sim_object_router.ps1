. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "SimObjectRouter"
KillProcess "SimObjectRouter"

Start-Process -WorkingDirectory $SimObjectRouterDir "$SimObjectRouterDir\SimObjectRouter.exe"
