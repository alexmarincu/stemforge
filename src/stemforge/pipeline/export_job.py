"""Export Job: encode, verify tags, and atomically write at the destination.

One Export Job reads one committed artifact (the master, one stem, the
Instrumental, or one member of a pitch render set), encodes WAV 24 / FLAC 24 /
MP3 320 with the record-driven tags and the import cover, and writes the result
beside the user's chosen destination. It never normalizes: the chosen set's
recorded presentation gain (ADR 0002) is applied once and recorded in the
result manifest. No peak is measured here, so an export can never invent a
different gain from the one the source set committed.

The encoder writes inside ``staging/<job_id>/`` first. The destination sees only
``<final-name>.partial``; after a successful copy, ``fsync``, and collision-safe
rename, that partial becomes the final file. Every handled failure, cancel, or
validation error removes the partial and leaves no final file. A hard crash is
the one documented exception.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final
from uuid import uuid4

from stemforge import errors
from stemforge.artifacts import (
    ArtifactStore,
    RenderArtifact,
    SeparationArtifact,
    SourceArtifact,
    require_free_space,
)
from stemforge.domain.cancellation import CancelToken
from stemforge.domain.methods import (
    DEFAULT_METHOD_ID,
    SeparationMethod,
    method_by_id,
    method_id_or_default,
)
from stemforge.domain.models import RenderScope
from stemforge.domain.roles import INSTRUMENTAL_ROLE, STEM_ROLES
from stemforge.engines.media import (
    EXPORT_TAG_FIELDS,
    WAV_TAG_FIELDS,
    CanonicalMetadata,
    ExportFormat,
    MediaInfo,
    MediaTool,
)
from stemforge.errors import StemForgeError
from stemforge.jobs.job import JobContext
from stemforge.pipeline.export_names import (
    ROLE_LABELS,
    destination_base_name,
    required_export_space,
    suggest_base_name,
    unique_export_path,
)

__all__ = [
    "INSTRUMENTAL_ROLE",
    "STAGE_ENCODING",
    "STAGE_TAGGING",
    "STAGE_WRITING",
    "STEM_ROLES",
    "ExportResult",
    "ExportTarget",
    "export_targets",
    "resolve_export_target",
    "run_export_job",
]

ORIGINAL_ROLE: Final = "original"

STAGE_ENCODING: Final = "Encoding"
STAGE_TAGGING: Final = "Tagging"
STAGE_WRITING: Final = "Writing file"

_COPY_CHUNK_BYTES: Final = 1024 * 1024
_DISK_FULL_ERRNOS: Final = frozenset({errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)})
_ProgressSink = Callable[[float, str, str], None]


@dataclass(frozen=True, slots=True)
class ExportTarget:
    """One selectable artifact for Export, already resolved for the UI.

    ``separation_method`` and ``set_key`` record where a Separation-derived
    target came from; ``method_label`` qualifies the suggested filename only
    when the Source holds more than one Separation set.
    """

    source_id: str
    key: str
    label: str
    role: str
    path: Path
    gain_db: float
    semitones: int
    scope: str
    render_key: str | None
    title: str
    separation_method: str | None = None
    set_key: str | None = None
    method_label: str | None = None

    @property
    def base_name(self) -> str:
        """The suggested filename, without an extension."""
        return suggest_base_name(
            self.title,
            role=self.role,
            semitones=self.semitones,
            method_label=self.method_label,
        )


@dataclass(frozen=True, slots=True)
class ExportResult:
    """The committed file, its measured facts, and the export manifest."""

    path: Path
    fmt: ExportFormat
    source_id: str
    target_key: str
    gain_db: float
    duration_s: float
    size_bytes: int
    sha256: str
    metadata: Mapping[str, str]
    cover: Path | None
    manifest: Mapping[str, object]


def export_targets(
    artifacts: ArtifactStore,
    *,
    source_id: str,
    title: str | None = None,
    separation_method: str = DEFAULT_METHOD_ID,
) -> tuple[ExportTarget, ...]:
    """Every existing, valid selection for one Source, in display order.

    The master is first, then the selected method's Separation set, then pitch
    render sets from every method newest-first. A missing or invalid manifest
    and a missing file simply omit that target.
    """
    source = artifacts.find_source(source_id)
    if source is None:
        return ()
    resolved_title = title or _record_title(source.manifest, source_id)
    qualify_method = _has_multiple_separation_sets(artifacts, source_id)

    targets: list[ExportTarget] = []
    master = _master_target(source, resolved_title)
    if master is not None:
        targets.append(master)
    separation = artifacts.find_separation(source_id, method=separation_method)
    if separation is not None:
        targets.extend(
            _separation_targets(separation, resolved_title, qualify_method=qualify_method)
        )
    for render in sorted(
        (item for item in artifacts.iter_renders() if item.source_id == source_id),
        key=lambda item: item.created_at,
        reverse=True,
    ):
        targets.extend(_render_targets(render, resolved_title, qualify_method=qualify_method))
    return _qualify_colliding_labels(targets)


def resolve_export_target(
    artifacts: ArtifactStore,
    *,
    source_id: str,
    key: str,
    title: str | None = None,
    verify_hashes: bool = False,
) -> ExportTarget | None:
    """Resolve one target key against the current manifests, or return ``None``.

    A Job calls this again when it runs, so a queued Export follows the current
    committed set and never trusts a path that was deleted in the meantime.
    ``verify_hashes=True`` is the Job's deep verification of the file it is about
    to encode; the UI's enumeration stays metadata-only. Separation keys name
    their method, while the legacy ``stem:<role>`` and ``instrumental`` keys
    resolve against the Demucs set.
    """
    source = artifacts.find_source(source_id, verify_hashes=verify_hashes)
    if source is None:
        return None
    resolved_title = title or _record_title(source.manifest, source_id)
    qualify_method = _has_multiple_separation_sets(artifacts, source_id)

    if key == "master":
        return _master_target(source, resolved_title)
    if key == INSTRUMENTAL_ROLE:
        separation = artifacts.find_separation(
            source_id, method=DEFAULT_METHOD_ID, verify_hashes=verify_hashes
        )
        return (
            _separation_target(
                separation,
                INSTRUMENTAL_ROLE,
                resolved_title,
                qualify_method=qualify_method,
            )
            if separation is not None
            else None
        )
    if key.startswith("stem:"):
        role = key.removeprefix("stem:")
        if role not in STEM_ROLES:
            return None
        separation = artifacts.find_separation(
            source_id, method=DEFAULT_METHOD_ID, verify_hashes=verify_hashes
        )
        return (
            _separation_target(
                separation,
                role,
                resolved_title,
                qualify_method=qualify_method,
            )
            if separation is not None
            else None
        )
    if key.startswith("separation:"):
        parts = key.split(":")
        if len(parts) != 3:
            return None
        _, method, role = parts
        separation = artifacts.find_separation(
            source_id, method=method, verify_hashes=verify_hashes
        )
        if separation is None:
            return None
        return _separation_target(
            separation,
            role,
            resolved_title,
            qualify_method=qualify_method,
        )
    if key.startswith("render:"):
        parts = key.split(":")
        if len(parts) != 3:
            return None
        _, render_key, role = parts
        render = artifacts.find_render(render_key, verify_hashes=verify_hashes)
        if render is None or render.source_id != source_id:
            return None
        scope = _render_scope(render)
        if scope is None:
            return None
        descriptor = _render_separation_method(render, scope)
        return _render_target(
            render,
            role,
            resolved_title,
            scope=scope,
            descriptor=descriptor,
            qualify_method=qualify_method,
        )
    return None


def run_export_job(
    ctx: JobContext,
    *,
    artifacts: ArtifactStore,
    media: MediaTool,
    source_id: str,
    target: ExportTarget | str,
    fmt: ExportFormat | str,
    destination_dir: Path,
    destination_name: str | None = None,
) -> ExportResult:
    """Encode one selection and atomically write it to ``destination_dir``.

    ``target`` accepts the value from :func:`export_targets` or its stable key.
    ``destination_name`` is the save dialog's chosen filename; when
    omitted the target's suggested base name is used. The destination directory
    must already exist and be writable; the Job never creates a directory on the
    user's behalf. Free space is checked against the WAV-24 worst case before
    any destination file is touched.
    """
    export_format = ExportFormat(fmt)
    target_key = target.key if isinstance(target, ExportTarget) else str(target)
    target_title = target.title if isinstance(target, ExportTarget) else None
    cancel = ctx.cancel
    log = ctx.log
    emit = _progress_sink(ctx)
    cancel.check()

    resolved = resolve_export_target(
        artifacts,
        source_id=source_id,
        key=target_key,
        title=target_title,
        verify_hashes=True,
    )
    if resolved is None:
        raise StemForgeError(
            errors.MEDIA_PROBE_FAILED,
            "the selected export target is no longer available",
            detail="target",
            context={"source_id": source_id, "target": target_key},
        )

    source = artifacts.find_source(source_id)
    if source is None:
        raise ValueError(f"source is not committed: {source_id}")

    destination = Path(destination_dir).expanduser().absolute()
    source_info = media.probe(resolved.path)
    duration_s = source_info.duration_s
    if duration_s <= 0:
        raise StemForgeError(
            errors.MEDIA_PROBE_FAILED,
            "the selected export target has no measurable duration",
            detail="duration",
            context={"path": str(resolved.path)},
        )

    base_name = (
        destination_base_name(destination_name, export_format.suffix)
        if destination_name
        else resolved.base_name
    )

    emit(0.02, STAGE_ENCODING, "checking the destination")
    _require_destination(destination)
    required_bytes = required_export_space(duration_s)
    require_free_space(destination, required_bytes, floor=0)
    cancel.check()

    staging = artifacts.stage(ctx.job_id)
    try:
        encoded = staging / f"export{export_format.suffix}"
        emit(0.05, STAGE_ENCODING, resolved.label)
        try:
            media.encode_export(
                resolved.path,
                encoded,
                fmt=export_format,
                metadata=_metadata_record(source.manifest),
                gain_db=resolved.gain_db,
                cover=_cover_for(source, export_format),
                cancel=cancel,
                log=log,
            )
        except StemForgeError as exc:
            # Both Export encoder failures are retryable even though the shared
            # adapter leaves the flag unset elsewhere.
            if exc.code in {errors.MEDIA_ENCODE_FAILED, errors.MEDIA_VALIDATION_FAILED}:
                raise StemForgeError(
                    exc.code,
                    exc.message,
                    retryable=True,
                    detail=exc.detail,
                    context=exc.context,
                ) from exc
            raise

        emit(0.60, STAGE_TAGGING, "verifying tags and cover")
        output_info = _verify_output(
            media,
            encoded,
            export_format=export_format,
            metadata=_metadata_record(source.manifest),
        )
        digest = _sha256(encoded)
        size_bytes = encoded.stat().st_size

        emit(0.80, STAGE_WRITING, "writing the file")
        final_path = _write_destination(
            encoded,
            destination=destination,
            base_name=base_name,
            fmt=export_format,
            cancel=cancel,
            required=required_bytes,
        )
        manifest = _manifest(
            source_id=source_id,
            target=resolved,
            fmt=export_format,
            info=output_info,
            destination=final_path,
            size_bytes=size_bytes,
            sha256=digest,
            metadata=_metadata_record(source.manifest),
            cover=_cover_for(source, export_format),
        )
        log.info("export manifest %s", json.dumps(manifest, sort_keys=True, ensure_ascii=False))
        return ExportResult(
            path=final_path,
            fmt=export_format,
            source_id=source_id,
            target_key=resolved.key,
            gain_db=resolved.gain_db,
            duration_s=output_info.duration_s,
            size_bytes=size_bytes,
            sha256=digest,
            metadata=_record_tags(source.manifest),
            cover=_cover_for(source, export_format),
            manifest=manifest,
        )
    finally:
        try:
            artifacts.discard_staging(ctx.job_id)
        except Exception:  # pragma: no cover - cleanup must not mask the result
            log.warning("could not discard export staging for %s", ctx.job_id, exc_info=True)


# ---------------------------------------------------------------------------
# Target resolution
# ---------------------------------------------------------------------------


def _master_target(source: SourceArtifact, title: str) -> ExportTarget | None:
    path = source.canonical_path
    if path is None:
        return None
    return ExportTarget(
        source_id=source.source_id,
        key="master",
        label=ROLE_LABELS["original"],
        role=ORIGINAL_ROLE,
        path=path,
        gain_db=source.gain_db,
        semitones=0,
        scope="master",
        render_key=None,
        title=title,
    )


def _separation_targets(
    separation: SeparationArtifact,
    title: str,
    *,
    qualify_method: bool,
) -> tuple[ExportTarget, ...]:
    descriptor = method_by_id(separation.method)
    if descriptor is None:
        return ()
    targets: list[ExportTarget] = []
    for role in descriptor.output_roles:
        target = _separation_target(
            separation,
            role,
            title,
            qualify_method=qualify_method,
        )
        if target is not None:
            targets.append(target)
    return tuple(targets)


def _separation_target(
    separation: SeparationArtifact,
    role: str,
    title: str,
    *,
    qualify_method: bool,
) -> ExportTarget | None:
    descriptor = method_by_id(separation.method)
    if descriptor is None or role not in descriptor.output_roles:
        return None
    path = separation.path_for(role)
    if path is None:
        return None
    return ExportTarget(
        source_id=separation.source_id,
        key=f"separation:{descriptor.id}:{role}",
        label=ROLE_LABELS[role],
        role=role,
        path=path,
        gain_db=separation.gain_db,
        semitones=0,
        scope="separation",
        render_key=None,
        title=title,
        separation_method=descriptor.id,
        set_key=separation.set_key,
        method_label=descriptor.label if qualify_method else None,
    )


def _render_targets(
    render: RenderArtifact,
    title: str,
    *,
    qualify_method: bool,
) -> tuple[ExportTarget, ...]:
    scope = _render_scope(render)
    if scope is None:
        return ()
    if scope is RenderScope.ORIGINAL:
        descriptor = None
        roles: tuple[str, ...] = (ORIGINAL_ROLE,)
    elif scope is RenderScope.STEMS:
        descriptor = _render_separation_method(render, scope)
        if descriptor is None:
            return ()
        roles = descriptor.output_roles
    else:
        return ()
    targets: list[ExportTarget] = []
    for role in roles:
        target = _render_target(
            render,
            role,
            title,
            scope=scope,
            descriptor=descriptor,
            qualify_method=qualify_method,
        )
        if target is not None:
            targets.append(target)
    return tuple(targets)


def _render_target(
    render: RenderArtifact,
    role: str,
    title: str,
    *,
    scope: RenderScope,
    descriptor: SeparationMethod | None,
    qualify_method: bool,
) -> ExportTarget | None:
    if scope is RenderScope.ORIGINAL:
        if role != ORIGINAL_ROLE:
            return None
    elif descriptor is None or role not in descriptor.output_roles:
        return None
    path = render.path_for(role)
    if path is None:
        return None
    semitones = _manifest_int(render.manifest, "semitones")
    if semitones is None:
        return None
    return ExportTarget(
        source_id=render.source_id,
        key=f"render:{render.render_key}:{role}",
        label=f"{ROLE_LABELS[role]} {semitones:+d} st",
        role=role,
        path=path,
        gain_db=render.gain_db,
        semitones=semitones,
        scope=scope.value,
        render_key=render.render_key,
        title=title,
        separation_method=descriptor.id if descriptor is not None else None,
        set_key=_recorded_set_key(render) if descriptor is not None else None,
        method_label=(descriptor.label if descriptor is not None and qualify_method else None),
    )


def _render_scope(render: RenderArtifact) -> RenderScope | None:
    value = render.manifest.get("scope")
    try:
        return RenderScope(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _render_separation_method(
    render: RenderArtifact, scope: RenderScope
) -> SeparationMethod | None:
    """The method a stems Render set came from; a legacy set is Demucs."""
    if scope is not RenderScope.STEMS:
        return None
    return method_by_id(method_id_or_default(render.manifest.get("separation_method")))


def _recorded_set_key(render: RenderArtifact) -> str | None:
    value = render.manifest.get("separation_set_key")
    return value if isinstance(value, str) and value else None


def _has_multiple_separation_sets(artifacts: ArtifactStore, source_id: str) -> bool:
    return len(artifacts.separation_sets(source_id)) > 1


def _qualify_colliding_labels(targets: Sequence[ExportTarget]) -> tuple[ExportTarget, ...]:
    """Append the method label only where two methods' labels truly collide."""
    groups: dict[str, list[ExportTarget]] = {}
    for target in targets:
        groups.setdefault(target.label, []).append(target)
    colliding = {
        label
        for label, group in groups.items()
        if len(group) > 1 and len({target.separation_method for target in group}) > 1
    }
    if not colliding:
        return tuple(targets)
    return tuple(
        replace(
            target,
            label=f"{target.label} ({_method_label(target.separation_method)})",
        )
        if target.label in colliding and target.separation_method is not None
        else target
        for target in targets
    )


def _method_label(method_id: str | None) -> str:
    descriptor = method_by_id(method_id) if method_id is not None else None
    return descriptor.label if descriptor is not None else ""


# ---------------------------------------------------------------------------
# Destination checks and atomic write
# ---------------------------------------------------------------------------


def _require_destination(destination: Path) -> None:
    probe: Path | None = None
    try:
        if not destination.exists():
            raise _destination_error(
                FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT)),
                destination,
            )
        if not destination.is_dir():
            raise _destination_error(
                NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR)),
                destination,
            )
        probe = destination / f".stemforge-write-{uuid4().hex}.tmp"
        descriptor = os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        probe.unlink()
        probe = None
    except StemForgeError:
        raise
    except OSError as exc:
        raise _destination_error(exc, destination) from exc
    finally:
        if probe is not None:
            with contextlib.suppress(OSError):
                probe.unlink()


def _write_destination(
    encoded: Path,
    *,
    destination: Path,
    base_name: str,
    fmt: ExportFormat,
    cancel: CancelToken,
    required: int | None = None,
) -> Path:
    partial = destination / f"{unique_export_path(destination, base_name, fmt.suffix).name}.partial"
    try:
        _copy_with_fsync(encoded, partial, cancel)
        return _commit_without_overwrite(
            partial,
            destination=destination,
            base_name=base_name,
            suffix=fmt.suffix,
            required=required,
        )
    except StemForgeError:
        _remove(partial)
        raise
    except OSError as exc:
        _remove(partial)
        raise _destination_error(exc, destination, required=required) from exc
    except BaseException:
        _remove(partial)
        raise


def _copy_with_fsync(source: Path, partial: Path, cancel: CancelToken) -> None:
    try:
        with source.open("rb") as reader, partial.open("wb") as writer:
            while chunk := reader.read(_COPY_CHUNK_BYTES):
                cancel.check()
                writer.write(chunk)
            writer.flush()
            os.fsync(writer.fileno())
    except BaseException:
        _remove(partial)
        raise


def _commit_without_overwrite(
    partial: Path,
    *,
    destination: Path,
    base_name: str,
    suffix: str,
    required: int | None = None,
) -> Path:
    """Rename ``partial`` to the first free final name.

    The final name is chosen again after the encode, so a file that appeared
    during encoding gets the next `` (2)`` suffix instead of being replaced.
    """
    for _ in range(9_999):
        final = unique_export_path(destination, base_name, suffix)
        if os.path.lexists(final):
            continue
        try:
            os.replace(partial, final)
        except FileExistsError:  # Windows can report the loser of a race
            continue
        return final
    raise _destination_error(
        OSError(errno.EEXIST, "no available export name"),
        destination,
        required=required,
    )


def _destination_error(
    exc: OSError,
    destination: Path,
    *,
    required: int | None = None,
) -> StemForgeError:
    reason = _write_reason(exc)
    context: dict[str, object] = {
        "path": str(destination),
        "errno": exc.errno,
        "reason": reason,
    }
    if required is not None:
        context["required"] = required
    if exc.errno in _DISK_FULL_ERRNOS:
        return StemForgeError(
            errors.STORAGE_DISK_FULL,
            f"not enough free space to write the export at {destination}",
            detail=reason,
            retryable=True,
            context=context,
        )
    return StemForgeError(
        errors.EXPORT_WRITE_FAILED,
        f"could not write the export at {destination}: {exc}",
        detail=reason,
        retryable=True,
        context=context,
    )


def _write_reason(exc: OSError) -> str:
    if getattr(exc, "winerror", None) == 32:
        return "in_use"
    if isinstance(exc, PermissionError) or exc.errno in {errno.EACCES, errno.EPERM}:
        return "permission"
    if exc.errno == errno.ENAMETOOLONG:
        return "name_too_long"
    if exc.errno in {errno.ENOENT, errno.ENODEV}:
        return "device_gone"
    return "other"


# ---------------------------------------------------------------------------
# Encoder validation and manifest
# ---------------------------------------------------------------------------


def _verify_output(
    media: MediaTool,
    path: Path,
    *,
    export_format: ExportFormat,
    metadata: CanonicalMetadata,
) -> MediaInfo:
    info = media.probe(path)
    expected = dict(metadata.tags())
    fields = WAV_TAG_FIELDS if export_format is ExportFormat.WAV24 else EXPORT_TAG_FIELDS
    missing = {
        key: expected[key]
        for key in fields
        if key in expected and info.tags.get(key) != expected[key]
    }
    if missing:
        raise StemForgeError(
            errors.MEDIA_VALIDATION_FAILED,
            "the export tags did not match the source record",
            retryable=True,
            detail="tags",
            context={"path": str(path), "fields": sorted(missing)},
        )
    return info


def _manifest(
    *,
    source_id: str,
    target: ExportTarget,
    fmt: ExportFormat,
    info: MediaInfo,
    destination: Path,
    size_bytes: int,
    sha256: str,
    metadata: CanonicalMetadata,
    cover: Path | None,
) -> dict[str, object]:
    return {
        "kind": "export",
        "source_id": source_id,
        "target": target.key,
        "role": target.role,
        "scope": target.scope,
        "separation_method": target.separation_method,
        "separation_set_key": target.set_key,
        "render_key": target.render_key,
        "semitones": target.semitones,
        "format": fmt.value,
        "codec": info.codec,
        "sample_rate": info.sample_rate,
        "channels": info.channels,
        "bit_depth": info.bit_depth,
        "bit_rate": info.bit_rate,
        "duration_s": info.duration_s,
        "gain_db": target.gain_db,
        "normalization": "none",
        "tags": dict(metadata.tags()),
        "cover": cover.name if cover is not None else None,
        "file": destination.name,
        "size": size_bytes,
        "sha256": sha256,
        "validation": {"ok": True},
    }


def _metadata_record(manifest: Mapping[str, object]) -> CanonicalMetadata:
    record = manifest.get("metadata")
    values = record if isinstance(record, Mapping) else {}
    return CanonicalMetadata(
        title=_text(values.get("title")),
        artist=_text(values.get("artist")),
        album=_text(values.get("album")),
        album_artist=_text(values.get("album_artist")),
        track=_text(values.get("track")),
        disc=_text(values.get("disc")),
        date=_text(values.get("date")),
        genre=_text(values.get("genre")),
        comment=_text(values.get("comment")),
        composer=_text(values.get("composer")),
        copyright=_text(values.get("copyright")),
        source_url=_text(values.get("source_url")),
        source_id=_text(values.get("source_id")),
        duration_s=_number(values.get("duration_s")) or 0.0,
        sample_rate=int(_number(values.get("sample_rate")) or 0),
        channels=int(_number(values.get("channels")) or 0),
    )


def _record_tags(manifest: Mapping[str, object]) -> Mapping[str, str]:
    record = manifest.get("metadata")
    values = record if isinstance(record, Mapping) else {}
    return {key: value for key in EXPORT_TAG_FIELDS if (value := _text(values.get(key)))}


def _record_title(manifest: Mapping[str, object], fallback: str) -> str:
    record = manifest.get("metadata")
    values = record if isinstance(record, Mapping) else {}
    return _text(values.get("title")) or fallback


def _cover_for(source: SourceArtifact, fmt: ExportFormat) -> Path | None:
    if fmt is ExportFormat.WAV24:
        return None
    return source.cover_path


def _manifest_int(manifest: Mapping[str, object], key: str) -> int | None:
    value = manifest.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _progress_sink(ctx: JobContext) -> _ProgressSink:
    def sink(fraction: float, stage: str, detail: str) -> None:
        ctx.progress(min(1.0, max(0.0, fraction)), stage=stage, detail=detail or None)

    return sink


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_COPY_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass
