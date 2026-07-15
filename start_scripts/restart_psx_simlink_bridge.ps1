. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "psx_simlink_bridge"
KillProcess "psx_simlink_bridge*"

# psx_simlink_bridge is started from startsim_master.ps1 and connects to
# the MASTER sim's router port (this is where GPS spoofing/jamming state
# and the true Qs121 position live - see the [psx] gps_spoofing_egress
# router setting), reachable at 127.0.0.1 since it runs on the same PC.
#
# Do NOT use -WindowStyle hidden here - unlike the Java-jar addons
# (SRSL-PSX, CMC-PSX), it breaks this app (its GUI window never renders).
$bridgeDir = Split-Path $PsxSimlinkBridgeExe -Parent

# This is a frozen (PyInstaller) Python app that prints box-drawing
# characters (e.g in its connection status box). With stdout/stderr
# redirected to files rather than a real console, Python falls back to
# the legacy cp1252 codepage instead of UTF-8 and crashes with
# UnicodeEncodeError before it can start - setting $env:PYTHONIOENCODING
# was not enough to prevent this. So, for now, do not redirect
# stdout/stderr: leaving them attached to a real console lets Python use
# its native Windows console UTF-8 output path instead.
Start-Process -WorkingDirectory $bridgeDir -FilePath $PsxSimlinkBridgeExe -ArgumentList "-ip", "127.0.0.1", "-port", "$FrankenrouterMasterPort"
