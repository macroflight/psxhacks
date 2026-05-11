. "$PSScriptRoot\common.ps1"

Set-Location $CpdlcDir

$Host.UI.RawUI.WindowTitle = "Hoppie PSX CPDLC"
KillPythonScript "psx-acars.py"

$env:PYTHONPATH = $PsxhacksDevel

Write-Output "Logon code used: $HoppieLogonCode"
& $PsxhacksPython psx-acars.py @CpdlcOptions $HoppieLogonCode

# Read-Host -Prompt "Press Enter to exit"
