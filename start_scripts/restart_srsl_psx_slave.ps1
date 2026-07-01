. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "SRSL-PSX (slave)"
KillJavaJar "$SrslPsxSlaveDir\SRSL-PSX.jar"

Start-Process -WindowStyle hidden -WorkingDirectory $SrslPsxSlaveDir -FilePath java -ArgumentList "-jar", "$SrslPsxSlaveDir\SRSL-PSX.jar" -RedirectStandardOutput "$SrslPsxSlaveDir\console.out" -RedirectStandardError "$SrslPsxSlaveDir\console.err"



