. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Restart MSFS Router"
KillProcess "PSX.NET.MSFS.Router"

Invoke-WindowPosition "PSX.NET.MSFS.Router"
& "$PsxNetMsfsRouterDir\PSX.NET.MSFS.Router.exe"
