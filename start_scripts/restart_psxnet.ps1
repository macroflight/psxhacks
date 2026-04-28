. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Restart PSX.NET"
KillProcess "PSX.NET"

Start-Process -WorkingDirectory $PsxNetDir "$PsxNetDir\PSX.NET.exe"

#Read-Host -Prompt "Press Enter to exit"
