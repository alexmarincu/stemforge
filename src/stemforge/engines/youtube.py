"""The pure YouTube URL grammar, and (from ticket 16) the yt-dlp adapter.

Ticket 09 §1 fixes validation at submit: trim, prepend a scheme when the paste
has none, then parse host, path, and query. The parser here is deliberately
small and dependency-free: no network, no yt-dlp, no filesystem. The caller gets
either a :class:`YouTubeVideo` with the canonical watch URL or a coded
:class:`~stemforge.errors.StemForgeError` for the field-level message.

The engine lives below the application layer, so the UI validates through
``WorkspaceService.validate_youtube`` and only a valid URL reaches the
``submit_youtube`` Job seam. Ticket 16 adds the pinned yt-dlp adapter lazily to
this module; the parser stays pure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final
from urllib.parse import parse_qs, urlsplit

from stemforge import errors
from stemforge.errors import StemForgeError

VIDEO_ID_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]{11}")
_YOUTUBE_HOSTS: Final = frozenset(
    {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
)
_SHORT_HOST: Final = "youtu.be"
_WATCH_PATH: Final = "watch"
_PATH_ONLY_SHAPES: Final = frozenset({"shorts", "embed", "live"})
_PLAYLIST_FIRST_SEGMENTS: Final = frozenset({"playlist", "results", "channel", "c", "user"})


@dataclass(frozen=True, slots=True)
class YouTubeVideo:
    """A validated video reference: the id plus its canonical watch URL."""

    video_id: str
    canonical_url: str

    @property
    def source_id(self) -> str:
        """The cache identity ticket 09 §5 assigns to this video."""
        return f"yt-{self.video_id}"


def parse_youtube_url(raw: str) -> YouTubeVideo:
    """Return the video named by ``raw`` or raise a coded refusal.

    Accepted: watch, youtu.be, shorts, embed, live, the ``www.``/``m.``/``music.``
    host variants, scheme-less pastes, and a bare 11-character id. A watch URL
    carrying both ``v`` and ``list`` is the single video (ticket 09 §1).
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
