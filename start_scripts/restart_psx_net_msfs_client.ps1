. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Restart MSFS Client"
KillProcess "PSX.NET.MSFS2024.Client"
& "$PsxNetMsfsClientDir\PSX.NET.MSFS2024.Client.exe"
