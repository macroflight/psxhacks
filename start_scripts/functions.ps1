# Shared helper functions for PSX start/stop scripts.
# Dot-sourced by common.ps1 — do not dot-source this file directly.

# Display an error message, wait for the user to press Enter, then stop
# script execution. Use this for unrecoverable errors that need the user's
# attention before the (console) window closes.
#
# Uses [Environment]::Exit() rather than the "exit" keyword: "exit"
# terminates at most the nearest script-file invoked via the call operator
# (&) - if the .ps1 chain that led here was ever invoked that way (e.g. by
# some file-type-association commands, which run "& '%1'" instead of
# passing -File), a plain "exit" might only unwind that far and let the
# calling script keep going. [Environment]::Exit() is a direct CLR call
# that always terminates the whole OS process, with no such ambiguity.
function Show-ErrorAndExit([string]$message) {
    Write-Host $message -ForegroundColor Red
    Write-Host "Press Enter to exit..." -ForegroundColor Yellow
    Read-Host | Out-Null
    [Environment]::Exit(1)
}

# Display a warning message and wait for the user to press Enter before
# continuing script execution. Use this for recoverable issues the user
# should be aware of but that do not need to stop the script.
function Show-WarningAndContinue([string]$message) {
    Write-Host $message -ForegroundColor Yellow
    Write-Host "Press Enter to continue..." -ForegroundColor Yellow
    Read-Host | Out-Null
}

# Silently stop a process by name; does nothing if the process is not running
function KillProcess([string]$name) {
    Stop-Process -Name $name -Force -ErrorAction SilentlyContinue
}

function KillPythonScript([string]$scriptName) {
    Get-CimInstance Win32_Process -Filter "name LIKE 'python%.exe'" |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*$scriptName*" } |
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

# Verify that every package listed in requirements.txt is installed in the
# configured Python virtual environment ($PsxhacksPython). Called once at
# startup by startsim_master.ps1/startsim_slave.ps1 - not from common.ps1,
# since invoking pip is too slow to do on every single script that dot-
# sources common.ps1 (e.g. a quick restart_frankentanker.ps1).
function Test-PythonRequirement {
    $requirementsFile = Join-Path $PsxhacksDevel "requirements.txt"
    if (-not (Test-Path $requirementsFile -PathType Leaf)) {
        return
    }

    $installed = @{}
    & $PsxhacksPython -m pip list --format=freeze --disable-pip-version-check 2>$null |
        ForEach-Object {
            if ($_ -match '^([^=]+)==') {
                $installed[$Matches[1].ToLowerInvariant()] = $true
            }
        }

    $missing = @()
    Get-Content $requirementsFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -eq '' -or $line.StartsWith('#')) { return }
        # Strip version specifiers/extras/markers, e.g. "aiohttp>=3.9" -> "aiohttp"
        $name = ($line -split '[<>=!~\[;]')[0].Trim()
        if ($name -and -not $installed.ContainsKey($name.ToLowerInvariant())) {
            $missing += $name
        }
    }

    if ($missing.Count -gt 0) {
        Show-ErrorAndExit "Missing Python module(s) in your virtual environment: $($missing -join ', ')`nEdit `$PsxhacksPython in $OverrideFile to point at a virtual environment with these installed, or run:`n  $PsxhacksPython -m pip install -r requirements.txt"
    }
}

# Returns the psxhacks directory for a given addon.
# If $repoName is set, resolves $SimBase\$repoName and verifies it exists;
# otherwise returns $PsxhacksDevel (a $Franken*Repo override should
# normally be $null - only set it when testing a different checkout of
# that specific addon).
function Resolve-AddonRepo([string]$repoName) {
    if ([string]::IsNullOrWhiteSpace($repoName)) { return $PsxhacksDevel }
    $dir = Join-Path $SimBase $repoName
    if (-not (Test-Path $dir -PathType Container)) {
        Show-ErrorAndExit "Alternate psxhacks repo not found: $dir`nCheck the `$Franken*Repo setting pointing at '$repoName' in $OverrideFile (it should normally be `$null - only set it when testing a different checkout of that addon)."
    }
    return $dir
}
