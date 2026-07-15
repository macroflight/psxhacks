. "$PSScriptRoot\common.ps1"


$Host.UI.RawUI.WindowTitle = "CMC-PSX"
KillJavaJar "$CmcPsxDir\CMC-PSX.jar"

# CMC-PSX is started from startsim_master.ps1, so it must connect to the
# MASTER sim's router port. Also force START_CONNECT=YES so it connects
# automatically on launch instead of waiting for a manual action in its GUI.
$iniPath = "$CmcPsxDir\CMC-PSX.ini"
$lines = Get-Content $iniPath
$lines = $lines -replace '^PORT=.*$', "PORT=$FrankenrouterMasterPort"
$lines = $lines -replace '^START_CONNECT=.*$', "START_CONNECT=YES"
Set-Content -Path $iniPath -Value $lines

Start-Process -WindowStyle hidden -WorkingDirectory $CmcPsxDir -FilePath java -ArgumentList "-jar", "$CmcPsxDir\CMC-PSX.jar" -RedirectStandardOutput "$CmcPsxDir\console.out" -RedirectStandardError "$CmcPsxDir\console.err"
