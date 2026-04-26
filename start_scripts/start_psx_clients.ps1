. "$PSScriptRoot\common.ps1"

& "$PSScriptRoot\stop_things_that_should_not_run_while_simming.ps1"

cd $AerowinxDir

# Start the five main PSX client windows (no server)
java -jar AerowinxStart.jar t9-main-noserver.pref
java -jar AerowinxStart.jar t9-mcp.pref
java -jar AerowinxStart.jar t9-pedestal.pref
java -jar AerowinxStart.jar t9-fo.pref
java -jar AerowinxStart.jar t9-overhead.pref
