. "$PSScriptRoot\common.ps1"


$Host.UI.RawUI.WindowTitle = "SRSL-PSX (master)"
KillJavaJar "$SrslPsxMasterDir\SRSL-PSX.jar"

Start-Process -WindowStyle hidden -WorkingDirectory $SrslPsxMasterDir -FilePath java -ArgumentList "-jar", "$SrslPsxMasterDir\SRSL-PSX.jar" -RedirectStandardOutput "$SrslPsxMasterDir\console.out" -RedirectStandardError "$SrslPsxMasterDir\console.err"



