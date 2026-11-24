# Run the frozen Windows bundle's diagnostic flags.
#
# --self-test, --smoke-test, and --preflight always
# run. -WithKaraoke runs --smoke-karaoke in the temporary STEMFORGE_HOME before
# any weight install: it needs no weights and no network. -WithSeparation then
# installs the pinned htdemucs weights into that same temporary home (never the
# real profile and never the repository) and runs --smoke-separation. The wall
# time and peak-RSS checks are recorded in the JSON reports and printed here.
#
# Exit code is nonzero when any required flag fails. Reports are written under
# build/reports/ for the CI evidence artifact.
[CmdletBinding()]
param(
    [string]$Bundle,
    [switch]$WithKaraoke,
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
    if ($Name -eq 'smoke-separation' -or $Name -eq 'smoke-karaoke') {
        $wallProperty = $payload.PSObject.Properties['wall_seconds']
        $wall = if ($null -ne $wallProperty) { $wallProperty.Value } else { 'n/a' }
        $rssName = if ($Name -eq 'smoke-karaoke') { 'karaoke.rss' } else { 'separation.rss' }
        $rss = ($payload.checks | Where-Object { $_.name -eq $rssName } | Select-Object -First 1)
        $rssDetail = if ($null -ne $rss) { $rss.detail } else { 'n/a' }
        Write-Host ("smoke-test: {0} wall={1}s rss={2}" -f $Name, $wall, $rssDetail)
    }
    else {
        $server = $payload.PSObject.Properties['server']
        if ($Name -eq 'smoke-test' -and $null -ne $server) {
            Write-Host ("smoke-test: server HTTP {0} in {1}s" -f $server.Value.status, $server.Value.seconds)
        }
    }
}

function Quote-Argument([string]$Value) {
    if ($Value -match '\s') {
        return '"' + $Value + '"'
    }
    return $Value
}

function Invoke-FrozenProcess([string[]]$Arguments) {
    # StemForge.exe is built with console=False. PowerShell does not wait for a
    # GUI-subsystem process and does not set $LASTEXITCODE, so start it
    # explicitly and read the process exit code.
    $quoted = @($Arguments | ForEach-Object { Quote-Argument $_ })
    $process = Start-Process -FilePath $Exe -ArgumentList ($quoted -join ' ') `
        -NoNewWindow -Wait -PassThru
    return $process.ExitCode
}

function Invoke-FrozenCheck(
    [string]$Name,
    [string[]]$Arguments,
    [int[]]$AllowedExitCodes = @(0),
    [switch]$Required
) {
    $report = Join-Path $Reports "$Name.json"
    Write-Host "smoke-test: $Exe $($Arguments -join ' ')"
    $code = Invoke-FrozenProcess (@($Arguments) + @('--report', $report))
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
    # WebView2 presence is a runner property, not a gate.
    # Exit 5 records an absent runtime; exit 6/7/8 are still failures.
    Invoke-FrozenCheck -Name 'preflight' -Arguments @('--preflight') `
        -AllowedExitCodes @(0, 4, 5) -Required

    if ($WithKaraoke) {
        Write-Host 'smoke-test: running the no-model Karaoke gate before any weight install'
        Invoke-FrozenCheck 'smoke-karaoke' @('--smoke-karaoke') -Required
    }
    else {
        Write-Host 'smoke-test: -WithKaraoke not requested; skipping --smoke-karaoke'
    }

    if ($WithSeparation) {
        Write-Host "smoke-test: installing the pinned model into $HomeRoot"
        $installCode = Invoke-FrozenProcess @('--install-model', '--accept-model-licence')
        if ($installCode -ne 0) {
            Write-Host "smoke-test: --install-model FAILED (exit $installCode); --smoke-separation cannot run"
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
