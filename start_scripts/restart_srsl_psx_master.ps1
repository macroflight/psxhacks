. "$PSScriptRoot\common.ps1"


$Host.UI.RawUI.WindowTitle = "SRSL-PSX (master)"
KillJavaJar "$SrslPsxMasterDir\SRSL-PSX.jar"

# SRSL-PSX (master) is started from startsim_master.ps1, so it must connect
# to the MASTER sim's router port.
$iniPath = "$SrslPsxMasterDir\SRSL-PSX.ini"
$lines = Get-Content $iniPath
$lines = $lines -replace '^PORT=.*$', "PORT=$FrankenrouterMasterPort"
Set-Content -Path $iniPath -Value $lines

Start-Process -WindowStyle hidden -WorkingDirectory $SrslPsxMasterDir -FilePath java -ArgumentList "-jar", "$SrslPsxMasterDir\SRSL-PSX.jar" -RedirectStandardOutput "$SrslPsxMasterDir\console.out" -RedirectStandardError "$SrslPsxMasterDir\console.err"



