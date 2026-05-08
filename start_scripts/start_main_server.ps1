. "$PSScriptRoot\common.ps1"

Set-Location $AerowinxDir

# Start the PSX main server only (no client windows)
java -jar AerowinxStart.jar main-server.pref
