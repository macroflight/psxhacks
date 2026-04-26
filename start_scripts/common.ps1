# Shared constants and helpers for PSX start/stop scripts.
# If common_override.ps1 exists alongside this file it is loaded after the
# defaults, allowing per-machine settings that survive a git pull unchanged.

$PsxhacksPython = "C:\fs\python\psxhacks-1\Scripts\python.exe"
$PsxhacksDevel  = "C:\fs\psxhacks-devel"
$SyncTrayzor    = "C:\Program Files\SyncTrayzor\synctrayzor.exe"
$BacarsDir          = "C:\fs\bacars\BACARS_V8.1.0"
$PsxNetMsfsClientDir = "C:\fs\psx_net_msfs\PSX.NET.MSFS20204.Client.20.0.0.5"
$PsxNetMsfsRouterDir = "C:\fs\psx_net_msfs\PSX.NET.MSFS.Router.20.0.0.5"
$PsxNetDir           = "C:\fs\psx_net\2026-04-11"
$PsxSoundsDir        = "C:\fs\psx_sounds\PSXSounds"

# Silently stop a process by name; does nothing if the process is not running
function KillProcess([string]$name) {
    Stop-Process -Name $name -Force -ErrorAction SilentlyContinue
}

# Work directories (where logs and config files are written/read from)
$FrankenFreezeDir = "$PSScriptRoot\..\..\frankenfreeze"
$FrankenTankerDir = "$PSScriptRoot\..\..\frankentanker"
$FrankenUsbDir    = "$PSScriptRoot\..\..\frankenusb"
$FrankenRouterDir = "$PSScriptRoot\..\..\frankenrouter"
$CpdlcDir         = "$PSScriptRoot\..\..\hafap"
$AerowinxDir      = "C:\fs\psx\Aerowinx"
$VPilotDir        = "C:\fs\vPilot"
$PsxNetEfbDir     = "C:\fs\psx_net_efb\PSX.NET.EFB-2.0.0.2-2025-11-12-2"
$AcarsPrintDir    = "C:\fs\acars_print\AcarsPrintV1_1"

# Set your personal Hoppie network logon code in common_override.ps1:
#   $HoppieLogonCode = "YOURCODE"
$HoppieLogonCode  = "DUMMYLOGONCODE"

# Set any of these to $false in common_override.ps1 to skip that addon at startup
$StartCpdlc        = $true
$StartFrankenfreeze = $true
$StartFrankenusb   = $true
$StartIdent        = $true
$StartPsxSounds    = $true
$StartVpilot       = $true
$StartAcarsPrint   = $true
$StartEfb          = $true

# Apps launched as-is during sim startup (no custom start script needed).
# Override $NonscriptedApps in common_override.ps1 to replace this list entirely.
$NonscriptedApps = @(
    "C:\fs\hw\cs_cdu\CockpitSimulator v2025.2.7.exe"
)

function KillPythonScript([string]$scriptName) {
    Get-CimInstance Win32_Process -Filter "name = 'python.exe'" |
        Where-Object { $_.CommandLine -like "*$scriptName*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

function KillJavaJar([string]$jarName) {
    Get-CimInstance Win32_Process -Filter "name = 'java.exe'" |
        Where-Object { $_.CommandLine -like "*$jarName*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

function Delay([int]$seconds) {
    Write-Output "Waiting $seconds seconds..."
    Start-Sleep -Seconds $seconds
}

function start_nonscripted_apps {
    foreach ($app in $NonscriptedApps) {
        Write-Output "Starting $app..."
        Start-Process $app
    }
}

$_override = "$PSScriptRoot\common_override.ps1"
if (Test-Path $_override) { . $_override }
