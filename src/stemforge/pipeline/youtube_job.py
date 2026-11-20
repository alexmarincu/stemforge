"""The YouTube import Job: resolve, pace, download, decode, preview, commit.

One Job kind, ``import_youtube``, running on the single JobRunner thread (ticket
06 §3, ticket 09 §3). The network work is delegated to
:class:`~stemforge.engines.youtube.YouTubeTool`; this module owns the stage
weights, the two 5 s politeness waits, the cache-hit shortcut, the manifest, and
the atomic commit. It imports no UI or application module.

Stage labels are the four the Job bar shows: ``Checking video`` (resolve and the
first wait), ``Downloading audio`` (download and the second wait), ``Decoding
audio`` (ffprobe, canonical WAV, metadata and peak), and ``Finishing`` (preview,
manifest, atomic commit, Track and recent entry).
"""

from __future__ import annotations

import errno
import hashlib
import math
import os
import shutil
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, cast

from stemforge import __version__, errors
from stemforge.artifacts import (
    ArtifactStore,
    SourceArtifact,
    estimate_import_bytes,
    free_space_requirement,
    require_free_space,
)
from stemforge.domain.models import OriginKind, Source, SourceMetadata, Track
from stemforge.engines.media import MediaTool
from stemforge.engines.youtube import (
    YOUTUBE_MAX_DURATION_S,
    DownloadedAudio,
    YouTubeMetadata,
    YouTubeTool,
    YouTubeVideo,
)
from stemforge.errors import JobCancelled, StemForgeError
from stemforge.jobs.job import JobContext
from stemforge.pipeline.import_job import CANONICAL_NAME, PREVIEW_NAME

__all__ = [
    "STAGE_CHECKING",
    "STAGE_DECODING",
    "STAGE_DOWNLOADING",
    "STAGE_FINISHING",
    "WAIT_SECONDS",
    "YouTubeImportResult",
    "run_youtube_import",
]

STAGE_CHECKING: Final = "Checking video"
STAGE_DOWNLOADING: Final = "Downloading audio"
STAGE_DECODING: Final = "Decoding audio"
STAGE_FINISHING: Final = "Finishing"
WAIT_SECONDS: Final = 5.0

_WAIT_TICK_S: Final = 0.5
_MAX_SUFFIX_LENGTH: Final = 8
_NATIVE_NAME: Final = "native"

FinalizeSource = Callable[[Source], Track]


@dataclass(frozen=True, slots=True)
class YouTubeImportResult:
    """What a successful YouTube import produced."""

    source: Source
    track: Track
    artifact: SourceArtifact
    cached: bool = False


def run_youtube_import(
    ctx: JobContext,
    video: YouTubeVideo,
    *,
    artifacts: ArtifactStore,
    media: MediaTool,
    youtube: YouTubeTool,
    finalize: FinalizeSource,
    sleep: Callable[[float], None] = time.sleep,
) -> YouTubeImportResult:
    """Import one validated YouTube video into the cache and the Workspace."""
    ctx.progress(0.0, stage=STAGE_CHECKING, detail="Resolving metadata")

    cached = artifacts.find_source(video.source_id, verify_hashes=True)
    if cached is not None:
        return _finish_cached(ctx, artifacts, cached, video, finalize)

    # Ticket 09 §5 case 2: a valid native file repairs a missing or invalid
    # canonical/preview without touching the network.
    recovered = _recover_from_native(ctx, artifacts, media, video, finalize)
    if recovered is not None:
        return recovered

    ctx.cancel.check()
    metadata = youtube.resolve(video, cancel=ctx.cancel, log=ctx.log)
    ctx.progress(0.05, stage=STAGE_CHECKING, detail="Metadata resolved")

    source_id = f"yt-{metadata.video_id}"
    if source_id != video.source_id:
        # Ticket 09 §1: yt-dlp's id is authoritative after resolve.
        cached = artifacts.find_source(source_id, verify_hashes=True)
        if cached is not None:
            return _finish_cached(
                ctx, artifacts, cached, video, finalize, origin=metadata.canonical_url
            )

    estimate = _check_free_space(artifacts, metadata.duration_s)
    artifacts.enforce_cap(estimate)
    staging: Path | None = None
    try:
        staging = artifacts.stage(ctx.job_id)
        _polite_wait(ctx, 0.05, STAGE_CHECKING, sleep)

        ctx.progress(0.05, stage=STAGE_DOWNLOADING)
        download_dir = artifacts.create_youtube_scratch(ctx.job_id)
        downloaded = youtube.download(
            video,
            download_dir,
            cancel=ctx.cancel,
            log=ctx.log,
            progress=lambda fraction, detail: ctx.progress(
                0.05 + min(1.0, max(0.0, fraction)) * 0.69,
                stage=STAGE_DOWNLOADING,
                detail=detail,
            ),
        )
        ctx.progress(0.74, stage=STAGE_DOWNLOADING, detail="Download complete")
        _polite_wait(ctx, 0.74, STAGE_DOWNLOADING, sleep)
        ctx.cancel.check()

        ctx.progress(0.75, stage=STAGE_DECODING)
        native_path = _move_native(downloaded.path, staging)
        try:
            artifacts.discard_youtube_scratch(ctx.job_id)
        except Exception:
            ctx.log.warning(
                "could not discard the YouTube scratch for %s", ctx.job_id, exc_info=True
            )
        native_info = media.probe(native_path)
        _require_duration_cap(native_info.duration_s)
        ctx.cancel.check()

        decoded = media.decode_canonical(
            native_path,
            staging / CANONICAL_NAME,
            cancel=ctx.cancel,
            log=ctx.log,
        )
        ctx.progress(0.86, stage=STAGE_DECODING)
        peak_dbfs = media.peak_dbfs(decoded.path, cancel=ctx.cancel, log=ctx.log)
        gain_db = 0.0 if peak_dbfs <= 0.0 else -1.0 - peak_dbfs
        ctx.cancel.check()

        ctx.progress(0.90, stage=STAGE_FINISHING, detail="Encoding preview")
        preview_path = staging / PREVIEW_NAME
        media.encode_preview(
            decoded.path,
            preview_path,
            gain_db=gain_db,
            title=metadata.title,
            artist=metadata.artist,
            cancel=ctx.cancel,
            log=ctx.log,
        )
        ctx.cancel.check()

        # Ticket 09 §5: the estimate is checked again before promotion.
        _check_free_space(artifacts, decoded.duration_s)
        ctx.progress(0.95, stage=STAGE_FINISHING, detail="Writing manifest")
        manifest = _source_manifest(
            source_id=source_id,
            metadata=metadata,
            downloaded=downloaded,
            native_path=native_path,
            canonical_path=decoded.path,
            preview_path=preview_path,
            duration_s=decoded.duration_s,
            sample_rate=decoded.sample_rate,
            channels=decoded.channels,
            downmixed=decoded.downmixed,
            native_bytes=_file_size(native_path),
            native_sha256=_sha256_file(native_path),
            gain_db=gain_db,
            tools=youtube.tool_versions(),
        )
        # From here on the commit is not interruptible (ticket 14 §5).
        artifact = artifacts.commit_source(staging, source_id, manifest)
        staging = None

        source = _source_from_artifact(artifact, fallback_origin=metadata.canonical_url)
        ctx.progress(0.99, stage=STAGE_FINISHING, detail="Track added")
        track = finalize(source)
        ctx.progress(1.0, stage=STAGE_FINISHING)
        return YouTubeImportResult(source=source, track=track, artifact=artifact)
    except BaseException:
        if staging is not None:
            try:
                artifacts.discard_staging(ctx.job_id)
            except Exception:
                ctx.log.warning("could not discard staging for %s", ctx.job_id, exc_info=True)
        try:
            artifacts.discard_youtube_scratch(ctx.job_id)
        except Exception:
            ctx.log.warning(
                "could not discard the YouTube scratch for %s", ctx.job_id, exc_info=True
            )
        raise


# --- cache hit -------------------------------------------------------------


def _finish_cached(
    ctx: JobContext,
    artifacts: ArtifactStore,
    artifact: SourceArtifact,
    video: YouTubeVideo,
    finalize: FinalizeSource,
    *,
    origin: str | None = None,
) -> YouTubeImportResult:
    """Open a committed artifact without a network call or a pacing wait."""
    ctx.progress(0.9, stage=STAGE_FINISHING, detail="Opening cached audio")
    artifacts.touch_source(artifact.source_id)
    source = _source_from_artifact(artifact, fallback_origin=origin or video.canonical_url)
    track = finalize(source)
    ctx.progress(1.0, stage=STAGE_FINISHING)
    return YouTubeImportResult(
        source=source,
        track=track,
        artifact=artifact,
        cached=True,
    )


def _recover_from_native(
    ctx: JobContext,
    artifacts: ArtifactStore,
    media: MediaTool,
    video: YouTubeVideo,
    finalize: FinalizeSource,
) -> YouTubeImportResult | None:
    """Re-decode a Source whose native file is intact but derived files are not.

    Returns ``None`` when there is nothing to repair, when the native file fails
    its recorded hash (the subtree is deleted so the caller downloads again), or
    when the native file cannot be decoded after all. No network is used here.
    """
    source_id = video.source_id
    record = artifacts.read_source_manifest(source_id)
    if record is None:
        return None
    manifest = record.manifest

    native_entry = record.entry_for("native")
    native_path = record.path_for("native")
    if native_entry is None or native_path is None:
        return None
    native_bytes = native_entry.size
    native_sha256 = native_entry.sha256
    if native_bytes is None or native_sha256 is None:
        return None
    try:
        if native_path.is_symlink() or not native_path.is_file():
            return None
        if native_path.stat().st_size != native_bytes:
            return None
    except OSError:
        return None
    if _sha256_file(native_path) != native_sha256:
        # Ticket 09 §5 case 3: the native file no longer matches its hash.
        _discard_broken_source(ctx, artifacts, source_id)
        return None

    metadata = _metadata_from_manifest(manifest, source_id, video)
    tools = _tools_from_manifest(manifest)
    downloaded = DownloadedAudio(
        path=native_path,
        format_id=_as_str(manifest.get("format_id")) or None,
        extractor=_as_str(manifest.get("extractor")) or None,
        extractor_version=_as_str(manifest.get("extractor_version")) or None,
    )

    ctx.progress(0.75, stage=STAGE_DECODING, detail="Repairing cached audio")
    duration_s = _as_float(_metadata_mapping(manifest).get("duration_s")) or 0.0
    artifacts.enforce_cap(estimate_import_bytes(duration_s, youtube=True))
    staging = artifacts.stage(ctx.job_id)
    try:
        native_target = staging / f"native{native_path.suffix.lower() or '.bin'}"
        _copy_native(native_path, native_target)
        decoded = media.decode_canonical(
            native_target,
            staging / CANONICAL_NAME,
            cancel=ctx.cancel,
            log=ctx.log,
        )
        ctx.progress(0.86, stage=STAGE_DECODING)
        peak_dbfs = media.peak_dbfs(decoded.path, cancel=ctx.cancel, log=ctx.log)
        gain_db = 0.0 if peak_dbfs <= 0.0 else -1.0 - peak_dbfs
        ctx.cancel.check()

        ctx.progress(0.90, stage=STAGE_FINISHING, detail="Encoding preview")
        preview_path = staging / PREVIEW_NAME
        media.encode_preview(
            decoded.path,
            preview_path,
            gain_db=gain_db,
            title=metadata.title,
            artist=metadata.artist,
            cancel=ctx.cancel,
            log=ctx.log,
        )
        ctx.cancel.check()

        ctx.progress(0.95, stage=STAGE_FINISHING, detail="Writing manifest")
        rebuilt = _source_manifest(
            source_id=source_id,
            metadata=metadata,
            downloaded=downloaded,
            native_path=native_target,
            canonical_path=decoded.path,
            preview_path=preview_path,
            duration_s=decoded.duration_s,
            sample_rate=decoded.sample_rate,
            channels=decoded.channels,
            downmixed=decoded.downmixed,
            native_bytes=native_bytes,
            native_sha256=native_sha256,
            gain_db=gain_db,
            tools=tools,
        )
        artifact = artifacts.commit_source(staging, source_id, rebuilt)
    except JobCancelled:
        artifacts.discard_staging(ctx.job_id)
        raise
    except StemForgeError as exc:
        artifacts.discard_staging(ctx.job_id)
        if exc.code in (
            errors.MEDIA_PROBE_FAILED,
            errors.MEDIA_VALIDATION_FAILED,
            errors.DECODE_FAILED,
            errors.IMPORT_NO_AUDIO_STREAM,
        ):
            ctx.log.warning("cached native audio could not be decoded: %s", exc.code)
            _discard_broken_source(ctx, artifacts, source_id)
            return None
        raise
    except BaseException:
        artifacts.discard_staging(ctx.job_id)
        raise

    source = _source_from_artifact(artifact, fallback_origin=metadata.canonical_url)
    ctx.progress(0.99, stage=STAGE_FINISHING, detail="Track added")
    track = finalize(source)
    ctx.progress(1.0, stage=STAGE_FINISHING)
    return YouTubeImportResult(
        source=source,
        track=track,
        artifact=artifact,
        cached=True,
    )


def _metadata_from_manifest(
    manifest: Mapping[str, object], source_id: str, video: YouTubeVideo
) -> YouTubeMetadata:
    record = _metadata_mapping(manifest)
    origin = _as_str(manifest.get("origin_url")) or _as_str(manifest.get("origin"))
    return YouTubeMetadata(
        video_id=_as_str(manifest.get("video_id")) or video.video_id,
        title=_as_str(record.get("title")) or f"YouTube {source_id.removeprefix('yt-')}",
        artist=_as_str(record.get("artist")),
        canonical_url=origin or video.canonical_url,
        duration_s=_as_float(record.get("duration_s")),
        upload_date=_as_str(record.get("date")),
    )


def _tools_from_manifest(manifest: Mapping[str, object]) -> Mapping[str, str]:
    raw = manifest.get("tools")
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(name): str(value)
        for name, value in cast("Mapping[object, object]", raw).items()
        if isinstance(name, str)
    }


def _discard_broken_source(ctx: JobContext, artifacts: ArtifactStore, source_id: str) -> None:
    try:
        artifacts.remove_source(source_id)
    except (OSError, ValueError, StemForgeError):
        ctx.log.warning("could not delete the broken cached source %s", source_id, exc_info=True)


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
            f"could not copy the cached native audio: {exc}",
            retryable=True,
            detail=str(exc),
            context={"source": str(source), "path": str(target), "errno": exc.errno},
        ) from exc


# --- pacing and validation -------------------------------------------------


def _polite_wait(
    ctx: JobContext,
    fraction: float,
    stage: str,
    sleep: Callable[[float], None],
    *,
    seconds: float = WAIT_SECONDS,
) -> None:
    """Wait between network phases, publishing the countdown at 2 Hz.

    The sleep function is injectable so tests do not spend real seconds; the
    Job bar still sees progress at least every 5 s while blocked (ticket 09 §3).
    """
    remaining = seconds
    while remaining > 0:
        ctx.cancel.check()
        ctx.progress(
            fraction,
            stage=stage,
            detail=f"Waiting for YouTube · {math.ceil(remaining)} s",
        )
        chunk = min(_WAIT_TICK_S, remaining)
        sleep(chunk)
        remaining -= chunk
    ctx.cancel.check()


def _require_duration_cap(duration_s: float) -> None:
    if duration_s > YOUTUBE_MAX_DURATION_S:
        raise StemForgeError(
            errors.IMPORT_YOUTUBE_TOO_LONG,
            "the video exceeds the 15-minute import limit",
            detail=f"duration={duration_s:.0f}s",
            context={"duration_s": duration_s},
        )


def _check_free_space(artifacts: ArtifactStore, duration_s: float | None) -> int:
    estimate = estimate_import_bytes(duration_s or 0.0, youtube=True)
    require_free_space(artifacts.root, free_space_requirement(estimate))
    return estimate


# --- files and manifests ---------------------------------------------------


def _move_native(downloaded: Path, staging: Path) -> Path:
    """Keep the native extension and move it to ``staging/native.<ext>``."""
    downloaded = Path(downloaded)
    suffix = downloaded.suffix.lower()
    if len(suffix) < 2 or len(suffix) - 1 > _MAX_SUFFIX_LENGTH or not suffix[1:].isalnum():
        suffix = ".bin"
    target = staging / f"{_NATIVE_NAME}{suffix}"
    if downloaded == target:
        return target
    try:
        os.replace(downloaded, target)
    except OSError as exc:
        code = (
            errors.STORAGE_DISK_FULL
            if exc.errno == errno.ENOSPC
            else errors.STORAGE_PERMISSION_DENIED
        )
        raise StemForgeError(
            code,
            f"could not move the downloaded audio into staging: {exc}",
            retryable=True,
            detail=str(exc),
            context={"source": str(downloaded), "path": str(target), "errno": exc.errno},
        ) from exc
    return target


def _file_size(path: Path) -> int:
    return path.stat().st_size


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
    metadata: YouTubeMetadata,
    downloaded: DownloadedAudio,
    native_path: Path,
    canonical_path: Path,
    preview_path: Path,
    duration_s: float,
    sample_rate: int,
    channels: int,
    downmixed: bool,
    native_bytes: int,
    native_sha256: str,
    gain_db: float,
    tools: Mapping[str, str],
) -> dict[str, object]:
    origin = metadata.canonical_url
    return {
        "schema_version": 1,
        "kind": "source",
        "source_id": source_id,
        "created_at": datetime.now(UTC).isoformat(),
        "origin": origin,
        "origin_kind": OriginKind.YOUTUBE.value,
        "origin_url": origin,
        "video_id": metadata.video_id,
        "content_hash": native_sha256,
        "app_version": __version__,
        "gain_db": gain_db,
        "downmixed": downmixed,
        "native_sha256": native_sha256,
        "native_bytes": native_bytes,
        "format_id": downloaded.format_id,
        "extractor": downloaded.extractor,
        "extractor_version": downloaded.extractor_version,
        "tools": {name: str(version) for name, version in tools.items()},
        "metadata": {
            "title": metadata.title,
            "artist": metadata.artist,
            "album": "",
            "album_artist": "",
            "track": "",
            "disc": "",
            "date": metadata.upload_date,
            "genre": "",
            "comment": origin,
            "composer": "",
            "copyright": "",
            "source_url": origin,
            "source_id": source_id,
            "duration_s": duration_s,
            "sample_rate": sample_rate,
            "channels": channels,
        },
        "files": [
            _file_entry(native_path, "native"),
            _file_entry(canonical_path, "canonical"),
            _file_entry(preview_path, "preview"),
        ],
    }


def _source_from_artifact(artifact: SourceArtifact, *, fallback_origin: str) -> Source:
    record = _metadata_mapping(artifact.manifest)
    origin_value = artifact.manifest.get("origin")
    origin = origin_value if isinstance(origin_value, str) and origin_value else fallback_origin
    metadata = SourceMetadata(
        title=_as_str(record.get("title")) or f"YouTube {artifact.source_id.removeprefix('yt-')}",
        artist=_as_str(record.get("artist")),
        album=_as_str(record.get("album")),
        duration_s=_as_float(record.get("duration_s")),
        sample_rate=_as_int(record.get("sample_rate")),
        channels=_as_int(record.get("channels")),
    )
    content_hash = artifact.manifest.get("content_hash")
    return Source(
        source_id=artifact.source_id,
        origin=origin,
        origin_kind=OriginKind.YOUTUBE,
        metadata=metadata,
        downmixed=artifact.manifest.get("downmixed") is True,
        gain_db=artifact.gain_db,
        native_path=artifact.path_for("native"),
        canonical_path=artifact.path_for("canonical"),
        preview_path=artifact.path_for("preview"),
        cover_path=None,
        content_hash=content_hash if isinstance(content_hash, str) else None,
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
