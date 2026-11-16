"""Logging: the rotating app log, the level override, and per-Job handlers."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stemforge.logging_setup import (
    APP_LOG_BACKUP_COUNT,
    APP_LOG_MAX_BYTES,
    configure_logging,
    job_log_handler,
)
from stemforge.platform.paths import AppPaths

STEMFORGE_LOGGER = "stemforge"


@pytest.fixture
def paths(tmp_path: Path) -> AppPaths:
    return AppPaths.for_home(tmp_path / "home").ensure()


@pytest.fixture(autouse=True)
def _restore_logger_state() -> object:
    logger = logging.getLogger(STEMFORGE_LOGGER)
    old_level = logger.level
    old_propagate = logger.propagate
    old_handlers = list(logger.handlers)
    uvicorn_levels = {
        name: logging.getLogger(name).level
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access")
    }
    yield
    for handler in list(logger.handlers):
        if handler not in old_handlers:
            logger.removeHandler(handler)
            handler.close()
    logger.setLevel(old_level)
    logger.propagate = old_propagate
    for name, level in uvicorn_levels.items():
        logging.getLogger(name).setLevel(level)


def _flush(logger: logging.Logger) -> None:
    for handler in logger.handlers:
        handler.flush()


@pytest.mark.unit
def test_configure_writes_the_app_log_and_sets_uvicorn_to_warning(
    paths: AppPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("STEMFORGE_LOG_LEVEL", raising=False)

    configure_logging(paths)
    logger = logging.getLogger(STEMFORGE_LOGGER)
    logger.info("self-test marker")
    _flush(logger)

    log_path = paths.logs / "stemforge.log"
    assert log_path.is_file()
    assert "self-test marker" in log_path.read_text(encoding="utf-8")
    assert logger.level == logging.INFO
    assert logger.propagate is False
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        assert logging.getLogger(name).level == logging.WARNING


@pytest.mark.unit
def test_log_level_can_be_overridden_by_environment(
    paths: AppPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STEMFORGE_LOG_LEVEL", "DEBUG")

    configure_logging(paths)

    assert logging.getLogger(STEMFORGE_LOGGER).level == logging.DEBUG


@pytest.mark.unit
def test_an_invalid_log_level_falls_back_to_info(
    paths: AppPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STEMFORGE_LOG_LEVEL", "verbose-ish")

    configure_logging(paths)

    assert logging.getLogger(STEMFORGE_LOGGER).level == logging.INFO


@pytest.mark.unit
def test_configure_is_idempotent(paths: AppPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STEMFORGE_LOG_LEVEL", raising=False)

    configure_logging(paths)
    configure_logging(paths)
    logger = logging.getLogger(STEMFORGE_LOGGER)
    logger.info("exactly once")
    _flush(logger)

    text = (paths.logs / "stemforge.log").read_text(encoding="utf-8")
    assert text.count("exactly once") == 1


@pytest.mark.unit
def test_app_log_rotates_at_two_mib_and_keeps_three_backups(
    paths: AppPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("STEMFORGE_LOG_LEVEL", raising=False)
    configure_logging(paths)
    logger = logging.getLogger(STEMFORGE_LOGGER)

    for _ in range(12_000):
        logger.info("%s", "x" * 1024)
    _flush(logger)

    files = sorted(paths.logs.glob("stemforge.log*"))
    assert [path.name for path in files] == [
        "stemforge.log",
        "stemforge.log.1",
        "stemforge.log.2",
        "stemforge.log.3",
    ]
    assert not (paths.logs / "stemforge.log.4").exists()
    assert all(path.stat().st_size <= APP_LOG_MAX_BYTES for path in files)
    assert (paths.logs / "stemforge.log.3").stat().st_size > 0
    assert APP_LOG_BACKUP_COUNT == 3


@pytest.mark.unit
def test_job_log_handler_names_the_file_with_the_utc_time_and_job_id(
    paths: AppPaths,
) -> None:
    handler = job_log_handler(
        paths,
        "job-123",
        opened_at=datetime(2026, 11, 16, 12, 0, tzinfo=UTC),
    )
    job_logger = logging.getLogger("stemforge.job.test")
    job_logger.setLevel(logging.INFO)
    job_logger.propagate = False
    job_logger.addHandler(handler)
    try:
        job_logger.info("worker stderr verbatim")
        handler.flush()

        log_path = paths.logs / "jobs" / "20261116T120000Z-job-123.log"
        assert log_path.is_file()
        assert "worker stderr verbatim" in log_path.read_text(encoding="utf-8")
    finally:
        job_logger.removeHandler(handler)
        handler.close()


@pytest.mark.unit
def test_job_log_handler_creates_the_jobs_directory(paths: AppPaths) -> None:
    handler = job_log_handler(paths, "job-456")
    try:
        assert (paths.logs / "jobs").is_dir()
        assert Path(handler.baseFilename).parent == paths.logs / "jobs"
        assert Path(handler.baseFilename).name.endswith("-job-456.log")
    finally:
        handler.close()
