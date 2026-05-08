. "$PSScriptRoot\common.ps1"

Set-Location $AerowinxDir

foreach ($pref in $AerowinxPrefFiles) {
    java -jar AerowinxStart.jar $pref
}
