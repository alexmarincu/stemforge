"""Pitch Job: render one immutable set from the master or a Separation set.

One function, :func:`run_pitch_job`, owns everything a Render action produces:
the shifted original, or every output of the selected Separation method. A
summed Instrumental (Demucs) is derived from the shifted non-vocal Stems; a
stored Instrumental (Karaoke) is shifted directly. It composes the
:class:`~stemforge.engines.pitch.PitchEngine` adapter and the
:class:`~stemforge.artifacts.ArtifactStore`, writes into ``staging/<job_id>/``,
records one shared presentation gain, encodes the previews from the unattenuated
float32 masters, writes the manifest last, and promotes the directory with one
atomic rename. A cancel or failure removes the whole staging directory, so no
partial render becomes visible.

Identity: ``renders/<render_key>/`` with ``render_key`` over the source id, the
scope (``original`` or ``stems``), the semitones, the sorted input hashes, the
engine version/preset, and, for stems, the Separation method. A master render
therefore can never satisfy a stems request, and two methods at the same
semitone cannot collide.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from stemforge.artifacts import (
    ArtifactStore,
    RenderArtifact,
    SeparationArtifact,
    estimate_render_bytes,
    free_space_requirement,
    require_free_space,
)
from stemforge.domain.methods import SeparationMethod, method_by_id, method_id_or_default
from stemforge.domain.models import RenderScope
from stemforge.domain.roles import INSTRUMENTAL_ROLE
from stemforge.engines.media import CANONICAL_CHANNELS, CANONICAL_SAMPLE_RATE, MediaTool
from stemforge.engines.pitch import (
    ENGINE_NAME,
    ENGINE_PRESET,
    FORMANT_PRESERVED,
    WINDOW,
    PitchEngine,
    ProgressSink,
    file_sha256,
    render_key_for,
    validate_semitones,
)
from stemforge.jobs.job import JobContext
from stemforge.pipeline.gain import max_peak_dbfs, presentation_gain

ORIGINAL_ROLE: Final = "original"

# Stems are shifted first; summing, gain, and previews share the remainder.
SHIFT_SHARE: Final = 0.80
INSTRUMENTAL_SHARE: Final = 0.04
GAIN_SHARE: Final = 0.05
PREVIEW_SHARE: Final = 0.10

STAGE_INSTRUMENTAL: Final = "Instrumental"
STAGE_GAIN: Final = "Gain"
STAGE_PREVIEWS: Final = "Previews"
STAGE_SAVING: Final = "Saving"
STAGE_DONE: Final = "Done"


@dataclass(frozen=True, slots=True)
class PitchInput:
    """One artifact the render reads, named by its role in the set."""

    role: str
    path: Path
    artifact: str


@dataclass(frozen=True, slots=True)
class PitchRenderSet:
    """What one Render action produced, resolved for the UI and exporters."""

    artifact: RenderArtifact
    scope: RenderScope
    semitones: int
    gain_db: float
    masters: Mapping[str, Path]
    previews: Mapping[str, Path]
    separation_method: str | None = None
    output_roles: tuple[str, ...] = (ORIGINAL_ROLE,)

    def master(self, role: str) -> Path | None:
        return self.masters.get(role)

    def preview(self, role: str) -> Path | None:
        return self.previews.get(role)


def run_pitch_job(
    ctx: JobContext,
    *,
    artifacts: ArtifactStore,
    pitch: PitchEngine,
    media: MediaTool,
    source_id: str,
    semitones: int,
    scope: RenderScope | str,
    separation_method: str | None = None,
    title: str | None = None,
    artist: str | None = None,
) -> PitchRenderSet:
    """Render one pitch set and return the committed artifact.

    ``semitones`` is an integer in ``-12...+12`` excluding ``0``. ``scope`` is
    ``original`` (the canonical master, with no Separation method) or ``stems``
    (every output of ``separation_method``'s committed set). Rendering an
    already-cached key returns the existing set without staging another. A cancel
    or failure discards ``staging/<job_id>/`` before the exception leaves.
    """
    shift = validate_semitones(semitones)
    resolved_scope = RenderScope(scope)
    descriptor, separation = _resolve_method_set(
        artifacts, source_id, resolved_scope, separation_method
    )
    cancel = ctx.cancel
    log = ctx.log
    emit = _context_progress(ctx)
    cancel.check()

    source = artifacts.find_source(source_id, verify_hashes=True)
    if source is None:
        raise ValueError(f"source is not committed: {source_id}")
    canonical = source.canonical_path
    if canonical is None:
        raise ValueError(f"source has no canonical audio: {source_id}")

    inputs = _resolve_inputs(resolved_scope, canonical, separation, descriptor)
    input_hashes = {item.role: file_sha256(item.path) for item in inputs}
    engine_version = pitch.version
    render_key = render_key_for(
        source_id=source_id,
        scope=resolved_scope,
        semitones=shift,
        input_hashes=list(input_hashes.values()),
        engine_version=engine_version,
        separation_method=descriptor.id if descriptor is not None else None,
    )
    output_roles = descriptor.output_roles if descriptor is not None else (ORIGINAL_ROLE,)

    existing = artifacts.find_render(render_key, verify_hashes=True)
    if existing is not None:
        artifacts.touch_render(render_key)
        emit(1.0, STAGE_DONE, "this render set already exists")
        return _render_set(
            existing,
            resolved_scope,
            shift,
            existing.gain_db,
            separation_method=descriptor.id if descriptor is not None else None,
            output_roles=output_roles,
        )

    expected_outputs = 1 if resolved_scope is RenderScope.ORIGINAL else len(output_roles)
    duration_s = media.probe(canonical).duration_s
    estimate = estimate_render_bytes(duration_s, outputs=expected_outputs)
    require_free_space(artifacts.root, free_space_requirement(estimate))
    artifacts.enforce_cap(estimate)

    staging = artifacts.stage(ctx.job_id)
    committed = False
    try:
        outputs: dict[str, Path] = {}
        total = len(inputs)
        for index, item in enumerate(inputs):
            cancel.check()
            start = index / total * SHIFT_SHARE
            span = SHIFT_SHARE / total
            detail = f"output {index + 1} of {total}" if resolved_scope is RenderScope.STEMS else ""
            destination = staging / f"{item.role}.wav"
            pitch.shift(
                item.path,
                destination,
                semitones=shift,
                cancel=cancel,
                log=log,
                progress=_scaled(emit, start=start, span=span, detail=detail or ORIGINAL_ROLE),
            )
            outputs[item.role] = destination

        if descriptor is not None and descriptor.instrumental_is_sum:
            cancel.check()
            emit(SHIFT_SHARE, STAGE_INSTRUMENTAL, "summing the shifted non-vocal stems")
            instrumental = staging / f"{INSTRUMENTAL_ROLE}.wav"
            pitch.sum_stems(
                [outputs[role] for role in descriptor.non_vocal_stems],
                instrumental,
                cancel=cancel,
                log=log,
            )
            outputs[INSTRUMENTAL_ROLE] = instrumental

        emit(SHIFT_SHARE + INSTRUMENTAL_SHARE, STAGE_GAIN, "measuring the set peak")
        peaks: dict[str, float] = {}
        for role, path in outputs.items():
            cancel.check()
            peaks[role] = pitch.peak_dbfs(path, cancel=cancel, log=log)
        gain_db = presentation_gain(peaks.values())

        preview_start = SHIFT_SHARE + INSTRUMENTAL_SHARE + GAIN_SHARE
        preview_end = preview_start + PREVIEW_SHARE
        emit(preview_start, STAGE_PREVIEWS, "")
        preview_title, preview_artist = _source_tags(source.manifest, title, artist)
        files: list[Mapping[str, object]] = []
        for index, (role, path) in enumerate(outputs.items()):
            cancel.check()
            preview = staging / f"{role}.mp3"
            media.encode_preview(
                path,
                preview,
                gain_db=gain_db,
                title=preview_title,
                artist=preview_artist,
                cancel=cancel,
                log=log,
            )
            files.append(_file_entry(path, role))
            files.append(_file_entry(preview, f"{role}_preview"))
            emit(
                preview_start + (preview_end - preview_start) * (index + 1) / len(outputs),
                STAGE_PREVIEWS,
                role,
            )

        manifest = _manifest(
            source_id=source_id,
            render_key=render_key,
            scope=resolved_scope,
            semitones=shift,
            engine_version=engine_version,
            gain_db=gain_db,
            peaks=peaks,
            inputs=inputs,
            input_hashes=input_hashes,
            files=files,
            separation_method=descriptor.id if descriptor is not None else None,
            separation_set_key=separation.set_key if separation is not None else None,
            separation_method_version=descriptor.version if descriptor is not None else None,
        )
        emit(preview_end, STAGE_SAVING, "writing the manifest")
        artifact = artifacts.commit_render(staging, render_key, manifest)
        committed = True
        emit(1.0, STAGE_DONE, "")
        return _render_set(
            artifact,
            resolved_scope,
            shift,
            gain_db,
            separation_method=descriptor.id if descriptor is not None else None,
            output_roles=output_roles,
        )
    except BaseException:
        if not committed:
            try:
                artifacts.discard_staging(ctx.job_id)
            except Exception:
                log.warning("could not discard staging for %s", ctx.job_id, exc_info=True)
        raise


def find_pitch_render(
    artifacts: ArtifactStore,
    *,
    source_id: str,
    semitones: int,
    scope: RenderScope | str,
    separation_method: str | None = None,
) -> PitchRenderSet | None:
    """Find the newest committed set for a (source, semitone, scope) request.

    Scope is part of the query, so a master render never answers a stems
    request. A stems query may name its Separation method; a set written before
    methods existed counts as Demucs.
    """
    resolved_scope = RenderScope(scope)
    matches = [
        artifact
        for artifact in artifacts.iter_renders()
        if artifact.source_id == source_id
        and artifact.manifest.get("scope") == resolved_scope.value
        and artifact.manifest.get("semitones") == semitones
        and (
            resolved_scope is not RenderScope.STEMS
            or separation_method is None
            or method_id_or_default(artifact.manifest.get("separation_method"))
            == separation_method
        )
    ]
    if not matches:
        return None
    newest = max(matches, key=lambda artifact: artifact.created_at)
    method = (
        method_id_or_default(newest.manifest.get("separation_method"))
        if resolved_scope is RenderScope.STEMS
        else None
    )
    descriptor = method_by_id(method) if method is not None else None
    output_roles = descriptor.output_roles if descriptor is not None else (ORIGINAL_ROLE,)
    return _render_set(
        newest,
        resolved_scope,
        semitones,
        newest.gain_db,
        separation_method=method,
        output_roles=output_roles,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _resolve_method_set(
    artifacts: ArtifactStore,
    source_id: str,
    scope: RenderScope,
    separation_method: str | None,
) -> tuple[SeparationMethod | None, SeparationArtifact | None]:
    """Resolve the method's committed set, or just the master's inputs.

    A stems Render never falls back to another method's set: an absent method or
    an absent set is a caller error, not a silent substitution.
    """
    if scope is RenderScope.ORIGINAL:
        return None, None
    if separation_method is None:
        raise ValueError("a stems render needs a Separation method")
    descriptor = method_by_id(separation_method)
    if descriptor is None:
        raise ValueError(f"unknown Separation method: {separation_method!r}")
    separation = artifacts.find_separation(source_id, method=descriptor.id, verify_hashes=True)
    if separation is None:
        raise ValueError(
            f"the {descriptor.label} Separation set is not available for this source: {source_id}"
        )
    return descriptor, separation


def _resolve_inputs(
    scope: RenderScope,
    canonical: Path,
    separation: SeparationArtifact | None,
    descriptor: SeparationMethod | None,
) -> list[PitchInput]:
    if scope is RenderScope.ORIGINAL:
        return [PitchInput(ORIGINAL_ROLE, canonical, "canonical.wav")]
    if separation is None or descriptor is None:  # pragma: no cover - resolved above
        raise ValueError("a stems render needs a committed Separation set")
    inputs: list[PitchInput] = []
    for role in descriptor.render_input_roles:
        path = separation.path_for(role)
        if path is None:
            raise ValueError(f"the separation is missing the {role} role")
        inputs.append(
            PitchInput(
                role,
                path,
                f"separations/{separation.method}/{role}.wav",
            )
        )
    return inputs


def _context_progress(ctx: JobContext) -> ProgressSink:
    def sink(fraction: float, stage: str, detail: str) -> None:
        ctx.progress(min(1.0, max(0.0, fraction)), stage=stage, detail=detail or None)

    return sink


def _scaled(
    sink: ProgressSink,
    *,
    start: float,
    span: float,
    detail: str,
) -> ProgressSink:
    def scaled(fraction: float, stage: str, _detail: str) -> None:
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
        "sha256": file_sha256(path),
    }


def _manifest(
    *,
    source_id: str,
    render_key: str,
    scope: RenderScope,
    semitones: int,
    engine_version: str,
    gain_db: float,
    peaks: Mapping[str, float],
    inputs: Sequence[PitchInput],
    input_hashes: Mapping[str, str],
    files: Sequence[Mapping[str, object]],
    separation_method: str | None,
    separation_set_key: str | None,
    separation_method_version: int | None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "kind": "render",
        "source_id": source_id,
        "render_key": render_key,
        "scope": scope.value,
        "semitones": semitones,
        "engine": ENGINE_NAME,
        "engine_version": engine_version,
        "preset": ENGINE_PRESET,
        "sample_rate": CANONICAL_SAMPLE_RATE,
        "channels": CANONICAL_CHANNELS,
        "formant": FORMANT_PRESERVED,
        "window": WINDOW,
        "gain_db": gain_db,
        "peak_dbfs": max_peak_dbfs(peaks.values()),
        "validation": {"ok": True},
        "inputs": [
            {"role": item.role, "artifact": item.artifact, "sha256": input_hashes[item.role]}
            for item in inputs
        ],
        "files": list(files),
    }
    # An original-scope Render carries no method; only a stems set names the
    # method and the exact set it was made from (ADR 0015).
    if separation_method is not None:
        payload["separation_method"] = separation_method
        payload["separation_set_key"] = separation_set_key
        payload["separation_method_version"] = separation_method_version
    return payload


def _render_set(
    artifact: RenderArtifact,
    scope: RenderScope,
    semitones: int,
    gain_db: float,
    *,
    separation_method: str | None = None,
    output_roles: tuple[str, ...] = (ORIGINAL_ROLE,),
) -> PitchRenderSet:
    masters: dict[str, Path] = {}
    previews: dict[str, Path] = {}
    for entry in artifact.files:
        if entry.role is None:
            continue
        if entry.role.endswith("_preview"):
            previews[entry.role.removesuffix("_preview")] = artifact.directory / entry.path
        else:
            masters[entry.role] = artifact.directory / entry.path
    return PitchRenderSet(
        artifact=artifact,
        scope=scope,
        semitones=semitones,
        gain_db=gain_db,
        masters=masters,
        previews=previews,
        separation_method=separation_method,
        output_roles=output_roles,
    )
