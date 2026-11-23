# Parse the PowerShell sources listed in a JSON manifest.
#
# ParseInput takes the workflow label and the block's own line numbers, so an
# inline failure points at the YAML step instead of a temporary file that no
# longer exists.
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$Manifest
)

$ErrorActionPreference = 'Stop'
$payload = Get-Content -LiteralPath $Manifest -Raw -Encoding UTF8 | ConvertFrom-Json
$root = [string]$payload.root
$messages = [System.Collections.Generic.List[string]]::new()

function Add-ParseErrors {
    param(
        [string]$Label,
        [System.Management.Automation.Language.ParseError[]]$Errors
    )
    foreach ($parseError in $Errors) {
        $extent = $parseError.Extent
        $messages.Add(
            ('{0}:{1}:{2}: {3}' -f $Label, $extent.StartLineNumber, $extent.StartColumnNumber, $parseError.Message)
        )
    }
}

foreach ($relative in @($payload.scripts)) {
    $tokens = $null
    $errors = $null
    $path = Join-Path $root ([string]$relative)
    [System.Management.Automation.Language.Parser]::ParseFile(
        $path, [ref]$tokens, [ref]$errors
    ) | Out-Null
    Add-ParseErrors -Label ([string]$relative) -Errors $errors
}

foreach ($block in @($payload.blocks)) {
    $tokens = $null
    $errors = $null
    $label = [string]$block.label
    [System.Management.Automation.Language.Parser]::ParseInput(
        [string]$block.text, $label, [ref]$tokens, [ref]$errors
    ) | Out-Null
    Add-ParseErrors -Label $label -Errors $errors
}

if ($messages.Count -gt 0) {
    foreach ($message in $messages) {
        [Console]::Error.WriteLine($message)
    }
    exit 1
}
