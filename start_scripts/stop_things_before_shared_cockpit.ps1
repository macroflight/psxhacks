. "$PSScriptRoot\common.ps1"

# Stop anything that is known to be incompatible with shared cockpit
# or where we should only run one instance in a shared cockpit setup
# (and therefore normally do it on the master sim).

Write-Host "Stopping addons we don't want running in shared cockpit mode..."

KillPythonScript "frankenfreeze.py"
KillPythonScript "frankenturb.py"
KillPythonScript "frankentanker.py"
KillPythonScript "psx-acars.py"
KillPythonScript "frankenutil.py"

KillProcess "PSX.Bacars.UI.exe"
KillProcess "PSX.NET"

Delay 1

Read-Host -Prompt "Done. Enter to close."
