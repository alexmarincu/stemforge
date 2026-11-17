# Build the Windows frozen bundle: PyInstaller onedir plus the Bundle root.
#
# Ticket 10 sections 1-3 and ticket 21. Run on windows-2022 after `uv sync --frozen
# --group build` and `scripts/vendor-fetch.ps1`. The script:
#
#   1. asserts the CPU torch wheel (a PyPI fallback silently pulls CUDA);
#   2. requires the complete pinned vendor tree;
#   3. requires the licence tree for a release build (ticket 22), unless the
#      explicit non-release `-AllowLicencePending` switch is given;
#   4. generates the version resource from stemforge.__version__;
#   5. runs PyInstaller with packaging/pyinstaller/stemforge_windows.spec;
#   6. copies vendor/, the licence tree, and README.txt beside _internal/;
#   7. runs packaging/windows/verify_bundle.py.
#
# vendor/, dist/, and build/ are git-ignored. A failure never leaves a
# half-assembled bundle behind: the previous dist/StemForge is removed first.
[CmdletBinding()]
param(
    [string]$Version,
    [switch]$SkipVendorFetch,
    [switch]$AllowLicencePending
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$Dist = Join-Path $RepoRoot 'dist'
$Bundle = Join-Path $Dist 'StemForge'
$Work = Join-Path $RepoRoot 'build\pyinstaller'
$Reports = Join-Path $RepoRoot 'build\windows'
$Spec = Join-Path $RepoRoot 'packaging\pyinstaller\stemforge_windows.spec'
$Verify = Join-Path $RepoRoot 'packaging\windows\verify_bundle.py'

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "missing $Python. Run 'uv sync --frozen --group build' from $RepoRoot first."
}

function Get-StemForgeVersion {
    $value = (& $Python -c 'import stemforge; print(stemforge.__version__)' | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $value) {
        throw 'cannot read stemforge.__version__ from the build environment'
    }
    return $value
}

function Assert-CpuTorch {
    # A file, not a here-string: Windows PowerShell mangles multi-line native
    # arguments, which turned the old -c assertion into a SyntaxError.
    & $Python (Join-Path $PSScriptRoot 'assert_cpu_torch.py')
    if ($LASTEXITCODE -ne 0) {
        throw 'the build environment does not have the CPU-only torch wheel'
    }
}

function Assert-File([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "missing $Label"
    }
}

function Invoke-Captured([string]$Tool, [string[]]$Arguments, [string]$Label) {
    # Rubber Band writes --version to stderr; capture both streams at the OS
    # level so PowerShell does not turn that into a NativeCommandError.
    $stdout = [System.IO.Path]::GetTempFileName()
    $stderr = [System.IO.Path]::GetTempFileName()
    try {
        $process = Start-Process -FilePath $Tool -ArgumentList $Arguments `
            -NoNewWindow -Wait -PassThru `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        $out = Get-Content -LiteralPath $stdout -Raw -ErrorAction SilentlyContinue
        $err = Get-Content -LiteralPath $stderr -Raw -ErrorAction SilentlyContinue
        if ($process.ExitCode -ne 0) {
            throw "$Label failed; the vendor tree is damaged"
        }
        return (("$out$err").Trim())
    }
    finally {
        Remove-Item -Force -ErrorAction SilentlyContinue -LiteralPath $stdout, $stderr
    }
}

function Add-RuntimeBuildInfo([string]$BundleRoot) {
    $licences = Join-Path $BundleRoot 'LICENSES'
    if (-not (Test-Path -LiteralPath $licences -PathType Container)) {
        return
    }
    $ffmpeg = Join-Path $BundleRoot 'vendor\ffmpeg\ffmpeg.exe'
    $ffmpegInfo = Join-Path $licences 'ffmpeg\BUILDINFO.txt'
    if ((Test-Path -LiteralPath $ffmpeg -PathType Leaf) -and (Test-Path -LiteralPath $ffmpegInfo -PathType Leaf)) {
        $version = Invoke-Captured $ffmpeg @('-version') 'shipped ffmpeg.exe -version'
        $configure = Invoke-Captured $ffmpeg @('-buildconf') 'shipped ffmpeg.exe -buildconf'
        Add-Content -LiteralPath $ffmpegInfo -Encoding UTF8 -Value @(
            '',
            '## Shipped runtime output',
            '',
            'Recorded by packaging/windows/build.ps1 from the bundled executable.',
            '',
            '$ ffmpeg -version',
            $version,
            '',
            '$ ffmpeg -buildconf',
            $configure
        )
    }

    $rubberband = Join-Path $BundleRoot 'vendor\rubberband\rubberband.exe'
    $rubberbandInfo = Join-Path $licences 'rubberband\BUILDINFO.txt'
    if ((Test-Path -LiteralPath $rubberband -PathType Leaf) -and (Test-Path -LiteralPath $rubberbandInfo -PathType Leaf)) {
        $version = Invoke-Captured $rubberband @('--version') 'shipped rubberband.exe --version'
        Add-Content -LiteralPath $rubberbandInfo -Encoding UTF8 -Value @(
            '',
            '## Shipped runtime output',
            '',
            'Recorded by packaging/windows/build.ps1 from the bundled executable.',
            '',
            '$ rubberband --version',
            $version
        )
    }

    $sndfile = Join-Path $BundleRoot 'vendor\rubberband\sndfile.dll'
    $sndfileInfo = Join-Path $licences 'libsndfile\BUILDINFO.txt'
    if ((Test-Path -LiteralPath $sndfile -PathType Leaf) -and (Test-Path -LiteralPath $sndfileInfo -PathType Leaf)) {
        $hash = (Get-FileHash -LiteralPath $sndfile -Algorithm SHA256).Hash.ToLowerInvariant()
        $size = (Get-Item -LiteralPath $sndfile).Length
        Add-Content -LiteralPath $sndfileInfo -Encoding UTF8 -Value @(
            '',
            '## Shipped library',
            '',
            'Recorded by packaging/windows/build.ps1 from the bundled file.',
            "path: vendor/rubberband/sndfile.dll",
            "size: $size",
            "sha256: $hash"
        )
    }
}

function Get-MissingVendor {
    $required = @(
        'vendor\ffmpeg\ffmpeg.exe',
        'vendor\ffmpeg\ffprobe.exe',
        'vendor\deno\deno.exe',
        'vendor\rubberband\rubberband.exe',
        'vendor\rubberband\sndfile.dll'
    )
    return @($required | Where-Object { -not (Test-Path -LiteralPath (Join-Path $RepoRoot $_) -PathType Leaf) })
}

function Get-MissingLicence {
    $required = @('LICENSE', 'THIRD_PARTY_NOTICES.md', 'SOURCE_OFFER.md')
    $missing = @($required | Where-Object { -not (Test-Path -LiteralPath (Join-Path $RepoRoot $_) -PathType Leaf) })
    $licences = Join-Path $RepoRoot 'LICENSES'
    if (-not (Test-Path -LiteralPath $licences -PathType Container)) {
        $missing += 'LICENSES\'
    }
    $native = Join-Path $RepoRoot 'packaging\licenses\native.toml'
    if (-not (Test-Path -LiteralPath $native -PathType Leaf)) {
        $missing += 'packaging\licenses\native.toml'
    }
    return $missing
}

if (-not $Version) {
    $Version = Get-StemForgeVersion
}
if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    throw "version '$Version' is not a release semver (expected e.g. 1.0.0)"
}
$declared = Get-StemForgeVersion
if ($Version -ne $declared) {
    throw "version '$Version' does not match stemforge.__version__ '$declared'"
}

Assert-CpuTorch

if (-not $SkipVendorFetch) {
    Write-Host 'build.ps1: fetching/building the pinned vendor set'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $RepoRoot 'scripts\vendor-fetch.ps1')
    if ($LASTEXITCODE -ne 0) {
        throw 'scripts/vendor-fetch.ps1 failed; the Windows runner needs MSVC, CMake, Meson, Ninja, and pkg-config (or -RubberBandPrebuilt)'
    }
}
$missingVendor = Get-MissingVendor
if ($missingVendor.Count -gt 0) {
    throw "vendor/ is incomplete: $($missingVendor -join ', '); run scripts/vendor-fetch.ps1"
}

$licencePending = $false
$missingLicence = Get-MissingLicence
if ($missingLicence.Count -gt 0) {
    if (-not $AllowLicencePending) {
        throw @"
The licence tree is incomplete: $($missingLicence -join ', ')
A release build must include LICENSE, THIRD_PARTY_NOTICES.md, SOURCE_OFFER.md,
LICENSES/, and packaging/licenses/native.toml (ticket 22). This build fails
closed. -AllowLicencePending exists only for non-release CI evidence and writes
a marker that package.ps1 refuses to package.
"@
    }
    $licencePending = $true
    Write-Warning "licence tree incomplete ($($missingLicence -join ', ')); continuing for non-release evidence only"
}

New-Item -ItemType Directory -Force -Path $Reports | Out-Null
$versionFile = Join-Path $Reports 'version_info.txt'
& $Python (Join-Path $RepoRoot 'packaging\windows\version_info.py') --version $Version --output $versionFile
if ($LASTEXITCODE -ne 0) {
    throw 'packaging/windows/version_info.py failed'
}
Assert-File $versionFile 'the generated version resource'
$env:STEMFORGE_VERSION_FILE = $versionFile

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $Bundle, $Work
New-Item -ItemType Directory -Force -Path $Dist | Out-Null

Write-Host "build.ps1: PyInstaller $Version"
& $Python -m PyInstaller $Spec --noconfirm --clean --distpath $Dist --workpath $Work
if ($LASTEXITCODE -ne 0) {
    throw 'PyInstaller failed'
}

Assert-File (Join-Path $Bundle 'StemForge.exe') 'dist/StemForge/StemForge.exe'
Assert-File (Join-Path $Bundle '_internal\vendor.lock.json') 'dist/StemForge/_internal/vendor.lock.json'
Assert-File (Join-Path $Bundle '_internal\models\models.lock.json') 'dist/StemForge/_internal/models/models.lock.json'

$bundleVendor = Join-Path $Bundle 'vendor'
New-Item -ItemType Directory -Force -Path $bundleVendor | Out-Null
foreach ($name in @('ffmpeg', 'deno', 'rubberband')) {
    $source = Join-Path $RepoRoot "vendor\$name"
    if (-not (Test-Path -LiteralPath $source -PathType Container)) {
        throw "missing vendor/$name"
    }
    Copy-Item -LiteralPath $source -Destination $bundleVendor -Recurse -Force
}

$provenance = Join-Path $RepoRoot 'vendor\PROVENANCE.txt'
if (Test-Path -LiteralPath $provenance -PathType Leaf) {
    Copy-Item -LiteralPath $provenance -Destination (Join-Path $Reports 'vendor-PROVENANCE.txt') -Force
}

if ($licencePending) {
    $marker = @"
Licence tree pending ticket 22.
This bundle was assembled with -AllowLicencePending for non-release CI evidence.
It has no LICENSE, THIRD_PARTY_NOTICES.md, SOURCE_OFFER.md, LICENSES/, or
packaging/licenses/native.toml. package.ps1 refuses to package it.
"@
    Set-Content -LiteralPath (Join-Path $Bundle 'LICENCE-GATE-PENDING.txt') -Value $marker -Encoding UTF8
}
else {
    foreach ($name in @('LICENSE', 'THIRD_PARTY_NOTICES.md', 'SOURCE_OFFER.md')) {
        Copy-Item -LiteralPath (Join-Path $RepoRoot $name) -Destination $Bundle -Force
    }
    Copy-Item -LiteralPath (Join-Path $RepoRoot 'LICENSES') -Destination $Bundle -Recurse -Force
    Add-RuntimeBuildInfo $Bundle
}

Copy-Item -LiteralPath (Join-Path $RepoRoot 'packaging\windows\README.txt') -Destination (Join-Path $Bundle 'README.txt') -Force

$verifyArguments = @(
    $Verify,
    '--bundle', $Bundle,
    '--lock', (Join-Path $RepoRoot 'packaging\vendor.lock.json'),
    '--native-manifest', (Join-Path $RepoRoot 'packaging\licenses\native.toml'),
    '--webview2-lock', (Join-Path $RepoRoot 'packaging\windows\webview2.lock.json'),
    '--report', (Join-Path $Reports 'verify-bundle.json')
)
if ($licencePending) {
    $verifyArguments += '--allow-licence-pending'
}
& $Python @verifyArguments
if ($LASTEXITCODE -ne 0) {
    throw 'packaging/windows/verify_bundle.py failed'
}

$size = (Get-ChildItem -LiteralPath $Bundle -Recurse -File | Measure-Object -Property Length -Sum).Sum
Write-Host ("build.ps1: bundle ready at {0} ({1:N1} MiB)" -f $Bundle, ($size / 1MB))
Write-Host "build.ps1: reports in $Reports"
