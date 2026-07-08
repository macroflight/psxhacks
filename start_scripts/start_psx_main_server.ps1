. "$PSScriptRoot\common.ps1"

# $AerowinxMainServerPrefFile has a sensible default in common.ps1, but
# guard against it being blanked out in the override file. Only checked
# here (not in common.ps1) since it's only needed when starting the
# master sim.
if ([string]::IsNullOrWhiteSpace($AerowinxMainServerPrefFile)) {
    Show-ErrorAndExit "`$AerowinxMainServerPrefFile is not set.`nEdit $OverrideFile and set `$AerowinxMainServerPrefFile to the .pref file used to start the PSX main server."
}

Set-Location $AerowinxDir

# Start the PSX main server only (no client windows)
java -jar AerowinxStart.jar $AerowinxMainServerPrefFile
