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

    def __init__(self, *, duration_s: float = DURATION_S, peak_dbfs: float = -3.0) -> None:
        self.duration_s = duration_s
        self.peak_dbfs_value = peak_dbfs
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
        return self.peak_dbfs_value


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

    def add_render(self, source_id: str, *, semitones: int = 3, gain_db: float = 0.0) -> str:
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
            "gain_db": gain_db,
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
        # Ticket 08 §4: the transport is a 5 px band, not a standard slider.
        assert seek.props["track-size"] == "5px"
        assert seek.props["thumb-size"] == "0px"
        assert _only(user, "ab-label").text == "A/B"
        # Ticket 42: the A/B segments and the transport Export share one box,
        # while the status chips stay label-sized.
        assert "sf-control" in _only(user, "ab-original").classes
        assert "sf-control" in _only(user, "ab-preview").classes
        assert "sf-control" in _only(user, "export-action").classes
        assert "sf-control" not in _only(user, "signal-separation").classes
        assert "sf-control" not in _only(user, "signal-pitch").classes

        # Ticket 08 §4: the transport advances with the media element.
        user.find(marker="player").trigger("timeupdate", 42.0)
        await asyncio.sleep(0.05)
        assert _only(user, "elapsed").text == "0:42"
        assert float(_only(user, "seek").value) == 42.0


async def test_detail_header_shows_duration_and_format(harness: Harness) -> None:
    async for user, workspace_page in _open_page(harness):
        (track_id,) = await _import(harness, user, workspace_page, "Song.wav")
        await asyncio.sleep(0.05)
        user.find(marker=f"track-row-{track_id}").click()
        await asyncio.sleep(0.05)

        meta = _only(user, "detail-meta")
        assert meta.text == "Local file · Song.wav · 3:07 · 44.1 kHz · stereo"


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
        assert _only(user, "ab-preview").text == "+3"

        user.find(marker="ab-preview").click()
        await asyncio.sleep(0.05)
        assert player.props["src"] == preview
        assert len(user.find(marker="player").elements) == 1
        assert _only(user, "now-playing").text == "Shifted master · +3"

        user.find(marker="ab-original").click()
        assert player.props["src"] == original
        assert len(user.find(marker="player").elements) == 1


async def test_the_ab_shortcut_highlights_only_the_latest_render(harness: Harness) -> None:
    """Ticket 08 §7: B is the latest render shortcut, not every shifted set."""
    async for user, workspace_page in _open_page(harness):
        (track_id,) = await _import(harness, user, workspace_page, "Shifted.wav")
        await asyncio.sleep(0.05)
        user.find(marker=f"track-row-{track_id}").click()
        await asyncio.sleep(0.05)

        source_id = harness.service.snapshot().tracks[0].source_id
        older = harness.add_render(source_id, semitones=3)
        newer = harness.add_render(source_id, semitones=1)
        with user:
            workspace_page.poll()
        await asyncio.sleep(0.05)
        user.find(marker=f"track-row-{track_id}").click()
        await asyncio.sleep(0.05)

        # Original is active, so the B shortcut names +1 but is not highlighted.
        assert _only(user, "ab-preview").text == "+1"
        assert "is-active" not in _only(user, "ab-preview").classes

        # A non-latest set leaves the B shortcut labelled but inactive.
        user.find(marker=f"output-tab-{older}").click()
        await asyncio.sleep(0.05)
        assert _only(user, "ab-preview").text == "+1"
        assert "is-active" not in _only(user, "ab-preview").classes
        assert "is-active" in _only(user, f"output-tab-{older}").classes

        # Only the latest render lights the shortcut.
        user.find(marker=f"output-tab-{newer}").click()
        await asyncio.sleep(0.05)
        assert "is-active" in _only(user, "ab-preview").classes
        assert "is-active" in _only(user, f"output-tab-{newer}").classes


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
        # Ticket 14 §4.4: the row enters the unavailable state with a repair.
        assert _enabled(user, "output-play-original") is False
        assert _enabled(user, "output-export-original") is False
        await user.should_see(marker="output-repair-import")
        assert _enabled(user, "transport-play") is False
        assert _enabled(user, f"track-play-{track_id}") is False


async def test_a_playback_failure_falls_back_to_the_original_version(
    harness: Harness,
) -> None:
    """Ticket 14 §4.8: the tab falls back to Original or the next existing set."""
    async for user, workspace_page in _open_page(harness):
        (track_id,) = await _import(harness, user, workspace_page, "Gone.wav")
        await asyncio.sleep(0.05)
        source_id = harness.service.snapshot().tracks[0].source_id
        render_key = harness.add_render(source_id, semitones=3)
        user.find(marker=f"track-row-{track_id}").click()
        await asyncio.sleep(0.05)

        user.find(marker=f"output-tab-{render_key}").click()
        await asyncio.sleep(0.05)
        user.find(marker="output-play-master").click()
        original_url = harness.service.preview_targets(track_id)[0].url
        render_url = f"/media/renders/{render_key}/original_preview.mp3"
        assert _player(user).props["src"] == render_url

        with user:
            workspace_page._on_player_error()
        await asyncio.sleep(0.05)

        # The failed render stays available behind a repair, but the view and
        # the single player now point at the working Original set.
        assert _player(user).props["src"] == original_url
        assert _only(user, "now-playing").text == "Original"
        assert "is-active" in _only(user, "output-tab-original").classes
        assert "is-active" not in _only(user, f"output-tab-{render_key}").classes


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

            # Other capabilities stay usable (ticket 14 §3). The Pitch card is
            # usable even though its Render action is disabled at 0 by design;
            # Separation shows its inline model gate because no weights are
            # installed in this harness.
            assert _enabled(user, "add-audio") is True
            assert _enabled(user, "model-download") is True
            assert _only(user, "pitch-semitones").text == "0"
            assert _enabled(user, "pitch-up") is True
            assert _enabled(user, "pitch-render") is False
            user.find(marker="pitch-up").click()
            await asyncio.sleep(0.05)
            assert _only(user, "pitch-semitones").text == "+1"
            assert _enabled(user, "pitch-render") is True
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
        # Ask for a semitone with no cached set so a Render action exists.
        for _ in range(4):
            user.find(marker="pitch-up").click()
        await asyncio.sleep(0.05)
        assert _enabled(user, "pitch-render") is True

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
        assert _enabled(user, "model-download") is False
        assert _enabled(user, "pitch-render") is False
        assert _enabled(user, "export-action") is False

        gate.set()
        await _finish_jobs(harness, user, workspace_page, expected_jobs=2)
        assert _enabled(user, "add-audio") is True
        await user.should_see("Later")


async def test_resource_warnings_are_a_dismissible_banner_and_never_block(
    tmp_path: Path,
) -> None:
    report = preflight.PreflightReport(
        checks=(
            preflight.CheckResult(
                name=preflight.CHECK_RESOURCES,
                status=preflight.STATUS_WARN,
                message=(
                    "This computer has 4.0 GB of RAM. Separation needs about 3 GB free; "
                    "long tracks may be slow."
                ),
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
            await user.should_see(marker="resource-banner")
            await user.should_see("This computer has 4.0 GB of RAM.")
            assert _enabled(user, "add-audio") is True

            user.find(marker="resource-dismiss").click()
            await asyncio.sleep(0.05)
            await user.should_not_see(marker="resource-banner")

            (track_id,) = await _import(harness, user, workspace_page, "Still.wav")
            assert track_id
            assert _enabled(user, "add-audio") is True
    finally:
        harness.runner.stop(timeout=10)


async def test_at_most_two_banners_stack_with_degraded_first(tmp_path: Path) -> None:
    report = preflight.PreflightReport(
        checks=(
            preflight.CheckResult(
                name=preflight.CHECK_PREVIEW,
                status=preflight.STATUS_DEGRADED,
                message="GStreamer is missing mpg123audiodec.",
                fatal=False,
            ),
            preflight.CheckResult(
                name=preflight.CHECK_RESOURCES,
                status=preflight.STATUS_WARN,
                message="This computer has 4.0 GB of RAM.",
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
    harness.context.show_recovery = True
    harness.runner.start()
    try:
        async for user, _workspace_page in _open_page(harness):
            await user.should_see(marker="playback-banner")
            await user.should_see(marker="resource-banner")
            await user.should_not_see(marker="recovery-banner")
    finally:
        harness.runner.stop(timeout=10)


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


async def test_the_gain_badge_is_shown_only_when_the_set_was_attenuated(
    tmp_path: Path,
) -> None:
    harness = Harness(
        root=tmp_path,
        media=FakeMedia(peak_dbfs=1.0),
        dialog=FakeDialog(),
    )
    harness.runner.start()
    try:
        async for user, workspace_page in _open_page(harness):
            (track_id,) = await _import(harness, user, workspace_page, "Loud.wav")
            await asyncio.sleep(0.05)
            user.find(marker=f"track-row-{track_id}").click()
            await asyncio.sleep(0.05)

            badge = _only(user, "level-adjusted")
            assert badge.text == "level adjusted \u22122.0 dB"
    finally:
        harness.runner.stop(timeout=10)


async def test_ab_switches_between_previews_that_carry_their_own_gain(
    harness: Harness,
) -> None:
    async for user, workspace_page in _open_page(harness):
        (track_id,) = await _import(harness, user, workspace_page, "Level.wav")
        await asyncio.sleep(0.05)
        user.find(marker=f"track-row-{track_id}").click()
        await asyncio.sleep(0.05)

        source_id = harness.service.snapshot().tracks[0].source_id
        render_key = harness.add_render(source_id, semitones=3, gain_db=-5.0)
        with user:
            workspace_page.poll()
        await asyncio.sleep(0.05)
        user.find(marker=f"track-row-{track_id}").click()
        await asyncio.sleep(0.05)

        # Each preview MP3 is encoded with its set's presentation gain, so A/B
        # switches sources and the player adds no second attenuation.
        user.find(marker="ab-preview").click()
        await asyncio.sleep(0.05)
        assert _player(user).props["src"] == f"/media/renders/{render_key}/original_preview.mp3"
        assert workspace_page._loaded_preview is not None
        assert workspace_page._loaded_preview.gain_db == -5.0

        user.find(marker="ab-original").click()
        await asyncio.sleep(0.05)
        assert workspace_page._loaded_preview is not None
        assert workspace_page._loaded_preview.gain_db == 0.0
