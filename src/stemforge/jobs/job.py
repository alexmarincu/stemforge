"""Job values: ``Job``, ``JobContext``, and the re-exported ``CancelToken``.

``JobRunner`` owns execution; this module owns the shapes a caller supplies and
the seam a pipeline function receives. ``CancelToken`` lives
in ``domain.cancellation`` because engine adapters take it and ``engines`` must
not import ``jobs``; it is re-exported here for the documented import path.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from stemforge.domain.cancellation import CancelToken
from stemforge.domain.models import JobKind
from stemforge.domain.progress import Progress

__all__ = ["CancelToken", "Job", "JobContext", "JobRun", "ProgressUpdate"]

JobRun = Callable[["JobContext"], Mapping[str, str] | None]
ProgressUpdate = Callable[[Progress], None]


@dataclass(frozen=True, slots=True)
class Job:
    """One unit of work submitted to the runner.

    ``identity`` is the dedupe key: while a Job with the same identity is queued
    or running, ``submit`` returns the existing job id. ``run`` receives the
    context and may return a mapping of result keys (for example ``source_id``
    and ``track_id``) that the snapshot publishes when the Job succeeds.
    """

    kind: JobKind
    identity: tuple[str, ...]
    run: JobRun
    title: str = ""

    def __post_init__(self) -> None:
        if not self.identity:
            raise ValueError("job identity must contain at least one part")
        if not all(isinstance(part, str) and part for part in self.identity):
            raise ValueError("job identity parts must be non-empty strings")
        if not callable(self.run):
            raise TypeError("job run must be callable")


class JobContext:
    """The pipeline-facing seam: progress, cancellation, and a Job-scoped log."""

    __slots__ = ("_cancel", "_job_id", "_kind", "_log", "_update")

    def __init__(
        self,
        *,
        job_id: str,
        kind: JobKind,
        cancel: CancelToken,
        log: logging.Logger,
        update: ProgressUpdate,
    ) -> None:
        self._job_id = job_id
        self._kind = kind
        self._cancel = cancel
        self._log = log
        self._update = update

    @property
    def job_id(self) -> str:
        """The runner-assigned Job id."""
        return self._job_id

    @property
    def kind(self) -> JobKind:
        """The Job kind."""
        return self._kind

    @property
    def cancel(self) -> CancelToken:
        """The cooperative cancel token; engine adapters check it."""
        return self._cancel

    @property
    def log(self) -> logging.Logger:
        """The Job-scoped logger, written to ``logs/jobs/<stamp>-<id>.log``."""
        return self._log

    def progress(
        self,
        fraction: float,
        *,
        stage: str | None = None,
        stage_code: str | None = None,
        stage_context: Mapping[str, object] | None = None,
        detail: str | None = None,
        detail_code: str | None = None,
        detail_context: Mapping[str, object] | None = None,
    ) -> None:
        """Publish a fraction with English log text and their stable codes."""
        value = float(fraction)
        if not math.isfinite(value):
            raise ValueError("progress must be finite")
        self._update(
            Progress(
                fraction=value,
                stage=stage,
                stage_code=stage_code,
                stage_context=stage_context or {},
                detail=detail,
                detail_code=detail_code,
                detail_context=detail_context or {},
            )
        )
