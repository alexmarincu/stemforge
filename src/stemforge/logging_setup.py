"""Logging configuration: the app log and the per-Job log handlers.

Stdlib ``logging`` only. Root logger ``stemforge``, ``logs/stemforge.log`` with
2 MiB with 3 backups, and one capped file per Job under ``logs/jobs``
(ticket 06 §9, ticket 07 §6). No telemetry and no network handlers.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Final

from .platform.paths import AppPaths

LOGGER_NAME: Final = "stemforge"
APP_LOG_FILENAME: Final = "stemforge.log"
APP_LOG_MAX_BYTES: Final = 2 * 1024 * 1024
APP_LOG_BACKUP_COUNT: Final = 3
JOB_LOG_MAX_BYTES: Final = 10 * 1024 * 1024
JOB_LOG_RETENTION: Final = 20
JOB_LOG_MAX_AGE_DAYS: Final = 7
LEVEL_ENV_VAR: Final = "STEMFORGE_LOG_LEVEL"

_LOG_FORMAT: Final = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_LOG_DATE_FORMAT: Final = "%Y-%m-%dT%H:%M:%S%z"
_UVICORN_LOGGERS: Final = ("uvicorn", "uvicorn.error", "uvicorn.access")


class _AppLogHandler(RotatingFileHandler):
    """Marker subclass so reconfiguration can replace only our handler."""


class _CappedFileHandler(logging.FileHandler):
    """A per-Job log that stops accepting records at a hard byte cap."""

    def __init__(self, filename: Path, *, max_bytes: int) -> None:
        super().__init__(filename, mode="a", encoding="utf-8", delay=False)
        self._max_bytes = max_bytes
        try:
            self._size = os.path.getsize(filename)
        except OSError:
            self._size = 0

    def emit(self, record: logging.LogRecord) -> None:
        if self._size >= self._max_bytes:
            return
        super().emit(record)
        self.flush()
        stream = self.stream
        if stream is not None:
            try:
                self._size = stream.tell()
            except (OSError, ValueError):
                self._size = self._max_bytes


def configure_logging(paths: AppPaths, *, level: int | str | None = None) -> None:
    """Configure the ``stemforge`` logger tree for the current process."""
    resolved = paths.ensure()
    handler = _AppLogHandler(
        resolved.logs / APP_LOG_FILENAME,
        maxBytes=APP_LOG_MAX_BYTES,
        backupCount=APP_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, _LOG_DATE_FORMAT))

    logger = logging.getLogger(LOGGER_NAME)
    for existing in list(logger.handlers):
        if isinstance(existing, _AppLogHandler):
            logger.removeHandler(existing)
            existing.close()
    logger.addHandler(handler)
    logger.setLevel(_resolve_level(level))
    logger.propagate = False

    for name in _UVICORN_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def job_log_handler(
    paths: AppPaths,
    job_id: str,
    *,
    opened_at: datetime | None = None,
) -> logging.FileHandler:
    """Create the capped ``logs/jobs/<UTC timestamp>-<job_id>.log`` handler."""
    resolved = paths.ensure()
    jobs_dir = resolved.logs / "jobs"
    jobs_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = (opened_at or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    handler = _CappedFileHandler(jobs_dir / f"{stamp}-{job_id}.log", max_bytes=JOB_LOG_MAX_BYTES)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, _LOG_DATE_FORMAT))
    return handler


def sweep_job_logs(
    paths: AppPaths,
    *,
    keep: int = JOB_LOG_RETENTION,
    max_age_days: float = JOB_LOG_MAX_AGE_DAYS,
    now: datetime | None = None,
) -> tuple[Path, ...]:
    """Delete Job logs beyond the newest ``keep`` or older than ``max_age_days``.

    Both rules apply: a file is removed when it falls outside the newest ``keep``
    *or* its mtime is older than the age limit. Missing or unreadable entries are
    ignored; this is a best-effort startup sweep (ticket 07 §6).
    """
    if keep < 1:
        raise ValueError("keep must be positive")
    if max_age_days < 0:
        raise ValueError("max_age_days must not be negative")

    jobs_dir = paths.logs / "jobs"
    if not jobs_dir.is_dir():
        return ()

    candidates: list[tuple[float, Path]] = []
    for entry in jobs_dir.iterdir():
        if entry.suffix != ".log" or not entry.is_file():
            continue
        try:
            candidates.append((entry.stat().st_mtime, entry))
        except OSError:
            continue
    candidates.sort(key=lambda item: (item[0], item[1].name), reverse=True)

    cutoff = (now or datetime.now(UTC)).timestamp() - max_age_days * 86_400
    removed: list[Path] = []
    for index, (mtime, path) in enumerate(candidates):
        if index < keep and mtime >= cutoff:
            continue
        try:
            path.unlink()
        except OSError:
            logging.getLogger(LOGGER_NAME).warning("could not remove Job log %s", path)
            continue
        removed.append(path)
    return tuple(removed)


def _resolve_level(level: int | str | None) -> int:
    if isinstance(level, int):
        return level
    raw = level if level is not None else os.environ.get(LEVEL_ENV_VAR)
    if raw is None:
        return logging.INFO
    text = str(raw).strip()
    if not text:
        return logging.INFO
    if text.isdigit():
        return int(text)
    resolved = logging.getLevelName(text.upper())
    return resolved if isinstance(resolved, int) else logging.INFO
