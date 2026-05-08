@{
    ExcludeRules = @(
        # Write-Host is intentional in these scripts — coloured interactive output
        'PSAvoidUsingWriteHost',
        # common.ps1 is a library file dot-sourced by other scripts; its variables
        # are used by callers, not within common.ps1 itself
        'PSUseDeclaredVarsMoreThanAssignments',
        # Files are UTF-8 without BOM; BOM causes issues with some Linux tooling
        'PSUseBOMForUnicodeEncodedFile'
    )
}
