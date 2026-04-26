. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Restart PSX.NET"
taskkill /im PSX.NET.exe /t /f

Start-Process -WorkingDirectory $PsxNetDir "$PsxNetDir\PSX.NET.exe"

#Read-Host -Prompt "Press Enter to exit"
