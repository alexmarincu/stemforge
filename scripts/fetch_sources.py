#!/usr/bin/env python3
"""Build and verify the third-party corresponding-source bundle.

The sources come from ``packaging/vendor.lock.json``'s ``source_offer.sources``
list and the human-readable mapping in ``packaging/vendor/SOURCES.md``. Every
archive is downloaded over HTTPS (or ``file://`` for tests), verified against
the pinned size and SHA-256, and laid out under a deterministic ``SOURCES/``
tree with the build scripts the release used.

No network is touched in the unit tests: a synthetic lock and local archives are
sufficient. A hash mismatch, a missing mapping row, or an archive path that
escapes ``SOURCES/`` fails closed and leaves no output.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

SCHEMA: Final = 1
DEFAULT_EPOCH: Final = 315532800  # 1980-01-01T00:00:00Z
WEIGHT_SUFFIXES: Final = (".safetensors", ".pt", ".pth", ".ckpt", ".onnx", ".tflite")
COMPONENT_RE: Final = re.compile(r"^[a-z0-9][a-z0-9+._-]*$")
SOURCES_ROOT: Final = "SOURCES"
MANIFEST: Final = f"{SOURCES_ROOT}/MANIFEST.json"
SOURCES_MD: Final = f"{SOURCES_ROOT}/SOURCES.md"


class SourceError(RuntimeError):
    """The source bundle cannot be built or verified as specified."""


@dataclass(frozen=True, slots=True)
class Source:
    """One archive named by the vendor lock's source offer."""

    component: str
    version: str
    license: str
    targets: tuple[str, ...]
    url: str
    filename: str
    sha256: str
    size: int | None
    source: str


@dataclass(frozen=True, slots=True)
class Mapping_:
    """One ``SOURCES.md`` component row."""

    version: str
    license: str


def _read_lock(path: Path) -> Mapping[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SourceError(f"cannot read {path}: {error}") from error
    if not isinstance(payload, dict):
        raise SourceError(f"{path} must contain a JSON object")
    return payload


def _table_rows(text: str, heading: str) -> list[list[str]]:
    """Return the table rows under one ``##`` heading, cells stripped."""
    rows: list[list[str]] = []
    active = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            active = stripped[3:].strip().lower() == heading.lower()
            continue
        if not active or not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if not cells or all(set(cell) <= {"-", ":", " "} for cell in cells):
            continue
        if cells[0].lower() == "component":
            continue
        rows.append(cells)
    return rows


def _unquote(value: str) -> str:
    return value.strip().strip("`").strip()


def read_sources_md(path: Path) -> dict[str, Mapping_]:
    """Parse the component licence/version mapping from ``SOURCES.md``."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise SourceError(f"cannot read {path}: {error}") from error
    mapping: dict[str, Mapping_] = {}
    for row in _table_rows(text, "Components"):
        if len(row) < 3:
            continue
        component = _unquote(row[0])
        if not component:
            continue
        mapping[component] = Mapping_(
            version=_unquote(row[1]),
            license=_unquote(row[2]),
        )
    if not mapping:
        raise SourceError(f"{path} has no Components table")
    for phrase in ("three years", "No weights"):
        if phrase not in text:
            raise SourceError(f"{path} lost its {phrase!r} statement")
    return mapping


def archive_sizes(path: Path) -> dict[tuple[str, str, str], int]:
    """Read the archive table's (component, filename, sha) -> size rows."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    sizes: dict[tuple[str, str, str], int] = {}
    for row in _table_rows(text, "Exact archives"):
        if len(row) < 5:
            continue
        component = _unquote(row[0])
        filename = _unquote(row[2])
        try:
            size = int(_unquote(row[3]).replace(",", "").replace("_", ""))
        except ValueError:
            continue
        sha = _unquote(row[4]).lower()
        sizes[(component, filename, sha)] = size
    return sizes


def _safe_basename(name: str) -> bool:
    return bool(name) and "/" not in name and "\\" not in name and ".." not in name


def _archive_filename(entry: Mapping[str, object]) -> str:
    url = str(entry.get("url", ""))
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"https", "file"}:
        raise SourceError(f"source URL must be https (or file for tests): {url!r}")
    path = urllib.parse.unquote(parsed.path)
    parts = PurePosixPath(path).parts
    if ".." in parts:
        raise SourceError(f"unsafe source URL path: {url!r}")
    filename = PurePosixPath(path).name
    if not filename or filename in {".", ".."}:
        raise SourceError(f"source URL has no filename: {url!r}")
    # The lock filename is a basename. Reject the forms that can escape a
    # directory or hide a file even after a URL is percent-decoded.
    if not COMPONENT_RE.match(filename.replace(".", "_")) and (
        "/" in filename or "\\" in filename or filename.startswith(".")
    ):
        raise SourceError(f"unsafe source filename: {filename!r}")
    return filename


def _locked_archives(lock: Mapping[str, object]) -> dict[str, tuple[str, int | None]]:
    """Map a pinned archive SHA to the lock's recorded filename and size.

    ``source_offer.sources`` records URLs, but the component targets are the
    canonical archive names (the Rubber Band tag URL's basename is just
    ``v4.0.0.tar.gz``; the lock and ``SOURCES.md`` call it
    ``rubberband-4.0.0.tar.gz``). Prefer the recorded name when the SHA matches.
    """
    recorded: dict[str, tuple[str, int | None]] = {}
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
            if not sha or not filename:
                continue
            size = source.get("size")
            try:
                recorded[sha] = (filename, int(size) if size is not None else None)
            except (TypeError, ValueError):
                recorded[sha] = (filename, None)
    return recorded


def load_sources(lock_path: Path, sources_md_path: Path) -> list[Source]:
    """Cross-check the lock's source offer with ``SOURCES.md`` and return rows."""
    lock = _read_lock(lock_path)
    mapping = read_sources_md(sources_md_path)
    sizes = archive_sizes(sources_md_path)
    recorded = _locked_archives(lock)
    offer = lock.get("source_offer")
    if not isinstance(offer, dict) or not isinstance(offer.get("sources"), list):
        raise SourceError(f"{lock_path} has no source_offer.sources list")
    sources: list[Source] = []
    for raw in offer["sources"]:
        if not isinstance(raw, dict):
            raise SourceError(f"{lock_path}: source_offer.sources[] must be objects")
        component = str(raw.get("component", ""))
        if not COMPONENT_RE.match(component):
            raise SourceError(f"unsafe source component: {component!r}")
        if component not in mapping:
            raise SourceError(
                f"{sources_md_path} has no Components row for source component {component!r}"
            )
        version = str(raw.get("version", ""))
        license_id = str(raw.get("license", ""))
        if mapping[component].version != version:
            raise SourceError(
                f"{sources_md_path} version for {component} is "
                f"{mapping[component].version!r}, lock says {version!r}"
            )
        if mapping[component].license != license_id:
            raise SourceError(
                f"{sources_md_path} licence for {component} is "
                f"{mapping[component].license!r}, lock says {license_id!r}"
            )
        url_filename = _archive_filename(raw)
        sha = str(raw.get("sha256", "")).lower()
        if not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise SourceError(f"{component}: invalid SHA-256 in the lock")
        filename, locked_size = recorded.get(sha, (url_filename, None))
        if not _safe_basename(filename):
            raise SourceError(f"unsafe locked source filename: {filename!r}")
        size = sizes.get((component, filename, sha))
        if size is None:
            size = locked_size
        targets = raw.get("targets", [])
        if not isinstance(targets, list) or not all(isinstance(item, str) for item in targets):
            raise SourceError(f"{component}: targets must be a string list")
        sources.append(
            Source(
                component=component,
                version=version,
                license=license_id,
                targets=tuple(str(item) for item in targets),
                url=str(raw.get("url", "")),
                filename=filename,
                sha256=sha,
                size=size,
                source=str(raw.get("source", "")),
            )
        )
    if not sources:
        raise SourceError("the source offer has no archives")
    return sources


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_archive(path: Path, source: Source) -> None:
    if not path.is_file():
        raise SourceError(f"missing cached source: {path}")
    size = path.stat().st_size
    if source.size is not None and size != source.size:
        raise SourceError(
            f"size mismatch for {source.filename} (expected {source.size}, got {size})"
        )
    actual = _hash(path)
    if actual != source.sha256:
        raise SourceError(
            f"SHA-256 mismatch for {source.filename} (expected {source.sha256}, got {actual})"
        )


def ensure_archive(source: Source, cache: Path, *, timeout: float) -> Path:
    """Download or copy one pinned archive; fail closed on any mismatch."""
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / source.filename
    if destination.is_file():
        _check_archive(destination, source)
        return destination
    if not source.url.startswith(("https://", "file://")):
        raise SourceError(f"refusing a non-HTTPS source URL: {source.url!r}")
    partial = cache / f".{source.filename}.part.{os.getpid()}"
    partial.unlink(missing_ok=True)
    digest = hashlib.sha256()
    size = 0
    try:
        with (
            urllib.request.urlopen(source.url, timeout=timeout) as response,
            partial.open("wb") as handle,
        ):
            for chunk in iter(lambda: response.read(1024 * 1024), b""):
                handle.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        if source.size is not None and size != source.size:
            raise SourceError(
                f"size mismatch for {source.filename} (expected {source.size}, got {size})"
            )
        if digest.hexdigest() != source.sha256:
            raise SourceError(
                f"SHA-256 mismatch for {source.filename} "
                f"(expected {source.sha256}, got {digest.hexdigest()})"
            )
        os.replace(partial, destination)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return destination


def _tarinfo(name: str, size: int, mode: int, epoch: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = size
    info.mode = mode
    info.mtime = epoch
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.type = tarfile.REGTYPE
    return info


def _manifest(sources: Sequence[Source], version: str) -> bytes:
    payload = {
        "schema": SCHEMA,
        "version": version,
        "generated_by": "scripts/fetch_sources.py",
        "sources": [
            {
                "component": source.component,
                "version": source.version,
                "license": source.license,
                "targets": list(source.targets),
                "url": source.url,
                "filename": source.filename,
                "size": source.size,
                "sha256": source.sha256,
                "source": source.source,
                "path": f"{SOURCES_ROOT}/{source.component}/{source.filename}",
            }
            for source in sorted(sources, key=lambda item: (item.component, item.filename))
        ],
    }
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _stage(
    sources: Sequence[Source],
    cache: Path,
    work: Path,
    *,
    root: Path,
    sources_md: Path,
    version: str,
    timeout: float,
) -> Path:
    stage = work / SOURCES_ROOT
    stage.mkdir(parents=True)
    shutil.copy2(sources_md, stage / "SOURCES.md")
    (stage / "MANIFEST.json").write_bytes(_manifest(sources, version))
    build = stage / "build"
    build.mkdir()
    for name in ("vendor-fetch.sh", "vendor-fetch.ps1"):
        source = root / "scripts" / name
        if not source.is_file():
            raise SourceError(f"missing build script: {source}")
        shutil.copy2(source, build / name)
    for source in sources:
        archive = ensure_archive(source, cache, timeout=timeout)
        destination = stage / source.component / source.filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(archive, destination)
        _check_archive(destination, source)
    return stage


def _write_tar(stage_root: Path, output: Path, epoch: int) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp.{os.getpid()}")
    temporary.unlink(missing_ok=True)
    try:
        if output.name.endswith(".tar.zst"):
            plain = temporary.with_suffix(".tar")
            plain.unlink(missing_ok=True)
            with tarfile.open(plain, "w", format=tarfile.GNU_FORMAT) as archive:
                _write_members(archive, stage_root, epoch)
            tool = shutil.which("zstd")
            if tool is None:
                raise SourceError("zstd is required to write a .tar.zst source bundle")
            result = subprocess.run(
                [tool, "-q", "--no-progress", "-19", "-T0", "-o", str(temporary), str(plain)],
                capture_output=True,
                text=True,
            )
            plain.unlink(missing_ok=True)
            if result.returncode != 0:
                raise SourceError(f"zstd failed: {result.stderr.strip()}")
        elif output.name.endswith((".tar.gz", ".tgz")):
            with (
                temporary.open("wb") as raw,
                gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as compressed,
                tarfile.open(fileobj=compressed, mode="w", format=tarfile.GNU_FORMAT) as archive,
            ):
                _write_members(archive, stage_root, epoch)
        elif output.name.endswith(".tar"):
            with tarfile.open(temporary, "w", format=tarfile.GNU_FORMAT) as archive:
                _write_members(archive, stage_root, epoch)
        else:
            raise SourceError(
                f"unsupported output suffix for {output.name}; use .tar.zst, .tar.gz, or .tar"
            )
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def _write_members(archive: tarfile.TarFile, stage_root: Path, epoch: int) -> None:
    for path in sorted(stage_root.rglob("*")):
        if not path.is_file():
            continue
        name = f"{SOURCES_ROOT}/{path.relative_to(stage_root).as_posix()}"
        mode = 0o755 if name.endswith((".sh", ".ps1")) else 0o644
        with path.open("rb") as handle:
            archive.addfile(_tarinfo(name, path.stat().st_size, mode, epoch), handle)


def _read_archive(path: Path) -> tarfile.TarFile:
    if not path.is_file():
        raise SourceError(f"missing source bundle: {path}")
    if path.name.endswith(".tar.zst"):
        tool = shutil.which("zstd")
        if tool is None:
            raise SourceError(f"zstd is required to read {path.name}")
        process = subprocess.Popen(
            [tool, "-dc", "--", str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        assert process.stdout is not None
        return tarfile.open(fileobj=process.stdout, mode="r|")
    if path.name.endswith((".tar.gz", ".tgz")):
        return tarfile.open(path, "r:gz")
    return tarfile.open(path, "r:")


def verify_bundle(path: Path, sources: Sequence[Source], version: str) -> list[str]:
    """Return every verification failure; an empty list is a valid bundle."""
    problems: list[str] = []
    try:
        archive = _read_archive(path)
    except (OSError, SourceError, tarfile.TarError) as error:
        return [f"cannot read {path}: {error}"]
    names: list[str] = []
    hashes: dict[str, str] = {}
    manifest_data: bytes | None = None
    try:
        with archive:
            for info in archive:
                if not info.isreg():
                    continue
                if (
                    PurePosixPath(info.name).is_absolute()
                    or ".." in PurePosixPath(info.name).parts
                    or "\\" in info.name
                ):
                    problems.append(f"unsafe archive path: {info.name!r}")
                    continue
                names.append(info.name)
                handle = archive.extractfile(info)
                if handle is None:
                    problems.append(f"cannot read {info.name}")
                    continue
                digest = hashlib.sha256()
                data = bytearray()
                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    if info.name == MANIFEST:
                        data.extend(chunk)
                hashes[info.name] = digest.hexdigest()
                if info.name == MANIFEST:
                    manifest_data = bytes(data)
    except (OSError, tarfile.TarError) as error:
        return [f"cannot read {path}: {error}"]

    for required in (
        SOURCES_MD,
        MANIFEST,
        f"{SOURCES_ROOT}/build/vendor-fetch.sh",
        f"{SOURCES_ROOT}/build/vendor-fetch.ps1",
    ):
        if required not in names:
            problems.append(f"missing {required}")
    for name in names:
        lowered = name.lower()
        if lowered.endswith(WEIGHT_SUFFIXES) or "models/htdemucs" in lowered:
            problems.append(f"model data in the source bundle: {name}")
    for source in sources:
        member = f"{SOURCES_ROOT}/{source.component}/{source.filename}"
        if member not in names:
            problems.append(f"missing {member}")
            continue
        if hashes.get(member) != source.sha256:
            problems.append(f"{member}: SHA-256 mismatch")
    if manifest_data is not None:
        try:
            manifest = json.loads(manifest_data)
        except ValueError as error:
            problems.append(f"{MANIFEST}: invalid JSON: {error}")
        else:
            if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
                problems.append(f"{MANIFEST}: unsupported schema")
            elif str(manifest.get("version")) != version:
                problems.append(
                    f"{MANIFEST}: version {manifest.get('version')!r} is not {version!r}"
                )
    return problems


def _read_version(root: Path) -> str:
    import ast

    with (root / "packaging" / "release.toml").open("rb") as handle:
        config = tomllib.load(handle)
    source = root / str(config["project"]["version_file"])
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__version__":
                    value = ast.literal_eval(node.value)
                    if isinstance(value, str) and value:
                        return value
    raise SourceError(f"{source} does not define __version__")


def _epoch() -> int:
    raw = os.environ.get("SOURCE_DATE_EPOCH", str(DEFAULT_EPOCH))
    try:
        return int(raw)
    except ValueError as error:
        raise SourceError(f"SOURCE_DATE_EPOCH is not an integer: {raw!r}") from error


def build(
    *,
    lock_path: Path,
    sources_md: Path,
    root: Path,
    output: Path,
    cache: Path,
    version: str,
    timeout: float,
) -> None:
    sources = load_sources(lock_path, sources_md)
    epoch = _epoch()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="stemforge-sources-") as raw:
        work = Path(raw)
        stage = _stage(
            sources,
            cache,
            work,
            root=root,
            sources_md=sources_md,
            version=version,
            timeout=timeout,
        )
        _write_tar(stage, output, epoch)
    problems = verify_bundle(output, sources, version)
    if problems:
        output.unlink(missing_ok=True)
        raise SourceError("; ".join(problems))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lock", type=Path, default=Path("packaging/vendor.lock.json"))
    parser.add_argument("--sources-md", type=Path, default=Path("packaging/vendor/SOURCES.md"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=Path("build/sources-cache"))
    parser.add_argument("--version", help="default: read from packaging/release.toml")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument(
        "--verify",
        action="store_true",
        help="verify --output instead of downloading and building it",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.verify:
        try:
            version = args.version or _read_version(args.root)
            sources = load_sources(args.lock, args.sources_md)
            problems = verify_bundle(args.output, sources, version)
        except SourceError as error:
            print(f"fetch_sources: {error}", file=sys.stderr)
            return 1
        for problem in problems:
            print(f"FAIL  {problem}")
        if problems:
            print(f"fetch_sources: {len(problems)} problem(s) in {args.output}")
            return 1
        print(f"fetch_sources: {args.output} verified ({len(sources)} archives)")
        return 0
    try:
        version = args.version or _read_version(args.root)
        build(
            lock_path=args.lock,
            sources_md=args.sources_md,
            root=args.root,
            output=args.output,
            cache=args.cache,
            version=version,
            timeout=args.timeout,
        )
    except (OSError, SourceError, urllib.error.URLError) as error:
        print(f"fetch_sources: {error}", file=sys.stderr)
        return 1
    print(f"fetch_sources: wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
