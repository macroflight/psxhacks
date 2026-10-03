. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "SimObjectRouter"

Start-Process -WorkingDirectory $SimObjectRouterDir "$SimObjectRouterDir\PSX.NET.MSFS.Temporary.SimObjectRouter.exe"
Delay 5
Invoke-WindowPosition "SimObjectRouter"
