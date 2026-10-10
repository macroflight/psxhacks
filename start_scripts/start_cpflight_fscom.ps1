. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "CPFlight FS_COM"

Start-Process -WorkingDirectory $CpflightFsComDir "$CpflightFsComDir\FS_COM_PSX_747.EXE"
Invoke-WindowPosition "CPFlight FS_COM"
