"""The one coded Job failure payload every UI surface renders.

``StemForgeError`` is the single error type the UI sees, and a finished Job's
snapshot carries the same failure fields. ``JobFailure`` is the view both
become: the shared six fields plus the request-specific data each feature keeps
beside it. The copy table stays in ``ui/formatting.py``; this module only
carries data.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from stemforge.application import JobView
from stemforge.errors import StemForgeError


@dataclass(frozen=True, slots=True)
class JobFailure:
    """The one coded failure payload every Job-backed surface renders.

    Feature-specific request data (Pitch scope, Export spec) lives beside this
    value, never as another copy of these fields.
    """

    code: str
    detail: str | None = None
    retryable: bool = False
    context: Mapping[str, object] | None = None
    job_id: str | None = None
    log_path: Path | None = None

    @classmethod
    def from_error(cls, error: StemForgeError) -> JobFailure:
        """Build the UI payload from the one exception type the UI sees."""
        return cls(
            code=error.code,
            detail=error.detail,
            retryable=error.retryable,
            context=error.context,
        )

    @classmethod
    def from_job(cls, job: JobView, *, fallback_code: str) -> JobFailure:
        """Build the UI payload from a finished Job's failure fields."""
        return cls(
            code=job.error_code or fallback_code,
            detail=job.error_detail,
            retryable=job.retryable,
            context=job.error_context,
            job_id=job.job_id or None,
            log_path=job.log_path,
        )
