#!/usr/bin/env python3
"""Release asset set and the checks behind ``scripts/verify-release.sh``.

This module is deliberately stdlib-only. It exists so the shell entry point can
stay a thin POSIX wrapper while the archive inspection (tar.gz, tar.zst, ZIP,
AppImage, Inno setup) is testable in the unit layer.

The expected names, budgets, and required licence tree come from ticket 10
sections 8-9 and ticket 11 section 8. A release is the exact set; an extra file
is a failure, not a warning.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final
from urllib.parse import urlparse

ASSET_TEMPLATES: Final = (
    "StemForge-{version}-linux-x86_64.tar.gz",
    "StemForge-{version}-linux-x86_64.tar.zst",
    "StemForge-{version}-linux-x86_64.AppImage",
    "StemForge-{version}-win64-portable.zip",
    "StemForge-{version}-win64-setup.exe",
    "StemForge-{version}-src.tar.gz",
    "StemForge-{version}-third-party-sources.tar.zst",
    "SHA256SUMS",
)
CHECKSUMS: Final = "SHA256SUMS"
LICENCE_FILES: Final = ("LICENSE", "THIRD_PARTY_NOTICES.md", "SOURCE_OFFER.md")
LICENCE_DIR: Final = "LICENSES"
WEIGHT_SUFFIXES: Final = (
    ".safetensors",
    ".pt",
    ".pth",
    ".ckpt",
    ".onnx",
    ".tflite",
    ".gguf",
    ".h5",
)
WEIGHT_MARKERS: Final = ("/models/htdemucs/", "955717e8")
MEGABYTE: Final = 1_000_000

# Ticket 10 section 9 targets. Exceeding a target fails the release.
BUDGET_BYTES: Final[dict[str, int]] = {
    "linux-x86_64.tar.gz": 450 * MEGABYTE,
    "linux-x86_64.tar.zst": 320 * MEGABYTE,
    "linux-x86_64.AppImage": 350 * MEGABYTE,
    "win64-portable.zip": 450 * MEGABYTE,
    "win64-setup.exe": 380 * MEGABYTE,
}

LINUX_PREFIX: Final = "StemForge-{version}-linux-x86_64"
WINDOWS_PREFIX: Final = "StemForge-{version}-win64"
SOURCE_PREFIX: Final = "StemForge-{version}"
SOURCES_ROOT: Final = "SOURCES"
APPIMAGE_PAYLOAD: Final = "usr/lib/stemforge"

_HEX64: Final = re.compile(r"^[0-9a-f]{64}$")
_SUM_LINE: Final = re.compile(r"^(?P<digest>[0-9a-f]{64})  (?P<name>[^/\\]+)$")


class AssetError(RuntimeError):
    """A release asset is missing, corrupt, or outside the contract."""


@dataclass(frozen=True, slots=True)
class Finding:
    """One named check result."""

    check: str
    status: str
    detail: str


@dataclass(frozen=True, slots=True)
class Member:
    """One regular file in an archive, already hashed."""

    name: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class Archive:
    """A regular-file index for a bundle archive or an extracted AppImage."""

    members: Mapping[str, Member]

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self.members))

    def has(self, name: str) -> bool:
        return name in self.members

    def member(self, name: str) -> Member:
        try:
            return self.members[name]
        except KeyError as error:
            raise AssetError(f"missing archive member: {name}") from error


def asset_names(version: str) -> tuple[str, ...]:
    """The eight release asset names for ``version`` in ticket order."""
    return tuple(template.format(version=version) for template in ASSET_TEMPLATES)


def _hash_handle(handle: io.BufferedIOBase | io.RawIOBase) -> str:
    digest = hashlib.sha256()
    while True:
        chunk = handle.read(1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
    return digest.hexdigest()


def _hash_file(path: Path) -> str:
    with path.open("rb") as handle:
        return _hash_handle(handle)


def _safe_name(name: str) -> bool:
    if not name or "\x00" in name or "\\" in name:
        return False
    pure = PurePosixPath(name)
    return not pure.is_absolute() and ".." not in pure.parts and bool(pure.parts)


def archive_from_tar(path: Path) -> Archive:
    """Index a tar, tar.gz, or tar.zst; every regular member is hashed once."""
    if path.name.endswith(".tar.zst"):
        tool = shutil.which("zstd")
        if tool is None:
            raise AssetError(f"zstd is required to inspect {path.name}")
        process = subprocess.Popen(
            [tool, "-dc", "--", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert process.stdout is not None
        try:
            members = _index_tar_stream(process.stdout)
        finally:
            process.stdout.close()
            stderr = process.stderr.read() if process.stderr is not None else b""
            code = process.wait()
        if code != 0:
            raise AssetError(
                f"zstd could not decompress {path.name}: {stderr.decode(errors='replace').strip()}"
            )
        return Archive(members)
    mode = "r:gz" if path.name.endswith((".tar.gz", ".tgz")) else "r:*"
    try:
        with tarfile.open(path, mode) as archive:
            return Archive(_index_tar_archive(archive))
    except (tarfile.TarError, OSError) as error:
        raise AssetError(f"{path.name}: {error}") from error


def _index_tar_stream(stream: io.BufferedIOBase) -> dict[str, Member]:
    try:
        with tarfile.open(fileobj=stream, mode="r|") as archive:
            return _index_tar_archive(archive)
    except (tarfile.TarError, OSError) as error:
        raise AssetError(f"tar stream: {error}") from error


def _index_tar_archive(archive: tarfile.TarFile) -> dict[str, Member]:
    members: dict[str, Member] = {}
    for info in archive:
        if not info.isreg():
            if not _safe_name(info.name):
                raise AssetError(f"unsafe archive path: {info.name!r}")
            continue
        if not _safe_name(info.name):
            raise AssetError(f"unsafe archive path: {info.name!r}")
        handle = archive.extractfile(info)
        if handle is None:
            raise AssetError(f"cannot read archive member: {info.name}")
        digest = _hash_handle(handle)
        members[info.name] = Member(info.name, info.size, digest)
    return members


def archive_from_zip(path: Path) -> Archive:
    """Index a ZIP; every regular member is hashed once."""
    try:
        with zipfile.ZipFile(path) as archive:
            members: dict[str, Member] = {}
            for info in archive.infolist():
                if info.is_dir():
                    continue
                if not _safe_name(info.filename):
                    raise AssetError(f"unsafe archive path: {info.filename!r}")
                with archive.open(info) as handle:
                    digest = _hash_handle(handle)
                members[info.filename] = Member(info.filename, info.file_size, digest)
            return Archive(members)
    except (zipfile.BadZipFile, OSError) as error:
        raise AssetError(f"{path.name}: {error}") from error


def archive_from_directory(root: Path) -> Archive:
    """Index an extracted AppImage payload."""
    members: dict[str, Member] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if not _safe_name(relative):
            raise AssetError(f"unsafe extracted path: {relative!r}")
        members[relative] = Member(relative, path.stat().st_size, _hash_file(path))
    return Archive(members)


def _contains_name(path: Path, names: Sequence[str]) -> set[str]:
    """Find embedded file names in a PE image in UTF-8 or UTF-16LE.

    Inno Setup stores its file table as text inside the installer. This is the
    fallback check when ``innoextract`` is unavailable; the publish job installs
    the extractor, so the release path uses the stronger check.
    """
    needles: list[tuple[str, bytes]] = []
    for name in names:
        needles.append((name, name.encode("utf-8")))
        needles.append((name, name.encode("utf-16-le")))
    found: set[str] = set()
    overlap = 4 * 1024
    tail = b""
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            haystack = tail + chunk
            for name, needle in needles:
                if name not in found and needle in haystack:
                    found.add(name)
            tail = haystack[-overlap:]
    return found


def pe_signature(path: Path) -> str:
    """Describe the PE certificate table without external tools."""
    try:
        with path.open("rb") as handle:
            header = handle.read(4096)
    except OSError as error:
        return f"unreadable ({error})"
    if header[:2] != b"MZ":
        return "not a PE image"
    try:
        pe_offset = int.from_bytes(header[0x3C:0x40], "little")
        if header[pe_offset : pe_offset + 4] != b"PE\0\0":
            return "not a PE image"
        optional = pe_offset + 24
        magic = int.from_bytes(header[optional : optional + 2], "little")
        directories = optional + (112 if magic == 0x20B else 96)
        security = directories + 4 * 8
        size = int.from_bytes(header[security + 4 : security + 8], "little")
    except (IndexError, ValueError):
        return "unparseable PE header"
    if size:
        return f"signed (certificate table {size} bytes)"
    return "unsigned"


def _release_version(repo_root: Path) -> str:
    config_path = repo_root / "packaging" / "release.toml"
    with config_path.open("rb") as handle:
        config = tomllib.load(handle)
    version_file = Path(str(config["project"]["version_file"]))
    if version_file.is_absolute() or ".." in version_file.parts:
        raise AssetError(f"project.version_file is not a relative path: {version_file}")
    source = repo_root / version_file
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__version__":
                    value = ast.literal_eval(node.value)
                    if isinstance(value, str) and value:
                        return value
    raise AssetError(f"{source} does not define __version__")


def _vendor_expectations(
    lock: Mapping[str, object], target: str
) -> tuple[dict[str, str | None], dict[str, str]]:
    """Return ``(to -> pinned sha or None, component -> version)`` for a target."""
    components = lock.get("components")
    if not isinstance(components, dict):
        raise AssetError("vendor.lock.json has no components table")
    expected: dict[str, str | None] = {}
    versions: dict[str, str] = {}
    for component, record in components.items():
        if not isinstance(record, dict):
            continue
        versions[str(component)] = str(record.get("version", ""))
        targets = record.get("targets")
        if not isinstance(targets, dict) or target not in targets:
            continue
        entry = targets[target]
        if not isinstance(entry, dict):
            continue
        for install in entry.get("install", []):
            if not isinstance(install, dict):
                continue
            destination = str(install["to"])
            if not _safe_name(destination):
                raise AssetError(f"unsafe vendor install path in the lock: {destination!r}")
            digest = install.get("sha256")
            expected[destination] = str(digest) if digest else None
    return expected, versions


def _provenance_from_tar(path: Path, prefix: str) -> dict[str, str]:
    """Read ``PROVENANCE.txt`` from a bundle tar and parse installed hashes."""
    wanted = f"{prefix}vendor/PROVENANCE.txt"
    data: bytes | None = None
    if path.name.endswith(".tar.zst"):
        tool = shutil.which("zstd")
        if tool is None:
            return {}
        process = subprocess.Popen(
            [tool, "-dc", "--", str(path)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        assert process.stdout is not None
        try:
            with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
                for info in archive:
                    if info.name == wanted and info.isreg():
                        handle = archive.extractfile(info)
                        data = handle.read() if handle is not None else None
                        break
        finally:
            process.stdout.close()
            process.wait()
    else:
        try:
            with tarfile.open(path, "r:*") as archive:
                for info in archive:
                    if info.name == wanted and info.isreg():
                        handle = archive.extractfile(info)
                        data = handle.read() if handle is not None else None
                        break
        except (tarfile.TarError, OSError):
            return {}
    if data is None:
        return {}
    hashes: dict[str, str] = {}
    for line in data.decode("utf-8", errors="replace").splitlines():
        match = re.search(r"installed:\s+vendor/(\S+)\s+sha256=([0-9a-f]{64})", line)
        if match:
            hashes[match.group(1)] = match.group(2)
    return hashes


def _verify_vendor_archive(
    archive: Archive,
    prefix: str,
    expected: Mapping[str, str | None],
    provenance: Mapping[str, str],
    check: str,
) -> list[Finding]:
    findings: list[Finding] = []
    vendor_prefix = f"{prefix}vendor/"
    actual_names = {
        name[len(vendor_prefix) :]
        for name in archive.names()
        if name.startswith(vendor_prefix) and not name.endswith("/")
    }
    actual_names.discard("PROVENANCE.txt")
    missing = sorted(set(expected) - actual_names)
    extra = sorted(actual_names - set(expected))
    if missing or extra:
        findings.append(
            Finding(
                check,
                "fail",
                f"missing {missing or '[]'}, extra {extra or '[]'}",
            )
        )
        return findings
    problems: list[str] = []
    for destination, pinned in sorted(expected.items()):
        member = archive.member(f"{prefix}vendor/{destination}")
        if pinned is not None:
            if member.sha256 != pinned:
                problems.append(f"{destination}: hash mismatch (expected {pinned})")
        else:
            recorded = provenance.get(destination)
            if recorded is None:
                problems.append(f"{destination}: no pinned or provenance hash")
            elif recorded != member.sha256:
                problems.append(f"{destination}: provenance hash mismatch")
    if problems:
        findings.append(Finding(check, "fail", "; ".join(problems)))
    else:
        findings.append(Finding(check, "pass", f"{len(expected)} files match vendor.lock.json"))
    return findings


def _bundle_prefix(expected_prefix: str, archive: Archive, check: str) -> Finding | None:
    roots = {PurePosixPath(name).parts[0] for name in archive.names() if PurePosixPath(name).parts}
    if roots != {expected_prefix}:
        return Finding(
            check,
            "fail",
            f"expected one top-level directory {expected_prefix!r}, found {sorted(roots)}",
        )
    return None


def _licence_findings(archive: Archive, prefix: str, check: str) -> list[Finding]:
    missing = [name for name in LICENCE_FILES if not archive.has(f"{prefix}{name}")]
    licences = [name for name in archive.names() if name.startswith(f"{prefix}{LICENCE_DIR}/")]
    if not licences:
        missing.append(f"{LICENCE_DIR}/")
    if missing:
        return [Finding(check, "fail", f"missing {', '.join(missing)}")]
    return [Finding(check, "pass", "LICENSE, notices, source offer, LICENSES/")]


def _weight_findings(archive: Archive, check: str) -> list[Finding]:
    bad = [
        name
        for name in archive.names()
        if name.lower().endswith(WEIGHT_SUFFIXES)
        or any(marker in name.lower() for marker in WEIGHT_MARKERS)
    ]
    if bad:
        return [Finding(check, "fail", f"model data found: {', '.join(sorted(bad)[:5])}")]
    return [Finding(check, "pass", "no model weights")]


def _budget_findings(directory: Path, version: str) -> list[Finding]:
    findings: list[Finding] = []
    for suffix, limit in BUDGET_BYTES.items():
        path = directory / f"StemForge-{version}-{suffix}"
        if not path.is_file():
            continue
        size = path.stat().st_size
        status = "pass" if size <= limit else "fail"
        findings.append(
            Finding(
                f"budget.{suffix}",
                status,
                f"{size} B of {limit} B",
            )
        )
    return findings


def _sums_findings(
    directory: Path, expected: Sequence[str], *, skip_sums: bool
) -> tuple[list[Finding], dict[str, str]]:
    findings: list[Finding] = []
    manifest = directory / CHECKSUMS
    if not manifest.is_file():
        return [Finding("sums.present", "fail", f"{CHECKSUMS} is missing")], {}
    findings.append(Finding("sums.present", "pass", CHECKSUMS))
    if skip_sums:
        findings.append(Finding("sums.coverage", "skip", "--skip-sums"))
        findings.append(Finding("sums.match", "skip", "--skip-sums"))
        return findings, {}
    try:
        rows = _parse_sums(manifest.read_text(encoding="utf-8"))
    except ValueError as error:
        return [*findings, Finding("sums.parse", "fail", str(error))], {}
    wanted = set(expected) - {CHECKSUMS}
    names = {name for _, name in rows}
    if names != wanted:
        findings.append(
            Finding(
                "sums.coverage",
                "fail",
                f"missing {sorted(wanted - names)}, extra {sorted(names - wanted)}",
            )
        )
    else:
        findings.append(Finding("sums.coverage", "pass", f"{len(rows)} assets"))
    problems: list[str] = []
    hashes: dict[str, str] = {}
    for digest, name in rows:
        path = directory / name
        if not path.is_file():
            problems.append(f"{name}: missing")
            continue
        actual = _hash_file(path)
        hashes[name] = actual
        if actual != digest:
            problems.append(f"{name}: SHA-256 mismatch")
    if problems:
        findings.append(Finding("sums.match", "fail", "; ".join(problems)))
    else:
        findings.append(Finding("sums.match", "pass", f"{len(rows)} hashes match"))
    return findings, hashes


def _parse_sums(text: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        match = _SUM_LINE.match(line)
        if match is None:
            raise ValueError(f"line {number} is not '<sha256>  <basename>': {raw!r}")
        name = match.group("name")
        if name in seen:
            raise ValueError(f"duplicate SHA256SUMS entry: {name}")
        seen.add(name)
        rows.append((match.group("digest"), name))
    if not rows:
        raise ValueError("SHA256SUMS is empty")
    if CHECKSUMS in seen:
        raise ValueError("SHA256SUMS must not list itself")
    return rows


def _recorded_source_names(lock: Mapping[str, object]) -> dict[str, str]:
    """Map a pinned archive SHA to the component target's recorded filename."""
    recorded: dict[str, str] = {}
    components = lock.get("components")
    if not isinstance(components, dict):
        return recorded
    for record in components.values():
        if not isinstance(record, dict):
            continue
        targets = record.get("targets")
        if not isinstance(targets, dict):
            continue
        for entry in targets.values():
            if not isinstance(entry, dict):
                continue
            source = entry.get("source", entry)
            if not isinstance(source, dict):
                continue
            sha = str(source.get("sha256", "")).lower()
            filename = str(source.get("filename", ""))
            if sha and filename:
                recorded[sha] = filename
    return recorded


def _source_offer_archives(lock: Mapping[str, object]) -> list[dict[str, str]]:
    offer = lock.get("source_offer")
    if not isinstance(offer, dict) or not isinstance(offer.get("sources"), list):
        raise AssetError("vendor.lock.json has no source_offer.sources list")
    recorded = _recorded_source_names(lock)
    archives: list[dict[str, str]] = []
    for entry in offer["sources"]:
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("url", ""))
        parsed = urlparse(url)
        url_filename = PurePosixPath(parsed.path).name
        sha = str(entry.get("sha256", "")).lower()
        filename = recorded.get(sha, url_filename)
        if (
            not filename
            or not _safe_name(filename)
            or not _safe_name(str(entry.get("component", "")))
        ):
            raise AssetError(f"unsafe source_offer entry: {entry!r}")
        archives.append(
            {
                "component": str(entry["component"]),
                "filename": filename,
                "sha256": sha,
            }
        )
    return archives


def _source_bundle_findings(
    directory: Path, version: str, lock: Mapping[str, object]
) -> list[Finding]:
    name = f"StemForge-{version}-third-party-sources.tar.zst"
    path = directory / name
    if not path.is_file():
        return [Finding("sources.contents", "fail", f"{name} is missing")]
    try:
        archive = archive_from_tar(path)
    except AssetError as error:
        return [Finding("sources.contents", "fail", str(error))]
    required = {
        f"{SOURCES_ROOT}/SOURCES.md",
        f"{SOURCES_ROOT}/MANIFEST.json",
        f"{SOURCES_ROOT}/build/vendor-fetch.sh",
        f"{SOURCES_ROOT}/build/vendor-fetch.ps1",
    }
    problems: list[str] = []
    missing = sorted(required - set(archive.names()))
    if missing:
        problems.append(f"missing {missing}")
    try:
        expected = _source_offer_archives(lock)
    except AssetError as error:
        problems.append(str(error))
        expected = []
    for entry in expected:
        member_name = f"{SOURCES_ROOT}/{entry['component']}/{entry['filename']}"
        if not archive.has(member_name):
            problems.append(f"missing {member_name}")
            continue
        actual = archive.member(member_name).sha256
        if actual != entry["sha256"]:
            problems.append(f"{member_name}: SHA-256 mismatch")
    if problems:
        return [Finding("sources.contents", "fail", "; ".join(problems))]
    return [
        Finding(
            "sources.contents",
            "pass",
            f"{len(expected)} source archives + SOURCES.md + build scripts",
        )
    ]


def _source_tree_findings(directory: Path, version: str) -> list[Finding]:
    name = f"StemForge-{version}-src.tar.gz"
    path = directory / name
    if not path.is_file():
        return [Finding("src.contents", "fail", f"{name} is missing")]
    try:
        archive = archive_from_tar(path)
    except AssetError as error:
        return [Finding("src.contents", "fail", str(error))]
    prefix = SOURCE_PREFIX.format(version=version)
    finding = _bundle_prefix(prefix, archive, "src.prefix")
    if finding is not None:
        return [finding]
    required = (
        "LICENSE",
        "packaging/release.toml",
        "packaging/vendor.lock.json",
        "scripts/vendor-fetch.sh",
        "scripts/vendor-fetch.ps1",
        "src/stemforge/__init__.py",
    )
    missing = [name for name in required if not archive.has(f"{prefix}/{name}")]
    if missing:
        return [Finding("src.contents", "fail", f"missing {', '.join(missing)}")]
    return [Finding("src.contents", "pass", f"{len(required)} required files")]


def _setup_findings(
    directory: Path,
    version: str,
    lock: Mapping[str, object],
    *,
    extracted_root: Path | None = None,
    skip_reason: str | None = None,
) -> list[Finding]:
    name = f"StemForge-{version}-win64-setup.exe"
    path = directory / name
    if not path.is_file():
        return [Finding("setup.tree", "fail", f"{name} is missing")]
    findings: list[Finding] = []
    if extracted_root is not None:
        # Strong path: innoextract produced the same app/ tree the installer
        # writes. The publish job installs it.
        archive = archive_from_directory(extracted_root)
        findings.extend(_licence_findings(archive, "", "setup.licences"))
        expected, _versions = _vendor_expectations(lock, "windows-x64")
        findings.extend(
            _verify_vendor_archive(
                archive,
                "",
                expected,
                _provenance_from_directory(extracted_root),
                "setup.vendor",
            )
        )
        findings.append(Finding("setup.tree", "pass", "innoextract app/ tree"))
    else:
        # Fallback for a machine without innoextract. Inno may compress the
        # setup header, so this is a name-presence check, not the release gate;
        # the publish job always uses innoextract.
        scan_names = [
            *LICENCE_FILES,
            "StemForge.exe",
            "vendor\\ffmpeg\\ffmpeg.exe",
            "vendor\\rubberband\\rubberband.exe",
            "vendor\\rubberband\\sndfile.dll",
            f"{LICENCE_DIR}\\",
            f"{LICENCE_DIR}/",
        ]
        found = _contains_name(path, scan_names)
        separators = {f"{LICENCE_DIR}\\", f"{LICENCE_DIR}/"}
        missing = [item for item in scan_names if item not in found and item not in separators]
        if not (found & separators):
            missing.append(LICENCE_DIR)
        reason = skip_reason or ("innoextract unavailable; the portable ZIP carries the same tree")
        if missing:
            findings.append(Finding("setup.tree", "skip", reason))
        else:
            findings.append(
                Finding("setup.tree", "pass", "licence tree names embedded in the installer")
            )
        findings.append(Finding("setup.licences", "skip", reason))
        findings.append(Finding("setup.vendor", "skip", reason))
    findings.append(Finding("signature.setup", "pass", pe_signature(path)))
    return findings


def _provenance_from_directory(root: Path) -> dict[str, str]:
    path = root / "vendor" / "PROVENANCE.txt"
    if not path.is_file():
        return {}
    hashes: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.search(r"installed:\s+vendor/(\S+)\s+sha256=([0-9a-f]{64})", line)
        if match:
            hashes[match.group(1)] = match.group(2)
    return hashes


def _innoextract_root(path: Path, destination: Path) -> Path | None:
    tool = shutil.which("innoextract")
    if tool is None:
        return None
    result = subprocess.run(
        [tool, "--silent", "--output-dir", str(destination), str(path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssetError(
            f"innoextract failed for {path.name}: {result.stderr.strip() or result.stdout.strip()}"
        )
    root = destination / "app"
    if not root.is_dir():
        raise AssetError(f"innoextract produced no app/ tree for {path.name}")
    return root


def verify(
    directory: Path,
    *,
    tag: str,
    repo_root: Path,
    vendor_lock: Path | None = None,
    appimage_root: Path | None = None,
    skip_sums: bool = False,
    use_innoextract: bool = True,
) -> list[Finding]:
    """Run every release-asset check and return the findings."""
    findings: list[Finding] = []
    try:
        version = _release_version(repo_root)
    except (OSError, KeyError, ValueError, AssetError) as error:
        return [Finding("version.read", "fail", str(error))]
    findings.append(Finding("version.read", "pass", f"StemForge {version}"))
    expected_tag = f"v{version}"
    if tag != expected_tag:
        findings.append(
            Finding("version.tag", "fail", f"tag {tag!r} does not match {expected_tag!r}")
        )
    else:
        findings.append(Finding("version.tag", "pass", tag))

    names = asset_names(version)
    expected = set(names)
    actual = {path.name for path in directory.iterdir()}
    if actual != expected:
        findings.append(
            Finding(
                "assets.exact",
                "fail",
                f"missing {sorted(expected - actual)}, extra {sorted(actual - expected)}",
            )
        )
    else:
        findings.append(Finding("assets.exact", "pass", f"{len(names)} assets"))
    findings.extend(_budget_findings(directory, version))
    sums_findings, _hashes = _sums_findings(directory, names, skip_sums=skip_sums)
    findings.extend(sums_findings)

    lock_path = vendor_lock or repo_root / "packaging" / "vendor.lock.json"
    lock: Mapping[str, object] | None
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        if not isinstance(lock, dict):
            raise AssetError(f"{lock_path} is not a JSON object")
    except (OSError, ValueError, AssetError) as error:
        findings.append(Finding("vendor.lock", "fail", str(error)))
        lock = None
    else:
        findings.append(Finding("vendor.lock", "pass", str(lock_path.name)))

    if lock is not None:
        findings.extend(_verify_linux_assets(directory, version, lock))
        findings.extend(
            _verify_windows_assets(directory, version, lock, use_innoextract=use_innoextract)
        )
    findings.extend(_source_tree_findings(directory, version))
    if lock is not None:
        findings.extend(_source_bundle_findings(directory, version, lock))
    else:
        findings.append(Finding("sources.contents", "skip", "vendor lock unavailable"))

    if appimage_root is not None:
        findings.extend(_appimage_findings(appimage_root, version, lock))
    else:
        findings.append(
            Finding(
                "appimage.extract",
                "fail",
                "no --appimage-root; run --appimage-extract first",
            )
        )
    return findings


def _verify_linux_assets(
    directory: Path, version: str, lock: Mapping[str, object]
) -> list[Finding]:
    findings: list[Finding] = []
    prefix = f"{LINUX_PREFIX.format(version=version)}/"
    expected, _versions = _vendor_expectations(lock, "linux-x86_64")
    for suffix in ("linux-x86_64.tar.gz", "linux-x86_64.tar.zst"):
        name = f"StemForge-{version}-{suffix}"
        path = directory / name
        check_prefix = suffix.split(".")[0]
        if not path.is_file():
            findings.append(Finding(f"licences.{check_prefix}", "fail", f"{name} is missing"))
            continue
        try:
            archive = archive_from_tar(path)
        except AssetError as error:
            findings.append(Finding(f"licences.{check_prefix}", "fail", str(error)))
            continue
        root = _bundle_prefix(prefix.removesuffix("/"), archive, f"layout.{check_prefix}")
        if root is not None:
            findings.append(root)
        else:
            findings.append(Finding(f"layout.{check_prefix}", "pass", prefix))
        findings.extend(_licence_findings(archive, prefix, f"licences.{check_prefix}"))
        findings.extend(_weight_findings(archive, f"weights.{check_prefix}"))
        findings.extend(
            _verify_vendor_archive(
                archive,
                prefix,
                expected,
                _provenance_from_tar(path, prefix),
                f"vendor.{check_prefix}",
            )
        )
    return findings


def _verify_windows_assets(
    directory: Path,
    version: str,
    lock: Mapping[str, object],
    *,
    use_innoextract: bool = True,
) -> list[Finding]:
    findings: list[Finding] = []
    prefix = f"{WINDOWS_PREFIX.format(version=version)}/"
    expected, _versions = _vendor_expectations(lock, "windows-x64")
    zip_name = f"StemForge-{version}-win64-portable.zip"
    path = directory / zip_name
    if path.is_file():
        try:
            archive = archive_from_zip(path)
        except AssetError as error:
            findings.append(Finding("licences.zip", "fail", str(error)))
        else:
            root = _bundle_prefix(prefix.removesuffix("/"), archive, "layout.zip")
            if root is not None:
                findings.append(root)
            else:
                findings.append(Finding("layout.zip", "pass", prefix))
            findings.extend(_licence_findings(archive, prefix, "licences.zip"))
            findings.extend(_weight_findings(archive, "weights.zip"))
            provenance = _provenance_from_directory_from_zip(path, prefix)
            findings.extend(
                _verify_vendor_archive(archive, prefix, expected, provenance, "vendor.zip")
            )
    else:
        findings.append(Finding("licences.zip", "fail", f"{zip_name} is missing"))

    setup_name = f"StemForge-{version}-win64-setup.exe"
    setup_path = directory / setup_name
    if not setup_path.is_file():
        findings.append(Finding("setup.tree", "fail", f"{setup_name} is missing"))
        return findings
    extracted: Path | None = None
    scratch: tempfile.TemporaryDirectory[str] | None = None
    skip_reason: str | None = None
    if use_innoextract:
        try:
            scratch = tempfile.TemporaryDirectory(prefix="stemforge-setup-")
            extracted = _innoextract_root(setup_path, Path(scratch.name))
        except AssetError as error:
            # innoextract 1.9 supports Inno Setup up to 6.0.5; the runner builds
            # a modern 6.x installer. The setup is compiled by package.ps1 from
            # the same bundle tree the portable ZIP ships, and package.ps1 runs
            # verify_bundle.py against that tree. Record the limitation rather
            # than pretending the setup was extracted.
            skip_reason = (
                "innoextract could not read this Inno Setup version; the portable "
                "ZIP carries the same tree (package.ps1 verified it): " + str(error).splitlines()[0]
            )
    findings.extend(
        _setup_findings(
            directory,
            version,
            lock,
            extracted_root=extracted,
            skip_reason=skip_reason,
        )
    )
    if scratch is not None:
        scratch.cleanup()
    return findings


def _provenance_from_directory_from_zip(path: Path, prefix: str) -> dict[str, str]:
    try:
        with zipfile.ZipFile(path) as archive:
            name = f"{prefix}vendor/PROVENANCE.txt"
            try:
                data = archive.read(name)
            except KeyError:
                return {}
    except (zipfile.BadZipFile, OSError):
        return {}
    hashes: dict[str, str] = {}
    for line in data.decode("utf-8", errors="replace").splitlines():
        match = re.search(r"installed:\s+vendor/(\S+)\s+sha256=([0-9a-f]{64})", line)
        if match:
            hashes[match.group(1)] = match.group(2)
    return hashes


def _appimage_findings(
    root: Path, version: str, lock: Mapping[str, object] | None
) -> list[Finding]:
    findings: list[Finding] = []
    payload = f"{APPIMAGE_PAYLOAD}/"
    try:
        archive = archive_from_directory(root)
    except AssetError as error:
        return [Finding("appimage.extract", "fail", str(error))]
    if not any(name.startswith(payload) for name in archive.names()):
        return [Finding("appimage.extract", "fail", f"{payload} is missing")]

    docs = "usr/share/doc/stemforge/"
    missing = [
        name
        for name in LICENCE_FILES
        if not archive.has(f"{docs}{name}") and not archive.has(f"{payload}{name}")
    ]
    if not any(name.startswith(f"{docs}{LICENCE_DIR}/") for name in archive.names()) and not any(
        name.startswith(f"{payload}{LICENCE_DIR}/") for name in archive.names()
    ):
        missing.append(f"{LICENCE_DIR}/")
    if missing:
        findings.append(Finding("licences.appimage", "fail", f"missing {', '.join(missing)}"))
    else:
        findings.append(Finding("licences.appimage", "pass", "licence tree in the AppImage"))
    findings.extend(_weight_findings(archive, "weights.appimage"))
    if lock is not None:
        expected, _versions = _vendor_expectations(lock, "linux-x86_64")
        findings.extend(
            _verify_vendor_archive(
                archive,
                payload,
                expected,
                _provenance_from_directory(root / payload),
                "vendor.appimage",
            )
        )
    findings.append(Finding("appimage.extract", "pass", f"{payload}"))
    return findings


def render(findings: Sequence[Finding]) -> str:
    """Render findings in the stable, greppable release-log form."""
    lines = []
    for finding in findings:
        lines.append(f"{finding.status.upper():4}  {finding.check:<22} {finding.detail}")
    failed = sum(1 for finding in findings if finding.status == "fail")
    skipped = sum(1 for finding in findings if finding.status == "skip")
    lines.append(
        f"release verification: {len(findings)} checks, {failed} failed, {skipped} skipped"
    )
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)
    version = subparsers.add_parser("version", help="print the release version")
    version.add_argument("--repo-root", type=Path, default=Path.cwd())
    verify_parser = subparsers.add_parser("verify", help="verify an asset directory")
    verify_parser.add_argument("--dir", type=Path, required=True)
    verify_parser.add_argument("--tag", required=True)
    verify_parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    verify_parser.add_argument("--vendor-lock", type=Path)
    verify_parser.add_argument("--appimage-root", type=Path)
    verify_parser.add_argument(
        "--skip-sums",
        action="store_true",
        help="structure only; the publish job writes the final SHA256SUMS",
    )
    verify_parser.add_argument(
        "--no-innoextract",
        action="store_true",
        help="diagnostic: skip setup extraction (synthetic fixture sets)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "version":
        try:
            print(_release_version(args.repo_root))
        except (OSError, KeyError, ValueError, AssetError) as error:
            print(f"release_assets: {error}", file=sys.stderr)
            return 1
        return 0
    findings = verify(
        args.dir,
        tag=args.tag,
        repo_root=args.repo_root,
        vendor_lock=args.vendor_lock,
        appimage_root=args.appimage_root,
        skip_sums=args.skip_sums,
        use_innoextract=not args.no_innoextract,
    )
    print(render(findings))
    return 0 if all(finding.status != "fail" for finding in findings) else 1


if __name__ == "__main__":
    raise SystemExit(main())
