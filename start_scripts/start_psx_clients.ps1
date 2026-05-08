. "$PSScriptRoot\common.ps1"

& "$PSScriptRoot\stop_things_that_should_not_run_while_simming.ps1"

Set-Location $AerowinxDir

foreach ($pref in $AerowinxPrefFiles) {
    java -jar AerowinxStart.jar $pref
}
