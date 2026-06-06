# Setup script: install Python 3.13 and create a psxhacks virtual environment.
#
# Run this once on a new machine (or to set up a new venv) from a normal
# PowerShell window - no administrator rights required as long as the target
# directory is writable.

$ErrorActionPreference = 'Stop'

function Prompt-WithDefault([string]$question, [string]$default) {
    $answer = Read-Host "$question [$default]"
    if ([string]::IsNullOrWhiteSpace($answer)) { $default } else { $answer }
}

function Show-InstallLog([string]$logPath) {
    # Search the log in $HOME too in case the installer chose a default path.
    $candidates = @($logPath, "$env:USERPROFILE\python-$latestVersion-install.log")
    foreach ($f in $candidates) {
        if (Test-Path $f) {
            Write-Host "--- Install log: $f ---" -ForegroundColor Yellow
            Get-Content $f | Select-Object -Last 40 | ForEach-Object { Write-Host $_ }
            return
        }
    }
    Write-Host "Install log not found (checked: $($candidates -join ', '))" -ForegroundColor DarkGray
}

# ---------------------------------------------------------------------------
# Step 1: Python base directory
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== Step 1: Python install location ===" -ForegroundColor White
$PythonBase = Prompt-WithDefault "Where should Python be installed?" "C:\fs\python"

# ---------------------------------------------------------------------------
# Step 2: Find latest Python 3.13.x
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== Step 2: Finding latest Python 3.13 release ===" -ForegroundColor White
Write-Host "Fetching release list from python.org..."

try {
    $ftpPage = Invoke-WebRequest -Uri "https://www.python.org/ftp/python/" -UseBasicParsing
} catch {
    Write-Host "ERROR: Could not reach python.org: $_" -ForegroundColor Red
    exit 1
}

$latestVersion = [regex]::Matches($ftpPage.Content, 'href="(3\.13\.(\d+))/"') |
    ForEach-Object { [version]$_.Groups[1].Value } |
    Sort-Object -Descending |
    Select-Object -First 1

if ($null -eq $latestVersion) {
    Write-Host "ERROR: Could not determine latest Python 3.13 version." -ForegroundColor Red
    exit 1
}

Write-Host "Latest Python 3.13: $latestVersion" -ForegroundColor Cyan

# Python is installed into a version-named subdirectory, e.g. C:\fs\python\3.13.13
$PythonDir = Join-Path $PythonBase "$latestVersion"
$pythonExe = Join-Path $PythonDir "python.exe"

# ---------------------------------------------------------------------------
# Steps 3 & 4: Download and install Python
# Skipped with a prompt if $PythonDir already exists.
# ---------------------------------------------------------------------------

# Check whether Python 3.13 is already registered in the Windows registry.
# If it is, the installer will run as a Modify/Repair and ignore TargetDir,
# so we must detect and report a conflict before downloading anything.
$regPaths = @(
    "HKCU:\Software\Python\PythonCore\3.13\InstallPath",
    "HKLM:\Software\Python\PythonCore\3.13\InstallPath",
    "HKLM:\Software\WOW6432Node\Python\PythonCore\3.13\InstallPath"
)
$existingDir = $null
foreach ($regPath in $regPaths) {
    $val = Get-ItemProperty -Path $regPath -ErrorAction SilentlyContinue
    if ($val -and $val.'(default)') {
        $existingDir = $val.'(default)'.TrimEnd('\')
        break
    }
}
if ($existingDir -and ($existingDir -ne $PythonDir.TrimEnd('\'))) {
    Write-Host ""
    Write-Host "ERROR: Python 3.13 is already installed at a different location:" -ForegroundColor Red
    Write-Host "  $existingDir" -ForegroundColor Yellow
    Write-Host "The installer cannot move an existing installation to $PythonDir." -ForegroundColor Yellow
    Write-Host "Uninstall it first via Settings > Apps > Installed Apps, then re-run this script." -ForegroundColor Yellow
    exit 1
}

if (Test-Path $PythonDir) {
    Write-Host ""
    Write-Host "=== Steps 3 & 4: Python already installed ===" -ForegroundColor White
    Write-Host "Found: $PythonDir" -ForegroundColor DarkGray
    if (Test-Path $pythonExe) { & $pythonExe --version }
    $proceed = Read-Host "Create a new virtual environment using this Python? [Y/n]"
    if ($proceed -eq 'n' -or $proceed -eq 'N') {
        Write-Host "Nothing to do."
        exit 0
    }
} else {
    # -------------------------------------------------------------------------
    # Step 3: Download installer
    # -------------------------------------------------------------------------
    Write-Host ""
    Write-Host "=== Step 3: Downloading installer ===" -ForegroundColor White

    $installerName = "python-$latestVersion-amd64.exe"
    $installerUrl  = "https://www.python.org/ftp/python/$latestVersion/$installerName"
    $installerPath = Join-Path $env:TEMP $installerName
    $installerLog  = Join-Path $PythonDir "python-$latestVersion-install.log"

    if (Test-Path $installerPath) {
        Write-Host "Already downloaded: $installerPath" -ForegroundColor DarkGray
    } else {
        Write-Host "Downloading $installerUrl ..."
        try {
            Invoke-WebRequest -Uri $installerUrl -OutFile $installerPath -UseBasicParsing
        } catch {
            Write-Host "ERROR: Download failed: $_" -ForegroundColor Red
            exit 1
        }
        Write-Host "Saved to $installerPath" -ForegroundColor Green
    }

    # -------------------------------------------------------------------------
    # Step 4: Install Python
    # -------------------------------------------------------------------------
    Write-Host ""
    Write-Host "=== Step 4: Installing Python ===" -ForegroundColor White

    # The target directory must exist before the installer runs, otherwise
    # the Python installer silently falls back to its default location.
    New-Item -ItemType Directory -Force -Path $PythonDir | Out-Null
    Write-Host "Installing to $PythonDir (a progress window will appear)..."

    # /passive  - show progress bar, no user interaction required.
    # /log      - write install log so failures can be diagnosed.
    # Pass as a single string; the installer's own parser handles quoting.
    $installArgs = "/passive InstallAllUsers=0 PrependPath=0 Include_launcher=0 " +
                   "Include_pip=1 TargetDir=`"$PythonDir`" /log `"$installerLog`""
    $proc = Start-Process -FilePath $installerPath -ArgumentList $installArgs -Wait -PassThru

    if ($proc.ExitCode -ne 0) {
        Write-Host "ERROR: Installer exited with code $($proc.ExitCode)." -ForegroundColor Red
        Show-InstallLog $installerLog
        exit 1
    }
    if (-not (Test-Path $pythonExe)) {
        Write-Host "ERROR: python.exe not found at $pythonExe after install." -ForegroundColor Red
        Write-Host "Python may already be registered from a previous run and was repaired" -ForegroundColor Yellow
        Write-Host "to its original location instead of $PythonDir." -ForegroundColor Yellow
        Show-InstallLog $installerLog
        Write-Host "Searching for python.exe in common locations..." -ForegroundColor DarkGray
        @(
            "$env:LOCALAPPDATA\Programs\Python\Python313",
            "$env:LOCALAPPDATA\Programs\Python\Python3.13",
            "C:\Python313", "C:\Python3.13",
            "$env:ProgramFiles\Python313", "$env:ProgramFiles\Python3.13"
        ) | Where-Object { Test-Path "$_\python.exe" } |
            ForEach-Object { Write-Host "  Found: $_\python.exe" -ForegroundColor Cyan }
        exit 1
    }

    Write-Host "Python installed successfully." -ForegroundColor Green
    & $pythonExe --version

    Write-Host "Cleaning up..." -ForegroundColor DarkGray
    Remove-Item $installerPath -Force
    Remove-Item $installerLog  -Force -ErrorAction SilentlyContinue
}

# ---------------------------------------------------------------------------
# Step 5: Virtual environment path
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== Step 5: Virtual environment ===" -ForegroundColor White

$today       = Get-Date -Format "yyyy-MM-dd"
$defaultVenv = "$PythonBase\psxhacks-venv-$today"
$VenvPath    = Prompt-WithDefault "Virtual environment path?" $defaultVenv

# ---------------------------------------------------------------------------
# Step 6: Create venv and install requirements
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== Step 6: Creating venv and installing packages ===" -ForegroundColor White

$requirementsFile = Join-Path $PSScriptRoot "..\requirements.txt"
if (-not (Test-Path $requirementsFile)) {
    Write-Host "ERROR: requirements.txt not found at $requirementsFile" -ForegroundColor Red
    exit 1
}
$requirementsFile = (Resolve-Path $requirementsFile).Path

if (Test-Path (Join-Path $VenvPath "Scripts\python.exe")) {
    Write-Host "Virtual environment already exists at $VenvPath" -ForegroundColor DarkGray
} else {
    Write-Host "Creating virtual environment..."
    & $pythonExe -m venv $VenvPath
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: venv creation failed." -ForegroundColor Red
        exit 1
    }
    Write-Host "Created." -ForegroundColor Green
}

$venvPython = Join-Path $VenvPath "Scripts\python.exe"
$venvPip    = Join-Path $VenvPath "Scripts\pip.exe"

Write-Host "Upgrading pip..."
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    Write-Host "WARNING: pip upgrade failed - continuing anyway." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Installing packages from $requirementsFile ..."
Write-Host "(rasterio and pywin32 may take a moment to download)" -ForegroundColor DarkGray
& $venvPip install -r $requirementsFile
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: pip install failed. See output above." -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== All done! ===" -ForegroundColor Green
Write-Host ""
Write-Host "Virtual environment : $VenvPath" -ForegroundColor Cyan
Write-Host "Python executable   : $venvPython" -ForegroundColor Cyan
Write-Host ""
Write-Host "To use this Python for psxhacks, add this line to your override file"
Write-Host "(psxhacks-start-override.ps1):" -ForegroundColor DarkGray
Write-Host ""
Write-Host ('  $PsxhacksPython = "' + $venvPython + '"') -ForegroundColor Yellow
Write-Host ""
