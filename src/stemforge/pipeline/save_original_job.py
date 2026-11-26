"""Save original Job: verify and copy a local Source's Native file.

Save original is a sibling of Export (ADR 0017), not an Export format. It reads
the cache's recorded ``native`` entry, copies it byte for byte to the user's
chosen destination, and checks the recorded SHA-256 while copying. It never
probes, decodes, encodes, rewrites tags, applies gain, resamples, or downmixes.

The destination gets the same guarantees as Export: a ``.partial`` file,
``fsync``, a collision-safe rename, and cleanup on every handled failure or
cancel. A YouTube Source is refused because its recorded native file is the
downloaded media, not a file the user supplied.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from stemforge import errors
from stemforge.artifacts import ArtifactStore, require_free_space
from stemforge.domain.cancellation import CancelToken
from stemforge.domain.models import OriginKind
from stemforge.domain.progress import (
    DETAIL_SAVE_ORIGINAL_CHECKING_DESTINATION,
    DETAIL_SAVE_ORIGINAL_COPYING_FILE,
    DETAIL_SAVE_ORIGINAL_FINAL_NAME,
    DETAIL_SAVE_ORIGINAL_WRITING_FILE,
    STAGE_SAVE_ORIGINAL_COPYING,
    STAGE_SAVE_ORIGINAL_WRITING,
    CodedText,
    context_progress,
)
from stemforge.errors import StemForgeError
from stemforge.jobs.job import JobContext

# Export and Save original share one destination write edge; importing these
# helpers keeps the partial, fsync, collision, and cleanup guarantees identical.
from stemforge.pipeline.export_job import (
    _commit_without_overwrite,
    _destination_error,
    _remove,
    _require_destination,
)
from stemforge.pipeline.export_names import (
    destination_base_name,
    suggest_base_name,
    unique_export_path,
)

__all__ = [
    "STAGE_COPYING",
    "STAGE_WRITING",
    "NativeFile",
    "SaveOriginalResult",
    "require_native_file",
    "run_save_original_job",
]

STAGE_COPYING: Final = "Copying"
STAGE_WRITING: Final = "Writing file"

_STAGE_CODE_BY_TEXT: Final[Mapping[str, str]] = {
    STAGE_COPYING: STAGE_SAVE_ORIGINAL_COPYING,
    STAGE_WRITING: STAGE_SAVE_ORIGINAL_WRITING,
}

_COPY_CHUNK_BYTES: Final = 1024 * 1024


@dataclass(frozen=True, slots=True)
class NativeFile:
    """The recorded Native file facts a Save original Job needs."""

    source_id: str
    path: Path
    suffix: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class SaveOriginalResult:
    """The copied file and the facts the Job result carries."""

    path: Path
    size_bytes: int
    sha256: str


def require_native_file(
    artifacts: ArtifactStore,
    source_id: str,
    *,
    verify_size: bool = False,
) -> NativeFile:
    """Return a local Source's Native file, or fail ``export.original_unavailable``.

    ``verify_size`` is the Job's cheap check against the recorded size; the
    copy itself verifies the recorded SHA-256. A missing manifest, native role,
    recorded hash, or file is ``missing``; a YouTube Source is ``not_local``.
    """
    manifest = artifacts.read_source_manifest(source_id)
    if manifest is None:
        raise _unavailable("missing", source_id)

    if manifest.manifest.get("origin_kind") != OriginKind.FILE.value:
        raise _unavailable("not_local", source_id)

    entry = manifest.entry_for("native")
    native = manifest.path_for("native")
    if entry is None or native is None or entry.sha256 is None:
        raise _unavailable("missing", source_id)

    try:
        if not native.is_file():
            raise _unavailable("missing", source_id, path=native)
        actual_size = native.stat().st_size
    except OSError as exc:
        raise _unavailable("missing", source_id, path=native) from exc

    if verify_size and entry.size is not None and actual_size != entry.size:
        raise _unavailable("changed", source_id, path=native)

    suffix = native.suffix.lower()
    if len(suffix) < 2 or not suffix[1:].isalnum():
        raise _unavailable("missing", source_id, path=native)

    return NativeFile(
        source_id=source_id,
        path=native,
        suffix=suffix,
        size_bytes=entry.size if entry.size is not None else actual_size,
        sha256=entry.sha256,
    )


def run_save_original_job(
    ctx: JobContext,
    *,
    artifacts: ArtifactStore,
    source_id: str,
    title: str,
    destination_dir: Path,
    destination_name: str | None = None,
) -> SaveOriginalResult:
    """Copy one local Source's Native file to ``destination_dir`` unchanged.

    ``destination_name`` is the save dialog's chosen name; when omitted the
    Track title plus the Native file's extension is used. The destination
    directory must already exist and be writable. The free-space requirement is
    the Native file's own size, not an encoded worst case.
    """
    cancel = ctx.cancel
    log = ctx.log
    emit = context_progress(ctx, _STAGE_CODE_BY_TEXT)
    cancel.check()

    native = require_native_file(artifacts, source_id, verify_size=True)
    destination = Path(destination_dir).expanduser().absolute()
    base_name = (
        destination_base_name(destination_name, native.suffix)
        if destination_name
        else suggest_base_name(title)
    )

    emit(
        0.02,
        STAGE_COPYING,
        CodedText(
            "checking the destination",
            code=DETAIL_SAVE_ORIGINAL_CHECKING_DESTINATION,
        ),
    )
    _require_destination(destination)
    require_free_space(destination, native.size_bytes, floor=0)
    cancel.check()

    free_name = unique_export_path(destination, base_name, native.suffix).name
    partial = destination / f"{free_name}.partial"
    emit(
        0.10,
        STAGE_COPYING,
        CodedText("copying the file", code=DETAIL_SAVE_ORIGINAL_COPYING_FILE),
    )
    try:
        digest = _copy_verified(
            native,
            partial,
            cancel,
            on_progress=lambda copied: emit(
                0.10 + 0.80 * min(1.0, copied / max(1, native.size_bytes)),
                STAGE_COPYING,
                CodedText(
                    "copying the file",
                    code=DETAIL_SAVE_ORIGINAL_COPYING_FILE,
                ),
            ),
        )
        emit(
            0.95,
            STAGE_WRITING,
            CodedText("writing the file", code=DETAIL_SAVE_ORIGINAL_WRITING_FILE),
        )
        cancel.check()
        final_path = _commit_without_overwrite(
            partial,
            destination=destination,
            base_name=base_name,
            suffix=native.suffix,
            required=native.size_bytes,
        )
    except StemForgeError:
        _remove(partial)
        raise
    except OSError as exc:
        _remove(partial)
        raise _destination_error(exc, destination, required=native.size_bytes) from exc
    except BaseException:
        _remove(partial)
        raise

    emit(
        1.0,
        STAGE_WRITING,
        CodedText(
            final_path.name,
            code=DETAIL_SAVE_ORIGINAL_FINAL_NAME,
            context={"name": final_path.name},
        ),
    )
    log.info(
        "saved original %s (%d bytes, sha256=%s)",
        final_path,
        native.size_bytes,
        digest,
    )
    return SaveOriginalResult(
        path=final_path,
        size_bytes=native.size_bytes,
        sha256=digest,
    )


def _copy_verified(
    native: NativeFile,
    partial: Path,
    cancel: CancelToken,
    *,
    on_progress: Callable[[int], None],
) -> str:
    """Copy while hashing; remove the partial on cancel or a changed source."""
    try:
        reader = native.path.open("rb")
    except OSError as exc:
        raise _unavailable("missing", native.source_id, path=native.path) from exc

    digest = hashlib.sha256()
    copied = 0
    try:
        with reader, partial.open("wb") as writer:
            while True:
                cancel.check()
                try:
                    chunk = reader.read(_COPY_CHUNK_BYTES)
                except OSError as exc:
                    raise _unavailable("missing", native.source_id, path=native.path) from exc
                if not chunk:
                    break
                writer.write(chunk)
                digest.update(chunk)
                copied += len(chunk)
                on_progress(copied)
            writer.flush()
            os.fsync(writer.fileno())
    except BaseException:
        _remove(partial)
        raise

    actual = digest.hexdigest()
    if copied != native.size_bytes or actual != native.sha256:
        _remove(partial)
        raise StemForgeError(
            errors.EXPORT_ORIGINAL_UNAVAILABLE,
            "the native file no longer matches its recorded checksum",
            detail="changed",
            context={
                "source_id": native.source_id,
                "path": str(native.path),
                "expected_size": native.size_bytes,
                "actual_size": copied,
                "expected_sha256": native.sha256,
                "actual_sha256": actual,
            },
        )
    return actual


def _unavailable(
    detail: str,
    source_id: str,
    *,
    path: Path | None = None,
) -> StemForgeError:
    messages = {
        "missing": "the source has no readable native file",
        "changed": "the native file no longer matches its recorded size",
        "not_local": "the source is not a local file",
    }
    context: dict[str, object] = {"source_id": source_id}
    if path is not None:
        context["path"] = str(path)
    return StemForgeError(
        errors.EXPORT_ORIGINAL_UNAVAILABLE,
        messages[detail],
        detail=detail,
        context=context,
    )
