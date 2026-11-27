"""Headless tests for the About licence rows and Storage controls.

The OS file manager is an injected seam: every test here uses a scripted
opener, so no test can launch ``xdg-open`` or Explorer. The licence paths are
resolved from the Bundle root, and a missing tree is a coded message rather
than a traceback.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from media_fake import FakeMedia
from nicegui.testing.user_simulation import user_simulation

from stemforge import __version__, errors, i18n
from stemforge.application import ServiceEngines, WorkspaceService
from stemforge.artifacts import ArtifactStore
from stemforge.errors import StemForgeError
from stemforge.jobs import Job, JobKind, JobState
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


async def _wait_until_enabled(user, marker: str) -> None:
    """The modal re-enables storage controls on its own poll; wait for that state."""
    for _ in range(50):
        if _enabled(user, marker):
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"{marker} stayed disabled")


@pytest.fixture(autouse=True)
def _no_network(no_external_network: None) -> None:
    """The About modal must work offline."""


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
    language_changes: list[str] = field(default_factory=list)
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
            on_language_change=self.language_changes.append,
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
        await user.should_see(marker="about-ytdlp-version")
        await user.should_see(marker="about-ytdlp-ejs-version")
        await user.should_see(marker="about-deno-version")
        assert _only(user, "about-ytdlp-version").text
        assert _only(user, "about-ytdlp-ejs-version").text
        assert _only(user, "about-deno-version").text == "2.9.8"
        await user.should_see(i18n.t("settings.bundled_tools"))
        await user.should_see("GPL-3.0-only")
        await user.should_see(i18n.t("settings.notices"))
        await user.should_see(i18n.t("settings.source"))
        await user.should_see(marker="about-open-licences")
        await user.should_see(i18n.t("settings.open_licences"))
        assert _only(user, "about-weights-note").text == i18n.t("settings.weights_note")
        assert _only(user, "about-licence-authoritative").text == i18n.t(
            "settings.licence_authoritative"
        )
        assert _only(user, "about-translation-credit").text == i18n.t("settings.translation_credit")
        assert _only(user, "about-youtube-note").text == i18n.t("settings.youtube_note")
        for marker in (
            "clear-cached-audio",
            "clear-recent",
            "clear-youtube-cache",
            "open-log-folder",
        ):
            await user.should_see(marker=marker)
        assert _only(user, "about-close-note").text == i18n.t("settings.close_note")
        assert _only(user, "about-partial-note").text == i18n.t("settings.partial_note")
        await user.should_see(".partial")
        assert harness.service.licence_files().complete is True


async def test_the_language_row_is_first_and_persists_without_reloading(
    harness: Harness,
) -> None:
    from nicegui import app

    async for user, _workspace_page in _open_page(harness):
        await _open_settings(user)

        await user.should_see("Language")
        await user.should_see("Changes apply immediately.")
        select = _only(user, "language-select")
        assert select.value == "system"
        assert select.options == {
            "system": "System (English)",
            "en": "English",
            "ro": "Română",
        }

        select.set_value("en")

        assert harness.service.language_preference() == "en"
        assert harness.language_changes == ["en"]
        assert i18n.preference() == "en"
        assert app.config.language == "en-GB"


async def test_the_system_option_shows_the_system_language_not_the_override(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness.settings.set_language("en")
    monkeypatch.setattr(i18n, "system_locale", lambda: "ro_RO.UTF-8")

    async for user, _workspace_page in _open_page(harness):
        await _open_settings(user)

        select = _only(user, "language-select")
        assert select.value == "en"
        assert select.options["system"] == "System (Română)"


async def test_a_language_change_persists_and_reloads_the_page(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    reloads: list[bool] = []
    monkeypatch.setattr(page.ui.navigate, "reload", lambda: reloads.append(True))
    harness.context.on_language_change = None

    async for user, _workspace_page in _open_page(harness):
        await _open_settings(user)

        _only(user, "language-select").set_value("ro")

        assert harness.service.language_preference() == "ro"
        assert reloads == [True]


async def test_a_rebuilt_page_keeps_a_running_job_and_starts_without_a_preview(
    harness: Harness,
) -> None:
    """A reload rebuilds transient page state; the service and Job ledger survive.

    The player is page-owned, so Preview starts empty on the rebuilt page even
    while the shared runner keeps the running Job.
    """
    gate = threading.Event()
    started = threading.Event()

    def wait(ctx) -> None:
        started.set()
        gate.wait(10)

    harness.import_local_track("Reload Source.wav")
    track_id = harness.service.snapshot().tracks[0].track_id
    job_id = harness.runner.submit(
        Job(kind=JobKind.SEPARATE, identity=("reload-survival",), run=wait)
    )
    try:
        assert await asyncio.to_thread(started.wait, 5) is True
        assert harness.runner.snapshot(job_id).state is JobState.RUNNING

        async for user, _first in _open_page(harness):
            user.find(marker=f"track-row-{track_id}").click()
            await user.should_see(marker="output-play-original")
            user.find(marker="output-play-original").click()
            assert _only(user, "player").props["src"] != ""

        async for user, second in _open_page(harness):
            assert second is not _first
            assert _only(user, "player").props["src"] == ""
            with user:
                second.poll()
            await user.should_see(marker="job-bar")
            await user.should_see(marker=f"track-row-{track_id}")
            running = [item for item in harness.service.jobs() if item.job_id == job_id]
            assert len(running) == 1
            assert running[0].state is JobState.RUNNING
    finally:
        gate.set()
        assert await asyncio.to_thread(harness.runner.wait_idle, 10) is True


async def test_open_licences_folder_calls_the_injected_opener(harness: Harness) -> None:
    async for user, _workspace_page in _open_page(harness):
        await _open_settings(user)
        expected = harness.service.licence_files().licenses_dir

        user.find(marker="about-open-licences").click()

        assert harness.opener.calls == [expected]
        assert expected.is_dir()
        assert (expected / "README.md").is_file()


async def test_open_log_folder_calls_the_injected_opener(harness: Harness) -> None:
    async for user, _workspace_page in _open_page(harness):
        await _open_settings(user)
        user.find(marker="open-log-folder").click()

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

        await user.should_see("Couldn't open the licences folder.")


async def test_a_failing_log_opener_uses_the_logging_code(harness: Harness) -> None:
    harness.context.open_path = FailingOpener()
    async for user, _workspace_page in _open_page(harness):
        await _open_settings(user)
        user.find(marker="open-log-folder").click()

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

        user.find(marker="about-view-source").click()
        await user.should_see("Corresponding source offer")
        user.find(marker="about-document-close").click()

        user.find(marker="about-view-licence").click()
        await user.should_see("GNU GENERAL PUBLIC LICENSE")
        user.find(marker="about-document-close").click()


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

        assert live_dir.is_dir()
        assert not orphan.exists()
        assert not (tmp / "partial").exists()
        assert not youtube.exists()
        await user.should_see("Cleared")

        user.find(marker="clear-recent").click()

        assert harness.service.recent_files() == ()
        await user.should_see("Recent files cleared.")


async def test_storage_clears_are_disabled_while_a_job_runs(harness: Harness) -> None:
    """Cache clears are disabled while any Job runs."""
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
            await _wait_until_enabled(user, "clear-cached-audio")

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

        assert harness.service.youtube_enabled() is False
        user.find(marker="settings-close").click()
        await user.should_not_see(marker="youtube-row")
