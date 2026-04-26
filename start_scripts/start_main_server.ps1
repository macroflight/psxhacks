. "$PSScriptRoot\common.ps1"

cd $AerowinxDir

# Start the PSX main server only (no client windows)
java -jar AerowinxStart.jar main-server.pref
