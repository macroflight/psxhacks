. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Stop SRSL-PSX (slave)"
KillJavaJar "$SrslPsxSlaveDir\SRSL-PSX.jar"
