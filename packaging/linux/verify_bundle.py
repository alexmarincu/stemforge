#!/usr/bin/env python3
"""Verify an assembled Linux Bundle against ticket 10 §2 and §9.

The release gate checks the onedir layout, the licence tree, the torch trim, the
absence of model weights, every vendor file against ``packaging/vendor.lock.json``,
and the size budgets for the tree and the three release archives. It is
stdlib-only so it can run in the PyInstaller build container before any release
tooling is installed.

Usage::

    python3 packaging/linux/verify_bundle.py \
        --bundle dist/StemForge-<version>-linux-x86_64 \
        --archive dist/StemForge-<version>-linux-x86_64.tar.gz \
        --archive dist/StemForge-<version>-linux-x86_64.tar.zst \
        --archive dist/StemForge-<version>-linux-x86_64.AppImage \
        --json

``--self-check`` builds a synthetic bundle in a temporary directory and proves
both the passing and the failing paths without touching a real build.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tarfile
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final, Literal

TARGET: Final = "linux-x86_64"
GIGABYTE: Final = 1_000_000_000
MEGABYTE: Final = 1_000_000

# Ticket 20's budgets: the release fails above these sizes. The larger
# "fail the release" limits in ticket 10 §9 are the outer guard; this verifier
# enforces the tighter target so a bundle cannot creep past it unnoticed.
BUDGET_BYTES: Final[dict[str, int]] = {
    "bundle": 1_150_000_000,
    "tar.gz": 450 * MEGABYTE,
    "tar.zst": 320 * MEGABYTE,
    "appimage": 350 * MEGABYTE,
}

DEFAULT_LOCK: Final = Path(__file__).resolve().parents[2] / "packaging" / "vendor.lock.json"

REQUIRED_FILES: Final = ("LICENSE", "THIRD_PARTY_NOTICES.md", "SOURCE_OFFER.md")
REQUIRED_ENTRIES: Final = ("StemForge", "run.sh", "README.md", "_internal", "vendor")
EXECUTABLE_ENTRIES: Final = ("StemForge", "run.sh")
WEIGHT_SUFFIXES: Final = (".safetensors", ".pt", ".pth", ".ckpt", ".onnx", ".tflite")
VENDOR_METADATA: Final = ("PROVENANCE.txt",)
TORCH_BIN_ALLOWED: Final = ("torch_shm_manager",)
ARCHIVE_SUFFIXES: Final = (
    (".tar.gz", "tar.gz"),
    (".tgz", "tar.gz"),
    (".tar.zst", "tar.zst"),
    (".AppImage", "appimage"),
)

Status = Literal["pass", "fail", "skip"]


@dataclass(frozen=True, slots=True)
class Finding:
    """One check result; ``skip`` is only used by the debug licence switch."""

    check: str
    status: Status
    detail: str


@dataclass(frozen=True, slots=True)
class Verification:
    """The findings plus the measured sizes and the limits they were held to."""

    bundle: Path
    sizes: dict[str, int]
    findings: tuple[Finding, ...]
    budgets: dict[str, int]

    @property
    def ok(self) -> bool:
        return all(finding.status != "fail" for finding in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "bundle": str(self.bundle),
            "sizes": self.sizes,
            "budgets": self.budgets,
            "findings": [
                {"check": finding.check, "status": finding.status, "detail": finding.detail}
                for finding in self.findings
            ],
        }


def tree_size(root: Path) -> int:
    """The sum of every regular file's size, matching ``du -sb`` closely."""
    total = 0
    for path in Path(root).rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def archive_kind(path: Path) -> str | None:
    """Classify an archive by its release filename."""
    name = Path(path).name
    for suffix, kind in ARCHIVE_SUFFIXES:
        if name.endswith(suffix):
            return kind
    return None


def verify_bundle(
    bundle: Path,
    *,
    lock_path: Path | None = None,
    archives: Sequence[Path] = (),
    budgets: Mapping[str, int] | None = None,
    allow_missing_licences: bool = False,
    target: str = TARGET,
) -> Verification:
    """Run every bundle and archive check and return the collected findings."""
    root = Path(bundle)
    limits = dict(BUDGET_BYTES if budgets is None else budgets)
    lock = DEFAULT_LOCK if lock_path is None else Path(lock_path)

    findings: list[Finding] = []
    findings.append(_check_layout(root))
    findings.append(_check_licences(root, allow_missing=allow_missing_licences))
    findings.append(_check_weights(root))
    findings.append(_check_torch(root))
    findings.append(_check_vendor(root, lock, target))

    sizes = {"bundle": tree_size(root)}
    findings.append(_budget("bundle", sizes["bundle"], limits["bundle"]))

    for archive in archives:
        path = Path(archive)
        kind = archive_kind(path)
        if kind is None:
            findings.append(Finding("archive.kind", "fail", f"{path} is not a known archive name"))
            continue
        try:
            size = path.stat().st_size
        except OSError as error:
            findings.append(Finding(f"archive.{kind}", "fail", f"{path}: {error}"))
            continue
        sizes[str(path)] = size
        findings.append(_budget(kind, size, limits[kind]))
        if kind == "tar.gz":
            findings.extend(_check_tar_contents(path, kind, allow_missing_licences))
        else:
            findings.append(
                Finding(
                    f"archive.{kind}.contents",
                    "skip",
                    "built from the verified tree; extraction is covered by smoke-test.sh",
                )
            )

    return Verification(bundle=root, sizes=sizes, findings=tuple(findings), budgets=limits)


def _check_layout(root: Path) -> Finding:
    problems: list[str] = []
    if not root.is_dir():
        return Finding("bundle.layout", "fail", f"{root} is not a directory")
    for name in REQUIRED_ENTRIES:
        if not (root / name).exists():
            problems.append(f"missing {name}")
    for name in EXECUTABLE_ENTRIES:
        path = root / name
        if path.is_file() and not os.access(path, os.X_OK):
            problems.append(f"{name} is not executable")
    if problems:
        return Finding("bundle.layout", "fail", "; ".join(problems))
    return Finding("bundle.layout", "pass", "StemForge, _internal/, vendor/, run.sh, README.md")


def _check_licences(root: Path, *, allow_missing: bool) -> Finding:
    if allow_missing:
        return Finding("bundle.licences", "skip", "debug build: the licence tree is not required")
    problems: list[str] = []
    for name in REQUIRED_FILES:
        path = root / name
        if not path.is_file():
            problems.append(f"missing {name}")
        elif path.stat().st_size == 0:
            problems.append(f"empty {name}")
    licences = root / "LICENSES"
    if not licences.is_dir():
        problems.append("missing LICENSES/")
    elif not any(path.is_file() for path in licences.rglob("*")):
        problems.append("LICENSES/ is empty")
    if problems:
        return Finding("bundle.licences", "fail", "; ".join(problems))
    return Finding("bundle.licences", "pass", "LICENSE, notices, source offer, LICENSES/")


def _check_weights(root: Path) -> Finding:
    problems: list[str] = []
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in WEIGHT_SUFFIXES:
            problems.append(f"weight file {path.relative_to(root)}")
    models = root / "_internal" / "models"
    if models.is_dir():
        for entry in sorted(models.iterdir()):
            if entry.name != "models.lock.json":
                problems.append(f"unexpected model data {entry.relative_to(root)}")
    if problems:
        return Finding("bundle.weights", "fail", "; ".join(problems[:8]))
    return Finding("bundle.weights", "pass", "no model weights or unexpected model data")


def _check_torch(root: Path) -> Finding:
    internal = root / "_internal"
    torch = internal / "torch"
    if not torch.is_dir():
        return Finding("bundle.torch", "fail", "torch/ is missing from _internal/")

    problems: list[str] = []
    if (torch / "test").exists():
        problems.append("torch/test is present")
    bin_dir = torch / "bin"
    if bin_dir.is_dir():
        extra = sorted(
            entry.name for entry in bin_dir.iterdir() if entry.name not in TORCH_BIN_ALLOWED
        )
        if extra:
            problems.append("torch/bin/" + ", torch/bin/".join(extra) + " is present")
    if internal.is_dir():
        for entry in sorted(internal.iterdir()):
            if entry.name == "torchaudio" or entry.name.startswith("torchaudio-"):
                problems.append(f"{entry.name} is present")
    if problems:
        return Finding("bundle.torch", "fail", "; ".join(problems))
    return Finding(
        "bundle.torch",
        "pass",
        "torch/test and torch/bin trimmed to torch_shm_manager; no torchaudio",
    )


def _check_vendor(root: Path, lock_path: Path, target: str) -> Finding:
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        return Finding("bundle.vendor", "fail", f"{lock_path}: {error}")
    declared = _declared_vendor(payload, target)
    if declared is None:
        return Finding("bundle.vendor", "fail", f"{lock_path} has no components for {target}")

    vendor = root / "vendor"
    problems: list[str] = []
    expected: set[Path] = set()
    for relative, executable, digest in declared:
        expected.add(relative)
        path = vendor / relative
        if not path.is_file():
            problems.append(f"missing vendor/{relative.as_posix()}")
            continue
        if executable and not os.access(path, os.X_OK):
            problems.append(f"not executable vendor/{relative.as_posix()}")
        if digest is not None and _sha256(path) != digest:
            problems.append(f"sha256 mismatch vendor/{relative.as_posix()}")

    if vendor.is_dir():
        for path in sorted(vendor.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(vendor)
            parts = relative.parts
            if parts and parts[0] == ".cache":
                continue
            if relative.as_posix() in VENDOR_METADATA:
                continue
            if relative not in expected:
                problems.append(f"undeclared vendor/{relative.as_posix()}")

    if problems:
        return Finding("bundle.vendor", "fail", "; ".join(problems[:8]))
    return Finding(
        "bundle.vendor",
        "pass",
        f"{len(declared)} files match {lock_path.name} for {target}",
    )


def _declared_vendor(payload: object, target: str) -> list[tuple[Path, bool, str | None]] | None:
    if not isinstance(payload, dict):
        return None
    components = payload.get("components")
    if not isinstance(components, dict):
        return None

    declared: dict[Path, tuple[bool, str | None]] = {}
    for record in components.values():
        if not isinstance(record, dict):
            continue
        targets = record.get("targets")
        if not isinstance(targets, dict):
            continue
        entry = targets.get(target)
        if not isinstance(entry, dict):
            continue
        install = entry.get("install")
        if not isinstance(install, list):
            continue
        for item in install:
            if not isinstance(item, dict):
                continue
            destination = item.get("to")
            if not isinstance(destination, str) or not destination:
                continue
            relative = Path(destination)
            if relative.is_absolute() or ".." in relative.parts:
                continue
            digest = item.get("sha256")
            declared[relative] = (
                item.get("executable") is True,
                digest if isinstance(digest, str) else None,
            )
    if not declared:
        return None
    return [(relative, executable, digest) for relative, (executable, digest) in declared.items()]


def _check_tar_contents(
    path: Path, kind: str, allow_missing_licences: bool = False
) -> list[Finding]:
    try:
        with tarfile.open(path, "r:gz") as archive:
            names = archive.getnames()
    except (OSError, tarfile.TarError) as error:
        return [Finding(f"archive.{kind}.contents", "fail", f"{path}: {error}")]

    problems: list[str] = []
    relative_names: list[PurePosixPath] = []
    for name in names:
        parts = PurePosixPath(name).parts
        if len(parts) > 1:
            relative_names.append(PurePosixPath(*parts[1:]))

    if any(relative.suffix.lower() in WEIGHT_SUFFIXES for relative in relative_names):
        problems.append("a weight file is present")
    for relative in relative_names:
        parts = relative.parts
        if "torchaudio" in parts or any(part.startswith("torchaudio-") for part in parts):
            problems.append("torchaudio is present")
            break
        if "test" in parts and "torch" in parts:
            problems.append("torch/test is present")
            break
    bin_files = [
        relative
        for relative in relative_names
        if len(relative.parts) >= 3 and relative.parts[-3:-1] == ("torch", "bin")
    ]
    for relative in bin_files:
        if relative.name not in TORCH_BIN_ALLOWED:
            problems.append(f"torch/bin/{relative.name} is present")
            break

    top = {relative.parts[0] for relative in relative_names if relative.parts}
    if not allow_missing_licences:
        for required in REQUIRED_FILES:
            if required not in top:
                problems.append(f"missing {required}")
        if "LICENSES" not in top:
            problems.append("missing LICENSES/")
    if "StemForge" not in top:
        problems.append("missing the StemForge launcher")
    if "run.sh" not in top:
        problems.append("missing run.sh")

    if problems:
        return [Finding(f"archive.{kind}.contents", "fail", "; ".join(problems[:8]))]
    detail = "no weights"
    if allow_missing_licences:
        detail = f"{detail}; licence check skipped (debug build)"
    else:
        detail = f"licence tree present; {detail}"
    return [Finding(f"archive.{kind}.contents", "pass", detail)]


def _budget(kind: str, size: int, limit: int) -> Finding:
    label = f"{size:,} B of {limit:,} B"
    if size > limit:
        return Finding(f"budget.{kind}", "fail", label)
    return Finding(f"budget.{kind}", "pass", label)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _report(result: Verification, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return
    print(f"bundle: {result.bundle}")
    for finding in result.findings:
        print(f"{finding.status.upper():<5} {finding.check:<28} {finding.detail}")
    print()
    for kind, size in result.sizes.items():
        limit = result.budgets.get(kind)
        print(f"{kind:<60} {size:>13,} B" + (f"  budget {limit:,} B" if limit else ""))
    print(f"\n{'PASS' if result.ok else 'FAIL'}: {result.bundle}")


def _make_self_check_bundle(root: Path) -> Path:
    def write(relative: str, data: bytes, *, executable: bool = False) -> Path:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if executable:
            path.chmod(0o755)
        return path

    write("StemForge", b"launcher", executable=True)
    write("run.sh", b"#!/bin/sh\n", executable=True)
    write("README.md", b"# StemForge\n")
    write("_internal/models/models.lock.json", b"{}")
    write("_internal/torch/bin/torch_shm_manager", b"shm")
    write("LICENSE", b"GPL-3.0-only\n")
    write("THIRD_PARTY_NOTICES.md", b"notices\n")
    write("SOURCE_OFFER.md", b"offer\n")
    write("LICENSES/ffmpeg/LICENSE.txt", b"GPL\n")
    ffmpeg = write("vendor/ffmpeg/ffmpeg", b"ffmpeg", executable=True)
    lock = root.parent / "vendor.lock.json"
    lock.write_text(
        json.dumps(
            {
                "targets": {"linux-x86_64": {"system": "linux", "arch": "x86_64"}},
                "components": {
                    "ffmpeg": {
                        "targets": {
                            "linux-x86_64": {
                                "install": [
                                    {
                                        "to": "ffmpeg/ffmpeg",
                                        "executable": True,
                                        "sha256": _sha256(ffmpeg),
                                    }
                                ]
                            }
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return lock


def _self_check() -> int:
    with tempfile.TemporaryDirectory(prefix="stemforge-verify-") as temporary:
        root = Path(temporary) / "StemForge-9.9.9-linux-x86_64"
        lock = _make_self_check_bundle(root)
        good = verify_bundle(root, lock_path=lock)
        if not good.ok:
            failures = [finding for finding in good.findings if finding.status == "fail"]
            print("self-check: the synthetic good bundle failed", file=sys.stderr)
            for finding in failures:
                print(f"  {finding.check}: {finding.detail}", file=sys.stderr)
            return 1
        (root / "_internal" / "models" / "weights.safetensors").write_bytes(b"weights")
        bad = verify_bundle(root, lock_path=lock)
        if bad.ok:
            print("self-check: a bundle with weights passed", file=sys.stderr)
            return 1
    print("self-check: ok (pass and fail paths)")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="verify_bundle", description=__doc__)
    parser.add_argument("--bundle", type=Path, help="the assembled onedir Bundle")
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK, help="vendor.lock.json")
    parser.add_argument(
        "--archive",
        type=Path,
        action="append",
        default=[],
        help="a release archive to measure (tar.gz, tar.zst, or AppImage)",
    )
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    parser.add_argument(
        "--allow-missing-licences",
        action="store_true",
        help="debug builds before ticket 22; never use for a release",
    )
    parser.add_argument("--self-check", action="store_true", help="exercise both outcomes")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.self_check:
        return _self_check()
    if args.bundle is None:
        print("verify_bundle: --bundle is required (or use --self-check)", file=sys.stderr)
        return 2
    result = verify_bundle(
        args.bundle,
        lock_path=args.lock,
        archives=args.archive,
        allow_missing_licences=args.allow_missing_licences,
    )
    _report(result, as_json=args.json)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
