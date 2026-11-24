"""The frozen-build test flags: ``--self-test``, ``--smoke-test``, and the smokes.

Each flag builds a JSON report, prints it to stdout, optionally
writes it to ``--report <path>``, and returns the exit code: ``0`` when every
required check passed, ``1`` otherwise.

``--self-test`` resolves the environment, imports the GUI stack, reads the
read-only data, executes whatever vendor binaries exist, and asserts the CPU
torch build *if* torch is installed. ``--smoke-test`` runs the same checks, then
boots the NiceGUI server headlessly and polls ``/`` for HTTP 200.
``--smoke-separation`` runs a one-second synthetic source through the real
Separation worker using the installed weights; it never downloads, and it is not
part of ``--smoke-test``. ``--smoke-karaoke`` runs the same worker on a
centre-only synthetic source with an empty model root; it needs no weights, no
network, and no FFmpeg, and proves the no-model release path.
"""

from __future__ import annotations

import array
import importlib
import importlib.util
import json
import logging
import math
import os
import socket
import struct
import subprocess
import sys
import tempfile
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
SMOKE_SEPARATION_TIMEOUT_SECONDS: Final = 60.0
SMOKE_SEPARATION_RSS_BYTES: Final = 2 * 1024 * 1024 * 1024
SMOKE_SEPARATION_SECONDS: Final = 1.0
SMOKE_KARAOKE_TIMEOUT_SECONDS: Final = 30.0
SMOKE_KARAOKE_RSS_BYTES: Final = 1024 * 1024 * 1024
# Centre-only input leaves the side channel at zero; a small tolerance keeps
# the check about the method's behaviour rather than float bit patterns.
SMOKE_KARAOKE_VOCALS_MIN_PEAK: Final = 0.01
SMOKE_KARAOKE_INSTRUMENTAL_MAX_PEAK: Final = 1e-4
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
# FFmpeg's long form is ``-version``; the other two tools accept ``--version``.
# Preflight and the vendor lock use the same flags.
VENDOR_VERSION_FLAGS: Final = {
    "ffmpeg": "-version",
    "ffprobe": "-version",
    "rubberband": "--version",
    "deno": "--version",
}

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
        except OSError as exc:
            # A development checkout may have no vendor binaries; missing ones
            # are reported as ``missing`` unless ``STEMFORGE_REQUIRE_VENDOR`` is set,
            # which makes them failures. A tool missing from both vendor/ and
            # PATH also lands here.
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
        # (or REQUIRE_VENDOR) treats it as fatal, as the preflight does.
        strict = resources.is_frozen() or require_vendor
        tool = name.removeprefix("vendor.")
        flag = VENDOR_VERSION_FLAGS.get(tool, "--version")
        try:
            completed = subprocess.run(
                [str(path), flag],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
                env=binaries.tool_environment(path),
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
            first = _first_line(completed.stdout or completed.stderr)
            _check(checks, name, "ok", f"{path}: {first}")
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
    """``--smoke-separation``: a real one-second separation with cached weights.

    The weights are never downloaded here: a missing or tampered cache fails with
    the exact ``--install-model --accept-model-licence`` remedy. This flag is
    kept out of ``--smoke-test``; nightly and release call it explicitly.
    """
    try:
        result = _run_smoke_separation()
    except Exception as exc:
        result = _error_report("smoke-separation", exc)
    written = _emit(result, report)
    return 0 if result["ok"] and written else 1


def smoke_karaoke(*, report: Path | None = None) -> int:
    """``--smoke-karaoke``: a real one-second centre-channel separation.

    The worker gets an empty model root, no model path, and no FFmpeg: Karaoke
    needs no weights, no network, and no vendor media tools. This flag is kept
    out of ``--smoke-test``; the release and nightly gates call it explicitly.
    """
    try:
        result = _run_smoke_karaoke()
    except Exception as exc:
        result = _error_report("smoke-karaoke", exc)
    written = _emit(result, report)
    return 0 if result["ok"] and written else 1


def _run_smoke_separation() -> Report:
    from stemforge.domain.cancellation import CancelToken
    from stemforge.engines.separation import WorkerSeparationEngine
    from stemforge.models import ModelStore
    from stemforge.worker_protocol import METHOD_OUTPUT_ROLES

    checks: list[Check] = []
    paths = _check_paths(checks)
    store = ModelStore(paths)
    status = store.verify()
    spec = store.spec
    model_record = {
        "name": spec.name,
        "revision": spec.revision,
        "filename": spec.filename,
        "size": spec.size_bytes,
        "sha256": spec.sha256,
    }
    if not status.verified:
        reason = status.reason or "absent"
        detail = (
            f"the {spec.name} weights are not installed (reason={reason}); "
            "run stemforge --install-model --accept-model-licence"
        )
        _check(checks, "model.weights", "failed", detail)
        print(f"stemforge: --smoke-separation needs the model: {detail}", file=sys.stderr)
        return _finalize(
            {
                "schema": REPORT_SCHEMA,
                "check": "smoke-separation",
                "app": "stemforge",
                "version": __version__,
                "frozen": resources.is_frozen(),
                "platform": sys.platform,
                "model": model_record,
                "message": detail,
                "checks": checks,
            }
        )
    _check(checks, "model.weights", "ok", f"{spec.name} {spec.sha256}")

    cache_tmp = paths.cache / "tmp"
    cache_tmp.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp_root = tempfile.mkdtemp(prefix="smoke-separation-", dir=cache_tmp)
    tmp = Path(tmp_root)
    source = tmp / "source.wav"
    out_dir = tmp / "stems"
    _write_smoke_wav(source)
    threads = max(1, (os.cpu_count() or 2) // 2)
    sampler = _ChildRssSampler()
    engine = WorkerSeparationEngine()
    progress: list[float] = []
    started = time.monotonic()
    import shutil

    output_roles = METHOD_OUTPUT_ROLES["demucs"]
    try:
        try:
            outputs = engine.separate(
                source,
                out_dir,
                method="demucs",
                expected_outputs=output_roles,
                model_path=status.path,
                expected_duration_s=SMOKE_SEPARATION_SECONDS,
                threads=threads,
                model_revision=spec.revision,
                env=_smoke_worker_environment(paths, threads=threads),
                progress=lambda fraction, _stage, _detail: progress.append(fraction),
                cancel=CancelToken(),
                log=logging.getLogger("stemforge.smoke"),
                timeout_s=SMOKE_SEPARATION_TIMEOUT_SECONDS,
                on_spawn=sampler.start,
            )
            wall_seconds = time.monotonic() - started
        except Exception as exc:
            wall_seconds = time.monotonic() - started
            code = getattr(exc, "code", "")
            detail = getattr(exc, "detail", None) or str(exc)
            _check(
                checks,
                "separation.worker",
                "failed",
                f"{code or type(exc).__name__}: {detail}",
            )
            return _finalize(
                {
                    "schema": REPORT_SCHEMA,
                    "check": "smoke-separation",
                    "app": "stemforge",
                    "version": __version__,
                    "frozen": resources.is_frozen(),
                    "platform": sys.platform,
                    "model": model_record,
                    "wall_seconds": round(wall_seconds, 3),
                    "threads": threads,
                    "checks": checks,
                }
            )
        peak_rss = sampler.stop()

        _check(
            checks,
            "separation.worker",
            "ok",
            f"five outputs in {wall_seconds:.2f} s",
        )
        problems: list[str] = []
        for stem in output_roles:
            path = outputs.path_for(stem)
            if path is None or not path.is_file():
                problems.append(f"{stem}=missing")
                continue
            facts = _wav_facts(path)
            if facts is None:
                problems.append(f"{stem}=unreadable")
                continue
            tag, channels, rate, duration = facts
            if tag != 3 or channels != 2 or rate != 44_100:
                problems.append(f"{stem}={rate}Hz/{channels}ch/tag{tag}")
            elif abs(duration - SMOKE_SEPARATION_SECONDS) > 0.050:
                problems.append(f"{stem}=duration {duration:.3f}s")
        _check(
            checks,
            "separation.outputs",
            "failed" if problems else "ok",
            ", ".join(problems) if problems else "five float32 44.1 kHz stereo outputs",
        )
        _check(
            checks,
            "separation.time",
            "ok" if wall_seconds < SMOKE_SEPARATION_TIMEOUT_SECONDS else "failed",
            f"{wall_seconds:.2f} s (limit {SMOKE_SEPARATION_TIMEOUT_SECONDS:g} s)",
        )
        rss = peak_rss or 0
        _check(
            checks,
            "separation.rss",
            "ok" if 0 < rss < SMOKE_SEPARATION_RSS_BYTES else "failed",
            f"{rss / (1024 * 1024):.0f} MiB (limit 2048 MiB)",
        )
        _check(
            checks,
            "separation.progress",
            "ok" if progress and max(progress) <= 1.0 and progress[-1] > 0 else "failed",
            f"{len(progress)} events, last {progress[-1]:.3f}"
            if progress
            else "no progress events",
        )
        return _finalize(
            {
                "schema": REPORT_SCHEMA,
                "check": "smoke-separation",
                "app": "stemforge",
                "version": __version__,
                "frozen": resources.is_frozen(),
                "platform": sys.platform,
                "model": model_record,
                "wall_seconds": round(wall_seconds, 3),
                "peak_rss_bytes": rss,
                "threads": threads,
                "outputs": list(output_roles),
                "checks": checks,
            }
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run_smoke_karaoke() -> Report:
    from stemforge.domain.cancellation import CancelToken
    from stemforge.engines.separation import WorkerSeparationEngine
    from stemforge.worker_protocol import METHOD_OUTPUT_ROLES

    checks: list[Check] = []
    paths = _check_paths(checks)
    output_roles = METHOD_OUTPUT_ROLES["karaoke"]
    cache_tmp = paths.cache / "tmp"
    cache_tmp.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp_root = tempfile.mkdtemp(prefix="smoke-karaoke-", dir=cache_tmp)
    tmp = Path(tmp_root)
    source = tmp / "source.wav"
    out_dir = tmp / "karaoke"
    model_root = tmp / "models"
    model_root.mkdir()
    _write_smoke_wav(source)
    threads = max(1, (os.cpu_count() or 2) // 2)
    sampler = _ChildRssSampler()
    engine = WorkerSeparationEngine()
    progress: list[float] = []
    started = time.monotonic()
    import shutil

    report: Report = {
        "schema": REPORT_SCHEMA,
        "check": "smoke-karaoke",
        "app": "stemforge",
        "version": __version__,
        "frozen": resources.is_frozen(),
        "platform": sys.platform,
        "model_required": False,
        "model_path": None,
        "threads": threads,
    }
    try:
        try:
            outputs = engine.separate(
                source,
                out_dir,
                method="karaoke",
                expected_outputs=output_roles,
                model_path=None,
                expected_duration_s=SMOKE_SEPARATION_SECONDS,
                threads=threads,
                env=_smoke_worker_environment(paths, threads=threads, model_root=model_root),
                progress=lambda fraction, _stage, _detail: progress.append(fraction),
                cancel=CancelToken(),
                log=logging.getLogger("stemforge.smoke"),
                timeout_s=SMOKE_KARAOKE_TIMEOUT_SECONDS,
                on_spawn=sampler.start,
            )
            wall_seconds = time.monotonic() - started
        except Exception as exc:
            wall_seconds = time.monotonic() - started
            sampler.stop()
            code = getattr(exc, "code", "")
            detail = getattr(exc, "detail", None) or str(exc)
            _check(
                checks,
                "karaoke.worker",
                "failed",
                (
                    f"{code or type(exc).__name__}: {detail}. "
                    "Karaoke needs no model or network; check that the cache is writable."
                ),
            )
            report["wall_seconds"] = round(wall_seconds, 3)
            report["checks"] = checks
            return _finalize(report)
        peak_rss = sampler.stop()

        _check(
            checks,
            "karaoke.worker",
            "ok",
            f"two outputs in {wall_seconds:.2f} s",
        )
        problems: list[str] = []
        if len(outputs.outputs) != 2 or set(outputs.outputs) != set(output_roles):
            problems.append(f"outputs={sorted(outputs.outputs)}")
        for role in output_roles:
            path = outputs.path_for(role)
            if path is None or not path.is_file():
                problems.append(f"{role}=missing")
                continue
            facts = _wav_facts(path)
            if facts is None:
                problems.append(f"{role}=unreadable")
                continue
            tag, channels, rate, duration = facts
            if tag != 3 or channels != 2 or rate != 44_100:
                problems.append(f"{role}={rate}Hz/{channels}ch/tag{tag}")
            elif abs(duration - SMOKE_SEPARATION_SECONDS) > 0.050:
                problems.append(f"{role}=duration {duration:.3f}s")
        _check(
            checks,
            "karaoke.outputs",
            "failed" if problems else "ok",
            ", ".join(problems) if problems else "two float32 44.1 kHz stereo outputs",
        )
        vocals = outputs.path_for("vocals")
        instrumental = outputs.path_for("instrumental")
        vocals_peak = _wav_peak_abs(vocals) if vocals is not None else None
        instrumental_peak = _wav_peak_abs(instrumental) if instrumental is not None else None
        _check(
            checks,
            "karaoke.vocals",
            "ok"
            if vocals_peak is not None and vocals_peak >= SMOKE_KARAOKE_VOCALS_MIN_PEAK
            else "failed",
            "missing" if vocals_peak is None else f"peak {vocals_peak:.3f}",
        )
        _check(
            checks,
            "karaoke.instrumental",
            "ok"
            if instrumental_peak is not None
            and instrumental_peak <= SMOKE_KARAOKE_INSTRUMENTAL_MAX_PEAK
            else "failed",
            "missing"
            if instrumental_peak is None
            else f"peak {instrumental_peak:.3f} after centre removal",
        )
        _check(
            checks,
            "karaoke.time",
            "ok" if wall_seconds < SMOKE_KARAOKE_TIMEOUT_SECONDS else "failed",
            f"{wall_seconds:.2f} s (limit {SMOKE_KARAOKE_TIMEOUT_SECONDS:g} s)",
        )
        rss = peak_rss or 0
        _check(
            checks,
            "karaoke.rss",
            "ok" if 0 < rss < SMOKE_KARAOKE_RSS_BYTES else "failed",
            f"{rss / (1024 * 1024):.0f} MiB (limit {SMOKE_KARAOKE_RSS_BYTES // (1024 * 1024)} MiB)",
        )
        _check(
            checks,
            "karaoke.progress",
            "ok" if progress and max(progress) <= 1.0 and progress[-1] > 0 else "failed",
            f"{len(progress)} events, last {progress[-1]:.3f}"
            if progress
            else "no progress events",
        )
        report.update(
            {
                "wall_seconds": round(wall_seconds, 3),
                "peak_rss_bytes": rss,
                "outputs": list(output_roles),
                "checks": checks,
            }
        )
        return _finalize(report)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _smoke_worker_environment(
    paths: AppPaths, *, threads: int, model_root: Path | None = None
) -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(
        {
            "HF_HOME": str(model_root or paths.models),
            "HF_HUB_OFFLINE": "1",
            "OMP_NUM_THREADS": str(threads),
            "MKL_NUM_THREADS": str(threads),
            "PYTHONUNBUFFERED": "1",
            "PYTHONUTF8": "1",
            "STEMFORGE_WORKER_STDIN_WATCH": "1",
        }
    )
    return environment


def _write_smoke_wav(path: Path) -> None:
    """A deterministic 1 s 440 Hz float32 stereo source, no FFmpeg required."""
    frames = round(SMOKE_SEPARATION_SECONDS * 44_100)
    samples = array.array("f")
    for frame in range(frames):
        value = 0.2 * math.sin(2.0 * math.pi * 440.0 * frame / 44_100)
        samples.append(value)
        samples.append(value)
    data = samples.tobytes()
    header = bytearray(56)
    header[0:4] = b"RIFF"
    struct.pack_into("<I", header, 4, 48 + len(data))
    header[8:12] = b"WAVE"
    header[12:16] = b"fmt "
    struct.pack_into("<I", header, 16, 16)
    struct.pack_into("<HHIIHH", header, 20, 3, 2, 44_100, 44_100 * 8, 8, 32)
    header[36:40] = b"fact"
    struct.pack_into("<I", header, 40, 4)
    struct.pack_into("<I", header, 44, frames)
    header[48:52] = b"data"
    struct.pack_into("<I", header, 52, len(data))
    path.write_bytes(header + data)


def _wav_facts(path: Path) -> tuple[int, int, int, float] | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    offset = 12
    fmt: tuple[int, int, int] | None = None
    while offset + 8 <= len(data):
        chunk_id = data[offset : offset + 4]
        size = struct.unpack("<I", data[offset + 4 : offset + 8])[0]
        body = data[offset + 8 : offset + 8 + size]
        if chunk_id == b"fmt " and len(body) >= 16:
            tag, channels, rate = struct.unpack_from("<HHI", body, 0)
            fmt = (tag, channels, rate)
        elif chunk_id == b"data" and fmt is not None:
            tag, channels, rate = fmt
            frames = size // max(1, channels * 4)
            return tag, channels, rate, frames / rate if rate else 0.0
        offset += 8 + size + (size & 1)
    return None


def _wav_peak_abs(path: Path) -> float | None:
    """The largest absolute float32 sample, or ``None`` when unreadable."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    offset = 12
    while offset + 8 <= len(data):
        chunk_id = data[offset : offset + 4]
        size = struct.unpack("<I", data[offset + 4 : offset + 8])[0]
        body = data[offset + 8 : offset + 8 + size]
        if chunk_id == b"fmt " and len(body) >= 16:
            tag = struct.unpack_from("<H", body, 0)[0]
            if tag != 3:
                return None
        elif chunk_id == b"data":
            values = array.array("f")
            values.frombytes(body[: len(body) - (len(body) % 4)])
            return max((abs(value) for value in values), default=0.0)
        offset += 8 + size + (size & 1)
    return None


class _ChildRssSampler:
    """Measure the worker's peak RSS.

    Linux uses ``RUSAGE_CHILDREN`` after the child exits. Windows keeps a polling
    thread reading ``PeakWorkingSetSize`` through ``GetProcessMemoryInfo``.
    """

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._peak = 0

    def start(self, pid: int) -> None:
        if os.name != "nt":
            return
        self._thread = threading.Thread(
            target=self._poll_windows, args=(pid,), name="stemforge-smoke-rss", daemon=True
        )
        self._thread.start()

    def stop(self) -> int:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        if os.name == "nt":
            return self._peak
        try:
            import resource

            return int(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss) * 1024
        except (ImportError, OSError):  # pragma: no cover - non-POSIX
            return 0

    def _poll_windows(self, pid: int) -> None:  # pragma: no cover - Windows only
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        psapi = ctypes.WinDLL("psapi", use_last_error=True)  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return
        try:
            counters = Counters()
            counters.cb = ctypes.sizeof(Counters)
            while not self._stop.is_set():
                if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                    self._peak = max(self._peak, int(counters.PeakWorkingSetSize))
                else:
                    return
                time.sleep(0.05)
        finally:
            kernel32.CloseHandle(handle)
