. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenRouter SLAVE"

Set-Location $FrankenRouterDir

$slaveConfigFile = ($FrankenrouterSlaveOptions | Where-Object { $_ -like "--config-file=*" }) -replace "^--config-file=", ""
if ([string]::IsNullOrWhiteSpace($slaveConfigFile)) {
    Show-ErrorAndExit "`$FrankenrouterSlaveOptions does not set --config-file.`nEdit $OverrideFile and add --config-file=<name> to `$FrankenrouterSlaveOptions, pointing at a config file in $FrankenrouterDir. See psxhacks-start-override-EXAMPLE.ps1 for an example."
} elseif (-not (Test-Path (Join-Path $FrankenrouterDir $slaveConfigFile) -PathType Leaf)) {
    Show-ErrorAndExit "Frankenrouter slave config file not found: $(Join-Path $FrankenrouterDir $slaveConfigFile)`nCreate it, or edit $OverrideFile and fix the --config-file=<name> entry in `$FrankenrouterSlaveOptions."
}

$repo = Resolve-AddonRepo $FrankenrouterRepo
$env:PYTHONPATH = $repo

& $PsxhacksPython "$repo\router\frankenrouter.py" @FrankenrouterslaveOptions --no-basic-mode

# Read-Host -Prompt "Press Enter to exit"
