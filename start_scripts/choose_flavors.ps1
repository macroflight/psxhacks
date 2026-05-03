. "$PSScriptRoot\common.ps1"

$Host.UI.RawUI.WindowTitle = "Choose Flavors"

$FlavorDefinitions = @(
    [ordered]@{
        Name        = "AirlineIcao"
        Description = "Airline ICAO code"
        Options     = @("BAW", "GTI", "BAN", "DLH", "CLX", "HGO", "MPH", "SIA", "UPS")
        Default     = "BAW"
        Type        = "string"
    },
    [ordered]@{
        Name        = "AirlineIata"
        Description = "Airline IATA code"
        Options     = @("BA", "5Y", "LH", "CL", "HC", "MP", "SQ", "5X")
        Default     = "BA"
        Type        = "string"
    },
    [ordered]@{
        Name        = "VpilotPlugin"
        Description = "vPilot private message plugin"
        Options     = @("Pushover", "PSX Printer")
        Default     = "Pushover"
        Type        = "string"
    },
    [ordered]@{
        Name        = "PsxSoundsRb211"
        Description = "PSX Sounds RB211 volume"
        Options     = @("0", "33", "66", "100")
        Default     = "66"
        Type        = "string"
    },
    [ordered]@{
        Name        = "StartFrankentanker"
        Description = "Start Frankentanker addon"
        Options     = @("master", "slave", "off")
        Default     = "off"
        Type        = "tristate"
    },
    [ordered]@{
        Name        = "StartFrankenfreeze"
        Description = "Start Frankenfreeze addon"
        Options     = @("master", "slave", "off")
        Default     = "off"
        Type        = "tristate"
    },
    [ordered]@{
        Name        = "StartFrankenwind"
        Description = "Start Frankenwind addon"
        Options     = @("master", "slave", "off")
        Default     = "off"
        Type        = "tristate"
    },
    [ordered]@{
        Name        = "StartFrankenturb"
        Description = "Start Frankenturb addon"
        Options     = @("master", "slave", "off")
        Default     = "off"
        Type        = "tristate"
    },
    [ordered]@{
        Name        = "StartFrankenutil"
        Description = "Start Frankenutil addon"
        Options     = @("master", "slave", "off")
        Default     = "off"
        Type        = "tristate"
    },
    [ordered]@{
        Name        = "StartFrankencduproxy"
        Description = "Start FrankenCDU proxy"
        Options     = @("master", "slave", "off")
        Default     = "off"
        Type        = "tristate"
    }
)

# Inject codemap pickers after AirlineIata for any multi-option named sets.
$injected = @()

if ($HoppieLogonCodes -is [hashtable] -and $HoppieLogonCodes.Count -gt 0) {
    $codeNames = @($HoppieLogonCodes.Keys)
    $currentName = ($HoppieLogonCodes.GetEnumerator() |
        Where-Object { $_.Value -eq $HoppieLogonCode } |
        Select-Object -First 1).Key
    if (-not $currentName) { $currentName = $codeNames[0] }
    $injected += @([ordered]@{
        Name        = "HoppieLogonCode"
        Description = "Hoppie network logon code"
        Options     = $codeNames
        Default     = $currentName
        Type        = "codemap"
        CodeMap     = $HoppieLogonCodes
    })
}

if ($SimfestEmails -is [hashtable] -and $SimfestEmails.Count -gt 0) {
    $emailNames = @($SimfestEmails.Keys)
    $currentEmailName = ($SimfestEmails.GetEnumerator() |
        Where-Object { $_.Value -eq $SimfestEmail } |
        Select-Object -First 1).Key
    if (-not $currentEmailName) { $currentEmailName = $emailNames[0] }
    $injected += @([ordered]@{
        Name        = "SimfestEmail"
        Description = "Simfest Portal email address"
        Options     = $emailNames
        Default     = $currentEmailName
        Type        = "codemap"
        CodeMap     = $SimfestEmails
    })
}

if ($injected.Count -gt 0) {
    $FlavorDefinitions = $FlavorDefinitions[0..1] + $injected + $FlavorDefinitions[2..($FlavorDefinitions.Count - 1)]
}

# Parse the flavor file to know which variables are explicitly set there
# (as opposed to inherited from common.ps1).
$flavorFileVars = @{}
if (Test-Path $FlavorFile) {
    Get-Content $FlavorFile | ForEach-Object {
        if ($_ -match '^\$(\w+)\s*=') { $flavorFileVars[$Matches[1]] = $true }
    }
}

# Update each flavor's Default and Options from the current runtime state.
foreach ($flavor in $FlavorDefinitions) {
    if ($flavor.Type -eq "tristate") {
        $preDefault = $PreFlavorDefaults[$flavor.Name]
        $defaultLabel = "use default ($preDefault)"
        $flavor.Options = @("master", "slave", "off", $defaultLabel)
        $flavor.Default = if ($flavorFileVars[$flavor.Name]) {
            $v = Get-Variable -Name $flavor.Name -ErrorAction SilentlyContinue
            if ($null -ne $v) { $v.Value } else { "off" }
        } else {
            $defaultLabel
        }
    } elseif ($flavor.Type -eq "codemap") {
        $v = Get-Variable -Name $flavor.Name -ErrorAction SilentlyContinue
        if ($null -ne $v) {
            $match = $flavor.CodeMap.GetEnumerator() |
                Where-Object { $_.Value -eq $v.Value } |
                Select-Object -First 1
            if ($match) { $flavor.Default = $match.Key }
        }
    } else {
        $v = Get-Variable -Name $flavor.Name -ErrorAction SilentlyContinue
        if ($null -ne $v) { $flavor.Default = $v.Value }
    }
}

function Select-Option([string]$description, [string[]]$options, [string]$current) {
    $idx = [Array]::IndexOf($options, $current)
    if ($idx -lt 0) { $idx = 0 }

    Clear-Host
    Write-Host $description -ForegroundColor White
    $startRow = [Console]::CursorTop

    $draw = {
        [Console]::SetCursorPosition(0, $startRow)
        for ($i = 0; $i -lt $options.Length; $i++) {
            if ($i -eq $idx) {
                Write-Host ("  > " + $options[$i]) -ForegroundColor Cyan
            } else {
                Write-Host ("    " + $options[$i]) -ForegroundColor DarkGray
            }
        }
    }

    [Console]::CursorVisible = $false
    try {
        & $draw
        while ($true) {
            $key = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
            switch ($key.VirtualKeyCode) {
                38 { if ($idx -gt 0)                   { $idx--; & $draw } }  # Up
                40 { if ($idx -lt $options.Length - 1) { $idx++; & $draw } }  # Down
                13 { return $options[$idx] }                                    # Enter
            }
        }
    } finally {
        [Console]::CursorVisible = $true
    }
}

$newValues = @{}
$redo = $false

do {
    foreach ($flavor in $FlavorDefinitions) {
        $newValues[$flavor.Name] = Select-Option $flavor.Description $flavor.Options $flavor.Default
    }

    Write-Host ""
    Write-Host "Summary:" -ForegroundColor White
    foreach ($flavor in $FlavorDefinitions) {
        $val = $newValues[$flavor.Name]
        $display = if ($flavor.Type -eq "tristate" -and $val -like "use default*") {
            $preDefault = $PreFlavorDefaults[$flavor.Name]
            "default ($preDefault)"
        } else { $val }
        Write-Host ("  " + $flavor.Description + ": ") -NoNewline -ForegroundColor DarkGray
        Write-Host $display -ForegroundColor Cyan
    }

    Write-Host ""
    Write-Host "[Enter] Confirm   [R] Redo" -ForegroundColor Yellow
    $key = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    $redo = ($key.Character -eq 'r' -or $key.Character -eq 'R')

    if ($redo) {
        foreach ($flavor in $FlavorDefinitions) {
            $flavor.Default = $newValues[$flavor.Name]
        }
        Clear-Host
    }
} while ($redo)

# Write psxhacks-current-flavor.ps1.
# Bool flavors set to "use default" are omitted so common.ps1 applies.
$lines = @("# Auto-generated by choose_flavors.ps1 - do not edit manually.")
foreach ($flavor in $FlavorDefinitions) {
    $val  = $newValues[$flavor.Name]
    $line = if ($flavor.Type -eq "tristate") {
        if ($val -like "use default*") { $null }
        else { "`$$($flavor.Name) = `"$val`"" }
    } elseif ($flavor.Type -eq "codemap") {
        "`$$($flavor.Name) = `"$($flavor.CodeMap[$val])`""
    } else {
        "`$$($flavor.Name) = `"$val`""
    }
    if ($null -ne $line) { $lines += $line }
}
$lines | Set-Content $FlavorFile -Encoding UTF8

Write-Host ""
Write-Host "Saved to $(Split-Path $FlavorFile -Leaf)"
