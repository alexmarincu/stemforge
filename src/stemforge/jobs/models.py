"""Immutable Job values published to the UI and the headless CLI.

``JobKind`` and ``JobState`` are domain vocabulary; this module re-exports them
so callers can import every Job value from one place.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

from stemforge.domain.models import JobKind, JobState

__all__ = ["JobKind", "JobSnapshot", "JobState"]

_ACTIVE_STATES: frozenset[JobState] = frozenset({JobState.QUEUED, JobState.RUNNING})


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    """A point-in-time view of one Job, safe to read from any thread."""

    job_id: str
    kind: JobKind
    state: JobState
    progress: float = 0.0
    stage: str = ""
    detail: str | None = None
    title: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: str | None = None
    error_detail: str | None = None
    error_context: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))
    retryable: bool = False
    log_path: Path | None = None
    result_keys: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    cancel_requested: bool = False

    @property
    def active(self) -> bool:
        """Whether the Job is queued or running."""
        return self.state in _ACTIVE_STATES

    @property
    def finished(self) -> bool:
        """Whether the Job reached a terminal state."""
        return not self.active

    @property
    def duration_s(self) -> float | None:
        """Seconds from start to finish, or since start while running."""
        if self.started_at is None:
            return None
        end = self.finished_at or datetime.now(UTC)
        return max(0.0, (end - self.started_at).total_seconds())
