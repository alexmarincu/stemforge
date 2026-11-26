"""Batch Export Job: every output of one Artifact set, one format, one folder.

A batch is one Job (ADR 0015). It plans the active version's outputs in card
order — Vocals, Instrumental, Drums, Bass, Other — probes them once to check
free space for the whole set, then runs the existing single-target
:func:`~stemforge.pipeline.export_job.run_export_job` per item. Item-level
failures are recorded and the rest continue; a destination-level failure stops
the batch because every remaining item would fail the same way.

Per-item outcomes are published through ``record`` as each item finishes. They
cannot live in the runner's ``result_keys``: the runner keeps those only on
success, and the results list must survive failure and cancellation.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Final

from stemforge import errors
from stemforge.artifacts import ArtifactStore, require_free_space
from stemforge.domain.models import ExportBatchItemState
from stemforge.domain.progress import STAGE_EXPORT_BATCH_ITEM, Progress
from stemforge.engines.media import ExportFormat, MediaTool
from stemforge.errors import JobCancelled, StemForgeError
from stemforge.jobs.job import JobContext
from stemforge.pipeline.export_job import (
    ExportTarget,
    resolve_export_target,
    run_export_job,
)
from stemforge.pipeline.export_names import required_export_space

__all__ = [
    "BATCH_ROLE_ORDER",
    "ExportBatchItemOutcome",
    "batch_targets",
    "run_export_batch_job",
]

# The card's order: the two primary outputs first, then the three remaining
# Stems. The order is visible on cancel and in the results list.
BATCH_ROLE_ORDER: Final = ("vocals", "instrumental", "drums", "bass", "other")


@dataclass(frozen=True, slots=True)
class ExportBatchItemOutcome:
    """One item's last known outcome, handed to the outcome recorder."""

    key: str
    label: str
    state: ExportBatchItemState
    path: Path | None = None
    error: StemForgeError | None = None
    label_key: str = ""
    label_context: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))


RecordOutcome = Callable[[ExportBatchItemOutcome], None]


@dataclass
class _RunState:
    """The mutable facts one batch run gathers as it goes."""

    finished: set[str] = field(default_factory=set)
    durations: dict[str, float] = field(default_factory=dict)
    failures: list[StemForgeError] = field(default_factory=list)


def batch_targets(
    targets: Sequence[ExportTarget],
    *,
    version_key: str,
) -> tuple[ExportTarget, ...]:
    """The outputs a batch exports for one version, in card order.

    ``"original"`` selects only the committed Separation's outputs; the master
    is never part of a batch. A Render version selects that set's outputs. A
    missing role is simply absent, so a two-output Separation exports two files.
    """
    if version_key == "original":
        selected = [target for target in targets if target.scope == "separation"]
    else:
        selected = [target for target in targets if target.render_key == version_key]
    order = {role: index for index, role in enumerate(BATCH_ROLE_ORDER)}
    return tuple(sorted(selected, key=lambda target: order.get(target.role, len(order))))


def run_export_batch_job(
    ctx: JobContext,
    *,
    artifacts: ArtifactStore,
    media: MediaTool,
    source_id: str,
    targets: Sequence[ExportTarget],
    fmt: ExportFormat | str,
    destination_dir: Path,
    record: RecordOutcome,
) -> Mapping[str, str]:
    """Export every target into one folder, recording each item as it finishes.

    Returns summary result keys on full success. A batch with any failed item
    finishes the rest and then fails with the first item's code; a
    destination-level failure and a cancel stop the batch and mark every item
    that was not written as not exported.
    """
    if not targets:
        raise StemForgeError(
            errors.INTERNAL_UNEXPECTED,
            "the batch has no exportable outputs",
            detail="target",
        )

    export_format = ExportFormat(fmt)
    destination = Path(destination_dir).expanduser().absolute()
    total = len(targets)
    cancel = ctx.cancel
    log = ctx.log
    cancel.check()

    state = _RunState()
    _probe_targets(
        ctx,
        artifacts=artifacts,
        media=media,
        source_id=source_id,
        targets=targets,
        record=record,
        state=state,
    )

    if state.durations:
        required = required_export_space(sum(state.durations.values()))
        try:
            require_free_space(destination, required, floor=0)
        except StemForgeError:
            _mark_unwritten(targets, state=state, record=record)
            raise

    written = 0
    for index, target in enumerate(targets):
        if target.key not in state.durations:
            continue
        cancel.check()
        item_context = _item_context(
            ctx,
            index=index,
            total=total,
            label=target.label,
            label_key=target.label_key,
            label_context=target.label_context,
        )
        item_context.progress(
            0.0,
            stage=_stage(index, total),
            stage_code=STAGE_EXPORT_BATCH_ITEM,
            stage_context={"index": index + 1, "total": total},
            detail=target.label,
            detail_code=target.label_key,
            detail_context=target.label_context,
        )
        try:
            result = run_export_job(
                item_context,
                artifacts=artifacts,
                media=media,
                source_id=source_id,
                target=target,
                fmt=export_format,
                destination_dir=destination,
            )
        except JobCancelled:
            _mark_unwritten(targets, state=state, record=record)
            raise
        except StemForgeError as exc:
            record(
                ExportBatchItemOutcome(
                    key=target.key,
                    label=target.label,
                    label_key=target.label_key,
                    label_context=target.label_context,
                    state=ExportBatchItemState.FAILED,
                    error=exc,
                )
            )
            state.finished.add(target.key)
            state.failures.append(exc)
            log.warning(
                "batch item %s failed code=%s detail=%s",
                target.key,
                exc.code,
                exc.detail or "",
            )
            if exc.code in errors.EXPORT_BATCH_ABORT_CODES:
                _mark_unwritten(targets, state=state, record=record)
                raise
            continue
        record(
            ExportBatchItemOutcome(
                key=target.key,
                label=target.label,
                label_key=target.label_key,
                label_context=target.label_context,
                state=ExportBatchItemState.WRITTEN,
                path=result.path,
            )
        )
        state.finished.add(target.key)
        written += 1
        ctx.progress(
            (index + 1) / total,
            stage=_stage(index, total),
            stage_code=STAGE_EXPORT_BATCH_ITEM,
            stage_context={"index": index + 1, "total": total},
            detail=target.label,
            detail_code=target.label_key,
            detail_context=target.label_context,
        )

    if state.failures:
        first = state.failures[0]
        raise StemForgeError(
            first.code,
            "the batch export did not complete every file",
            retryable=first.retryable,
            detail=first.detail,
            context={
                **dict(first.context),
                "failed": len(state.failures),
                "written": written,
                "total": total,
            },
        )

    return {
        "destination": str(destination),
        "format": export_format.value,
        "files": str(written),
    }


def _probe_targets(
    ctx: JobContext,
    *,
    artifacts: ArtifactStore,
    media: MediaTool,
    source_id: str,
    targets: Sequence[ExportTarget],
    record: RecordOutcome,
    state: _RunState,
) -> None:
    """Re-resolve and measure each target once for the free-space sum.

    Re-resolution at run time follows a fresh Separation instead of the paths
    the sheet saw, exactly like a single Export. A target that is already gone
    or unreadable is an item-level failure: the rest of the batch still runs,
    and the failed item's place in the plan stays visible in the results list.
    """
    for target in targets:
        ctx.cancel.check()
        try:
            resolved = resolve_export_target(
                artifacts,
                source_id=source_id,
                key=target.key,
                title=target.title,
            )
            if resolved is None:
                raise StemForgeError(
                    errors.MEDIA_PROBE_FAILED,
                    "the selected export target is no longer available",
                    detail="target",
                    context={"source_id": source_id, "target": target.key},
                )
            info = media.probe(resolved.path)
            if info.duration_s <= 0:
                raise StemForgeError(
                    errors.MEDIA_PROBE_FAILED,
                    "the selected export target has no measurable duration",
                    detail="duration",
                    context={"path": str(resolved.path)},
                )
        except StemForgeError as exc:
            record(
                ExportBatchItemOutcome(
                    key=target.key,
                    label=target.label,
                    label_key=target.label_key,
                    label_context=target.label_context,
                    state=ExportBatchItemState.FAILED,
                    error=exc,
                )
            )
            state.finished.add(target.key)
            state.failures.append(exc)
            continue
        state.durations[target.key] = info.duration_s


def _mark_unwritten(
    targets: Sequence[ExportTarget],
    *,
    state: _RunState,
    record: RecordOutcome,
) -> None:
    """Record every item without a final state as not exported."""
    for target in targets:
        if target.key in state.finished:
            continue
        record(
            ExportBatchItemOutcome(
                key=target.key,
                label=target.label,
                label_key=target.label_key,
                label_context=target.label_context,
                state=ExportBatchItemState.NOT_EXPORTED,
            )
        )


def _item_context(
    ctx: JobContext,
    *,
    index: int,
    total: int,
    label: str,
    label_key: str,
    label_context: Mapping[str, object],
) -> JobContext:
    """A per-item context that maps the item's progress into the batch's slice."""

    def update(progress: Progress) -> None:
        overall = (index + min(1.0, max(0.0, progress.fraction))) / total
        ctx.progress(
            overall,
            stage=_stage(index, total),
            stage_code=STAGE_EXPORT_BATCH_ITEM,
            stage_context={"index": index + 1, "total": total},
            detail=label,
            detail_code=label_key,
            detail_context=label_context,
        )

    return JobContext(
        job_id=ctx.job_id,
        kind=ctx.kind,
        cancel=ctx.cancel,
        log=ctx.log,
        update=update,
    )


def _stage(index: int, total: int) -> str:
    return f"{index + 1} of {total}"
