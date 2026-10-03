. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Stop SRSL-PSX (master)"
KillJavaJar "$SrslPsxMasterDir\SRSL-PSX.jar"
