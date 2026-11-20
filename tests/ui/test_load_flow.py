"""Headless NiceGUI tests for the load workflow (ticket 08).

The page is built through NiceGUI's own user simulation. Media work is injected:
the fake never spawns a process, never touches a real audio codec, and completes
(or blocks) on the test's command. Every write is under ``tmp_path``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest
from nicegui.testing.user_simulation import user_simulation

from stemforge.application import ServiceEngines, WorkspaceService
from stemforge.artifacts import ArtifactStore
from stemforge.domain.cancellation import CancelToken
from stemforge.engines.media import CanonicalMetadata, DecodeResult, MediaInfo
from stemforge.integration.dialogs import normalize_selected_paths
from stemforge.jobs.runner import JobRunner
from stemforge.platform.paths import AppPaths
from stemforge.settings import SettingsStore
from stemforge.ui import page, theme

pytestmark = pytest.mark.ui

SAMPLE_RATE = 44_100
CHANNELS = 2
DURATION_S = 187.0


class FakeMedia:
    """A scripted ``MediaTool`` that writes tiny stand-ins, never real audio."""

    def __init__(
        self,
        *,
        duration_s: float = DURATION_S,
        title: str | None = None,
        fail_probe: bool = False,
        source_channels: int = CHANNELS,
    ) -> None:
        self.duration_s = duration_s
        self.title = title
        self.fail_probe = fail_probe
        self.source_channels = source_channels
        self._gate: threading.Event | None = None
        self.decode_started = threading.Event()

    def block_decoding(self) -> threading.Event:
        self._gate = threading.Event()
        return self._gate

    def probe(self, path: Path) -> MediaInfo:
        if self.fail_probe:
            from stemforge import errors
            from stemforge.errors import StemForgeError

            raise StemForgeError(errors.MEDIA_PROBE_FAILED, "fake probe failure")
        return MediaInfo(
            codec="pcm_s16le",
            sample_rate=SAMPLE_RATE,
            channels=self.source_channels,
            duration_s=self.duration_s,
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
            duration_s=self.duration_s,
            sample_rate=SAMPLE_RATE,
            channels=CHANNELS,
            downmixed=self.source_channels > CHANNELS,
        )

    def extract_metadata(self, src: Path) -> CanonicalMetadata:
        return CanonicalMetadata(
            title=self.title or src.stem,
            duration_s=self.duration_s,
            sample_rate=SAMPLE_RATE,
            channels=CHANNELS,
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
    """An injected open dialog; each call pops the next scripted selection."""

    def __init__(self, selections: Sequence[Sequence[str]] = ()) -> None:
        self._selections = [tuple(item) for item in selections]
        self.calls = 0

    async def choose_audio_files(self) -> tuple[Path, ...]:
        self.calls += 1
        if not self._selections:
            return ()
        return normalize_selected_paths(self._selections.pop(0))


@dataclass
class Harness:
    root: Path
    media: FakeMedia
    dialog: FakeDialog
    show_recovery: bool = False
    paths: AppPaths = field(init=False)
    settings: SettingsStore = field(init=False)
    artifacts: ArtifactStore = field(init=False)
    runner: JobRunner = field(init=False)
    service: WorkspaceService = field(init=False)
    context: page.UiContext = field(init=False)

    def __post_init__(self) -> None:
        self.paths = AppPaths.for_home(self.root / "home").ensure()
        self.settings = SettingsStore(self.paths)
        self.artifacts = ArtifactStore(self.paths)
        self.runner = JobRunner(paths=self.paths)
        self.service = WorkspaceService(
            self.settings,
            paths=self.paths,
            artifacts=self.artifacts,
            runner=self.runner,
            engines=ServiceEngines(media=self.media),
        )
        self.context = page.UiContext(
            service=self.service,
            dialogs=self.dialog,
            show_recovery=self.show_recovery,
        )

    def source_file(self, name: str = "My Song.WAV") -> Path:
        path = self.root / name
        path.write_bytes(b"fake source bytes for " + name.encode())
        return path


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[Harness]:
    item = Harness(root=tmp_path, media=FakeMedia(), dialog=FakeDialog())
    item.runner.start()
    try:
        yield item
    finally:
        item.runner.stop(timeout=10)


@pytest.fixture
def multichannel_harness(tmp_path: Path) -> Iterator[Harness]:
    item = Harness(root=tmp_path, media=FakeMedia(source_channels=6), dialog=FakeDialog())
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


def _only(user, marker: str):
    elements = list(user.find(marker=marker).elements)
    assert len(elements) == 1, f"expected exactly one {marker}, found {len(elements)}"
    return elements[0]


async def _finish_jobs(
    harness: Harness, user, workspace_page: page.WorkspacePage, *, expected_jobs: int | None = None
) -> None:
    for _ in range(300):
        jobs = harness.runner.jobs()
        if jobs and (expected_jobs is None or len(jobs) >= expected_jobs):
            break
        await asyncio.sleep(0.01)
    assert await asyncio.to_thread(harness.runner.wait_idle, 10) is True
    with user:
        workspace_page.poll()


async def test_empty_state_explains_the_way_in(harness: Harness) -> None:
    async for user, _workspace_page in _open_page(harness):
        await user.should_see("Bring in a track")
        await user.should_see("Everything runs on this machine.")
        await user.should_see("Drop audio files here")
        await user.should_see("WAV, MP3, FLAC, M4A — or choose files…")
        await user.should_see("No tracks yet")
        assert _only(user, "theme-colors").props["primary"] == theme.ACCENT
        brand = _only(user, "brand-mark")
        assert brand.content.startswith('<img src="data:image/svg+xml;base64,')
        await user.should_see(marker="empty-model-card")
        await user.should_see(
            "Separation model not installed yet · 84 MB — you can load and preview now; "
            "Separation asks for the download the first time you use it."
        )
        await user.should_not_see(marker="track-detail")


async def test_the_empty_state_omits_the_model_card_once_installed(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed = replace(harness.service.model_status(), installed=True)
    monkeypatch.setattr(harness.service, "model_status", lambda: installed)

    async for user, _workspace_page in _open_page(harness):
        await user.should_see("Bring in a track")
        await user.should_not_see(marker="empty-model-card")
        await user.should_not_see("Separation model not installed yet")


async def test_a_multichannel_import_shows_the_downmix_note(
    multichannel_harness: Harness,
) -> None:
    harness = multichannel_harness
    source = harness.source_file("Surround.flac")
    harness.dialog._selections = [(str(source),)]

    async for user, workspace_page in _open_page(harness):
        user.find(marker="add-audio").click()
        await asyncio.sleep(0.1)
        await _finish_jobs(harness, user, workspace_page, expected_jobs=1)

        track = harness.service.snapshot().tracks[0]
        assert track.source.downmixed is True
        assert track.source.metadata.channels == 2
        await user.should_see(marker="track-row")

        user.find(marker="track-row").click()
        await user.should_see(marker="track-detail")
        with user.scope(marker="track-detail"):
            await user.should_see(marker="downmix-note")
            await user.should_see("Multichannel source downmixed to stereo.")


async def test_a_selected_file_becomes_a_track_with_audio_facts(harness: Harness) -> None:
    source = harness.source_file()
    harness.dialog._selections = [(str(source),)]

    async for user, workspace_page in _open_page(harness):
        user.find(marker="add-audio").click()
        await asyncio.sleep(0.1)
        await _finish_jobs(harness, user, workspace_page, expected_jobs=1)

        await user.should_see("My Song")
        await user.should_see("3:07")
        await user.should_see("Local file")
        await user.should_see("44.1 kHz")
        await user.should_see("stereo")

        user.find(marker="track-row").click()
        await user.should_see(marker="track-detail")
        with user.scope(marker="track-detail"):
            await user.should_see("My Song")
            await user.should_see("My Song.WAV")
            await user.should_see("3:07")
            await user.should_see("44.1 kHz")
            await user.should_see("stereo")
            await user.should_not_see(marker="downmix-note")
            await user.should_not_see("Multichannel source downmixed to stereo.")

        assert source.read_bytes() == b"fake source bytes for My Song.WAV"
        assert [entry.title for entry in harness.service.recent_files()] == ["My Song"]
        cache_sources = list((harness.paths.cache / "sources").iterdir())
        assert len(cache_sources) == 1
        assert (cache_sources[0] / "source.json").is_file()


async def test_window_title_follows_the_selected_track_and_clears_on_remove(
    harness: Harness,
) -> None:
    source = harness.source_file()
    harness.dialog._selections = [(str(source),)]

    async for user, workspace_page in _open_page(harness):
        assert workspace_page._client is not None
        assert workspace_page._client.resolve_title() == "StemForge"

        user.find(marker="add-audio").click()
        await asyncio.sleep(0.1)
        await _finish_jobs(harness, user, workspace_page, expected_jobs=1)
        await asyncio.sleep(0.05)
        assert workspace_page._client.resolve_title() == "StemForge — My Song"

        # Ticket 08 §4: the header carries Settings and Remove.
        user.find(marker="detail-settings").click()
        await user.should_see(marker="settings-dialog")
        user.find(marker="settings-close").click()
        await asyncio.sleep(0.05)

        user.find(marker="detail-remove").click()
        await asyncio.sleep(0.05)
        user.find(marker="confirm-remove").click()
        await asyncio.sleep(0.05)
        assert workspace_page._client.resolve_title() == "StemForge"


async def test_signal_path_starts_at_source_only(harness: Harness) -> None:
    source = harness.source_file()
    harness.dialog._selections = [(str(source),)]

    async for user, workspace_page in _open_page(harness):
        user.find(marker="add-audio").click()
        await asyncio.sleep(0.1)
        await _finish_jobs(harness, user, workspace_page, expected_jobs=1)
        await asyncio.sleep(0.05)

        with user.scope(marker="signal-path"):
            await user.should_see("Source")
            await user.should_see("Separation")
            await user.should_see("not run")
            await user.should_see("Pitch")
            await user.should_see("not rendered")

        await user.should_see(marker="track-meta")
        assert "No stems yet" in _only(user, "track-meta").text


async def test_the_job_bar_reports_stage_progress_and_cancel(harness: Harness) -> None:
    source = harness.source_file()
    harness.dialog._selections = [(str(source),)]
    gate = harness.media.block_decoding()

    async for user, workspace_page in _open_page(harness):
        user.find(marker="add-audio").click()
        assert await asyncio.to_thread(harness.media.decode_started.wait, 5)
        with user:
            workspace_page.poll()

        await user.should_see(marker="job-bar")
        await user.should_see("Importing")
        await user.should_see("Decoding")
        await user.should_see("Cancel")
        await user.should_not_see("My Song")

        user.find(marker="cancel-job").click()
        gate.set()
        await _finish_jobs(harness, user, workspace_page, expected_jobs=1)

        await user.should_not_see(marker="job-bar")
        await user.should_see("Import cancelled.")
        await user.should_not_see(marker="import-error")
        await user.should_not_see("My Song")


async def test_import_failure_shows_the_coded_headline_and_keeps_the_app_usable(
    harness: Harness,
) -> None:
    source = harness.source_file("broken.wav")
    harness.dialog._selections = [(str(source),)]
    harness.media.fail_probe = True

    async for user, workspace_page in _open_page(harness):
        user.find(marker="add-audio").click()
        await asyncio.sleep(0.1)
        await _finish_jobs(harness, user, workspace_page, expected_jobs=1)

        await user.should_see(marker="import-error")
        await user.should_see(
            "StemForge can't read this file. It may be damaged or in a format it doesn't support."
        )
        await user.should_see("Try WAV, MP3, FLAC, or M4A.")
        await user.should_not_see(marker="job-bar")
        assert harness.service.snapshot().tracks == ()

        # The app is still usable: the dialog opens again and a good file loads.
        harness.media.fail_probe = False
        harness.dialog._selections = [(str(harness.source_file("good.mp3")),)]
        user.find(marker="add-audio").click()
        await asyncio.sleep(0.1)
        await _finish_jobs(harness, user, workspace_page, expected_jobs=2)
        await user.should_see("good")
        await user.should_not_see(marker="import-error")


async def test_remove_confirms_cache_deletion_and_never_touches_the_source(
    harness: Harness,
) -> None:
    source = harness.source_file()
    original = source.read_bytes()
    harness.dialog._selections = [(str(source),)]

    async for user, workspace_page in _open_page(harness):
        user.find(marker="add-audio").click()
        await asyncio.sleep(0.1)
        await _finish_jobs(harness, user, workspace_page, expected_jobs=1)
        await user.should_see("My Song")

        source_id = harness.service.snapshot().tracks[0].source_id
        cache_source = harness.paths.cache / "sources" / source_id
        assert cache_source.is_dir()

        user.find(marker="remove-track").click()
        await user.should_see(marker="remove-dialog")
        await user.should_see("deletes its cached audio")
        await user.should_see("The file on disk is not touched.")
        user.find(marker="confirm-remove").click()
        await asyncio.sleep(0.1)

        await user.should_not_see(marker="track-row")
        await user.should_see("No tracks yet")
        assert harness.service.snapshot().tracks == ()
        assert not cache_source.exists()
        assert source.is_file()
        assert source.read_bytes() == original
        assert [entry.origin for entry in harness.service.recent_files()] == [str(source)]


async def test_recent_reopens_a_file_and_reports_a_missing_source(harness: Harness) -> None:
    source = harness.source_file()
    harness.dialog._selections = [(str(source),)]

    async for user, workspace_page in _open_page(harness):
        user.find(marker="add-audio").click()
        await asyncio.sleep(0.1)
        await _finish_jobs(harness, user, workspace_page, expected_jobs=1)
        await user.should_see(marker="track-row")
        user.find(marker="remove-track").click()
        user.find(marker="confirm-remove").click()
        await user.should_not_see(marker="track-row")
        await user.should_see(marker="recent-row")

        # A cache miss for a source that still exists re-imports it.
        user.find(marker="recent-row").click()
        await asyncio.sleep(0.1)
        await _finish_jobs(harness, user, workspace_page, expected_jobs=2)
        await user.should_see(marker="track-row")
        assert len(harness.service.snapshot().tracks) == 1

        # Remove the track again, then make the user's file disappear.
        user.find(marker="remove-track").click()
        user.find(marker="confirm-remove").click()
        await user.should_not_see(marker="track-row")
        source.unlink()
        await user.should_see("missing", retries=10)

        user.find(marker="recent-row").click()
        await asyncio.sleep(0.1)
        await user.should_see(marker="import-error")
        await user.should_see(
            "Can't open that file. It may have moved, been deleted, or be unreadable."
        )
        await user.should_see("Choose a file you can open.")
        await user.should_not_see(marker="job-bar")


async def test_track_rows_are_selectable_and_the_detail_follows_the_selection(
    harness: Harness,
) -> None:
    first = harness.source_file("First.wav")
    second = harness.source_file("Second.mp3")
    harness.dialog._selections = [(str(first), str(second))]

    async for user, workspace_page in _open_page(harness):
        user.find(marker="add-audio").click()
        await asyncio.sleep(0.1)
        await _finish_jobs(harness, user, workspace_page, expected_jobs=2)

        await user.should_see("First")
        await user.should_see("Second")
        tracks = harness.service.snapshot().tracks
        assert len(tracks) == 2
        first_id = next(track.track_id for track in tracks if track.title == "First")
        second_id = next(track.track_id for track in tracks if track.title == "Second")

        user.find(marker=f"track-row-{second_id}").click()
        await asyncio.sleep(0.1)
        with user.scope(marker="track-detail"):
            await user.should_see("Second")

        user.find(marker=f"track-row-{first_id}").click()
        await asyncio.sleep(0.1)
        with user.scope(marker="track-detail"):
            await user.should_see("First")


async def test_recovery_banner_shows_once_after_the_sweep_and_stays_dismissed(
    harness: Harness,
) -> None:
    staging = harness.paths.cache / "staging" / "crashed-job"
    staging.mkdir(parents=True)
    (staging / "part.wav").write_bytes(b"unfinished")
    (harness.paths.cache / "tmp").mkdir(parents=True, exist_ok=True)
    (harness.paths.cache / "tmp" / "leftover").write_bytes(b"tmp")
    report = harness.service.sweep_stale()
    assert report.deleted_anything is True
    harness.context.show_recovery = report.deleted_anything

    async for user, _workspace_page in _open_page(harness):
        await user.should_see(marker="recovery-banner")
        await user.should_see(
            "StemForge closed unexpectedly last time. Unfinished work was discarded; "
            "cached audio and completed renders are intact."
        )
        user.find("Dismiss").click()
        await user.should_not_see(marker="recovery-banner")

        # The dismissal is session state, not a one-render accident.
        await user.open("/")
        await user.should_not_see(marker="recovery-banner")


async def test_a_clean_sweep_shows_no_recovery_banner(harness: Harness) -> None:
    report = harness.service.sweep_stale()
    assert report.deleted_anything is False
    harness.context.show_recovery = report.deleted_anything

    async for user, _workspace_page in _open_page(harness):
        await user.should_not_see(marker="recovery-banner")


async def test_drop_accepts_real_paths_and_quietly_ignores_fake_ones(harness: Harness) -> None:
    async for user, workspace_page in _open_page(harness):
        with user:
            workspace_page.handle_drop([r"C:\fakepath\song.wav", "file:///tmp/song.wav"])
        await user.should_see("That drop didn't include a usable audio file. Use Add audio.")
        await user.should_not_see(marker="job-bar")
        assert harness.service.snapshot().tracks == ()

        source = harness.source_file("Dropped.wav")
        with user:
            workspace_page.handle_drop([str(source)])
        await asyncio.sleep(0.1)
        await _finish_jobs(harness, user, workspace_page, expected_jobs=1)
        await user.should_see("Dropped")


async def test_an_unsupported_dialog_extension_is_a_field_error_without_a_job(
    harness: Harness,
) -> None:
    harness.dialog._selections = [(str(harness.root / "notes.ogg"),)]

    async for user, _workspace_page in _open_page(harness):
        user.find(marker="add-audio").click()
        await user.should_see(marker="import-error")
        await user.should_see("That file type isn't supported.")
        await user.should_see("Choose a WAV, MP3, FLAC, or M4A file.")
        await user.should_not_see(marker="job-bar")
        assert harness.runner.jobs() == []
