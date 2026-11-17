"""Model install Job: pinned download, SHA-256 verification, and marker install.

The Job is the only code that downloads model weights. It streams to
``<models>/htdemucs/<filename>.partial`` while hashing, verifies the pinned size
and digest, then promotes the file with one ``os.replace`` and writes the
``installed/<model>.json`` marker. Cancellation and every failure delete the
partial; no final weights exist unless verification passed.

The transport is injected so tests exercise the real streaming, hashing, and
install path without a network. Production uses :mod:`urllib.request`; the
transport only has to expose ``headers``, ``read(size)``, and ``close()``.
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
from typing import Final, Protocol, cast

from stemforge import errors
from stemforge.artifacts import (
    estimate_model_install_bytes,
    free_space_requirement,
    require_free_space,
)
from stemforge.domain.models import ModelConsent
from stemforge.errors import StemForgeError
from stemforge.jobs.job import JobContext
from stemforge.models import ModelStatus, ModelStore

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
FALLBACK_WINDOW_BYTES: Final = 16 * 1024 * 1024
DOWNLOAD_TIMEOUT_S: Final = 60.0
READ_CHUNK_BYTES: Final = 1024 * 1024


class DownloadResponse(Protocol):
    """The slice of an HTTP response the downloader needs."""

    headers: Mapping[str, str]

    def read(self, size: int = -1, /) -> bytes: ...

    def close(self) -> None: ...


Transport = Callable[[str], DownloadResponse]


class _NetworkFailure(Exception):
    """A classified transport failure; ticket 14 §8 forbids automatic retries."""

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
        ctx.progress(0.0, stage=STAGE_CONSENT)

        existing = self.store.verify()
        if existing.verified:
            ctx.progress(1.0, stage=STAGE_INSTALL, detail="already installed")
            return ModelInstallResult(status=existing, downloaded=False)

        require_model_consent(self.consent, required_version=self.store.spec.licence_notice_version)
        ctx.progress(0.01, stage=STAGE_CONSENT)
        require_free_space(self.store.root, free_space_requirement(estimate_model_install_bytes()))
        ctx.cancel.check()

        partial = self.store.partial_path()
        ctx.progress(DOWNLOAD_START, stage=STAGE_DOWNLOAD)
        try:
            size, digest = self._download(ctx, partial)
            ctx.cancel.check()
            ctx.progress(DOWNLOAD_END, stage=STAGE_VERIFY)
            status = self.store.commit_verified_partial(size=size, sha256=digest)
            ctx.progress(VERIFIED_END, stage=STAGE_VERIFY)
            ctx.progress(INSTALL_START, stage=STAGE_INSTALL)
            result = ModelInstallResult(status=status, downloaded=True)
        except BaseException:
            self.store.discard_partial()
            raise
        ctx.progress(1.0, stage=STAGE_INSTALL)
        return result

    # -- download -----------------------------------------------------------

    def _download(self, ctx: JobContext, partial: Path) -> tuple[int, str]:
        try:
            return self._download_once(ctx, partial)
        except _NetworkFailure as failure:
            self.store.discard_partial()
            raise self._download_error(failure) from failure.cause

    def _download_once(self, ctx: JobContext, partial: Path) -> tuple[int, str]:
        spec = self.store.spec
        response: DownloadResponse | None = None
        try:
            try:
                opener = self.transport or _urlopen
                response = opener(spec.download_url)
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

            total = _content_length(response.headers)
            digest = hashlib.sha256()
            downloaded = 0
            try:
                partial.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                handle = partial.open("wb")
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
                        _download_progress(downloaded, total),
                        stage=STAGE_DOWNLOAD,
                        detail=_download_detail(downloaded, total),
                    )
                    ctx.cancel.check()
            return downloaded, digest.hexdigest()
        finally:
            if response is not None:
                _close(response)

    def _download_error(self, failure: _NetworkFailure) -> StemForgeError:
        return StemForgeError(
            errors.SEPARATION_MODEL_DOWNLOAD_FAILED,
            "the model download failed",
            retryable=True,
            detail=f"reason={failure.reason}",
            context={"url": self.store.spec.download_url, "reason": failure.reason},
        )

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


def _content_length(headers: Mapping[str, str]) -> int | None:
    raw = headers.get("Content-Length")
    if raw is None:
        raw = headers.get("content-length")
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _download_progress(downloaded: int, total: int | None) -> float:
    """Map transferred bytes into the Download stage, monotonically.

    A declared ``Content-Length`` gives an exact fraction. Without one, the
    asymptotic ``downloaded / (downloaded + 16 MiB)`` curve never regresses and
    stays below the stage ceiling until the complete file has arrived.
    """
    if total is not None:
        fraction = min(1.0, downloaded / total)
    else:
        fraction = downloaded / (downloaded + FALLBACK_WINDOW_BYTES)
    return DOWNLOAD_START + (DOWNLOAD_END - DOWNLOAD_START) * fraction


def _download_detail(downloaded: int, total: int | None) -> str:
    if total is not None:
        return f"{downloaded / 1_000_000:.1f} / {total / 1_000_000:.1f} MB"
    return f"{downloaded / 1_000_000:.1f} MB"


def _close(response: DownloadResponse) -> None:
    with suppress(OSError):
        response.close()


def _urlopen(url: str) -> DownloadResponse:
    """The production transport; the only place that opens a socket."""
    return cast(
        "DownloadResponse",
        urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_S),
    )
