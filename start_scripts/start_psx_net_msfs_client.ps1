. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Restart MSFS Client"

# This is a separate GUI .exe (unlike most other start_<addon>.ps1 scripts,
# which launch a console-hosted Python addon whose window is the console
# itself) -- its own window takes a moment to appear, so it's launched
# non-blocking (like PSX.NET.VATSIM/SimObjectRouter) and positioned
# afterward, instead of before the process has even started.
Start-Process -WorkingDirectory $PsxNetMsfsClientDir "$PsxNetMsfsClientDir\PSX.NET.MSFS2024.Client.exe"

Delay 5
Invoke-WindowPosition "PSX.NET.MSFS"
