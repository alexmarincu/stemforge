# Package the Windows bundle: portable ZIP plus per-user Inno Setup installer.
#
# Ticket 10 section 6 and ticket 21. Both artifacts are built from the same
# dist/StemForge tree:
#
#   StemForge-<ver>-win64-portable.zip   the tree plus the WebView2 bootstrapper
#   StemForge-<ver>-win64-setup.exe      the same tree via Inno Setup 6
#   SHA256SUMS                           GNU coreutils form, basenames only
#   UNSIGNED.txt                         the SmartScreen / unsigned note
#
# The Inno compiler receives /DVersion=<ver> and /DWebView2Installer=<verified
# path>; stemforge.iss embeds the bootstrapper as a dontcopy file. The build
# refuses a bundle carrying LICENCE-GATE-PENDING.txt: a release artifact must
# contain the ticket 22 licence tree.
[CmdletBinding()]
param(
    [string]$Version,
    [string]$Bundle,
    [string]$OutputDir,
    [switch]$SkipZip,
    [switch]$SkipInno
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$Reports = Join-Path $RepoRoot 'build\windows'
$WebViewLockPath = Join-Path $RepoRoot 'packaging\windows\webview2.lock.json'
$Verify = Join-Path $RepoRoot 'packaging\windows\verify_bundle.py'
$ShaTool = Join-Path $RepoRoot 'packaging\windows\sha256sums.py'

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "missing $Python. Run 'uv sync --frozen --group build' first."
}
if (-not $Bundle) {
    $Bundle = Join-Path $RepoRoot 'dist\StemForge'
}
if (-not $OutputDir) {
    $OutputDir = Join-Path $RepoRoot 'dist'
}
if (-not (Test-Path -LiteralPath $Bundle -PathType Container)) {
    throw "missing bundle directory $Bundle; run packaging/windows/build.ps1 first"
}
if (Test-Path -LiteralPath (Join-Path $Bundle 'LICENCE-GATE-PENDING.txt') -PathType Leaf) {
    throw "refusing to package ${Bundle}: the licence tree is pending ticket 22. Rebuild without -AllowLicencePending."
}
foreach ($name in @('LICENSE', 'THIRD_PARTY_NOTICES.md', 'SOURCE_OFFER.md')) {
    if (-not (Test-Path -LiteralPath (Join-Path $Bundle $name) -PathType Leaf)) {
        throw "refusing to package: $Bundle\$name is missing (the licence tree is incomplete)"
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $Bundle 'LICENSES') -PathType Container)) {
    throw "refusing to package: $Bundle\LICENSES is missing (the licence tree is incomplete)"
}

if (-not $Version) {
    $Version = (& $Python -c 'import stemforge; print(stemforge.__version__)' | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $Version) {
        throw 'cannot read stemforge.__version__'
    }
}
if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    throw "version '$Version' is not a release semver"
}
$declared = (& $Python -c 'import stemforge; print(stemforge.__version__)' | Out-String).Trim()
if ($Version -ne $declared) {
    throw "version '$Version' does not match stemforge.__version__ '$declared'"
}

function Get-Iscc {
    $candidates = New-Object System.Collections.Generic.List[string]
    if (${env:ProgramFiles(x86)}) {
        $candidates.Add((Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'))
    }
    if ($env:ProgramFiles) {
        $candidates.Add((Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe'))
    }
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }
    $command = Get-Command 'ISCC.exe' -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }
    throw 'ISCC.exe was not found. Install Inno Setup 6 (choco install innosetup -y) or pass -SkipInno for a ZIP-only evidence build.'
}

function Assert-Sha256([string]$Path, [string]$Expected, [string]$Label) {
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $Expected) {
        throw "SHA-256 mismatch for $Label (expected $Expected, got $actual); refreshing the WebView2 pin is a deliberate release decision"
    }
}

function Get-WebView2Bootstrapper {
    $lock = Get-Content -LiteralPath $WebViewLockPath -Raw | ConvertFrom-Json
    if (-not $lock.url -or -not $lock.sha256 -or -not $lock.filename) {
        throw "$WebViewLockPath is incomplete"
    }
    $cache = Join-Path $RepoRoot 'build\redist'
    New-Item -ItemType Directory -Force -Path $cache | Out-Null
    $destination = Join-Path $cache ([string]$lock.filename)
    if (Test-Path -LiteralPath $destination -PathType Leaf) {
        Assert-Sha256 $destination ([string]$lock.sha256) ([string]$lock.filename)
        $actualSize = (Get-Item -LiteralPath $destination).Length
        if ($actualSize -ne [long]$lock.size) {
            throw "size mismatch for $($lock.filename) (expected $($lock.size), got $actualSize)"
        }
        Write-Host "package.ps1: cached $destination"
        return $destination
    }
    Write-Host "package.ps1: fetching $($lock.url)"
    $partial = "$destination.part"
    try {
        Invoke-WebRequest -UseBasicParsing -Uri ([string]$lock.url) -OutFile $partial
        Assert-Sha256 $partial ([string]$lock.sha256) ([string]$lock.filename)
        $actualSize = (Get-Item -LiteralPath $partial).Length
        if ($actualSize -ne [long]$lock.size) {
            throw "size mismatch for $($lock.filename) (expected $($lock.size), got $actualSize)"
        }
        Move-Item -Force -LiteralPath $partial -Destination $destination
    }
    finally {
        Remove-Item -Force -ErrorAction SilentlyContinue -LiteralPath $partial
    }
    return $destination
}

New-Item -ItemType Directory -Force -Path $OutputDir, $Reports | Out-Null
$bootstrapper = Get-WebView2Bootstrapper

$stageRoot = Join-Path $RepoRoot 'build\package'
$stage = Join-Path $stageRoot "StemForge-$Version-win64"
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $stage
New-Item -ItemType Directory -Force -Path $stage | Out-Null
Copy-Item -Path (Join-Path $Bundle '*') -Destination $stage -Recurse -Force
Copy-Item -LiteralPath $bootstrapper -Destination (Join-Path $stage 'MicrosoftEdgeWebview2Setup.exe') -Force

$zip = Join-Path $OutputDir "StemForge-$Version-win64-portable.zip"
$setup = Join-Path $OutputDir "StemForge-$Version-win64-setup.exe"
Remove-Item -Force -ErrorAction SilentlyContinue $zip, $setup

if (-not $SkipZip) {
    $sevenZip = Get-Command '7z.exe' -ErrorAction SilentlyContinue
    if ($null -ne $sevenZip) {
        Push-Location -LiteralPath $stageRoot
        try {
            & $sevenZip.Source a -tzip -mx=5 -y $zip "StemForge-$Version-win64" | Out-Host
            if ($LASTEXITCODE -ne 0) {
                throw '7z.exe failed to create the portable ZIP'
            }
        }
        finally {
            Pop-Location
        }
    }
    else {
        Compress-Archive -Path $stage -DestinationPath $zip -CompressionLevel Optimal -Force
    }
    Write-Host "package.ps1: wrote $zip"
}

if (-not $SkipInno) {
    $iscc = Get-Iscc
    & $iscc "/DVersion=$Version" "/DWebView2Installer=$bootstrapper" "/O$OutputDir" (Join-Path $RepoRoot 'packaging\windows\stemforge.iss')
    if ($LASTEXITCODE -ne 0) {
        throw 'ISCC.exe failed to compile stemforge.iss'
    }
    if (-not (Test-Path -LiteralPath $setup -PathType Leaf)) {
        throw "ISCC succeeded but $setup is missing"
    }
    Write-Host "package.ps1: wrote $setup"
}

$artifacts = @()
if (-not $SkipZip) {
    $artifacts += $zip
}
if (-not $SkipInno) {
    $artifacts += $setup
}
if ($artifacts.Count -eq 0) {
    throw 'nothing to package: both -SkipZip and -SkipInno were given'
}

$sums = Join-Path $OutputDir 'SHA256SUMS'
& $Python $ShaTool --output $sums @artifacts
if ($LASTEXITCODE -ne 0) {
    throw 'packaging/windows/sha256sums.py failed'
}
& $Python $ShaTool --check $sums
if ($LASTEXITCODE -ne 0) {
    throw 'packaging/windows/sha256sums.py could not verify its own manifest'
}

$noteLines = @(
    "StemForge $Version is unsigned.",
    '',
    'Windows SmartScreen may show "Windows protected your PC" or "Unknown publisher"',
    'the first time the download is run. This is expected for an unsigned build.',
    'Compare the file hash with SHA256SUMS from the same release page before you',
    'choose "More info" and "Run anyway". Never disable SmartScreen globally.',
    '',
    'SHA-256:'
)
foreach ($artifact in $artifacts) {
    $hash = (Get-FileHash -LiteralPath $artifact -Algorithm SHA256).Hash.ToLowerInvariant()
    $noteLines += "$hash  $(Split-Path -Leaf $artifact)"
}
$noteLines += ''
$noteLines += 'Verify with: certutil -hashfile <file> SHA256'
Set-Content -LiteralPath (Join-Path $OutputDir 'UNSIGNED.txt') -Value $noteLines -Encoding UTF8

$verifyArguments = @(
    $Verify,
    '--bundle', $Bundle,
    '--lock', (Join-Path $RepoRoot 'packaging\vendor.lock.json'),
    '--native-manifest', (Join-Path $RepoRoot 'packaging\licenses\native.toml'),
    '--webview2-lock', $WebViewLockPath,
    '--webview2-installer', $bootstrapper,
    '--report', (Join-Path $Reports 'verify-artifacts.json')
)
if (-not $SkipZip) {
    $verifyArguments += @('--zip', $zip)
}
if (-not $SkipInno) {
    $verifyArguments += @('--setup', $setup)
}
& $Python @verifyArguments
if ($LASTEXITCODE -ne 0) {
    throw 'packaging/windows/verify_bundle.py rejected the release artifacts'
}

foreach ($artifact in $artifacts) {
    $size = (Get-Item -LiteralPath $artifact).Length
    Write-Host ("package.ps1: {0} {1:N1} MiB" -f (Split-Path -Leaf $artifact), ($size / 1MB))
}
Write-Host "package.ps1: wrote $sums and UNSIGNED.txt"
