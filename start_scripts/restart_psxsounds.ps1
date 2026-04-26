. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Restart PSXSounds"
KillProcess "PSXSounds"


Start-Process -WorkingDirectory $PsxSoundsDir "$PsxSoundsDir\PSXSounds.exe"

#Read-Host -Prompt "Press Enter to exit"
