"""``ArtifactStore``: the cache layout, manifests, staging, and promotion.

The cache under ``<cache>`` is the only persistent record of imported and derived
audio (ADR 0004). Each artifact set is a directory with a schema-versioned
manifest whose paths are relative to that directory; the manifest is written last
inside ``staging/<job_id>/`` and the completed directory is promoted with one
atomic rename (ADR 0012). No database and no second index exist.

Layout::

    sources/<source_id>/native.<ext>
    sources/<source_id>/canonical.wav
    sources/<source_id>/original.mp3
    sources/<source_id>/cover.<ext>
    sources/<source_id>/source.json
    sources/<source_id>/stems/... + manifest.json
    renders/<render_key>/... + manifest.json
    staging/<job_id>/
    tmp/

The store is the only writer of artifact files. It never parses a filename to
infer state; manifests are the source of truth.

Manifest schema (schema version 1; all paths are POSIX-relative to the manifest's
own directory)::

    {
      "schema_version": 1,
      "kind": "source" | "separation" | "render",
      "source_id": "<id>",          # separation and render
      "render_key": "<key>",        # render only
      "created_at": "<ISO-8601>",
      "gain_db": 0.0,               # optional
      "files": [
        {"path": "canonical.wav", "role": "canonical",
         "size": 123, "sha256": "<64 hex>"}
      ]
    }

Every file must exist and, when the manifest records one, match its size; a
recorded ``sha256`` is provenance and is checked only by a deep verification
(``verify_hashes=True``) or when a set is committed. Routine lookups stay
metadata-only so the UI never reads hundreds of megabytes of audio per refresh.
Caller-specific fields (metadata, semitones, engine version,
validation result) are preserved as written. A schema newer than
:data:`SCHEMA_VERSION` is ignored by lookup and left untouched by the sweep, but
remains eligible for LRU eviction.
"""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Final, cast
from uuid import uuid4

from stemforge import errors
from stemforge.errors import StemForgeError
from stemforge.platform.paths import AppPaths
from stemforge.platform.storage import DEFAULT_CACHE_CAP_BYTES

_LOG = logging.getLogger(__name__)

SCHEMA_VERSION: Final = 1
SOURCE_MANIFEST_NAME: Final = "source.json"
ARTIFACT_MANIFEST_NAME: Final = "manifest.json"
USED_MARKER_NAME: Final = ".used"
EVICTION_LEDGER_NAME: Final = "evictions.jsonl"

SOURCE_KIND: Final = "source"
SEPARATION_KIND: Final = "separation"
RENDER_KIND: Final = "render"

CACHE_KIND_SOURCE: Final = "source"
CACHE_KIND_RENDER: Final = "render"

# PCM 44.1 kHz stereo float32 WAV, MP3 192 preview, WAV 24 export.
MIB: Final = 1024 * 1024
GIB: Final = 1024 * MIB
PCM_F32_BYTES_PER_MINUTE: Final = 21_200_000
PREVIEW_BYTES_PER_MINUTE: Final = 1_440_000
EXPORT_WAV24_BYTES_PER_MINUTE: Final = 15_900_000
YOUTUBE_IMPORT_BYTES_PER_MINUTE: Final = 24_000_000
MODEL_BYTES: Final = 84_025_440
JOB_HEADROOM: Final = 1.2
FREE_SPACE_HEADROOM: Final = 1.5

_IDENTITY_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_WINDOWS_RESERVED: Final = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
)


# ---------------------------------------------------------------------------
# Disk-space estimates (pure)
# ---------------------------------------------------------------------------


def estimate_import_bytes(
    duration_s: float,
    *,
    native_bytes: int | None = None,
    youtube: bool = False,
) -> int:
    """Estimate an import's cache footprint, including the 1.2 job headroom."""
    minutes = _minutes(duration_s)
    if youtube:
        return ceil(YOUTUBE_IMPORT_BYTES_PER_MINUTE * minutes * JOB_HEADROOM)
    if native_bytes is None:
        raise ValueError("native_bytes is required for a local import")
    if native_bytes < 0:
        raise ValueError("native_bytes must not be negative")
    return ceil(
        (native_bytes + (PCM_F32_BYTES_PER_MINUTE + PREVIEW_BYTES_PER_MINUTE) * minutes)
        * JOB_HEADROOM
    )


def estimate_separation_bytes(duration_s: float) -> int:
    """Five WAVs plus five previews, including the 1.2 job headroom."""
    minutes = _minutes(duration_s)
    return ceil(5 * (PCM_F32_BYTES_PER_MINUTE + PREVIEW_BYTES_PER_MINUTE) * minutes * JOB_HEADROOM)


def estimate_render_bytes(duration_s: float, *, outputs: int = 1) -> int:
    """One original or five stem outputs, each a WAV plus preview."""
    minutes = _minutes(duration_s)
    if outputs < 1:
        raise ValueError("outputs must be positive")
    return ceil(
        outputs * (PCM_F32_BYTES_PER_MINUTE + PREVIEW_BYTES_PER_MINUTE) * minutes * JOB_HEADROOM
    )


def estimate_model_install_bytes() -> int:
    """Two model copies plus 256 MiB of working room."""
    return 2 * MODEL_BYTES + 256 * MIB


def estimate_export_bytes(duration_s: float) -> int:
    """Worst-case WAV 24 export: 1.2 headroom plus 64 MiB."""
    minutes = _minutes(duration_s)
    return ceil(EXPORT_WAV24_BYTES_PER_MINUTE * minutes * JOB_HEADROOM + 64 * MIB)


def free_space_requirement(estimate: int, *, floor: int = GIB) -> int:
    """The free space a Job needs before it starts: max(floor, estimate x 1.5)."""
    if estimate < 0:
        raise ValueError("estimate must not be negative")
    return max(floor, ceil(estimate * FREE_SPACE_HEADROOM))


def require_free_space(path: Path, needed: int, *, floor: int = GIB) -> None:
    """Fail ``storage.disk_full`` unless ``max(floor, needed)`` bytes are free.

    ``needed`` is the final requirement (use :func:`free_space_requirement` for
    the 1.5x rule). Export checks pass ``floor=0`` so a small file can
    fit a nearly full destination.
    """
    if needed < 0:
        raise ValueError("needed must not be negative")
    required = max(floor, needed)
    target = _existing_ancestor(Path(path))
    try:
        free = shutil.disk_usage(target).free
    except OSError as exc:
        _raise_wrapped(exc, f"measure free space for {target}")
    if free < required:
        raise StemForgeError(
            errors.STORAGE_DISK_FULL,
            f"not enough free space on {target}: need {required} bytes, have {free}",
            retryable=True,
            context={"path": str(target), "required": required, "free": free},
        )


def _minutes(duration_s: float) -> float:
    if duration_s < 0:
        raise ValueError("duration must not be negative")
    return duration_s / 60.0


def _existing_ancestor(path: Path) -> Path:
    candidate = path.expanduser().absolute()
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


# ---------------------------------------------------------------------------
# Public records
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ManifestFile:
    """One file referenced by a manifest, with paths relative to the manifest."""

    path: str
    role: str | None = None
    sha256: str | None = None
    size: int | None = None


@dataclass(frozen=True, slots=True)
class SourceArtifact:
    """A committed ``sources/<source_id>/`` set."""

    source_id: str
    directory: Path
    manifest_path: Path
    manifest: Mapping[str, object]
    files: tuple[ManifestFile, ...]
    created_at: datetime
    gain_db: float

    def path_for(self, role: str) -> Path | None:
        """Return the file with ``role``, if the manifest records one."""
        for entry in self.files:
            if entry.role == role:
                return self.directory / entry.path
        return None

    @property
    def canonical_path(self) -> Path | None:
        return self.path_for("canonical")

    @property
    def native_path(self) -> Path | None:
        return self.path_for("native")

    @property
    def preview_path(self) -> Path | None:
        return self.path_for("preview")

    @property
    def cover_path(self) -> Path | None:
        return self.path_for("cover")


@dataclass(frozen=True, slots=True)
class SourceManifest:
    """A structurally valid ``source.json`` even when derived files are missing.

    Recovery uses this when :meth:`ArtifactStore.find_source` refuses an
    incomplete set: the record is trusted only for what the manifest says, and
    the caller re-validates the native file's size and hash before repairing it.
    It is not a committed artifact set and never satisfies a cache lookup.
    """

    source_id: str
    directory: Path
    manifest_path: Path
    manifest: Mapping[str, object]
    files: tuple[ManifestFile, ...]
    created_at: datetime

    def entry_for(self, role: str) -> ManifestFile | None:
        """Return the file entry with ``role``, if the manifest records one."""
        for entry in self.files:
            if entry.role == role:
                return entry
        return None

    def path_for(self, role: str) -> Path | None:
        """Return the recorded path with ``role``; existence is not implied."""
        entry = self.entry_for(role)
        return None if entry is None else self.directory / entry.path


@dataclass(frozen=True, slots=True)
class SeparationArtifact:
    """A committed ``sources/<source_id>/stems/`` set."""

    source_id: str
    directory: Path
    manifest_path: Path
    manifest: Mapping[str, object]
    files: tuple[ManifestFile, ...]
    created_at: datetime
    gain_db: float

    def path_for(self, role: str) -> Path | None:
        for entry in self.files:
            if entry.role == role:
                return self.directory / entry.path
        return None

    @property
    def stems(self) -> Mapping[str, Path]:
        """Every file whose role names a stem, keyed by role."""
        return MappingProxyType(
            {
                entry.role: self.directory / entry.path
                for entry in self.files
                if entry.role is not None and entry.role.endswith("_preview") is False
            }
        )


@dataclass(frozen=True, slots=True)
class RenderArtifact:
    """A committed ``renders/<render_key>/`` set."""

    render_key: str
    source_id: str
    directory: Path
    manifest_path: Path
    manifest: Mapping[str, object]
    files: tuple[ManifestFile, ...]
    created_at: datetime
    gain_db: float

    def path_for(self, role: str) -> Path | None:
        for entry in self.files:
            if entry.role == role:
                return self.directory / entry.path
        return None


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class ArtifactStore:
    """Owns the on-disk artifact layout and validates every manifest."""

    def __init__(
        self,
        paths: AppPaths | None = None,
        *,
        cap_bytes: int | None = None,
        live_keys: Callable[[], AbstractSet[str]] | None = None,
    ) -> None:
        self._paths = paths if paths is not None else AppPaths.from_environment()
        if cap_bytes is None:
            cap_bytes = DEFAULT_CACHE_CAP_BYTES
        if cap_bytes < 0:
            raise ValueError("cache cap must not be negative")
        self._cap_bytes = cap_bytes
        self._live_keys_callback = live_keys
        self._lock = threading.RLock()
        self._retained: dict[str, int] = {}

    # -- layout -------------------------------------------------------------

    @property
    def paths(self) -> AppPaths:
        return self._paths

    @property
    def root(self) -> Path:
        return self._paths.cache

    @property
    def sources_dir(self) -> Path:
        return self.root / "sources"

    @property
    def renders_dir(self) -> Path:
        return self.root / "renders"

    @property
    def staging_dir(self) -> Path:
        return self.root / "staging"

    @property
    def tmp_dir(self) -> Path:
        return self.root / "tmp"

    @property
    def youtube_cache_dir(self) -> Path:
        """The yt-dlp tool cache; "Clear YouTube cache" deletes only this."""
        return self.root / "yt-dlp"

    @property
    def eviction_ledger_path(self) -> Path:
        """The append-only cache eviction journal used by diagnostics."""
        return self.root / EVICTION_LEDGER_NAME

    @property
    def cap_bytes(self) -> int:
        return self._cap_bytes

    def _ensure_roots(self) -> None:
        with self._lock:
            try:
                self._paths = self._paths.ensure()
                for directory in (
                    self.sources_dir,
                    self.renders_dir,
                    self.staging_dir,
                    self.tmp_dir,
                ):
                    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            except OSError as exc:
                _raise_wrapped(exc, "create the cache roots")

    # -- staging ------------------------------------------------------------

    def stage(self, job_id: str) -> Path:
        """Create ``staging/<job_id>/`` for one Job and return it."""
        _validate_identity(job_id, "job id")
        self._ensure_roots()
        staging = self.staging_dir / job_id
        try:
            if staging.is_symlink() or staging.exists():
                _remove_tree(staging)
            staging.mkdir(mode=0o700, parents=False, exist_ok=False)
        except OSError as exc:
            _raise_wrapped(exc, f"create staging for {job_id}")
        return staging

    def discard_staging(self, job_id: str) -> bool:
        """Delete ``staging/<job_id>/`` after a cancel or failure.

        Missing staging is not an error: a Job that failed before staging or one
        whose directory was already promoted has nothing left to discard.
        """
        _validate_identity(job_id, "job id")
        staging = self.staging_dir / job_id
        with self._lock:
            existed = staging.exists() or staging.is_symlink()
            try:
                _remove_tree(staging)
            except OSError as exc:
                _raise_wrapped(exc, f"discard staging for {job_id}")
            return existed

    # -- commits ------------------------------------------------------------

    def commit_source(
        self, staging: Path, source_id: str, manifest: Mapping[str, object]
    ) -> SourceArtifact:
        """Write ``source.json`` last and promote a staged Source atomically."""
        _validate_identity(source_id, "source id")
        staging = self._validated_staging(staging)
        payload = _normalize_payload(
            manifest, kind=SOURCE_KIND, identity_field="source_id", identity=source_id
        )
        _validate_referenced_files(payload, staging, required_roles=frozenset({"canonical"}))
        self._ensure_roots()
        self._write_manifest(staging, SOURCE_MANIFEST_NAME, payload)

        with self._lock:
            target = self.sources_dir / source_id
            existing = self.find_source(source_id, verify_hashes=True)
            if existing is not None:
                _remove_checked(staging, "discard the redundant staging directory")
                self.touch_source(source_id)
                return existing
            self._promote(staging, target)
            self.touch_source(source_id)

        artifact = self.find_source(source_id)
        if artifact is None:  # pragma: no cover - defensive: we just committed it
            raise StemForgeError(errors.INTERNAL_UNEXPECTED, "committed source is unreadable")
        return artifact

    def commit_separation(
        self, staging: Path, source_id: str, manifest: Mapping[str, object]
    ) -> SeparationArtifact:
        """Promote a staged Separation set into ``sources/<id>/stems/``."""
        _validate_identity(source_id, "source id")
        staging = self._validated_staging(staging)
        payload = _normalize_payload(
            manifest, kind=SEPARATION_KIND, identity_field="source_id", identity=source_id
        )
        _validate_referenced_files(payload, staging)
        self._ensure_roots()
        source_dir = self.sources_dir / source_id
        if self.find_source(source_id) is None:
            raise ValueError(f"source is not committed: {source_id}")
        self._write_manifest(staging, ARTIFACT_MANIFEST_NAME, payload)

        with self._lock:
            target = source_dir / "stems"
            self._promote(staging, target)

        artifact = self.find_separation(source_id)
        if artifact is None:  # pragma: no cover - defensive
            raise StemForgeError(errors.INTERNAL_UNEXPECTED, "committed separation is unreadable")
        return artifact

    def commit_render(
        self, staging: Path, render_key: str, manifest: Mapping[str, object]
    ) -> RenderArtifact:
        """Write ``manifest.json`` last and promote a staged Render atomically."""
        _validate_identity(render_key, "render key")
        staging = self._validated_staging(staging)
        payload = _normalize_payload(
            manifest, kind=RENDER_KIND, identity_field="render_key", identity=render_key
        )
        source_id = payload.get("source_id")
        if not isinstance(source_id, str):
            raise ValueError("render manifest requires a source_id")
        _validate_identity(source_id, "source id")
        _validate_referenced_files(payload, staging)
        self._ensure_roots()
        self._write_manifest(staging, ARTIFACT_MANIFEST_NAME, payload)

        with self._lock:
            target = self.renders_dir / render_key
            existing = self.find_render(render_key, verify_hashes=True)
            if existing is not None:
                _remove_checked(staging, "discard the redundant staging directory")
                self.touch_render(render_key)
                return existing
            self._promote(staging, target)
            self.touch_render(render_key)

        artifact = self.find_render(render_key)
        if artifact is None:  # pragma: no cover - defensive
            raise StemForgeError(errors.INTERNAL_UNEXPECTED, "committed render is unreadable")
        return artifact

    def _validated_staging(self, staging: Path) -> Path:
        try:
            resolved = Path(staging).resolve(strict=True)
            staging_root = self.staging_dir.resolve(strict=False)
        except OSError as exc:
            _raise_wrapped(exc, "resolve staging")
        if not resolved.is_dir() or resolved.parent != staging_root:
            raise ValueError(f"staging path is not a job directory: {staging}")
        return resolved

    def _write_manifest(self, directory: Path, name: str, payload: Mapping[str, object]) -> None:
        data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        descriptor: int | None = None
        temp_path: Path | None = None
        try:
            descriptor, temp_name = tempfile.mkstemp(
                prefix=f".{name}.", suffix=".tmp", dir=directory
            )
            temp_path = Path(temp_name)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                descriptor = None
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, directory / name)
            temp_path = None
        except OSError as exc:
            _raise_wrapped(exc, f"write manifest {name}")
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    _LOG.warning("could not remove the manifest temp file %s", temp_path)

    def _promote(self, staging: Path, target: Path) -> None:
        """Move ``staging`` to ``target``; an existing target is replaced."""
        aside: Path | None = None
        try:
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if target.is_symlink() or target.is_file():
                target.unlink()
            if not target.exists():
                os.replace(staging, target)
                return
            aside = target.parent / f".{target.name}.old-{uuid4().hex}"
            os.replace(target, aside)
            try:
                os.replace(staging, target)
            except BaseException:
                os.replace(aside, target)
                raise
        except OSError as exc:
            _raise_wrapped(exc, "promote the artifact set")
        if aside is not None:
            try:
                _remove_tree(aside)
            except OSError:
                _LOG.warning("could not remove the replaced artifact set %s", aside)

    # -- lookup -------------------------------------------------------------

    def find_source(self, source_id: str, *, verify_hashes: bool = False) -> SourceArtifact | None:
        """Return a committed Source, or ``None`` when it is not usable.

        Routine lookups validate the manifest, file existence, and recorded
        sizes only. ``verify_hashes=True`` additionally re-reads every file and
        compares its SHA-256, which is appropriate for a Job that is about to
        consume the audio, never for a UI refresh.
        """
        if not _is_valid_identity(source_id):
            return None
        directory = self.sources_dir / source_id
        result = _read_manifest(
            directory / SOURCE_MANIFEST_NAME,
            base_dir=directory,
            expect_kind=SOURCE_KIND,
            identity_field="source_id",
            identity=source_id,
            required_roles=frozenset({"canonical"}),
            verify_hashes=verify_hashes,
        )
        if result.status is not _ManifestStatus.VALID:
            return None
        assert result.payload is not None
        assert result.created_at is not None
        return SourceArtifact(
            source_id=source_id,
            directory=directory,
            manifest_path=directory / SOURCE_MANIFEST_NAME,
            manifest=result.payload,
            files=result.files,
            created_at=result.created_at,
            gain_db=_gain_db(result.payload),
        )

    def read_source_manifest(self, source_id: str) -> SourceManifest | None:
        """Read a source manifest without requiring every referenced file.

        ADR 0004 makes the store the only manifest reader. Recovery needs the
        recorded native entry when a canonical or preview file is missing, so
        this validates the schema, identity, timestamp, and path confinement
        while leaving existence and hashes to the caller.
        """
        if not _is_valid_identity(source_id):
            return None
        directory = self.sources_dir / source_id
        result = _read_manifest(
            directory / SOURCE_MANIFEST_NAME,
            base_dir=directory,
            expect_kind=SOURCE_KIND,
            identity_field="source_id",
            identity=source_id,
            required_roles=frozenset(),
            require_files=False,
        )
        if result.status is not _ManifestStatus.VALID:
            return None
        assert result.payload is not None
        assert result.created_at is not None
        return SourceManifest(
            source_id=source_id,
            directory=directory,
            manifest_path=directory / SOURCE_MANIFEST_NAME,
            manifest=result.payload,
            files=result.files,
            created_at=result.created_at,
        )

    def find_separation(
        self, source_id: str, *, verify_hashes: bool = False
    ) -> SeparationArtifact | None:
        """Return the committed Stems set; see :meth:`find_source` for integrity."""
        if not _is_valid_identity(source_id):
            return None
        directory = self.sources_dir / source_id / "stems"
        result = _read_manifest(
            directory / ARTIFACT_MANIFEST_NAME,
            base_dir=directory,
            expect_kind=SEPARATION_KIND,
            identity_field="source_id",
            identity=source_id,
            required_roles=frozenset(),
            verify_hashes=verify_hashes,
        )
        if result.status is not _ManifestStatus.VALID:
            return None
        assert result.payload is not None
        assert result.created_at is not None
        return SeparationArtifact(
            source_id=source_id,
            directory=directory,
            manifest_path=directory / ARTIFACT_MANIFEST_NAME,
            manifest=result.payload,
            files=result.files,
            created_at=result.created_at,
            gain_db=_gain_db(result.payload),
        )

    def find_render(self, render_key: str, *, verify_hashes: bool = False) -> RenderArtifact | None:
        """Return a committed Render set; see :meth:`find_source` for integrity."""
        if not _is_valid_identity(render_key):
            return None
        directory = self.renders_dir / render_key
        result = _read_manifest(
            directory / ARTIFACT_MANIFEST_NAME,
            base_dir=directory,
            expect_kind=RENDER_KIND,
            identity_field="render_key",
            identity=render_key,
            required_roles=frozenset(),
            verify_hashes=verify_hashes,
        )
        if result.status is not _ManifestStatus.VALID:
            return None
        assert result.payload is not None
        assert result.created_at is not None
        source_id = result.payload.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            return None
        return RenderArtifact(
            render_key=render_key,
            source_id=source_id,
            directory=directory,
            manifest_path=directory / ARTIFACT_MANIFEST_NAME,
            manifest=result.payload,
            files=result.files,
            created_at=result.created_at,
            gain_db=_gain_db(result.payload),
        )

    def iter_sources(self) -> Iterator[SourceArtifact]:
        directory = self.sources_dir
        if not directory.is_dir():
            return
        for entry in sorted(directory.iterdir(), key=lambda item: item.name):
            if not entry.is_dir() or entry.is_symlink():
                continue
            artifact = self.find_source(entry.name)
            if artifact is not None:
                yield artifact

    def iter_renders(self) -> Iterator[RenderArtifact]:
        directory = self.renders_dir
        if not directory.is_dir():
            return
        for entry in sorted(directory.iterdir(), key=lambda item: item.name):
            if not entry.is_dir() or entry.is_symlink():
                continue
            artifact = self.find_render(entry.name)
            if artifact is not None:
                yield artifact

    # -- recency ------------------------------------------------------------

    def touch_source(self, source_id: str) -> bool:
        return self._touch_marker(self.sources_dir / source_id)

    def touch_render(self, render_key: str) -> bool:
        return self._touch_marker(self.renders_dir / render_key)

    def _touch_marker(self, directory: Path) -> bool:
        if not _is_valid_identity(directory.name) or not directory.is_dir():
            return False
        marker = directory / USED_MARKER_NAME
        try:
            marker.touch(exist_ok=True)
            os.utime(marker, None)
        except OSError as exc:
            _raise_wrapped(exc, f"touch {marker}")
        return True

    # -- sweep --------------------------------------------------------------

    def sweep_stale(self) -> SweepReport:
        """Delete crash residue, never a committed artifact set.

        Only staging directories, tmp files, partial files, and the
        ``.*.old-*`` directories a crashed replacement leaves behind are ours to
        remove. A committed source, separation, or render with a stray or
        invalid manifest is left in place: lookups already treat it as absent,
        and recovery must never delete finished work (ADR 0012).
        """
        self._ensure_roots()
        with self._lock:
            removed_tmp = self._sweep_children(self.tmp_dir)
            removed_staging = self._sweep_children(self.staging_dir)
            removed_partials = self._sweep_partials()
            removed_residue = self._sweep_residue()
        return SweepReport(
            removed_tmp=removed_tmp,
            removed_staging=removed_staging,
            removed_partials=removed_partials,
            removed_residue=removed_residue,
        )

    def _sweep_children(self, directory: Path) -> tuple[Path, ...]:
        if not directory.is_dir() or directory.is_symlink():
            return ()
        removed: list[Path] = []
        for entry in sorted(directory.iterdir(), key=lambda item: item.name):
            try:
                _remove_tree(entry)
            except OSError as exc:
                _raise_wrapped(exc, f"remove {entry}")
            removed.append(entry)
        return tuple(removed)

    def _sweep_partials(self) -> tuple[Path, ...]:
        removed: list[Path] = []
        for base in (self.sources_dir, self.renders_dir):
            if not base.is_dir():
                continue
            matches = sorted(
                base.rglob("*.partial*"), key=lambda item: len(item.parts), reverse=True
            )
            for path in matches:
                if not path.exists() and not path.is_symlink():
                    continue
                try:
                    _remove_tree(path)
                except OSError as exc:
                    _raise_wrapped(exc, f"remove partial {path}")
                removed.append(path)
        return tuple(removed)

    def _sweep_residue(self) -> tuple[Path, ...]:
        """Remove ``.*.old-*`` directories left by an interrupted replacement."""
        removed: list[Path] = []
        for base in (self.sources_dir, self.renders_dir):
            if not base.is_dir():
                continue
            for path in sorted(base.rglob(".*.old-*"), key=lambda item: len(item.parts)):
                if not path.exists() and not path.is_symlink():
                    continue
                try:
                    _remove_tree(path)
                except OSError as exc:
                    _raise_wrapped(exc, f"remove replacement residue {path}")
                removed.append(path)
        return tuple(removed)

    # -- cache usage and LRU ------------------------------------------------

    def usage_bytes(self) -> int:
        """Bytes used by ``sources/`` and ``renders/`` only."""
        with self._lock:
            return sum(
                _directory_size(directory) for directory in (self.sources_dir, self.renders_dir)
            )

    def enforce_cap(self, needed_bytes: int = 0) -> EvictionReport:
        """Evict whole non-live sets oldest-first until the cap admits a reservation.

        ``needed_bytes`` is the next Job's output estimate: the
        cache must reach ``usage + needed_bytes <= cap``, not merely fit today's
        bytes. Raises ``storage.cache_full`` when everything left is live.
        """
        if needed_bytes < 0:
            raise ValueError("needed bytes must not be negative")
        with self._lock:
            entries = self._cache_entries()
            usage_before = sum(entry.size_bytes for entry in entries)
            protected = self._live_snapshot()
            plan = plan_evictions(
                entries,
                cap_bytes=self._cap_bytes,
                live_keys=protected,
                needed_bytes=needed_bytes,
            )
            for entry in plan.evicted:
                base = self.sources_dir if entry.kind == CACHE_KIND_SOURCE else self.renders_dir
                try:
                    _remove_tree(base / entry.key)
                except OSError as exc:
                    _raise_wrapped(exc, f"evict {entry.key}")
            self._record_evictions(plan.evicted)
            usage_after = self.usage_bytes()
            if usage_after + needed_bytes > self._cap_bytes:
                raise StemForgeError(
                    errors.STORAGE_CACHE_FULL,
                    "cache is full and everything left is in use",
                    retryable=True,
                    context={
                        "usage": usage_after,
                        "cap": self._cap_bytes,
                        "needed": usage_after + needed_bytes - self._cap_bytes,
                    },
                )
            return EvictionReport(
                evicted=plan.evicted,
                usage_before=usage_before,
                usage_after=usage_after,
                cap_bytes=self._cap_bytes,
            )

    def _record_evictions(self, entries: Sequence[CacheEntry]) -> None:
        """Append evicted keys to the diagnostics journal; never fail the eviction."""
        if not entries:
            return
        stamp = datetime.now(UTC).isoformat()
        try:
            with self.eviction_ledger_path.open("a", encoding="utf-8") as handle:
                for entry in entries:
                    payload = {
                        "at": stamp,
                        "key": entry.key,
                        "kind": entry.kind,
                        "bytes": entry.size_bytes,
                    }
                    handle.write(json.dumps(payload, sort_keys=True) + "\n")
        except OSError:
            _LOG.warning("could not record cache evictions", exc_info=True)

    def eviction_count(self, since: datetime) -> int:
        """Count evictions at or after ``since`` from the local journal."""
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
        try:
            lines = self.eviction_ledger_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return 0
        count = 0
        for line in lines:
            try:
                payload = json.loads(line)
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            stamp = _parse_datetime(payload.get("at"))
            if stamp is not None and stamp >= since:
                count += 1
        return count

    def _cache_entries(self) -> tuple[CacheEntry, ...]:
        entries: list[CacheEntry] = []
        for kind, directory in (
            (CACHE_KIND_SOURCE, self.sources_dir),
            (CACHE_KIND_RENDER, self.renders_dir),
        ):
            if not directory.is_dir() or directory.is_symlink():
                continue
            for entry in sorted(directory.iterdir(), key=lambda item: item.name):
                if entry.is_symlink() or not entry.is_dir():
                    continue
                manifest_name = (
                    SOURCE_MANIFEST_NAME if kind == CACHE_KIND_SOURCE else ARTIFACT_MANIFEST_NAME
                )
                entries.append(
                    CacheEntry(
                        kind=kind,
                        key=entry.name,
                        size_bytes=_directory_size(entry),
                        used_at=self._used_at(entry, manifest_name),
                        source_id=entry.name
                        if kind == CACHE_KIND_SOURCE
                        else _manifest_source_id(entry / manifest_name),
                    )
                )
        return tuple(entries)

    def _used_at(self, directory: Path, manifest_name: str) -> float:
        marker = directory / USED_MARKER_NAME
        try:
            return marker.stat().st_mtime
        except OSError:
            pass
        created_at = _manifest_created_at(directory / manifest_name)
        if created_at is not None:
            return created_at.timestamp()
        try:
            return directory.stat().st_mtime
        except OSError:
            return 0.0

    def _live_snapshot(self, extra: AbstractSet[str] = frozenset()) -> AbstractSet[str]:
        with self._lock:
            live = set(extra)
            live.update(key for key, count in self._retained.items() if count > 0)
            live.update(self._callback_live_keys())
            return frozenset(live)

    # -- refcount hooks -----------------------------------------------------

    def retain(self, key: str) -> None:
        """Mark one cache entry as referenced by the live Workspace."""
        with self._lock:
            self._retained[key] = self._retained.get(key, 0) + 1

    def release(self, key: str) -> bool:
        """Drop one reference; returns ``False`` when none was held."""
        with self._lock:
            count = self._retained.get(key, 0)
            if count == 0:
                return False
            if count == 1:
                del self._retained[key]
            else:
                self._retained[key] = count - 1
            return True

    def is_live(self, key: str) -> bool:
        with self._lock:
            return self._retained.get(key, 0) > 0 or key in self._callback_live_keys()

    def _callback_live_keys(self) -> AbstractSet[str]:
        if self._live_keys_callback is None:
            return frozenset()
        return self._live_keys_callback()

    def remove_source(self, source_id: str) -> RemoveReport:
        """Delete one source subtree and its derived renders once released."""
        _validate_identity(source_id, "source id")
        with self._lock:
            if self.is_live(source_id):
                raise ValueError(f"source is still in use: {source_id}")
            live = self._live_snapshot()
            removed_renders: list[str] = []
            if self.renders_dir.is_dir() and not self.renders_dir.is_symlink():
                for entry in sorted(self.renders_dir.iterdir(), key=lambda item: item.name):
                    if entry.is_symlink() or not entry.is_dir():
                        continue
                    manifest = entry / ARTIFACT_MANIFEST_NAME
                    if _manifest_source_id(manifest) != source_id:
                        continue
                    candidate = CacheEntry(
                        kind=CACHE_KIND_RENDER,
                        key=entry.name,
                        size_bytes=0,
                        used_at=0.0,
                        source_id=source_id,
                    )
                    if _entry_is_live(candidate, live):
                        continue
                    try:
                        _remove_tree(entry)
                    except OSError as exc:
                        _raise_wrapped(exc, f"remove render {entry.name}")
                    removed_renders.append(entry.name)

            source_dir = self.sources_dir / source_id
            existed = source_dir.exists() or source_dir.is_symlink()
            if existed:
                try:
                    _remove_tree(source_dir)
                except OSError as exc:
                    _raise_wrapped(exc, f"remove source {source_id}")
            return RemoveReport(source_removed=existed, renders_removed=tuple(removed_renders))

    def youtube_cache_bytes(self) -> int:
        """The size of ``<cache>/yt-dlp/`` for the Settings & About row."""
        with self._lock:
            return _directory_size(self.youtube_cache_dir)

    def youtube_scratch(self, job_id: str) -> Path:
        """The per-Job yt-dlp download scratch under ``<cache>/yt-dlp/``."""
        _validate_identity(job_id, "job id")
        return self.youtube_cache_dir / job_id

    def create_youtube_scratch(self, job_id: str) -> Path:
        """Create one Job's yt-dlp scratch and return it."""
        scratch = self.youtube_scratch(job_id)
        try:
            scratch.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            _raise_wrapped(exc, f"create the YouTube scratch for {job_id}")
        return scratch

    def discard_youtube_scratch(self, job_id: str) -> bool:
        """Delete one Job's yt-dlp scratch after a cancel or failure."""
        scratch = self.youtube_scratch(job_id)
        with self._lock:
            existed = scratch.exists() or scratch.is_symlink()
            try:
                _remove_tree(scratch)
            except OSError as exc:
                _raise_wrapped(exc, f"discard the YouTube scratch for {job_id}")
            return existed

    def clear_youtube_cache(self) -> int:
        """Delete ``<cache>/yt-dlp/`` only; return the bytes freed.

        Never ``sources/``, ``renders/``, recent files, the model
        store, or the Deno cache.
        """
        with self._lock:
            directory = self.youtube_cache_dir
            freed = _directory_size(directory)
            try:
                _remove_tree(directory)
            except OSError as exc:
                _raise_wrapped(exc, "clear the YouTube cache")
            return freed

    def clear_cache(self) -> ClearReport:
        """Delete every non-live source/render set plus tool caches."""
        with self._lock:
            usage_before = self.usage_bytes()
            live = self._live_snapshot()
            removed_sources: list[str] = []
            removed_renders: list[str] = []
            for kind, directory, target in (
                (CACHE_KIND_SOURCE, self.sources_dir, removed_sources),
                (CACHE_KIND_RENDER, self.renders_dir, removed_renders),
            ):
                if not directory.is_dir() or directory.is_symlink():
                    continue
                for entry in sorted(directory.iterdir(), key=lambda item: item.name):
                    if entry.is_symlink() or not entry.is_dir():
                        _remove_checked(entry, f"clear {entry.name}")
                        continue
                    manifest = (
                        entry / SOURCE_MANIFEST_NAME
                        if kind == CACHE_KIND_SOURCE
                        else entry / ARTIFACT_MANIFEST_NAME
                    )
                    candidate = CacheEntry(
                        kind=kind,
                        key=entry.name,
                        size_bytes=0,
                        used_at=0.0,
                        source_id=entry.name
                        if kind == CACHE_KIND_SOURCE
                        else _manifest_source_id(manifest),
                    )
                    if _entry_is_live(candidate, live):
                        continue
                    try:
                        _remove_tree(entry)
                    except OSError as exc:
                        _raise_wrapped(exc, f"clear {entry.name}")
                    target.append(entry.name)
            for name in ("tmp", "deno", "yt-dlp"):
                path = self.root / name
                try:
                    _remove_tree(path)
                except OSError as exc:
                    _raise_wrapped(exc, f"clear {name}")
            self._ensure_roots()
            usage_after = self.usage_bytes()
            return ClearReport(
                sources_removed=tuple(removed_sources),
                renders_removed=tuple(removed_renders),
                freed_bytes=max(0, usage_before - usage_after),
            )


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SweepReport:
    """What :meth:`ArtifactStore.sweep_stale` removed."""

    removed_tmp: tuple[Path, ...] = ()
    removed_staging: tuple[Path, ...] = ()
    removed_partials: tuple[Path, ...] = ()
    removed_residue: tuple[Path, ...] = ()

    @property
    def deleted_anything(self) -> bool:
        """True only for crash residue that triggers the recovery banner."""
        return bool(self.removed_tmp or self.removed_staging or self.removed_partials)


@dataclass(frozen=True, slots=True)
class EvictionReport:
    """The outcome of one LRU enforcement pass."""

    evicted: tuple[CacheEntry, ...]
    usage_before: int
    usage_after: int
    cap_bytes: int


@dataclass(frozen=True, slots=True)
class RemoveReport:
    """What :meth:`ArtifactStore.remove_source` deleted."""

    source_removed: bool
    renders_removed: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ClearReport:
    """What :meth:`ArtifactStore.clear_cache` deleted."""

    sources_removed: tuple[str, ...]
    renders_removed: tuple[str, ...]
    freed_bytes: int


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """One LRU unit: a whole source subtree or a whole render set."""

    kind: str
    key: str
    size_bytes: int
    used_at: float
    source_id: str | None = None


@dataclass(frozen=True, slots=True)
class EvictionPlan:
    """A pure decision about which entries an LRU pass must evict."""

    evicted: tuple[CacheEntry, ...]
    usage_before: int
    remaining_bytes: int
    over_cap: bool


def plan_evictions(
    entries: Sequence[CacheEntry],
    *,
    cap_bytes: int,
    live_keys: AbstractSet[str] = frozenset(),
    needed_bytes: int = 0,
) -> EvictionPlan:
    """Choose whole non-live sets oldest-first until usage plus a reservation fits.

    A live source also protects every render whose manifest names it: those are
    artifacts of the live Workspace even when the render itself is not retained.
    ``over_cap`` is true only when everything left is live.
    """
    if needed_bytes < 0:
        raise ValueError("needed bytes must not be negative")
    target = cap_bytes - needed_bytes
    usage = sum(entry.size_bytes for entry in entries)
    if usage <= target:
        return EvictionPlan((), usage, usage, False)

    candidates = sorted(
        (entry for entry in entries if not _entry_is_live(entry, live_keys)),
        key=lambda entry: (entry.used_at, _cache_kind_rank(entry.kind), entry.key),
    )
    remaining = usage
    evicted: list[CacheEntry] = []
    for entry in candidates:
        if remaining <= target:
            break
        evicted.append(entry)
        remaining -= entry.size_bytes
    return EvictionPlan(tuple(evicted), usage, remaining, remaining > target)


def _entry_is_live(entry: CacheEntry, live_keys: AbstractSet[str]) -> bool:
    if entry.key in live_keys:
        return True
    return (
        entry.kind == CACHE_KIND_RENDER
        and entry.source_id is not None
        and entry.source_id in live_keys
    )


def _cache_kind_rank(kind: str) -> int:
    return 0 if kind == CACHE_KIND_SOURCE else 1


# ---------------------------------------------------------------------------
# Manifest parsing
# ---------------------------------------------------------------------------


class _ManifestStatus:
    VALID = "valid"
    MISSING = "missing"
    INVALID = "invalid"
    NEWER = "newer"


@dataclass(frozen=True, slots=True)
class _ManifestResult:
    status: str
    payload: Mapping[str, object] | None = None
    files: tuple[ManifestFile, ...] = ()
    created_at: datetime | None = None


def _read_manifest(
    manifest_path: Path,
    *,
    base_dir: Path,
    expect_kind: str,
    identity_field: str,
    identity: str,
    required_roles: AbstractSet[str],
    require_files: bool = True,
    verify_hashes: bool = False,
) -> _ManifestResult:
    """Parse and validate one manifest.

    Existence and recorded sizes are always checked when ``require_files`` is
    set; recorded hashes are re-read only when ``verify_hashes`` is set. The
    default keeps a lookup proportional to the manifest's metadata rather than
    to the size of the audio it references.
    """
    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _ManifestResult(_ManifestStatus.MISSING)
    except OSError:
        return _ManifestResult(_ManifestStatus.MISSING)

    try:
        decoded: object = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return _ManifestResult(_ManifestStatus.INVALID)
    if not isinstance(decoded, dict):
        return _ManifestResult(_ManifestStatus.INVALID)
    payload = cast("dict[str, object]", decoded)

    version = payload.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool):
        return _ManifestResult(_ManifestStatus.INVALID)
    if version > SCHEMA_VERSION:
        return _ManifestResult(_ManifestStatus.NEWER)
    if version != SCHEMA_VERSION:
        return _ManifestResult(_ManifestStatus.INVALID)

    if payload.get("kind") != expect_kind:
        return _ManifestResult(_ManifestStatus.INVALID)
    if payload.get(identity_field) != identity:
        return _ManifestResult(_ManifestStatus.INVALID)

    created_at = _parse_datetime(payload.get("created_at"))
    if created_at is None:
        return _ManifestResult(_ManifestStatus.INVALID)

    raw_files = payload.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        return _ManifestResult(_ManifestStatus.INVALID)

    files: list[ManifestFile] = []
    seen_paths: set[str] = set()
    for raw_entry in raw_files:
        entry = _parse_manifest_file(
            raw_entry,
            base_dir,
            require_file=require_files,
            verify_hashes=verify_hashes,
        )
        if entry is None:
            return _ManifestResult(_ManifestStatus.INVALID)
        if entry.path in seen_paths:
            return _ManifestResult(_ManifestStatus.INVALID)
        seen_paths.add(entry.path)
        files.append(entry)

    if not required_roles <= {entry.role for entry in files}:
        return _ManifestResult(_ManifestStatus.INVALID)

    return _ManifestResult(
        _ManifestStatus.VALID,
        payload=MappingProxyType(payload),
        files=tuple(files),
        created_at=created_at,
    )


def _parse_manifest_file(
    raw_entry: object,
    base_dir: Path,
    *,
    require_file: bool = True,
    verify_hashes: bool = False,
) -> ManifestFile | None:
    if not isinstance(raw_entry, dict):
        return None
    entry = cast("dict[str, object]", raw_entry)
    relative = entry.get("path")
    if not isinstance(relative, str) or not relative:
        return None
    target = _resolve_member(base_dir, relative, must_exist=require_file)
    if target is None:
        return None

    role = entry.get("role")
    if role is not None and not isinstance(role, str):
        return None

    raw_size = entry.get("size")
    size: int | None = None
    if raw_size is not None:
        if not isinstance(raw_size, int) or isinstance(raw_size, bool) or raw_size < 0:
            return None
        size = raw_size

    raw_hash = entry.get("sha256")
    sha256: str | None = None
    if raw_hash is not None:
        if not isinstance(raw_hash, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", raw_hash):
            return None
        sha256 = raw_hash.lower()

    if not require_file:
        return ManifestFile(path=relative, role=role, sha256=sha256, size=size)

    try:
        stat_result = target.stat()
    except OSError:
        return None
    if not target.is_file():
        return None
    if size is not None and stat_result.st_size != size:
        return None
    if verify_hashes and sha256 is not None and _sha256_file(target) != sha256:
        return None
    return ManifestFile(path=relative, role=role, sha256=sha256, size=size)


def _resolve_member(base_dir: Path, relative: str, *, must_exist: bool = True) -> Path | None:
    """Resolve a manifest path and refuse absolute, traversing, or symlink escapes."""
    if "\\" in relative or ":" in relative:
        return None
    pure = PurePosixPath(relative)
    if pure.is_absolute() or not pure.parts:
        return None
    if any(part in {"", ".", ".."} for part in pure.parts):
        return None
    target = base_dir.joinpath(*pure.parts)
    try:
        if target.is_symlink():
            return None
        resolved = target.resolve(strict=must_exist)
        root = base_dir.resolve(strict=must_exist)
    except OSError:
        return None
    if not resolved.is_relative_to(root):
        return None
    return resolved


def _validate_referenced_files(
    payload: Mapping[str, object],
    base_dir: Path,
    *,
    required_roles: AbstractSet[str] = frozenset(),
) -> None:
    raw_files = payload.get("files")
    assert isinstance(raw_files, list)
    parsed: list[ManifestFile] = []
    for raw_entry in raw_files:
        entry = _parse_manifest_file(raw_entry, base_dir, verify_hashes=True)
        if entry is None:
            raise ValueError(f"manifest references a missing or invalid file: {raw_entry!r}")
        parsed.append(entry)
    if not required_roles <= {entry.role for entry in parsed}:
        raise ValueError(f"manifest is missing required roles: {sorted(required_roles)}")


def _normalize_payload(
    manifest: Mapping[str, object],
    *,
    kind: str,
    identity_field: str,
    identity: str,
) -> dict[str, object]:
    if not isinstance(manifest, Mapping):
        raise ValueError("manifest must be a mapping")
    payload = dict(manifest)

    version = payload.get("schema_version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise ValueError(f"unsupported manifest schema: {version!r}")
    payload["schema_version"] = SCHEMA_VERSION

    declared_kind = payload.get("kind", kind)
    if declared_kind != kind:
        raise ValueError(f"manifest kind must be {kind!r}: {declared_kind!r}")
    payload["kind"] = kind

    declared_identity = payload.get(identity_field, identity)
    if declared_identity != identity:
        raise ValueError(f"manifest {identity_field} must be {identity!r}")
    payload[identity_field] = identity

    created_at = payload.get("created_at")
    if created_at is None:
        payload["created_at"] = datetime.now(UTC).isoformat()
    elif _parse_datetime(created_at) is None:
        raise ValueError("manifest created_at is not an ISO timestamp")

    files = payload.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("manifest files must be a non-empty list")
    return payload


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _gain_db(payload: Mapping[str, object]) -> float:
    value = payload.get("gain_db", 0.0)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _directory_size(path: Path) -> int:
    try:
        if path.is_symlink() or not path.is_dir():
            return 0
    except OSError:
        return 0
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path, followlinks=False):
        for name in filenames:
            try:
                total += (Path(dirpath) / name).stat(follow_symlinks=False).st_size
            except OSError:
                continue
    return total


def _read_manifest_json(path: Path) -> Mapping[str, object] | None:
    """Best-effort read used for LRU metadata, including newer schema versions."""
    try:
        decoded: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(decoded, dict):
        return None
    return cast("Mapping[str, object]", decoded)


def _manifest_created_at(path: Path) -> datetime | None:
    payload = _read_manifest_json(path)
    if payload is None:
        return None
    return _parse_datetime(payload.get("created_at"))


def _manifest_source_id(path: Path) -> str | None:
    payload = _read_manifest_json(path)
    if payload is None:
        return None
    source_id = payload.get("source_id")
    return source_id if isinstance(source_id, str) else None


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _is_valid_identity(value: object) -> bool:
    if not isinstance(value, str) or _IDENTITY_PATTERN.fullmatch(value) is None:
        return False
    return value.split(".", 1)[0].upper() not in _WINDOWS_RESERVED


def _validate_identity(value: object, label: str) -> None:
    if not _is_valid_identity(value):
        raise ValueError(f"invalid {label}: {value!r}")


def _raise_wrapped(exc: OSError, action: str) -> None:
    code = (
        errors.STORAGE_DISK_FULL if exc.errno == errno.ENOSPC else errors.STORAGE_PERMISSION_DENIED
    )
    raise StemForgeError(
        code,
        f"could not {action}: {exc}",
        retryable=True,
        detail=str(exc),
        context={"errno": exc.errno},
    ) from exc


def _remove_tree(path: Path) -> None:
    """Delete a file, symlink, or directory without following symlinks."""
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink(missing_ok=True)


def _remove_checked(path: Path, action: str) -> None:
    try:
        _remove_tree(path)
    except OSError as exc:
        _raise_wrapped(exc, action)
