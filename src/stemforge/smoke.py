"""The frozen-build test flags: ``--self-test``, ``--smoke-test``, ``--smoke-separation``.

Each flag builds a JSON report (ticket 10 §7), prints it to stdout, optionally
writes it to ``--report <path>``, and returns the exit code: ``0`` when every
required check passed, ``1`` otherwise.

``--self-test`` resolves the environment, imports the GUI stack, reads the
read-only data, executes whatever vendor binaries exist, and asserts the CPU
torch build *if* torch is installed. ``--smoke-test`` runs the same checks, then
boots the NiceGUI server headlessly and polls ``/`` for HTTP 200.
``--smoke-separation`` is an honest stub until tickets 13 and 20 land the
worker and the model weights.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Final

from stemforge import __version__
from stemforge.platform import binaries, resources
from stemforge.platform.environment import configure
from stemforge.platform.paths import AppPaths

REPORT_SCHEMA: Final = 1
SMOKE_TIMEOUT_SECONDS: Final = 30.0
REQUIRE_VENDOR_ENV_VAR: Final = "STEMFORGE_REQUIRE_VENDOR"
SERVER_SHUTDOWN_TIMEOUT_SECONDS: Final = 10.0

REQUIRED_IMPORTS: Final = (("import.nicegui", "nicegui"), ("import.pywebview", "webview"))
ASSET_FILES: Final = ("stemforge.ico", "stemforge.svg", "stemforge-512.png")
VENDOR_BINARIES: Final = (
    ("vendor.ffmpeg", binaries.ffmpeg),
    ("vendor.ffprobe", binaries.ffprobe),
    ("vendor.rubberband", binaries.rubberband),
    ("vendor.deno", binaries.deno),
)

Check = dict[str, Any]
Report = dict[str, Any]


def _check(
    checks: list[Check],
    name: str,
    status: str,
    detail: str = "",
    *,
    required: bool = True,
) -> None:
    checks.append({"name": name, "status": status, "detail": detail, "required": required})


def _first_line(text: str, *, limit: int = 200) -> str:
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    line = lines[0] if lines else ""
    return line if len(line) <= limit else f"{line[: limit - 3]}..."


def _require_vendor() -> bool:
    return os.environ.get(REQUIRE_VENDOR_ENV_VAR, "").strip().lower() in {"1", "true", "yes"}


def _check_paths(checks: list[Check]) -> AppPaths:
    paths = configure()
    for name, path in (
        ("paths.data", paths.data),
        ("paths.cache", paths.cache),
        ("paths.logs", paths.logs),
        ("paths.models", paths.models),
        ("paths.config", paths.config),
        ("paths.webview", paths.webview),
    ):
        status = "ok" if path.is_dir() else "failed"
        _check(checks, name, status, str(path))
    return paths


def _check_imports(checks: list[Check]) -> None:
    for name, module_name in REQUIRED_IMPORTS:
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            _check(checks, name, "failed", f"{type(exc).__name__}: {exc}")
            continue
        version = str(getattr(module, "__version__", "") or "imported")
        _check(checks, name, "ok", version)


def _check_torch(checks: list[Check]) -> None:
    if importlib.util.find_spec("torch") is None:
        _check(
            checks,
            "import.torch",
            "missing",
            "torch is not installed in this build",
            required=False,
        )
        return
    try:
        torch = importlib.import_module("torch")
    except Exception as exc:
        _check(checks, "import.torch", "failed", f"{type(exc).__name__}: {exc}")
        return
    version = str(torch.__version__)
    if version.endswith("+cpu"):
        _check(checks, "import.torch", "ok", version)
    else:
        _check(checks, "import.torch", "failed", f"{version} is not a +cpu build")


def _check_data_root(checks: list[Check]) -> None:
    data_root = resources.data_root()
    _check(checks, "data.root", "ok" if data_root.is_dir() else "failed", str(data_root))

    assets = resources.assets_dir()
    missing = [name for name in ASSET_FILES if not (assets / name).is_file()]
    if not assets.is_dir():
        _check(checks, "assets", "failed", f"{assets} is missing")
    elif missing:
        _check(checks, "assets", "failed", f"{assets} is missing {', '.join(missing)}")
    else:
        _check(checks, "assets", "ok", str(assets))

    lock = resources.models_lock_path()
    try:
        payload = json.loads(lock.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _check(checks, "models.lock", "failed", f"{lock}: {type(exc).__name__}: {exc}")
    else:
        if isinstance(payload, dict) and isinstance(payload.get("models"), dict):
            _check(checks, "models.lock", "ok", str(lock))
        else:
            _check(checks, "models.lock", "failed", f"{lock} has no models record")


def _check_bundle_root(checks: list[Check], *, require_vendor: bool) -> None:
    if resources.is_frozen():
        vendor_dir = resources.vendor_dir()
        _check(
            checks,
            "vendor.root",
            "ok" if vendor_dir.is_dir() else "failed",
            str(vendor_dir),
        )
    else:
        _check(checks, "vendor.root", "skipped", "development checkout", required=False)

    for name, resolver in VENDOR_BINARIES:
        try:
            path = resolver()
        except binaries.BundleDamagedError as exc:
            # Ticket 4's skeleton Bundle has no vendor binaries yet; ticket 19
            # flips REQUIRE_VENDOR=1 and turns this into a failure.
            _check(
                checks,
                name,
                "failed" if require_vendor else "missing",
                str(exc),
                required=require_vendor,
            )
            continue
        if path is None:
            _check(
                checks,
                name,
                "failed" if require_vendor else "missing",
                "not found in vendor/ or PATH",
                required=require_vendor,
            )
            continue
        # A PATH stand-in is a development convenience: a system tool that will
        # not run is reported as missing, not as a broken Bundle. A frozen build
        # (or REQUIRE_VENDOR) treats it as fatal, as ticket 14's preflight does.
        strict = resources.is_frozen() or require_vendor
        try:
            completed = subprocess.run(
                [str(path), "--version"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            _check(
                checks,
                name,
                "failed" if strict else "missing",
                f"{path}: {type(exc).__name__}: {exc}",
                required=strict,
            )
            continue
        if completed.returncode == 0:
            _check(checks, name, "ok", f"{path}: {_first_line(completed.stdout)}")
        else:
            _check(
                checks,
                name,
                "failed" if strict else "missing",
                f"{path}: exit {completed.returncode}: {_first_line(completed.stderr)}",
                required=strict,
            )


def _check_workspace(checks: list[Check], paths: AppPaths) -> None:
    from stemforge.application import WorkspaceService
    from stemforge.logging_setup import configure_logging
    from stemforge.settings import SettingsStore

    settings = SettingsStore(paths)
    configure_logging(paths)
    settings.save()
    service = WorkspaceService(settings, paths=paths)
    snapshot = service.snapshot()
    logging.getLogger("stemforge").info(
        "self-test: data=%s cache=%s logs=%s models=%s config=%s",
        paths.data,
        paths.cache,
        paths.logs,
        paths.models,
        paths.config,
    )
    logging.getLogger("stemforge").info("self-test: workspace=%d tracks", len(snapshot))
    _check(checks, "workspace", "ok", f"{len(snapshot)} tracks")


def _check_runtime_hook(checks: list[Check]) -> None:
    if resources.is_frozen():
        active = bool(getattr(sys, "stemforge_frozen_env_guard", False))
        _check(
            checks,
            "runtime.env_guard",
            "ok" if active else "failed",
            "NiceGUI/Demucs import guard active"
            if active
            else "the PyInstaller runtime hook did not run",
        )
    else:
        _check(checks, "runtime.env_guard", "skipped", "development checkout", required=False)


def run_self_test() -> Report:
    """Run every headless check and return the JSON-able report."""
    checks: list[Check] = []
    require_vendor = _require_vendor()

    paths = _check_paths(checks)
    _check_data_root(checks)
    _check_runtime_hook(checks)
    _check_imports(checks)
    _check_torch(checks)
    _check_bundle_root(checks, require_vendor=require_vendor)
    _check_workspace(checks, paths)

    return _finalize(
        {
            "schema": REPORT_SCHEMA,
            "check": "self-test",
            "app": "stemforge",
            "version": __version__,
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "frozen": resources.is_frozen(),
            "executable": str(Path(sys.executable)),
            "data_root": str(resources.data_root()),
            "bundle_root": str(resources.bundle_root()),
            "checks": checks,
        }
    )


def _summary(checks: list[Check]) -> dict[str, int]:
    summary = {"ok": 0, "missing": 0, "failed": 0, "skipped": 0}
    for check in checks:
        status = str(check["status"])
        summary[status] = summary.get(status, 0) + 1
    return summary


def _finalize(report: Report) -> Report:
    checks: list[Check] = report["checks"]
    report["summary"] = _summary(checks)
    report["ok"] = not any(check["status"] == "failed" for check in checks)
    return report


def _error_report(check_name: str, exc: BaseException) -> Report:
    detail = f"{type(exc).__name__}: {exc}"
    return {
        "schema": REPORT_SCHEMA,
        "check": check_name,
        "app": "stemforge",
        "version": __version__,
        "frozen": resources.is_frozen(),
        "checks": [{"name": check_name, "status": "failed", "detail": detail, "required": True}],
        "summary": {"ok": 0, "missing": 0, "failed": 1, "skipped": 0},
        "ok": False,
        "error": detail,
    }


def _emit(report: Report, report_path: Path | None) -> bool:
    """Print the report and optionally write it; ``False`` when the file write failed."""
    text = json.dumps(report, indent=2, sort_keys=True)
    print(text)
    if report_path is None:
        return True
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(f"{text}\n", encoding="utf-8")
    except OSError as exc:
        print(f"stemforge: cannot write {report_path}: {exc}", file=sys.stderr)
        return False
    return True


def self_test(*, report: Path | None = None) -> int:
    """``--self-test``: the headless runtime contract, exit 0/1."""
    try:
        result = run_self_test()
    except Exception as exc:
        result = _error_report("self-test", exc)
    written = _emit(result, report)
    return 0 if result["ok"] and written else 1


def _open_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _serve_and_poll(timeout: float) -> dict[str, Any]:
    # ``--smoke-test`` boots a real server, but a test runner may have spawned
    # this process; NiceGUI's pytest mode would otherwise demand its
    # User-simulation port instead of the one we open below.
    os.environ.pop("PYTEST_CURRENT_TEST", None)

    from nicegui import ui
    from nicegui.server import Server

    importlib.import_module("stemforge.ui.page")  # registers the "/" route

    port = _open_port()
    url = f"http://127.0.0.1:{port}/"
    errors: list[BaseException] = []
    started = time.monotonic()

    def serve() -> None:
        try:
            ui.run(
                native=False,
                show=False,
                reload=False,
                host="127.0.0.1",
                port=port,
                title="StemForge",
                fastapi_docs=False,
                endpoint_documentation="none",
                show_welcome_message=False,
                uvicorn_logging_level="warning",
            )
        except SystemExit as exc:
            if exc.code not in (0, None):
                errors.append(exc)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=serve, name="stemforge-smoke-server", daemon=True)
    thread.start()
    status: int | None = None
    body = ""
    detail = ""
    try:
        deadline = started + timeout
        while time.monotonic() < deadline:
            if errors:
                break
            try:
                with urllib.request.urlopen(url, timeout=2) as response:
                    status = int(response.status)
                    body = response.read(65536).decode("utf-8", "replace")
                detail = ""
                break
            except urllib.error.HTTPError as exc:
                status = int(exc.code)
                detail = f"HTTP {exc.code}"
                break
            except (urllib.error.URLError, OSError) as exc:
                detail = str(exc)
                time.sleep(0.1)
    finally:
        instance = getattr(Server, "instance", None)
        if instance is not None:
            instance.should_exit = True
        thread.join(timeout=SERVER_SHUTDOWN_TIMEOUT_SECONDS)

    seconds = round(time.monotonic() - started, 3)
    if errors:
        detail = f"{type(errors[0]).__name__}: {errors[0]}"
    if thread.is_alive():
        detail = "the smoke server did not stop within 10 s"
        status = None
    if status == 200 and "StemForge" not in body:
        detail = "HTTP 200 but the page did not mention StemForge"
        status = None
    return {"url": url, "port": port, "status": status, "seconds": seconds, "detail": detail}


def smoke_test(*, report: Path | None = None) -> int:
    """``--smoke-test``: ``--self-test`` plus a headless HTTP 200 on ``/``."""
    try:
        result = run_self_test()
        result["check"] = "smoke-test"
        server = _serve_and_poll(SMOKE_TIMEOUT_SECONDS)
        result["server"] = server
        _check(
            result["checks"],
            "server.http",
            "ok" if server["status"] == 200 else "failed",
            f"HTTP {server['status']} from {server['url']} in {server['seconds']} s"
            + (f" ({server['detail']})" if server["detail"] else ""),
        )
        result = _finalize(result)
    except Exception as exc:
        result = _error_report("smoke-test", exc)
    written = _emit(result, report)
    return 0 if result["ok"] and written else 1


def smoke_separation(*, report: Path | None = None) -> int:
    """``--smoke-separation``: an honest stub until the worker and weights land."""
    message = "not available in this build"
    result: Report = {
        "schema": REPORT_SCHEMA,
        "check": "smoke-separation",
        "app": "stemforge",
        "version": __version__,
        "frozen": resources.is_frozen(),
        "message": message,
        "checks": [
            {
                "name": "separation.worker",
                "status": "failed",
                "detail": message,
                "required": True,
            }
        ],
        "summary": {"ok": 0, "missing": 0, "failed": 1, "skipped": 0},
        "ok": False,
    }
    print(f"stemforge: --smoke-separation is {message}", file=sys.stderr)
    written = _emit(result, report)
    return 0 if result["ok"] and written else 1
