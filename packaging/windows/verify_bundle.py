#!/usr/bin/env python3
"""Verify a Windows StemForge bundle before it becomes a release artifact.

Ticket 21 and ticket 10 §9. The checks are deliberately executable on Linux so
the unit job can feed synthetic trees to the same code the Windows runner uses:

* budgets for the portable ZIP (450 MiB target, 600 MiB hard fail) and the Inno
  setup (380 MiB target, 500 MiB hard fail);
* no model weights anywhere;
* no ``torch/test``, no ``torch/bin`` except ``torch_shm_manager``, no
  ``torchaudio``;
* ``vendor/`` exactly matches ``packaging/vendor.lock.json`` for ``windows-x64``;
* the licence tree (``LICENSE``, ``THIRD_PARTY_NOTICES.md``,
  ``SOURCE_OFFER.md``, ``LICENSES/``) is complete; every curated component has
  a ``NOTICE.md`` and every shipped native file maps to a curated notice or a
  row in ``LICENSES/python/DEPENDENCIES.md``;
* ``StemForge.exe`` is a GUI-subsystem PE with a ``VS_VERSION_INFO`` resource;
* the pinned WebView2 bootstrapper, when supplied, matches its lock.

The release licence gate belongs to ticket 22. A build may pass
``--allow-licence-pending`` only for non-release evidence; ``package.ps1`` never
does, and a bundle carrying ``LICENCE-GATE-PENDING.txt`` is refused there.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
import tomllib
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
DEFAULT_LOCK: Final = REPO_ROOT / "packaging" / "vendor.lock.json"
DEFAULT_NATIVE_MANIFEST: Final = REPO_ROOT / "packaging" / "licenses" / "native.toml"
DEFAULT_WEBVIEW2_LOCK: Final = REPO_ROOT / "packaging" / "windows" / "webview2.lock.json"

TARGET: Final = "windows-x64"
BUNDLE_EXE: Final = "StemForge.exe"
MIB: Final = 1024 * 1024

ZIP_TARGET_BYTES: Final = 450 * MIB
ZIP_FAIL_BYTES: Final = 600 * MIB
SETUP_TARGET_BYTES: Final = 380 * MIB
SETUP_FAIL_BYTES: Final = 500 * MIB

GUI_SUBSYSTEM: Final = 2
AMD64_MACHINE: Final = 0x8664
PE32_MAGIC: Final = 0x10B
PE32_PLUS_MAGIC: Final = 0x20B
VERSION_RESOURCE_KEY: Final = "VS_VERSION_INFO".encode("utf-16-le")

WEIGHT_SUFFIXES: Final = (".safetensors", ".pt", ".pth", ".onnx", ".ckpt", ".h5", ".gguf")
NATIVE_SUFFIXES: Final = (".exe", ".dll", ".pyd", ".so", ".dylib")
TORCH_BIN_ALLOWED: Final = frozenset({"torch_shm_manager", "torch_shm_manager.exe"})
LICENCE_ROOT_FILES: Final = ("LICENSE", "THIRD_PARTY_NOTICES.md", "SOURCE_OFFER.md")
LICENCE_REQUIRED_DIRS: Final = ("LICENSES", "LICENSES/common", "LICENSES/python")
LICENCE_README: Final = "LICENSES/README.md"
PYTHON_DEPENDENCIES: Final = "LICENSES/python/DEPENDENCIES.md"

# Ticket 22's native.toml is a curated component list, not a file-path mapping.
# These are the components a shipped native file may be attributed to by
# location; everything else must have a row in LICENSES/python/DEPENDENCIES.md.
CURATED_NATIVE_NOTICES: Final = {
    "ffmpeg": "ffmpeg",
    "deno": "deno",
    "rubberband": "rubberband",
    "libsndfile": "libsndfile",
    "pyinstaller": "pyinstaller",
    "pytorch": "pytorch",
    "torch": "pytorch",
    "numpy": "numpy",
    "python-runtime": "python-runtime",
    "llvm-openmp": "llvm-openmp",
    "gcc-runtime": "gcc-runtime",
    "openblas": "openblas",
    "openssl": "openssl",
    "webview2": "webview2",
}
NATIVE_OWNER_ALIASES: Final = {
    "_cffi_backend": "cffi",
    "clr_loader": "clr-loader",
    "pydantic_core": "pydantic-core",
    "yaml": "pyyaml",
    "webview": "pywebview",
}
RUNTIME_NATIVE_PREFIXES: Final = (
    "python3",
    "python313",
    "vcruntime",
    "msvcp",
    "concrt",
    "vcomp",
    "ucrtbase",
    "api-ms-win-",
    "libffi",
    "sqlite3",
    "libexpat",
    "libbz2",
    "liblzma",
    "libmpdec",
    "zlib",
)
RUNTIME_NATIVE_NAMES: Final = frozenset(
    {
        "_asyncio",
        "_bz2",
        "_ctypes",
        "_decimal",
        "_elementtree",
        "_hashlib",
        "_lzma",
        "_multiprocessing",
        "_overlapped",
        "_queue",
        "_socket",
        "_sqlite3",
        "_ssl",
        "_uuid",
        "_wmi",
        "pyexpat",
        "select",
        "unicodedata",
        "winsound",
    }
)

LEVEL_PASS: Final = "pass"
LEVEL_WARN: Final = "warn"
LEVEL_ERROR: Final = "error"


@dataclass(frozen=True, slots=True)
class Finding:
    """One verification result."""

    level: str
    check: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"level": self.level, "check": self.check, "message": self.message}


@dataclass(slots=True)
class Report:
    """The ordered findings for one bundle."""

    bundle: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> tuple[Finding, ...]:
        return tuple(item for item in self.findings if item.level == LEVEL_ERROR)

    @property
    def ok(self) -> bool:
        return not self.errors

    def pass_(self, check: str, message: str) -> None:
        self.findings.append(Finding(LEVEL_PASS, check, message))

    def warn(self, check: str, message: str) -> None:
        self.findings.append(Finding(LEVEL_WARN, check, message))

    def error(self, check: str, message: str) -> None:
        self.findings.append(Finding(LEVEL_ERROR, check, message))

    def to_dict(self) -> dict[str, Any]:
        return {
            "bundle": self.bundle,
            "ok": self.ok,
            "errors": len(self.errors),
            "findings": [item.to_dict() for item in self.findings],
        }

    def render(self) -> str:
        lines = [
            f"verify-bundle: bundle={self.bundle}",
            *(f"{item.level:<5} {item.check:<20} {item.message}" for item in self.findings),
        ]
        if self.ok:
            lines.append(f"verify-bundle: OK ({len(self.findings)} checks)")
        else:
            lines.append(
                f"verify-bundle: FAILED ({len(self.errors)} error(s), {len(self.findings)} checks)"
            )
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class PeFacts:
    """The inspectable facts from a Windows PE file."""

    machine: int
    subsystem: int
    has_resources: bool
    has_version_resource: bool


@dataclass(frozen=True, slots=True)
class ArtifactBudget:
    """A size target and the hard fail threshold for one artifact kind."""

    target: int
    fail: int


@dataclass(frozen=True, slots=True)
class NativeComponent:
    """One curated row from ``packaging/licenses/native.toml``."""

    id: str
    title: str
    kind: str
    texts: tuple[str, ...]
    buildinfo: bool


BUDGETS: Final = {
    "zip": ArtifactBudget(ZIP_TARGET_BYTES, ZIP_FAIL_BYTES),
    "setup": ArtifactBudget(SETUP_TARGET_BYTES, SETUP_FAIL_BYTES),
}


def _mib(value: int) -> str:
    return f"{value / MIB:.1f} MiB"


def budget_error(kind: str, size: int) -> str | None:
    """Return a message when ``size`` violates the budget for ``kind``.

    The target is the release expectation; crossing it fails this verifier. The
    higher fail threshold is named in the message so a human can tell the
    difference between "trim the bundle" and "the artifact is unusable".
    """
    budget = BUDGETS.get(kind)
    if budget is None:
        raise ValueError(f"unknown artifact kind: {kind}")
    if size > budget.fail:
        return (
            f"{kind} is {_mib(size)}, over the hard fail threshold "
            f"of {_mib(budget.fail)} (target {_mib(budget.target)})"
        )
    if size > budget.target:
        return (
            f"{kind} is {_mib(size)}, over the {_mib(budget.target)} budget "
            f"(hard fail at {_mib(budget.fail)})"
        )
    return None


def pe_facts(path: Path) -> PeFacts | None:
    """Read the machine, subsystem, and resource presence from ``path``.

    A small, dependency-free PE reader: PyInstaller's windowed bootloader must
    report ``IMAGE_SUBSYSTEM_WINDOWS_GUI`` and a version resource, and those
    facts are checkable without executing the file.
    """
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if len(data) < 0x40 or data[:2] != b"MZ":
        return None
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if e_lfanew + 24 > len(data) or data[e_lfanew : e_lfanew + 4] != b"PE\0\0":
        return None
    coff = e_lfanew + 4
    try:
        machine, _sections, _timestamp, _symbols, _count, optional_size, _flags = (
            struct.unpack_from("<HHIIIHH", data, coff)
        )
    except struct.error:
        return None
    optional = coff + 20
    if optional + optional_size > len(data) or optional + 70 > len(data):
        return None
    magic = struct.unpack_from("<H", data, optional)[0]
    if magic not in (PE32_MAGIC, PE32_PLUS_MAGIC):
        return None
    if optional + optional_size < optional + 70:
        return None
    subsystem = struct.unpack_from("<H", data, optional + 68)[0]

    directories = optional + (96 if magic == PE32_MAGIC else 112)
    count_offset = optional + (92 if magic == PE32_MAGIC else 108)
    has_resources = False
    if count_offset + 4 <= len(data):
        count = struct.unpack_from("<I", data, count_offset)[0]
        if count > 2 and directories + 24 <= len(data):
            resource_rva, resource_size = struct.unpack_from("<II", data, directories + 16)
            has_resources = resource_rva != 0 and resource_size != 0
    return PeFacts(
        machine=machine,
        subsystem=subsystem,
        has_resources=has_resources,
        has_version_resource=VERSION_RESOURCE_KEY in data,
    )


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _files(root: Path) -> Iterator[Path]:
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*")):
        if path.is_file():
            yield path


def _check_layout(report: Report, bundle: Path) -> None:
    required = (
        BUNDLE_EXE,
        "_internal",
        "_internal/vendor.lock.json",
        "_internal/models/models.lock.json",
    )
    missing = [name for name in required if not (bundle / name).exists()]
    if missing:
        report.error("layout", "missing from the bundle: " + ", ".join(missing))
    else:
        report.pass_("layout", "StemForge.exe, _internal/, and the frozen locks are present")

    facts = pe_facts(bundle / BUNDLE_EXE)
    if facts is None:
        report.error("exe.pe", f"{BUNDLE_EXE} is not a readable PE file")
        return
    if facts.machine != AMD64_MACHINE:
        report.error("exe.pe", f"{BUNDLE_EXE} machine is 0x{facts.machine:04x}, not AMD64")
    if facts.subsystem != GUI_SUBSYSTEM:
        report.error(
            "exe.pe",
            f"{BUNDLE_EXE} subsystem is {facts.subsystem}, not the GUI subsystem "
            f"({GUI_SUBSYSTEM}); a console window would appear",
        )
    if not facts.has_resources or not facts.has_version_resource:
        report.error("exe.version", f"{BUNDLE_EXE} has no VS_VERSION_INFO resource")
    else:
        report.pass_("exe.pe", "AMD64 GUI subsystem with a version resource")


def _check_forbidden_contents(report: Report, bundle: Path) -> None:
    weights: list[str] = []
    torch_test: list[str] = []
    torch_bin: list[str] = []
    torchaudio: list[str] = []
    for path in _files(bundle):
        rel = _relative(path, bundle)
        lowered = rel.lower()
        name = path.name.lower()
        if lowered.endswith(WEIGHT_SUFFIXES):
            weights.append(rel)
        if "/torch/test/" in f"/{lowered}" or lowered.startswith("torch/test/"):
            torch_test.append(rel)
        if (
            "/torch/bin/" in f"/{lowered}" or lowered.startswith("torch/bin/")
        ) and name not in TORCH_BIN_ALLOWED:
            torch_bin.append(rel)
        if any(part == "torchaudio" for part in lowered.split("/")):
            torchaudio.append(rel)

    if weights:
        report.error("weights", "model weights ship: " + ", ".join(weights[:5]))
    else:
        report.pass_("weights", "no model weights in the bundle")
    if torch_test:
        report.error("torch.test", "torch/test ships: " + ", ".join(torch_test[:5]))
    else:
        report.pass_("torch.test", "torch/test is trimmed")
    if torch_bin:
        report.error(
            "torch.bin",
            "torch/bin ships more than torch_shm_manager: " + ", ".join(torch_bin[:5]),
        )
    else:
        report.pass_("torch.bin", "torch/bin, if present, holds only torch_shm_manager")
    if torchaudio:
        report.error("torchaudio", "torchaudio ships: " + ", ".join(torchaudio[:5]))
    else:
        report.pass_("torchaudio", "torchaudio is absent")


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("the top-level JSON value is not an object")
    return payload


def _vendor_entries(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    components = payload.get("components")
    if not isinstance(components, dict):
        raise ValueError("vendor.lock.json has no components object")
    entries: dict[str, dict[str, Any]] = {}
    for _name, record in components.items():
        if not isinstance(record, dict):
            continue
        targets = record.get("targets")
        if not isinstance(targets, dict):
            continue
        target = targets.get(TARGET)
        if not isinstance(target, dict):
            continue
        install = target.get("install")
        if not isinstance(install, list):
            continue
        for item in install:
            if not isinstance(item, dict) or not isinstance(item.get("to"), str):
                continue
            entries[item["to"]] = item
    return entries


def _check_vendor(report: Report, bundle: Path, lock_path: Path) -> None:
    vendor = bundle / "vendor"
    try:
        payload = _load_json(lock_path)
        entries = _vendor_entries(payload)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        report.error("vendor.lock", f"{lock_path}: {error}")
        return
    if not entries:
        report.error("vendor.lock", f"{lock_path} has no {TARGET} install entries")
        return

    missing: list[str] = []
    mismatched: list[str] = []
    for relative, item in sorted(entries.items()):
        path = vendor / relative
        if not path.is_file():
            missing.append(relative)
            continue
        expected = item.get("sha256")
        if isinstance(expected, str) and expected:
            actual = _sha256(path)
            if actual != expected:
                mismatched.append(f"{relative} (expected {expected}, got {actual})")
        if item.get("executable") and not relative.lower().endswith(".exe"):
            mismatched.append(f"{relative} is executable but not a .exe")

    if missing:
        report.error("vendor.files", "missing vendor files: " + ", ".join(missing))
    if mismatched:
        report.error("vendor.files", "vendor mismatches: " + "; ".join(mismatched))
    if not missing and not mismatched:
        report.pass_("vendor.files", f"{len(entries)} vendor files match vendor.lock.json")

    expected_paths = set(entries)
    unexpected = [
        _relative(path, bundle)
        for path in _files(vendor)
        if _relative(path, bundle).removeprefix("vendor/") not in expected_paths
    ]
    if unexpected:
        report.error("vendor.extra", "unlisted files in vendor/: " + ", ".join(unexpected[:8]))
    else:
        report.pass_("vendor.extra", "vendor/ contains only the locked files")


def _native_components(manifest: Path) -> dict[str, NativeComponent]:
    """Read ticket 22's ``[[component]]`` rows from ``native.toml``."""
    payload = tomllib.loads(manifest.read_text(encoding="utf-8"))
    rows = payload.get("component")
    if not isinstance(rows, list):
        raise ValueError("native.toml has no [[component]] rows")
    components: dict[str, NativeComponent] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            continue
        texts = row.get("texts", [])
        if not isinstance(texts, list):
            texts = []
        component_id = row["id"]
        components[component_id] = NativeComponent(
            id=component_id,
            title=str(row.get("title", component_id)),
            kind=str(row.get("kind", "")),
            texts=tuple(item for item in texts if isinstance(item, str)),
            buildinfo=bool(row.get("buildinfo", False)),
        )
    if not components:
        raise ValueError("native.toml has no usable [[component]] rows")
    return components


def _dependency_names(text: str) -> set[str]:
    """The package names in ``LICENSES/python/DEPENDENCIES.md`` rows."""
    names: set[str] = set()
    for line in text.splitlines():
        if line.startswith("| `") and "`" in line[3:]:
            name = line.split("`", 2)[1]
            names.add(name.lower().replace("_", "-"))
    return names


def _native_owner(relative: str) -> str | None:
    """Attribute one shipped native file to a curated notice or a package row."""
    lowered = relative.lower()
    if lowered == "stemforge.exe":
        return "pyinstaller"
    if lowered.startswith("vendor/ffmpeg/"):
        return "ffmpeg"
    if lowered.startswith("vendor/deno/"):
        return "deno"
    if lowered.startswith("vendor/rubberband/rubberband"):
        return "rubberband"
    if lowered.startswith("vendor/rubberband/sndfile"):
        return "libsndfile"
    if not lowered.startswith("_internal/"):
        return None

    inner = relative.removeprefix("_internal/")
    parts = inner.split("/")
    base = parts[-1].lower()
    stem = base.split(".", 1)[0]
    if base.startswith("libiomp5md"):
        return "llvm-openmp"
    if base.startswith(("libssl", "libcrypto")):
        return "openssl"
    if base.startswith(("libgomp", "libgfortran", "libquadmath")):
        return "gcc-runtime"
    if base.startswith(("libscipy_openblas", "openblas")):
        return "openblas"
    if base.startswith(RUNTIME_NATIVE_PREFIXES):
        return "python-runtime"
    if stem in RUNTIME_NATIVE_NAMES:
        return "python-runtime"

    if len(parts) > 1:
        owner = parts[0].lower()
    else:
        owner = stem
        alias = NATIVE_OWNER_ALIASES.get(owner)
        if alias is not None:
            owner = alias
        elif owner.startswith("_"):
            return "python-runtime"
    owner = NATIVE_OWNER_ALIASES.get(owner, owner)
    return CURATED_NATIVE_NOTICES.get(owner, owner)


def _check_native_coverage(
    report: Report,
    bundle: Path,
    components: dict[str, NativeComponent],
) -> None:
    try:
        dependencies = _dependency_names((bundle / PYTHON_DEPENDENCIES).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        report.error("licence.native", f"{PYTHON_DEPENDENCIES}: {error}")
        return

    native = [
        _relative(path, bundle) for path in _files(bundle) if path.suffix.lower() in NATIVE_SUFFIXES
    ]
    uncovered: list[str] = []
    for relative in native:
        owner = _native_owner(relative)
        if owner is None:
            uncovered.append(relative)
            continue
        if owner in CURATED_NATIVE_NOTICES:
            notice = bundle / "LICENSES" / owner / "NOTICE.md"
            if owner not in components or not notice.is_file():
                uncovered.append(f"{relative} (no LICENSES/{owner}/NOTICE.md)")
        elif owner.lower().replace("_", "-") not in dependencies:
            uncovered.append(f"{relative} (no {owner} row in {PYTHON_DEPENDENCIES})")

    if uncovered:
        report.error(
            "licence.native",
            "native files without a notice/row: " + ", ".join(uncovered[:8]),
        )
    else:
        report.pass_(
            "licence.native",
            f"{len(native)} native files map to a curated notice or a Python dependency row",
        )


def _check_licence_tree(report: Report, bundle: Path, manifest: Path) -> None:
    missing = [name for name in LICENCE_ROOT_FILES if not (bundle / name).is_file()]
    missing.extend(name for name in LICENCE_REQUIRED_DIRS if not (bundle / name).is_dir())
    if not (bundle / LICENCE_README).is_file():
        missing.append(LICENCE_README)
    if not (bundle / PYTHON_DEPENDENCIES).is_file():
        missing.append(PYTHON_DEPENDENCIES)
    if missing:
        report.error("licence.tree", "missing licence tree entries: " + ", ".join(missing))
        return

    try:
        components = _native_components(manifest)
    except (OSError, ValueError, tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        report.error("licence.native", f"{manifest}: {error}")
        return

    try:
        notices = (bundle / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        licence = (bundle / "LICENSE").read_text(encoding="utf-8")
        offer = (bundle / "SOURCE_OFFER.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        report.error("licence.tree", f"unreadable licence text: {error}")
        return

    missing_components: list[str] = []
    missing_texts: list[str] = []
    for component in components.values():
        notice = Path("LICENSES") / component.id / "NOTICE.md"
        if not (bundle / notice).is_file():
            missing_components.append(notice.as_posix())
        if component.buildinfo:
            buildinfo = Path("LICENSES") / component.id / "BUILDINFO.txt"
            if not (bundle / buildinfo).is_file():
                missing_components.append(buildinfo.as_posix())
        for text in component.texts:
            relative = Path("LICENSES") / "common" / f"{text}.txt"
            if not (bundle / relative).is_file():
                missing_texts.append(relative.as_posix())
        lowered = notices.lower()
        if component.id.lower() not in lowered and component.title.lower() not in lowered:
            missing_components.append(f"THIRD_PARTY_NOTICES.md entry for {component.id}")
    if missing_components:
        report.error(
            "licence.tree",
            "missing curated notices: " + ", ".join(missing_components[:8]),
        )
    if missing_texts:
        report.error("licence.tree", "missing common texts: " + ", ".join(missing_texts[:8]))
    if missing_components or missing_texts:
        return

    for required in ("ffmpeg", "rubberband", "libsndfile", "deno", "webview2"):
        if required not in components:
            report.error("licence.native", f"{manifest} has no {required} component")
    if "GNU GENERAL PUBLIC LICENSE" not in licence.upper() or "VERSION 3" not in licence.upper():
        report.error("licence.license", "LICENSE is not the GPL-3 text")
    else:
        report.pass_("licence.license", "LICENSE is the GPL-3 text")
    if len(offer.strip()) < 100 or "three years" not in offer.lower():
        report.error("licence.offer", "SOURCE_OFFER.md is missing the three-year written offer")
    else:
        report.pass_("licence.offer", "SOURCE_OFFER.md carries the written offer")
    report.pass_(
        "licence.notices",
        f"{len(components)} curated components have NOTICE.md entries",
    )

    _check_native_coverage(report, bundle, components)


def _check_licence_pending(report: Report, bundle: Path) -> None:
    marker = bundle / "LICENCE-GATE-PENDING.txt"
    if marker.is_file():
        report.warn(
            "licence.pending",
            "the licence tree is pending ticket 22; this bundle is not releasable",
        )
    else:
        report.pass_("licence.pending", "no licence-pending marker")


def _check_artifacts(
    report: Report,
    *,
    zip_path: Path | None,
    setup_path: Path | None,
) -> None:
    for kind, path in (("zip", zip_path), ("setup", setup_path)):
        if path is None:
            continue
        if not path.is_file():
            report.error(f"budget.{kind}", f"{path} does not exist")
            continue
        size = path.stat().st_size
        problem = budget_error(kind, size)
        if problem is not None:
            report.error(f"budget.{kind}", problem)
        else:
            report.pass_(
                f"budget.{kind}",
                f"{path.name} is {_mib(size)} (budget {_mib(BUDGETS[kind].target)})",
            )


def _check_webview2(
    report: Report,
    *,
    lock_path: Path,
    installer: Path | None,
    bundle: Path,
) -> None:
    try:
        payload = _load_json(lock_path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        report.error("webview2.lock", f"{lock_path}: {error}")
        return
    url = payload.get("url")
    sha = payload.get("sha256")
    size = payload.get("size")
    lock_errors = 0
    if not isinstance(url, str) or not url.startswith("https://"):
        report.error("webview2.lock", f"{lock_path} has no HTTPS bootstrapper URL")
        lock_errors += 1
    if not isinstance(sha, str) or len(sha) != 64:
        report.error("webview2.lock", f"{lock_path} has no 64-character SHA-256")
        lock_errors += 1
    if not isinstance(size, int) or size <= 0:
        report.error("webview2.lock", f"{lock_path} has no positive size")
        lock_errors += 1
    if lock_errors:
        return
    if installer is None:
        report.pass_("webview2.lock", "the bootstrapper pin is present")
        return
    if not installer.is_file():
        report.error("webview2.bootstrapper", f"{installer} is missing")
        return
    actual_size = installer.stat().st_size
    actual_sha = _sha256(installer)
    if actual_size != size:
        report.error(
            "webview2.bootstrapper",
            f"size mismatch (expected {size}, got {actual_size}); refresh the pin only "
            "after confirming Microsoft's change",
        )
    elif actual_sha != sha:
        report.error(
            "webview2.bootstrapper",
            f"SHA-256 mismatch (expected {sha}, got {actual_sha}); refresh the pin only "
            "after confirming Microsoft's change",
        )
    else:
        report.pass_("webview2.bootstrapper", f"{installer.name} matches webview2.lock.json")
    notices = bundle / "THIRD_PARTY_NOTICES.md"
    if (
        notices.is_file()
        and "webview2" not in notices.read_text(encoding="utf-8", errors="replace").lower()
    ):
        report.error("webview2.notice", "THIRD_PARTY_NOTICES.md does not mention WebView2")


def verify(
    bundle: Path,
    *,
    lock_path: Path = DEFAULT_LOCK,
    native_manifest: Path = DEFAULT_NATIVE_MANIFEST,
    allow_licence_pending: bool = False,
    zip_path: Path | None = None,
    setup_path: Path | None = None,
    webview2_lock: Path = DEFAULT_WEBVIEW2_LOCK,
    webview2_installer: Path | None = None,
) -> Report:
    """Run every check and return the ordered findings."""
    bundle = Path(bundle)
    report = Report(bundle=str(bundle))
    if not bundle.is_dir():
        report.error("layout", f"{bundle} is not a directory")
        return report

    _check_layout(report, bundle)
    _check_forbidden_contents(report, bundle)
    _check_vendor(report, bundle, Path(lock_path))
    if allow_licence_pending:
        report.warn(
            "licence.tree",
            "licence tree checks skipped by --allow-licence-pending; not releasable",
        )
        _check_licence_pending(report, bundle)
    else:
        _check_licence_tree(report, bundle, Path(native_manifest))
    _check_artifacts(report, zip_path=zip_path, setup_path=setup_path)
    _check_webview2(
        report,
        lock_path=Path(webview2_lock),
        installer=webview2_installer,
        bundle=bundle,
    )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--native-manifest", type=Path, default=DEFAULT_NATIVE_MANIFEST)
    parser.add_argument(
        "--allow-licence-pending",
        action="store_true",
        help="skip the ticket 22 licence gate; never use for a release artifact",
    )
    parser.add_argument("--zip", type=Path, help="portable ZIP to budget-check")
    parser.add_argument("--setup", type=Path, help="Inno setup executable to budget-check")
    parser.add_argument("--webview2-lock", type=Path, default=DEFAULT_WEBVIEW2_LOCK)
    parser.add_argument("--webview2-installer", type=Path)
    parser.add_argument("--report", type=Path, help="write the JSON report here")
    args = parser.parse_args(argv)

    report = verify(
        args.bundle,
        lock_path=args.lock,
        native_manifest=args.native_manifest,
        allow_licence_pending=args.allow_licence_pending,
        zip_path=args.zip,
        setup_path=args.setup,
        webview2_lock=args.webview2_lock,
        webview2_installer=args.webview2_installer,
    )
    print(report.render())
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
