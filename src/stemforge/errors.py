"""The single error type the UI sees, and the stable code taxonomy.

Codes are stable dotted strings (ticket 06 §9 plus the ticket 14 amendments).
User-facing copy does not live here: ``ui/formatting.py`` maps ``(code, reason)``
to every headline and hint, and an exception's ``message`` is log-only.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

# import.*
IMPORT_NO_AUDIO_STREAM: Final = "import.no_audio_stream"
IMPORT_UNSUPPORTED_SOURCE: Final = "import.unsupported_source"
IMPORT_DOWNLOAD_FAILED: Final = "import.download_failed"
IMPORT_YOUTUBE_UNAVAILABLE: Final = "import.youtube_unavailable"
IMPORT_YOUTUBE_AGE_RESTRICTED: Final = "import.youtube_age_restricted"
IMPORT_FILE_UNAVAILABLE: Final = "import.file_unavailable"
IMPORT_TOO_LONG: Final = "import.too_long"
IMPORT_YOUTUBE_URL_INVALID: Final = "import.youtube_url_invalid"
IMPORT_YOUTUBE_PLAYLIST: Final = "import.youtube_playlist"
IMPORT_YOUTUBE_TOO_LONG: Final = "import.youtube_too_long"
IMPORT_YOUTUBE_LIVE: Final = "import.youtube_live"
IMPORT_YOUTUBE_RATE_LIMITED: Final = "import.youtube_rate_limited"
IMPORT_YOUTUBE_EXTRACTOR_OUTDATED: Final = "import.youtube_extractor_outdated"
IMPORT_NETWORK: Final = "import.network"

# decode.*
DECODE_FAILED: Final = "decode.failed"

# separation.*
SEPARATION_MODEL_MISSING: Final = "separation.model_missing"
SEPARATION_MODEL_DOWNLOAD_FAILED: Final = "separation.model_download_failed"
SEPARATION_WORKER_CRASHED: Final = "separation.worker_crashed"
SEPARATION_ENGINE_ERROR: Final = "separation.engine_error"
SEPARATION_PROTOCOL_MISMATCH: Final = "separation.protocol_mismatch"
SEPARATION_OUTPUT_INVALID: Final = "separation.output_invalid"

# pitch.*
PITCH_ENGINE_MISSING: Final = "pitch.engine_missing"
PITCH_RENDER_FAILED: Final = "pitch.render_failed"
PITCH_OUTPUT_INVALID: Final = "pitch.output_invalid"

# media.*
MEDIA_PROBE_FAILED: Final = "media.probe_failed"
MEDIA_ENCODE_FAILED: Final = "media.encode_failed"
MEDIA_VALIDATION_FAILED: Final = "media.validation_failed"

# export.*
EXPORT_WRITE_FAILED: Final = "export.write_failed"

# logging.*
LOGGING_OPEN_FAILED: Final = "logging.open_failed"

# storage.*
STORAGE_DISK_FULL: Final = "storage.disk_full"
STORAGE_CACHE_FULL: Final = "storage.cache_full"
STORAGE_PERMISSION_DENIED: Final = "storage.permission_denied"

# internal.*
INTERNAL_UNEXPECTED: Final = "internal.unexpected"

# preflight.*
PREFLIGHT_PLATFORM_UNSUPPORTED: Final = "preflight.platform_unsupported"
PREFLIGHT_WINDOW_UNAVAILABLE: Final = "preflight.window_unavailable"
PREFLIGHT_WEBVIEW2_MISSING: Final = "preflight.webview2_missing"
PREFLIGHT_BUNDLE_DAMAGED: Final = "preflight.bundle_damaged"
PREFLIGHT_STORAGE_UNWRITABLE: Final = "preflight.storage_unwritable"
PREFLIGHT_PREVIEW_UNAVAILABLE: Final = "preflight.preview_unavailable"

# playback.*
PLAYBACK_UNAVAILABLE: Final = "playback.unavailable"

# licensing.*
LICENSING_FILES_MISSING: Final = "licensing.files_missing"
LICENSING_OPEN_FAILED: Final = "licensing.open_failed"

# Worker protocol only: a handled worker exit code 2 carrying ``cancelled``.
CANCELLED: Final = "cancelled"

ALL_CODES: Final[frozenset[str]] = frozenset(
    {
        IMPORT_NO_AUDIO_STREAM,
        IMPORT_UNSUPPORTED_SOURCE,
        IMPORT_DOWNLOAD_FAILED,
        IMPORT_YOUTUBE_UNAVAILABLE,
        IMPORT_YOUTUBE_AGE_RESTRICTED,
        IMPORT_FILE_UNAVAILABLE,
        IMPORT_TOO_LONG,
        IMPORT_YOUTUBE_URL_INVALID,
        IMPORT_YOUTUBE_PLAYLIST,
        IMPORT_YOUTUBE_TOO_LONG,
        IMPORT_YOUTUBE_LIVE,
        IMPORT_YOUTUBE_RATE_LIMITED,
        IMPORT_YOUTUBE_EXTRACTOR_OUTDATED,
        IMPORT_NETWORK,
        DECODE_FAILED,
        SEPARATION_MODEL_MISSING,
        SEPARATION_MODEL_DOWNLOAD_FAILED,
        SEPARATION_WORKER_CRASHED,
        SEPARATION_ENGINE_ERROR,
        SEPARATION_PROTOCOL_MISMATCH,
        SEPARATION_OUTPUT_INVALID,
        PITCH_ENGINE_MISSING,
        PITCH_RENDER_FAILED,
        PITCH_OUTPUT_INVALID,
        MEDIA_PROBE_FAILED,
        MEDIA_ENCODE_FAILED,
        MEDIA_VALIDATION_FAILED,
        EXPORT_WRITE_FAILED,
        LOGGING_OPEN_FAILED,
        STORAGE_DISK_FULL,
        STORAGE_CACHE_FULL,
        STORAGE_PERMISSION_DENIED,
        INTERNAL_UNEXPECTED,
        PREFLIGHT_PLATFORM_UNSUPPORTED,
        PREFLIGHT_WINDOW_UNAVAILABLE,
        PREFLIGHT_WEBVIEW2_MISSING,
        PREFLIGHT_BUNDLE_DAMAGED,
        PREFLIGHT_STORAGE_UNWRITABLE,
        PREFLIGHT_PREVIEW_UNAVAILABLE,
        PLAYBACK_UNAVAILABLE,
        LICENSING_FILES_MISSING,
        LICENSING_OPEN_FAILED,
        CANCELLED,
    }
)


class StemForgeError(Exception):
    """A failure with a stable code for UI copy and a log-only message."""

    def __init__(
        self,
        code: str,
        message: str,
        retryable: bool = False,
        detail: str | None = None,
        context: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.detail = detail
        self.context: Mapping[str, object] = MappingProxyType(dict(context or {}))


class JobCancelled(StemForgeError):
    """Raised at a safe checkpoint after a Job's cancel token was set."""

    def __init__(self, message: str = "Job cancelled") -> None:
        super().__init__(CANCELLED, message)
