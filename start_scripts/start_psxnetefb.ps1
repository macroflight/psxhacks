. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Start PSX.NET.EFB"

Start-Process -WorkingDirectory $PsxNetEfbDir "$PsxNetEfbDir\PSX.NET.EFB.Windows.exe"

#Read-Host -Prompt "Press Enter to exit"
