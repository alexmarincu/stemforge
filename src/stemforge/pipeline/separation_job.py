"""Separation Job: run one method's worker, validate, gain, preview, and commit.

:func:`run_separation_job` resolves the requested Separation method and owns
everything it produces. A method that requires Model weights re-verifies the
pinned weights before spawning anything and records its block plan; a method that
does not (Karaoke) never touches the model store. The worker runs into
``staging/<job_id>/``, the parent validates exactly the method's declared output
roles, measures one presentation gain across them, encodes their previews, writes
the manifest last, and promotes the directory with one atomic rename. A cancel or
failure removes the whole staging directory, so no partial separation becomes
visible and an earlier successful set stays playable.

The manifest records ``method`` and ``method_version`` at schema version 1. A
method's engine identity, validation, files, gain, and peak are always present;
the Demucs model identity, Instrumental description, and block-plan fingerprint
are recorded only for a method that used them.
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
from stemforge.domain.methods import DEFAULT_METHOD_ID, SeparationMethod, method_by_id
from stemforge.domain.roles import INSTRUMENTAL_ROLE
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
    "SeparationSet",
    "block_plan",
    "run_separation_job",
]

STAGE_VERIFY: Final = "Verify"
STAGE_SEPARATE: Final = "Separate"
STAGE_VALIDATE: Final = "Validate"
STAGE_GAIN: Final = "Gain"
STAGE_PREVIEWS: Final = "Previews"
STAGE_SAVING: Final = "Saving"
STAGE_DONE: Final = "Done"

DEVICE: Final = "cpu"

# The worker owns 2%..88%; the rest is validation, gain, and previews.
SEPARATE_START: Final = 0.02
SEPARATE_SPAN: Final = 0.86
VALIDATE_END: Final = 0.90
GAIN_END: Final = 0.94
PREVIEWS_END: Final = 0.99


@dataclass(frozen=True, slots=True)
class SeparationSet:
    """What one Separation produced, resolved for the UI and exporters.

    ``outputs`` carries every declared role, so a method with fewer than five
    outputs needs no special case. ``stems`` excludes Instrumental (GLOSSARY).
    """

    artifact: SeparationArtifact
    outputs: Mapping[str, Path]
    previews: Mapping[str, Path]
    gain_db: float
    method: str
    method_version: int
    block_plan: Mapping[str, object] | None = None

    def master(self, role: str) -> Path | None:
        return self.outputs.get(role)

    def preview(self, role: str) -> Path | None:
        return self.previews.get(role)

    @property
    def stems(self) -> Mapping[str, Path]:
        descriptor = method_by_id(self.method)
        if descriptor is None:
            return {}
        return {role: path for role, path in self.outputs.items() if role in descriptor.stems}

    @property
    def instrumental(self) -> Path | None:
        return self.outputs.get(INSTRUMENTAL_ROLE)


def run_separation_job(
    ctx: JobContext,
    *,
    artifacts: ArtifactStore,
    engine: SeparationEngine,
    media: MediaTool,
    models: ModelStore,
    source_id: str,
    method: str = DEFAULT_METHOD_ID,
    threads: int | None = None,
    title: str | None = None,
    artist: str | None = None,
) -> SeparationSet:
    """Separate one committed Source with one method and return its set.

    A model-requiring method re-hashes the weights before the worker is spawned;
    a missing or tampered cache raises ``separation.model_missing`` and no
    staging directory is created. A method without weights never reads the model
    store. A cancel or any failure discards ``staging/<job_id>/``.
    """
    descriptor = _method_descriptor(method)
    cancel = ctx.cancel
    log = ctx.log
    emit = _context_progress(ctx)
    cancel.check()
    emit(0.0, STAGE_VERIFY, "checking the separation method")

    source = artifacts.find_source(source_id, verify_hashes=True)
    if source is None:
        raise ValueError(f"source is not committed: {source_id}")
    canonical = source.canonical_path
    if canonical is None:
        raise ValueError(f"source has no canonical audio: {source_id}")

    status = None
    spec = None
    plan: dict[str, object] | None = None
    if descriptor.requires_model:
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
    estimate = estimate_separation_bytes(expected_duration, outputs=descriptor.output_count)
    require_free_space(artifacts.root, free_space_requirement(estimate))
    # Reserve the set's cache footprint before it is written.
    artifacts.enforce_cap(estimate)
    if descriptor.requires_model:
        assert spec is not None
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
        emit(SEPARATE_START, STAGE_SEPARATE, "starting the worker")
        outputs = engine.separate(
            canonical,
            staging,
            method=descriptor.id,
            expected_outputs=descriptor.output_roles,
            model_path=status.path if status is not None else None,
            expected_duration_s=expected_duration,
            threads=threads,
            model_revision=spec.revision if spec is not None else "",
            env=_worker_environment(models if descriptor.requires_model else None, threads=threads),
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

        produced = set(outputs.outputs)
        expected = set(descriptor.output_roles)
        if produced != expected:
            missing = sorted(expected - produced)
            extra = sorted(produced - expected)
            raise StemForgeError(
                errors.SEPARATION_OUTPUT_INVALID,
                "the separation did not match the method's declared outputs",
                detail=f"missing={','.join(missing)} extra={','.join(extra)}",
                context={"missing": missing, "extra": extra, "source_id": source_id},
            )
        masters: dict[str, Path] = {}
        for role in descriptor.output_roles:
            path = outputs.path_for(role)
            assert path is not None
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
            f"{len(descriptor.output_roles)} outputs validated",
        )
        cancel.check()

        emit(VALIDATE_END, STAGE_GAIN, "measuring the set peak")
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
        for index, role in enumerate(descriptor.output_roles):
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
                GAIN_END + (PREVIEWS_END - GAIN_END) * (index + 1) / len(descriptor.output_roles),
                STAGE_PREVIEWS,
                role,
            )

        manifest = _manifest(
            descriptor=descriptor,
            source_id=source_id,
            spec_name=spec.name if spec is not None else "",
            spec_revision=spec.revision if spec is not None else "",
            weights_sha256=spec.sha256 if spec is not None else "",
            plan=plan,
            gain_db=gain_db,
            peaks=peaks,
            files=files,
        )
        emit(PREVIEWS_END, STAGE_SAVING, "writing the manifest")
        artifact = artifacts.commit_separation(staging, source_id, manifest, method=descriptor.id)
        committed = True
        emit(1.0, STAGE_DONE, "")
        committed_outputs = {
            role: _committed_file(artifact, role) for role in descriptor.output_roles
        }
        committed_previews = {
            role: _committed_file(artifact, f"{role}_preview") for role in descriptor.output_roles
        }
        return SeparationSet(
            artifact=artifact,
            outputs=committed_outputs,
            previews=committed_previews,
            gain_db=gain_db,
            method=descriptor.id,
            method_version=descriptor.version,
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
        "engine": DEFAULT_METHOD_ID,
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


def _method_descriptor(method: str) -> SeparationMethod:
    descriptor = method_by_id(method)
    if descriptor is None:
        raise ValueError(f"unknown Separation method: {method!r}")
    return descriptor


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
    descriptor: SeparationMethod,
    source_id: str,
    spec_name: str,
    spec_revision: str,
    weights_sha256: str,
    plan: Mapping[str, object] | None,
    gain_db: float,
    peaks: Mapping[str, float],
    files: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "kind": "separation",
        "source_id": source_id,
        "method": descriptor.id,
        "method_version": descriptor.version,
        "engine": descriptor.id,
        "engine_version": DEMUCS_VERSION if descriptor.requires_model else str(descriptor.version),
        "sample_rate": CANONICAL_SAMPLE_RATE,
        "channels": CANONICAL_CHANNELS,
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
    if descriptor.requires_model:
        assert plan is not None
        payload.update(
            {
                "model": spec_name,
                "model_revision": spec_revision,
                "weights_sha256": weights_sha256,
                "device": DEVICE,
                "block_plan": dict(plan),
                "block_plan_fingerprint": plan["fingerprint"],
                "instrumental": ("float32 sum of drums + bass + other, written by the worker"),
            }
        )
    return payload


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


def _worker_environment(models: ModelStore | None, *, threads: int | None) -> dict[str, str]:
    """The worker environment; Hugging Face variables exist only for a model method."""
    resolved = max(1, (os.cpu_count() or 2) // 2) if threads is None else max(1, int(threads))
    environment = dict(os.environ)
    environment.update(
        {
            "OMP_NUM_THREADS": str(resolved),
            "MKL_NUM_THREADS": str(resolved),
            "PYTHONUNBUFFERED": "1",
            "PYTHONUTF8": "1",
            "STEMFORGE_WORKER_STDIN_WATCH": "1",
        }
    )
    if models is not None:
        environment["HF_HOME"] = str(models.root)
        environment["HF_HUB_OFFLINE"] = "1"
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
        ctx.progress(min(1.0, max(0.0, fraction)), stage=stage, detail=detail or None)

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
