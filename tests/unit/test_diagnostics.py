"""The copy-only diagnostics report.

The renderer is pure: tests build facts by hand and inspect the exact text. The
collector is exercised against a temporary ``STEMFORGE_HOME`` only, so no test
reads or writes a real profile. Nothing here uploads, zips, or writes a report
unless the CLI's explicit ``--report`` path is used.
"""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stemforge import __main__ as entry
from stemforge.artifacts import ArtifactStore
from stemforge.domain.models import JobKind, JobState
from stemforge.integration import diagnostics
from stemforge.jobs.models import JobSnapshot
from stemforge.models import ModelStore
from stemforge.platform.paths import AppPaths
from stemforge.platform.preflight import CheckResult, PreflightReport
from stemforge.settings import SettingsStore

pytestmark = pytest.mark.unit


def _facts(**overrides: object) -> diagnostics.DiagnosticsFacts:
    base = diagnostics.DiagnosticsFacts(
        version="9.9.9",
        bundle_kind="source",
        os_name="Linux",
        os_version="6.8.0",
        arch="x86_64",
        session_type="wayland",
        python_version="3.13.18",
        frozen=False,
        pyinstaller_version="6.22.3",
        cpu_model="Fixture CPU",
        cores=4,
        threads=8,
        ram_gb=16.0,
        disk_free_gb=120.0,
        preflight_summary="window ok | preview ok | bundle ok | storage ok",
        data_path="/home/alex/.local/share/stemforge",
        cache_path="/home/alex/.cache/stemforge",
        logs_path="/home/alex/.local/state/stemforge/log",
        models_path="/home/alex/.local/share/stemforge/models",
        home_set=True,
        cache_used="3.4 GB",
        cache_cap="8.0 GB",
        source_count=2,
        render_count=1,
        evictions_last_7_days=3,
        youtube_enabled=True,
        cache_cap_setting="8.0 GB",
        export_dir="/home/alex/Music",
        first_run=False,
        interface_language="en",
        language_source="system",
        model=diagnostics.ModelFact(
            installed=True,
            revision="cbc8a9b1a870",
            sha256_verified=True,
            verified_at="2026-11-17T10:00:00+00:00",
        ),
        jobs=(
            diagnostics.JobFact("job-new", "export", "succeeded", None),
            diagnostics.JobFact("job-old", "separate", "failed", "separation.engine_error"),
        ),
        app_log_lines=("app line 1", "app line 2"),
        job_log_id="job-new",
        job_log_lines=("job line 1",),
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def test_report_has_the_full_structure() -> None:
    text = diagnostics.render_diagnostics(_facts())
    lines = text.splitlines()

    assert lines[0] == "StemForge 9.9.9 · source · Linux 6.8.0 x86_64 · wayland"
    assert lines[1] == "Python 3.13.18 · source · PyInstaller 6.22.3"
    assert lines[2] == ("CPU Fixture CPU, 4/8 · RAM 16.0 GB · free disk (cache) 120.0 GB")
    assert lines[3] == "Preflight: window ok | preview ok | bundle ok | storage ok"
    assert lines[4] == (
        "Paths: data=/home/alex/.local/share/stemforge cache=/home/alex/.cache/stemforge "
        "logs=/home/alex/.local/state/stemforge/log "
        "models=/home/alex/.local/share/stemforge/models (STEMFORGE_HOME=set)"
    )
    assert lines[5] == "Cache: 3.4 GB / 8.0 GB · 2 sources · 1 renders · 3 evictions last 7 days"
    assert lines[6] == (
        "Settings: youtube_enabled=True · cache_cap=8.0 GB · "
        "export_dir=/home/alex/Music · first_run=False"
    )
    assert lines[7] == "Language: en (system)"
    assert lines[8] == (
        "Model: installed · revision cbc8a9b1a870 · sha256 verified · "
        "verified_at 2026-11-17T10:00:00+00:00"
    )
    assert "Jobs: job-new export succeeded -" in text
    assert "Jobs: job-old separate failed separation.engine_error" in text
    assert "--- app log (last 200 lines) ---" in text
    assert "app line 1" in text
    assert "app line 2" in text
    assert "--- job log job-new (last 100 lines) ---" in text
    assert "job line 1" in text


def test_an_absent_model_and_no_jobs_still_render_every_section() -> None:
    text = diagnostics.render_diagnostics(
        _facts(
            model=diagnostics.ModelFact(
                installed=False,
                revision="cbc8a9b1a870",
                sha256_verified=False,
                verified_at=None,
            ),
            jobs=(),
            job_log_id=None,
            job_log_lines=(),
        )
    )

    assert "Model: absent · revision cbc8a9b1a870 · sha256 not verified · verified_at —" in text
    assert "Jobs: none" in text
    assert "--- job log" not in text


def test_home_unset_and_absent_export_dir_are_explicit() -> None:
    text = diagnostics.render_diagnostics(
        _facts(home_set=False, export_dir="-", jobs=(), job_log_id=None, job_log_lines=())
    )

    assert "(STEMFORGE_HOME=unset)" in text
    assert "export_dir=-" in text


def test_report_never_contains_recent_titles_tags_or_cookies(
    app_paths: AppPaths,
) -> None:
    settings = SettingsStore(app_paths)
    settings.record_recent(
        source_id="src-secret",
        title="SECRET-SONG-TITLE",
        origin="/tmp/secret-song.wav",
    )
    text = diagnostics.collect_diagnostics(app_paths, settings=settings)

    assert "SECRET-SONG-TITLE" not in text
    assert "secret-song.wav" not in text
    assert "cookie" not in text.lower()
    assert "comment=" not in text


def test_collect_reads_cache_counts_settings_model_and_the_open_job_log(
    app_paths: AppPaths,
) -> None:
    settings = SettingsStore(app_paths)
    settings.set_youtube_enabled(False)
    settings.set_language("ro")
    store = ArtifactStore(app_paths, cap_bytes=settings.settings.cache_cap_bytes)
    staging = store.stage("source-job")
    (staging / "canonical.wav").write_bytes(b"canonical")
    import hashlib

    data = (staging / "canonical.wav").read_bytes()
    store.commit_source(
        staging,
        "src-1",
        {
            "schema_version": 1,
            "kind": "source",
            "source_id": "src-1",
            "created_at": datetime.now(UTC).isoformat(),
            "files": [
                {
                    "path": "canonical.wav",
                    "role": "canonical",
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            ],
        },
    )
    job_log = app_paths.logs / "jobs" / "20261117T100000Z-job-open.log"
    job_log.parent.mkdir(parents=True, exist_ok=True)
    job_log.write_text("\n".join(f"job line {index}" for index in range(150)), encoding="utf-8")
    app_log = app_paths.logs / "stemforge.log"
    app_log.write_text("\n".join(f"app line {index}" for index in range(250)), encoding="utf-8")
    snapshot = JobSnapshot(
        job_id="job-open",
        kind=JobKind.EXPORT,
        state=JobState.RUNNING,
        log_path=job_log,
    )
    report = PreflightReport(
        checks=(
            CheckResult("window", "ok", "GTK 3, WebKitGTK 2.50.0, Soup 3.", False),
            CheckResult("preview", "ok", "GStreamer elements available via gst-python.", False),
            CheckResult("bundle", "ok", "ffmpeg, ffprobe, rubberband, and deno match.", False),
            CheckResult("storage", "ok", "All application folders are writable.", False),
        ),
        fail_on_degraded=False,
    )

    text = diagnostics.collect_diagnostics(
        app_paths,
        report=report,
        settings=settings,
        artifacts=store,
        models=ModelStore(app_paths),
        jobs=(snapshot,),
        job_id="job-open",
    )

    assert "Cache: 0.0 MB / 8.0 GB · 1 sources · 0 renders · 0 evictions last 7 days" in text
    assert "Settings: youtube_enabled=False" in text
    assert "Language: ro (override)" in text
    assert "Model: absent · revision cbc8a9b1a870" in text
    assert "Jobs: job-open export running -" in text
    assert "app line 249" in text
    assert "app line 49" not in text
    assert "job line 149" in text
    assert "job line 49" not in text
    assert "Preflight: window ok (GTK 3" in text


def test_a_system_preference_resolves_from_the_host_locale(
    app_paths: AppPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(diagnostics.i18n, "system_locale", lambda: "ro_RO.UTF-8")
    settings = SettingsStore(app_paths)

    text = diagnostics.collect_diagnostics(app_paths, settings=settings)

    assert "Language: ro (system)" in text


def test_cli_diagnostics_report_writes_the_same_text(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "diagnostics.txt"

    code = entry.main(["--diagnostics", "--report", str(target)])

    assert code == 0
    written = target.read_text(encoding="utf-8")
    assert written.startswith("StemForge ")
    assert "Preflight:" in written
    assert "Paths: data=" in written
    assert "--- app log (last 200 lines) ---" in written
    # Windows hands out 8.3 short names (RUNNER~1) for the session temp dir; the
    # app resolves them through AppPaths.ensure(), so compare the same path.
    assert str(Path(os.environ["STEMFORGE_HOME"]).resolve() / "data") in written
    assert capsys.readouterr().out == ""


def test_bare_report_writes_the_diagnostics_structure(tmp_path: Path) -> None:
    target = tmp_path / "diagnostics.txt"

    code = entry.main(["--report", str(target), "--quiet"])

    assert code == 0
    text = target.read_text(encoding="utf-8")
    assert text.startswith("StemForge ")
    assert "--- app log (last 200 lines) ---" in text


def test_writing_the_report_needs_an_explicit_cli_path(tmp_path: Path) -> None:
    target = tmp_path / "diagnostics.txt"
    assert not target.exists()

    text = diagnostics.collect_diagnostics(AppPaths.for_home(tmp_path / "home"))

    assert "StemForge " in text
    assert not target.exists()
