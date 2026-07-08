. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "FrankenRouter MASTER"

Set-Location $FrankenRouterDir

$masterConfigFile = ($FrankenrouterMasterOptions | Where-Object { $_ -like "--config-file=*" }) -replace "^--config-file=", ""
if ([string]::IsNullOrWhiteSpace($masterConfigFile)) {
    Show-ErrorAndExit "`$FrankenrouterMasterOptions does not set --config-file.`nEdit $OverrideFile and add --config-file=<name> to `$FrankenrouterMasterOptions, pointing at a config file in $FrankenrouterDir. See psxhacks-start-override-EXAMPLE.ps1 for an example."
} elseif (-not (Test-Path (Join-Path $FrankenrouterDir $masterConfigFile) -PathType Leaf)) {
    Show-ErrorAndExit "Frankenrouter master config file not found: $(Join-Path $FrankenrouterDir $masterConfigFile)`nCreate it, or edit $OverrideFile and fix the --config-file=<name> entry in `$FrankenrouterMasterOptions."
}

$repo = Resolve-AddonRepo $FrankenrouterRepo
$env:PYTHONPATH = $repo

& $PsxhacksPython "$repo\router\frankenrouter.py" @FrankenrouterMasterOptions --no-basic-mode

# Read-Host -Prompt "Press Enter to exit"
