"""The YouTube URL grammar: accepted shapes, rejected shapes, and codes.

The parser is pure: no network, no yt-dlp, and every refusal is a coded error the
UI can render as a field message. Expected values are pinned here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from stemforge import errors
from stemforge.engines.youtube import YouTubeVideo, parse_youtube_url
from stemforge.errors import StemForgeError

pytestmark = pytest.mark.unit

VIDEO_ID = "dQw4w9WgXcQ"
CANONICAL = f"https://www.youtube.com/watch?v={VIDEO_ID}"

ACCEPTED: tuple[tuple[str, str], ...] = (
    (f"https://www.youtube.com/watch?v={VIDEO_ID}", VIDEO_ID),
    (f"https://youtube.com/watch?v={VIDEO_ID}&t=42", VIDEO_ID),
    (f"http://youtube.com/watch?v={VIDEO_ID}", VIDEO_ID),
    (f"youtube.com/watch?v={VIDEO_ID}", VIDEO_ID),
    (f"m.youtube.com/watch?v={VIDEO_ID}", VIDEO_ID),
    (f"music.youtube.com/watch?v={VIDEO_ID}", VIDEO_ID),
    (f"https://youtu.be/{VIDEO_ID}", VIDEO_ID),
    (f"youtu.be/{VIDEO_ID}?t=30", VIDEO_ID),
    (f"youtu.be/{VIDEO_ID}", VIDEO_ID),
    (f"https://www.youtube.com/shorts/{VIDEO_ID}", VIDEO_ID),
    (f"https://www.youtube.com/embed/{VIDEO_ID}", VIDEO_ID),
    (f"youtube.com/embed/{VIDEO_ID}", VIDEO_ID),
    (f"https://m.youtube.com/live/{VIDEO_ID}?feature=share", VIDEO_ID),
    (f"music.youtube.com/shorts/{VIDEO_ID}", VIDEO_ID),
    (f"https://www.youtube.com/watch?v={VIDEO_ID}&list=PL123&index=2", VIDEO_ID),
    (f"https://www.youtube.com/watch?list=PL123&v={VIDEO_ID}", VIDEO_ID),
    (VIDEO_ID, VIDEO_ID),
    (f"  {VIDEO_ID}\n", VIDEO_ID),
    (f"  https://WWW.YouTube.com/watch?v={VIDEO_ID}  ", VIDEO_ID),
    (f"HTTPS://youtu.be/{VIDEO_ID}", VIDEO_ID),
    ("https://youtube.com/watch?v=_-aBcDeF9Z1", "_-aBcDeF9Z1"),
    (f"https://www.youtube.com/shorts/{VIDEO_ID}/", VIDEO_ID),
)


@pytest.mark.parametrize(("raw", "video_id"), ACCEPTED)
def test_accepted_shapes_canonicalise_to_the_watch_url(raw: str, video_id: str) -> None:
    video = parse_youtube_url(raw)

    assert video == YouTubeVideo(
        video_id=video_id,
        canonical_url=f"https://www.youtube.com/watch?v={video_id}",
    )
    assert video.source_id == f"yt-{video_id}"


def test_an_id_may_contain_dashes_and_underscores() -> None:
    video = parse_youtube_url("_-aBcDeF9Z1")

    assert video.video_id == "_-aBcDeF9Z1"
    assert video.canonical_url == "https://www.youtube.com/watch?v=_-aBcDeF9Z1"


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("", errors.IMPORT_YOUTUBE_URL_INVALID),
        ("   ", errors.IMPORT_YOUTUBE_URL_INVALID),
        ("not a url", errors.IMPORT_YOUTUBE_URL_INVALID),
        (f"https://www.youtube.com/watch?v={VIDEO_ID[:-1]}", errors.IMPORT_YOUTUBE_URL_INVALID),
        (f"https://www.youtube.com/watch?v={VIDEO_ID}X", errors.IMPORT_YOUTUBE_URL_INVALID),
        ("dQw4w9WgXc", errors.IMPORT_YOUTUBE_URL_INVALID),
        ("dQw4w9WgXcQQ", errors.IMPORT_YOUTUBE_URL_INVALID),
        ("https://www.youtube.com/watch", errors.IMPORT_YOUTUBE_URL_INVALID),
        ("https://www.youtube.com/watch?list=PL123", errors.IMPORT_YOUTUBE_URL_INVALID),
        ("https://www.youtube.com/watch?v=", errors.IMPORT_YOUTUBE_URL_INVALID),
        ("https://www.youtube.com/watch?feature=share", errors.IMPORT_YOUTUBE_URL_INVALID),
        ("https://www.youtube.com/shorts/", errors.IMPORT_YOUTUBE_URL_INVALID),
        (
            f"https://www.youtube.com/shorts/{VIDEO_ID}/extra",
            errors.IMPORT_YOUTUBE_URL_INVALID,
        ),
        (f"https://www.youtube.com/embed/{VIDEO_ID}/extra", errors.IMPORT_YOUTUBE_URL_INVALID),
        (f"https://www.youtube.com/watch/extra?v={VIDEO_ID}", errors.IMPORT_YOUTUBE_URL_INVALID),
        (f"https://youtu.be/{VIDEO_ID}/extra", errors.IMPORT_YOUTUBE_URL_INVALID),
        ("https://youtu.be/", errors.IMPORT_YOUTUBE_URL_INVALID),
        ("https://youtu.be/short", errors.IMPORT_YOUTUBE_URL_INVALID),
        ("https://www.youtube.com/feed/subscriptions", errors.IMPORT_YOUTUBE_URL_INVALID),
        (
            f"https://www.youtube.com/watch?v={VIDEO_ID}&v=abcdefghijk",
            errors.IMPORT_YOUTUBE_URL_INVALID,
        ),
        (f"ftp://youtube.com/watch?v={VIDEO_ID}", errors.IMPORT_YOUTUBE_URL_INVALID),
        (
            "https://www.youtube.com/playlist?list=PL123",
            errors.IMPORT_YOUTUBE_PLAYLIST,
        ),
        ("youtube.com/playlist?list=PL123", errors.IMPORT_YOUTUBE_PLAYLIST),
        ("https://www.youtube.com/@somechannel", errors.IMPORT_YOUTUBE_PLAYLIST),
        ("https://www.youtube.com/channel/UC123", errors.IMPORT_YOUTUBE_PLAYLIST),
        ("https://www.youtube.com/c/SomeName", errors.IMPORT_YOUTUBE_PLAYLIST),
        ("https://www.youtube.com/user/SomeName", errors.IMPORT_YOUTUBE_PLAYLIST),
        (
            "https://www.youtube.com/results?search_query=daft+punk",
            errors.IMPORT_YOUTUBE_PLAYLIST,
        ),
        ("https://vimeo.com/12345", errors.IMPORT_UNSUPPORTED_SOURCE),
        (
            f"https://notyoutube.com/watch?v={VIDEO_ID}",
            errors.IMPORT_UNSUPPORTED_SOURCE,
        ),
        (
            f"https://youtube.com.evil.example/watch?v={VIDEO_ID}",
            errors.IMPORT_UNSUPPORTED_SOURCE,
        ),
        ("https://example.com/youtu.be/dQw4w9WgXcQ", errors.IMPORT_UNSUPPORTED_SOURCE),
    ],
)
def test_rejected_shapes_carry_the_field_level_code(raw: str, code: str) -> None:
    with pytest.raises(StemForgeError) as caught:
        parse_youtube_url(raw)

    assert caught.value.code == code


def test_the_parser_imports_no_network_or_yt_dlp() -> None:
    module = sys.modules["stemforge.engines.youtube"]
    assert "yt_dlp" not in getattr(module, "__dict__", {})
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "\nimport yt_dlp" not in source
    assert "\nfrom yt_dlp" not in source
