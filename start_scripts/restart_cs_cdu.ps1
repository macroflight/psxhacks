. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "CS CDU"
KillProcess "CockpitSimulator*"

try {
    Start-Process $CsCduExe
} catch {
    Write-Output "Error: $_"
    Read-Host -Prompt "Press Enter to close"
}
# Read-Host -Prompt "Press Enter to exit"
