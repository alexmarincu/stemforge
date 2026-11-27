"""The yt-dlp adapter's download-path fallbacks.

yt-dlp reports the file it wrote in more than one shape: a
``requested_downloads`` list, a top-level ``filepath``, or the older
``_filename``. Every shape has to resolve inside the Job's staging directory,
and a report that points outside it must fail instead of leaving a stale file.
These tests drive the real :class:`YtDlpImporter` so the branch the Job uses is
the branch under test.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from stemforge import errors
from stemforge.domain.cancellation import CancelToken
from stemforge.engines.youtube import DownloadedAudio, YouTubeVideo, YtDlpImporter
from stemforge.errors import StemForgeError

pytestmark = pytest.mark.unit

VIDEO_ID = "dQw4w9WgXcQ"
VIDEO = YouTubeVideo(video_id=VIDEO_ID, canonical_url=f"https://youtu.be/{VIDEO_ID}")
TITLE = "Test Video"
LOG = logging.getLogger("test.youtube-download")


def _info() -> dict[str, object]:
    return {
        "_type": "video",
        "id": VIDEO_ID,
        "title": TITLE,
        "uploader": "Test Uploader",
        "duration": 3.0,
        "upload_date": "20240102",
        "extractor": "youtube",
    }


@dataclass
class _Session:
    """A YoutubeDL slice that records each call and writes one staged file."""

    target: Path
    report: Mapping[str, object]
    write: bool = True
    calls: list[bool] = field(default_factory=list)

    def __call__(self, options: Mapping[str, object]) -> _Session:
        del options
        return self

    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def extract_info(self, url: str, download: bool = False) -> dict[str, object]:
        del url
        self.calls.append(download)
        if not download:
            return _info()
        if self.write:
            self.target.parent.mkdir(parents=True, exist_ok=True)
            self.target.write_bytes(b"native webm bytes")
        return {**_info(), **self.report}


def _adapter(tmp_path: Path, session: _Session) -> YtDlpImporter:
    return YtDlpImporter(
        cache_root=tmp_path / "cache",
        ffmpeg=tmp_path / "ffmpeg",
        deno=tmp_path / "deno",
        youtube_dl_factory=session,
    )


def _download(tmp_path: Path, session: _Session) -> DownloadedAudio:
    return _adapter(tmp_path, session).download(
        VIDEO,
        tmp_path / "staging",
        cancel=CancelToken(),
        log=LOG,
        progress=lambda fraction, detail: None,
    )


def _staged(tmp_path: Path, name: str) -> Path:
    return tmp_path / "staging" / name


def test_a_download_without_a_requested_downloads_list_uses_the_filepath(
    tmp_path: Path,
) -> None:
    target = _staged(tmp_path, f"{VIDEO_ID}.webm")
    session = _Session(
        target=target,
        report={"filepath": str(target), "format_id": "251", "acodec": "opus"},
    )

    downloaded = _download(tmp_path, session)

    assert downloaded.path == target.resolve()
    assert downloaded.path.suffix == ".webm"
    assert downloaded.format_id == "251"
    assert session.calls == [True]


def test_a_requested_entry_without_a_filepath_falls_back_to_the_filepath(
    tmp_path: Path,
) -> None:
    target = _staged(tmp_path, f"{VIDEO_ID}.webm")
    session = _Session(
        target=target,
        report={
            "requested_downloads": [{"format_id": "251", "acodec": "opus"}],
            "filepath": str(target),
        },
    )

    downloaded = _download(tmp_path, session)

    assert downloaded.path == target.resolve()
    assert downloaded.format_id == "251"


def test_a_download_with_only_an_underscore_filename_uses_it(tmp_path: Path) -> None:
    target = _staged(tmp_path, f"{VIDEO_ID}.webm")
    session = _Session(
        target=target,
        report={"_filename": str(target), "format_id": "251", "acodec": "opus"},
    )

    downloaded = _download(tmp_path, session)

    assert downloaded.path == target.resolve()
    assert downloaded.path.suffix == ".webm"


def test_a_download_reporting_no_path_is_a_retryable_download_failure(
    tmp_path: Path,
) -> None:
    session = _Session(target=_staged(tmp_path, "unused.webm"), report={})

    with pytest.raises(StemForgeError) as caught:
        _download(tmp_path, session)

    assert caught.value.code == errors.IMPORT_DOWNLOAD_FAILED
    assert caught.value.detail == "reason=no_filepath"
    assert caught.value.retryable is True


def test_a_download_reporting_a_missing_file_fails_as_missing_file(
    tmp_path: Path,
) -> None:
    target = _staged(tmp_path, f"{VIDEO_ID}.webm")
    session = _Session(target=target, report={"filepath": str(target)}, write=False)

    with pytest.raises(StemForgeError) as caught:
        _download(tmp_path, session)

    assert caught.value.code == errors.IMPORT_DOWNLOAD_FAILED
    assert caught.value.detail == "reason=missing_file"
    assert caught.value.retryable is True
    assert not target.exists()


def test_a_download_reporting_a_file_outside_staging_fails(tmp_path: Path) -> None:
    outside = tmp_path / f"{VIDEO_ID}.webm"
    session = _Session(target=outside, report={"filepath": str(outside)})

    with pytest.raises(StemForgeError) as caught:
        _download(tmp_path, session)

    assert caught.value.code == errors.IMPORT_DOWNLOAD_FAILED
    assert caught.value.detail == "reason=outside_staging"
    assert outside.is_file(), "the adapter must not delete a file it refuses to take"


def test_reusing_one_youtube_dl_instance_serves_resolve_and_download(
    tmp_path: Path,
) -> None:
    """One session for both phases is how a real ``YoutubeDL`` may behave."""
    target = _staged(tmp_path, f"{VIDEO_ID}.webm")
    session = _Session(
        target=target,
        report={"filepath": str(target), "format_id": "251", "acodec": "opus"},
    )
    adapter = _adapter(tmp_path, session)

    metadata = adapter.resolve(VIDEO, cancel=CancelToken(), log=LOG)
    downloaded = adapter.download(
        VIDEO,
        tmp_path / "staging",
        cancel=CancelToken(),
        log=LOG,
        progress=lambda fraction, detail: None,
    )

    assert metadata.title == TITLE
    assert session.calls == [False, True]
    assert downloaded.path == target.resolve()
