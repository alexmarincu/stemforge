"""Model install Job: resumable pinned download, verification, and marker install.

The Job is the only code that downloads model weights. It appends to
``<models>/htdemucs/<filename>.partial`` and asks for the remaining bytes with
``Range: bytes=<size>-`` when a failed or cancelled attempt left data behind.
Only a stream that matches the pinned size and SHA-256 is promoted with one
``os.replace`` and followed by the ``installed/<model>.json`` marker. A partial
that is still short stays on disk for the next retry; a complete-but-wrong file
is discarded because it cannot be resumed.

The transport is injected so tests exercise resume, streaming, hashing, and
install without a network. Production uses :mod:`urllib.request`; the transport
only has to expose ``status``, ``headers``, ``read(size)``, and ``close()``.
"""

from __future__ import annotations

import hashlib
import http.client
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol, cast

from stemforge import errors
from stemforge.artifacts import (
    estimate_model_install_bytes,
    free_space_requirement,
    require_free_space,
)
from stemforge.domain.models import ModelConsent
from stemforge.domain.progress import (
    DETAIL_MODEL_ALREADY_INSTALLED,
    DETAIL_MODEL_DOWNLOAD_PROGRESS,
    STAGE_MODEL_CONSENT,
    STAGE_MODEL_DOWNLOAD,
    STAGE_MODEL_INSTALL,
    STAGE_MODEL_VERIFY,
)
from stemforge.errors import StemForgeError
from stemforge.jobs.job import JobContext
from stemforge.models import ModelStatus, ModelStore
from stemforge.models.store import sha256_file

__all__ = [
    "STAGE_CONSENT",
    "STAGE_DOWNLOAD",
    "STAGE_INSTALL",
    "STAGE_VERIFY",
    "DownloadResponse",
    "ModelInstallJob",
    "ModelInstallResult",
    "Transport",
    "consent_is_current",
    "require_model_consent",
]

STAGE_CONSENT: Final = "Consent"
STAGE_DOWNLOAD: Final = "Download"
STAGE_VERIFY: Final = "Verify"
STAGE_INSTALL: Final = "Install"

DOWNLOAD_START: Final = 0.02
DOWNLOAD_END: Final = 0.90
VERIFIED_END: Final = 0.95
INSTALL_START: Final = 0.98
DOWNLOAD_TIMEOUT_S: Final = 60.0
READ_CHUNK_BYTES: Final = 1024 * 1024


class DownloadResponse(Protocol):
    """The slice of an HTTP response the downloader needs."""

    status: int
    headers: Mapping[str, str]

    def read(self, size: int = -1, /) -> bytes: ...

    def close(self) -> None: ...


Transport = Callable[[str, Mapping[str, str] | None], DownloadResponse]


class _NetworkFailure(Exception):
    """A classified transport failure; automatic retries are forbidden."""

    def __init__(self, reason: str, *, detail: str, cause: BaseException) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail
        self.cause = cause


@dataclass(frozen=True, slots=True)
class ModelInstallResult:
    """What a successful install Job produced."""

    status: ModelStatus
    downloaded: bool


@dataclass(frozen=True, slots=True)
class ModelInstallJob:
    """Download, verify, and install the pinned model.

    Consent is required only when bytes must be downloaded; an already-verified
    cache is an idempotent success and never touches the network.
    """

    store: ModelStore
    consent: ModelConsent | None = None
    transport: Transport | None = None

    def run(self, ctx: JobContext) -> ModelInstallResult:
        """Run the whole install, publishing ``Consent`` → ``Download`` → … stages."""
        ctx.cancel.check()
        ctx.progress(0.0, stage=STAGE_CONSENT, stage_code=STAGE_MODEL_CONSENT)

        existing = self.store.verify()
        if existing.verified:
            ctx.progress(
                1.0,
                stage=STAGE_INSTALL,
                stage_code=STAGE_MODEL_INSTALL,
                detail="already installed",
                detail_code=DETAIL_MODEL_ALREADY_INSTALLED,
            )
            return ModelInstallResult(status=existing, downloaded=False)

        require_model_consent(self.consent, required_version=self.store.spec.licence_notice_version)
        ctx.progress(0.01, stage=STAGE_CONSENT, stage_code=STAGE_MODEL_CONSENT)
        require_free_space(self.store.root, free_space_requirement(estimate_model_install_bytes()))
        ctx.cancel.check()

        partial = self.store.partial_path()
        ctx.progress(DOWNLOAD_START, stage=STAGE_DOWNLOAD, stage_code=STAGE_MODEL_DOWNLOAD)
        size, digest = self._download(ctx, partial)
        ctx.cancel.check()
        spec = self.store.spec
        if size != spec.size_bytes:
            raise _download_error(
                _NetworkFailure(
                    "incomplete",
                    detail=f"{size} of {spec.size_bytes} bytes",
                    cause=OSError("the response ended before the pinned size"),
                ),
                spec.download_url,
            )
        ctx.progress(DOWNLOAD_END, stage=STAGE_VERIFY, stage_code=STAGE_MODEL_VERIFY)
        status = self.store.commit_verified_partial(size=size, sha256=digest)
        ctx.progress(VERIFIED_END, stage=STAGE_VERIFY, stage_code=STAGE_MODEL_VERIFY)
        ctx.progress(INSTALL_START, stage=STAGE_INSTALL, stage_code=STAGE_MODEL_INSTALL)
        result = ModelInstallResult(status=status, downloaded=True)
        ctx.progress(1.0, stage=STAGE_INSTALL, stage_code=STAGE_MODEL_INSTALL)
        return result

    # -- download -----------------------------------------------------------

    def _download(self, ctx: JobContext, partial: Path) -> tuple[int, str]:
        try:
            return self._download_once(ctx, partial)
        except _NetworkFailure as failure:
            raise _download_error(failure, self.store.spec.download_url) from failure.cause

    def _download_once(self, ctx: JobContext, partial: Path) -> tuple[int, str]:
        spec = self.store.spec
        existing = _partial_size(partial, limit=spec.size_bytes)
        if existing == spec.size_bytes:
            # A previous attempt completed the stream but stopped before the
            # marker was written; verify the retained bytes without a network.
            return existing, sha256_file(partial)

        response: DownloadResponse | None = None
        try:
            headers = {"Range": f"bytes={existing}-"} if existing else None
            try:
                opener = self.transport or _urlopen
                response = opener(spec.download_url, headers)
            except urllib.error.HTTPError as exc:
                raise _NetworkFailure(
                    "http",
                    detail=f"HTTP {exc.code}",
                    cause=exc,
                ) from exc
            except urllib.error.URLError as exc:
                raise _NetworkFailure(
                    "offline",
                    detail=f"{type(exc.reason).__name__}: {exc.reason}",
                    cause=exc,
                ) from exc
            except (TimeoutError, ConnectionError, http.client.HTTPException, OSError) as exc:
                raise _NetworkFailure(
                    "offline",
                    detail=f"{type(exc).__name__}: {exc}",
                    cause=exc,
                ) from exc

            resuming = existing > 0 and response.status == 206
            if not resuming:
                existing = 0
            digest = hashlib.sha256()
            downloaded = existing
            if resuming:
                try:
                    digest = _sha256_prefix(partial, existing)
                except OSError as exc:
                    raise self._storage_error(partial, exc) from exc
            try:
                partial.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                handle = partial.open("ab" if resuming else "wb")
            except OSError as exc:
                raise self._storage_error(partial, exc) from exc

            with handle:
                while True:
                    ctx.cancel.check()
                    try:
                        chunk = response.read(READ_CHUNK_BYTES)
                    except (
                        TimeoutError,
                        ConnectionError,
                        http.client.HTTPException,
                        OSError,
                    ) as exc:
                        raise _NetworkFailure(
                            "offline",
                            detail=f"{type(exc).__name__}: {exc}",
                            cause=exc,
                        ) from exc
                    if not chunk:
                        break
                    try:
                        handle.write(chunk)
                    except OSError as exc:
                        raise self._storage_error(partial, exc) from exc
                    digest.update(chunk)
                    downloaded += len(chunk)
                    ctx.progress(
                        _download_progress(downloaded, spec.size_bytes),
                        stage=STAGE_DOWNLOAD,
                        stage_code=STAGE_MODEL_DOWNLOAD,
                        detail=_download_detail(downloaded, spec.size_bytes),
                        detail_code=DETAIL_MODEL_DOWNLOAD_PROGRESS,
                        detail_context={
                            "downloaded": f"{downloaded / 1_000_000:.1f}",
                            "total": f"{spec.size_bytes / 1_000_000:.1f}",
                        },
                    )
                    ctx.cancel.check()
            return downloaded, digest.hexdigest()
        finally:
            if response is not None:
                _close(response)

    def _storage_error(self, path: Path, exc: OSError) -> StemForgeError:
        import errno

        if exc.errno == errno.ENOSPC:
            code = errors.STORAGE_DISK_FULL
        elif exc.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
            code = errors.STORAGE_PERMISSION_DENIED
        else:
            code = errors.SEPARATION_ENGINE_ERROR
        return StemForgeError(
            code,
            "could not write the model download",
            retryable=True,
            detail=f"{type(exc).__name__}: {exc}",
            context={"path": str(path), "errno": exc.errno},
        )


def _download_error(failure: _NetworkFailure, url: str) -> StemForgeError:
    return StemForgeError(
        errors.SEPARATION_MODEL_DOWNLOAD_FAILED,
        "the model download failed",
        retryable=True,
        detail=f"reason={failure.reason}",
        context={"url": url, "reason": failure.reason},
    )


def consent_is_current(consent: ModelConsent | None, *, required_version: int) -> bool:
    """Whether a persisted consent satisfies the bundled notice version."""
    return bool(
        consent is not None and consent.acknowledged and consent.version >= required_version
    )


def require_model_consent(consent: ModelConsent | None, *, required_version: int) -> None:
    """Refuse a download until the user accepted the current licence notice."""
    if consent_is_current(consent, required_version=required_version):
        return
    raise StemForgeError(
        errors.SEPARATION_MODEL_MISSING,
        "the model licence notice has not been accepted",
        detail="reason=consent",
        context={
            "reason": "consent",
            "required_version": required_version,
            "consent_version": None if consent is None else consent.version,
        },
    )


def _partial_size(partial: Path, *, limit: int) -> int:
    """The bytes a retry can keep; an oversized or unreadable partial restarts."""
    try:
        size = partial.stat().st_size
    except OSError:
        return 0
    return size if 0 <= size <= limit else 0


def _sha256_prefix(path: Path, size: int) -> Any:
    digest = hashlib.sha256()
    remaining = size
    with path.open("rb") as handle:
        while remaining > 0:
            chunk = handle.read(min(READ_CHUNK_BYTES, remaining))
            if not chunk:
                raise OSError("the partial ended before its recorded size")
            digest.update(chunk)
            remaining -= len(chunk)
    return digest


def _download_progress(downloaded: int, total: int) -> float:
    """Map transferred bytes into the Download stage, monotonically."""
    fraction = min(1.0, downloaded / total) if total > 0 else 0.0
    return DOWNLOAD_START + (DOWNLOAD_END - DOWNLOAD_START) * fraction


def _download_detail(downloaded: int, total: int) -> str:
    return f"{downloaded / 1_000_000:.1f} / {total / 1_000_000:.1f} MB"


def _close(response: DownloadResponse) -> None:
    with suppress(OSError):
        response.close()


def _urlopen(url: str, headers: Mapping[str, str] | None = None) -> DownloadResponse:
    """The production transport; the only place that opens a socket."""
    request = urllib.request.Request(url, headers=dict(headers or {}))
    return cast(
        "DownloadResponse",
        urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_S),
    )
