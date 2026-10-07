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

# Fail fast with one clear, actionable error instead of letting a missing
# config file cascade into several confusing ones out of $xml.Load():
# when Load() throws, $xml stays an empty XmlDocument, so
# SelectSingleNode() returns $null, .InnerText on $null fails, and
# Save() then complains about a missing root element - four cryptic
# errors for one real cause. A redirected Documents folder (OneDrive,
# etc.) not reflected in the relevant config-dir variable is a common
# real-world way to hit this (confirmed live).
function RequireConfigFile([string]$ConfigPath, [string]$DirVariableName) {
    if (-not (Test-Path $ConfigPath -PathType Leaf)) {
        Show-ErrorAndExit (
            "Config file not found: $ConfigPath`n" +
            "Check that $DirVariableName in $OverrideFile points at the right " +
            "directory - a redirected Documents folder (OneDrive, etc.) is a " +
            "common cause.")
    }
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

# Called before each addon in startsim_master.ps1/startsim_slave.ps1. A
# plain `Delay $DelayBetweenAddons` would print "Waiting 0 seconds..."
# before every single addon at the default (disabled) setting - this stays
# quiet unless the override file actually set it to something.
function AddonDelay {
    if ($DelayBetweenAddons -gt 0) {
        Delay $DelayBetweenAddons
    }
}

function start_nonscripted_apps {
    foreach ($app in $NonscriptedApps) {
        AddonDelay
        Write-Output "Starting $app..."
        Start-Process $app
    }
}

# Returns start_scripts' own version number (start_scripts.version, next
# to this file) or "unknown" if it can't be read -- mirrors the
# never-crash-on-a-version-read behavior of the Python addons'
# get_version() in psxhacks_version.py. $PSScriptRoot here resolves to
# this file's own directory (start_scripts\) regardless of which script
# dot-sourced common.ps1/functions.ps1 to get here.
function Get-StartScriptsVersion {
    $versionFile = Join-Path $PSScriptRoot "start_scripts.version"
    try {
        return (Get-Content $versionFile -Raw -ErrorAction Stop).Trim()
    } catch {
        return "unknown"
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

# Position (or minimize) an addon's window per its saved entry in
# psxhacks-current-positions.ps1, if $ChangeWindowPositions is on. Called
# from each start_<addon>.ps1, at the end, once the addon's process has
# been launched - not from the startsim_*.ps1/frankencontrol.py callers,
# so positioning always happens consistently regardless of who triggered
# the start. A no-op (with no output) if $ChangeWindowPositions is off.
function Invoke-WindowPosition([string]$addon) {
    if ($ChangeWindowPositions) {
        $name = if ($SimAddonNames.Contains($addon)) { $SimAddonNames[$addon] } else { $addon }
        Write-Output ("Positioning " + $name + "...")
        & "$PSScriptRoot\apply_window_positions.ps1" -Addon $addon
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

# Full path to the file remembering which psxhacks-start-profile-<name>.ps1
# was chosen by Resolve-StartOverrideFile, so a later script in the same
# session (in particular a stop/restart script, which must never prompt)
# can silently reuse the same choice. Lives next to the override file
# itself, one level above the psxhacks checkout.
function Get-StartProfileSelectionFile {
    [System.IO.Path]::GetFullPath("$PSScriptRoot\..\..\psxhacks-start-profile-selected.txt")
}

# Interactive Up/Down/Enter/Escape picker for choosing one of several start
# profiles. Returns the chosen name, or $null if the user pressed Escape.
function Select-StartProfile([string[]]$ProfileNames) {
    $index = 0
    $done = $false
    $cancelled = $false

    Write-Host ""
    Write-Host "Multiple start profiles found. Up/Down to choose, Enter to select, Escape to cancel:" -ForegroundColor Cyan
    Write-Host ""
    $top = [Console]::CursorTop

    while (-not $done) {
        [Console]::SetCursorPosition(0, $top)
        for ($i = 0; $i -lt $ProfileNames.Count; $i++) {
            if ($i -eq $index) {
                Write-Host ("> " + $ProfileNames[$i]).PadRight(60) `
                    -ForegroundColor Black -BackgroundColor White
            } else {
                Write-Host ("  " + $ProfileNames[$i]).PadRight(60)
            }
        }
        $key = [Console]::ReadKey($true)
        switch ($key.Key) {
            'UpArrow'   { $index = ($index - 1 + $ProfileNames.Count) % $ProfileNames.Count }
            'DownArrow' { $index = ($index + 1) % $ProfileNames.Count }
            'Enter'     { $done = $true }
            'Escape'    { $done = $true; $cancelled = $true }
        }
    }
    Write-Host ""
    if ($cancelled) { return $null }
    return $ProfileNames[$index]
}

# Resolve which override file to use when psxhacks-start-override.ps1 (and,
# for a no-router sim, psxhacks-start-override-norouter.ps1) doesn't exist.
# Returns $DefaultOverrideFile unchanged if there is nothing else to try -
# the caller (common.ps1) then reports the normal "override file not
# found" error for it, same as before this function existed.
#
# $IsStopContext (derived by common.ps1 from the name of whichever script
# dot-sourced it - see $MyInvocation.PSCommandPath there) controls whether
# the interactive picker below may run at all: a stop/restart must never
# block the shutdown sequence on a prompt, so it only ever silently reuses
# an already-saved choice (see Get-StartProfileSelectionFile) and never
# shows the picker itself.
#
# For a start script with no saved choice yet: look for
# psxhacks-start-profile-<name>.ps1 files next to where the override file
# would be. None found: fall through to the normal error. One or more
# found: show Select-StartProfile and save the choice for next time (so
# the eventual stop script reuses it without prompting again).
function Resolve-StartOverrideFile([string]$DefaultOverrideFile, [bool]$IsStopContext) {
    $profileDir = [System.IO.Path]::GetDirectoryName($DefaultOverrideFile)
    $selectionFile = Get-StartProfileSelectionFile

    if (Test-Path $selectionFile) {
        $chosen = (Get-Content $selectionFile -Raw).Trim()
        $chosenFile = Join-Path $profileDir "psxhacks-start-profile-$chosen.ps1"
        if (Test-Path $chosenFile) {
            return $chosenFile
        }
        Write-Host "Previously selected start profile '$chosen' no longer exists ($chosenFile) - ignoring it." -ForegroundColor Yellow
    }

    if ($IsStopContext) {
        return $DefaultOverrideFile
    }

    $profileFiles = @(Get-ChildItem -Path $profileDir -Filter "psxhacks-start-profile-*.ps1" `
        -File -ErrorAction SilentlyContinue)
    if ($profileFiles.Count -eq 0) {
        return $DefaultOverrideFile
    }

    $profileNames = $profileFiles | ForEach-Object {
        $_.BaseName -replace '^psxhacks-start-profile-', ''
    } | Sort-Object

    $chosen = Select-StartProfile -ProfileNames $profileNames
    if (-not $chosen) {
        Show-ErrorAndExit "No start profile selected, exiting."
    }

    $chosenFile = Join-Path $profileDir "psxhacks-start-profile-$chosen.ps1"
    Set-Content -Path $selectionFile -Value $chosen
    Write-Host "Using start profile '$chosen' ($chosenFile)" -ForegroundColor Green
    return $chosenFile
}
