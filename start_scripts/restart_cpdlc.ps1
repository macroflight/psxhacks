. "$PSScriptRoot\common.ps1"

Set-Location $CpdlcDir

$Host.UI.RawUI.WindowTitle = "Hoppie PSX CPDLC"
KillPythonScript "psx-acars.py"

# HAFAP/CPDLC (psx-acars.py) does not live in the psxhacks repo and has no
# config file, so it can't be given a --psx-port-override option like our
# own addons. Instead, strip any --psx-port the user set in $CpdlcOptions
# and force the MASTER sim's router port instead, since HAFAP/CPDLC is
# started from startsim_master.ps1.
$cpdlcOptionsFiltered = @()
$skipNext = $false
foreach ($opt in $CpdlcOptions) {
    if ($skipNext) {
        $skipNext = $false
        continue
    }
    if ($opt -eq "--psx-port") {
        $skipNext = $true
        Show-WarningAndContinue "--psx-port is set in `$CpdlcOptions in $OverrideFile.`nRemoving it and forcing --psx-port=$FrankenrouterMasterPort instead, since HAFAP/CPDLC is started from startsim_master.ps1 and must connect to the MASTER sim's router port."
        continue
    }
    if ($opt -like "--psx-port=*") {
        Show-WarningAndContinue "--psx-port is set in `$CpdlcOptions in $OverrideFile.`nRemoving it and forcing --psx-port=$FrankenrouterMasterPort instead, since HAFAP/CPDLC is started from startsim_master.ps1 and must connect to the MASTER sim's router port."
        continue
    }
    $cpdlcOptionsFiltered += $opt
}
$cpdlcOptionsFiltered += "--psx-port=$FrankenrouterMasterPort"

Write-Output "Logon code used: $HoppieLogonCode"
& $PsxhacksPython psx-acars.py @cpdlcOptionsFiltered $HoppieLogonCode

# Read-Host -Prompt "Press Enter to exit"
