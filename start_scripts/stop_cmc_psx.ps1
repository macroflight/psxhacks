. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Stop CMC-PSX"
KillJavaJar "$CmcPsxDir\CMC-PSX.jar"
