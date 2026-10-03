. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Stop HAFAP/CPDLC"

# Tell start_cpdlc.ps1's window (if still open) that this stop is
# expected, so it doesn't mistake the forced kill below for a crash.
New-Item -Path $CpdlcExpectedStopFlag -ItemType File -Force | Out-Null
KillPythonScript "psx-acars.py"
