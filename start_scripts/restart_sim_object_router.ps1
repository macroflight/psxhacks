. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "SimObjectRouter"
KillProcess "PSX.NET.MSFS.Temporary.SimObjectRouter"

Start-Process -WorkingDirectory $SimObjectRouterDir "$SimObjectRouterDir\PSX.NET.MSFS.Temporary.SimObjectRouter.exe"
