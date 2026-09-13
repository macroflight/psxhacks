. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Configure Window Positions"
$PositionsFile = "$PSScriptRoot\..\..\psxhacks-current-positions.ps1"

$SimAddons = @($SimAddonNames.Keys)

if (-not ([System.Management.Automation.PSTypeName]'WinPosConf').Type) {
    Add-Type @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

public class WinPosConf {
    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumWindowsProc enumProc, IntPtr lParam);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);

    [DllImport("user32.dll")]
    private static extern bool IsWindowVisible(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);

    [DllImport("user32.dll")]
    public static extern bool MoveWindow(IntPtr hWnd, int X, int Y, int nWidth, int nHeight, bool bRepaint);

    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

    private delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    public const int SW_MINIMIZE = 6;
    public const int SW_RESTORE  = 9;

    [StructLayout(LayoutKind.Sequential)]
    public struct RECT {
        public int Left, Top, Right, Bottom;
    }

    public class WindowInfo {
        public IntPtr Handle;
        public string Title;
    }

    public static List<WindowInfo> GetVisibleWindows() {
        var list = new List<WindowInfo>();
        EnumWindows((hWnd, lParam) => {
            if (IsWindowVisible(hWnd)) {
                var sb = new StringBuilder(256);
                int len = GetWindowText(hWnd, sb, 256);
                if (len > 0)
                    list.Add(new WindowInfo { Handle = hWnd, Title = sb.ToString() });
            }
            return true;
        }, IntPtr.Zero);
        list.Sort((a, b) => string.Compare(a.Title, b.Title, StringComparison.OrdinalIgnoreCase));
        return list;
    }
}
"@
}

function Get-WinRect([IntPtr]$hWnd) {
    $r = New-Object WinPosConf+RECT
    if ([WinPosConf]::GetWindowRect($hWnd, [ref]$r)) { return $r }
    return $null
}

function Select-Addon([hashtable]$positions) {
    $idx = 0
    $total = $SimAddons.Length
    $maxList = [Math]::Max(5, [Console]::WindowHeight - 6)

    $draw = {
        $scrollOffset = 0
        if ($idx -ge $maxList) { $scrollOffset = $idx - $maxList + 1 }
        $displayCount = [Math]::Min($total, $maxList)

        Clear-Host
        Write-Host "=== Configure Window Positions ===" -ForegroundColor White
        Write-Host ""
        Write-Host "Select sim addon:" -ForegroundColor White
        for ($i = 0; $i -lt $displayCount; $i++) {
            $ai = $i + $scrollOffset
            $label = $SimAddonNames[$SimAddons[$ai]]
            $entry = $positions[$SimAddons[$ai]]
            if ($null -ne $entry) {
                if ($entry.DoNotPosition) {
                    $label += " (do not position)"
                } else {
                    $label += " (configured)"
                }
            }
            if ($ai -eq $idx) {
                Write-Host ("  > " + $label) -ForegroundColor Cyan
            } else {
                Write-Host ("    " + $label) -ForegroundColor DarkGray
            }
        }
        $remaining = $total - $scrollOffset - $displayCount
        if ($remaining -gt 0) {
            Write-Host ("    ... and " + $remaining + " more") -ForegroundColor DarkGray
        }
        Write-Host ""
        Write-Host "Up/Down to navigate, Enter to select, Esc to quit" -ForegroundColor Yellow
    }

    [Console]::CursorVisible = $false
    try {
        & $draw
        while ($true) {
            $key = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
            switch ($key.VirtualKeyCode) {
                38 { if ($idx -gt 0)        { $idx--; & $draw } }
                40 { if ($idx -lt $total-1) { $idx++; & $draw } }
                13 { return $SimAddons[$idx] }
                27 { return $null }
            }
        }
    } finally {
        [Console]::CursorVisible = $true
    }
}

function Select-Window([string]$addonName) {
    $allWindows = @([WinPosConf]::GetVisibleWindows())

    # If this addon has a known-title regex (see $KnownWindowTitlePatterns
    # in common.ps1) and it currently identifies exactly one window, offer
    # it as a one-key shortcut pinned at the top of the picker -- ambiguous
    # (0 or 2+) matches fall back to the normal search-to-pick flow below.
    $standardMatch = $null
    if ($KnownWindowTitlePatterns -and $KnownWindowTitlePatterns.Contains($addonName)) {
        $pattern = $KnownWindowTitlePatterns[$addonName]
        $patternHits = @($allWindows | Where-Object { $_.Title -match $pattern })
        if ($patternHits.Count -eq 1) { $standardMatch = $patternHits[0] }
    }

    $search = ""
    $idx = 0
    $maxList = [Math]::Max(5, [Console]::WindowHeight - 9)

    [Console]::CursorVisible = $false
    try {
        while ($true) {
            if ($search -eq "") {
                $filtered = $allWindows
            } else {
                $filtered = @($allWindows | Where-Object { $_.Title -like "*$search*" })
            }
            # Only offered while the search box is empty -- once the user
            # starts typing they're looking for something specific, so the
            # shortcut (which is otherwise always "first") would just be
            # noise mixed into search results it has no relation to.
            $showStandard = ($null -ne $standardMatch -and $search -eq "")
            $standardOffset = if ($showStandard) { 1 } else { 0 }
            $total = $filtered.Count + $standardOffset

            if ($idx -ge $total -and $total -gt 0) { $idx = $total - 1 }
            if ($total -eq 0) { $idx = 0 }

            $scrollOffset = 0
            if ($idx -ge $maxList) { $scrollOffset = $idx - $maxList + 1 }

            Clear-Host
            Write-Host "=== Configure Window Positions ===" -ForegroundColor White
            Write-Host ("Addon: " + $addonName) -ForegroundColor Cyan
            Write-Host ""
            Write-Host ("Search: " + $search + "_") -ForegroundColor Yellow
            Write-Host ""

            $displayCount = [Math]::Min($total, $maxList)
            for ($i = 0; $i -lt $displayCount; $i++) {
                $wi = $i + $scrollOffset
                if ($wi -ge $total) { continue }
                if ($showStandard -and $wi -eq 0) {
                    $label = "Use standard match: " + $standardMatch.Title
                    if ($wi -eq $idx) {
                        Write-Host ("  > " + $label) -ForegroundColor Green
                    } else {
                        Write-Host ("    " + $label) -ForegroundColor Green
                    }
                    continue
                }
                $w = $filtered[$wi - $standardOffset]
                if ($wi -eq $idx) {
                    Write-Host ("  > " + $w.Title) -ForegroundColor Cyan
                } else {
                    Write-Host ("    " + $w.Title) -ForegroundColor DarkGray
                }
            }
            $remaining = $total - $scrollOffset - $displayCount
            if ($remaining -gt 0) {
                Write-Host ("    ... and " + $remaining + " more - refine search to narrow") -ForegroundColor DarkGray
            }
            if ($total -eq 0) {
                Write-Host "    (no matching windows)" -ForegroundColor DarkGray
            }

            Write-Host ""
            Write-Host "Type to search, Up/Down to navigate, Enter to select, Esc to go back" -ForegroundColor Yellow

            $key = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
            switch ($key.VirtualKeyCode) {
                38 { if ($idx -gt 0)        { $idx-- } }
                40 { if ($idx -lt $total-1) { $idx++ } }
                13 {
                    if ($total -gt 0) {
                        if ($showStandard -and $idx -eq 0) { return $standardMatch }
                        return $filtered[$idx - $standardOffset]
                    }
                }
                27 { return $null }
                8  {
                    if ($search.Length -gt 0) {
                        $search = $search.Substring(0, $search.Length - 1)
                        $idx = 0
                    }
                }
                default {
                    $c = $key.Character
                    if ([int][char]$c -ge 32) {
                        $search += $c
                        $idx = 0
                    }
                }
            }
        }
    } finally {
        [Console]::CursorVisible = $true
    }
}

function Watch-WindowPosition([object]$window, [string]$addonName) {
    Clear-Host
    Write-Host "=== Configure Window Positions ===" -ForegroundColor White
    Write-Host ("Addon:  " + $addonName) -ForegroundColor Cyan
    Write-Host ("Window: " + $window.Title) -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Move the window to the desired position, then press Enter." -ForegroundColor Yellow
    Write-Host "Press Esc to cancel." -ForegroundColor DarkGray
    Write-Host ""
    $posRow = [Console]::CursorTop
    Write-Host "  (reading...)" -ForegroundColor DarkGray

    [Console]::CursorVisible = $false
    try {
        while ($true) {
            $r = Get-WinRect $window.Handle
            [Console]::SetCursorPosition(0, $posRow)
            if ($null -ne $r) {
                $w = $r.Right - $r.Left
                $h = $r.Bottom - $r.Top
                Write-Host ("  X=" + $r.Left + "  Y=" + $r.Top + "  Width=" + $w + "  Height=" + $h + "    ") -ForegroundColor Cyan
            } else {
                Write-Host "  (window not found - did it close?)    " -ForegroundColor Red
            }

            if ([Console]::KeyAvailable) {
                $key = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
                if ($key.VirtualKeyCode -eq 13) { break }
                if ($key.VirtualKeyCode -eq 27) { return $null }
            }
            Start-Sleep -Milliseconds 100
        }
    } finally {
        [Console]::CursorVisible = $true
    }

    return Get-WinRect $window.Handle
}

function Read-YesNo([string]$prompt, [bool]$defaultYes) {
    $hint = if ($defaultYes) { "[Y/n]" } else { "[y/N]" }
    Write-Host ($prompt + " " + $hint + " ") -NoNewline -ForegroundColor Yellow
    [Console]::CursorVisible = $true
    while ($true) {
        $key = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
        $c = $key.Character
        if ($c -eq 'y' -or $c -eq 'Y') { Write-Host "Yes"; return $true }
        if ($c -eq 'n' -or $c -eq 'N') { Write-Host "No";  return $false }
        if ($key.VirtualKeyCode -eq 13) {
            $label = if ($defaultYes) { "Yes" } else { "No" }
            Write-Host $label
            return $defaultYes
        }
    }
}

function Read-AddonAction([string]$addonName, [hashtable]$positions) {
    $entry = $positions[$addonName]
    $status = if ($null -eq $entry) {
        "not configured"
    } elseif ($entry.DoNotPosition) {
        "marked as 'do not position'"
    } else {
        "configured (" + $entry.Title + ")"
    }

    Clear-Host
    Write-Host "=== Configure Window Positions ===" -ForegroundColor White
    Write-Host ("Addon: " + $addonName) -ForegroundColor Cyan
    Write-Host ("Current status: " + $status) -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  [S] Set/update position" -ForegroundColor White
    Write-Host "  [D] Mark as 'do not position' (some apps misbehave when moved/resized)" -ForegroundColor White
    Write-Host "  [Esc] Back" -ForegroundColor DarkGray
    Write-Host ""

    [Console]::CursorVisible = $false
    try {
        while ($true) {
            $key = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
            $c = $key.Character
            if ($c -eq 's' -or $c -eq 'S') { return 'set' }
            if ($c -eq 'd' -or $c -eq 'D') { return 'donotposition' }
            if ($key.VirtualKeyCode -eq 27) { return $null }
        }
    } finally {
        [Console]::CursorVisible = $true
    }
}

function Get-PositionData {
    $positions = @{}
    if (Test-Path $PositionsFile) {
        try {
            $WindowPositions = $null
            . $PositionsFile
            if ($null -ne $WindowPositions) { $positions = $WindowPositions }
        } catch {
            Write-Host "Warning: could not parse existing positions file." -ForegroundColor Yellow
        }
    }
    return $positions
}

function Export-PositionData([hashtable]$positions) {
    $lines = @("# Auto-generated by configure_window_positions.ps1 - do not edit manually.")
    $lines += '$WindowPositions = @{}'
    foreach ($key in ($positions.Keys | Sort-Object)) {
        $e = $positions[$key]
        $k = $key -replace "'", "''"
        if ($e.DoNotPosition) {
            $lines += "`$WindowPositions['" + $k + "'] = @{ DoNotPosition = `$true }"
        } else {
            $t = $e.Title   -replace "'", "''"
            $m = if ($e.Minimized) { '$true' } else { '$false' }
            $lines += "`$WindowPositions['" + $k + "'] = @{ Title = '" + $t + "'; X = " + $e.X + "; Y = " + $e.Y + "; Width = " + $e.Width + "; Height = " + $e.Height + "; Minimized = " + $m + " }"
        }
    }
    $lines | Set-Content $PositionsFile -Encoding UTF8
}

function Remove-OrphanedPosition {
    <#
    Prune saved positions for addons that are no longer in $SimAddonNames
    -- e.g. after an addon is removed or renamed in common.ps1. Left in
    place, an orphaned entry's Title can still fuzzy-match some other,
    unrelated running addon's window (apply_window_positions.ps1 falls
    back to a "contains anywhere" match when nothing matches exactly) and
    silently reposition/minimize it under the wrong addon's saved
    coordinates. Confirmed live: a leftover 'PSX.NET' entry (Title =
    'PSX.NET', from before PSX.NET.Orchestration replaced it) grabbed
    BACARS's window and minimized it, since BACARS is now part of the
    PSX.NET suite and its real window title contains that substring --
    even though BACARS itself had a correct, separate DoNotPosition entry.
    #>
    [CmdletBinding(SupportsShouldProcess)]
    param()

    $positions = Get-PositionData
    $orphans = @($positions.Keys | Where-Object { $_ -notin $SimAddons } | Sort-Object)
    if ($orphans.Count -eq 0) { return }

    Write-Host "=== Configure Window Positions ===" -ForegroundColor White
    Write-Host ""
    Write-Host "Removing saved window position(s) for addon(s) no longer in use:" -ForegroundColor Yellow
    foreach ($key in $orphans) {
        if ($PSCmdlet.ShouldProcess($key, 'Remove orphaned window position')) {
            Write-Host ("  - " + $key) -ForegroundColor DarkGray
            $positions.Remove($key)
        }
    }
    if ($PSCmdlet.ShouldProcess($PositionsFile, 'Save pruned window positions')) {
        Export-PositionData $positions
    }
    Write-Host ""
    Write-Host "Press any key to continue..." -ForegroundColor DarkGray
    [Console]::CursorVisible = $false
    try {
        $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown") | Out-Null
    } finally {
        [Console]::CursorVisible = $true
    }
}

Remove-OrphanedPosition

# Main loop
while ($true) {
    $positions = Get-PositionData
    $addon = Select-Addon $positions
    if ($null -eq $addon) { break }

    $action = Read-AddonAction $addon $positions
    if ($null -eq $action) { continue }

    if ($action -eq 'donotposition') {
        $positions[$addon] = @{ DoNotPosition = $true }
        Export-PositionData $positions

        Write-Host ""
        Write-Host "Saved." -ForegroundColor Green
        Write-Host ("  Addon:  " + $addon) -ForegroundColor DarkGray
        Write-Host "  Status: do not position" -ForegroundColor DarkGray
        Write-Host ("  File:   " + (Split-Path $PositionsFile -Leaf)) -ForegroundColor DarkGray
        Write-Host ""

        $again = Read-YesNo "Configure another window?" $true
        if (-not $again) { break }
        continue
    }

    $window = Select-Window $addon
    if ($null -eq $window) { continue }

    $rect = Watch-WindowPosition $window $addon
    if ($null -eq $rect) { continue }

    Write-Host ""
    $save = Read-YesNo "Save position for '$addon' ($($window.Title))?" $true
    if (-not $save) { continue }

    $minimized = Read-YesNo "Start this window minimized?" $false

    $positions = Get-PositionData
    $positions[$addon] = @{
        Title     = $window.Title
        X         = $rect.Left
        Y         = $rect.Top
        Width     = $rect.Right - $rect.Left
        Height    = $rect.Bottom - $rect.Top
        Minimized = $minimized
    }
    Export-PositionData $positions

    $pw = $rect.Right - $rect.Left
    $ph = $rect.Bottom - $rect.Top
    Write-Host ""
    Write-Host "Saved." -ForegroundColor Green
    Write-Host ("  Addon:     " + $addon) -ForegroundColor DarkGray
    Write-Host ("  Window:    " + $window.Title) -ForegroundColor DarkGray
    Write-Host ("  Position:  X=" + $rect.Left + " Y=" + $rect.Top + " Width=" + $pw + " Height=" + $ph) -ForegroundColor DarkGray
    Write-Host ("  Minimized: " + $minimized) -ForegroundColor DarkGray
    Write-Host ("  File: " + (Split-Path $PositionsFile -Leaf)) -ForegroundColor DarkGray
    Write-Host ""

    $again = Read-YesNo "Configure another window?" $true
    if (-not $again) { break }
}

Write-Host ""
Write-Host "Done." -ForegroundColor Green
