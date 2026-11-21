"""The durable model store: install, verify, and delete the pinned weights.

Layout (ticket 07 §4 and ticket 12)::

    <data>/models/htdemucs/<filename>
    <data>/models/htdemucs/<filename>.partial
    <data>/models/installed/<model>.json

The marker appears only after the streamed bytes matched the pinned size and
SHA-256. The store never bundles weights; ``verify`` re-hashes the file on every
call because ticket 07 §4 requires a re-hash before every Separation. There is
deliberately no cross-call hash cache: 84 MB is cheap against a multi-minute Job.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, cast

from stemforge import __version__, errors
from stemforge.domain.models import ModelConsent
from stemforge.errors import StemForgeError
from stemforge.models.spec import ModelSpec, default_spec
from stemforge.platform.paths import AppPaths

__all__ = [
    "ModelConsent",
    "ModelSpec",
    "ModelStatus",
    "ModelStore",
    "default_spec",
]

MODEL_DIRNAME: Final = "installed"
HASH_CHUNK_BYTES: Final = 1024 * 1024

STATUS_ABSENT: Final = "absent"
STATUS_CHECKSUM: Final = "checksum"


@dataclass(frozen=True, slots=True)
class ModelStatus:
    """The result of a cheap installation check or a full verification.

    ``installed`` comes from the file size and marker and is cheap enough for
    every UI render. ``verified`` means "hashed during this call"; only
    :meth:`ModelStore.verify` (and the before-use seam) set it.
    """

    name: str
    path: Path
    expected_size_bytes: int
    expected_sha256: str
    installed: bool
    verified: bool
    reason: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    verified_at: datetime | None = None

    @property
    def needs_download(self) -> bool:
        """Whether the UI should offer the download gate (``verify`` refines this)."""
        return not self.installed


class ModelStore:
    """Owns the model directory, marker, and verification rules."""

    def __init__(self, paths: AppPaths, *, spec: ModelSpec | None = None) -> None:
        self._paths = paths
        self._spec = spec

    @property
    def paths(self) -> AppPaths:
        return self._paths

    @property
    def root(self) -> Path:
        """The durable model root (``<data>/models``, also ``HF_HOME``)."""
        return self._paths.models

    @property
    def spec(self) -> ModelSpec:
        if self._spec is None:
            self._spec = default_spec()
        return self._spec

    def path(self) -> Path:
        """The final weights path."""
        return self.root / self.spec.name / self.spec.filename

    def partial_path(self) -> Path:
        """The in-progress download path; never the final name."""
        final = self.path()
        return final.with_name(f"{final.name}.partial")

    def marker_path(self) -> Path:
        """The ``installed/<model>.json`` verification marker."""
        return self.root / MODEL_DIRNAME / f"{self.spec.name}.json"

    def is_installed(self) -> bool:
        """Cheap check: the weights and a matching marker exist with the pinned size."""
        try:
            size = self.path().stat().st_size
        except OSError:
            return False
        if size != self.spec.size_bytes:
            return False
        marker = self._read_marker()
        return marker is not None and _marker_matches(marker, self.spec)

    def status(self) -> ModelStatus:
        """A cheap status for UI state; ``verified`` is only set by ``verify``."""
        try:
            size = self.path().stat().st_size
        except OSError:
            return self._status(installed=False, verified=False, reason=STATUS_ABSENT)
        marker = self._read_marker()
        if size != self.spec.size_bytes or marker is None or not _marker_matches(marker, self.spec):
            return self._status(installed=False, verified=False, reason=STATUS_ABSENT)
        return self._status(
            installed=True,
            verified=False,
            size_bytes=size,
            sha256=_marker_str(marker, "sha256"),
            verified_at=_parse_timestamp(marker.get("verified_at")),
        )

    def verify(self) -> ModelStatus:
        """Re-hash the weights and repair the marker; discard a mismatch.

        A size or SHA-256 mismatch deletes the weights and marker so the UI can
        offer a fresh download (ticket 07 §4). An unreadable file raises
        ``separation.engine_error``.
        """
        target = self.path()
        try:
            stat = target.stat()
        except FileNotFoundError:
            self._remove_marker()
            return self._status(installed=False, verified=False, reason=STATUS_ABSENT)
        except OSError as exc:
            raise self._read_error(target, exc) from exc

        if stat.st_size != self.spec.size_bytes:
            self._discard()
            return self._status(
                installed=False,
                verified=False,
                reason=STATUS_CHECKSUM,
                size_bytes=stat.st_size,
            )

        try:
            digest = sha256_file(target)
        except OSError as exc:
            raise self._read_error(target, exc) from exc

        if digest != self.spec.sha256:
            self._discard()
            return self._status(
                installed=False,
                verified=False,
                reason=STATUS_CHECKSUM,
                size_bytes=stat.st_size,
                sha256=digest,
            )

        verified_at = datetime.now(UTC)
        self._write_marker(size=stat.st_size, sha256=digest, verified_at=verified_at)
        return self._status(
            installed=True,
            verified=True,
            size_bytes=stat.st_size,
            sha256=digest,
            verified_at=verified_at,
        )

    def require_verified(self) -> ModelStatus:
        """Return a verified status or raise ``separation.model_missing``."""
        status = self.verify()
        if status.verified:
            return status
        reason = status.reason or STATUS_ABSENT
        message = (
            "the cached htdemucs model failed verification"
            if reason == STATUS_CHECKSUM
            else "the htdemucs model is not installed"
        )
        raise StemForgeError(
            errors.SEPARATION_MODEL_MISSING,
            message,
            detail=f"reason={reason}",
            context={
                "model": self.spec.name,
                "path": str(status.path),
                "reason": reason,
                "expected_size": self.spec.size_bytes,
                "actual_size": status.size_bytes,
            },
        )

    def delete(self) -> bool:
        """Delete the weights, any partial download, and the marker."""
        removed = False
        for target in (self.path(), self.partial_path(), self.marker_path()):
            try:
                target.unlink()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise self._write_error(target, exc, "delete the cached model") from exc
            removed = True
        return removed

    def discard_partial(self) -> None:
        """Best-effort removal of a failed or cancelled download."""
        try:
            self.partial_path().unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def install_bytes(self, data: bytes) -> ModelStatus:
        """Diagnostic install path: write known bytes through the real checks.

        This is used by tests and diagnostics, never by the download Job. A bad
        size or hash leaves no weights, partial, or marker.
        """
        digest = hashlib.sha256(data).hexdigest()
        if len(data) != self.spec.size_bytes or digest != self.spec.sha256:
            self.discard_partial()
            raise self._checksum_error(size=len(data), sha256=digest)

        partial = self.partial_path()
        try:
            partial.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            partial.write_bytes(data)
            partial.chmod(0o600)
        except OSError as exc:
            self.discard_partial()
            raise self._write_error(partial, exc, "write the model download") from exc
        return self.commit_verified_partial(size=len(data), sha256=digest)

    def commit_verified_partial(self, *, size: int, sha256: str) -> ModelStatus:
        """Promote a fully streamed partial after checking its pins.

        The caller computed the digest while streaming. The final rename is the
        only commit point; the marker is written afterwards, so a crash between
        the two leaves a file that ``verify`` can re-check and repair.
        """
        if size != self.spec.size_bytes or sha256.lower() != self.spec.sha256:
            self._discard()
            raise self._checksum_error(size=size, sha256=sha256)

        partial = self.partial_path()
        target = self.path()
        try:
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.replace(partial, target)
            target.chmod(0o600)
        except OSError as exc:
            self._discard()
            raise self._write_error(target, exc, "install the model") from exc

        verified_at = datetime.now(UTC)
        self._write_marker(size=size, sha256=sha256, verified_at=verified_at)
        return self._status(
            installed=True,
            verified=True,
            size_bytes=size,
            sha256=sha256,
            verified_at=verified_at,
        )

    # -- internals ----------------------------------------------------------

    def _status(
        self,
        *,
        installed: bool,
        verified: bool,
        reason: str | None = None,
        size_bytes: int | None = None,
        sha256: str | None = None,
        verified_at: datetime | None = None,
    ) -> ModelStatus:
        return ModelStatus(
            name=self.spec.name,
            path=self.path(),
            expected_size_bytes=self.spec.size_bytes,
            expected_sha256=self.spec.sha256,
            installed=installed,
            verified=verified,
            reason=reason,
            size_bytes=size_bytes,
            sha256=sha256,
            verified_at=verified_at,
        )

    def _checksum_error(self, *, size: int | None, sha256: str | None) -> StemForgeError:
        return StemForgeError(
            errors.SEPARATION_MODEL_MISSING,
            "the downloaded model did not match its pinned checksum",
            detail=f"reason={STATUS_CHECKSUM}",
            context={
                "model": self.spec.name,
                "expected_size": self.spec.size_bytes,
                "actual_size": size,
                "expected_sha256": self.spec.sha256,
                "actual_sha256": sha256,
            },
        )

    def _read_error(self, path: Path, exc: OSError) -> StemForgeError:
        return StemForgeError(
            errors.SEPARATION_ENGINE_ERROR,
            "the cached model could not be read",
            retryable=True,
            detail=f"{type(exc).__name__}: {exc}",
            context={"path": str(path), "errno": exc.errno},
        )

    def _write_error(self, path: Path, exc: OSError, action: str) -> StemForgeError:
        if exc.errno == errno.ENOSPC:
            code = errors.STORAGE_DISK_FULL
        elif exc.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
            code = errors.STORAGE_PERMISSION_DENIED
        else:
            code = errors.SEPARATION_ENGINE_ERROR
        return StemForgeError(
            code,
            f"could not {action}",
            retryable=True,
            detail=f"{type(exc).__name__}: {exc}",
            context={"path": str(path), "errno": exc.errno},
        )

    def _discard(self) -> None:
        """Remove the weights and marker after a failed verification."""
        for target in (self.path(), self.partial_path(), self.marker_path()):
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass

    def _remove_marker(self) -> None:
        try:
            self.marker_path().unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def _read_marker(self) -> Mapping[str, object] | None:
        try:
            payload: object = json.loads(self.marker_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        return cast("Mapping[str, object]", payload)

    def _write_marker(self, *, size: int, sha256: str, verified_at: datetime) -> None:
        marker = self.marker_path()
        payload = {
            "model": self.spec.name,
            "revision": self.spec.revision,
            "filename": self.spec.filename,
            "sha256": sha256,
            "size": size,
            "verified_at": verified_at.isoformat(),
            "app_version": __version__,
        }
        data = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        try:
            marker.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor, temp_name = tempfile.mkstemp(
                prefix=f".{marker.name}.", suffix=".tmp", dir=marker.parent
            )
            temp_path = Path(temp_name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                temp_path.chmod(0o600)
                os.replace(temp_path, marker)
            except BaseException:
                temp_path.unlink(missing_ok=True)
                raise
        except OSError as exc:
            raise self._write_error(marker, exc, "write the model marker") from exc


def sha256_file(path: Path) -> str:
    """Hash a file in 1 MiB chunks."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _marker_matches(marker: Mapping[str, object], spec: ModelSpec) -> bool:
    return (
        marker.get("model") == spec.name
        and marker.get("revision") == spec.revision
        and marker.get("filename") == spec.filename
        and marker.get("sha256") == spec.sha256
        and marker.get("size") == spec.size_bytes
    )


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _marker_str(marker: Mapping[str, object], key: str) -> str | None:
    value = marker.get(key)
    return value if isinstance(value, str) else None
