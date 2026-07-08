. "$PSScriptRoot\common.ps1"

# $AerowinxPrefFiles has no default - it must be set in the override file
# (see psxhacks-start-override-EXAMPLE.ps1) to the list of .pref files for
# the PSX main client instances to start. Only checked here (not in
# common.ps1) since it's only needed when starting the slave sim.
if (-not $AerowinxPrefFiles -or $AerowinxPrefFiles.Count -eq 0) {
    Show-ErrorAndExit "`$AerowinxPrefFiles is not set.`nEdit $OverrideFile and set `$AerowinxPrefFiles to the list of .pref files for your PSX main client instances."
}

Set-Location $AerowinxDir

foreach ($pref in $AerowinxPrefFiles) {
    java -jar AerowinxStart.jar $pref
}

# Wait for PSX main clients to start (one of which runs the boost
# server which PSX.NET.MSFS.Router needs.
Delay 5
