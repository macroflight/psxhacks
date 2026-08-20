. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Restart PSX.NET.GroundHandling"
KillProcess "PSX.NET.GroundHandling"

# PSX.NET.GroundHandling is started from startsim_master.ps1, and is
# mutually exclusive with PSX.NET (see the check in common.ps1).
Start-Process -WorkingDirectory $PsxNetGroundHandlingDir "$PsxNetGroundHandlingDir\PSX.NET.GroundHandling.exe"

#Read-Host -Prompt "Press Enter to exit"
