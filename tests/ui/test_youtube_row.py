"""Headless tests for the YouTube row, consent, and toggle (ticket 15).

Every write stays under ``tmp_path``; the page is built through NiceGUI's user
simulation; the import seam is spied so a rejected URL can prove it was never
called. No test may open a socket or import ``yt_dlp`` (ticket 15, ticket 16 is
where the adapter arrives).
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from nicegui.testing.user_simulation import user_simulation

from stemforge import errors
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

VALID_URL = "https://youtu.be/dQw4w9WgXcQ?t=5"
DISCLAIMER = (
    "Only import content you have the right to use. "
    "YouTube's Terms of Service prohibit downloading."
)
CONSENT_BODY = (
    "StemForge downloads audio with yt-dlp, entirely on this machine. YouTube's Terms of "
    "Service prohibit downloading except where the law permits, and downloading may infringe "
    "copyright. Only continue if you have the right to use this content."
)


@pytest.fixture(autouse=True)
def _no_network(no_external_network: None) -> None:
    """Any non-local connection attempt in this module fails the test (ticket 15)."""


class FakeMedia:
    """A tiny local-import stand-in; the toggle test needs one real Track."""

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


class FakeYouTube:
    """The row tests never reach the network: resolve fails as unavailable."""

    def __init__(self) -> None:
        self.gate: threading.Event | None = None
        self.started = threading.Event()

    def resolve(self, video: object, *, cancel: CancelToken, log: logging.Logger) -> None:
        del video, log
        if self.gate is not None:
            self.started.set()
            self.gate.wait(10)
        cancel.check()
        raise StemForgeError(
            errors.IMPORT_YOUTUBE_UNAVAILABLE,
            "fake YouTube adapter",
            detail="reason=unavailable",
        )

    def download(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("the row tests must not download")

    def tool_versions(self) -> dict[str, str]:
        return {"yt_dlp": "2026.10.26", "yt_dlp_ejs": "0.8.0", "deno": "2.9.8"}


class FakeDialog:
    async def choose_audio_files(self) -> tuple[Path, ...]:
        return ()


@dataclass
class Harness:
    root: Path
    paths: AppPaths = field(init=False)
    settings: SettingsStore = field(init=False)
    runner: JobRunner = field(init=False)
    service: WorkspaceService = field(init=False)
    youtube: FakeYouTube = field(init=False)
    context: page.UiContext = field(init=False)

    def __post_init__(self) -> None:
        self.paths = AppPaths.for_home(self.root / "home").ensure()
        self.settings = SettingsStore(self.paths)
        self.runner = JobRunner(paths=self.paths)
        self.youtube = FakeYouTube()
        self.service = WorkspaceService(
            self.settings,
            paths=self.paths,
            artifacts=ArtifactStore(self.paths),
            runner=self.runner,
            engines=ServiceEngines(media=FakeMedia(), youtube=self.youtube),
            youtube_delay=lambda _: None,
        )
        self.context = page.UiContext(service=self.service, dialogs=FakeDialog())

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


def _spy_seam(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    original = harness.service.submit_youtube

    def spy(url: str):
        calls.append(url)
        return original(url)

    monkeypatch.setattr(harness.service, "submit_youtube", spy)
    return calls


@pytest.mark.parametrize(
    ("url", "message"),
    [
        (
            "https://www.youtube.com/playlist?list=PL123",
            "That's a playlist or channel. Paste a single video link.",
        ),
        ("https://vimeo.com/12345", "Only YouTube video links are supported."),
        (
            "https://www.youtube.com/watch?v=short",
            "That doesn't look like a YouTube link.",
        ),
    ],
)
async def test_a_rejected_link_is_a_field_error_and_never_calls_the_seam(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    message: str,
) -> None:
    calls = _spy_seam(harness, monkeypatch)

    async for user, _workspace_page in _open_page(harness):
        user.find(marker="youtube-url").type(url)
        user.find(marker="youtube-import").click()

        await user.should_see(marker="youtube-field-error")
        await user.should_see(message)
        await user.should_not_see(marker="youtube-consent")
        await user.should_not_see(marker="job-bar")
        assert calls == []
        assert harness.runner.jobs() == []


async def test_consent_appears_once_then_persists_with_the_disclaimer(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _spy_seam(harness, monkeypatch)

    async for user, workspace_page in _open_page(harness):
        await user.should_see(marker="youtube-disclaimer")
        await user.should_see(DISCLAIMER)

        user.find(marker="youtube-url").type(VALID_URL)
        user.find(marker="youtube-import").click()

        await user.should_see(marker="youtube-consent")
        await user.should_see("Import from YouTube")
        await user.should_see(CONSENT_BODY)
        await user.should_see("Cancel")
        await user.should_see("I have the right to use this content")
        assert calls == []
        assert harness.runner.jobs() == []

        user.find(marker="youtube-consent-accept").click()
        await asyncio.sleep(0.1)

        await user.should_not_see(marker="youtube-consent")
        await user.should_see(marker="youtube-disclaimer")
        assert calls == [VALID_URL]
        assert harness.service.youtube_notice_acknowledged() is True
        assert [job.kind.value for job in harness.runner.jobs()] == ["import_youtube"]
        assert await asyncio.to_thread(harness.runner.wait_idle, 10) is True

        payload = json.loads(harness.settings.path.read_text(encoding="utf-8"))
        assert payload["youtube_notice_acknowledged"] is True
        assert payload["youtube_notice_version"] == 1
        assert isinstance(payload["youtube_notice_accepted_at"], str)

        # The same page does not ask again.
        with user:
            workspace_page.poll()
        await user.should_see(marker="youtube-row")
        user.find(marker="youtube-url").type(VALID_URL)
        user.find(marker="youtube-import").click()
        await asyncio.sleep(0.05)
        await user.should_not_see(marker="youtube-consent")
        assert len(calls) == 2
        assert "yt_dlp" not in getattr(sys.modules["stemforge.engines.youtube"], "__dict__", {})

        # A fresh page build reads the persisted acknowledgment and stays quiet.
        await user.open("/")
        await user.should_see(marker="youtube-row")
        await user.should_not_see(marker="youtube-consent")
        user.find(marker="youtube-url").type(VALID_URL)
        user.find(marker="youtube-import").click()
        await user.should_not_see(marker="youtube-consent")
        assert len(calls) == 3


async def test_declining_consent_disables_the_row_and_blocks_the_seam(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _spy_seam(harness, monkeypatch)

    async for user, _workspace_page in _open_page(harness):
        user.find(marker="youtube-url").type(VALID_URL)
        user.find(marker="youtube-import").click()
        await user.should_see(marker="youtube-consent")

        user.find(marker="youtube-consent-cancel").click()
        await asyncio.sleep(0.1)

        await user.should_not_see(marker="youtube-consent")
        await user.should_see("YouTube import is off until you accept the notice.")
        await user.should_see(marker="youtube-disclaimer")
        assert next(iter(user.find(marker="youtube-url").elements)).enabled is False
        assert next(iter(user.find(marker="youtube-import").elements)).enabled is False

        # The disabled control cannot reach the seam or create a Job.
        user.find(marker="youtube-import").click()
        await asyncio.sleep(0.05)
        assert calls == []
        assert harness.runner.jobs() == []
        assert harness.service.youtube_notice_acknowledged() is False
        await user.should_not_see(marker="youtube-consent")


async def test_the_toggle_hides_only_the_youtube_row_and_keeps_consent(
    harness: Harness,
) -> None:
    harness.service.accept_youtube_notice()
    harness.import_local_track()
    harness.service.settings.record_recent(
        source_id="yt-demo",
        title="Recent YouTube Song",
        origin="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    )

    async for user, _workspace_page in _open_page(harness):
        await user.should_see("Tracks 1")
        await user.should_see(marker="track-row")
        await user.should_see(marker="youtube-row")
        await user.should_see(marker="youtube-action")
        await user.should_see(marker="dropzone")

        user.find(marker="settings-about").click()
        await user.should_see(marker="settings-dialog")
        await user.should_see("Settings & About")
        await user.should_see("YouTube import")
        await user.should_see("Off hides YouTube import.")
        user.find(marker="youtube-toggle").click()
        await asyncio.sleep(0.1)
        user.find(marker="settings-close").click()
        await asyncio.sleep(0.1)

        await user.should_not_see(marker="youtube-row")
        await user.should_not_see(marker="youtube-disclaimer")
        await user.should_not_see(marker="youtube-action")
        await user.should_see(marker="dropzone")
        await user.should_see(marker="add-audio")
        await user.should_see(marker="track-row")
        await user.should_see("Tracks 1")
        await user.should_see("YouTube import is off.")
        assert harness.service.youtube_enabled() is False
        assert (
            json.loads(harness.settings.path.read_text(encoding="utf-8"))["youtube_enabled"]
            is False
        )

        user.find(marker="settings-about").click()
        user.find(marker="youtube-toggle").click()
        await asyncio.sleep(0.1)
        user.find(marker="settings-close").click()
        await asyncio.sleep(0.1)

        await user.should_see(marker="youtube-row")
        await user.should_see(marker="youtube-disclaimer")
        await user.should_see(marker="youtube-action")
        await user.should_not_see("YouTube import is off.")
        await user.should_not_see(marker="youtube-consent")
        assert harness.service.youtube_enabled() is True
        assert harness.service.youtube_notice_acknowledged() is True
        assert [job.kind.value for job in harness.runner.jobs()] == ["import_local"]


async def test_the_disabled_row_stays_hidden_across_a_page_rebuild(harness: Harness) -> None:
    harness.service.set_youtube_enabled(False)

    async for user, _workspace_page in _open_page(harness):
        await user.should_not_see(marker="youtube-row")
        await user.should_see(marker="dropzone")

        await user.open("/")
        await user.should_not_see(marker="youtube-row")
        await user.should_see(marker="dropzone")


async def test_the_rail_action_opens_the_youtube_modal_from_a_track_view(
    harness: Harness,
) -> None:
    harness.import_local_track()
    harness.service.accept_youtube_notice()

    async for user, workspace_page in _open_page(harness):
        user.find(marker="track-row").click()
        await asyncio.sleep(0.05)
        selected = workspace_page._selected_id
        assert selected is not None
        await user.should_see(marker="track-detail")

        user.find(marker="youtube-action").click()
        await asyncio.sleep(0.05)

        await user.should_see(marker="youtube-modal")
        with user.scope(marker="youtube-modal"):
            await user.should_see(marker="youtube-url")
            await user.should_see(marker="youtube-import")
            await user.should_see(marker="youtube-disclaimer")
        assert workspace_page._selected_id == selected
        await user.should_see(marker="track-detail")


async def test_a_modal_submit_keeps_the_existing_youtube_job_path(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness.import_local_track()
    harness.service.accept_youtube_notice()
    calls = _spy_seam(harness, monkeypatch)

    async for user, workspace_page in _open_page(harness):
        user.find(marker="track-row").click()
        await asyncio.sleep(0.05)
        user.find(marker="youtube-action").click()
        await asyncio.sleep(0.05)

        user.find(marker="youtube-url").type(VALID_URL)
        user.find(marker="youtube-import").click()
        await asyncio.sleep(0.1)

        await user.should_not_see(marker="youtube-modal")
        await user.should_see(marker="job-bar")
        assert calls == [VALID_URL]
        assert sorted(job.kind.value for job in harness.runner.jobs()) == [
            "import_local",
            "import_youtube",
        ]
        assert await asyncio.to_thread(harness.runner.wait_idle, 10) is True

        with user:
            workspace_page.poll()
        user.find(marker="youtube-action").click()
        await asyncio.sleep(0.05)
        await user.should_see(marker="youtube-modal")
        assert next(iter(user.find(marker="youtube-url").elements)).value == ""


async def test_declining_in_the_modal_disables_every_youtube_surface(
    harness: Harness,
) -> None:
    async for user, _workspace_page in _open_page(harness):
        await user.should_see(marker="youtube-row")

        user.find(marker="youtube-action").click()
        await asyncio.sleep(0.05)
        with user.scope(marker="youtube-modal"):
            user.find(marker="youtube-url").type(VALID_URL)
            user.find(marker="youtube-import").click()
        await user.should_see(marker="youtube-consent")

        user.find(marker="youtube-consent-cancel").click()
        await asyncio.sleep(0.1)

        await user.should_not_see(marker="youtube-consent")
        with user.scope(marker="youtube-modal"):
            await user.should_see("YouTube import is off until you accept the notice.")
            assert next(iter(user.find(marker="youtube-url").elements)).enabled is False
            assert next(iter(user.find(marker="youtube-import").elements)).enabled is False

        user.find(marker="youtube-modal-close").click()
        await asyncio.sleep(0.1)
        await user.should_not_see(marker="youtube-modal")

        with user.scope(marker="youtube-row"):
            await user.should_see("YouTube import is off until you accept the notice.")
            assert next(iter(user.find(marker="youtube-url").elements)).enabled is False
            assert next(iter(user.find(marker="youtube-import").elements)).enabled is False


async def test_the_modal_asks_for_consent_once_and_then_imports(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness.import_local_track()
    calls = _spy_seam(harness, monkeypatch)

    async for user, workspace_page in _open_page(harness):
        user.find(marker="track-row").click()
        await asyncio.sleep(0.05)
        user.find(marker="youtube-action").click()
        await asyncio.sleep(0.05)
        user.find(marker="youtube-url").type(VALID_URL)
        user.find(marker="youtube-import").click()

        await user.should_see(marker="youtube-consent")
        user.find(marker="youtube-consent-accept").click()
        await asyncio.sleep(0.1)

        await user.should_not_see(marker="youtube-consent")
        await user.should_not_see(marker="youtube-modal")
        assert calls == [VALID_URL]
        assert "import_youtube" in [job.kind.value for job in harness.runner.jobs()]
        assert await asyncio.to_thread(harness.runner.wait_idle, 10) is True
        with user:
            workspace_page.poll()

        user.find(marker="youtube-action").click()
        await asyncio.sleep(0.05)
        user.find(marker="youtube-url").type(VALID_URL)
        user.find(marker="youtube-import").click()
        await asyncio.sleep(0.05)
        await user.should_not_see(marker="youtube-consent")
        assert len(calls) == 2


async def test_a_rejected_link_in_the_modal_is_still_a_field_error(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness.import_local_track()
    harness.service.accept_youtube_notice()
    calls = _spy_seam(harness, monkeypatch)

    async for user, _workspace_page in _open_page(harness):
        user.find(marker="track-row").click()
        await asyncio.sleep(0.05)
        user.find(marker="youtube-action").click()
        await asyncio.sleep(0.05)

        user.find(marker="youtube-url").type("https://www.youtube.com/playlist?list=PL123")
        user.find(marker="youtube-import").click()

        await user.should_see(marker="youtube-modal")
        await user.should_see(marker="youtube-field-error")
        await user.should_see("That's a playlist or channel. Paste a single video link.")
        assert calls == []
        assert [job.kind.value for job in harness.runner.jobs()] == ["import_local"]


async def test_closing_the_modal_discards_the_typed_url(harness: Harness) -> None:
    harness.import_local_track()
    harness.service.accept_youtube_notice()

    async for user, _workspace_page in _open_page(harness):
        user.find(marker="track-row").click()
        await asyncio.sleep(0.05)
        user.find(marker="youtube-action").click()
        await asyncio.sleep(0.05)
        user.find(marker="youtube-url").type(VALID_URL)

        user.find(marker="youtube-modal-close").click()
        await asyncio.sleep(0.1)
        await user.should_not_see(marker="youtube-modal")

        user.find(marker="youtube-action").click()
        await asyncio.sleep(0.05)
        assert next(iter(user.find(marker="youtube-url").elements)).value == ""


async def test_the_rail_action_is_disabled_while_a_job_runs(harness: Harness) -> None:
    harness.service.accept_youtube_notice()
    gate = threading.Event()
    harness.youtube.gate = gate

    async for user, workspace_page in _open_page(harness):
        user.find(marker="youtube-action").click()
        await asyncio.sleep(0.05)
        user.find(marker="youtube-url").type(VALID_URL)
        user.find(marker="youtube-import").click()
        assert await asyncio.to_thread(harness.youtube.started.wait, 5) is True

        with user:
            workspace_page.poll()
        await user.should_see(marker="job-bar")
        await user.should_see("Wait for the current job to finish.")
        assert next(iter(user.find(marker="youtube-action").elements)).enabled is False

        gate.set()
        assert await asyncio.to_thread(harness.runner.wait_idle, 10) is True
        with user:
            workspace_page.poll()
        assert next(iter(user.find(marker="youtube-action").elements)).enabled is True
        await user.should_see("or drop files anywhere")
