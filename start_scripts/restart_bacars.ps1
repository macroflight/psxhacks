. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Restart BACARS"
taskkill /im PSX.Bacars.UI.exe /t /f

Start-Process -WorkingDirectory $BacarsDir "$BacarsDir\PSX.Bacars.UI.exe"
#Read-Host -Prompt "Press Enter to exit"
