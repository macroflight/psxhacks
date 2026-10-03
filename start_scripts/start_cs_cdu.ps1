. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "CS CDU"

try {
    Start-Process $CsCduExe
} catch {
    Write-Output "Error: $_"
    Read-Host -Prompt "Press Enter to close"
}
