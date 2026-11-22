"""Shared mutable application state and its one consistency lock.

``WorkspaceState`` owns the session Workspace and the single re-entrant lock.
``JobLedger`` records which Track or Source owns each running Job, so Remove can
cancel the right work before deleting cache. Both use the same lock: the current
service relies on that atomicity when a Track is removed while its Job is active.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from types import TracebackType
from typing import Protocol

from stemforge.domain.models import Track
from stemforge.domain.workspace import Workspace, WorkspaceSnapshot
from stemforge.jobs.job import Job
from stemforge.jobs.models import JobSnapshot
from stemforge.jobs.runner import JobRunner


class Lock(Protocol):
    """The re-entrant lock operations the shared state needs."""

    def __enter__(self) -> bool: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None: ...


class WorkspaceState:
    """The Workspace plus the lock every reader and writer takes."""

    def __init__(self, workspace: Workspace | None = None) -> None:
        self._workspace = workspace if workspace is not None else Workspace()
        self._lock: Lock = threading.RLock()

    @property
    def lock(self) -> Lock:
        return self._lock

    def snapshot(self) -> WorkspaceSnapshot:
        with self._lock:
            return self._workspace.snapshot()

    def find(self, track_id: str) -> Track | None:
        with self._lock:
            return self._workspace.find(track_id)

    def find_by_source(self, source_id: str) -> Track | None:
        with self._lock:
            return self._workspace.find_by_source(source_id)

    def add_track(self, track: Track) -> Track:
        with self._lock:
            return self._workspace.add_track(track)

    def remove_track(self, track_id: str) -> Track:
        with self._lock:
            return self._workspace.remove_track(track_id)


class JobLedger:
    """Submits Jobs and remembers which Track or Source each one belongs to."""

    def __init__(self, runner: JobRunner, lock: Lock) -> None:
        self._runner = runner
        self._lock = lock
        self._owners: dict[str, tuple[str | None, str | None]] = {}

    @property
    def runner(self) -> JobRunner:
        return self._runner

    def submit(
        self,
        job: Job,
        *,
        track_id: str | None = None,
        source_id: str | None = None,
    ) -> str:
        job_id = self._runner.submit(job)
        if track_id is not None or source_id is not None:
            with self._lock:
                self._owners[job_id] = (track_id, source_id)
        return job_id

    def jobs(self) -> tuple[JobSnapshot, ...]:
        return tuple(self._runner.jobs())

    def snapshot(self, job_id: str) -> JobSnapshot:
        return self._runner.snapshot(job_id)

    def cancel(self, job_id: str) -> bool:
        return self._runner.cancel(job_id)

    def wait_idle(self, timeout: float | None = None) -> bool:
        return self._runner.wait_idle(timeout=timeout)

    def active_owned_job_ids(self, *, track_id: str, source_id: str) -> tuple[str, ...]:
        with self._lock:
            return tuple(
                snapshot.job_id
                for snapshot in self._runner.jobs()
                if snapshot.active and self._owns_locked(snapshot.job_id, track_id, source_id)
            )

    def forget(self, job_ids: Iterable[str]) -> None:
        with self._lock:
            for job_id in job_ids:
                self._owners.pop(job_id, None)

    def _owns_locked(self, job_id: str, track_id: str, source_id: str) -> bool:
        owner = self._owners.get(job_id)
        if owner is None:
            return False
        owner_track, owner_source = owner
        return owner_track == track_id or owner_source == source_id
