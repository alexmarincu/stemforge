"""Separation Job: verify the model, run the worker, validate, sum, and commit.

One function, :func:`run_separation_job`, owns everything a Separation produces
: it re-verifies the pinned weights before
spawning anything, runs the worker into ``staging/<job_id>/``, validates all five
outputs (the worker writes Instrumental in-process), measures one presentation gain, encodes the
previews, writes the manifest last, and promotes the directory with one atomic
rename. A cancel or failure removes the whole staging directory, so no partial
separation becomes visible and an earlier successful Stems set stays playable.

The worker writes all five outputs, including Instrumental as the float32 sum of
the non-vocal stems while they are still in memory. The parent validates every
WAV, measures one presentation gain, encodes the previews, writes the manifest,
and promotes the directory with one atomic rename. A cancel or failure removes
the whole staging directory, so no partial separation becomes visible and an
earlier successful Stems set stays playable.

The manifest records a block-plan fingerprint. A future engine, model, or block
change produces a different fingerprint, so a cache lookup can tell that an old
set was made by a different plan.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from stemforge import errors
from stemforge.artifacts import (
    ArtifactStore,
    SeparationArtifact,
    estimate_separation_bytes,
    free_space_requirement,
    require_free_space,
)
from stemforge.domain.display import role_label_key
from stemforge.domain.progress import (
    DETAIL_SEPARATION_CHECKING_WEIGHTS,
    DETAIL_SEPARATION_MEASURING_PEAK,
    DETAIL_SEPARATION_OUTPUTS_VALIDATED,
    DETAIL_SEPARATION_STARTING_WORKER,
    DETAIL_SEPARATION_WRITING_MANIFEST,
    STAGE_SEPARATION_DONE,
    STAGE_SEPARATION_GAIN,
    STAGE_SEPARATION_PREVIEWS,
    STAGE_SEPARATION_SAVING,
    STAGE_SEPARATION_SEPARATE,
    STAGE_SEPARATION_VALIDATE,
    STAGE_SEPARATION_VERIFY,
    ProgressDetail,
)
from stemforge.domain.roles import INSTRUMENTAL_ROLE, OUTPUT_ROLES, STEM_ROLES
from stemforge.engines.media import (
    CANONICAL_CHANNELS,
    CANONICAL_CODEC,
    CANONICAL_SAMPLE_RATE,
    DURATION_TOLERANCE_S,
    MediaTool,
)
from stemforge.engines.separation import (
    DEFAULT_BLOCK_SECONDS,
    DEFAULT_CROSSFADE_SECONDS,
    DEFAULT_OVERLAP,
    DEFAULT_SHIFTS,
    DEMUCS_VERSION,
    ProgressSink,
    SeparationEngine,
)
from stemforge.errors import StemForgeError
from stemforge.jobs.job import JobContext
from stemforge.models import ModelStore
from stemforge.models.store import sha256_file
from stemforge.pipeline.gain import max_peak_dbfs, presentation_gain
from stemforge.worker_protocol import block_count

__all__ = [
    "INSTRUMENTAL_ROLE",
    "STAGE_DONE",
    "STAGE_GAIN",
    "STAGE_PREVIEWS",
    "STAGE_SAVING",
    "STAGE_SEPARATE",
    "STAGE_VALIDATE",
    "STAGE_VERIFY",
    "STEM_ROLES",
    "SeparationSet",
    "block_plan",
    "run_separation_job",
]

# The four Stems are re-exported here for callers that predate domain.roles;
# a Separation's full output set lives in ``OUTPUT_ROLES`` (Instrumental is an
# output, never a Stem).
STAGE_VERIFY: Final = "Verify"
STAGE_SEPARATE: Final = "Separate"
STAGE_VALIDATE: Final = "Validate"
STAGE_GAIN: Final = "Gain"
STAGE_PREVIEWS: Final = "Previews"
STAGE_SAVING: Final = "Saving"
STAGE_DONE: Final = "Done"

_STAGE_CODE_BY_TEXT: Final[Mapping[str, str]] = {
    STAGE_VERIFY: STAGE_SEPARATION_VERIFY,
    STAGE_SEPARATE: STAGE_SEPARATION_SEPARATE,
    STAGE_VALIDATE: STAGE_SEPARATION_VALIDATE,
    STAGE_GAIN: STAGE_SEPARATION_GAIN,
    STAGE_PREVIEWS: STAGE_SEPARATION_PREVIEWS,
    STAGE_SAVING: STAGE_SEPARATION_SAVING,
    STAGE_DONE: STAGE_SEPARATION_DONE,
}

ENGINE_NAME: Final = "demucs"
DEVICE: Final = "cpu"

# The worker owns 2%..88%; the rest is validation, gain, and previews.
SEPARATE_START: Final = 0.02
SEPARATE_SPAN: Final = 0.86
VALIDATE_END: Final = 0.90
GAIN_END: Final = 0.94
PREVIEWS_END: Final = 0.99


@dataclass(frozen=True, slots=True)
class SeparationSet:
    """What one Separation produced, resolved for the UI and exporters."""

    artifact: SeparationArtifact
    stems: Mapping[str, Path]
    instrumental: Path
    previews: Mapping[str, Path]
    gain_db: float
    block_plan: Mapping[str, object]

    def master(self, role: str) -> Path | None:
        if role == INSTRUMENTAL_ROLE:
            return self.instrumental
        return self.stems.get(role)

    def preview(self, role: str) -> Path | None:
        return self.previews.get(role)


def run_separation_job(
    ctx: JobContext,
    *,
    artifacts: ArtifactStore,
    engine: SeparationEngine,
    media: MediaTool,
    models: ModelStore,
    source_id: str,
    threads: int | None = None,
    title: str | None = None,
    artist: str | None = None,
) -> SeparationSet:
    """Separate one committed Source and return the promoted stem set.

    ``models.require_verified()`` re-hashes the weights before the worker is
    spawned; a missing or tampered cache raises ``separation.model_missing`` and
    no staging directory is created. A cancel or any failure discards
    ``staging/<job_id>/``.
    """
    cancel = ctx.cancel
    log = ctx.log
    emit = _context_progress(ctx)
    cancel.check()
    emit(
        0.0,
        STAGE_VERIFY,
        ProgressDetail("checking the model weights", code=DETAIL_SEPARATION_CHECKING_WEIGHTS),
    )

    source = artifacts.find_source(source_id, verify_hashes=True)
    if source is None:
        raise ValueError(f"source is not committed: {source_id}")
    canonical = source.canonical_path
    if canonical is None:
        raise ValueError(f"source has no canonical audio: {source_id}")

    status = models.require_verified()
    spec = models.spec
    if not status.verified:
        # require_verified raises; this is the defensive branch for a custom store.
        raise StemForgeError(
            errors.SEPARATION_MODEL_MISSING,
            "the separation model is not installed",
            detail=f"reason={status.reason or 'absent'}",
        )

    info = media.probe(canonical)
    expected_duration = info.duration_s
    if expected_duration <= 0:
        raise StemForgeError(
            errors.SEPARATION_ENGINE_ERROR,
            "the source has no measurable duration",
            detail="reason=duration",
            context={"path": str(canonical)},
        )
    estimate = estimate_separation_bytes(expected_duration)
    require_free_space(artifacts.root, free_space_requirement(estimate))
    # Reserve the set's cache footprint before it is written.
    artifacts.enforce_cap(estimate)
    plan = block_plan(
        duration_s=expected_duration,
        block_seconds=engine_block_seconds(engine),
        crossfade_seconds=engine_crossfade_seconds(engine),
        overlap=engine_overlap(engine),
        shifts=engine_shifts(engine),
        model=spec.name,
        model_revision=spec.revision,
        weights_sha256=spec.sha256,
        source_sha256=sha256_file(canonical),
    )
    cancel.check()

    staging = artifacts.stage(ctx.job_id)
    committed = False
    try:
        emit(
            SEPARATE_START,
            STAGE_SEPARATE,
            ProgressDetail("starting the worker", code=DETAIL_SEPARATION_STARTING_WORKER),
        )
        outputs = engine.separate(
            canonical,
            staging,
            model_path=status.path,
            expected_duration_s=expected_duration,
            threads=threads,
            model_revision=spec.revision,
            env=_worker_environment(models, threads=threads),
            progress=_scaled(
                emit,
                start=SEPARATE_START,
                span=SEPARATE_SPAN,
                stage=STAGE_SEPARATE,
            ),
            cancel=cancel,
            log=log,
        )
        cancel.check()

        masters: dict[str, Path] = {}
        for role in OUTPUT_ROLES:
            path = outputs.path_for(role)
            if path is None:
                raise StemForgeError(
                    errors.SEPARATION_OUTPUT_INVALID,
                    "the separation is missing an output",
                    detail=f"missing={role}",
                    context={"role": role, "source_id": source_id},
                )
            _validate_wav(
                media,
                path,
                role=role,
                expected_duration=expected_duration,
                code=errors.SEPARATION_OUTPUT_INVALID,
            )
            masters[role] = path
        emit(
            VALIDATE_END,
            STAGE_VALIDATE,
            ProgressDetail("five outputs validated", code=DETAIL_SEPARATION_OUTPUTS_VALIDATED),
        )
        cancel.check()

        emit(
            VALIDATE_END,
            STAGE_GAIN,
            ProgressDetail("measuring the set peak", code=DETAIL_SEPARATION_MEASURING_PEAK),
        )
        peaks: dict[str, float] = {}
        for role, path in masters.items():
            cancel.check()
            peaks[role] = media.peak_dbfs(path, cancel=cancel, log=log)
        gain_db = presentation_gain(peaks.values())
        emit(GAIN_END, STAGE_GAIN, "")
        cancel.check()

        emit(GAIN_END, STAGE_PREVIEWS, "")
        preview_title, preview_artist = _source_tags(source.manifest, title, artist)
        files: list[Mapping[str, object]] = []
        previews: dict[str, Path] = {}
        preview_roles = [*STEM_ROLES, INSTRUMENTAL_ROLE]
        for index, role in enumerate(preview_roles):
            cancel.check()
            preview = staging / f"{role}.mp3"
            media.encode_preview(
                masters[role],
                preview,
                gain_db=gain_db,
                title=preview_title,
                artist=preview_artist,
                cancel=cancel,
                log=log,
            )
            previews[role] = preview
            files.append(_file_entry(masters[role], role))
            files.append(_file_entry(preview, f"{role}_preview"))
            emit(
                GAIN_END + (PREVIEWS_END - GAIN_END) * (index + 1) / len(preview_roles),
                STAGE_PREVIEWS,
                ProgressDetail(role, code=role_label_key(role)),
            )

        manifest = _manifest(
            source_id=source_id,
            spec_name=spec.name,
            spec_revision=spec.revision,
            weights_sha256=spec.sha256,
            plan=plan,
            gain_db=gain_db,
            peaks=peaks,
            files=files,
        )
        emit(
            PREVIEWS_END,
            STAGE_SAVING,
            ProgressDetail("writing the manifest", code=DETAIL_SEPARATION_WRITING_MANIFEST),
        )
        artifact = artifacts.commit_separation(staging, source_id, manifest)
        committed = True
        emit(1.0, STAGE_DONE, "")
        committed_stems = {role: _committed_file(artifact, role) for role in STEM_ROLES}
        committed_instrumental = _committed_file(artifact, INSTRUMENTAL_ROLE)
        committed_previews = {
            role: _committed_file(artifact, f"{role}_preview")
            for role in (*STEM_ROLES, INSTRUMENTAL_ROLE)
        }
        return SeparationSet(
            artifact=artifact,
            stems=committed_stems,
            instrumental=committed_instrumental,
            previews=committed_previews,
            gain_db=gain_db,
            block_plan=plan,
        )
    except BaseException:
        if not committed:
            try:
                artifacts.discard_staging(ctx.job_id)
            except Exception:
                log.warning("could not discard staging for %s", ctx.job_id, exc_info=True)
        raise


# ---------------------------------------------------------------------------
# Block plan and manifest
# ---------------------------------------------------------------------------


def block_plan(
    *,
    duration_s: float,
    block_seconds: float = DEFAULT_BLOCK_SECONDS,
    crossfade_seconds: float = DEFAULT_CROSSFADE_SECONDS,
    overlap: float = DEFAULT_OVERLAP,
    shifts: int = DEFAULT_SHIFTS,
    model: str = "htdemucs",
    model_revision: str = "",
    weights_sha256: str = "",
    source_sha256: str = "",
) -> dict[str, object]:
    """The deterministic block plan recorded in the separation manifest."""
    if duration_s <= 0:
        raise ValueError("duration_s must be positive")
    if block_seconds <= 0 or crossfade_seconds < 0 or crossfade_seconds >= block_seconds:
        raise ValueError("invalid block or crossfade length")
    block = round(block_seconds * CANONICAL_SAMPLE_RATE)
    fade = round(crossfade_seconds * CANONICAL_SAMPLE_RATE)
    total = round(duration_s * CANONICAL_SAMPLE_RATE)
    blocks = block_count(total_frames=total, block_frames=block, fade_frames=fade)
    payload: dict[str, object] = {
        "engine": ENGINE_NAME,
        "engine_version": DEMUCS_VERSION,
        "model": model,
        "model_revision": model_revision,
        "weights_sha256": weights_sha256,
        "source_sha256": source_sha256,
        "sample_rate": CANONICAL_SAMPLE_RATE,
        "channels": CANONICAL_CHANNELS,
        "block_seconds": block_seconds,
        "crossfade_seconds": crossfade_seconds,
        "overlap": overlap,
        "shifts": shifts,
        "blocks": blocks,
    }
    payload["fingerprint"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


def engine_block_seconds(engine: object) -> float:
    return float(getattr(engine, "block_seconds", DEFAULT_BLOCK_SECONDS))


def engine_crossfade_seconds(engine: object) -> float:
    return float(getattr(engine, "crossfade_seconds", DEFAULT_CROSSFADE_SECONDS))


def engine_overlap(engine: object) -> float:
    return float(getattr(engine, "overlap", DEFAULT_OVERLAP))


def engine_shifts(engine: object) -> int:
    return int(getattr(engine, "shifts", DEFAULT_SHIFTS))


def _manifest(
    *,
    source_id: str,
    spec_name: str,
    spec_revision: str,
    weights_sha256: str,
    plan: Mapping[str, object],
    gain_db: float,
    peaks: Mapping[str, float],
    files: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    return {
        "kind": "separation",
        "source_id": source_id,
        "engine": ENGINE_NAME,
        "engine_version": DEMUCS_VERSION,
        "model": spec_name,
        "model_revision": spec_revision,
        "weights_sha256": weights_sha256,
        "device": DEVICE,
        "sample_rate": CANONICAL_SAMPLE_RATE,
        "channels": CANONICAL_CHANNELS,
        "block_plan": dict(plan),
        "block_plan_fingerprint": plan["fingerprint"],
        "instrumental": "float32 sum of drums + bass + other, written by the worker",
        "gain_db": gain_db,
        "peak_dbfs": max_peak_dbfs(peaks.values()),
        "validation": {
            "ok": True,
            "codec": CANONICAL_CODEC,
            "sample_rate": CANONICAL_SAMPLE_RATE,
            "channels": CANONICAL_CHANNELS,
            "duration_tolerance_s": DURATION_TOLERANCE_S,
        },
        "files": list(files),
    }


def _validate_wav(
    media: MediaTool,
    path: Path,
    *,
    role: str,
    expected_duration: float,
    code: str,
) -> None:
    try:
        info = media.probe(path)
    except StemForgeError as exc:
        raise StemForgeError(
            code,
            "the separation produced an unreadable file",
            retryable=True,
            detail=f"{role}: {exc.code}",
            context={"role": role, "path": str(path)},
        ) from exc
    problems: list[str] = []
    if info.codec != CANONICAL_CODEC:
        problems.append(f"codec={info.codec}")
    if info.sample_rate != CANONICAL_SAMPLE_RATE:
        problems.append(f"sample_rate={info.sample_rate}")
    if info.channels != CANONICAL_CHANNELS:
        problems.append(f"channels={info.channels}")
    if abs(info.duration_s - expected_duration) > DURATION_TOLERANCE_S:
        problems.append(f"duration={info.duration_s:.3f}")
    if problems:
        raise StemForgeError(
            code,
            "the separation produced an invalid file",
            retryable=True,
            detail=f"{role}: {', '.join(problems)}",
            context={"role": role, "path": str(path), "problems": problems},
        )


def _worker_environment(models: ModelStore, *, threads: int | None) -> dict[str, str]:
    resolved = max(1, (os.cpu_count() or 2) // 2) if threads is None else max(1, int(threads))
    environment = dict(os.environ)
    environment.update(
        {
            "HF_HOME": str(models.root),
            "HF_HUB_OFFLINE": "1",
            "OMP_NUM_THREADS": str(resolved),
            "MKL_NUM_THREADS": str(resolved),
            "PYTHONUNBUFFERED": "1",
            "PYTHONUTF8": "1",
            "STEMFORGE_WORKER_STDIN_WATCH": "1",
        }
    )
    return environment


def _committed_file(artifact: SeparationArtifact, role: str) -> Path:
    path = artifact.path_for(role)
    if path is None:  # pragma: no cover - the store validated the manifest
        raise StemForgeError(
            errors.INTERNAL_UNEXPECTED,
            "the committed separation is missing a file",
            detail=f"role={role}",
            context={"role": role},
        )
    return path


def _context_progress(ctx: JobContext) -> ProgressSink:
    def sink(fraction: float, stage: str, detail: str) -> None:
        coded = detail if isinstance(detail, ProgressDetail) else None
        ctx.progress(
            min(1.0, max(0.0, fraction)),
            stage=stage,
            stage_code=_STAGE_CODE_BY_TEXT.get(stage),
            detail=detail or None,
            detail_code=coded.code if coded is not None else None,
            detail_context=coded.context if coded is not None else None,
        )

    return sink


def _scaled(
    sink: ProgressSink,
    *,
    start: float,
    span: float,
    stage: str,
) -> ProgressSink:
    def scaled(fraction: float, _stage: str, detail: str) -> None:
        sink(min(1.0, max(0.0, start + span * fraction)), stage, detail)

    return scaled


def _source_tags(
    manifest: Mapping[str, object], title: str | None, artist: str | None
) -> tuple[str, str]:
    metadata = manifest.get("metadata")
    values = metadata if isinstance(metadata, dict) else {}
    resolved_title = title if title is not None else _text(values.get("title"))
    resolved_artist = artist if artist is not None else _text(values.get("artist"))
    return resolved_title, resolved_artist


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _file_entry(path: Path, role: str) -> Mapping[str, object]:
    return {
        "path": path.name,
        "role": role,
        "size": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
