. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "PSX-WINCTRL-PFPx"

# Unlike the psxhacks-native addons, this is a separate, third-party tool
# with its own dedicated Python virtual environment ($PsxWinctrlPfpPython,
# not $PsxhacksPython) -- the port to connect to PSX or FrankenCDUProxy is
# yours to decide, pass it via $PsxWinctrlPfpOptions (e.g. "--psx-port=10746");
# it is deliberately not forced here.
Invoke-WindowPosition "PSX-WINCTRL-PFPx"
& $PsxWinctrlPfpPython "$PsxWinctrlPfpDir\psx_winctrl_pfp.py" @PsxWinctrlPfpOptions
