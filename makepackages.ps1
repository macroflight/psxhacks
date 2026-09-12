# makepackages.ps1 - Build all PSXhacks EXEs and package for distribution.
#
# Usage:
#   .\makepackages.ps1
#   .\makepackages.ps1 -PythonDir C:\fs\python\3.13.13
#   .\makepackages.ps1 -VenvDir C:\path\to\venv
#   .\makepackages.ps1 -Addon frankenweather   # build just one EXE, no zip
#
# The venv is created automatically on first run and reused on subsequent runs.
# To force a fresh venv, delete $VenvDir before running.

param(
    [string]$PythonDir = "C:\fs\python\3.13.13",
    [string]$VenvDir   = "C:\fs\python\makepackages-venv",
    # When set, build only this one addon's EXE (see $AddonSpecs below for the
    # list of valid names) and skip copying config files / zipping a release
    # package -- useful for a quick single-addon rebuild during development.
    [string]$Addon     = ""
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$Python     = "$VenvDir\Scripts\python.exe"
$Pip        = "$VenvDir\Scripts\pip.exe"
$PyI        = "$VenvDir\Scripts\pyinstaller.exe"

# ---------------------------------------------------------------------------
# Create / update virtualenv
# ---------------------------------------------------------------------------

if (-not (Test-Path $PyI)) {
    Write-Output "Creating virtual environment at $VenvDir (using Python from $PythonDir) ..."
    & "$PythonDir\python.exe" -m venv $VenvDir
    & $Pip install --upgrade pip
    & $Pip install -r requirements.txt
    # pyinstaller-hooks-contrib provides community hooks for aiohttp, rasterio, etc.
    & $Pip install pyinstaller "pyinstaller-hooks-contrib>=2024.0"
    Write-Output "Virtual environment ready."
}

# ---------------------------------------------------------------------------
# Build EXEs
# ---------------------------------------------------------------------------

# addon name -> PyInstaller args (spec path, plus --distpath for the one
# that lives in a subdirectory). Ordered so the full build's output stays
# predictable; -Addon looks up a single entry from the same list, so there
# is only one place that needs to know about a new addon's spec file.
$AddonSpecs = [ordered]@{
    'frankenusb'           = @('frankenusb.spec')
    'frankenweather'       = @('frankenweather.spec')
    'frankentanker'        = @('frankentanker.spec')
    'frankenpush'          = @('frankenpush.spec')
    'frankencduproxy'      = @('frankencduproxy.spec')
    'frankenprint'         = @('frankenprint.spec')
    'frankenrouter_ident'  = @('frankenrouter_ident.spec')
    'psx_shutdown'         = @('psx_shutdown.spec')
    'temporary_weather_logger' = @('temporary_weather_logger.spec')
    'show_psx'             = @('show_psx.spec')
    'show_usb'             = @('show_usb.spec')
    'show_hid'             = @('show_hid.spec')
    # frankenrouter lives in a subdirectory; --distpath keeps output alongside the rest
    'frankenrouter'        = @('--distpath', '.\dist', 'router\frankenrouter.spec')
}

function Build-Addon([string]$Name) {
    $pyiArgs = $AddonSpecs[$Name]
    Write-Output ""
    Write-Output "=== Building $Name ($($pyiArgs[-1])) ==="
    & $PyI --clean @pyiArgs
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed for $Name" }
}

if ($Addon -ne "") {
    if (-not $AddonSpecs.Contains($Addon)) {
        Write-Output "Unknown addon '$Addon'. Valid names:"
        $AddonSpecs.Keys | ForEach-Object { Write-Output "  $_" }
        exit 1
    }
    Build-Addon $Addon
    Write-Output ""
    Write-Output "Done. Built $Addon only (no config copy, no zip) -- see dist\"
    exit 0
}

foreach ($name in $AddonSpecs.Keys) {
    Build-Addon $name
}

# ---------------------------------------------------------------------------
# Copy sample config files into dist/
# ---------------------------------------------------------------------------

Write-Output ""
Write-Output "Copying sample config files ..."
Copy-Item config_examples\*.conf dist\
Copy-Item router\config_examples\*.conf dist\

$Date    = Get-Date -Format 'yyyy-MM-dd'
$ZipName = "psxhacks-$Date.zip"

Write-Output ""
Write-Output "Creating $ZipName ..."
Compress-Archive -Path .\dist\* -DestinationPath $ZipName -Force

Write-Output ""
Write-Output "Done. Release package: $ZipName"
