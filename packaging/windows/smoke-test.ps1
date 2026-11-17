# Run the frozen Windows bundle's diagnostic flags.
#
# Ticket 11 section 4 and ticket 21. --self-test, --smoke-test, and --preflight always
# run. -WithSeparation installs the pinned htdemucs weights into a temporary
# STEMFORGE_HOME (never the real profile and never the repository), then runs
# --smoke-separation. The wall time and peak-RSS check are recorded in the JSON
# report and printed here.
#
# Exit code is nonzero when any required flag fails. Reports are written under
# build/reports/ for the CI evidence artifact.
[CmdletBinding()]
param(
    [string]$Bundle,
    [switch]$WithSeparation,
    [switch]$KeepHome,
    [string]$HomePath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if (-not $Bundle) {
    $Bundle = Join-Path $RepoRoot 'dist\StemForge'
}
$Exe = Join-Path $Bundle 'StemForge.exe'
$Reports = Join-Path $RepoRoot 'build\reports'

if (-not (Test-Path -LiteralPath $Exe -PathType Leaf)) {
    throw "smoke-test.ps1: missing $Exe; run packaging/windows/build.ps1 first"
}
New-Item -ItemType Directory -Force -Path $Reports | Out-Null

if ($HomePath) {
    # The release workflow restores and saves the pinned weights cache here.
    # It is a scratch directory inside the checkout, never a real profile.
    $HomeRoot = [System.IO.Path]::GetFullPath($HomePath)
}
else {
    $HomeRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("stemforge-smoke-" + [guid]::NewGuid().ToString('N'))
}
New-Item -ItemType Directory -Force -Path $HomeRoot | Out-Null
$env:STEMFORGE_HOME = $HomeRoot
$status = 0

function Write-ReportSummary([string]$Name, [string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        Write-Host "smoke-test: $Name wrote no report at $Path"
        return
    }
    try {
        $payload = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    }
    catch {
        Write-Host "smoke-test: $Name report is not valid JSON: $Path"
        return
    }
    if ($Name -eq 'smoke-separation') {
        $wallProperty = $payload.PSObject.Properties['wall_seconds']
        $wall = if ($null -ne $wallProperty) { $wallProperty.Value } else { 'n/a' }
        $rss = ($payload.checks | Where-Object { $_.name -eq 'separation.rss' } | Select-Object -First 1)
        $rssDetail = if ($null -ne $rss) { $rss.detail } else { 'n/a' }
        Write-Host ("smoke-test: separation wall={0}s rss={1}" -f $wall, $rssDetail)
    }
    else {
        $server = $payload.PSObject.Properties['server']
        if ($Name -eq 'smoke-test' -and $null -ne $server) {
            Write-Host ("smoke-test: server HTTP {0} in {1}s" -f $server.Value.status, $server.Value.seconds)
        }
    }
}

function Invoke-FrozenCheck(
    [string]$Name,
    [string[]]$Arguments,
    [int[]]$AllowedExitCodes = @(0),
    [switch]$Required
) {
    $report = Join-Path $Reports "$Name.json"
    Write-Host "smoke-test: $Exe $($Arguments -join ' ')"
    & $Exe @Arguments --report $report
    $code = $LASTEXITCODE
    Write-ReportSummary $Name $report
    if ($code -notin $AllowedExitCodes) {
        Write-Host "smoke-test: $Name FAILED (exit $code); report: $report"
        if ($Required) {
            $script:status = 1
        }
    }
    elseif ($code -ne 0) {
        Write-Host "smoke-test: $Name recorded exit $code (environment capability); report: $report"
    }
    else {
        Write-Host "smoke-test: $Name passed"
    }
}

try {
    Invoke-FrozenCheck -Name 'self-test' -Arguments @('--self-test') -Required
    Invoke-FrozenCheck -Name 'smoke-test' -Arguments @('--smoke-test') -Required
    # Ticket 11 section 3: WebView2 presence is a runner property, not a gate.
    # Exit 5 records an absent runtime; exit 6/7/8 are still failures.
    Invoke-FrozenCheck -Name 'preflight' -Arguments @('--preflight') `
        -AllowedExitCodes @(0, 4, 5) -Required

    if ($WithSeparation) {
        Write-Host "smoke-test: installing the pinned model into $HomeRoot"
        & $Exe '--install-model' '--accept-model-licence'
        if ($LASTEXITCODE -ne 0) {
            Write-Host 'smoke-test: --install-model FAILED; --smoke-separation cannot run'
            Write-Host 'smoke-test: this needs network access to Hugging Face; the weights stay in the temporary STEMFORGE_HOME'
            $status = 1
        }
        else {
            Invoke-FrozenCheck 'smoke-separation' @('--smoke-separation') -Required
        }
    }
    else {
        Write-Host 'smoke-test: -WithSeparation not requested; skipping --smoke-separation'
    }
}
finally {
    if ($KeepHome) {
        Write-Host "smoke-test: kept temporary home $HomeRoot"
    }
    else {
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue -LiteralPath $HomeRoot
    }
}

if ($status -ne 0) {
    Write-Host "smoke-test: FAILED; reports are in $Reports"
}
else {
    Write-Host "smoke-test: all requested checks passed; reports are in $Reports"
}
exit $status
