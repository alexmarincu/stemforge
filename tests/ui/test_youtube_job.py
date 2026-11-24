"""Headless UI tests for the YouTube import Job.

The YouTube adapter is a scripted fake: it never imports yt-dlp, never opens a
socket, and blocks only when a test asks it to. Every write stays under
``tmp_path``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from nicegui.testing.user_simulation import user_simulation

from stemforge import errors
from stemforge.application import ServiceEngines, WorkspaceService
from stemforge.artifacts import ArtifactStore
from stemforge.domain.cancellation import CancelToken
from stemforge.engines.media import (
    CanonicalMetadata,
    DecodeResult,
    LevelMeasurement,
    MediaInfo,
)
from stemforge.engines.youtube import DownloadedAudio, YouTubeMetadata, YouTubeVideo
from stemforge.errors import StemForgeError
from stemforge.integration.dialogs import normalize_selected_paths
from stemforge.jobs.runner import JobRunner
from stemforge.platform.paths import AppPaths
from stemforge.settings import SettingsStore
from stemforge.ui import page

pytestmark = pytest.mark.ui

VIDEO_ID = "dQw4w9WgXcQ"
VALID_URL = f"https://youtu.be/{VIDEO_ID}?t=5"
TITLE = "YouTube Song"
AGE_HEADLINE = "This video is age-restricted. StemForge doesn't sign in to YouTube."


@pytest.fixture(autouse=True)
def _no_network(no_external_network: None) -> None:
    """External connections fail; loopback (NiceGUI's test harness) is allowed."""


class FakeMedia:
    """A tiny ``MediaTool`` writing stand-ins; no real codec runs."""

    def __init__(self, *, duration_s: float = 120.0) -> None:
        self.duration_s = duration_s

    def probe(self, path: Path) -> MediaInfo:
        return MediaInfo(
            codec="pcm_s16le",
            sample_rate=44_100,
            channels=2,
            duration_s=self.duration_s,
            format="wav",
            tags={},
        )

    def decode_canonical(
        self, src: Path, dst: Path, *, cancel: CancelToken, log: logging.Logger
    ) -> DecodeResult:
        cancel.check()
        dst.write_bytes(b"canonical:" + src.read_bytes())
        return DecodeResult(
            path=dst,
            duration_s=self.duration_s,
            sample_rate=44_100,
            channels=2,
            downmixed=False,
        )

    def extract_metadata(self, src: Path) -> CanonicalMetadata:
        return CanonicalMetadata(title=src.stem, duration_s=self.duration_s)

    def extract_cover(self, src: Path, dst_dir: Path) -> Path | None:
        return None

    def encode_preview(
        self,
        src: Path,
        dst: Path,
        *,
        gain_db: float,
        title: str,
        artist: str,
        cancel: CancelToken,
        log: logging.Logger,
    ) -> None:
        cancel.check()
        dst.write_bytes(b"preview:" + src.read_bytes())

    def peak_dbfs(self, path: Path, *, cancel: CancelToken, log: logging.Logger) -> float:
        cancel.check()
        return -3.0

    def measure_levels(
        self, path: Path, *, cancel: CancelToken, log: logging.Logger
    ) -> LevelMeasurement:
        cancel.check()
        return LevelMeasurement(peak_dbfs=-3.0, side_peak_dbfs=-6.0)


class FakeYouTube:
    """A scripted ``YouTubeTool``; ``gate`` blocks resolve when set."""

    def __init__(self, *, fail: StemForgeError | None = None) -> None:
        self.fail = fail
        self.gate: threading.Event | None = None
        self.resolve_started = threading.Event()
        self.calls: list[str] = []

    def block_resolve(self) -> threading.Event:
        self.gate = threading.Event()
        self.resolve_started.clear()
        return self.gate

    def resolve(
        self, video: YouTubeVideo, *, cancel: CancelToken, log: logging.Logger
    ) -> YouTubeMetadata:
        del log
        self.calls.append("resolve")
        self.resolve_started.set()
        if self.fail is not None:
            raise self.fail
        if self.gate is not None:
            self.gate.wait(timeout=10)
        cancel.check()
        return YouTubeMetadata(
            video_id=video.video_id,
            title=TITLE,
            artist="Test Uploader",
            canonical_url=video.canonical_url,
            duration_s=120.0,
            upload_date="2024-01-02",
        )

    def download(
        self,
        video: YouTubeVideo,
        staging: Path,
        *,
        cancel: CancelToken,
        log: logging.Logger,
        progress,
    ) -> DownloadedAudio:
        del video, log
        cancel.check()
        self.calls.append("download")
        target = staging / f"{VIDEO_ID}.wav"
        target.write_bytes(b"fake native audio")
        progress(1.0, "Downloading audio · 100%")
        return DownloadedAudio(
            path=target,
            format_id="251",
            extractor="youtube",
            extractor_version="2026.10.26",
        )

    def tool_versions(self) -> dict[str, str]:
        return {"yt_dlp": "2026.10.26", "yt_dlp_ejs": "0.8.0", "deno": "2.9.8"}


class FakeDialog:
    def __init__(self, selections: Sequence[Sequence[str]] = ()) -> None:
        self._selections = [tuple(item) for item in selections]

    async def choose_audio_files(self) -> tuple[Path, ...]:
        if not self._selections:
            return ()
        return normalize_selected_paths(self._selections.pop(0))


@dataclass
class Harness:
    root: Path
    youtube: FakeYouTube = field(default_factory=FakeYouTube)
    dialog: FakeDialog = field(default_factory=FakeDialog)
    delay: Callable[[float], None] | None = None
    paths: AppPaths = field(init=False)
    settings: SettingsStore = field(init=False)
    runner: JobRunner = field(init=False)
    service: WorkspaceService = field(init=False)
    context: page.UiContext = field(init=False)

    def __post_init__(self) -> None:
        self.paths = AppPaths.for_home(self.root / "home").ensure()
        self.settings = SettingsStore(self.paths)
        self.runner = JobRunner(paths=self.paths)
        self.service = WorkspaceService(
            self.settings,
            paths=self.paths,
            artifacts=ArtifactStore(self.paths),
            runner=self.runner,
            engines=ServiceEngines(media=FakeMedia(), youtube=self.youtube),
            youtube_delay=self.delay if self.delay is not None else (lambda _: None),
        )
        self.context = page.UiContext(service=self.service, dialogs=self.dialog)

    def source_file(self, name: str = "Local Song.wav") -> Path:
        path = self.root / name
        path.write_bytes(b"fake local audio")
        return path


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[Harness]:
    item = Harness(root=tmp_path)
    item.runner.start()
    try:
        yield item
    finally:
        item.runner.stop(timeout=10)


async def _open_page(harness: Harness):
    built: list[page.WorkspacePage] = []

    def root() -> None:
        workspace_page = page.build_page(harness.context)
        built.append(workspace_page)
        workspace_page.build()

    simulation = user_simulation(root=root)
    user = await simulation.__aenter__()
    try:
        await user.open("/")
        assert built
        yield user, built[0]
    finally:
        await simulation.__aexit__(None, None, None)


async def _finish_jobs(
    harness: Harness,
    user,
    workspace_page: page.WorkspacePage,
    *,
    expected_jobs: int | None = None,
) -> None:
    for _ in range(300):
        jobs = harness.runner.jobs()
        if jobs and (expected_jobs is None or len(jobs) >= expected_jobs):
            break
        await asyncio.sleep(0.01)
    assert await asyncio.to_thread(harness.runner.wait_idle, 10) is True
    with user:
        workspace_page.poll()


async def test_a_valid_url_submits_and_reports_in_the_job_bar(harness: Harness) -> None:
    harness.service.accept_youtube_notice()
    gate = harness.youtube.block_resolve()

    async for user, workspace_page in _open_page(harness):
        user.find(marker="youtube-url").type(VALID_URL)
        user.find(marker="youtube-import").click()
        assert await asyncio.to_thread(harness.youtube.resolve_started.wait, 5) is True

        with user:
            workspace_page.poll()
        await user.should_see(marker="job-bar")
        await user.should_see("Importing")
        await user.should_see("Checking video")
        assert next(iter(user.find(marker="youtube-url").elements)).enabled is False
        assert [job.kind.value for job in harness.runner.jobs()] == ["import_youtube"]

        gate.set()
        await _finish_jobs(harness, user, workspace_page)

        await user.should_not_see(marker="job-bar")
        await user.should_see(marker="track-detail")
        with user.scope(marker="track-detail"):
            await user.should_see(TITLE)
            await user.should_see("YouTube")
        assert harness.service.snapshot().tracks[0].title == TITLE
        assert [entry.source_id for entry in harness.service.recent_files()] == [f"yt-{VIDEO_ID}"]


async def test_an_invalid_url_never_submits(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(harness.service, "submit_youtube", calls.append)

    async for user, _workspace_page in _open_page(harness):
        user.find(marker="youtube-url").type("https://www.youtube.com/playlist?list=PL123")
        user.find(marker="youtube-import").click()

        await user.should_see(marker="youtube-field-error")
        await user.should_see("That's a playlist or channel. Paste a single video link.")
        await user.should_not_see(marker="job-bar")
        assert calls == []
        assert harness.runner.jobs() == []


async def test_the_politeness_wait_is_injected_and_visible_in_the_job_bar(
    tmp_path: Path,
) -> None:
    wait_started = threading.Event()
    release = threading.Event()

    def gated_delay(seconds: float) -> None:
        assert seconds <= 0.5
        wait_started.set()
        release.wait(timeout=10)

    harness = Harness(root=tmp_path, delay=gated_delay)
    harness.service.accept_youtube_notice()
    harness.runner.start()
    try:
        async for user, workspace_page in _open_page(harness):
            user.find(marker="youtube-url").type(VALID_URL)
            user.find(marker="youtube-import").click()
            assert await asyncio.to_thread(wait_started.wait, 5) is True

            with user:
                workspace_page.poll()
            await user.should_see(marker="job-bar")
            await user.should_see("Checking video")
            await user.should_see("Waiting for YouTube · 5 s")

            release.set()
            await _finish_jobs(harness, user, workspace_page)
            await user.should_not_see(marker="job-bar")
            await user.should_see(marker="track-detail")
    finally:
        harness.runner.stop(timeout=10)


async def test_a_failed_import_shows_the_coded_copy(harness: Harness) -> None:
    harness.service.accept_youtube_notice()
    harness.youtube.fail = StemForgeError(
        errors.IMPORT_YOUTUBE_AGE_RESTRICTED,
        "fake age gate",
        detail="reason=age_restricted",
    )

    async for user, workspace_page in _open_page(harness):
        user.find(marker="youtube-url").type(VALID_URL)
        user.find(marker="youtube-import").click()
        await _finish_jobs(harness, user, workspace_page)

        await user.should_see(marker="import-error")
        await user.should_see(AGE_HEADLINE)
        await user.should_see("Choose a video without an age gate.")
        await user.should_not_see(marker="import-retry")
        await user.should_not_see(marker="job-bar")
        assert harness.service.snapshot().tracks == ()
        assert harness.service.recent_files() == ()
        staging = harness.paths.cache / "staging"
        assert not staging.exists() or list(staging.iterdir()) == []


async def test_a_retryable_failure_resubmits_the_same_url(harness: Harness) -> None:
    """Retry re-submits the identical request, not a dialog."""
    harness.service.accept_youtube_notice()
    harness.youtube.fail = StemForgeError(
        errors.IMPORT_NETWORK,
        "fake offline",
        retryable=True,
        detail="reason=network",
    )

    async for user, workspace_page in _open_page(harness):
        user.find(marker="youtube-url").type(VALID_URL)
        user.find(marker="youtube-import").click()
        await _finish_jobs(harness, user, workspace_page)

        await user.should_see(marker="import-error")
        await user.should_see("No connection.")
        await user.should_see(marker="import-retry")
        assert harness.service.snapshot().tracks == ()

        harness.youtube.fail = None
        user.find(marker="import-retry").click()
        await _finish_jobs(harness, user, workspace_page, expected_jobs=2)

        await user.should_not_see(marker="import-error")
        await user.should_see(marker="track-detail")
        jobs = [job for job in harness.runner.jobs() if job.kind.value == "import_youtube"]
        assert len(jobs) == 2


async def test_the_toggle_hides_the_row_but_local_import_still_works(harness: Harness) -> None:
    harness.service.set_youtube_enabled(False)
    source = harness.source_file()
    harness.dialog._selections = [(str(source),)]

    async for user, workspace_page in _open_page(harness):
        await user.should_not_see(marker="youtube-row")
        await user.should_not_see(marker="youtube-disclaimer")
        await user.should_see(marker="dropzone")

        user.find(marker="add-audio").click()
        await _finish_jobs(harness, user, workspace_page)

        await user.should_see("Local Song")
        await user.should_see(marker="track-detail")
        assert [job.kind.value for job in harness.runner.jobs()] == ["import_local"]
        assert harness.youtube.calls == []


async def test_clear_youtube_cache_is_a_settings_action(harness: Harness) -> None:
    cache = harness.paths.cache / "yt-dlp"
    cache.mkdir(parents=True)
    (cache / "scratch.part").write_bytes(b"x" * 2_097_152)

    async for user, _workspace_page in _open_page(harness):
        user.find(marker="settings-about").click()
        await user.should_see(marker="settings-dialog")
        await user.should_see("Storage")
        await user.should_see("YouTube cache")
        await user.should_see("2.0 MB")

        user.find(marker="clear-youtube-cache").click()
        await asyncio.sleep(0.1)

        assert not cache.exists()
        await user.should_see("0.0 MB")
        await user.should_see("YouTube cache cleared (2.0 MB).")
