"""The pure YouTube URL grammar and the pinned yt-dlp adapter.

Validation trims, prepends a scheme when the paste
has none, then parses host, path, and query. The parser here is deliberately
small and dependency-free: no network, no yt-dlp, no filesystem. The caller gets
either a :class:`YouTubeVideo` with the canonical watch URL or a coded
:class:`~stemforge.errors.StemForgeError` for the field-level message.

The engine lives below the application layer, so the UI validates through
``WorkspaceService.validate_youtube`` and only a valid URL reaches the
``submit_youtube`` Job seam. The pinned yt-dlp adapter is loaded lazily in this
module; the parser stays pure, and ``yt_dlp`` is imported only inside a function
so startup and the frozen build never pay for it.
"""

from __future__ import annotations

import errno
import logging
import math
import re
import socket
import subprocess
import time
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, NoReturn, Protocol, Self, cast
from urllib.parse import parse_qs, urlsplit

from stemforge import errors
from stemforge.domain.cancellation import CancelToken
from stemforge.errors import StemForgeError
from stemforge.platform import binaries

__all__ = [
    "DENO_PINNED_VERSION",
    "VIDEO_ID_PATTERN",
    "YOUTUBE_MAX_DURATION_S",
    "DownloadedAudio",
    "YouTubeMetadata",
    "YouTubeTool",
    "YouTubeVideo",
    "YtDlpImporter",
    "build_youtube_dl_options",
    "classify_youtube_error",
    "normalize_title",
    "parse_youtube_url",
    "shipped_tool_versions",
]

VIDEO_ID_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]{11}")
_YOUTUBE_HOSTS: Final = frozenset(
    {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
)
_SHORT_HOST: Final = "youtu.be"
_WATCH_PATH: Final = "watch"
_PATH_ONLY_SHAPES: Final = frozenset({"shorts", "embed", "live"})
_PLAYLIST_FIRST_SEGMENTS: Final = frozenset({"playlist", "results", "channel", "c", "user"})

# The 15-minute cap and the Deno pin.
YOUTUBE_MAX_DURATION_S: Final = 900.0
DENO_PINNED_VERSION: Final = "2.9.8"
_TITLE_LIMIT: Final = 200
_PROGRESS_INTERVAL_S: Final = 0.25
_MIB: Final = 1024 * 1024

ProgressHook = Callable[[Mapping[str, object]], None]
ProgressCallback = Callable[[float, str], None]
YoutubeDLFactory = Callable[[dict[str, object]], "_YoutubeDL"]


@dataclass(frozen=True, slots=True)
class YouTubeVideo:
    """A validated video reference: the id plus its canonical watch URL."""

    video_id: str
    canonical_url: str

    @property
    def source_id(self) -> str:
        """The cache identity assigned to this video."""
        return f"yt-{self.video_id}"


@dataclass(frozen=True, slots=True)
class YouTubeMetadata:
    """The resolved, metadata-only facts for one video."""

    video_id: str
    title: str
    artist: str
    canonical_url: str
    duration_s: float | None = None
    upload_date: str = ""


@dataclass(frozen=True, slots=True)
class DownloadedAudio:
    """The native audio yt-dlp wrote into the Job's staging directory."""

    path: Path
    format_id: str | None = None
    extractor: str | None = None
    extractor_version: str | None = None


class YouTubeTool(Protocol):
    """The yt-dlp seam the import Job calls; tests inject fakes."""

    def resolve(
        self,
        video: YouTubeVideo,
        *,
        cancel: CancelToken,
        log: logging.Logger,
    ) -> YouTubeMetadata: ...

    def download(
        self,
        video: YouTubeVideo,
        staging: Path,
        *,
        cancel: CancelToken,
        log: logging.Logger,
        progress: ProgressCallback,
    ) -> DownloadedAudio: ...

    def tool_versions(self) -> Mapping[str, str]: ...


class _YoutubeDL(Protocol):
    """The slice of ``YoutubeDL`` the adapter uses, so tests can stub it."""

    def __enter__(self) -> Self: ...

    def __exit__(self, *exc_info: object) -> bool | None: ...

    def extract_info(self, url: str, download: bool = ...) -> dict[str, object] | None: ...


def parse_youtube_url(raw: str) -> YouTubeVideo:
    """Return the video named by ``raw`` or raise a coded refusal.

    Accepted: watch, youtu.be, shorts, embed, live, the ``www.``/``m.``/``music.``
    host variants, scheme-less pastes, and a bare 11-character id. A watch URL
    carrying both ``v`` and ``list`` is the single video.
    """
    text = raw.strip()
    if not text:
        raise _invalid("empty")

    if VIDEO_ID_PATTERN.fullmatch(text):
        return _video(text)

    if any(character.isspace() for character in text):
        raise _invalid("unparseable")

    if "://" not in text:
        host_part = text.split("/", 1)[0]
        if "." not in host_part:
            raise _invalid("unparseable")
        text = f"https://{text}"

    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"}:
        raise _invalid("unsupported_scheme")
    host = (parsed.hostname or "").lower()
    if host in _YOUTUBE_HOSTS:
        return _parse_youtube_host(parsed.path, parsed.query)
    if host == _SHORT_HOST:
        return _parse_short_host(parsed.path)
    raise StemForgeError(
        errors.IMPORT_UNSUPPORTED_SOURCE,
        f"not a YouTube host: {host or '<missing>'}",
        detail="host",
        context={"host": host},
    )


def _parse_youtube_host(path: str, query: str) -> YouTubeVideo:
    segments = _segments(path)
    if not segments:
        raise _invalid("missing_path")
    first = segments[0]
    if first.startswith("@") or first in _PLAYLIST_FIRST_SEGMENTS:
        detail = (
            "search" if first == "results" else "channel" if first != "playlist" else "playlist"
        )
        raise StemForgeError(
            errors.IMPORT_YOUTUBE_PLAYLIST,
            f"playlist, channel, or search URL: /{first}",
            detail=detail,
        )
    if first == _WATCH_PATH:
        if len(segments) != 1:
            raise _invalid("extra_path")
        values = parse_qs(query, keep_blank_values=True).get("v", [])
        if len(values) != 1:
            raise _invalid("missing_video_id" if not values else "ambiguous_video_id")
        return _video(_require_id(values[0]))
    if first in _PATH_ONLY_SHAPES:
        if len(segments) != 2:
            raise _invalid("extra_path")
        return _video(_require_id(segments[1]))
    raise _invalid("unknown_path")


def _parse_short_host(path: str) -> YouTubeVideo:
    segments = _segments(path)
    if len(segments) != 1:
        raise _invalid("extra_path" if segments else "missing_video_id")
    return _video(_require_id(segments[0]))


def _segments(path: str) -> list[str]:
    """Split one path into literal segments; a single trailing slash is cosmetic."""
    if path in {"", "/"}:
        return []
    if path.endswith("/"):
        path = path[:-1]
    if not path.startswith("/"):
        return []
    return path[1:].split("/")


def _require_id(candidate: str) -> str:
    if not VIDEO_ID_PATTERN.fullmatch(candidate):
        raise _invalid("malformed_video_id")
    return candidate


def _video(video_id: str) -> YouTubeVideo:
    return YouTubeVideo(
        video_id=video_id,
        canonical_url=f"https://www.youtube.com/watch?v={video_id}",
    )


def _invalid(detail: str) -> StemForgeError:
    return StemForgeError(
        errors.IMPORT_YOUTUBE_URL_INVALID,
        f"not a usable YouTube video URL: {detail}",
        detail=detail,
    )


# ---------------------------------------------------------------------------
# yt-dlp adapter
# ---------------------------------------------------------------------------


def build_youtube_dl_options(
    *,
    home: Path,
    cache_root: Path,
    ffmpeg: Path,
    deno: Path,
    log: logging.Logger,
    progress_hooks: Sequence[ProgressHook],
) -> dict[str, object]:
    """The yt-dlp option table; no cookies, accounts, or proxy.

    ``home`` is the yt-dlp output directory; the YouTube Job passes its
    per-Job scratch under ``<cache>/yt-dlp/``. ``postprocessors`` is empty: the
    native audio file is kept and decoded by StemForge's FFmpeg pipeline.
    """
    return {
        "format": "bestaudio/best",
        "noplaylist": True,
        "outtmpl": {"default": "%(id)s.%(ext)s"},
        "paths": {"home": str(home)},
        "cachedir": str(cache_root / "yt-dlp"),
        "windowsfilenames": True,
        "trim_file_name": 120,
        "quiet": True,
        "no_warnings": False,
        "noprogress": True,
        "ignoreerrors": False,
        "logger": log,
        "progress_hooks": list(progress_hooks),
        "retries": 3,
        "fragment_retries": 3,
        "extractor_retries": 3,
        "socket_timeout": 20,
        "progress_delta": 0.5,
        "warn_when_outdated": False,
        "ffmpeg_location": str(Path(ffmpeg).parent),
        "js_runtimes": {"deno": {"path": str(deno)}},
        "postprocessors": [],
    }


class YtDlpImporter:
    """The in-process yt-dlp adapter with an injectable ``YoutubeDL`` factory."""

    def __init__(
        self,
        *,
        cache_root: Path,
        ffmpeg: Path | None = None,
        deno: Path | None = None,
        youtube_dl_factory: YoutubeDLFactory | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cache_root = Path(cache_root)
        self._ffmpeg = ffmpeg
        self._deno = deno
        self._factory = youtube_dl_factory
        self._clock = clock

    def resolve(
        self,
        video: YouTubeVideo,
        *,
        cancel: CancelToken,
        log: logging.Logger,
    ) -> YouTubeMetadata:
        """Fetch metadata only; no media is written."""
        cancel.check()
        self._ensure_cache_dir()
        options = self._options(
            home=self._cache_root / "yt-dlp" / "resolve",
            log=log,
            progress_hooks=[],
        )
        try:
            with self._open(options) as youtube_dl:
                raw = youtube_dl.extract_info(video.canonical_url, download=False)
        except StemForgeError:
            raise
        except BaseException as exc:
            self._raise_translated(exc, cancel, log)

        info = _info_dict(raw)
        if info.get("_type", "video") != "video":
            raise StemForgeError(
                errors.IMPORT_YOUTUBE_PLAYLIST,
                "yt-dlp resolved a playlist or channel",
                detail="playlist",
            )
        metadata = _metadata_from_info(info, fallback=video)
        _validate_resolved(info, metadata)
        return metadata

    def download(
        self,
        video: YouTubeVideo,
        staging: Path,
        *,
        cancel: CancelToken,
        log: logging.Logger,
        progress: ProgressCallback,
    ) -> DownloadedAudio:
        """Download the native audio into the yt-dlp scratch with progress hooks."""
        cancel.check()
        self._ensure_cache_dir()
        staging = Path(staging)
        staging.mkdir(mode=0o700, parents=True, exist_ok=True)
        last_update = [0.0]

        def hook(data: Mapping[str, object]) -> None:
            if cancel.cancelled:
                from yt_dlp.utils import DownloadCancelled

                raise DownloadCancelled("cancelled by the StemForge Job")
            status = data.get("status")
            if status == "finished":
                progress(1.0, "Downloading audio · 100%")
                return
            if status != "downloading":
                return
            fraction = _download_fraction(data)
            now = self._clock()
            if now - last_update[0] < _PROGRESS_INTERVAL_S and fraction < 1.0:
                return
            last_update[0] = now
            progress(fraction, _download_detail(data))

        options = self._options(
            home=staging,
            log=log,
            progress_hooks=[hook],
        )
        try:
            with self._open(options) as youtube_dl:
                raw = youtube_dl.extract_info(video.canonical_url, download=True)
        except StemForgeError:
            raise
        except BaseException as exc:
            self._raise_translated(exc, cancel, log)

        cancel.check()
        info = _info_dict(raw)
        if info.get("_type", "video") != "video":
            raise StemForgeError(
                errors.IMPORT_YOUTUBE_PLAYLIST,
                "yt-dlp downloaded a playlist or channel",
                detail="playlist",
            )
        path = _downloaded_path(info, staging)
        acodec = _requested_acodec(info)
        if acodec == "none":
            raise StemForgeError(
                errors.IMPORT_NO_AUDIO_STREAM,
                "the selected yt-dlp format has no audio stream",
                detail="acodec=none",
                context={"path": str(path)},
            )
        return DownloadedAudio(
            path=path,
            format_id=_download_format_id(info),
            extractor=_optional_str(info.get("extractor") or info.get("extractor_key")),
            extractor_version=_yt_dlp_version(),
        )

    def tool_versions(self) -> Mapping[str, str]:
        """The pinned trio versions recorded in ``source.json``."""
        return {
            "yt_dlp": _yt_dlp_version(),
            "yt_dlp_ejs": _distribution_version("yt-dlp-ejs"),
            "deno": self._deno_version(),
        }

    # -- internals ----------------------------------------------------------

    def _open(self, options: dict[str, object]) -> _YoutubeDL:
        factory = self._factory if self._factory is not None else _default_youtube_dl
        return factory(options)

    def _options(
        self,
        *,
        home: Path,
        log: logging.Logger,
        progress_hooks: Sequence[ProgressHook],
    ) -> dict[str, object]:
        return build_youtube_dl_options(
            home=home,
            cache_root=self._cache_root,
            ffmpeg=self._ffmpeg_path(),
            deno=self._deno_path(),
            log=log,
            progress_hooks=progress_hooks,
        )

    def _ffmpeg_path(self) -> Path:
        if self._ffmpeg is not None:
            return self._ffmpeg
        try:
            return binaries.ffmpeg()
        except OSError as exc:
            raise _bundle_error("ffmpeg", exc) from exc

    def _deno_path(self) -> Path:
        if self._deno is not None:
            return self._deno
        try:
            resolved = binaries.deno()
        except OSError as exc:
            raise _bundle_error("deno", exc) from exc
        if resolved is None:
            raise _bundle_error("deno", FileNotFoundError("deno is not on PATH"))
        return resolved

    def _ensure_cache_dir(self) -> None:
        try:
            self._cache_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            code = (
                errors.STORAGE_DISK_FULL
                if exc.errno == errno.ENOSPC
                else errors.STORAGE_PERMISSION_DENIED
            )
            raise StemForgeError(
                code,
                f"could not create the yt-dlp cache directory: {exc}",
                retryable=True,
                detail=str(exc),
                context={"path": str(self._cache_root), "errno": exc.errno},
            ) from exc

    def _deno_version(self) -> str:
        try:
            path = self._deno_path()
            completed = subprocess.run(
                [str(path), "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError, StemForgeError):
            return DENO_PINNED_VERSION
        first = completed.stdout.splitlines()[0].split() if completed.stdout.splitlines() else []
        if len(first) >= 2 and first[0] == "deno":
            return first[1]
        return DENO_PINNED_VERSION

    def _raise_translated(
        self,
        exc: BaseException,
        cancel: CancelToken,
        log: logging.Logger,
    ) -> NoReturn:
        if cancel.cancelled:
            cancel.check()
        if isinstance(exc, StemForgeError):
            raise exc
        log.error("yt-dlp %s: %s", type(exc).__name__, exc)
        raise classify_youtube_error(exc) from exc


def _default_youtube_dl(options: dict[str, object]) -> _YoutubeDL:
    from yt_dlp import YoutubeDL

    return cast("_YoutubeDL", YoutubeDL(options))


# ---------------------------------------------------------------------------
# Resolve/download validation and metadata
# ---------------------------------------------------------------------------

_UNAVAILABLE_AVAILABILITY: Final = frozenset(
    {"private", "premium_only", "subscriber_only", "needs_auth", "unavailable"}
)


def _info_dict(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise StemForgeError(
            errors.IMPORT_DOWNLOAD_FAILED,
            "yt-dlp returned no video information",
            detail="reason=no_info",
            retryable=True,
        )
    return cast("dict[str, object]", raw)


def _metadata_from_info(info: Mapping[str, object], *, fallback: YouTubeVideo) -> YouTubeMetadata:
    video_id = _optional_str(info.get("id")) or fallback.video_id
    title = normalize_title(info.get("title"), video_id)
    artist = _clean_text(info.get("uploader") or info.get("channel"), limit=_TITLE_LIMIT)
    return YouTubeMetadata(
        video_id=video_id,
        title=title,
        artist=artist,
        canonical_url=f"https://www.youtube.com/watch?v={video_id}",
        duration_s=_duration_s(info.get("duration")),
        upload_date=_upload_date(info.get("upload_date")),
    )


def normalize_title(raw: object, video_id: str) -> str:
    """NFC, whitespace-collapsed, at most 200 characters; fallback ``YouTube <id>``."""
    title = _clean_text(raw, limit=_TITLE_LIMIT)
    return title or f"YouTube {video_id}"


def _clean_text(raw: object, *, limit: int) -> str:
    if not isinstance(raw, str):
        return ""
    normalized = unicodedata.normalize("NFC", raw)
    collapsed = " ".join(normalized.split())
    return collapsed[:limit].strip()


def _validate_resolved(info: Mapping[str, object], metadata: YouTubeMetadata) -> None:
    availability = _optional_str(info.get("availability")).lower()
    if availability in _UNAVAILABLE_AVAILABILITY:
        raise _coded(errors.IMPORT_YOUTUBE_UNAVAILABLE, "unavailable")

    age_limit = _as_int(info.get("age_limit")) or 0
    if age_limit > 0:
        raise _coded(errors.IMPORT_YOUTUBE_AGE_RESTRICTED, "age_restricted")

    live_status = _optional_str(info.get("live_status")).lower()
    if info.get("is_live") is True or live_status in {"is_live", "is_upcoming"}:
        raise _coded(errors.IMPORT_YOUTUBE_LIVE, "live")

    duration = metadata.duration_s
    if duration is not None and duration > YOUTUBE_MAX_DURATION_S:
        raise StemForgeError(
            errors.IMPORT_YOUTUBE_TOO_LONG,
            "the video exceeds the 15-minute import limit",
            detail=f"duration={duration:.0f}s",
            context={"duration_s": duration},
        )


def _coded(code: str, reason: str) -> StemForgeError:
    return StemForgeError(code, f"YouTube import refused ({reason})", detail=f"reason={reason}")


def _bundle_error(name: str, error: OSError) -> StemForgeError:
    return StemForgeError(
        errors.PREFLIGHT_BUNDLE_DAMAGED,
        f"the bundled {name} is missing",
        detail=str(error),
        context={"binary": name},
    )


def _downloaded_path(info: Mapping[str, object], staging: Path) -> Path:
    candidate = ""
    requested = info.get("requested_downloads")
    if isinstance(requested, list) and requested:
        first = requested[0]
        if isinstance(first, Mapping):
            filepath = first.get("filepath")
            if isinstance(filepath, str) and filepath:
                candidate = filepath
    if not candidate:
        for key in ("filepath", "_filename"):
            value = info.get(key)
            if isinstance(value, str) and value:
                candidate = value
                break
    if not candidate:
        raise StemForgeError(
            errors.IMPORT_DOWNLOAD_FAILED,
            "yt-dlp did not report a downloaded file",
            detail="reason=no_filepath",
            retryable=True,
        )
    try:
        resolved = Path(candidate).resolve(strict=True)
        root = Path(staging).resolve(strict=True)
    except OSError as exc:
        raise StemForgeError(
            errors.IMPORT_DOWNLOAD_FAILED,
            "the downloaded file is missing",
            detail="reason=missing_file",
            retryable=True,
            context={"path": candidate},
        ) from exc
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise StemForgeError(
            errors.IMPORT_DOWNLOAD_FAILED,
            "yt-dlp reported a file outside the Job staging directory",
            detail="reason=outside_staging",
            context={"path": str(resolved)},
        )
    return resolved


def _requested_acodec(info: Mapping[str, object]) -> str:
    requested = info.get("requested_downloads")
    if isinstance(requested, list) and requested and isinstance(requested[0], Mapping):
        return _optional_str(requested[0].get("acodec")).lower()
    return _optional_str(info.get("acodec")).lower()


def _download_format_id(info: Mapping[str, object]) -> str | None:
    requested = info.get("requested_downloads")
    if isinstance(requested, list) and requested and isinstance(requested[0], Mapping):
        requested_format = _optional_str(requested[0].get("format_id"))
        if requested_format:
            return requested_format
    return _optional_str(info.get("format_id")) or None


def _download_fraction(data: Mapping[str, object]) -> float:
    downloaded = _as_int(data.get("downloaded_bytes")) or 0
    total = _as_int(data.get("total_bytes")) or _as_int(data.get("total_bytes_estimate"))
    if not total or total <= 0:
        return 0.0
    return min(1.0, max(0.0, downloaded / total))


def _download_detail(data: Mapping[str, object]) -> str:
    downloaded = _as_int(data.get("downloaded_bytes")) or 0
    total = _as_int(data.get("total_bytes")) or _as_int(data.get("total_bytes_estimate"))
    speed = _as_float(data.get("speed"))
    parts = [f"Downloading audio · {_megabytes(downloaded)}"]
    parts.append(f"of {_megabytes(total)} MB" if total else "MB")
    if speed is not None and speed > 0:
        parts.append(f"· {speed / _MIB:.1f} MB/s")
    return " ".join(parts)


def _megabytes(value: int | None) -> str:
    return f"{(value or 0) / _MIB:.1f}"


def _duration_s(value: object) -> float | None:
    number = _as_float(value)
    if number is None or number <= 0 or not math.isfinite(number):
        return None
    return number


def _upload_date(value: object) -> str:
    text = _optional_str(value)
    if len(text) != 8 or not text.isdigit():
        return ""
    try:
        return datetime.strptime(text, "%Y%m%d").date().isoformat()
    except ValueError:
        return ""


def _optional_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return int(value)
    return None


def _as_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    return None


def shipped_tool_versions() -> Mapping[str, str]:
    """The pinned trio versions About reports without starting any process."""
    return {
        "yt_dlp": _yt_dlp_version(),
        "yt_dlp_ejs": _distribution_version("yt-dlp-ejs"),
        "deno": DENO_PINNED_VERSION,
    }


def _yt_dlp_version() -> str:
    try:
        from yt_dlp.version import __version__

        return str(__version__)
    except Exception:  # pragma: no cover - defensive: the package is a hard dependency
        return "unknown"


def _distribution_version(name: str) -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(name)
    except PackageNotFoundError:  # pragma: no cover - defensive
        return "unknown"


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

_UNAVAILABLE_MESSAGES: Final = (
    "private video",
    "video unavailable",
    "this video is unavailable",
    "removed by the uploader",
    "video has been removed",
    "members-only",
    "members only",
    "join this channel",
    "not available in your country",
    "geo restricted",
    "geo-restricted",
    "region-locked",
    "region locked",
    "drm protected",
    "premium only",
    "payment required",
    "no longer available",
    "account associated with this video has been terminated",
    "http error 404",
    "http error 410",
)
_AGE_MESSAGES: Final = (
    "age-restricted",
    "age restricted",
    "confirm your age",
    "age verification",
    "age_verification_required",
    "age_check_required",
    "inappropriate for some users",
)
_LIVE_MESSAGES: Final = (
    "is live",
    "live event",
    "will begin",
    "premiere",
    "upcoming",
    "live stream",
)
_RATE_LIMIT_MESSAGES: Final = (
    "not a bot",
    "sign in to confirm",
    "429",
    "too many requests",
    "rate limit",
    "rate-limit",
    "try again later",
    "temporarily blocked",
)
_EXTRACTOR_MESSAGES: Final = (
    "unable to extract",
    "failed to extract",
    "no video formats",
    "nsig",
    "signature",
    "javascript runtime",
    "js runtime",
    "js challenge",
    "player response",
    "unsupported url",
)
_NETWORK_MESSAGES: Final = (
    "timed out",
    "timeout",
    "connection reset",
    "connection refused",
    "connection aborted",
    "network is unreachable",
    "name or service not known",
    "temporary failure in name resolution",
    "nodename nor servname",
    "ssl",
    "certificate",
    "broken pipe",
    "urlopen error",
    "http error 5",
)


def classify_youtube_error(exc: BaseException) -> StemForgeError:
    """Map a yt-dlp exception to one stable code without leaking its text.

    Order: unavailable, age, live, rate limit, extractor, network, then
    ``import.download_failed``. The exception's message goes to
    the Job log only; the returned ``detail`` is a short stable reason.
    """
    from yt_dlp.networking.exceptions import HTTPError, RequestError, TransportError
    from yt_dlp.utils import (
        ExtractorError,
        GeoRestrictedError,
        UnavailableVideoError,
        UnsupportedError,
    )

    chain = _exception_chain(exc)
    text = _error_text(chain)

    if _matches(text, _UNAVAILABLE_MESSAGES) or any(
        isinstance(item, (GeoRestrictedError, UnavailableVideoError)) for item in chain
    ):
        return _coded_retryable(errors.IMPORT_YOUTUBE_UNAVAILABLE, "unavailable", retryable=False)
    if _matches(text, _AGE_MESSAGES):
        return _coded_retryable(
            errors.IMPORT_YOUTUBE_AGE_RESTRICTED, "age_restricted", retryable=False
        )
    if _matches(text, _LIVE_MESSAGES):
        return _coded_retryable(errors.IMPORT_YOUTUBE_LIVE, "live", retryable=False)
    if _matches(text, _RATE_LIMIT_MESSAGES):
        return _coded_retryable(errors.IMPORT_YOUTUBE_RATE_LIMITED, "rate_limited", retryable=True)
    if _matches(text, _EXTRACTOR_MESSAGES) or any(
        isinstance(item, (ExtractorError, UnsupportedError)) for item in chain
    ):
        return _coded_retryable(
            errors.IMPORT_YOUTUBE_EXTRACTOR_OUTDATED, "extractor", retryable=False
        )
    if _matches(text, _NETWORK_MESSAGES):
        return _coded_retryable(errors.IMPORT_NETWORK, "network", retryable=True)

    status = _http_status(chain)
    if status == 429:
        return _coded_retryable(errors.IMPORT_YOUTUBE_RATE_LIMITED, "rate_limited", retryable=True)
    if status in {404, 410}:
        return _coded_retryable(errors.IMPORT_YOUTUBE_UNAVAILABLE, "unavailable", retryable=False)
    if status is not None and 500 <= status < 600:
        return _coded_retryable(errors.IMPORT_NETWORK, "network", retryable=True)
    if any(isinstance(item, HTTPError) for item in chain) and status in {401, 403}:
        return _coded_retryable(
            errors.IMPORT_YOUTUBE_EXTRACTOR_OUTDATED, "extractor", retryable=False
        )
    if any(isinstance(item, (RequestError, TransportError)) for item in chain):
        return _coded_retryable(errors.IMPORT_NETWORK, "network", retryable=True)
    if any(isinstance(item, (ConnectionError, TimeoutError, socket.gaierror)) for item in chain):
        return _coded_retryable(errors.IMPORT_NETWORK, "network", retryable=True)
    return _coded_retryable(errors.IMPORT_DOWNLOAD_FAILED, "download", retryable=True)


def _coded_retryable(code: str, reason: str, *, retryable: bool) -> StemForgeError:
    return StemForgeError(
        code,
        f"YouTube import failed ({reason})",
        detail=f"reason={reason}",
        retryable=retryable,
    )


def _exception_chain(exc: BaseException) -> tuple[BaseException, ...]:
    seen: set[int] = set()
    pending: list[BaseException] = [exc]
    ordered: list[BaseException] = []
    while pending:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        ordered.append(current)
        cause = getattr(current, "__cause__", None)
        if isinstance(cause, BaseException):
            pending.append(cause)
        exc_info = getattr(current, "exc_info", None)
        if isinstance(exc_info, tuple) and len(exc_info) > 1:
            inner = exc_info[1]
            if isinstance(inner, BaseException):
                pending.append(inner)
        explicit = getattr(current, "cause", None)
        if isinstance(explicit, BaseException):
            pending.append(explicit)
    return tuple(ordered)


def _error_text(chain: Sequence[BaseException]) -> str:
    parts: list[str] = []
    for item in chain:
        parts.append(str(item))
    normalized = " ".join(" ".join(parts).lower().replace("\u2019", "'").split())
    return f" {normalized} "


def _matches(text: str, needles: Sequence[str]) -> bool:
    return any(needle in text for needle in needles)


def _http_status(chain: Sequence[BaseException]) -> int | None:
    for item in chain:
        status = getattr(item, "status", None)
        if isinstance(status, int) and not isinstance(status, bool):
            return status
        response = getattr(item, "response", None)
        response_status = getattr(response, "status", None)
        if isinstance(response_status, int) and not isinstance(response_status, bool):
            return response_status
    return None
