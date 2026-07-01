# makepackages.ps1 - Build all PSXhacks EXEs and package for distribution.
#
# Usage:
#   .\makepackages.ps1
#   .\makepackages.ps1 -PythonDir C:\fs\python\3.13.13
#   .\makepackages.ps1 -VenvDir C:\path\to\venv
#
# The venv is created automatically on first run and reused on subsequent runs.
# To force a fresh venv, delete $VenvDir before running.

param(
    [string]$PythonDir = "C:\fs\python\3.13.13",
    [string]$VenvDir   = "C:\fs\python\makepackages-venv"
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

$Specs = @(
    'frankenusb.spec',
    'frankenweather.spec',
    'frankenmsfsbridge.spec',
    'frankentanker.spec',
    'frankenpush.spec',
    'psx_shutdown.spec',
    'show_psx.spec',
    'show_usb.spec',
    'show_hid.spec'
)

foreach ($spec in $Specs) {
    Write-Output ""
    Write-Output "=== Building $spec ==="
    & $PyI --clean $spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed for $spec" }
}

# frankenrouter lives in a subdirectory; --distpath keeps output alongside the rest
Write-Output ""
Write-Output "=== Building router\frankenrouter.spec ==="
& $PyI --clean --distpath .\dist router\frankenrouter.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed for frankenrouter.spec" }

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
