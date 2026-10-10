. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Stop CPFlight FS_COM"
KillProcess "FS_COM_PSX_747"
