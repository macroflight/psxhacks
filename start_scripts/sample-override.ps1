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
#             sample-override.ps1           <- this file (leave it here)
#             ...
#         psxhacks-start-override.ps1       <- your copy goes HERE
#
#     The override file lives outside the Git tree so your local settings
#     are never accidentally committed or overwritten by a git pull.
#
#  3. EDIT your copy.  This sample starts nothing — change any $Start* flag
#     below from $false to $true to enable that addon, and fill in the paths
#     that differ on your machine.
#
#  4. The most important setting is $PsxhacksPython.  Nothing will work
#     until that points at the python.exe inside your virtual environment.
#     Run start_scripts\setup_python_venv.ps1 to create one; it will print
#     the exact path to paste here.
#
##############################################################################


# ---------------------------------------------------------------------------
# Python executable
# The python.exe inside your psxhacks virtual environment.
# Run setup_python_venv.ps1 to create one; it will print the right path.
# ---------------------------------------------------------------------------
$PsxhacksPython = "C:\fs\python\psxhacks-venv-YYYY-MM-DD\Scripts\python.exe"


# ---------------------------------------------------------------------------
# Third-party application directories
# Defaults mirror common.ps1.  Only override lines that differ on your machine.
# ---------------------------------------------------------------------------
#$BacarsDir           = "$SimBase\bacars\BACARS_V8.1.0"
#$PsxNetDir           = "$SimBase\psx_net\2026-04-11"
#$PsxNetVatsimDir     = "$SimBase\psx_net_vatsim\2026-05-08"
#$PsxNetMsfsClientDir = "$SimBase\psx_net_msfs\PSX.NET.MSFS20204.Client.20.0.0.5"
#$PsxNetMsfsRouterDir = "$SimBase\psx_net_msfs\PSX.NET.MSFS.Router.20.0.0.5"
#$PsxNetWeatherRadarDir = "$SimBase\psx_net_weather_radar"
#$PsxNetGroundCrewDir   = "$SimBase\psx_net_ground_crew"
#$PsxNetEfbDir        = "$SimBase\psx_net_efb\PSX.NET.EFB-2.0.0.2-2025-11-12-2"
#$PsxSoundsDir        = "$SimBase\psx_sounds\PSXSounds"
#$CpdlcDir            = "$SimBase\hafap"
#$VPilotDir           = "$SimBase\vPilot"
#$AerowinxDir         = "$SimBase\psx\Aerowinx"
#$AcarsPrintDir       = "$SimBase\acars_print\AcarsPrintV1_1"
#$SrslPsxMasterDir    = "$SimBase\SRSL-PSX\2026-05-23-master"
#$SrslPsxSlaveDir     = "$SimBase\SRSL-PSX\2026-05-23"
#$SimObjectRouterDir  = "$SimBase\sim_object_router"
#$CsCduExe            = "$SimBase\hw\cs_cdu\CockpitSimulator v2025.2.7.exe"
#$FrankenusbDir       = "$SimBase\frankenusb"
#$FrankenrouterDir    = "$SimBase\frankenrouter"


# ---------------------------------------------------------------------------
# Hoppie ACARS logon code — required if you run BACARS or CPDLC on a network
# ---------------------------------------------------------------------------
#$HoppieLogonCode = "your-logon-code-here"


# ---------------------------------------------------------------------------
# Addon start flags
# Change $false to $true for each addon you want started with the sim.
#
# Master sim addons (startsim_master.ps1)
# ---------------------------------------------------------------------------
$StartBacars             = $false   # BACARS ACARS system (default: true in common.ps1)
$StartPsxNet             = $false   # PSX.NET (EFB/nav data bridge) (default: true)
$StartPsxNetMsfsRouter   = $false   # PSX.NET MSFS router (default: true)

$StartCpdlc              = $false   # HAFAP CPDLC client
$StartFrankencduproxy    = $false   # FrankenCDU proxy (CS CDU Bridge <-> PSX)
$StartCsCdu              = $false   # Cockpit Simulator CDU hardware
$StartFrankentanker      = $false   # FrankenTanker (in-flight refuelling)
$StartFrankenturb        = $false   # FrankenTurb (terrain/wind turbulence)
$StartFrankenident       = $false   # FrankenIDENT (slave)
$StartFrankenidentMaster = $false   # FrankenIDENT (master instance)
$StartFrankenutil        = $false   # FrankenUtil (misc utility addon)
$StartFrankenusb         = $false   # FrankenUSB (USB hardware input)
$StartSrslPsxMaster      = $false   # SRSL-PSX SmartRunway/SmartLanding (master)

# Slave sim addons (startsim_slave.ps1)
$StartPsxSounds          = $false   # PSX Sounds
$StartVpilot             = $false   # vPilot VATSIM voice/text client
$StartPsxNetVatsim       = $false   # PSX.NET.VATSIM (alternative to vPilot)
$StartPsxNetMsfsRouter   = $false   # (also relevant on slave if MSFS runs there)
$StartPsxNetWeatherRadar = $false   # PSX.NET WeatherRadar
$StartPsxNetGroundCrew   = $false   # PSX.NET GroundCrew
$StartSimObjectRouter    = $false   # SimObjectRouter
$StartEfb                = $false   # PSX.NET EFB
$StartAcarsPrint         = $false   # ACARS Print App
$StartFrankenprint       = $false   # FrankenPrinter (replacement for ACARS Print App)
$StartSrslPsxSlave       = $false   # SRSL-PSX SmartRunway/SmartLanding (slave)
$StartFrankenmsfsbridge  = $false   # FrankenMSFSBridge (MSFS → PSX data bridge)


# ---------------------------------------------------------------------------
# Alternative psxhacks repo per addon
# Set to the name of a sibling directory (relative to the psxhacks parent)
# to run that specific addon from a different checkout of the repo.
# All other addons continue to use the main $PsxhacksDevel directory.
# Leave commented out (or $null) to use the normal psxhacks directory.
# Example: $FrankenusbRepo = "psxhacks-frankenusb-devel"
#   -> runs C:\fs\psxhacks-frankenusb-devel\frankenusb.py
# ---------------------------------------------------------------------------
#$FrankencduproxyRepo    = $null
#$FrankenmsfsbridgeRepo  = $null
#$FrankenprintRepo       = $null
#$FrankenidentRepo       = $null
#$FrankenidentMasterRepo = $null
#$FrankentankerRepo      = $null
#$FrankenturbRepo        = $null
#$FrankenusbRepo         = $null
#$FrankenutilRepo        = $null
#$CpdlcRepo              = $null


# ---------------------------------------------------------------------------
# Extra command-line options for individual addons
# Uncomment and populate the ones you need.
# ---------------------------------------------------------------------------
#$FrankenrouterMasterOptions = @("--config-file=frankensim-core.toml")
#$FrankenrouterSlaveOptions  = @("--config-file=frankensim-client.toml")
#$FrankentankerOptions       = @()
#$FrankenusbOptions          = @()
#$FrankenturbOptions         = @()
#$FrankenidentOptions        = @()
#$FrankenidentMasterOptions  = @("--psx-port=10748")
#$FrankencduproxyOptions     = @()
#$FrankenmsfsbridgeOptions   = @()
#$FrankenprintOptions        = @()
#$FrakenrouterSlavePort      = 10747
#$FrankenrouterMasterPort    = 10748
#$CpdlcOptions = "--stealth","--no-no-comm","--min-interval=15","--max-interval=30"


# ---------------------------------------------------------------------------
# Window positioning  (apply_window_positions.ps1)
# Set to $true to automatically move addon windows on startup.
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
