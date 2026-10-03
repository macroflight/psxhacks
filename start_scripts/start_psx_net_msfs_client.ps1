. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Restart MSFS Client"

Invoke-WindowPosition "PSX.NET.MSFS"
& "$PsxNetMsfsClientDir\PSX.NET.MSFS2024.Client.exe"
