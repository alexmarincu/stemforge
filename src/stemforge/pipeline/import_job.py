"""Local import Job: probe, decode, metadata, manifest, and atomic commit.

The pipeline function composes the engine adapter, ``ArtifactStore``, and the
Workspace through a callback; it imports no UI or application module. Progress
is monotonic and uses the four stage labels: Probing, Decoding, Reading
metadata, Saving. Cancellation is honoured at every checkpoint before the
manifest is written; once the store starts promoting, the Job is not
interruptible.
"""

from __future__ import annotations

import errno
import hashlib
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from stemforge import errors
from stemforge.artifacts import (
    ArtifactStore,
    SourceArtifact,
    estimate_import_bytes,
    free_space_requirement,
    require_free_space,
)
from stemforge.domain.models import OriginKind, Source, SourceMetadata, Track
from stemforge.engines.media import MAX_DURATION_S, CanonicalMetadata, MediaTool
from stemforge.errors import StemForgeError
from stemforge.jobs.job import JobContext
from stemforge.pipeline.source_manifest import source_manifest

__all__ = [
    "CANONICAL_NAME",
    "PREVIEW_NAME",
    "STAGE_DECODING",
    "STAGE_PROBING",
    "STAGE_READING_METADATA",
    "STAGE_SAVING",
    "ImportResult",
    "run_local_import",
]

CANONICAL_NAME: Final = "canonical.wav"
PREVIEW_NAME: Final = "original.mp3"
STAGE_PROBING: Final = "Probing"
STAGE_DECODING: Final = "Decoding"
STAGE_READING_METADATA: Final = "Reading metadata"
STAGE_SAVING: Final = "Saving"

_MAX_SUFFIX_LENGTH: Final = 8

FinalizeSource = Callable[[Source], Track]


@dataclass(frozen=True, slots=True)
class ImportResult:
    """What a successful local import produced."""

    source: Source
    track: Track
    artifact: SourceArtifact
    cached: bool = False


def run_local_import(
    ctx: JobContext,
    path: Path,
    *,
    artifacts: ArtifactStore,
    media: MediaTool,
    finalize: FinalizeSource,
) -> ImportResult:
    """Import one local file into the cache and the Workspace."""
    source_path = Path(path).expanduser().absolute()
    staging: Path | None = None
    try:
        ctx.progress(0.0, stage=STAGE_PROBING)
        ctx.cancel.check()
        native_bytes = _require_readable(source_path)
        source_id = _blake2b_file(source_path)

        info = media.probe(source_path)
        if info.duration_s > MAX_DURATION_S:
            raise StemForgeError(
                errors.IMPORT_TOO_LONG,
                "source exceeds the 30-minute import limit",
                detail=f"duration={info.duration_s:.3f}",
                context={"path": str(source_path), "duration_s": info.duration_s},
            )
        estimate = _check_free_space(artifacts, info.duration_s, native_bytes)
        ctx.cancel.check()
        ctx.progress(0.05, stage=STAGE_PROBING, detail=source_path.name)

        cached = artifacts.find_source(source_id)
        if cached is not None:
            artifacts.touch_source(source_id)
            source = _source_from_artifact(cached, source_path, source_id)
            ctx.progress(0.9, stage=STAGE_SAVING)
            track = finalize(source)
            ctx.progress(1.0, stage=STAGE_SAVING)
            return ImportResult(source=source, track=track, artifact=cached, cached=True)

        # The Job reserves its output estimate before it writes.
        artifacts.enforce_cap(estimate)
        staging = artifacts.stage(ctx.job_id)
        native_path = staging / _native_name(source_path)
        _copy_native(source_path, native_path)
        ctx.progress(0.1, stage=STAGE_DECODING)
        ctx.cancel.check()

        decoded = media.decode_canonical(
            source_path,
            staging / CANONICAL_NAME,
            cancel=ctx.cancel,
            log=ctx.log,
        )
        ctx.cancel.check()
        ctx.progress(0.65, stage=STAGE_DECODING)
        ctx.progress(0.7, stage=STAGE_READING_METADATA)

        metadata = media.extract_metadata(source_path)
        cover = media.extract_cover(source_path, staging)
        peak_dbfs = media.peak_dbfs(decoded.path, cancel=ctx.cancel, log=ctx.log)
        gain_db = 0.0 if peak_dbfs <= 0.0 else -1.0 - peak_dbfs
        ctx.cancel.check()
        ctx.progress(0.85, stage=STAGE_SAVING)

        title = metadata.title or source_path.stem
        preview_path = staging / PREVIEW_NAME
        media.encode_preview(
            decoded.path,
            preview_path,
            gain_db=gain_db,
            title=title,
            artist=metadata.artist,
            cancel=ctx.cancel,
            log=ctx.log,
        )
        ctx.cancel.check()
        ctx.progress(0.9, stage=STAGE_SAVING)

        manifest = _source_manifest(
            source_id=source_id,
            source_path=source_path,
            native_path=native_path,
            canonical_path=decoded.path,
            preview_path=preview_path,
            cover_path=cover,
            decoded_duration_s=decoded.duration_s,
            decoded_sample_rate=decoded.sample_rate,
            decoded_channels=decoded.channels,
            downmixed=decoded.downmixed,
            metadata=metadata,
            title=title,
            gain_db=gain_db,
        )
        # From here on the commit is not interruptible.
        artifact = artifacts.commit_source(staging, source_id, manifest)
        staging = None

        source = _source_from_artifact(artifact, source_path, source_id, fallback_title=title)
        ctx.progress(0.95, stage=STAGE_SAVING)
        track = finalize(source)
        ctx.progress(1.0, stage=STAGE_SAVING)
        return ImportResult(source=source, track=track, artifact=artifact)
    except BaseException:
        if staging is not None:
            try:
                artifacts.discard_staging(ctx.job_id)
            except Exception:
                ctx.log.warning("could not discard staging for %s", ctx.job_id, exc_info=True)
        raise


# --- helpers ---------------------------------------------------------------


def _require_readable(path: Path) -> int:
    try:
        if not path.is_file():
            raise StemForgeError(
                errors.IMPORT_FILE_UNAVAILABLE,
                "source path is not a readable file",
                detail="not a file",
                context={"path": str(path)},
            )
        with path.open("rb"):
            pass
        return path.stat().st_size
    except OSError as exc:
        raise StemForgeError(
            errors.IMPORT_FILE_UNAVAILABLE,
            "source file could not be opened",
            detail=str(exc),
            context={"path": str(path), "errno": exc.errno},
        ) from exc


def _blake2b_file(path: Path) -> str:
    digest = hashlib.blake2b(digest_size=32)
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise StemForgeError(
            errors.IMPORT_FILE_UNAVAILABLE,
            "source file could not be read",
            detail=str(exc),
            context={"path": str(path), "errno": exc.errno},
        ) from exc
    return digest.hexdigest()


def _check_free_space(artifacts: ArtifactStore, duration_s: float, native_bytes: int) -> int:
    estimate = estimate_import_bytes(duration_s, native_bytes=native_bytes)
    require_free_space(artifacts.root, free_space_requirement(estimate))
    return estimate


def _copy_native(source: Path, target: Path) -> None:
    try:
        shutil.copyfile(source, target)
    except OSError as exc:
        code = (
            errors.STORAGE_DISK_FULL
            if exc.errno == errno.ENOSPC
            else errors.STORAGE_PERMISSION_DENIED
        )
        raise StemForgeError(
            code,
            f"could not copy the source into staging: {exc}",
            retryable=True,
            detail=str(exc),
            context={"source": str(source), "path": str(target), "errno": exc.errno},
        ) from exc


def _native_name(source: Path) -> str:
    suffix = source.suffix.lower()
    if len(suffix) < 2 or len(suffix) - 1 > _MAX_SUFFIX_LENGTH or not suffix[1:].isalnum():
        suffix = ".bin"
    return f"native{suffix}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_entry(path: Path, role: str) -> dict[str, object]:
    return {
        "path": path.name,
        "role": role,
        "size": path.stat().st_size,
        "sha256": _sha256_file(path),
    }


def _source_manifest(
    *,
    source_id: str,
    source_path: Path,
    native_path: Path,
    canonical_path: Path,
    preview_path: Path,
    cover_path: Path | None,
    decoded_duration_s: float,
    decoded_sample_rate: int,
    decoded_channels: int,
    downmixed: bool,
    metadata: CanonicalMetadata,
    title: str,
    gain_db: float,
) -> dict[str, object]:
    files = [
        _file_entry(native_path, "native"),
        _file_entry(canonical_path, "canonical"),
        _file_entry(preview_path, "preview"),
    ]
    if cover_path is not None:
        files.append(_file_entry(cover_path, "cover"))

    return source_manifest(
        source_id=source_id,
        origin=str(source_path),
        origin_kind=OriginKind.FILE,
        content_hash=source_id,
        gain_db=gain_db,
        downmixed=downmixed,
        metadata=_metadata_record(
            metadata,
            source_id=source_id,
            title=title,
            duration_s=decoded_duration_s,
            sample_rate=decoded_sample_rate,
            channels=decoded_channels,
        ),
        files=files,
    )


def _metadata_record(
    metadata: CanonicalMetadata,
    *,
    source_id: str,
    title: str,
    duration_s: float,
    sample_rate: int,
    channels: int,
) -> dict[str, object]:
    return {
        "title": title,
        "artist": metadata.artist,
        "album": metadata.album,
        "album_artist": metadata.album_artist,
        "track": metadata.track,
        "disc": metadata.disc,
        "date": metadata.date,
        "genre": metadata.genre,
        "comment": metadata.comment,
        "composer": metadata.composer,
        "copyright": metadata.copyright,
        "source_url": metadata.source_url,
        "source_id": source_id,
        "duration_s": duration_s,
        "sample_rate": sample_rate,
        "channels": channels,
    }


def _source_from_artifact(
    artifact: SourceArtifact,
    source_path: Path,
    source_id: str,
    *,
    fallback_title: str | None = None,
) -> Source:
    record = _metadata_mapping(artifact.manifest)
    title = _as_str(record.get("title")) or fallback_title or source_path.stem
    metadata = SourceMetadata(
        title=title,
        artist=_as_str(record.get("artist")),
        album=_as_str(record.get("album")),
        duration_s=_as_float(record.get("duration_s")),
        sample_rate=_as_int(record.get("sample_rate")),
        channels=_as_int(record.get("channels")),
    )
    return Source(
        source_id=source_id,
        origin=str(source_path),
        origin_kind=OriginKind.FILE,
        metadata=metadata,
        downmixed=artifact.manifest.get("downmixed") is True,
        gain_db=artifact.gain_db,
        native_path=artifact.path_for("native"),
        canonical_path=artifact.path_for("canonical"),
        preview_path=artifact.path_for("preview"),
        cover_path=artifact.path_for("cover"),
        content_hash=source_id,
        created_at=artifact.created_at,
    )


def _metadata_mapping(manifest: Mapping[str, object]) -> Mapping[str, object]:
    raw = manifest.get("metadata")
    return cast("Mapping[str, object]", raw) if isinstance(raw, Mapping) else {}


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None
