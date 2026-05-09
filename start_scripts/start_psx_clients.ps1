. "$PSScriptRoot\common.ps1"

Set-Location $AerowinxDir

foreach ($pref in $AerowinxPrefFiles) {
    java -jar AerowinxStart.jar $pref
}

# Wait for PSX main clients to start (one of which runs the boost
# server which PSX.NET.MSFS.Router needs.
Delay 5
