"""Headless tests for the About licence rows and Storage controls (ticket 22).

The OS file manager is an injected seam: every test here uses a scripted
opener, so no test can launch ``xdg-open`` or Explorer. The licence paths are
resolved from the Bundle root, and a missing tree is a coded message rather
than a traceback.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from nicegui.testing.user_simulation import user_simulation

from stemforge import __version__, errors
from stemforge.application import ServiceEngines, WorkspaceService
from stemforge.artifacts import ArtifactStore
from stemforge.domain.cancellation import CancelToken
from stemforge.engines.media import CanonicalMetadata, DecodeResult, MediaInfo
from stemforge.errors import StemForgeError
from stemforge.jobs.runner import JobRunner
from stemforge.platform.paths import AppPaths
from stemforge.settings import SettingsStore
from stemforge.ui import page

pytestmark = pytest.mark.ui

MISSING_HEADLINE = "The licence files are missing from this installation."


def _only(user, marker: str):
    elements = list(user.find(marker=marker).elements)
    assert len(elements) == 1, f"expected exactly one {marker}, found {len(elements)}"
    return elements[0]


def _enabled(user, marker: str) -> bool:
    return bool(_only(user, marker).enabled)


@pytest.fixture(autouse=True)
def _no_network(no_external_network: None) -> None:
    """The About modal must work offline (ticket 13 §10)."""


class FakeMedia:
    """A local-import stand-in so one live Track can prove Clear cache is safe."""

    def __init__(self) -> None:
        self._gate: threading.Event | None = None
        self.decode_started = threading.Event()

    def block_decoding(self) -> threading.Event:
        self._gate = threading.Event()
        return self._gate

    def probe(self, path: Path) -> MediaInfo:
        return MediaInfo(
            codec="pcm_s16le",
            sample_rate=44_100,
            channels=2,
            duration_s=120.0,
            format="wav",
            tags={},
        )

    def decode_canonical(
        self, src: Path, dst: Path, *, cancel: CancelToken, log: logging.Logger
    ) -> DecodeResult:
        cancel.check()
        self.decode_started.set()
        if self._gate is not None:
            self._gate.wait(timeout=10)
        cancel.check()
        dst.write_bytes(b"canonical:" + src.read_bytes())
        return DecodeResult(
            path=dst,
            duration_s=120.0,
            sample_rate=44_100,
            channels=2,
            downmixed=False,
        )

    def extract_metadata(self, src: Path) -> CanonicalMetadata:
        return CanonicalMetadata(
            title=src.stem,
            duration_s=120.0,
            sample_rate=44_100,
            channels=2,
        )

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


class FakeDialog:
    async def choose_audio_files(self) -> tuple[Path, ...]:
        return ()


class RecordingOpener:
    """The injected OS seam; it records paths instead of opening a file manager."""

    def __init__(self) -> None:
        self.calls: list[Path] = []

    def open_path(self, path: Path) -> None:
        self.calls.append(path)


class FailingOpener:
    def open_path(self, path: Path) -> None:
        raise StemForgeError(errors.LICENSING_OPEN_FAILED, "fake opener failure")


@dataclass
class Harness:
    root: Path
    bundle_root: Path | None = None
    paths: AppPaths = field(init=False)
    settings: SettingsStore = field(init=False)
    runner: JobRunner = field(init=False)
    service: WorkspaceService = field(init=False)
    media: FakeMedia = field(default_factory=FakeMedia)
    opener: RecordingOpener = field(default_factory=RecordingOpener)
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
            engines=ServiceEngines(media=self.media),
            youtube_delay=lambda _: None,
            bundle_root=self.bundle_root,
        )
        self.context = page.UiContext(
            service=self.service,
            dialogs=FakeDialog(),
            open_path=self.opener,
        )

    def import_local_track(self, name: str = "Local Song.wav") -> None:
        source = self.root / name
        source.write_bytes(b"fake local audio")
        self.service.import_local(source)
        assert self.runner.wait_idle(10) is True


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


async def _open_settings(user) -> None:
    user.find(marker="settings-about").click()
    await user.should_see(marker="settings-dialog")


async def test_modal_shows_version_licence_notices_and_source_offer(harness: Harness) -> None:
    async for user, _workspace_page in _open_page(harness):
        await _open_settings(user)

        await user.should_see("Settings & About")
        await user.should_see(marker="about-app-mark")
        await user.should_see(marker="about-version")
        await user.should_see(f"StemForge {__version__}")
        await user.should_see("GPL-3.0-only")
        await user.should_see("Third-party notices")
        await user.should_see("Corresponding source")
        await user.should_see("Open licences folder")
        await user.should_see("htdemucs weights are downloaded from Hugging Face")
        await user.should_see(marker="clear-cached-audio")
        await user.should_see(marker="clear-recent")
        await user.should_see(marker="clear-youtube-cache")
        await user.should_see(marker="open-log-folder")
        await user.should_see(marker="about-close-note")
        await user.should_see("There is no close prompt.")
        await user.should_see(marker="about-partial-note")
        await user.should_see(".partial")
        assert harness.service.licence_files().complete is True


async def test_open_licences_folder_calls_the_injected_opener(harness: Harness) -> None:
    async for user, _workspace_page in _open_page(harness):
        await _open_settings(user)
        expected = harness.service.licence_files().licenses_dir

        user.find(marker="about-open-licences").click()
        await asyncio.sleep(0.1)

        assert harness.opener.calls == [expected]
        assert expected.is_dir()
        assert (expected / "README.md").is_file()


async def test_open_log_folder_calls_the_injected_opener(harness: Harness) -> None:
    async for user, _workspace_page in _open_page(harness):
        await _open_settings(user)
        user.find(marker="open-log-folder").click()
        await asyncio.sleep(0.1)

        assert harness.opener.calls == [harness.service.logs_dir()]
        assert harness.service.logs_dir().is_dir()


async def test_missing_tree_shows_the_coded_fallback_and_never_opens(
    tmp_path: Path,
) -> None:
    item = Harness(root=tmp_path, bundle_root=tmp_path / "absent-bundle")
    item.runner.start()
    try:
        async for user, _workspace_page in _open_page(item):
            await _open_settings(user)

            await user.should_see(MISSING_HEADLINE)
            user.find(marker="about-open-licences").click()
            await asyncio.sleep(0.1)

            assert item.opener.calls == []
            await user.should_see(MISSING_HEADLINE)
            assert item.service.licence_files().complete is False
    finally:
        item.runner.stop(timeout=10)


async def test_a_failing_opener_is_a_coded_message_not_a_traceback(harness: Harness) -> None:
    harness.context.open_path = FailingOpener()
    async for user, _workspace_page in _open_page(harness):
        await _open_settings(user)
        user.find(marker="about-open-licences").click()
        await asyncio.sleep(0.1)

        await user.should_see("Couldn't open the licences folder.")


async def test_a_failing_log_opener_uses_the_logging_code(harness: Harness) -> None:
    harness.context.open_path = FailingOpener()
    async for user, _workspace_page in _open_page(harness):
        await _open_settings(user)
        user.find(marker="open-log-folder").click()
        await asyncio.sleep(0.1)

        await user.should_see("Couldn't open the log folder.")
        assert errors.LOGGING_OPEN_FAILED not in _only(user, "about-files-error").text
        assert errors.LOGGING_OPEN_FAILED in _only(user, "about-files-error-code").text


async def test_documents_open_as_safe_plain_text(harness: Harness) -> None:
    async for user, _workspace_page in _open_page(harness):
        await _open_settings(user)

        user.find(marker="about-view-notices").click()
        await user.should_see(marker="about-document")
        await user.should_see("FFmpeg")
        user.find(marker="about-document-close").click()
        await asyncio.sleep(0.05)

        user.find(marker="about-view-source").click()
        await user.should_see(marker="about-document")
        await user.should_see("Corresponding source offer")
        user.find(marker="about-document-close").click()
        await asyncio.sleep(0.05)

        user.find(marker="about-view-licence").click()
        await user.should_see(marker="about-document")
        await user.should_see("GNU GENERAL PUBLIC LICENSE")
        user.find(marker="about-document-close").click()
        await asyncio.sleep(0.05)


async def test_storage_controls_clear_only_what_they_own(harness: Harness) -> None:
    harness.import_local_track()
    source = next(iter(harness.service.snapshot().tracks)).source
    live_dir = harness.paths.cache / "sources" / source.source_id
    orphan = harness.paths.cache / "sources" / "orphan"
    orphan.mkdir(parents=True)
    (orphan / "stale.wav").write_bytes(b"orphan")
    tmp = harness.paths.cache / "tmp" / "scratch"
    tmp.mkdir(parents=True)
    (tmp / "partial").write_bytes(b"partial")
    youtube = harness.paths.cache / "yt-dlp" / "scratch"
    youtube.mkdir(parents=True)
    (youtube / "download.part").write_bytes(b"partial")
    harness.service.settings.record_recent(
        source_id="recent-demo",
        title="Recent Song",
        origin=str(harness.root / "Recent Song.wav"),
    )

    assert live_dir.is_dir()

    async for user, _workspace_page in _open_page(harness):
        await _open_settings(user)
        await user.should_see(marker="about-cache-size")
        await user.should_see(marker="about-recent-size")

        user.find(marker="clear-cached-audio").click()
        await asyncio.sleep(0.1)

        assert live_dir.is_dir()
        assert not orphan.exists()
        assert not (tmp / "partial").exists()
        assert not youtube.exists()
        await user.should_see("Cleared")

        user.find(marker="clear-recent").click()
        await asyncio.sleep(0.1)

        assert harness.service.recent_files() == ()
        await user.should_see("Recent files cleared.")


async def test_storage_clears_are_disabled_while_a_job_runs(harness: Harness) -> None:
    """Ticket 14 §6: cache clears are disabled while any Job runs."""
    source = harness.root / "Busy.wav"
    source.write_bytes(b"fake local audio")
    gate = harness.media.block_decoding()
    harness.service.import_local(source)
    assert await asyncio.to_thread(harness.media.decode_started.wait, 5) is True

    try:
        async for user, _workspace_page in _open_page(harness):
            await _open_settings(user)

            assert _enabled(user, "clear-cached-audio") is False
            assert _enabled(user, "clear-youtube-cache") is False
            assert _enabled(user, "clear-recent") is True

            gate.set()
            assert await asyncio.to_thread(harness.runner.wait_idle, 10) is True
            await asyncio.sleep(0.6)

            assert _enabled(user, "clear-cached-audio") is True
            assert _enabled(user, "clear-youtube-cache") is True
    finally:
        gate.set()
        harness.runner.wait_idle(10)


async def test_the_youtube_toggle_still_works_from_the_modal(harness: Harness) -> None:
    async for user, _workspace_page in _open_page(harness):
        await _open_settings(user)
        assert harness.service.youtube_enabled() is True

        user.find(marker="youtube-toggle").click()
        await asyncio.sleep(0.1)

        assert harness.service.youtube_enabled() is False
        user.find(marker="settings-close").click()
        await asyncio.sleep(0.05)
        await user.should_not_see(marker="youtube-row")
