. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Stop PSX-WINCTRL-PFPx"
KillPythonScript "psx_winctrl_pfp.py"
