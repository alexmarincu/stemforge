"""``JobRunner``: the single background thread that owns Jobs.

One daemon thread, a FIFO queue, and strictly one active Job (ADR 0003). Every
state change happens under one condition variable, and readers only ever see
immutable :class:`~stemforge.jobs.models.JobSnapshot` values. A Job that raises
is recorded and the runner continues with the next Job.

Per-Job logs are created when a Job starts and closed when it ends; the app log
gets one runner-level line per start and finish. A retention sweep runs once per
runner start (ticket 07 §6).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Final
from uuid import uuid4

from stemforge import errors
from stemforge.domain.cancellation import CancelToken
from stemforge.domain.models import JobState
from stemforge.errors import JobCancelled, StemForgeError
from stemforge.jobs.job import Job, JobContext, ProgressUpdate
from stemforge.jobs.models import JobSnapshot
from stemforge.logging_setup import job_log_handler, sweep_job_logs
from stemforge.platform.paths import AppPaths

_LOG = logging.getLogger(__name__)

FINISHED_HISTORY: Final = 20
STOP_TIMEOUT_S: Final = 5.0

_FINISHED_STATES: frozenset[JobState] = frozenset(
    {JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED}
)


@dataclass
class _Entry:
    """Mutable runner-owned state for one Job."""

    job: Job
    job_id: str
    cancel: CancelToken
    created_at: datetime
    state: JobState = JobState.QUEUED
    progress: float = 0.0
    stage: str = ""
    detail: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: str | None = None
    error_detail: str | None = None
    retryable: bool = False
    log_path: Path | None = None
    result_keys: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    def snapshot(self) -> JobSnapshot:
        return JobSnapshot(
            job_id=self.job_id,
            kind=self.job.kind,
            state=self.state,
            progress=self.progress,
            stage=self.stage,
            detail=self.detail,
            title=self.job.title,
            created_at=self.created_at,
            started_at=self.started_at,
            finished_at=self.finished_at,
            error_code=self.error_code,
            error_detail=self.error_detail,
            retryable=self.retryable,
            log_path=self.log_path,
            result_keys=self.result_keys,
            cancel_requested=self.cancel.cancelled,
        )


class JobRunner:
    """Runs at most one Job at a time and publishes immutable snapshots."""

    def __init__(
        self,
        paths: AppPaths | None = None,
        *,
        finished_history: int = FINISHED_HISTORY,
    ) -> None:
        if finished_history < 1:
            raise ValueError("finished_history must be positive")
        self._paths = paths if paths is not None else AppPaths.from_environment()
        self._history_limit = finished_history
        self._cond = threading.Condition(threading.Lock())
        self._queue: deque[_Entry] = deque()
        self._active: _Entry | None = None
        self._entries: dict[str, _Entry] = {}
        self._order: list[str] = []
        self._thread: threading.Thread | None = None
        self._stopping = False

    @property
    def paths(self) -> AppPaths:
        return self._paths

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Start the worker thread and sweep expired Job logs once."""
        with self._cond:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stopping = False
            thread = threading.Thread(
                target=self._run_loop,
                name="stemforge-jobs",
                daemon=True,
            )
            self._thread = thread
            thread.start()

        try:
            sweep_job_logs(self._paths)
        except OSError:
            _LOG.warning("could not sweep expired Job logs", exc_info=True)

    def stop(self, timeout: float = STOP_TIMEOUT_S) -> None:
        """Cancel the active Job, drop queued Jobs, and join the worker."""
        with self._cond:
            self._stopping = True
            now = datetime.now(UTC)
            if self._active is not None:
                self._active.cancel.cancel()
            for entry in self._queue:
                entry.state = JobState.CANCELLED
                entry.finished_at = now
                entry.cancel.cancel()
                _LOG.info("job %s %s cancelled before start", entry.job_id, entry.job.kind.value)
            self._queue.clear()
            self._prune_locked()
            thread = self._thread
            self._cond.notify_all()

        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
            if thread.is_alive():
                _LOG.warning("the Job runner did not stop within %.1fs", timeout)

        with self._cond:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None

    # -- public API ---------------------------------------------------------

    def submit(self, job: Job) -> str:
        """Queue ``job`` and return its id, or the active duplicate's id."""
        with self._cond:
            for entry in self._active_entries():
                if entry.job.identity == job.identity:
                    return entry.job_id

            job_id = uuid4().hex
            entry = _Entry(
                job=job,
                job_id=job_id,
                cancel=CancelToken(),
                created_at=datetime.now(UTC),
            )
            self._entries[job_id] = entry
            self._order.append(job_id)
            self._queue.append(entry)
            self._cond.notify_all()
            return job_id

    def cancel(self, job_id: str) -> bool:
        """Cancel a queued or running Job; return ``False`` for a finished one."""
        with self._cond:
            entry = self._entries.get(job_id)
            if entry is None:
                return False
            if entry.state is JobState.QUEUED:
                try:
                    self._queue.remove(entry)
                except ValueError:
                    return False
                entry.state = JobState.CANCELLED
                entry.finished_at = datetime.now(UTC)
                entry.cancel.cancel()
                _LOG.info("job %s %s cancelled before start", job_id, entry.job.kind.value)
                self._prune_locked()
                self._cond.notify_all()
                return True
            if entry.state is JobState.RUNNING:
                entry.cancel.cancel()
                self._cond.notify_all()
                return True
            return False

    def snapshot(self, job_id: str) -> JobSnapshot:
        """Return the current snapshot of ``job_id`` or raise :class:`KeyError`."""
        with self._cond:
            entry = self._entries.get(job_id)
            if entry is None:
                raise KeyError(job_id)
            return entry.snapshot()

    def jobs(self) -> list[JobSnapshot]:
        """Every Job the runner still remembers, newest first."""
        with self._cond:
            return [self._entries[job_id].snapshot() for job_id in reversed(self._order)]

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Block until no Job is queued or running; ``False`` on timeout."""
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cond:
            while self._active is not None or self._queue:
                if deadline is None:
                    self._cond.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cond.wait(remaining)
            return True

    # -- worker -------------------------------------------------------------

    def _run_loop(self) -> None:
        while True:
            with self._cond:
                while not self._stopping and not self._queue:
                    self._cond.wait()
                if self._stopping:
                    return
                entry = self._queue.popleft()
                entry.state = JobState.RUNNING
                entry.started_at = datetime.now(UTC)
                self._active = entry
                self._cond.notify_all()

            try:
                self._execute(entry)
            except Exception:
                _LOG.exception("job %s failed outside the Job boundary", entry.job_id)
                self._finish_failed(
                    entry,
                    errors.INTERNAL_UNEXPECTED,
                    detail="the Job runner could not record this failure",
                    retryable=False,
                )
            finally:
                with self._cond:
                    if self._active is entry:
                        self._active = None
                    if entry.finished_at is None:
                        entry.finished_at = datetime.now(UTC)
                    self._prune_locked()
                    self._cond.notify_all()

    def _execute(self, entry: _Entry) -> None:
        handler: logging.Handler | None = None
        job_log = _LOG
        started = time.monotonic()
        try:
            try:
                handler = job_log_handler(self._paths, entry.job_id)
                job_log = logging.getLogger(f"stemforge.job.{entry.job_id}")
                job_log.setLevel(logging.DEBUG)
                job_log.propagate = False
                job_log.addHandler(handler)
                entry.log_path = Path(handler.baseFilename)
            except OSError:
                _LOG.warning("could not open the Job log for %s", entry.job_id, exc_info=True)

            _LOG.info("job %s %s started", entry.job_id, entry.job.kind.value)
            job_log.info(
                "job started kind=%s identity=%s",
                entry.job.kind.value,
                "/".join(entry.job.identity),
            )

            entry.cancel.check()
            context = JobContext(
                job_id=entry.job_id,
                kind=entry.job.kind,
                cancel=entry.cancel,
                log=job_log,
                update=self._progress_update(entry.job_id),
            )
            result = entry.job.run(context)
            keys = _result_keys(result)

            elapsed = time.monotonic() - started
            with self._cond:
                entry.state = JobState.SUCCEEDED
                entry.progress = 1.0
                entry.result_keys = keys
                entry.finished_at = datetime.now(UTC)
                entry.detail = None
                self._cond.notify_all()
            job_log.info("job succeeded in %.2fs", elapsed)
            _LOG.info("job %s %s succeeded in %.2fs", entry.job_id, entry.job.kind.value, elapsed)
        except JobCancelled:
            elapsed = time.monotonic() - started
            self._finish(entry, JobState.CANCELLED)
            job_log.info("cancelled after %.2fs", elapsed)
            _LOG.info("job %s %s cancelled in %.2fs", entry.job_id, entry.job.kind.value, elapsed)
        except StemForgeError as exc:
            elapsed = time.monotonic() - started
            self._finish(
                entry,
                JobState.FAILED,
                error_code=exc.code,
                error_detail=exc.detail,
                retryable=exc.retryable,
            )
            job_log.error(
                "failed code=%s retryable=%s detail=%s stage=%s progress=%.3f after %.2fs",
                exc.code,
                exc.retryable,
                exc.detail or "",
                entry.stage,
                entry.progress,
                elapsed,
            )
            _LOG.info(
                "job %s %s failed code=%s after %.2fs",
                entry.job_id,
                entry.job.kind.value,
                exc.code,
                elapsed,
            )
        except Exception as exc:
            elapsed = time.monotonic() - started
            detail = f"{type(exc).__name__}: {exc}"
            self._finish(
                entry,
                JobState.FAILED,
                error_code=errors.INTERNAL_UNEXPECTED,
                error_detail=detail,
                retryable=False,
            )
            job_log.exception("unexpected failure after %.2fs", elapsed)
            _LOG.exception("job %s %s failed unexpectedly", entry.job_id, entry.job.kind.value)
        finally:
            if handler is not None:
                job_log.removeHandler(handler)
                handler.close()
            with self._cond:
                if entry.finished_at is None:
                    entry.finished_at = datetime.now(UTC)
                self._cond.notify_all()

    def _finish(
        self,
        entry: _Entry,
        state: JobState,
        *,
        error_code: str | None = None,
        error_detail: str | None = None,
        retryable: bool = False,
    ) -> None:
        with self._cond:
            entry.state = state
            entry.error_code = error_code
            entry.error_detail = error_detail
            entry.retryable = retryable
            entry.finished_at = datetime.now(UTC)
            self._cond.notify_all()

    def _finish_failed(
        self,
        entry: _Entry,
        code: str,
        *,
        detail: str,
        retryable: bool,
    ) -> None:
        self._finish(
            entry, JobState.FAILED, error_code=code, error_detail=detail, retryable=retryable
        )

    def _progress_update(self, job_id: str) -> ProgressUpdate:
        def update(fraction: float, stage: str | None, detail: str | None) -> None:
            with self._cond:
                entry = self._entries.get(job_id)
                if entry is None or entry.state is not JobState.RUNNING:
                    return
                clamped = min(1.0, max(0.0, fraction))
                if clamped > entry.progress:
                    entry.progress = clamped
                if stage is not None:
                    entry.stage = stage
                if detail is not None:
                    entry.detail = detail
                self._cond.notify_all()

        return update

    def _active_entries(self) -> tuple[_Entry, ...]:
        """Every queued or running entry; the caller holds the lock."""
        active = () if self._active is None else (self._active,)
        return (*active, *self._queue)

    def _prune_locked(self) -> None:
        finished = [
            job_id for job_id in self._order if self._entries[job_id].state in _FINISHED_STATES
        ]
        while len(finished) > self._history_limit:
            oldest = finished.pop(0)
            self._order.remove(oldest)
            del self._entries[oldest]


def _result_keys(result: object) -> Mapping[str, str]:
    if result is None:
        return MappingProxyType({})
    if not isinstance(result, Mapping):
        raise TypeError("a Job must return a mapping of result keys or None")
    keys: dict[str, str] = {}
    for key, value in result.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise TypeError("Job result keys and values must be strings")
        keys[key] = value
    return MappingProxyType(keys)
