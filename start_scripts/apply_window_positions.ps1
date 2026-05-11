param(
    [string]$Addon = ""
)

. "$PSScriptRoot\common.ps1"

$PositionsFile = "$PSScriptRoot\..\..\psxhacks-current-positions.ps1"

if (-not (Test-Path $PositionsFile)) {
    Write-Host "Config file not found: $(Split-Path $PositionsFile -Leaf)" -ForegroundColor Red
    Write-Host "Run configure_window_positions.ps1 first to set up window positions."
    exit 1
}

if (-not ([System.Management.Automation.PSTypeName]'WinPosApply').Type) {
    Add-Type @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

public class WinPosApply {
    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumWindowsProc enumProc, IntPtr lParam);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);

    [DllImport("user32.dll")]
    private static extern bool IsWindowVisible(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern bool MoveWindow(IntPtr hWnd, int X, int Y, int nWidth, int nHeight, bool bRepaint);

    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

    private delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    public const int SW_MINIMIZE = 6;
    public const int SW_RESTORE  = 9;

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
        return list;
    }
}
"@
}

$WindowPositions = $null
try {
    . $PositionsFile
} catch {
    Write-Host "Error loading positions file: $_" -ForegroundColor Red
    exit 1
}

if ($null -eq $WindowPositions -or $WindowPositions.Count -eq 0) {
    Write-Host "No window positions configured in $(Split-Path $PositionsFile -Leaf)." -ForegroundColor Yellow
    exit 0
}

Write-Host "=== Apply Window Positions ===" -ForegroundColor White
Write-Host ""

$found = 0
$missed = 0

if ($Addon -ne "" -and -not $WindowPositions.ContainsKey($Addon)) {
    Write-Host ("No position configured for '" + $Addon + "'.") -ForegroundColor Red
    Write-Host ("Configured addons: " + (($WindowPositions.Keys | Sort-Object) -join ", ")) -ForegroundColor DarkGray
    exit 1
}

$keys = if ($Addon -ne "") { @($Addon) } else { $WindowPositions.Keys | Sort-Object }

foreach ($addon in $keys) {
    $entry       = $WindowPositions[$addon]
    $title       = $entry.Title
    $displayName = if ($SimAddonNames -and $SimAddonNames.Contains($addon)) { $SimAddonNames[$addon] } else { $addon }

    $match   = $null
    $elapsed = 0.0
    while ($null -eq $match -and $elapsed -lt $WindowPositionSleepSecondsMax) {
        $windows = [WinPosApply]::GetVisibleWindows()
        $match = $windows | Where-Object { $_.Title -eq $title }    | Select-Object -First 1
        if ($null -eq $match) {
            $match = $windows | Where-Object { $_.Title -like "$title*" }  | Select-Object -First 1
        }
        if ($null -eq $match) {
            $match = $windows | Where-Object { $_.Title -like "*$title*" } | Select-Object -First 1
        }
        if ($null -eq $match) {
            Start-Sleep -Milliseconds ([int]($WindowPositionSleepSeconds * 1000))
            $elapsed += $WindowPositionSleepSeconds
        }
    }

    if ($null -ne $match) {
        $found++
        Write-Host ($displayName + ": ") -NoNewline -ForegroundColor Cyan
        Write-Host $match.Title -NoNewline -ForegroundColor White
        [WinPosApply]::ShowWindow($match.Handle, [WinPosApply]::SW_RESTORE) | Out-Null
        [WinPosApply]::MoveWindow($match.Handle, $entry.X, $entry.Y, $entry.Width, $entry.Height, $true) | Out-Null
        if ($entry.Minimized) {
            [WinPosApply]::ShowWindow($match.Handle, [WinPosApply]::SW_MINIMIZE) | Out-Null
            Write-Host " -> minimized" -ForegroundColor DarkGray
        } else {
            Write-Host " -> positioned" -ForegroundColor DarkGray
        }
    } else {
        $missed++
        Write-Host ($displayName + ": not found after " + $WindowPositionSleepSecondsMax + "s") -ForegroundColor DarkGray
        Write-Host ("  (looking for '" + $title + "')") -ForegroundColor DarkGray
    }
}

Write-Host ""
$color = if ($missed -eq 0) { "Green" } else { "Yellow" }
Write-Host ("Positioned: " + $found + "  Not found: " + $missed) -ForegroundColor $color
