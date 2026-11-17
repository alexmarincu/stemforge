# Fetch, verify, and lay out the pinned vendor set on Windows (ticket 19).
#
# Inputs:  packaging/vendor.lock.json (the only pin list)
# Output:  vendor\{ffmpeg,deno,rubberband}\...
#
# FFmpeg and Deno are downloaded from the pinned archives and SHA-256-verified.
# Rubber Band 4.0.0 and libsndfile 1.2.2 are built from their pinned source
# archives with MSVC, CMake, and Meson on windows-2022 (ticket 10 section 8 and
# ticket 21). The configure/build lines are read from packaging/vendor.lock.json.
#
# A pinned prebuilt pair is still accepted for environments without the MSVC
# toolchain (the decision record names no public prebuilt archive):
#
#   powershell -File scripts/vendor-fetch.ps1 `
#       -RubberBandPrebuilt C:\path\to\prebuilt-or.zip `
#       -RubberBandPrebuiltSha256 <sha256 of the zip>
#
# The expected directory contains rubberband.exe (which must report 4.0.0) and
# sndfile.dll. A zip is verified against -RubberBandPrebuiltSha256.
#
# vendor/ is git-ignored. A failure never replaces an existing vendor file.
[CmdletBinding()]
param(
    [switch]$VerifyOnly,
    [switch]$Force,
    [string]$RubberBandPrebuilt = $env:STEMFORGE_RUBBERBAND_PREBUILT,
    [string]$RubberBandPrebuiltSha256 = $env:STEMFORGE_RUBBERBAND_PREBUILT_SHA256,
    [int]$Jobs = [Environment]::ProcessorCount
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$LockPath = Join-Path $RepoRoot 'packaging\vendor.lock.json'
$Vendor = Join-Path $RepoRoot 'vendor'
$Cache = Join-Path $Vendor '.cache'
$TargetName = 'windows-x64'
$Components = @('ffmpeg', 'ffprobe', 'deno', 'libsndfile', 'rubberband')

$VenvScripts = Join-Path $RepoRoot '.venv\Scripts'
if (Test-Path -LiteralPath $VenvScripts -PathType Container) {
    $env:PATH = "$VenvScripts;$env:PATH"
}

function Write-Note([string]$Message) {
    Write-Host "vendor-fetch: $Message"
}

function Get-Lock {
    if (-not (Test-Path -LiteralPath $LockPath -PathType Leaf)) {
        throw "missing $LockPath"
    }
    return Get-Content -LiteralPath $LockPath -Raw | ConvertFrom-Json
}

function Get-Component($Lock, [string]$Name) {
    $property = $Lock.components.PSObject.Properties[$Name]
    if ($null -eq $property) {
        throw "vendor.lock.json has no component '$Name'"
    }
    return $property.Value
}

function Get-Target($Record, [string]$Target) {
    $property = $Record.targets.PSObject.Properties[$Target]
    if ($null -eq $property) {
        throw "vendor.lock.json has no target '$Target'"
    }
    return $property.Value
}

function Get-Source($Entry) {
    if ($Entry.kind -eq 'built') {
        return $Entry.source
    }
    return $Entry
}

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Assert-Sha256([string]$Path, [string]$Expected, [string]$Label) {
    if ($Expected -notmatch '^[0-9a-f]{64}$') {
        throw "no valid SHA-256 recorded for $Label"
    }
    $actual = Get-Sha256 $Path
    if ($actual -ne $Expected) {
        throw "SHA-256 mismatch for $Label (expected $Expected, got $actual); refusing to use it"
    }
}

function Assert-File([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "missing: $Label"
    }
}

function Get-Archive($Lock, [string]$Name) {
    $record = Get-Component $Lock $Name
    $entry = Get-Target $record $TargetName
    $source = Get-Source $entry
    $filename = [string]$source.filename
    $sha = [string]$source.sha256
    $size = [long]$source.size
    $url = [string]$source.url
    if (-not $filename -or -not $url) {
        throw "$Name has no archive URL in vendor.lock.json"
    }
    New-Item -ItemType Directory -Force -Path $Cache | Out-Null
    $destination = Join-Path $Cache $filename

    if (Test-Path -LiteralPath $destination -PathType Leaf) {
        Assert-Sha256 $destination $sha $filename
        Write-Note "cached  $filename"
        return $destination
    }

    Write-Note "fetch   $url"
    $partial = "$destination.part"
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $partial
        Assert-Sha256 $partial $sha $filename
        $actualSize = (Get-Item -LiteralPath $partial).Length
        if ($actualSize -ne $size) {
            throw "size mismatch for $filename (expected $size, got $actualSize)"
        }
        Move-Item -Force -LiteralPath $partial -Destination $destination
    }
    catch {
        Remove-Item -Force -ErrorAction SilentlyContinue -LiteralPath $partial
        throw
    }
    Write-Note "verified $filename $size bytes sha256=$sha"
    return $destination
}

function Expand-ZipArchive([string]$Archive, [string]$Destination) {
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    Expand-Archive -LiteralPath $Archive -DestinationPath $Destination -Force
}

function Expand-TarArchive(
    [string]$Archive,
    [string]$Destination,
    [int]$Strip = 0
) {
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    # windows-2022 and windows-2025 ship different bsdtar builds; the 2022 one
    # stalled on libsndfile's .tar.xz in the first release run. Python's stdlib
    # tarfile (with lzma) is the same everywhere and rejects path escapes.
    $python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        $python = 'python'
    }
    Write-Note "extract $Archive -> $Destination (strip $Strip)"
    & $python (Join-Path $PSScriptRoot 'vendor_unpack.py') `
        --archive $Archive --destination $Destination --strip $Strip
    if ($LASTEXITCODE -ne 0) {
        throw "vendor_unpack.py failed to extract $Archive"
    }
}

function Expand-TarXz([string]$Archive, [string]$Destination, [int]$Strip = 0) {
    Expand-TarArchive $Archive $Destination $Strip
}

function Expand-TarGz([string]$Archive, [string]$Destination, [int]$Strip = 0) {
    Expand-TarArchive $Archive $Destination $Strip
}

function Find-Child([string]$Root, [string]$Leaf) {
    $found = Get-ChildItem -LiteralPath $Root -Recurse -File -Filter $Leaf |
        Select-Object -First 1
    if ($null -eq $found) {
        throw "archive does not contain $Leaf"
    }
    return $found.FullName
}

function Test-Version([string]$Tool, [string]$Path, [string]$Flag, [string]$Expected) {
    Assert-File $Path $Path
    $output = (& $Path $Flag 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "--version failed: $Tool ($output)"
    }
    $first = ($output -split "`r?`n")[0].Trim()
    switch ($Tool) {
        'ffmpeg' {
            if ($first -notlike "*$($Expected.TrimStart([char]'n'))*") {
                throw "$Tool version mismatch: expected $Expected, got '$first'"
            }
        }
        'ffprobe' {
            if ($first -notlike "*$($Expected.TrimStart([char]'n'))*") {
                throw "$Tool version mismatch: expected $Expected, got '$first'"
            }
        }
        'deno' {
            if ($first -notlike "deno $Expected *") {
                throw "$Tool version mismatch: expected $Expected, got '$first'"
            }
        }
        'rubberband' {
            if ($first -ne $Expected -and $first -ne "Rubber Band $Expected") {
                throw "$Tool version mismatch: expected $Expected, got '$first'"
            }
        }
        default {
            throw "unknown versioned tool: $Tool"
        }
    }
}

function Test-Tree([string]$Root, $Lock) {
    $versions = @{}
    foreach ($name in $Components) {
        $record = Get-Component $Lock $name
        $entry = Get-Target $record $TargetName
        $versions[$name] = [string]$record.version
        foreach ($item in @($entry.install)) {
            $relative = [string]$item.to
            $path = Join-Path $Root $relative
            Assert-File $path "vendor/$relative"
            if ($item.executable -and -not $relative.EndsWith('.exe')) {
                throw "not executable: vendor/$relative (Windows requires .exe)"
            }
            if ($null -ne $item.sha256 -and [string]$item.sha256) {
                Assert-Sha256 $path ([string]$item.sha256) "vendor/$relative"
            }
        }
    }
    Assert-File (Join-Path $Root 'rubberband\sndfile.dll') 'vendor/rubberband/sndfile.dll'
    Test-Version 'ffmpeg' (Join-Path $Root 'ffmpeg\ffmpeg.exe') '-version' $versions['ffmpeg']
    Test-Version 'ffprobe' (Join-Path $Root 'ffmpeg\ffprobe.exe') '-version' $versions['ffprobe']
    Test-Version 'deno' (Join-Path $Root 'deno\deno.exe') '--version' $versions['deno']
    Test-Version 'rubberband' (Join-Path $Root 'rubberband\rubberband.exe') '--version' $versions['rubberband']
}

function Test-TreeComplete([string]$Root, $Lock) {
    foreach ($name in $Components) {
        $record = Get-Component $Lock $name
        $entry = Get-Target $record $TargetName
        foreach ($item in @($entry.install)) {
            $path = Join-Path $Root ([string]$item.to)
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
                return $false
            }
        }
    }
    return (Test-Path -LiteralPath (Join-Path $Root 'rubberband\sndfile.dll') -PathType Leaf)
}

function Copy-Staged([string]$Source, [string]$Relative) {
    $destination = Join-Path $Stage $Relative
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
    Copy-Item -LiteralPath $Source -Destination $destination -Force
}

function Install-Stage([string]$Relative) {
    $source = Join-Path $Stage $Relative
    $destination = Join-Path $Vendor $Relative
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
    $temporary = "$destination.new"
    Copy-Item -LiteralPath $source -Destination $temporary -Force
    Move-Item -Force -LiteralPath $temporary -Destination $destination
}

function Invoke-Logged([string]$Tool, [string[]]$Arguments) {
    # Stream each native line. Out-Host buffers a native process's output until
    # it exits on the Windows runner; ForEach-Object/Write-Host keeps the log
    # live so a hang is visible in the canceled-step evidence.
    Write-Note "run  $Tool $($Arguments -join ' ')"
    & $Tool @Arguments 2>&1 | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) {
        throw "$Tool failed with exit code $LASTEXITCODE"
    }
}

function Invoke-LockBuildConfigure($Entry, [string]$SourceRoot, [string]$WorkingDirectory) {
    $argv = @($Entry.build.configure | ForEach-Object { $_ -replace '<source>', $SourceRoot })
    if ($argv.Count -lt 2) {
        throw "the lock has no usable build.configure line"
    }
    $tool = [string]$argv[0]
    $arguments = @($argv[1..($argv.Count - 1)])
    Push-Location -LiteralPath $WorkingDirectory
    try {
        Invoke-Logged $tool $arguments
    }
    finally {
        Pop-Location
    }
}

function Find-Tool([string]$Name, [string]$Hint) {
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "missing build tool '$Name'. $Hint"
    }
    return $command.Source
}

function Enter-MsvcEnvironment {
    if ($null -ne (Get-Command cl.exe -ErrorAction SilentlyContinue)) {
        return
    }
    $programFilesX86 = ${env:ProgramFiles(x86)}
    if (-not $programFilesX86) {
        $programFilesX86 = $env:ProgramFiles
    }
    $vswhere = Join-Path $programFilesX86 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (-not (Test-Path -LiteralPath $vswhere -PathType Leaf)) {
        throw "MSVC x64 tools are not on PATH and vswhere.exe is missing. Run this script from a Developer PowerShell for Visual Studio 2022, or pass -RubberBandPrebuilt."
    }
    $installation = (& $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath | Select-Object -First 1)
    if (-not $installation) {
        throw "Visual Studio with the MSVC x64 toolchain was not found. Install the Desktop development with C++ workload, or pass -RubberBandPrebuilt."
    }
    $devShell = Join-Path $installation 'Common7\Tools\Microsoft.VisualStudio.DevShell.dll'
    if (-not (Test-Path -LiteralPath $devShell -PathType Leaf)) {
        throw "Microsoft.VisualStudio.DevShell.dll was not found under $installation; run from a Developer PowerShell or pass -RubberBandPrebuilt."
    }
    Import-Module $devShell -Force
    Enter-VsDevShell -VsInstallPath $installation -SkipAutomaticLocation -DevCmdArguments '-arch=x64 -host_arch=x64' | Out-Null
    if ($null -eq (Get-Command cl.exe -ErrorAction SilentlyContinue)) {
        throw "MSVC x64 tools are still unavailable after Enter-VsDevShell; pass -RubberBandPrebuilt."
    }
}

function Build-Libsndfile($Lock, [int]$Parallel) {
    $record = Get-Component $Lock 'libsndfile'
    $entry = Get-Target $record $TargetName
    $format = [string]$entry.build.archive_format
    $archive = Get-Archive $Lock 'libsndfile'
    $sourceRoot = Join-Path $Work 'libsndfile-src'
    $workDir = Join-Path $Work 'libsndfile-work'
    New-Item -ItemType Directory -Force -Path $workDir | Out-Null

    Write-Note 'build   libsndfile 1.2.2 (CMake, shared, WAV-only)'
    $cmake = Find-Tool 'cmake' 'CMake is preinstalled on windows-2022; install it or pass -RubberBandPrebuilt.'
    Write-Note "cmake   $((& $cmake --version | Select-Object -First 1))"
    # Have MSVC on PATH before CMake probes for a toolchain. The VS generator
    # still drives MSBuild, but this removes the runner-image difference that
    # made the first windows-2022 configure look idle for the whole job.
    Enter-MsvcEnvironment
    switch ($format) {
        'tar.xz' { Expand-TarXz $archive $sourceRoot 1 }
        'tar.gz' { Expand-TarGz $archive $sourceRoot 1 }
        default { throw "unsupported libsndfile archive format: $format" }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot 'CMakeLists.txt') -PathType Leaf)) {
        throw "libsndfile source extraction did not produce CMakeLists.txt"
    }

    Invoke-LockBuildConfigure $entry $sourceRoot $workDir
    $buildDir = Join-Path $workDir 'build'
    Invoke-Logged $cmake @('--build', $buildDir, '--config', 'Release', '--parallel', "$Parallel")

    $dll = Find-Child $workDir 'sndfile.dll'
    $importLibrary = Find-Child $workDir 'sndfile.lib'
    $include = Join-Path $sourceRoot 'include'
    Assert-File (Join-Path $include 'sndfile.h') 'libsndfile sndfile.h'
    Write-Note "built   $dll"
    return [pscustomobject]@{
        Dll = $dll
        Lib = $importLibrary
        Include = $include
        BuildDir = $buildDir
    }
}

function Build-RubberBandFromSource($Lock, $Sndfile) {
    $record = Get-Component $Lock 'rubberband'
    $entry = Get-Target $record $TargetName
    $format = [string]$entry.build.archive_format
    $archive = Get-Archive $Lock 'rubberband'
    $sourceRoot = Join-Path $Work 'rubberband-src'
    $buildDir = Join-Path $Work 'rubberband-build'
    $pkgConfigDir = Join-Path $Work 'sndfile-pkgconfig'
    New-Item -ItemType Directory -Force -Path $pkgConfigDir | Out-Null

    switch ($format) {
        'tar.gz' { Expand-TarGz $archive $sourceRoot 1 }
        'tar.xz' { Expand-TarXz $archive $sourceRoot 1 }
        default { throw "unsupported Rubber Band archive format: $format" }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot 'meson.build') -PathType Leaf)) {
        throw "Rubber Band source extraction did not produce meson.build"
    }

    Enter-MsvcEnvironment
    $meson = Find-Tool 'meson' "Install it with 'uv pip install --python .venv\Scripts\python.exe meson ninja'."
    $ninja = Find-Tool 'ninja' "Install it with 'uv pip install --python .venv\Scripts\python.exe meson ninja'."
    $pkgConfig = Get-Command 'pkg-config' -ErrorAction SilentlyContinue
    if ($null -eq $pkgConfig) {
        $pkgConfig = Get-Command 'pkgconf' -ErrorAction SilentlyContinue
    }
    if ($null -eq $pkgConfig) {
        throw "missing pkg-config. Install pkgconfiglite (choco install pkgconfiglite -y) or pass -RubberBandPrebuilt."
    }

    # Meson's dependency('sndfile') is required when the command line is
    # enabled. A tiny .pc file points at the CMake import library by absolute
    # path, which works with MSVC; the generic '-lsndfile' form does not.
    $libPath = ([string]$Sndfile.Lib).Replace('\', '/')
    $includePath = ([string]$Sndfile.Include).Replace('\', '/')
    $libDir = (Split-Path -Parent ([string]$Sndfile.Lib)).Replace('\', '/')
    $pcLines = @(
        "prefix=$(([string]$Sndfile.BuildDir).Replace('\', '/'))",
        'exec_prefix=${prefix}',
        'libdir=${prefix}',
        "includedir=$includePath",
        'Name: sndfile',
        'Description: libsndfile 1.2.2 built for StemForge',
        'Version: 1.2.2',
        'Requires:',
        'Libs: ' + $libPath,
        'Cflags: -I' + $includePath
    )
    $pcPath = Join-Path $pkgConfigDir 'sndfile.pc'
    Set-Content -LiteralPath $pcPath -Value $pcLines -Encoding ASCII

    $oldPkgConfigPath = $env:PKG_CONFIG_PATH
    $oldPkgConfig = $env:PKG_CONFIG
    try {
        if ($oldPkgConfigPath) {
            $env:PKG_CONFIG_PATH = "$pkgConfigDir;$oldPkgConfigPath"
        }
        else {
            $env:PKG_CONFIG_PATH = $pkgConfigDir
        }
        $env:PKG_CONFIG = $pkgConfig.Source
        $libs = (& $pkgConfig.Source '--libs' 'sndfile' 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or $libs -notlike "*$libPath*") {
            throw "pkg-config did not resolve the absolute sndfile import library (got: $libs)"
        }

        Write-Note 'build   Rubber Band 4.0.0 (Meson/MSVC, static library, CLI)'
        $mesonArguments = @(
            'setup', $buildDir, $sourceRoot,
            '--buildtype=release',
            '--default-library=static',
            '-Dfft=builtin',
            '-Dresampler=builtin',
            '-Dvamp=disabled',
            '-Dlv2=disabled',
            '-Dladspa=disabled',
            '-Djni=disabled',
            '-Dcmdline=enabled',
            '-Dtests=disabled',
            '--vsenv',
            "-Dextra_lib_dirs=$libDir",
            "-Dextra_include_dirs=$includePath"
        )
        Invoke-Logged $meson $mesonArguments
        Invoke-Logged $ninja @('-C', $buildDir)
    }
    finally {
        $env:PKG_CONFIG_PATH = $oldPkgConfigPath
        $env:PKG_CONFIG = $oldPkgConfig
    }

    $executable = Get-ChildItem -LiteralPath $buildDir -Recurse -File |
        Where-Object { $_.Name -in @('rubberband.exe', 'rubberband-program.exe') } |
        Select-Object -First 1
    if ($null -eq $executable) {
        throw "the Rubber Band build produced no rubberband.exe or rubberband-program.exe"
    }
    Copy-Staged $executable.FullName 'rubberband/rubberband.exe'
    Copy-Staged $Sndfile.Dll 'rubberband/sndfile.dll'
    Test-Version 'rubberband' (Join-Path $Stage 'rubberband\rubberband.exe') '--version' $record.version
    Write-Note "built   $($executable.FullName)"
}

function Import-RubberBandPrebuilt([string]$InputPath, [string]$ExpectedSha) {
    if (-not $InputPath) {
        throw @"
Rubber Band 4.0.0 and libsndfile 1.2.2 must be MSVC-built on windows-2022
(ticket 10 section 8). The decision record names no public prebuilt archive, so this
script verifies a pinned build instead of inventing a URL. Pass either a
directory or a zip containing:
  rubberband.exe   (must report 4.0.0)
  sndfile.dll      (co-located; Windows loads it from the exe directory)

  powershell -File scripts/vendor-fetch.ps1 -RubberBandPrebuilt <path> [-RubberBandPrebuiltSha256 <sha256>]

The exact Meson/CMake lines are recorded in packaging/vendor.lock.json.
"@
    }

    $resolved = (Resolve-Path -LiteralPath $InputPath).Path
    $extracted = Join-Path $Work 'rubberband-prebuilt'
    if (Test-Path -LiteralPath $resolved -PathType Container) {
        $sourceRoot = $resolved
    }
    elseif ($resolved.ToLowerInvariant().EndsWith('.zip')) {
        if (-not $ExpectedSha) {
            throw "-RubberBandPrebuiltSha256 is required for a prebuilt zip"
        }
        Assert-Sha256 $resolved $ExpectedSha (Split-Path -Leaf $resolved)
        Expand-ZipArchive $resolved $extracted
        $sourceRoot = $extracted
    }
    else {
        throw "RubberBandPrebuilt must be a directory or a .zip"
    }

    $executable = Find-Child $sourceRoot 'rubberband.exe'
    $library = Find-Child $sourceRoot 'sndfile.dll'
    Test-Version 'rubberband' $executable '--version' '4.0.0'
    Copy-Staged $executable 'rubberband/rubberband.exe'
    Copy-Staged $library 'rubberband/sndfile.dll'
}

function Show-Summary($Lock) {
    Write-Host ''
    Write-Host 'Vendor set (ticket 19) - provenance and licences'
    Write-Host '================================================'
    foreach ($name in $Components) {
        $record = Get-Component $Lock $name
        $entry = Get-Target $record $TargetName
        $source = Get-Source $entry
        Write-Host ''
        Write-Host ("{0} {1} - {2}" -f $record.provenance.project, $record.version, $record.license)
        Write-Host ("  source: {0}" -f $source.filename)
        Write-Host ("  url:    {0}" -f $source.url)
        Write-Host ("  sha256: {0}" -f $source.sha256)
        foreach ($item in @($entry.install)) {
            $relative = [string]$item.to
            $path = Join-Path $Vendor $relative
            Write-Host ("  vendor/{0}  {1} bytes  sha256={2}" -f $relative, (Get-Item -LiteralPath $path).Length, (Get-Sha256 $path))
        }
    }
    Write-Host ''
    Write-Host 'Source offer: GPL-3.0-only section 6(b), valid for three years.'
    Write-Host '  contact:  https://github.com/alexmarincu/stemforge/issues'
    Write-Host '  releases: https://github.com/alexmarincu/stemforge/releases'
    Write-Host '  inputs:   packaging/vendor/SOURCES.md (ticket 22)'
    Write-Host 'No model weights are present in vendor/.'
}

function Write-Provenance($Lock) {
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add('StemForge vendor set - generated by scripts/vendor-fetch.ps1')
    $lines.Add("date: $([DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ'))")
    $lines.Add("target: $TargetName")
    $lines.Add('lock: packaging/vendor.lock.json')
    $lines.Add('')
    foreach ($name in $Components) {
        $record = Get-Component $Lock $name
        $entry = Get-Target $record $TargetName
        $source = Get-Source $entry
        $lines.Add("$($record.provenance.project) $($record.version) ($($record.license))")
        $lines.Add("  obtained: $($source.filename) $($source.url)")
        $lines.Add("  archive sha256: $($source.sha256)")
        $buildProperty = $entry.PSObject.Properties['build']
        if ($null -ne $buildProperty) {
            $build = $buildProperty.Value
            $lines.Add("  build system: $($build.system)")
            $lines.Add("  configure: $($build.configure -join ' ')")
        }
        foreach ($item in @($entry.install)) {
            $relative = [string]$item.to
            $lines.Add("  installed: vendor/$relative sha256=$(Get-Sha256 (Join-Path $Vendor $relative))")
        }
        $lines.Add('')
    }
    $lines.Add('Source offer: GPL-3.0-only section 6(b), three years,')
    $lines.Add('https://github.com/alexmarincu/stemforge/issues')
    Set-Content -LiteralPath (Join-Path $Vendor 'PROVENANCE.txt') -Value $lines -Encoding UTF8
}

try {
    $Lock = Get-Lock
    if ($Lock.schema -ne 1) {
        throw "unsupported vendor.lock.json schema: $($Lock.schema)"
    }
    if ($null -eq $Lock.targets.PSObject.Properties[$TargetName]) {
        throw "vendor.lock.json has no $TargetName target"
    }

    if (-not $VerifyOnly -and -not $Force -and (Test-TreeComplete $Vendor $Lock)) {
        Write-Note 'vendor tree is complete; verifying it (use -Force to rebuild)'
        Test-Tree $Vendor $Lock
        Show-Summary $Lock
        exit 0
    }

    if ($VerifyOnly) {
        if (-not (Test-Path -LiteralPath $Vendor -PathType Container)) {
            throw 'vendor/ is missing; run scripts/vendor-fetch.ps1 first'
        }
        Test-Tree $Vendor $Lock
        Show-Summary $Lock
        exit 0
    }

    $Work = Join-Path ([System.IO.Path]::GetTempPath()) ("stemforge-vendor-" + [guid]::NewGuid().ToString('N'))
    $Stage = Join-Path $Work 'stage'
    New-Item -ItemType Directory -Force -Path $Stage | Out-Null

    try {
        $ffmpegArchive = Get-Archive $Lock 'ffmpeg'
        $denoArchive = Get-Archive $Lock 'deno'

        $ffmpegRoot = Join-Path $Work 'ffmpeg'
        $denoRoot = Join-Path $Work 'deno'
        Expand-ZipArchive $ffmpegArchive $ffmpegRoot
        Expand-ZipArchive $denoArchive $denoRoot

        Copy-Staged (Find-Child $ffmpegRoot 'ffmpeg.exe') 'ffmpeg/ffmpeg.exe'
        Copy-Staged (Find-Child $ffmpegRoot 'ffprobe.exe') 'ffmpeg/ffprobe.exe'
        Copy-Staged (Find-Child $denoRoot 'deno.exe') 'deno/deno.exe'

        # Verify the downloaded files against the extracted-file hashes before
        # they can reach vendor/.
        $ffmpegEntry = Get-Target (Get-Component $Lock 'ffmpeg') $TargetName
        $denoEntry = Get-Target (Get-Component $Lock 'deno') $TargetName
        foreach ($item in @($ffmpegEntry.install)) {
            $relative = [string]$item.to
            if ($null -ne $item.sha256 -and [string]$item.sha256) {
                Assert-Sha256 (Join-Path $Stage $relative) ([string]$item.sha256) "vendor/$relative"
            }
        }
        foreach ($item in @($denoEntry.install)) {
            $relative = [string]$item.to
            if ($null -ne $item.sha256 -and [string]$item.sha256) {
                Assert-Sha256 (Join-Path $Stage $relative) ([string]$item.sha256) "vendor/$relative"
            }
        }

        if ($RubberBandPrebuilt) {
            Import-RubberBandPrebuilt $RubberBandPrebuilt $RubberBandPrebuiltSha256
        }
        else {
            $sndfile = Build-Libsndfile $Lock $Jobs
            Build-RubberBandFromSource $Lock $sndfile
        }

        Test-Tree $Stage $Lock
        foreach ($name in $Components) {
            $entry = Get-Target (Get-Component $Lock $name) $TargetName
            foreach ($item in @($entry.install)) {
                Install-Stage ([string]$item.to)
            }
        }
        Test-Tree $Vendor $Lock
        Write-Provenance $Lock
        Show-Summary $Lock
        Write-Note "wrote $(Join-Path $Vendor 'PROVENANCE.txt')"
    }
    finally {
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue -LiteralPath $Work
    }
}
catch {
    Write-Error $_
    exit 1
}
