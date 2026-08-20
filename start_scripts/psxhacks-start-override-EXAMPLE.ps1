##############################################################################
#
#  HOW TO USE THIS FILE
#  ====================
#
#  1. COPY this file — do not edit it in place inside the psxhacks directory.
#
#  2. PLACE the copy one directory above the psxhacks directory and name it
#     exactly:  psxhacks-start-override.ps1
#
#     Example layout:
#       C:\fs\                              <- your sim root
#         psxhacks\                         <- the psxhacks Git checkout
#           start_scripts\
#             psxhacks-start-override-EXAMPLE.ps1  <- this file (leave it here)
#             ...
#         psxhacks-start-override.ps1       <- your copy goes HERE
#
#     The override file lives outside the Git tree so your local settings
#     are never accidentally committed or overwritten by a git pull.
#
#  3. EDIT your copy.  This sample starts nothing — every addon below is
#     commented out. To enable one, uncomment ALL of its lines (the
#     $Start* flag AND its directory/options settings, where present) and
#     fill in any paths that differ on your machine.
#
#  4. A few settings are REQUIRED no matter which addons you enable -
#     startup will error out with instructions until they are set:
#       - $PsxhacksPython       (python.exe inside your virtual environment;
#                                run start_scripts\setup_python_venv.ps1 to
#                                create one, it will print the exact path)
#       - $AerowinxDir          (your Aerowinx PSX installation directory)
#     A few more are required, but only for one of the two sim roles:
#       - $AerowinxPrefFiles         - only if you start a slave sim
#       - $AerowinxMainServerPrefFile - only if you start a master sim
#                                       (already has a sensible default)
#     $HoppieLogonCodes (or the legacy singular $HoppieLogonCode) is also
#     required, but only if you enable BACARS or HAFAP/CPDLC.
#     See each setting's own section below for details.
#
##############################################################################


# ---------------------------------------------------------------------------
# Python executable
# The python.exe inside your psxhacks virtual environment. REQUIRED - there
# is no default in common.ps1, and startup will error out until this is set.
# Run setup_python_venv.ps1 to create one; it will print the right path.
# ---------------------------------------------------------------------------
#$PsxhacksPython = "$SimBase\python\psxhacks-venv-YYYY-MM-DD\Scripts\python.exe"


# ---------------------------------------------------------------------------
# Aerowinx PSX settings
# PSX itself is always started (both the main server on the master and the
# main client instances on the slave). $AerowinxDir has no default in
# common.ps1 and is REQUIRED regardless of role - startup will error out
# until it is set.
#
# $AerowinxDir must point at your Aerowinx PSX installation directory
# (checked at startup: must contain AerowinxStart.jar).
#
# $AerowinxPrefFiles is the list of .pref files for the PSX main client
# instances to start in the slave sim (start_psx_main_clients.ps1) - one per
# cockpit position/window you want opened. It has no default and is only
# REQUIRED (checked in start_psx_main_clients.ps1, not common.ps1) if you
# actually start a slave sim.
#
# NOTE: start_scripts does not control PSX window position or size (unlike
# $ChangeWindowPositions further down, which only applies to addon
# windows). Each PSX client window's position/size is normal PSX behavior,
# handled by that client's own .pref file - use PSX's own preferences
# dialog to set it up.
# ---------------------------------------------------------------------------
#$AerowinxDir       = "$SimBase\psx\Aerowinx"
#$AerowinxPrefFiles = @("t9-main-noserver.pref", "t9-mcp.pref", "t9-pedestal.pref", "t9-fo.pref", "t9-overhead.pref")

# Name of the .pref file used to start the PSX main server
# (start_psx_main_server.ps1). This ships with AerowinxStart and is the same
# for everyone, so it already has a sensible default ("main-server.pref")
# in common.ps1 - only override it if your setup uses a different one. It is
# only REQUIRED to be non-empty (checked in start_psx_main_server.ps1, not
# common.ps1) if you actually start a master sim.
#
# This preferences file should be configured to:
#   - be a main server
#   - listen on the port defined as upstream in the master frankenrouter
#     config file (recommended: 20747) - set "Port10747=20747" in the
#     preferences file to do this
#   - not use audio
#   - not use USB controls
#   - have a very small window (uses less graphics resources this way)
#     that can be minimized
#$AerowinxMainServerPrefFile = "main-server.pref"


# ---------------------------------------------------------------------------
# BACARS settings
# NOTE: BACARS is started from startsim_master.ps1. Unlike most addons in
# this file, restart_bacars.ps1 already takes care of pointing it at the
# MASTER sim's PSX port for you - it rewrites ServerAddress to 127.0.0.1
# and ServerPort to $FrankenrouterMasterPort in BACARS' config file
# (now $PsxNetConfigDir\PSX.NET.BACARS.xml, alongside the other PSX.NET.*
# configs, not next to the BACARS .exe) on every start (BACARS always runs
# on the same host as frankenrouter and the PSX main server, so
# ServerAddress is always the loopback address).
#
# $BacarsDir has no default in common.ps1 - it is REQUIRED if $StartBacars
# is $true (checked at startup: must point at a directory containing
# PSX.Bacars.UI.exe). Both lines below are commented out since the default
# is not to start BACARS. To enable it: uncomment BOTH lines below AND edit
# $BacarsDir to the actual path of your BACARS installation.
# ---------------------------------------------------------------------------
#$StartBacars = $true   # BACARS ACARS system
#$BacarsDir   = "$SimBase\bacars\BACARS_V8.1.0"


# ---------------------------------------------------------------------------
# PSX.NET settings
# NOTE: PSX.NET is started from startsim_master.ps1. restart_psx_net.ps1
# already takes care of pointing it at the MASTER sim's PSX port for you -
# it rewrites PsxServerIP/PsxServerPort in Settings\PSX.NET.xml on every
# start.
#
# $PsxNetDir has no default in common.ps1 - it is REQUIRED if $StartPsxNet
# is $true (checked at startup: must point at a directory containing
# PSX.NET.exe). Both lines below are commented out since the default is not
# to start PSX.NET. To enable it: uncomment BOTH lines below AND edit
# $PsxNetDir to the actual path of your PSX.NET installation.
# ---------------------------------------------------------------------------
#$StartPsxNet = $true   # PSX.NET (EFB/nav data bridge)
#$PsxNetDir   = "$SimBase\psx_net\2026-04-11"


# ---------------------------------------------------------------------------
# PSX.NET.GroundHandling settings
# NOTE: PSX.NET.GroundHandling is started from startsim_master.ps1, and
# replaces both PSX.NET and PSX.NET.GroundCrew - enable at most ONE of the
# three (checked at startup). restart_psx_net_groundhandling.ps1 takes care
# of pointing it at the MASTER sim's PSX port for you - it rewrites
# PsxHost/PsxPort in $PsxNetConfigDir\PSX.NET.GroundHandling.Config.xml on
# every start.
#
# $PsxNetGroundHandlingDir has no default in common.ps1 - it is REQUIRED if
# $StartPsxNetGroundHandling is $true (checked at startup: must point at a
# directory containing PSX.NET.GroundHandling.exe). Both lines below are
# commented out since the default is not to start it. To enable it:
# uncomment BOTH lines below AND edit $PsxNetGroundHandlingDir to the actual
# path of your PSX.NET.GroundHandling installation.
# ---------------------------------------------------------------------------
#$StartPsxNetGroundHandling = $true
#$PsxNetGroundHandlingDir   = "$SimBase\psx_net_groundhandling\2026-04-11"


# ---------------------------------------------------------------------------
# PSX.NET.VATSIM settings
# $PsxNetVatsimDir has no default in common.ps1 - it is REQUIRED if
# $StartPsxNetVatsim is $true (checked at startup: must point at a
# directory containing GeoVR.PSX.Client.Wpf.exe). Both lines below are
# commented out since the default is not to start PSX.NET.VATSIM. To
# enable it: uncomment BOTH lines below AND edit $PsxNetVatsimDir to the
# actual path of your PSX.NET.VATSIM installation.
# ---------------------------------------------------------------------------
#$StartPsxNetVatsim = $true   # PSX.NET.VATSIM (alternative to vPilot)
#$PsxNetVatsimDir    = "$SimBase\psx_net_vatsim\2026-05-08"


# ---------------------------------------------------------------------------
# vPilot settings (alternative to PSX.NET.VATSIM above)
# $VPilotDir has no default in common.ps1 - it is REQUIRED if $StartVpilot
# is $true (checked at startup: must point at a directory containing
# vPilot.exe). Both lines below are commented out since the default is not
# to start vPilot. To enable it: uncomment BOTH lines below AND edit
# $VPilotDir to the actual path of your vPilot installation.
# ---------------------------------------------------------------------------
#$StartVpilot = $true   # vPilot VATSIM voice/text client
#$VPilotDir    = "$SimBase\vPilot"


# ---------------------------------------------------------------------------
# PSX.NET.MSFS.Client settings
# $PsxNetMsfsClientDir has no default in common.ps1 - it is REQUIRED if
# $StartPsxNetMsfsClient is $true (checked at startup: must point at a
# directory containing PSX.NET.MSFS2024.Client.exe). Both lines below are
# commented out since the default is not to start PSX.NET.MSFS.Client. To
# enable it: uncomment BOTH lines below AND edit $PsxNetMsfsClientDir to
# the actual path of your PSX.NET.MSFS.Client installation.
# ---------------------------------------------------------------------------
#$StartPsxNetMsfsClient = $true   # PSX.NET.MSFS.Client (needed if this sim runs MSFS)
#$PsxNetMsfsClientDir   = "$SimBase\psx_net_msfs\PSX.NET.MSFS20204.Client.20.0.0.5"


# ---------------------------------------------------------------------------
# PSX.NET.MSFS.Router settings
# $PsxNetMsfsRouterDir has no default in common.ps1 - it is REQUIRED if
# $StartPsxNetMsfsRouter is $true (checked at startup: must point at a
# directory containing PSX.NET.MSFS.Router.exe). Both lines below are
# commented out since the default is not to start PSX.NET.MSFS.Router. To
# enable it: uncomment BOTH lines below AND edit $PsxNetMsfsRouterDir to
# the actual path of your PSX.NET.MSFS.Router installation.
# ---------------------------------------------------------------------------
#$StartPsxNetMsfsRouter = $true   # PSX.NET.MSFS.Router (also relevant on slave if MSFS runs there)
#$PsxNetMsfsRouterDir    = "$SimBase\psx_net_msfs\PSX.NET.MSFS.Router.20.0.0.5"


# ---------------------------------------------------------------------------
# PSX.NET.WeatherRadar settings
# $PsxNetWeatherRadarDir has no default in common.ps1 - it is REQUIRED if
# $StartPsxNetWeatherRadar is $true (checked at startup: must point at a
# directory containing PSX.NET.WeatherRadar.exe). Both lines below are
# commented out since the default is not to start PSX.NET.WeatherRadar. To
# enable it: uncomment BOTH lines below AND edit $PsxNetWeatherRadarDir to
# the actual path of your PSX.NET.WeatherRadar installation.
# ---------------------------------------------------------------------------
#$StartPsxNetWeatherRadar = $true   # PSX.NET WeatherRadar
#$PsxNetWeatherRadarDir    = "$SimBase\psx_net_weather_radar\2026-05-07"


# ---------------------------------------------------------------------------
# PSX.NET.GroundCrew settings
# $PsxNetGroundCrewDir has no default in common.ps1 - it is REQUIRED if
# $StartPsxNetGroundCrew is $true (checked at startup: must point at a
# directory containing PSX.NET.GroundCrew.exe). Both lines below are
# commented out since the default is not to start PSX.NET.GroundCrew. To
# enable it: uncomment BOTH lines below AND edit $PsxNetGroundCrewDir to
# the actual path of your PSX.NET.GroundCrew installation.
# ---------------------------------------------------------------------------
#$StartPsxNetGroundCrew = $true   # PSX.NET GroundCrew
#$PsxNetGroundCrewDir    = "$SimBase\psx_net_ground_crew\2026-05-07"


# ---------------------------------------------------------------------------
# PSX.NET.EFB settings
# $PsxNetEfbDir has no default in common.ps1 - it is REQUIRED if $StartEfb
# is $true (checked at startup: must point at a directory containing
# PSX.NET.EFB.Windows.exe). Both lines below are commented out since the
# default is not to start PSX.NET.EFB. To enable it: uncomment BOTH lines
# below AND edit $PsxNetEfbDir to the actual path of your PSX.NET.EFB
# installation.
# ---------------------------------------------------------------------------
#$StartEfb      = $true   # PSX.NET EFB
#$PsxNetEfbDir   = "$SimBase\psx_net_efb\2026-05-15"


# ---------------------------------------------------------------------------
# PSXSounds settings
# $PsxSoundsDir has no default in common.ps1 - it is REQUIRED if
# $StartPsxSounds is $true (checked at startup: must point at a directory
# containing PSXSounds.exe). Both lines below are commented out since the
# default is not to start PSXSounds. To enable it: uncomment BOTH lines
# below AND edit $PsxSoundsDir to the actual path of your PSXSounds
# installation.
# ---------------------------------------------------------------------------
#$StartPsxSounds = $true   # PSX Sounds
#$PsxSoundsDir    = "$SimBase\psx_sounds\2026-07-08"


# ---------------------------------------------------------------------------
# HAFAP/CPDLC settings
# NOTE: HAFAP/CPDLC is started from startsim_master.ps1. restart_cpdlc.ps1
# already takes care of pointing it at the MASTER sim's PSX port for you -
# it strips any --psx-port you set in $CpdlcOptions and forces the correct
# one instead (HAFAP/CPDLC has no config file, so this can't be done via a
# config file rewrite like most other addons).
#
# $CpdlcDir has no default in common.ps1 - it is REQUIRED if $StartCpdlc is
# $true (checked at startup: must point at a directory containing
# psx-acars.py). Both lines below are commented out since the default is
# not to start HAFAP/CPDLC. To enable it: uncomment BOTH lines below AND
# edit $CpdlcDir to the actual path of your HAFAP/CPDLC installation.
#
# $CpdlcOptions notes:
#   --stealth is mandatory if you also use BACARS
#   --min-interval=15 --max-interval=30 seems to be OK on VATSIM
#   --no-no-comm fixes an annoying problem but you need Macroflight's
#     patched version of HAFAP
# ---------------------------------------------------------------------------
#$StartCpdlc  = $true   # HAFAP CPDLC client
#$CpdlcDir     = "$SimBase\hafap"
#$CpdlcOptions = "--stealth","--no-no-comm","--min-interval=15","--max-interval=30"


# ---------------------------------------------------------------------------
# Hoppie ACARS logon code(s) — needed if you run BACARS or CPDLC (HAFAP) on
# a network; the same code is used by both. $HoppieLogonCodes has no
# default in common.ps1 - it is REQUIRED (checked at startup) if
# $StartBacars or $StartCpdlc is $true, unless you set the legacy singular
# $HoppieLogonCode directly instead (still works, but prints a startup
# warning recommending you switch to $HoppieLogonCodes).
#
# Define a named hashtable, even if you only have one code - configure_flavor.ps1
# will let you pick one by name and will set $HoppieLogonCode for you:
# ---------------------------------------------------------------------------
#$HoppieLogonCodes = @{ "normal" = "your-logon-code-here"; "testing" = "your-other-logon-code-here" }



# ---------------------------------------------------------------------------
# ACARS Print settings
# $AcarsPrintDir has no default in common.ps1 - it is REQUIRED if
# $StartAcarsPrint is $true (checked at startup: must point at a directory
# containing AcarsPrint.jar). Both lines below are commented out since the
# default is not to start ACARS Print. To enable it: uncomment BOTH lines
# below AND edit $AcarsPrintDir to the actual path of your ACARS Print
# installation.
# ---------------------------------------------------------------------------
#$StartAcarsPrint = $true   # ACARS Print App
#$AcarsPrintDir    = "$SimBase\acars_print\AcarsPrintV1_1"


# ---------------------------------------------------------------------------
# SRSL-PSX settings (SmartRunway/SmartLanding)
# $SrslPsxMasterDir/$SrslPsxSlaveDir have no default in common.ps1 - each is
# REQUIRED if its matching $StartSrslPsx* flag is $true (checked at
# startup: must point at a directory containing SRSL-PSX.jar). Master and
# slave use separate directories since they connect to different PSX
# instances. Lines below are commented out since the default is not to
# start either. To enable one: uncomment its BOTH lines below AND edit the
# path to the actual SRSL-PSX installation.
#
# NOTE: restart_srsl_psx_master.ps1/restart_srsl_psx_slave.ps1 rewrite the
# PORT= line in SRSL-PSX.ini (in each of these directories) on every start,
# forcing the master instance to the MASTER sim's port and the slave
# instance to the SLAVE sim's port - no manual configuration needed.
# ---------------------------------------------------------------------------
#$StartSrslPsxMaster = $true   # SRSL-PSX SmartRunway/SmartLanding (master)
#$SrslPsxMasterDir    = "$SimBase\SRSL-PSX\2026-05-23-master"
#$StartSrslPsxSlave  = $true   # SRSL-PSX SmartRunway/SmartLanding (slave)
#$SrslPsxSlaveDir     = "$SimBase\SRSL-PSX\2026-05-23"


# ---------------------------------------------------------------------------
# CMC-PSX settings
# $CmcPsxDir has no default in common.ps1 - it is REQUIRED if $StartCmcPsx is
# $true (checked at startup: must point at a directory containing
# CMC-PSX.jar). CMC-PSX only runs in the master sim, connecting to the
# master router. Both lines below are commented out since the default is
# not to start it. To enable it: uncomment BOTH lines below AND edit the
# path to the actual CMC-PSX installation.
#
# NOTE: restart_cmc_psx.ps1 rewrites the PORT= and START_CONNECT= lines in
# CMC-PSX.ini (in this directory) on every start, forcing it to the MASTER
# sim's port and auto-connecting on launch - no manual configuration needed.
# ---------------------------------------------------------------------------
#$StartCmcPsx = $true   # CMC-PSX
#$CmcPsxDir    = "$SimBase\CMC-PSX"


# ---------------------------------------------------------------------------
# CS CDU Bridge settings
# $CsCduExe has no default in common.ps1 - it is REQUIRED if $StartCsCdu is
# $true (checked at startup: must point at an .exe file). Both lines below
# are commented out since the default is not to start the CS CDU bridge.
# To enable it: uncomment BOTH lines below AND edit $CsCduExe to the
# actual path of your CS CDU Bridge (CockpitSimulator) .exe.
# ---------------------------------------------------------------------------
#$StartCsCdu = $true   # Cockpit Simulator CDU hardware
#$CsCduExe    = "$SimBase\hw\cs_cdu\CockpitSimulator v2026.1.13.exe"


# ---------------------------------------------------------------------------
# psx_simlink_bridge settings
# Download: https://aerowinx.com/board/index.php/topic,8010.msg86285.html#msg86285
# $PsxSimlinkBridgeExe has no default in common.ps1 - it is REQUIRED if
# $StartPsxSimlinkBridge is $true (checked at startup: must point at an
# .exe file). Like CMC-PSX/SRSL-PSX, it only runs in the master sim:
# started from startsim_master.ps1, connecting to the MASTER router
# (restart_psx_simlink_bridge.ps1 passes -ip 127.0.0.1 and -port
# $FrankenrouterMasterPort on the command line - no config file to edit).
# Unlike CMC-PSX/SRSL-PSX, it is NOT started with -WindowStyle hidden -
# that breaks this app's GUI - so its window will be visible on start.
# Its console output is also NOT redirected to a file - redirecting it
# makes this PyInstaller-built app crash with UnicodeEncodeError (it
# needs a real console to print its UTF-8 box-drawing characters). Both
# lines below are commented out since the default is not to start it. To
# enable it: uncomment BOTH lines below AND edit $PsxSimlinkBridgeExe to
# the actual path of the .exe.
# ---------------------------------------------------------------------------
#$StartPsxSimlinkBridge = $true   # psx_simlink_bridge
#$PsxSimlinkBridgeExe    = "$SimBase\psx_simlink_bridge\psx_simlink_bridge_windows_v1_0b.exe"


# ---------------------------------------------------------------------------
# SimObjectRouter settings
# $SimObjectRouterDir has no default in common.ps1 - it is REQUIRED if
# $StartSimObjectRouter is $true (checked at startup: must point at a
# directory containing PSX.NET.MSFS.Temporary.SimObjectRouter.exe). Both
# lines below are commented out since the default is not to start
# SimObjectRouter. To enable it: uncomment BOTH lines below AND edit
# $SimObjectRouterDir to the actual path of your SimObjectRouter
# installation.
# ---------------------------------------------------------------------------
#$StartSimObjectRouter = $true   # SimObjectRouter
#$SimObjectRouterDir    = "$SimBase\sim_object_router\2026-05-07"


# ---------------------------------------------------------------------------
# The franken*.py addon sections below all share one convention:
# $Franken*Repo should normally be $null (or left commented out) - it
# makes that one addon run from a different sibling checkout of the
# psxhacks repo, and is only useful when testing a different (e.g.
# in-development) version of that specific addon. Everyone else should
# leave every $Franken*Repo setting alone.
# Example: $FrankenusbRepo = "psxhacks-frankenusb-devel"
#   -> runs C:\fs\psxhacks-frankenusb-devel\frankenusb.py
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# FrankenRouter settings
# $FrankenrouterDir is a working directory YOU create (not an installer
# target) - it holds frankenrouter's config file(s): one if you only run a
# master or only a slave router, two if you run both (they connect to
# different PSX instances and need different settings). This directory is
# REQUIRED to exist since frankenrouter.py is always started, on both the
# master and slave sim. If it's missing, startup will error out and tell
# you to create it.
#
# Once you've created the directory and placed your config file(s) in it,
# tell frankenrouter which one to use via --config-file in
# $FrankenrouterMasterOptions / $FrankenrouterSlaveOptions. This is
# REQUIRED - start_router_master.ps1/start_router_slave.ps1 check that
# --config-file is set and that the file exists before starting the
# router, and always add --no-basic-mode themselves so frankenrouter
# refuses to fall back to interactive Basic Mode if the config file is
# somehow still missing at that point.
#
# $FrankenrouterSlavePort/$FrankenrouterMasterPort must match the port
# number configured in the corresponding TOML config file - frankenrouter
# doesn't read the port from here, these are only used by other
# scripts/addons (e.g. stopsim_master.ps1/stopsim_slave.ps1) that need to
# know where to connect.
# ---------------------------------------------------------------------------
#$FrankenrouterDir           = "$SimBase\frankenrouter"
#$FrankenrouterMasterOptions = @("--config-file=master.toml")
#$FrankenrouterSlaveOptions  = @("--config-file=slave.toml")
#$FrankenrouterSlavePort     = 10747
#$FrankenrouterMasterPort    = 10748
#$FrankenrouterRepo          = $null


# ---------------------------------------------------------------------------
# FrankenUSB settings (slave sim)
# $FrankenusbDir is a working directory YOU create (not an installer
# target) - it holds frankenusb's config file. It only needs to exist if
# $StartFrankenusb is $true (checked at startup). frankenusb.py looks for
# frankenusb.conf in this directory by default; if your config file has a
# different name, pass it via $FrankenusbOptions.
# ---------------------------------------------------------------------------
#$StartFrankenusb   = $true   # FrankenUSB (USB hardware input)
#$FrankenusbDir      = "$SimBase\frankenusb"
#$FrankenusbOptions  = @()   # e.g. @("--config-file=frankenusb.conf")
#$FrankenusbRepo     = $null


# ---------------------------------------------------------------------------
# FrankenCDU proxy settings (slave sim)
# Translates L/C/R CDU keywords between the CS CDU Bridge and PSX.
# ---------------------------------------------------------------------------
#$StartFrankencduproxy  = $true   # FrankenCDU proxy (CS CDU Bridge <-> PSX)
#$FrankencduproxyOptions = @("--listen-port=10750","--cdu-map=L=L")
#$FrankencduproxyRepo    = $null


# ---------------------------------------------------------------------------
# FrankenTanker settings (master sim)
# Fire retardant loading/dropping simulation (e.g. CL-415/AT-802 water bombers).
#
# NOTE: FrankenTanker is started from startsim_master.ps1. restart_frankentanker.ps1
# already takes care of pointing it at the MASTER sim's PSX port for you via
# --psx-port-override (this applies to every franken*.py addon started from
# start_scripts - no need to set --psx-port yourself).
# ---------------------------------------------------------------------------
#$StartFrankentanker  = $true   # FrankenTanker (fire retardant load/drop)
#$FrankentankerOptions = @("--cdus=LR","--menu-row=4")
#$FrankentankerRepo    = $null


# ---------------------------------------------------------------------------
# FrankenIDENT settings (slave sim)
# frankenrouter_ident.py
# ---------------------------------------------------------------------------
#$StartFrankenident  = $true   # FrankenIDENT
#$FrankenidentOptions = @()
#$FrankenidentRepo    = $null


# ---------------------------------------------------------------------------
# FrankenPrinter settings (slave sim)
# Replacement for the ACARS Print App.
# ---------------------------------------------------------------------------
#$StartFrankenprint  = $true   # FrankenPrinter (replacement for ACARS Print App)
#$FrankenprintOptions = @("--printer=EPSON TM-T20III Receipt", "--lines-after=3", "--lines-before=3")
#$FrankenprintRepo    = $null


# ---------------------------------------------------------------------------
# FrankenWeather settings (master sim)
#
# NOTE: started from startsim_master.ps1 - see the FrankenTanker settings
# note above regarding the PSX port.
# ---------------------------------------------------------------------------
#$StartFrankenweather  = $true   # FrankenWeather
#$FrankenweatherOptions = @("--web-port=9999", "--config-file=C:\fs\frankenweather.toml")
#$FrankenweatherRepo    = $null


# ---------------------------------------------------------------------------
# FrankenMSFSBridge settings (slave sim)
# MSFS -> PSX data bridge; a helper for FrankenWeather (see above).
# ---------------------------------------------------------------------------
#$StartFrankenmsfsbridge  = $true   # FrankenMSFSBridge (MSFS -> PSX data bridge)
#$FrankenmsfsbridgeOptions = @("--sdk-path=C:\MSFS 2024 SDK")
#$FrankenmsfsbridgeRepo    = $null


# ---------------------------------------------------------------------------
# FrankenPush settings (master sim)
#
# NOTE: started from startsim_master.ps1 - see the FrankenTanker settings
# note above regarding the PSX port.
# ---------------------------------------------------------------------------
#$StartFrankenpush  = $true   # FrankenPush
#$FrankenpushOptions  = @("--simevents","--logon-code=CHANGEME","--upload-autosave-from=C:\fs\psx\Aerowinx\Situations")
#$FrankenpushRepo    = $null


# ---------------------------------------------------------------------------
# Non-scripted apps
# Anything here is launched as-is (Start-Process) at the end of
# startsim_slave.ps1 - use this for simple apps that don't need their own
# restart script (no config file editing, no repo/PYTHONPATH handling).
# Defaults to an empty list in common.ps1.
# ---------------------------------------------------------------------------
#$NonscriptedApps = @("C:\fs\some_tool\some_tool.exe", "notepad.exe")


# ---------------------------------------------------------------------------
# Window positioning  (apply_window_positions.ps1)
# Set to $true to automatically move addon windows on startup.
#
# Before enabling this, run start_scripts\configure_window_positions.ps1
# once to record where you want each addon's window placed - it saves
# your layout to psxhacks-current-positions.ps1, which
# apply_window_positions.ps1 then uses on every startup.
# ---------------------------------------------------------------------------
$ChangeWindowPositions = $false


# ---------------------------------------------------------------------------
# Interactive pauses in startsim_slave.ps1
# $StopAfterSlaveRouterStart — pause after the slave router starts (before
#   launching further addons) so you can verify the router is up.
# $StopBeforeMsfsStart — pause again just before MSFS is launched.
# Both default to $true in common.ps1.  Set to $false for a fully
# automated startup.
# ---------------------------------------------------------------------------
#$StopAfterSlaveRouterStart = $true
#$StopBeforeMsfsStart       = $true


# ---------------------------------------------------------------------------
# Confirmation prompt in stopsim_master.ps1/stopsim_slave.ps1
# Defaults to $true in common.ps1 (asks "Are you sure?" before stopping).
# Set to $false to stop the sim immediately with no confirmation prompt.
# ---------------------------------------------------------------------------
#$StopSimConfirm = $true
