. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Restart MSFS Client"
taskkill /im PSX.NET.MSFS2024.Client.exe /t /f
& "$PsxNetMsfsClientDir\PSX.NET.MSFS2024.Client.exe"

# Read-Host -Prompt "Press Enter to exit"
