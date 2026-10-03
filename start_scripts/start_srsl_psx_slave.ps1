. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "SRSL-PSX (slave)"

# SRSL-PSX (slave) is started from startsim_slave.ps1, so it must connect to
# the SLAVE sim's router port.
$iniPath = "$SrslPsxSlaveDir\SRSL-PSX.ini"
$lines = Get-Content $iniPath
$lines = $lines -replace '^PORT=.*$', "PORT=$FrankenrouterSlavePort"
Set-Content -Path $iniPath -Value $lines

Start-Process -WindowStyle hidden -WorkingDirectory $SrslPsxSlaveDir -FilePath java -ArgumentList "-jar", "$SrslPsxSlaveDir\SRSL-PSX.jar" -RedirectStandardOutput "$SrslPsxSlaveDir\console.out" -RedirectStandardError "$SrslPsxSlaveDir\console.err"
Invoke-WindowPosition "SRSL-PSX slave"
