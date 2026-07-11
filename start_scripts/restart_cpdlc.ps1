. "$PSScriptRoot\common.ps1"

Set-Location $CpdlcDir

$Host.UI.RawUI.WindowTitle = "Hoppie PSX CPDLC"
KillPythonScript "psx-acars.py"

# Clear any stale flag from a previous run (e.g. this window was closed
# before it could consume the flag - see below), so it can't mask a
# genuine crash this time around.
Remove-Item $CpdlcExpectedStopFlag -Force -ErrorAction SilentlyContinue

# HAFAP/CPDLC (psx-acars.py) does not live in the psxhacks repo and has no
# config file, so it can't be given a --psx-port-override option like our
# own addons. Instead, strip any --psx-port the user set in $CpdlcOptions
# and force the MASTER sim's router port instead, since HAFAP/CPDLC is
# started from startsim_master.ps1.
$cpdlcOptionsFiltered = @()
for ($i = 0; $i -lt $CpdlcOptions.Count; $i++) {
    $opt = $CpdlcOptions[$i]
    if ($opt -eq "--psx-port") {
        $existingValue = $CpdlcOptions[$i + 1]
        $i++
        if ($existingValue -ne "$FrankenrouterMasterPort") {
            Show-WarningAndContinue "--psx-port is set in `$CpdlcOptions in $OverrideFile.`nRemoving it and forcing --psx-port=$FrankenrouterMasterPort instead, since HAFAP/CPDLC is started from startsim_master.ps1 and must connect to the MASTER sim's router port."
        }
        continue
    }
    if ($opt -like "--psx-port=*") {
        $existingValue = $opt.Substring("--psx-port=".Length)
        if ($existingValue -ne "$FrankenrouterMasterPort") {
            Show-WarningAndContinue "--psx-port is set in `$CpdlcOptions in $OverrideFile.`nRemoving it and forcing --psx-port=$FrankenrouterMasterPort instead, since HAFAP/CPDLC is started from startsim_master.ps1 and must connect to the MASTER sim's router port."
        }
        continue
    }
    $cpdlcOptionsFiltered += $opt
}
$cpdlcOptionsFiltered += "--psx-port=$FrankenrouterMasterPort"

Write-Output "Logon code used: $HoppieLogonCode"
& $PsxhacksPython psx-acars.py @cpdlcOptionsFiltered $HoppieLogonCode

if ($LASTEXITCODE -ne 0) {
    if (Test-Path $CpdlcExpectedStopFlag) {
        # We were killed intentionally (e.g. by stopsim_master.ps1) rather
        # than having crashed on our own - not an error, let the window
        # close normally.
        Remove-Item $CpdlcExpectedStopFlag -Force -ErrorAction SilentlyContinue
    } else {
        Show-ErrorAndExit "CPDLC (psx-acars.py) exited with error code $LASTEXITCODE - see the output above for details."
    }
}

# Read-Host -Prompt "Press Enter to exit"
