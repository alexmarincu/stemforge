"""Startup preflight checks for the window stack, storage, and bundled files.

The report lists the checks in a fixed order. Every result is
recorded; the first fatal result decides the process exit code, and a missing
preview stack is only a degraded capability (exit 4 in ``--preflight``, never a
fatal error for the GUI). The module stays stdlib-only at import time and loads
``gi``/``winreg`` lazily inside the probes.
"""

from __future__ import annotations

import contextlib
import importlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal, Protocol, Self

from . import binaries

REPORT_VERSION: Final = 1

EXIT_OK: Final = 0
EXIT_WINDOW_UNAVAILABLE: Final = 3
EXIT_PREVIEW_UNAVAILABLE: Final = 4
EXIT_WEBVIEW2_UNAVAILABLE: Final = 5
EXIT_STORAGE_UNWRITABLE: Final = 6
EXIT_PLATFORM_UNSUPPORTED: Final = 7
EXIT_BUNDLE_DAMAGED: Final = 8

CHECK_PLATFORM: Final = "platform"
CHECK_WINDOW: Final = "window"
CHECK_BUNDLE: Final = "bundle"
CHECK_STORAGE: Final = "storage"
CHECK_PREVIEW: Final = "preview"
CHECK_RESOURCES: Final = "resources"

STATUS_OK: Final = "ok"
STATUS_WARN: Final = "warn"
STATUS_DEGRADED: Final = "degraded"
STATUS_FAIL: Final = "fail"

CheckStatus = Literal["ok", "warn", "degraded", "fail"]

MIN_GLIBC: Final = (2, 35)
MIN_WEBKITGTK: Final = (2, 44, 0)
MIN_DOTNET_RELEASE: Final = 394802
DISK_WARN_BYTES: Final = 20 * 1024**3
DISK_FLOOR_BYTES: Final = 2 * 1024**3
RAM_WARN_BYTES: Final = 8 * 1024**3
GIB: Final = 1024**3

WEBVIEW2_CLIENT_ID: Final = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
PREVIEW_ELEMENTS: Final = ("wavparse", "mpg123audiodec", "autoaudiosink")
VENDOR_TOOLS: Final = ("ffmpeg", "ffprobe", "rubberband", "deno")


class AppPathsLike(Protocol):
    """The :class:`~stemforge.platform.paths.AppPaths` surface preflight needs."""

    @property
    def data(self) -> Path: ...

    @property
    def cache(self) -> Path: ...

    @property
    def logs(self) -> Path: ...

    @property
    def models(self) -> Path: ...

    @property
    def config(self) -> Path: ...

    @property
    def webview(self) -> Path: ...

    @property
    def roots(self) -> tuple[Path, ...]: ...

    def ensure(self) -> Self: ...


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One preflight result; ``fatal`` is true only for a blocking failure."""

    name: str
    status: CheckStatus
    message: str
    fatal: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "fatal": self.fatal,
        }


@dataclass(frozen=True, slots=True)
class PreflightReport:
    """Every check result plus the derived exit code and diagnostics fields."""

    checks: tuple[CheckResult, ...]
    system: str = field(default_factory=lambda: sys.platform)
    fail_on_degraded: bool = True

    @property
    def fatal(self) -> bool:
        return any(result.fatal and result.status == STATUS_FAIL for result in self.checks)

    @property
    def first_fatal(self) -> CheckResult | None:
        for result in self.checks:
            if result.fatal and result.status == STATUS_FAIL:
                return result
        return None

    @property
    def degraded(self) -> bool:
        return any(
            result.name == CHECK_PREVIEW and result.status == STATUS_DEGRADED
            for result in self.checks
        )

    @property
    def warnings(self) -> tuple[str, ...]:
        return tuple(result.message for result in self.checks if result.status == STATUS_WARN)

    @property
    def ok(self) -> bool:
        return self.exit_code == EXIT_OK

    @property
    def exit_code(self) -> int:
        for result in self.checks:
            if result.fatal and result.status == STATUS_FAIL:
                return _exit_code_for(result, self.system)
        if self.fail_on_degraded and self.degraded:
            return EXIT_PREVIEW_UNAVAILABLE
        return EXIT_OK

    def to_dict(self) -> dict[str, object]:
        return {
            "version": REPORT_VERSION,
            "ok": self.ok,
            "exit_code": self.exit_code,
            "degraded": self.degraded,
            "checks": [result.to_dict() for result in self.checks],
            "warnings": list(self.warnings),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def _exit_code_for(result: CheckResult, system: str) -> int:
    if result.name == CHECK_PLATFORM:
        return EXIT_PLATFORM_UNSUPPORTED
    if result.name == CHECK_WINDOW:
        return EXIT_WEBVIEW2_UNAVAILABLE if system.startswith("win") else EXIT_WINDOW_UNAVAILABLE
    if result.name == CHECK_BUNDLE:
        return EXIT_BUNDLE_DAMAGED
    if result.name == CHECK_STORAGE:
        return EXIT_STORAGE_UNWRITABLE
    return EXIT_PLATFORM_UNSUPPORTED


def write_report(path: Path, report: PreflightReport) -> None:
    """Write the same JSON schema as ``--preflight --json`` to ``path``."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(report.to_json() + "\n", encoding="utf-8")


def format_human(report: PreflightReport) -> str:
    """Render one line per check; ``--quiet`` suppresses this entirely."""
    lines = []
    for result in report.checks:
        lines.append(f"{result.name:<10} {result.status:<9} {result.message}")
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class NativeChrome:
    """The dialog text the Interface language owns.

    This module cannot import the catalog, so ``__main__`` resolves every field
    and passes the result in; no English copy lives here.
    """

    fatal_title: str
    details_label: str
    logs_unavailable: str
    copy_details_label: str
    ok_label: str
    already_running_title: str
    already_running_body: str


@dataclass(frozen=True, slots=True)
class FatalMessage:
    """The native-box payload for one fatal preflight result."""

    code: str
    sentence: str
    fix_line: str
    log_path: Path | None
    copy_text: str
    chrome: NativeChrome

    @property
    def title(self) -> str:
        return self.chrome.fatal_title

    @property
    def body(self) -> str:
        details = (
            f"{self.chrome.details_label} {self.log_path}"
            if self.log_path is not None
            else self.chrome.logs_unavailable
        )
        return f"{self.sentence}\n\n{self.fix_line}\n\n{details}"


def build_fatal_message(
    *,
    code: str,
    sentence: str,
    fix_line: str,
    log_path: Path | None,
    copy_text: str,
    chrome: NativeChrome,
) -> FatalMessage:
    """Assemble the body of the native fatal box."""
    return FatalMessage(
        code=code,
        sentence=sentence,
        fix_line=fix_line,
        log_path=log_path,
        copy_text=copy_text,
        chrome=chrome,
    )


MessageBackend = Callable[[str, str, str | None], None]


@dataclass(frozen=True, slots=True)
class WindowProbe:
    """The import/registry facts the native-window check depends on."""

    gi: bool = False
    gtk: str | None = None
    webkit: str | None = None
    soup: str | None = None
    webkit_version: tuple[int, int, int] | None = None
    webview2: str | None = None
    dotnet_release: int | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class PreviewProbe:
    """The GStreamer probe outcome, injected directly in unit tests."""

    method: str
    present: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    error: str | None = None


def run_preflight(
    paths: AppPathsLike,
    *,
    system: str | None = None,
    machine: str | None = None,
    frozen: bool | None = None,
    checkers: Sequence[Callable[[], CheckResult]] | None = None,
    fail_on_degraded: bool = True,
) -> PreflightReport:
    """Run every check in order and return the recorded report."""
    resolved_system = sys.platform if system is None else system
    resolved_machine = platform.machine() if machine is None else machine
    if checkers is not None:
        results = tuple(checker() for checker in checkers)
    else:
        results = (
            check_platform(system=resolved_system, machine=resolved_machine),
            check_window(system=resolved_system),
            check_bundle(frozen=frozen),
            check_storage(paths),
            check_preview(system=resolved_system),
            check_resources(paths),
        )
    return PreflightReport(
        checks=results,
        system=resolved_system,
        fail_on_degraded=fail_on_degraded,
    )


def check_platform(
    *,
    system: str | None = None,
    machine: str | None = None,
    glibc: tuple[int, int] | None = None,
) -> CheckResult:
    """Check the OS, architecture, and the Linux glibc floor (exit 7)."""
    resolved_system = sys.platform if system is None else system
    resolved_machine = platform.machine() if machine is None else machine
    architecture = resolved_machine.lower()

    if resolved_system.startswith("linux"):
        if architecture not in {"x86_64", "amd64"}:
            return _fail(
                CHECK_PLATFORM,
                f"StemForge runs on 64-bit Linux; this machine is {resolved_machine}.",
            )
        detected = _glibc_version() if glibc is None else glibc
        if detected is None:
            return _fail(CHECK_PLATFORM, "The glibc version could not be determined.")
        if detected < MIN_GLIBC:
            return _fail(
                CHECK_PLATFORM,
                f"glibc {detected[0]}.{detected[1]} is older than 2.35.",
            )
        return _ok(
            CHECK_PLATFORM,
            f"Linux x86_64, glibc {detected[0]}.{detected[1]}.",
        )

    if resolved_system.startswith("win"):
        if architecture not in {"amd64", "x86_64"}:
            return _fail(
                CHECK_PLATFORM,
                f"StemForge runs on 64-bit Windows; this machine is {resolved_machine}.",
            )
        return _ok(CHECK_PLATFORM, "Windows x64.")

    return _fail(
        CHECK_PLATFORM,
        f"StemForge runs on 64-bit Linux and 64-bit Windows; {resolved_system} is unsupported.",
    )


def check_window(
    *,
    system: str | None = None,
    probe: WindowProbe | None = None,
) -> CheckResult:
    """Check GTK/WebKitGTK (exit 3) or WebView2/.NET (exit 5)."""
    resolved_system = sys.platform if system is None else system

    if resolved_system.startswith("linux"):
        facts = _probe_linux_window() if probe is None else probe
        missing = [
            label
            for label, value in (
                ("Gtk 3.0", facts.gtk if facts.gi else None),
                ("WebKit2 4.1", facts.webkit if facts.gi else None),
                ("Soup 3.0", facts.soup if facts.gi else None),
            )
            if value is None
        ]
        if not facts.gi:
            return _fail(
                CHECK_WINDOW,
                "the bundled PyGObject (gi) is missing or cannot be imported; reinstall StemForge.",
            )
        if missing:
            return _fail(CHECK_WINDOW, f"Missing: {', '.join(missing)}.")
        if facts.webkit_version is None:
            return _fail(CHECK_WINDOW, "The WebKitGTK version could not be determined.")
        if facts.webkit_version < MIN_WEBKITGTK:
            major, minor, micro = facts.webkit_version
            return _fail(
                CHECK_WINDOW,
                f"WebKitGTK {major}.{minor}.{micro} is older than 2.44.",
            )
        return _ok(CHECK_WINDOW, f"GTK 3, WebKitGTK {_version_text(facts.webkit_version)}, Soup 3.")

    if resolved_system.startswith("win"):
        facts = _probe_windows_window() if probe is None else probe
        if not facts.webview2:
            return _fail(CHECK_WINDOW, "Microsoft Edge WebView2 Runtime is not installed.")
        if facts.dotnet_release is None:
            return _fail(CHECK_WINDOW, "The .NET Framework release could not be determined.")
        if facts.dotnet_release < MIN_DOTNET_RELEASE:
            return _fail(
                CHECK_WINDOW,
                f".NET Framework release {facts.dotnet_release} is older than 4.8.",
            )
        return _ok(
            CHECK_WINDOW,
            f"WebView2 {facts.webview2}, .NET release {facts.dotnet_release}.",
        )

    return _fail(CHECK_WINDOW, f"Native window checks are unsupported on {resolved_system}.")


def check_bundle(
    *,
    bundle_root: Path | None = None,
    data_root: Path | None = None,
    frozen: bool | None = None,
    system: str | None = None,
    target: str | None = None,
    version_of: Callable[[Path, str], str | None] | None = None,
) -> CheckResult:
    """Verify the vendor binaries, lock files, and model lock (exit 8).

    A frozen Bundle must contain every file ``vendor.lock.json`` names for its
    target, each executable must run and report the locked version, and the
    Rubber Band CLI must have its co-located libsndfile. A development checkout
    keeps the fallback: no ``vendor/`` means PATH is the source.
    """
    resolved_system = sys.platform if system is None else system
    is_frozen = _is_frozen() if frozen is None else frozen
    bundle = _bundle_root(is_frozen) if bundle_root is None else Path(bundle_root)
    data = _data_root(is_frozen) if data_root is None else Path(data_root)
    vendor = bundle / "vendor"
    executable_suffix = ".exe" if resolved_system.startswith("win") else ""
    target_key = target or _vendor_target(resolved_system)

    if not is_frozen:
        if not vendor.is_dir():
            return _ok(
                CHECK_BUNDLE,
                "Development fallback: vendor/ is absent; tools resolve from PATH.",
            )
        lock_path = _first_existing(_vendor_lock_candidates(bundle, data))
        models_lock = _first_existing(_models_lock_candidates(bundle, data))
        if lock_path is None or models_lock is None:
            return _ok(
                CHECK_BUNDLE,
                "Development fallback: vendor metadata is incomplete; skipping verification.",
            )
    else:
        if not vendor.is_dir():
            return _fail(
                CHECK_BUNDLE,
                "vendor/ is missing from the installation. Reinstall or re-extract StemForge.",
            )
        lock_path = _first_existing(_vendor_lock_candidates(bundle, data))
        if lock_path is None:
            return _fail(
                CHECK_BUNDLE,
                "vendor.lock.json is missing from the installation. "
                "Reinstall or re-extract StemForge.",
            )
        models_lock = _first_existing(_models_lock_candidates(bundle, data))
        if models_lock is None:
            return _fail(CHECK_BUNDLE, "models.lock.json is missing or unreadable.")

    payload = _read_lock_payload(lock_path)
    expected_versions = _versions_from_payload(payload) if payload is not None else None
    if expected_versions is None:
        if is_frozen:
            return _fail(
                CHECK_BUNDLE,
                "vendor.lock.json is unreadable. Reinstall or re-extract StemForge.",
            )
        return _ok(
            CHECK_BUNDLE,
            "Development fallback: vendor.lock.json is unreadable; skipping verification.",
        )
    if not _readable(models_lock):
        return _fail(CHECK_BUNDLE, "models.lock.json is missing or unreadable.")

    problems: list[str] = []
    declared = _lock_installs(payload, target_key) if payload is not None else {}
    if declared:
        problems.extend(
            _verify_declared_files(
                vendor,
                declared,
                expected_versions,
                resolved_system=resolved_system,
                version_of=version_of,
            )
        )
    else:
        problems.extend(
            _verify_fallback_layout(
                vendor,
                expected_versions,
                executable_suffix=executable_suffix,
                resolved_system=resolved_system,
                version_of=version_of,
            )
        )

    if problems:
        return _fail(
            CHECK_BUNDLE,
            "; ".join(problems) + ". Reinstall or re-extract StemForge.",
        )

    return _ok(
        CHECK_BUNDLE,
        "ffmpeg, ffprobe, rubberband, and deno match vendor.lock.json; "
        "models.lock.json is readable.",
    )


def _vendor_target(system: str) -> str | None:
    if system.startswith("win"):
        return "windows-x64"
    if system.startswith("linux"):
        return "linux-x86_64"
    return None


def _lock_installs(payload: object, target: str | None) -> dict[str, list[tuple[Path, bool]]]:
    """The vendor-relative files the lock declares for one target."""
    if not isinstance(payload, dict) or target is None:
        return {}
    components = payload.get("components")
    if not isinstance(components, dict):
        return {}

    declared: dict[str, list[tuple[Path, bool]]] = {}
    for name, record in components.items():
        if not isinstance(name, str) or not isinstance(record, dict):
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
        files: list[tuple[Path, bool]] = []
        for item in install:
            if not isinstance(item, dict):
                continue
            relative = item.get("to")
            if not isinstance(relative, str) or not relative:
                continue
            path = Path(relative)
            if path.is_absolute() or ".." in path.parts:
                continue
            files.append((path, item.get("executable") is True))
        if files:
            declared[name] = files
    return declared


def _verify_declared_files(
    vendor: Path,
    declared: dict[str, list[tuple[Path, bool]]],
    expected_versions: dict[str, str],
    *,
    resolved_system: str,
    version_of: Callable[[Path, str], str | None] | None,
) -> list[str]:
    problems: list[str] = []
    present: list[Path] = []

    for tool, files in declared.items():
        for relative, executable in files:
            path = vendor / relative
            present.append(relative)
            if not path.is_file():
                problems.append(f"missing: vendor/{relative.as_posix()}")
                continue
            if executable and not _is_executable(path, resolved_system):
                problems.append(f"not executable: vendor/{relative.as_posix()}")
                continue
            if not executable or tool not in VENDOR_TOOLS:
                continue
            output = (version_of or _tool_version)(path, tool)
            expected = expected_versions.get(tool)
            if output is None:
                problems.append(f"--version failed: {tool}")
            elif expected is not None and not _version_matches(output, expected):
                problems.append(f"version mismatch: {tool}")

    library = shared_library_name("rubberband", system=resolved_system)
    if "rubberband" in declared and library is not None:
        relative = Path("rubberband") / library
        if relative not in present:
            problems.append(f"missing: vendor/{relative.as_posix()}")
    return problems


def _verify_fallback_layout(
    vendor: Path,
    expected_versions: dict[str, str],
    *,
    executable_suffix: str,
    resolved_system: str,
    version_of: Callable[[Path, str], str | None] | None,
) -> list[str]:
    """The fallback layout, used by an older or minimal lock without installs."""
    problems: list[str] = []
    for tool in VENDOR_TOOLS:
        path = vendor / _tool_directory(tool) / f"{tool}{executable_suffix}"
        if not path.is_file():
            problems.append(f"missing: {tool}")
            continue
        if not _is_executable(path, resolved_system):
            problems.append(f"not executable: {tool}")
            continue
        output = (version_of or _tool_version)(path, tool)
        expected = expected_versions.get(tool)
        if output is None:
            problems.append(f"--version failed: {tool}")
        elif expected is not None and not _version_matches(output, expected):
            problems.append(f"version mismatch: {tool}")
    return problems


def shared_library_name(tool: str, *, system: str | None = None) -> str | None:
    """The co-located shared library the lock expects for ``tool``."""
    return binaries.shared_library_name(tool, system=system)


def check_storage(paths: AppPathsLike, *, home_override: str | None = None) -> CheckResult:
    """Create and probe every writable root, honouring ``STEMFORGE_HOME`` (exit 6)."""
    override = os.environ.get("STEMFORGE_HOME") if home_override is None else home_override
    if override:
        home = Path(override).expanduser()
        if home.exists() and not home.is_dir():
            return _fail(CHECK_STORAGE, f"StemForge can't write to {home}.")

    try:
        resolved = paths.ensure()
    except OSError:
        return _fail(
            CHECK_STORAGE,
            f"StemForge can't write to {_storage_failure_path(paths, home_override)}.",
        )

    for root in resolved.roots:
        probe = root / f".stemforge-preflight-{os.getpid()}-{os.urandom(4).hex()}"
        try:
            with probe.open("xb") as handle:
                handle.write(b"stemforge\n")
        except OSError:
            return _fail(CHECK_STORAGE, f"StemForge can't write to {root}.")
        finally:
            with contextlib.suppress(OSError):
                probe.unlink(missing_ok=True)
    return _ok(CHECK_STORAGE, "All application folders are writable.")


def _storage_failure_path(paths: AppPathsLike, override: str | None) -> Path:
    if override:
        return Path(override).expanduser()
    return paths.data


def check_preview(
    *,
    system: str | None = None,
    probe: PreviewProbe | None = None,
) -> CheckResult:
    """Check the GStreamer preview stack; a missing element is degraded (exit 4)."""
    resolved_system = sys.platform if system is None else system
    if resolved_system.startswith("win"):
        return _ok(CHECK_PREVIEW, "Preview is covered by the WebView2 assertion.")
    if not resolved_system.startswith("linux"):
        return _ok(CHECK_PREVIEW, "Preview stack is unknown on this platform; assuming present.")

    facts = _probe_preview() if probe is None else probe
    if facts.missing:
        return CheckResult(
            name=CHECK_PREVIEW,
            status=STATUS_DEGRADED,
            message="GStreamer is missing " + ", ".join(facts.missing) + ".",
            fatal=False,
        )
    if facts.method == "unknown":
        return _ok(
            CHECK_PREVIEW,
            "GStreamer status is unknown; assuming playback is available.",
        )
    return _ok(CHECK_PREVIEW, f"GStreamer elements available via {facts.method}.")


def check_resources(
    paths: AppPathsLike,
    *,
    disk_free: int | None = None,
    total_ram: int | None = None,
) -> CheckResult:
    """Warn under the disk and RAM floors; resources never block startup."""
    free = _disk_free(paths) if disk_free is None else disk_free
    ram = _total_ram_bytes() if total_ram is None else total_ram
    messages: list[str] = []

    if free is not None and free < DISK_WARN_BYTES:
        messages.append(
            f"Only {_gib(free)} GB of disk space is free. A 30-minute track can need up to 7.6 GB."
        )
        if free < DISK_FLOOR_BYTES:
            messages.append("StemForge may not be able to import or separate tracks.")
    if ram is not None and ram < RAM_WARN_BYTES:
        messages.append(
            f"This computer has {_gib(ram)} GB of RAM. "
            "Separation needs about 3 GB free; long tracks may be slow."
        )

    if messages:
        return CheckResult(
            name=CHECK_RESOURCES,
            status=STATUS_WARN,
            message=" ".join(messages),
            fatal=False,
        )
    return _ok(CHECK_RESOURCES, "Disk and memory are above the warning floors.")


def _ok(name: str, message: str) -> CheckResult:
    return CheckResult(name=name, status=STATUS_OK, message=message, fatal=False)


def _fail(name: str, message: str) -> CheckResult:
    return CheckResult(name=name, status=STATUS_FAIL, message=message, fatal=True)


def _glibc_version() -> tuple[int, int] | None:
    raw: str | None
    try:
        raw = os.confstr("CS_GNU_LIBC_VERSION")
    except (AttributeError, OSError, ValueError):
        raw = None
    if not raw:
        libc, version = platform.libc_ver()
        raw = f"{libc} {version}" if version else None
    if not raw:
        return None
    match = re.search(r"(\d+)\.(\d+)", raw)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def _version_text(version: tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in version)


def _gtk_version(gtk: object) -> str:
    major = getattr(gtk, "get_major_version", lambda: 0)()
    minor = getattr(gtk, "get_minor_version", lambda: 0)()
    micro = getattr(gtk, "get_micro_version", lambda: 0)()
    return f"{major}.{minor}.{micro}"


def _probe_linux_window() -> WindowProbe:
    try:
        gi = importlib.import_module("gi")
    except ImportError:
        return WindowProbe(gi=False)

    gtk = _import_typelib(gi, "Gtk", "3.0")
    webkit2 = _import_typelib(gi, "WebKit2", "4.1")
    soup = _import_typelib(gi, "Soup", "3.0")
    if gtk is None or webkit2 is None or soup is None:
        return WindowProbe(
            gi=True,
            gtk=None if gtk is None else _gtk_version(gtk),
            webkit=None if webkit2 is None else "4.1",
            soup=None if soup is None else "3.0",
            error="one or more GTK/WebKitGTK typelibs are missing",
        )

    try:
        webkit_version = (
            webkit2.get_major_version(),
            webkit2.get_minor_version(),
            webkit2.get_micro_version(),
        )
    except AttributeError:
        webkit_version = None
    return WindowProbe(
        gi=True,
        gtk=_gtk_version(gtk),
        webkit="4.1",
        soup="3.0",
        webkit_version=webkit_version,
    )


def _import_typelib(gi: object, namespace: str, version: str) -> Any:
    try:
        gi.require_version(namespace, version)  # type: ignore[attr-defined]
        return importlib.import_module(f"gi.repository.{namespace}")
    except (AttributeError, ImportError, OSError, ValueError):
        return None


def _probe_windows_window() -> WindowProbe:
    try:
        winreg = importlib.import_module("winreg")
    except ImportError:
        return WindowProbe()

    pv: str | None = None
    for root, subkey in (
        (
            winreg.HKEY_CURRENT_USER,
            rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_ID}",
        ),
        (
            winreg.HKEY_LOCAL_MACHINE,
            rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_ID}",
        ),
        (
            winreg.HKEY_LOCAL_MACHINE,
            rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_ID}",
        ),
    ):
        try:
            with winreg.OpenKey(root, subkey) as key:
                value, _kind = winreg.QueryValueEx(key, "pv")
                if value:
                    pv = str(value)
                    break
        except OSError:
            continue

    dotnet: int | None = None
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for subkey in (
            r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full",
            r"SOFTWARE\WOW6432Node\Microsoft\NET Framework Setup\NDP\v4\Full",
        ):
            try:
                with winreg.OpenKey(root, subkey) as key:
                    value, _kind = winreg.QueryValueEx(key, "Release")
                    dotnet = int(value)
                    break
            except (OSError, ValueError, TypeError):
                continue
        if dotnet is not None:
            break

    return WindowProbe(webview2=pv, dotnet_release=dotnet)


def _probe_preview() -> PreviewProbe:
    try:
        gi = importlib.import_module("gi")
        gi.require_version("Gst", "1.0")
        gst = importlib.import_module("gi.repository.Gst")
        # PyGObject 3.50 rejects None here; an empty argv is what Gst.init
        # expects from a binding call.
        gst.init([])
        missing_elements = tuple(
            element for element in PREVIEW_ELEMENTS if gst.ElementFactory.find(element) is None
        )
        return PreviewProbe(
            method="gst-python",
            present=tuple(e for e in PREVIEW_ELEMENTS if e not in missing_elements),
            missing=missing_elements,
        )
    except (AttributeError, ImportError, OSError, RuntimeError, ValueError):
        pass

    inspector = shutil.which("gst-inspect-1.0")
    if inspector is None:
        return PreviewProbe(method="unknown")
    present: list[str] = []
    missing: list[str] = []
    for element in PREVIEW_ELEMENTS:
        try:
            completed = subprocess.run(
                [inspector, element],
                capture_output=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return PreviewProbe(method="unknown")
        (present if completed.returncode == 0 else missing).append(element)
    return PreviewProbe(method="gst-inspect", present=tuple(present), missing=tuple(missing))


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _bundle_root(frozen: bool) -> Path:
    if frozen:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


def _data_root(frozen: bool) -> Path:
    if frozen:
        internal = getattr(sys, "_MEIPASS", None)
        return Path(internal) if internal is not None else _bundle_root(True)
    return _bundle_root(False)


def _vendor_lock_candidates(bundle: Path, data: Path) -> tuple[Path, ...]:
    return (
        data / "vendor.lock.json",
        data / "packaging" / "vendor.lock.json",
        bundle / "vendor.lock.json",
        bundle / "packaging" / "vendor.lock.json",
    )


def _models_lock_candidates(bundle: Path, data: Path) -> tuple[Path, ...]:
    return (
        data / "models" / "models.lock.json",
        data / "packaging" / "models" / "models.lock.json",
        bundle / "models" / "models.lock.json",
        bundle / "packaging" / "models" / "models.lock.json",
        # Development checkout: ``resources.data_root()`` is the package
        # directory, so the lock the app reads lives under ``src/stemforge``.
        bundle / "src" / "stemforge" / "models" / "models.lock.json",
    )


def _first_existing(candidates: Sequence[Path]) -> Path | None:
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def read_vendor_lock_versions(path: Path) -> dict[str, str] | None:
    """The tool versions recorded in ``vendor.lock.json``, or ``None``.

    Accepts the ``components.<tool>.version`` schema and the older
    skeleton spellings (``versions`` and loose ``name``/``tool``/``id`` records)
    so a stale development lock cannot crash the preflight.
    """
    payload = _read_lock_payload(path)
    if payload is None:
        return None
    return _versions_from_payload(payload)


def _read_lock_payload(path: Path) -> object | None:
    try:
        payload: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return payload


def _versions_from_payload(payload: object) -> dict[str, str] | None:
    if not isinstance(payload, (dict, list)):
        return None

    containers: list[object] = [payload]
    if isinstance(payload, dict):
        for key in ("components", "versions"):
            nested = payload.get(key)
            if isinstance(nested, (dict, list)):
                containers.append(nested)
    elif isinstance(payload, list):
        containers.append(payload)

    versions: dict[str, str] = {}
    for tool in VENDOR_TOOLS:
        found = _version_in_containers(tool, containers)
        if found is not None:
            versions[tool] = found
    return versions


def _version_in_containers(tool: str, containers: Sequence[object]) -> str | None:
    for container in containers:
        if isinstance(container, dict):
            entry = container.get(tool)
            if isinstance(entry, dict):
                value = entry.get("version")
                if isinstance(value, str):
                    return value
            elif isinstance(entry, str):
                return entry
            name = container.get("name") or container.get("tool") or container.get("id")
            value = container.get("version")
            if name == tool and isinstance(value, str):
                return value
        elif isinstance(container, list):
            for item in container:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("tool") or item.get("id")
                    value = item.get("version")
                    if name == tool and isinstance(value, str):
                        return value
    return None


def _readable(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return True


def _tool_directory(tool: str) -> str:
    if tool in {"ffmpeg", "ffprobe"}:
        return "ffmpeg"
    return tool


def _is_executable(path: Path, system: str) -> bool:
    """Windows has no executable bit; the vendor set is ``.exe``-only there."""
    if system.startswith("win"):
        return path.suffix.lower() == ".exe"
    return os.access(path, os.X_OK)


def _tool_version(path: Path, tool: str) -> str | None:
    flag = "-version" if tool in {"ffmpeg", "ffprobe"} else "--version"
    try:
        completed = subprocess.run(
            [str(path), flag],
            capture_output=True,
            check=False,
            text=True,
            errors="replace",
            timeout=10,
            env=binaries.tool_environment(path),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout or completed.stderr


def _version_matches(output: str, expected: str) -> bool:
    normalized = expected.removeprefix("n")
    return normalized in output or expected in output


def _disk_free(paths: AppPathsLike) -> int | None:
    try:
        return shutil.disk_usage(_existing_ancestor(paths.cache)).free
    except OSError:
        return None


def _existing_ancestor(path: Path) -> Path:
    candidate = Path(path)
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def total_ram_bytes() -> int | None:
    """The machine's total physical RAM, for the diagnostics report."""
    return _total_ram_bytes()


def _total_ram_bytes() -> int | None:
    if sys.platform.startswith("win"):
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
            return int(status.ullTotalPhys)
        return None

    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        pages = os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, OSError, ValueError):
        return None
    if not isinstance(page_size, int) or not isinstance(pages, int):
        return None
    return page_size * pages


def _gib(value: int) -> str:
    return f"{value / GIB:.1f}"


def _show_native_message(
    title: str,
    body: str,
    copy_text: str | None,
    *,
    copy_label: str,
    ok_label: str,
    backend: MessageBackend | None = None,
) -> None:
    if backend is not None:
        backend(title, body, copy_text)
        return
    if sys.platform.startswith("win"):
        shown = False
        with contextlib.suppress(Exception):
            _windows_message_box(title, body)
            shown = True
        if shown:
            return
    if _gtk_message_dialog(title, body, copy_text, copy_label=copy_label, ok_label=ok_label):
        return
    _stderr_message(title, body)


def show_fatal_message(message: FatalMessage, *, backend: MessageBackend | None = None) -> None:
    """Show one fatal preflight result through the native or stderr backend."""
    _show_native_message(
        message.title,
        message.body,
        message.copy_text,
        copy_label=message.chrome.copy_details_label,
        ok_label=message.chrome.ok_label,
        backend=backend,
    )


def show_already_running(*, chrome: NativeChrome, backend: MessageBackend | None = None) -> None:
    """Show the native single-instance message and return; the caller exits 0."""
    text = chrome
    _show_native_message(
        text.already_running_title,
        text.already_running_body,
        None,
        copy_label=text.copy_details_label,
        ok_label=text.ok_label,
        backend=backend,
    )


def _stderr_message(title: str, body: str) -> None:
    print(title, file=sys.stderr)
    print(body, file=sys.stderr)


def _gtk_message_dialog(
    title: str,
    body: str,
    copy_text: str | None,
    *,
    copy_label: str,
    ok_label: str,
) -> bool:
    try:
        gi = importlib.import_module("gi")
        gi.require_version("Gtk", "3.0")
        gtk = importlib.import_module("gi.repository.Gtk")
        gdk = importlib.import_module("gi.repository.Gdk")
    except (AttributeError, ImportError, OSError, RuntimeError, ValueError):
        return False
    try:
        initialized, _argv = gtk.init_check()
    except Exception:
        initialized = False
    if not initialized:
        return False

    dialog = gtk.MessageDialog(
        message_type=gtk.MessageType.ERROR,
        buttons=gtk.ButtonsType.NONE,
        text=title,
    )
    try:
        dialog.format_secondary_text(body)
        copy_response = 1
        if copy_text:
            dialog.add_button(copy_label, copy_response)
        dialog.add_button(ok_label, gtk.ResponseType.OK)
        dialog.set_default_response(gtk.ResponseType.OK)
        while True:
            response = dialog.run()
            if response == copy_response and copy_text:
                clipboard = gtk.Clipboard.get(gdk.SELECTION_CLIPBOARD)
                clipboard.set_text(copy_text, -1)
                clipboard.store()
                continue
            break
    except Exception:
        return False
    finally:
        dialog.destroy()
    return True


def _windows_message_box(title: str, body: str) -> None:
    ctypes = importlib.import_module("ctypes")

    mb_iconerror = 0x00000010
    mb_ok = 0x00000000
    ctypes.windll.user32.MessageBoxW(None, body, title, mb_iconerror | mb_ok)
