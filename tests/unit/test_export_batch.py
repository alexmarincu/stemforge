"""The batch plan and the batch Job loop: order, outcomes, abort, and cancel."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from stemforge import errors
from stemforge.domain.cancellation import CancelToken
from stemforge.domain.models import ExportBatchItemState, JobKind
from stemforge.domain.progress import (
    STAGE_EXPORT_BATCH_ITEM,
    Progress,
)
from stemforge.engines.media import ExportFormat, MediaInfo
from stemforge.errors import JobCancelled, StemForgeError
from stemforge.jobs.job import JobContext
from stemforge.pipeline import export_batch_job
from stemforge.pipeline.export_batch_job import (
    ExportBatchItemOutcome,
    batch_targets,
    run_export_batch_job,
)
from stemforge.pipeline.export_job import ExportTarget

pytestmark = pytest.mark.unit

LOG = logging.getLogger("test.export-batch")
SOURCE_ID = "src-batch"
EXPORTS = Path("/exports")


def _target(
    key: str,
    role: str,
    *,
    scope: str = "separation",
    render_key: str | None = None,
) -> ExportTarget:
    return ExportTarget(
        source_id=SOURCE_ID,
        key=key,
        label=role.title(),
        role=role,
        path=Path("/cache") / f"{key.replace(':', '-')}.wav",
        gain_db=0.0,
        semitones=0,
        scope=scope,
        render_key=render_key,
        title="Song",
        label_key=f"role.{role}",
        label_context={},
    )


def _full_targets() -> tuple[ExportTarget, ...]:
    return (
        _target("master", "original", scope="master"),
        _target("stem:vocals", "vocals"),
        _target("stem:drums", "drums"),
        _target("stem:bass", "bass"),
        _target("stem:other", "other"),
        _target("instrumental", "instrumental"),
        _target("render:r1:vocals", "vocals", scope="stems", render_key="r1"),
        _target("render:r1:instrumental", "instrumental", scope="stems", render_key="r1"),
        _target("render:r1:drums", "drums", scope="stems", render_key="r1"),
    )


def test_the_original_version_batches_only_the_separation_outputs_in_card_order() -> None:
    targets = batch_targets(_full_targets(), version_key="original")

    assert [target.key for target in targets] == [
        "stem:vocals",
        "instrumental",
        "stem:drums",
        "stem:bass",
        "stem:other",
    ]


def test_a_render_version_batches_only_that_set_in_card_order() -> None:
    targets = batch_targets(_full_targets(), version_key="r1")

    assert [target.key for target in targets] == [
        "render:r1:vocals",
        "render:r1:instrumental",
        "render:r1:drums",
    ]


def test_an_original_scope_render_batches_its_one_output() -> None:
    render = _target("render:r2:original", "original", scope="original", render_key="r2")

    targets = batch_targets((*_full_targets(), render), version_key="r2")

    assert [target.key for target in targets] == ["render:r2:original"]


def test_a_missing_role_is_simply_absent() -> None:
    targets = batch_targets(
        (_target("stem:vocals", "vocals"), _target("instrumental", "instrumental")),
        version_key="original",
    )

    assert [target.key for target in targets] == ["stem:vocals", "instrumental"]


class ProbeMedia:
    """A probe-only adapter; the encoder is monkeypatched out per test."""

    def __init__(self, *, duration_s: float = 60.0, fail: Sequence[str] = ()) -> None:
        self.duration_s = duration_s
        self.fail = set(fail)
        self.probed: list[Path] = []

    def probe(self, path: Path) -> MediaInfo:
        self.probed.append(path)
        if path.name in self.fail:
            raise StemForgeError(errors.MEDIA_PROBE_FAILED, "probe failed")
        return MediaInfo(
            codec="pcm_s24le",
            sample_rate=44_100,
            channels=2,
            duration_s=self.duration_s,
            format="wav",
            tags={},
            bit_depth=24,
        )


@dataclass(frozen=True)
class FakeResult:
    path: Path


def _context(
    events: list[tuple[float, str | None, str | None]] | None = None,
    *,
    cancel: CancelToken | None = None,
    updates: list[Progress] | None = None,
) -> JobContext:
    def update(progress) -> None:
        if events is not None:
            events.append((progress.fraction, progress.stage, progress.detail))
        if updates is not None:
            updates.append(progress)

    return JobContext(
        job_id="job-batch",
        kind=JobKind.EXPORT_BATCH,
        cancel=cancel if cancel is not None else CancelToken(),
        log=LOG,
        update=update,
    )


def _run(
    monkeypatch: pytest.MonkeyPatch,
    targets: Sequence[ExportTarget],
    *,
    media: ProbeMedia | None = None,
    behavior=None,
    resolve=None,
    events: list[tuple[float, str | None, str | None]] | None = None,
    cancel: CancelToken | None = None,
    updates: list[Progress] | None = None,
) -> tuple[dict[str, str] | None, list[ExportBatchItemOutcome], BaseException | None]:
    recorded: list[ExportBatchItemOutcome] = []

    def default_resolve(artifacts, *, source_id, key, title=None):
        del artifacts, source_id, title
        return next((target for target in targets if target.key == key), None)

    def fake_run(ctx: JobContext, **kwargs: object) -> FakeResult:
        target = kwargs["target"]
        assert isinstance(target, ExportTarget)
        if behavior is not None:
            behavior(target, ctx)
        return FakeResult(path=EXPORTS / f"{target.key}.wav")

    monkeypatch.setattr(export_batch_job, "resolve_export_target", resolve or default_resolve)
    monkeypatch.setattr(export_batch_job, "run_export_job", fake_run)
    result: dict[str, str] | None = None
    error: BaseException | None = None
    try:
        result = run_export_batch_job(
            _context(events, cancel=cancel, updates=updates),
            artifacts=object(),  # type: ignore[arg-type]
            media=(media if media is not None else ProbeMedia()),  # type: ignore[arg-type]
            source_id=SOURCE_ID,
            targets=tuple(targets),
            fmt=ExportFormat.WAV24,
            destination_dir=EXPORTS,
            record=recorded.append,
        )
    except BaseException as exc:
        error = exc
    return result, recorded, error


def _states(recorded: Sequence[ExportBatchItemOutcome]) -> dict[str, ExportBatchItemState]:
    return {item.key: item.state for item in recorded}


def test_a_full_batch_records_every_written_item_in_plan_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets = batch_targets(_full_targets(), version_key="original")
    events: list[tuple[float, str | None, str | None]] = []

    result, recorded, error = _run(monkeypatch, targets, events=events)

    assert error is None
    assert result is not None
    assert _states(recorded) == {
        "stem:vocals": ExportBatchItemState.WRITTEN,
        "instrumental": ExportBatchItemState.WRITTEN,
        "stem:drums": ExportBatchItemState.WRITTEN,
        "stem:bass": ExportBatchItemState.WRITTEN,
        "stem:other": ExportBatchItemState.WRITTEN,
    }
    assert [item.key for item in recorded] == [target.key for target in targets]
    assert result["files"] == "5"
    assert events[-1][0] == 1.0
    assert {stage for _, stage, _ in events} >= {"1 of 5", "5 of 5"}
    assert {detail for _, _, detail in events} == {
        "Vocals",
        "Instrumental",
        "Drums",
        "Bass",
        "Other",
    }
    assert [item.label_key for item in recorded] == [
        "role.vocals",
        "role.instrumental",
        "role.drums",
        "role.bass",
        "role.other",
    ]


def test_batch_progress_carries_the_stage_code_and_target_label_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets = batch_targets(_full_targets(), version_key="original")
    updates: list[Progress] = []

    _result, _recorded, error = _run(monkeypatch, targets, updates=updates)

    assert error is None
    item_updates = [update for update in updates if update.stage_code == STAGE_EXPORT_BATCH_ITEM]
    assert item_updates
    first = next(update for update in item_updates if update.stage_context["index"] == 1)
    assert first.stage == "1 of 5"
    assert dict(first.stage_context) == {"index": 1, "total": 5}
    assert first.detail == "Vocals"
    assert first.detail_code == "role.vocals"
    assert dict(first.detail_context) == {}


def test_an_item_level_failure_continues_and_the_batch_then_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets = batch_targets(_full_targets(), version_key="original")

    def behavior(target: ExportTarget, ctx: JobContext) -> None:
        del ctx
        if target.key == "stem:drums":
            raise StemForgeError(
                errors.MEDIA_ENCODE_FAILED,
                "fake encoder failure",
                retryable=True,
                detail="stderr",
            )

    _result, recorded, error = _run(monkeypatch, targets, behavior=behavior)

    assert isinstance(error, StemForgeError)
    assert error.code == errors.MEDIA_ENCODE_FAILED
    assert error.retryable is True
    assert error.detail == "stderr"
    assert error.context["failed"] == 1
    assert error.context["written"] == 4
    assert error.context["total"] == 5
    assert _states(recorded) == {
        "stem:vocals": ExportBatchItemState.WRITTEN,
        "instrumental": ExportBatchItemState.WRITTEN,
        "stem:drums": ExportBatchItemState.FAILED,
        "stem:bass": ExportBatchItemState.WRITTEN,
        "stem:other": ExportBatchItemState.WRITTEN,
    }


def test_a_destination_failure_stops_the_batch_and_marks_the_rest_not_exported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets = batch_targets(_full_targets(), version_key="original")

    def behavior(target: ExportTarget, ctx: JobContext) -> None:
        del ctx
        if target.key == "instrumental":
            raise StemForgeError(
                errors.EXPORT_WRITE_FAILED,
                "fake write failure",
                retryable=True,
                detail="permission",
            )

    _result, recorded, error = _run(monkeypatch, targets, behavior=behavior)

    assert isinstance(error, StemForgeError)
    assert error.code == errors.EXPORT_WRITE_FAILED
    assert _states(recorded) == {
        "stem:vocals": ExportBatchItemState.WRITTEN,
        "instrumental": ExportBatchItemState.FAILED,
        "stem:drums": ExportBatchItemState.NOT_EXPORTED,
        "stem:bass": ExportBatchItemState.NOT_EXPORTED,
        "stem:other": ExportBatchItemState.NOT_EXPORTED,
    }


def test_a_cancel_marks_the_current_and_remaining_items_not_exported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets = batch_targets(_full_targets(), version_key="original")
    cancel = CancelToken()

    def behavior(target: ExportTarget, ctx: JobContext) -> None:
        if target.key == "stem:drums":
            ctx.cancel.cancel()
            raise JobCancelled()

    _result, recorded, error = _run(
        monkeypatch,
        targets,
        behavior=behavior,
        cancel=cancel,
    )

    assert isinstance(error, JobCancelled)
    assert _states(recorded) == {
        "stem:vocals": ExportBatchItemState.WRITTEN,
        "instrumental": ExportBatchItemState.WRITTEN,
        "stem:drums": ExportBatchItemState.NOT_EXPORTED,
        "stem:bass": ExportBatchItemState.NOT_EXPORTED,
        "stem:other": ExportBatchItemState.NOT_EXPORTED,
    }


def test_the_up_front_space_check_sums_the_set_once_before_any_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets = batch_targets(_full_targets(), version_key="original")
    requested: list[int] = []

    def require_space(path: Path, needed: int, *, floor: int = 0) -> None:
        del path, floor
        requested.append(needed)

    monkeypatch.setattr(export_batch_job, "require_free_space", require_space)

    _result, recorded, error = _run(
        monkeypatch,
        targets,
        media=ProbeMedia(duration_s=60.0),
    )

    assert error is None
    assert len(requested) == 1
    assert requested[0] == export_batch_job.required_export_space(300.0)
    assert all(item.state is ExportBatchItemState.WRITTEN for item in recorded)


def test_a_full_destination_records_nothing_written_and_marks_everything_not_exported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets = batch_targets(_full_targets(), version_key="original")

    def refuse(path: Path, needed: int, *, floor: int = 0) -> None:
        del path, needed, floor
        raise StemForgeError(errors.STORAGE_DISK_FULL, "no space", retryable=True)

    monkeypatch.setattr(export_batch_job, "require_free_space", refuse)

    _result, recorded, error = _run(monkeypatch, targets)

    assert isinstance(error, StemForgeError)
    assert error.code == errors.STORAGE_DISK_FULL
    assert _states(recorded) == {
        "stem:vocals": ExportBatchItemState.NOT_EXPORTED,
        "instrumental": ExportBatchItemState.NOT_EXPORTED,
        "stem:drums": ExportBatchItemState.NOT_EXPORTED,
        "stem:bass": ExportBatchItemState.NOT_EXPORTED,
        "stem:other": ExportBatchItemState.NOT_EXPORTED,
    }


def test_a_probe_failure_is_item_level_and_the_rest_still_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets = batch_targets(_full_targets(), version_key="original")
    media = ProbeMedia(fail={"instrumental.wav"})

    _result, recorded, error = _run(monkeypatch, targets, media=media)

    assert isinstance(error, StemForgeError)
    assert error.code == errors.MEDIA_PROBE_FAILED
    assert error.context["written"] == 4
    assert error.context["failed"] == 1
    assert _states(recorded)["instrumental"] is ExportBatchItemState.FAILED


def test_a_fresh_separation_is_re_resolved_before_the_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets = batch_targets(_full_targets(), version_key="original")
    new_path = Path("/cache/new-vocals.wav")

    def resolve(artifacts, *, source_id, key, title=None):
        del artifacts, source_id, title
        target = next(item for item in targets if item.key == key)
        return replace(target, path=new_path) if key == "stem:vocals" else target

    media = ProbeMedia()
    _result, recorded, error = _run(
        monkeypatch,
        targets,
        media=media,
        resolve=resolve,
    )

    assert error is None
    assert new_path in media.probed
    assert _states(recorded)["stem:vocals"] is ExportBatchItemState.WRITTEN


def test_a_target_that_vanished_before_the_run_is_item_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets = batch_targets(_full_targets(), version_key="original")

    def resolve(artifacts, *, source_id, key, title=None):
        del artifacts, source_id, title
        if key == "stem:drums":
            return None
        return next(item for item in targets if item.key == key)

    _result, recorded, error = _run(monkeypatch, targets, resolve=resolve)

    assert isinstance(error, StemForgeError)
    assert error.code == errors.MEDIA_PROBE_FAILED
    assert error.context["written"] == 4
    assert error.context["failed"] == 1
    assert _states(recorded)["stem:drums"] is ExportBatchItemState.FAILED
    assert _states(recorded)["stem:vocals"] is ExportBatchItemState.WRITTEN


def test_an_empty_plan_is_a_coded_internal_failure() -> None:
    with pytest.raises(StemForgeError) as raised:
        run_export_batch_job(
            _context(),
            artifacts=object(),  # type: ignore[arg-type]
            media=ProbeMedia(),  # type: ignore[arg-type]
            source_id=SOURCE_ID,
            targets=(),
            fmt=ExportFormat.WAV24,
            destination_dir=EXPORTS,
            record=lambda item: None,
        )

    assert raised.value.code == errors.INTERNAL_UNEXPECTED
    assert raised.value.detail == "target"
