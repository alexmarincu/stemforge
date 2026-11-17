"""The copy-only diagnostics report (ticket 14 §10).

``render_diagnostics`` is a pure text builder over an immutable fact record.
``collect_diagnostics`` gathers those facts from the app's normal seams and the
two log files. The UI only ever displays the resulting text; the sole writer is
the explicit ``--report <path>`` CLI path, never a copy action.
"""

from __future__ import annotations

import importlib.metadata
import os
import platform
import shutil
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from stemforge.artifacts import ArtifactStore
from stemforge.models import ModelStore
from stemforge.platform import preflight
from stemforge.platform.paths import AppPaths
from stemforge.settings import Settings, SettingsStore

__all__ = [
    "DiagnosticsFacts",
    "JobFact",
    "ModelFact",
    "collect_diagnostics",
    "render_diagnostics",
    "write_report",
]

APP_LOG_LINES = 200
JOB_LOG_LINES = 100
JOBS_SHOWN = 10
_MIB = 1024 * 1024
_GIB = 1024 * _MIB


@dataclass(frozen=True, slots=True)
class JobFact:
    """One Job row in the diagnostics report."""

    job_id: str
    kind: str
    state: str
    code: str | None = None


@dataclass(frozen=True, slots=True)
class ModelFact:
    """The htdemucs identity and verification state; never the weights."""

    installed: bool
    revision: str
    sha256_verified: bool
    verified_at: str | None = None


@dataclass(frozen=True, slots=True)
class DiagnosticsFacts:
    """Everything ticket 14 §10 asks the report to contain."""

    version: str
    bundle_kind: str
    os_name: str
    os_version: str
    arch: str
    session_type: str
    python_version: str
    frozen: bool
    pyinstaller_version: str
    cpu_model: str
    cores: int
    threads: int
    ram_gb: float
    disk_free_gb: float
    preflight_summary: str
    data_path: str
    cache_path: str
    logs_path: str
    models_path: str
    home_set: bool
    cache_used: str
    cache_cap: str
    source_count: int
    render_count: int
    evictions_last_7_days: int
    youtube_enabled: bool
    cache_cap_setting: str
    export_dir: str
    first_run: bool
    model: ModelFact
    jobs: tuple[JobFact, ...]
    app_log_lines: tuple[str, ...]
    job_log_id: str | None
    job_log_lines: tuple[str, ...]


def render_diagnostics(facts: DiagnosticsFacts) -> str:
    """Render the exact ticket 14 §10 text; no I/O and no hidden state."""
    lines = [
        f"StemForge {facts.version} · {facts.bundle_kind} · "
        f"{facts.os_name} {facts.os_version} {facts.arch} · {facts.session_type}",
        f"Python {facts.python_version} · {'frozen' if facts.frozen else 'source'} · "
        f"PyInstaller {facts.pyinstaller_version}",
        f"CPU {facts.cpu_model}, {facts.cores}/{facts.threads} · "
        f"RAM {facts.ram_gb:.1f} GB · free disk (cache) {facts.disk_free_gb:.1f} GB",
        f"Preflight: {facts.preflight_summary}",
        f"Paths: data={facts.data_path} cache={facts.cache_path} logs={facts.logs_path} "
        f"models={facts.models_path} "
        f"(STEMFORGE_HOME={'set' if facts.home_set else 'unset'})",
        f"Cache: {facts.cache_used} / {facts.cache_cap} · {facts.source_count} sources · "
        f"{facts.render_count} renders · {facts.evictions_last_7_days} evictions last 7 days",
        f"Settings: youtube_enabled={facts.youtube_enabled} · "
        f"cache_cap={facts.cache_cap_setting} · export_dir={facts.export_dir} · "
        f"first_run={facts.first_run}",
        f"Model: {'installed' if facts.model.installed else 'absent'} · "
        f"revision {facts.model.revision} · "
        f"sha256 {'verified' if facts.model.sha256_verified else 'not verified'} · "
        f"verified_at {facts.model.verified_at or '—'}",
    ]
    if facts.jobs:
        lines.extend(
            f"Jobs: {job.job_id} {job.kind} {job.state} {job.code or '-'}"
            for job in facts.jobs[:JOBS_SHOWN]
        )
    else:
        lines.append("Jobs: none")
    lines.append("--- app log (last 200 lines) ---")
    lines.extend(facts.app_log_lines)
    if facts.job_log_id is not None:
        lines.append(f"--- job log {facts.job_log_id} (last 100 lines) ---")
        lines.extend(facts.job_log_lines)
    return "\n".join(lines).rstrip() + "\n"


def collect_diagnostics(
    paths: AppPaths,
    *,
    report: preflight.PreflightReport | None = None,
    settings: SettingsStore | None = None,
    artifacts: ArtifactStore | None = None,
    models: ModelStore | None = None,
    jobs: Sequence[object] = (),
    job_id: str | None = None,
    app_log_path: Path | None = None,
    now: datetime | None = None,
) -> str:
    """Read the normal app seams and render the report.

    Every optional read is best-effort: a fatal storage failure still has to
    produce a copyable report, so an unreadable cache or model store degrades to
    zero/absent rather than raising.
    """
    current = datetime.now(UTC) if now is None else now
    store = _safe_settings(paths, settings)
    values = store.settings if store is not None else Settings()
    cache = (
        artifacts
        if artifacts is not None
        else ArtifactStore(paths, cap_bytes=values.cache_cap_bytes)
    )
    model_store = models if models is not None else ModelStore(paths)

    usage = _safe_int(cache.usage_bytes)
    source_count = _safe_count(cache.iter_sources())
    render_count = _safe_count(cache.iter_renders())
    evictions = _safe_evictions(cache, current)
    model_fact = _model_fact(model_store)
    job_facts = tuple(_job_fact(job) for job in jobs)
    job_log_path = _job_log_path(job_facts, jobs, job_id)
    ram = _total_ram_bytes()
    disk_free = _disk_free(paths)

    facts = DiagnosticsFacts(
        version=_version(),
        bundle_kind="bundle" if _frozen() else "source",
        os_name=platform.system() or sys.platform,
        os_version=platform.release() or platform.version(),
        arch=platform.machine() or "unknown",
        session_type=_session_type(),
        python_version=platform.python_version(),
        frozen=_frozen(),
        pyinstaller_version=_pyinstaller_version(),
        cpu_model=_cpu_model(),
        cores=_physical_cores(),
        threads=os.cpu_count() or 1,
        ram_gb=0.0 if ram is None else ram / _GIB,
        disk_free_gb=0.0 if disk_free is None else disk_free / _GIB,
        preflight_summary=_preflight_summary(report),
        data_path=str(paths.data),
        cache_path=str(paths.cache),
        logs_path=str(paths.logs),
        models_path=str(paths.models),
        home_set=bool(os.environ.get("STEMFORGE_HOME")),
        cache_used=_format_bytes(usage),
        cache_cap=_format_bytes(cache.cap_bytes),
        source_count=source_count,
        render_count=render_count,
        evictions_last_7_days=evictions,
        youtube_enabled=values.youtube_enabled,
        cache_cap_setting=_format_bytes(values.cache_cap_bytes),
        export_dir=str(values.export_dir) if values.export_dir is not None else "-",
        first_run=values.first_run,
        model=model_fact,
        jobs=job_facts,
        app_log_lines=read_last_lines(
            app_log_path if app_log_path is not None else paths.logs / "stemforge.log",
            APP_LOG_LINES,
        ),
        job_log_id=job_id if job_log_path is not None else None,
        job_log_lines=(
            read_last_lines(job_log_path, JOB_LOG_LINES) if job_log_path is not None else ()
        ),
    )
    return render_diagnostics(facts)


def write_report(path: Path, text: str) -> None:
    """Write the report for the explicit CLI ``--report`` path only."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def read_last_lines(path: Path, limit: int) -> tuple[str, ...]:
    """Return the last ``limit`` lines, or ``()`` when the file is unavailable."""
    if limit < 0:
        raise ValueError("line limit must not be negative")
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ()
    lines = text.splitlines()
    return tuple(lines[-limit:]) if limit else ()


# ---------------------------------------------------------------------------
# Fact helpers
# ---------------------------------------------------------------------------


def _job_fact(job: object) -> JobFact:
    kind = getattr(job, "kind", "")
    state = getattr(job, "state", "")
    return JobFact(
        job_id=str(getattr(job, "job_id", "") or "unknown"),
        kind=str(getattr(kind, "value", kind)),
        state=str(getattr(state, "value", state)),
        code=getattr(job, "error_code", None),
    )


def _job_log_path(
    facts: Sequence[JobFact],
    jobs: Sequence[object],
    job_id: str | None,
) -> Path | None:
    if job_id is None:
        return None
    for fact, job in zip(facts, jobs, strict=False):
        if fact.job_id != job_id:
            continue
        path = getattr(job, "log_path", None)
        return Path(path) if isinstance(path, Path) else None
    return None


def _model_fact(models: ModelStore) -> ModelFact:
    try:
        status = models.status()
        spec = models.spec
    except Exception:
        return ModelFact(installed=False, revision="unknown", sha256_verified=False)
    verified = bool(status.installed and status.sha256 is not None and status.sha256 == spec.sha256)
    return ModelFact(
        installed=status.installed,
        revision=spec.revision,
        sha256_verified=verified,
        verified_at=status.verified_at.isoformat() if status.verified_at is not None else None,
    )


def _preflight_summary(report: preflight.PreflightReport | None) -> str:
    if report is None:
        return "not run"
    parts = []
    for check in report.checks:
        message = " ".join(check.message.split())
        detail = f" ({message})" if message else ""
        parts.append(f"{check.name} {check.status}{detail}")
    return " | ".join(parts) if parts else "not run"


def _safe_settings(paths: AppPaths, settings: SettingsStore | None) -> SettingsStore | None:
    if settings is not None:
        return settings
    try:
        return SettingsStore(paths)
    except Exception:
        return None


def _safe_int(read: Callable[[], int]) -> int:
    try:
        value = read()
    except Exception:
        return 0
    return int(value) if isinstance(value, int) and value >= 0 else 0


def _safe_count(iterator: Iterable[object]) -> int:
    try:
        return sum(1 for _ in iterator)
    except Exception:
        return 0


def _safe_evictions(artifacts: ArtifactStore, now: datetime) -> int:
    try:
        return artifacts.eviction_count(now - timedelta(days=7))
    except Exception:
        return 0


def _version() -> str:
    from stemforge import __version__

    return __version__


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _pyinstaller_version() -> str:
    for name in ("pyinstaller", "PyInstaller"):
        try:
            return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            continue
    return "unknown"


def _session_type() -> str:
    if os.environ.get("STEMFORGE_BROWSER", "").strip().lower() in {"1", "true", "yes", "on"}:
        return "browser"
    if sys.platform.startswith("win"):
        return "windows"
    session = os.environ.get("XDG_SESSION_TYPE", "").strip().lower()
    if session:
        return session
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "headless"


def _cpu_model() -> str:
    if sys.platform.startswith("linux"):
        try:
            for line in (
                Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace").splitlines()
            ):
                key, _, value = line.partition(":")
                if key.strip() in {"model name", "Hardware"} and value.strip():
                    return " ".join(value.split())
        except OSError:
            pass
    processor = platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER", "")
    return " ".join(processor.split()) or platform.machine() or "unknown"


def _physical_cores() -> int:
    threads = os.cpu_count() or 1
    if sys.platform.startswith("linux"):
        try:
            for line in (
                Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace").splitlines()
            ):
                key, _, value = line.partition(":")
                if key.strip() == "cpu cores" and value.strip().isdigit():
                    return max(1, min(threads, int(value.strip())))
        except OSError:
            pass
    return max(1, threads // 2) if threads > 1 else 1


def _total_ram_bytes() -> int | None:
    try:
        return preflight.total_ram_bytes()
    except Exception:
        return None


def _disk_free(paths: AppPaths) -> int | None:
    try:
        return shutil.disk_usage(_existing_ancestor(paths.cache)).free
    except OSError:
        return None


def _existing_ancestor(path: Path) -> Path:
    candidate = Path(path)
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def _format_bytes(size: int) -> str:
    if size <= 0:
        return "0.0 MB"
    if size < _GIB:
        return f"{size / _MIB:.1f} MB"
    return f"{size / _GIB:.1f} GB"
