. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenStutter"

$repo = Resolve-AddonRepo $FrankenstutterRepo
$env:PYTHONPATH = $repo

# frankenstutter only watches MSFS and the PC: no PSX connection, so no port override.
Invoke-WindowPosition "frankenstutter"
& $PsxhacksPython "$repo\frankenstutter.py" @FrankenstutterOptions
