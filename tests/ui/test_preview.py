"""Headless NiceGUI tests for the preview player, A/B, and degraded mode.

The page owns exactly one ``<audio>`` element. Per-row play, transport play, and
A/B only swap its source; no second player is ever created. A degraded preflight
report disables transport controls without disabling Separation, Pitch, or
Export. Media work is injected and every write stays under ``tmp_path``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
from nicegui.testing.user_simulation import user_simulation

from stemforge.application import ServiceEngines, WorkspaceService
from stemforge.artifacts import ArtifactStore
from stemforge.domain.cancellation import CancelToken
from stemforge.engines.media import CanonicalMetadata, DecodeResult, MediaInfo
from stemforge.integration.dialogs import normalize_selected_paths
from stemforge.jobs.runner import JobRunner
from stemforge.platform import preflight
from stemforge.platform.paths import AppPaths
from stemforge.settings import SettingsStore
from stemforge.ui import page

pytestmark = pytest.mark.ui

SAMPLE_RATE = 44_100
CHANNELS = 2
DURATION_S = 187.0


class FakeMedia:
    """A scripted ``MediaTool`` that writes tiny stand-ins, never real audio."""

    def __init__(self, *, duration_s: float = DURATION_S) -> None:
        self.duration_s = duration_s
        self._gate: threading.Event | None = None
        self.decode_started = threading.Event()

    def block_decoding(self) -> threading.Event:
        self._gate = threading.Event()
        return self._gate

    def probe(self, path: Path) -> MediaInfo:
        return MediaInfo(
            codec="pcm_s16le",
            sample_rate=SAMPLE_RATE,
            channels=CHANNELS,
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
            downmixed=False,
        )

    def extract_metadata(self, src: Path) -> CanonicalMetadata:
        return CanonicalMetadata(
            title=src.stem,
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
    preflight_report: preflight.PreflightReport | None = None
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
            preflight=self.preflight_report,
        )

    def source_file(self, name: str = "My Song.WAV") -> Path:
        path = self.root / name
        path.write_bytes(b"fake source bytes for " + name.encode())
        return path

    def add_render(self, source_id: str, *, semitones: int = 3) -> str:
        """Commit a real render artifact so the A/B hook sees a newest preview."""
        key = f"render-{source_id[:8]}-{semitones}"
        staging = self.artifacts.stage("preview-ui-test")
        payload = b"render preview bytes"
        (staging / "original_preview.mp3").write_bytes(payload)
        manifest = {
            "schema_version": 1,
            "kind": "render",
            "render_key": key,
            "source_id": source_id,
            "created_at": datetime.now(UTC).isoformat(),
            "semitones": semitones,
            "scope": "original",
            "gain_db": 0.0,
            "files": [
                {
                    "path": "original_preview.mp3",
                    "role": "original_preview",
                    "size": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            ],
        }
        self.artifacts.commit_render(staging, key, manifest)
        return key


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[Harness]:
    item = Harness(root=tmp_path, media=FakeMedia(), dialog=FakeDialog())
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


async def _import(
    harness: Harness, user, workspace_page: page.WorkspacePage, *names: str
) -> list[str]:
    paths = tuple(str(harness.source_file(name)) for name in names)
    harness.dialog._selections = [paths]
    user.find(marker="add-audio").click()
    await asyncio.sleep(0.05)
    await _finish_jobs(harness, user, workspace_page, expected_jobs=len(names))
    return [track.track_id for track in harness.service.snapshot().tracks]


def _only(user, marker: str):
    elements = list(user.find(marker=marker).elements)
    assert len(elements) == 1, f"expected exactly one {marker}, found {len(elements)}"
    return elements[0]


def _player(user):
    return _only(user, "player")


def _enabled(user, marker: str) -> bool:
    element = _only(user, marker)
    return bool(element.enabled)


async def test_one_player_serves_per_row_play_from_every_track(harness: Harness) -> None:
    async for user, workspace_page in _open_page(harness):
        first, second = await _import(harness, user, workspace_page, "First.wav", "Second.wav")
        await asyncio.sleep(0.05)

        assert len(user.find(marker="player").elements) == 1
        player = _player(user)
        assert player.props["src"] == ""

        user.find(marker=f"track-play-{first}").click()
        first_source = harness.service.preview_targets(first)[0].url
        assert player.props["src"] == first_source
        assert len(user.find(marker="player").elements) == 1

        # Starting the other Track replaces the source of the same element.
        user.find(marker=f"track-play-{second}").click()
        second_source = harness.service.preview_targets(second)[0].url
        assert player.props["src"] == second_source
        assert len(user.find(marker="player").elements) == 1
        assert first_source != second_source


async def test_transport_and_output_rows_drive_the_same_single_player(harness: Harness) -> None:
    async for user, workspace_page in _open_page(harness):
        (track_id,) = await _import(harness, user, workspace_page, "Song.wav")
        await asyncio.sleep(0.05)
        user.find(marker=f"track-row-{track_id}").click()
        await asyncio.sleep(0.05)

        player = _player(user)
        expected = harness.service.preview_targets(track_id)[0].url

        user.find(marker="output-play-original").click()
        assert player.props["src"] == expected
        await user.should_see("Original")

        # The transport toggles the same element rather than opening another.
        user.find(marker="transport-play").click()
        assert _player(user) is player
        assert len(user.find(marker="player").elements) == 1

        user.find(marker="transport-play").click()
        assert _player(user) is player

        seek = _only(user, "seek")
        assert float(seek.props["max"]) == DURATION_S


async def test_ab_swaps_original_and_newest_preview_in_one_player(harness: Harness) -> None:
    async for user, workspace_page in _open_page(harness):
        (track_id,) = await _import(harness, user, workspace_page, "Shifted.wav")
        await asyncio.sleep(0.05)
        user.find(marker=f"track-row-{track_id}").click()
        await asyncio.sleep(0.05)

        # Without a render, B is disabled (ticket 08 §7).
        assert _enabled(user, "ab-preview") is False
        assert _enabled(user, "ab-original") is True

        source_id = harness.service.snapshot().tracks[0].source_id
        render_key = harness.add_render(source_id, semitones=3)
        with user:
            workspace_page.poll()
        await asyncio.sleep(0.05)
        user.find(marker=f"track-row-{track_id}").click()
        await asyncio.sleep(0.05)

        player = _player(user)
        original = harness.service.preview_targets(track_id)[0].url
        preview = f"/media/renders/{render_key}/original_preview.mp3"
        assert _enabled(user, "ab-preview") is True

        user.find(marker="ab-preview").click()
        assert player.props["src"] == preview
        assert len(user.find(marker="player").elements) == 1

        user.find(marker="ab-original").click()
        assert player.props["src"] == original
        assert len(user.find(marker="player").elements) == 1


async def test_a_missing_preview_is_unavailable_instead_of_a_dead_url(harness: Harness) -> None:
    async for user, workspace_page in _open_page(harness):
        (track_id,) = await _import(harness, user, workspace_page, "Gone.wav")
        await asyncio.sleep(0.05)
        preview = harness.service.snapshot().tracks[0].source.preview_path
        assert preview is not None
        preview.unlink()

        user.find(marker=f"track-row-{track_id}").click()
        await asyncio.sleep(0.05)

        await user.should_see(marker="preview-unavailable")
        await user.should_not_see(marker="output-play-original")
        assert _enabled(user, "transport-play") is False
        assert _enabled(user, f"track-play-{track_id}") is False


async def test_degraded_preview_keeps_a_banner_and_disables_only_transport(
    tmp_path: Path,
) -> None:
    report = preflight.PreflightReport(
        checks=(
            preflight.CheckResult(
                name=preflight.CHECK_PREVIEW,
                status=preflight.STATUS_DEGRADED,
                message="GStreamer is missing mpg123audiodec.",
                fatal=False,
            ),
        ),
        fail_on_degraded=False,
    )
    harness = Harness(
        root=tmp_path,
        media=FakeMedia(),
        dialog=FakeDialog(),
        preflight_report=report,
    )
    harness.runner.start()
    try:
        async for user, workspace_page in _open_page(harness):
            (track_id,) = await _import(harness, user, workspace_page, "Quiet.wav")
            await asyncio.sleep(0.05)
            user.find(marker=f"track-row-{track_id}").click()
            await asyncio.sleep(0.05)

            await user.should_see(marker="playback-banner")
            await user.should_see("Playback isn't available on this system.")
            await user.should_see("Separation, pitch, and export still work.")

            assert _enabled(user, "transport-play") is False
            assert _enabled(user, "ab-original") is False
            assert _enabled(user, "ab-preview") is False
            assert _enabled(user, f"track-play-{track_id}") is False
            assert _enabled(user, "output-play-original") is False

            # Other capabilities stay usable (ticket 14 §3).
            assert _enabled(user, "add-audio") is True
            assert _enabled(user, "separation-action") is True
            assert _enabled(user, "pitch-action") is True
            assert _enabled(user, "export-action") is True
    finally:
        harness.runner.stop(timeout=10)


async def test_playback_stays_enabled_while_second_job_actions_disable(
    harness: Harness,
) -> None:
    """Ticket 08 §11: A/B and playback stay enabled while a Job runs."""
    async for user, workspace_page in _open_page(harness):
        (track_id,) = await _import(harness, user, workspace_page, "Playing.wav")
        await asyncio.sleep(0.05)
        source_id = harness.service.snapshot().tracks[0].source_id
        render_key = harness.add_render(source_id, semitones=2)
        user.find(marker=f"track-row-{track_id}").click()
        await asyncio.sleep(0.05)
        assert _enabled(user, "transport-play") is True

        gate = harness.media.block_decoding()
        harness.dialog._selections = [(str(harness.source_file("Later.wav")),)]
        user.find(marker="add-audio").click()
        assert await asyncio.to_thread(harness.media.decode_started.wait, 5)
        with user:
            workspace_page.poll()

        # Playback and A/B remain live during the import Job.
        assert _enabled(user, "transport-play") is True
        assert _enabled(user, "seek") is True
        assert _enabled(user, "ab-original") is True
        assert _enabled(user, "ab-preview") is True
        assert _enabled(user, f"track-play-{track_id}") is True
        assert _enabled(user, "output-play-original") is True

        player = _player(user)
        original = harness.service.preview_targets(track_id)[0].url
        preview = f"/media/renders/{render_key}/original_preview.mp3"
        user.find(marker="ab-preview").click()
        assert player.props["src"] == preview
        user.find(marker="ab-original").click()
        assert player.props["src"] == original
        user.find(marker="transport-play").click()
        assert len(user.find(marker="player").elements) == 1

        # Only actions that would submit a second Job are disabled.
        assert _enabled(user, "add-audio") is False
        assert _enabled(user, "separation-action") is False
        assert _enabled(user, "pitch-action") is False
        assert _enabled(user, "export-action") is False

        gate.set()
        await _finish_jobs(harness, user, workspace_page, expected_jobs=2)
        assert _enabled(user, "add-audio") is True
        await user.should_see("Later")


async def test_deleting_the_playing_preview_stops_and_reports_unavailable(
    harness: Harness,
) -> None:
    async for user, workspace_page in _open_page(harness):
        (track_id,) = await _import(harness, user, workspace_page, "Gone.wav")
        await asyncio.sleep(0.05)
        user.find(marker=f"track-row-{track_id}").click()
        await asyncio.sleep(0.05)
        user.find(marker=f"track-play-{track_id}").click()
        assert _player(user).props["src"] != ""

        preview = harness.service.snapshot().tracks[0].source.preview_path
        assert preview is not None
        preview.unlink()
        with user:
            workspace_page.poll()
        await asyncio.sleep(0.05)

        assert _only(user, "now-playing").text == "Preview unavailable"
        assert _enabled(user, "transport-play") is False
        assert _enabled(user, f"track-play-{track_id}") is False
        await user.should_see(marker="preview-unavailable")
        assert len(user.find(marker="player").elements) == 1
