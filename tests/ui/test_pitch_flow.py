"""Headless Pitch-card flow: request stepper, version tabs, A/B, render errors.

Everything runs through NiceGUI's user simulation. The pitch adapter is a fake
that writes tiny stand-ins and never spawns Rubber Band; the media adapter is a
fake that never spawns FFmpeg. Every write stays under ``tmp_path``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
from collections.abc import Iterator, Mapping, Sequence
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
from stemforge.errors import StemForgeError
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


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


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

    def measure_levels(
        self, path: Path, *, cancel: CancelToken, log: logging.Logger
    ) -> LevelMeasurement:
        cancel.check()
        return LevelMeasurement(peak_dbfs=-3.0, side_peak_dbfs=-6.0)


class FakePitch:
    """A scripted ``PitchEngine``: no Rubber Band, no subprocess, no audio codec."""

    version = "4.0.0-test"

    def __init__(self) -> None:
        self.fail_next = False
        self.fail_code = errors.PITCH_RENDER_FAILED
        self.fail_retryable = True
        self._gate: threading.Event | None = None
        self.shift_started = threading.Event()
        self.sum_calls = 0

    def block(self) -> threading.Event:
        self._gate = threading.Event()
        return self._gate

    def shift(
        self,
        src: Path,
        dst: Path,
        *,
        semitones: int,
        cancel: CancelToken,
        log: logging.Logger,
        progress: object | None = None,
    ) -> None:
        cancel.check()
        self.shift_started.set()
        if self._gate is not None:
            self._gate.wait(timeout=10)
        cancel.check()
        if self.fail_next:
            self.fail_next = False
            raise StemForgeError(
                self.fail_code,
                "fake render failure",
                retryable=self.fail_retryable,
                detail="rubberband exited 1",
            )
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"shift:" + bytes([semitones % 256]) + src.read_bytes())

    def sum_stems(
        self,
        srcs: Sequence[Path],
        dst: Path,
        *,
        cancel: CancelToken,
        log: logging.Logger,
    ) -> None:
        cancel.check()
        self.sum_calls += 1
        dst.write_bytes(b"sum:" + b"|".join(path.read_bytes() for path in srcs))

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


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _file_entry(name: str, role: str, data: bytes) -> Mapping[str, object]:
    return {
        "path": name,
        "role": role,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


@dataclass
class Harness:
    root: Path
    media: FakeMedia
    pitch: FakePitch
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
            engines=ServiceEngines(media=self.media, pitch=self.pitch),
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

    def add_separation(
        self,
        source_id: str,
        *,
        version: str = "v1",
        method: str = "demucs",
    ) -> dict[str, str]:
        """Commit one method's set and return its role -> sha256 map."""
        roles = (
            ("vocals", "instrumental")
            if method == "karaoke"
            else ("vocals", "drums", "bass", "other")
        )
        staging = self.artifacts.stage(f"ui-separation-{method}-{version}")
        hashes: dict[str, str] = {}
        files: list[Mapping[str, object]] = []
        for role in roles:
            data = f"{version}-{method}-{role}-audio".encode()
            (staging / f"{role}.wav").write_bytes(data)
            hashes[role] = hashlib.sha256(data).hexdigest()
            files.append(_file_entry(f"{role}.wav", role, data))
            preview = f"{version}-{role}-preview".encode()
            (staging / f"{role}_preview.mp3").write_bytes(preview)
            files.append(_file_entry(f"{role}_preview.mp3", f"{role}_preview", preview))
        manifest = {
            "schema_version": 1,
            "kind": "separation",
            "source_id": source_id,
            "files": files,
        }
        self.artifacts.commit_separation(staging, source_id, manifest, method=method)
        return hashes

    def add_render(
        self,
        source_id: str,
        *,
        semitones: int = 3,
        scope: str = "original",
        inputs: Mapping[str, str] | None = None,
        tag: str | None = None,
        method: str | None = None,
    ) -> str:
        key = f"ui-render-{scope}-{semitones:+d}".replace("+", "p").replace("-", "m")
        if tag is not None:
            key += f"-{tag}"
        staging = self.artifacts.stage(f"ui-render-job-{key}")
        files: list[Mapping[str, object]] = []
        if scope == "original":
            data = f"render-master-{semitones}-{tag}".encode()
            (staging / "original.wav").write_bytes(data)
            files.append(_file_entry("original.wav", "original", data))
            preview = f"render-master-preview-{semitones}-{tag}".encode()
            (staging / "original_preview.mp3").write_bytes(preview)
            files.append(_file_entry("original_preview.mp3", "original_preview", preview))
        else:
            roles = (
                ("vocals", "instrumental")
                if method == "karaoke"
                else ("vocals", "drums", "bass", "other", "instrumental")
            )
            for role in roles:
                data = f"render-stems-{semitones}-{role}-{tag}".encode()
                (staging / f"{role}.wav").write_bytes(data)
                files.append(_file_entry(f"{role}.wav", role, data))
                preview = f"render-stems-preview-{semitones}-{role}-{tag}".encode()
                (staging / f"{role}_preview.mp3").write_bytes(preview)
                files.append(_file_entry(f"{role}_preview.mp3", f"{role}_preview", preview))
        manifest: dict[str, object] = {
            "schema_version": 1,
            "kind": "render",
            "render_key": key,
            "source_id": source_id,
            "scope": scope,
            "semitones": semitones,
            "gain_db": 0.0,
            "files": files,
        }
        if inputs is not None:
            manifest["inputs"] = [
                {
                    "role": role,
                    "artifact": f"separations/{method or 'demucs'}/{role}.wav",
                    "sha256": digest,
                }
                for role, digest in inputs.items()
            ]
        if method is not None:
            manifest["separation_method"] = method
            manifest["separation_method_version"] = 1
        self.artifacts.commit_render(staging, key, manifest)
        return key


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[Harness]:
    item = Harness(
        root=tmp_path,
        media=FakeMedia(),
        pitch=FakePitch(),
        dialog=FakeDialog(),
    )
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
    await asyncio.sleep(0.05)


async def _import(
    harness: Harness,
    user,
    workspace_page: page.WorkspacePage,
    name: str = "Song.wav",
) -> str:
    harness.dialog._selections = [(str(harness.source_file(name)),)]
    user.find(marker="add-audio").click()
    await asyncio.sleep(0.05)
    await _finish_jobs(harness, user, workspace_page, expected_jobs=1)
    track = harness.service.snapshot().tracks[0]
    user.find(marker=f"track-row-{track.track_id}").click()
    await asyncio.sleep(0.05)
    return track.track_id


def _only(user, marker: str):
    elements = list(user.find(marker=marker).elements)
    assert len(elements) == 1, f"expected exactly one {marker}, found {len(elements)}"
    return elements[0]


def _enabled(user, marker: str) -> bool:
    return bool(_only(user, marker).enabled)


def _count(user, marker: str) -> int:
    try:
        return len(user.find(marker=marker).elements)
    except AssertionError:
        return 0


def _step(user, times: int, *, up: bool = True) -> None:
    for _ in range(times):
        user.find(marker="pitch-up" if up else "pitch-down").click()


async def _refresh(harness: Harness, user, workspace_page: page.WorkspacePage) -> None:
    with user:
        workspace_page.poll()
        workspace_page._refresh_all()
    await asyncio.sleep(0.05)


# ---------------------------------------------------------------------------
# Request vs view
# ---------------------------------------------------------------------------


async def test_stepper_does_not_change_the_view_until_render_succeeds(
    harness: Harness,
) -> None:
    async for user, workspace_page in _open_page(harness):
        await _import(harness, user, workspace_page)

        player = _only(user, "player")
        assert player.props["src"] == ""
        assert _only(user, "now-playing").text == "Original"
        assert _count(user, "output-tab") == 0

        _step(user, 3)
        await asyncio.sleep(0.05)

        assert _only(user, "pitch-semitones").text == "+3"
        assert _only(user, "now-playing").text == "Original"
        assert player.props["src"] == ""
        assert _count(user, "output-tab") == 0

        gate = harness.pitch.block()
        user.find(marker="pitch-render").click()
        assert await asyncio.to_thread(harness.pitch.shift_started.wait, 5)
        with user:
            workspace_page.poll()
        assert _only(user, "now-playing").text == "Original"
        assert _enabled(user, "pitch-render") is False

        gate.set()
        await _finish_jobs(harness, user, workspace_page, expected_jobs=2)

        assert _only(user, "now-playing").text == "Shifted master · +3"
        track = harness.service.snapshot().tracks[0]
        key = harness.service.pitch_sets(track)[0].render_key
        assert _only(user, f"output-tab-{key}").text == "+3"
        target = harness.service.pitch_render_preview_target(track, key, "original")
        assert target is not None
        assert player.props["src"] == target.url
        assert len(user.find(marker="player").elements) == 1


async def test_render_is_disabled_at_zero_and_while_a_job_runs(harness: Harness) -> None:
    async for user, workspace_page in _open_page(harness):
        await _import(harness, user, workspace_page)

        assert _only(user, "pitch-semitones").text == "0"
        assert _enabled(user, "pitch-render") is False
        # The stepper and the primary action share the compact box;
        # the disabled Render keeps the primary size.
        assert "sf-control" in _only(user, "pitch-down").classes
        assert "sf-control--icon" in _only(user, "pitch-down").classes
        assert "sf-control" in _only(user, "pitch-up").classes
        assert "sf-control--icon" in _only(user, "pitch-up").classes
        assert "sf-control" in _only(user, "pitch-render").classes
        assert "sf-control--primary" in _only(user, "pitch-render").classes

        _step(user, 2)
        await asyncio.sleep(0.05)
        assert _only(user, "pitch-semitones").text == "+2"
        assert _enabled(user, "pitch-render") is True

        gate = harness.pitch.block()
        user.find(marker="pitch-render").click()
        assert await asyncio.to_thread(harness.pitch.shift_started.wait, 5)
        with user:
            workspace_page.poll()

        assert _enabled(user, "pitch-render") is False
        await user.should_see(marker="job-bar")
        await user.should_see("Rendering")

        gate.set()
        await _finish_jobs(harness, user, workspace_page, expected_jobs=2)

        # The successful set is now the active view, so the action is passive.
        await user.should_see(marker="pitch-passive")
        await user.should_not_see(marker="pitch-render")

        _step(user, 1)
        await asyncio.sleep(0.05)
        assert _only(user, "pitch-semitones").text == "+3"
        assert _enabled(user, "pitch-render") is True


async def test_a_cached_set_offers_a_compact_open_action(harness: Harness) -> None:
    async for user, workspace_page in _open_page(harness):
        await _import(harness, user, workspace_page)
        source_id = harness.service.snapshot().tracks[0].source_id
        harness.add_render(source_id, semitones=3, scope="original")
        await _refresh(harness, user, workspace_page)

        _step(user, 3)
        await asyncio.sleep(0.05)
        action = _only(user, "pitch-open")
        # Open is navigation in the primary slot, so it keeps the
        # compact box rather than the Render's primary size.
        assert "sf-control" in action.classes
        assert "sf-control--primary" not in action.classes


# ---------------------------------------------------------------------------
# Tabs, scopes, A/B
# ---------------------------------------------------------------------------


async def test_a_single_scope_shows_a_bare_tab_and_both_scopes_stay_distinct(
    harness: Harness,
) -> None:
    async for user, workspace_page in _open_page(harness):
        await _import(harness, user, workspace_page)
        source_id = harness.service.snapshot().tracks[0].source_id
        master = harness.add_render(source_id, semitones=3, scope="original")
        await _refresh(harness, user, workspace_page)

        assert _only(user, f"output-tab-{master}").text == "+3"
        assert len(user.find(marker="output-tab").elements) == 2

        harness.add_separation(source_id)
        stems = harness.add_render(source_id, semitones=3, scope="stems")
        await _refresh(harness, user, workspace_page)

        assert _only(user, f"output-tab-{master}").text == "+3 master"
        assert _only(user, f"output-tab-{stems}").text == "+3 stems"
        assert len(user.find(marker="output-tab").elements) == 3

        player = _only(user, "player")
        track = harness.service.snapshot().tracks[0]
        user.find(marker=f"output-tab-{master}").click()
        await asyncio.sleep(0.05)
        master_target = harness.service.pitch_render_preview_target(track, master, "original")
        assert master_target is not None
        assert player.props["src"] == master_target.url

        user.find(marker=f"output-tab-{stems}").click()
        await asyncio.sleep(0.05)
        stems_target = harness.service.pitch_render_preview_target(track, stems, "vocals")
        assert stems_target is not None
        assert stems_target.url.endswith("/vocals_preview.mp3")
        assert player.props["src"] == stems_target.url
        assert player.props["src"] != master_target.url
        assert len(user.find(marker="player").elements) == 1


async def test_ab_flips_original_and_the_selected_set_without_a_second_player(
    harness: Harness,
) -> None:
    async for user, workspace_page in _open_page(harness):
        await _import(harness, user, workspace_page)
        source_id = harness.service.snapshot().tracks[0].source_id
        harness.add_separation(source_id)
        master = harness.add_render(source_id, semitones=3, scope="original")
        stems = harness.add_render(source_id, semitones=3, scope="stems")
        await _refresh(harness, user, workspace_page)

        track = harness.service.snapshot().tracks[0]
        original_target = harness.service.preview_targets(track.track_id)[0]
        master_target = harness.service.pitch_render_preview_target(track, master, "original")
        stems_target = harness.service.pitch_render_preview_target(track, stems, "vocals")
        assert master_target is not None
        assert stems_target is not None
        player = _only(user, "player")

        # A/B is Original | the latest render, regardless of the active tab.
        assert _only(user, "ab-preview").text == "+3 stems"
        user.find(marker=f"output-tab-{stems}").click()
        await asyncio.sleep(0.05)
        assert _only(user, "ab-preview").text == "+3 stems"
        assert player.props["src"] == stems_target.url
        assert len(user.find(marker="player").elements) == 1

        user.find(marker="ab-original").click()
        await asyncio.sleep(0.05)
        assert player.props["src"] == original_target.url
        assert len(user.find(marker="player").elements) == 1

        user.find(marker="ab-preview").click()
        await asyncio.sleep(0.05)
        assert player.props["src"] == stems_target.url
        assert len(user.find(marker="player").elements) == 1

        user.find(marker=f"output-tab-{master}").click()
        await asyncio.sleep(0.05)
        assert player.props["src"] == master_target.url
        assert _only(user, "ab-preview").text == "+3 stems"
        user.find(marker="ab-original").click()
        await asyncio.sleep(0.05)
        assert player.props["src"] == original_target.url
        user.find(marker="ab-preview").click()
        await asyncio.sleep(0.05)
        assert player.props["src"] == stems_target.url
        assert len(user.find(marker="player").elements) == 1


async def test_the_stems_scope_is_offered_only_after_a_separation_and_renders_stems(
    harness: Harness,
) -> None:
    async for user, workspace_page in _open_page(harness):
        await _import(harness, user, workspace_page)
        source_id = harness.service.snapshot().tracks[0].source_id

        # The primary action follows the current scope: master before
        # Separation, stems after it. There is no separate stems button.
        await user.should_not_see(marker="pitch-stems")
        assert _enabled(user, "pitch-render") is False

        harness.add_separation(source_id)
        await _refresh(harness, user, workspace_page)
        assert _enabled(user, "pitch-render") is False

        _step(user, 3)
        await asyncio.sleep(0.05)
        assert _enabled(user, "pitch-render") is True
        user.find(marker="pitch-render").click()
        await _finish_jobs(harness, user, workspace_page, expected_jobs=2)
        await asyncio.sleep(0.05)

        sets = harness.service.pitch_sets(harness.service.snapshot().tracks[0])
        assert [item.scope.value for item in sets] == ["stems"]
        assert _only(user, "now-playing").text == "Vocals · +3"
        jobs = harness.runner.jobs()
        assert sum(1 for job in jobs if job.kind.value == "render_pitch") == 1


async def test_a_karaoke_stems_render_shifts_two_outputs_and_shows_its_rows(
    harness: Harness,
) -> None:
    async for user, workspace_page in _open_page(harness):
        await _import(harness, user, workspace_page)
        source_id = harness.service.snapshot().tracks[0].source_id
        harness.add_separation(source_id, method="karaoke")
        await _refresh(harness, user, workspace_page)

        await user.should_see(marker="pitch-karaoke-fineprint")
        assert _only(user, "pitch-karaoke-fineprint").text == "Renders Vocals + Instrumental."

        _step(user, 3)
        await asyncio.sleep(0.05)
        user.find(marker="pitch-render").click()
        await _finish_jobs(harness, user, workspace_page, expected_jobs=2)
        await asyncio.sleep(0.05)

        track = harness.service.snapshot().tracks[0]
        (item,) = harness.service.pitch_sets(track)
        assert item.method == "karaoke"
        assert item.output_roles == ("vocals", "instrumental")
        assert item.label == "+3"
        assert harness.pitch.sum_calls == 0
        artifact = harness.artifacts.find_render(item.render_key)
        assert artifact is not None
        assert artifact.manifest["separation_method"] == "karaoke"
        assert isinstance(artifact.manifest["separation_set_key"], str)
        instrumental = artifact.path_for("instrumental")
        assert instrumental is not None
        assert instrumental.read_bytes().startswith(b"shift:")

        assert _only(user, "now-playing").text == "Vocals · +3"
        await user.should_see(marker="output-row-vocals")
        await user.should_see(marker="output-row-instrumental")
        await user.should_not_see(marker="output-row-drums")
        await user.should_not_see(marker="outputs-show-more")
        await user.should_not_see("Show 3 more stems")
        await user.should_not_see("sum")


async def test_version_tabs_qualify_the_method_only_on_a_collision(harness: Harness) -> None:
    async for user, workspace_page in _open_page(harness):
        await _import(harness, user, workspace_page)
        source_id = harness.service.snapshot().tracks[0].source_id
        harness.add_separation(source_id)
        harness.add_separation(source_id, method="karaoke")
        demucs = harness.add_render(
            source_id, semitones=3, scope="stems", tag="demucs", method="demucs"
        )
        karaoke = harness.add_render(
            source_id, semitones=3, scope="stems", tag="karaoke", method="karaoke"
        )
        await _refresh(harness, user, workspace_page)

        assert _only(user, f"output-tab-{demucs}").text == "+3 · Demucs"
        assert _only(user, f"output-tab-{karaoke}").text == "+3 · Karaoke"

        lone = harness.add_render(
            source_id, semitones=4, scope="stems", tag="karaoke", method="karaoke"
        )
        await _refresh(harness, user, workspace_page)

        assert _only(user, f"output-tab-{lone}").text == "+4"


async def test_switching_to_demucs_keeps_the_karaoke_render_valid(
    harness: Harness,
) -> None:
    async for user, workspace_page in _open_page(harness):
        await _import(harness, user, workspace_page)
        source_id = harness.service.snapshot().tracks[0].source_id
        harness.add_separation(source_id, method="karaoke")
        karaoke_hashes = harness.add_separation(source_id, method="karaoke", version="v2")
        karaoke = harness.add_render(
            source_id,
            semitones=3,
            scope="stems",
            inputs=karaoke_hashes,
            method="karaoke",
        )
        await _refresh(harness, user, workspace_page)
        assert _only(user, f"output-tab-{karaoke}").text == "+3"

        harness.add_separation(source_id, method="demucs")
        await _refresh(harness, user, workspace_page)
        user.find(marker="separation-method-demucs").click()
        await asyncio.sleep(0.05)

        await user.should_not_see(marker="pitch-karaoke-fineprint")
        track = harness.service.snapshot().tracks[0]
        (item,) = harness.service.pitch_sets(track)
        assert item.render_key == karaoke
        assert item.based_on_earlier_separation is False
        assert "based on an earlier separation" not in _only(user, f"output-tab-{karaoke}").text


# ---------------------------------------------------------------------------
# Errors and retry
# ---------------------------------------------------------------------------


async def test_a_failed_render_is_inline_coded_and_retry_resubmits_the_request(
    harness: Harness,
) -> None:
    async for user, workspace_page in _open_page(harness):
        await _import(harness, user, workspace_page)
        _step(user, 3)
        await asyncio.sleep(0.05)

        harness.pitch.fail_next = True
        user.find(marker="pitch-render").click()
        await _finish_jobs(harness, user, workspace_page, expected_jobs=2)

        await user.should_see(marker="pitch-error")
        assert "pitch.render_failed" in _only(user, "pitch-error-code").text
        await user.should_see("The pitch render failed.")
        assert _only(user, "pitch-semitones").text == "+3"
        assert _only(user, "now-playing").text == "Original"
        assert _count(user, "output-tab") == 0
        assert _enabled(user, "pitch-retry") is True

        user.find(marker="pitch-retry").click()
        await _finish_jobs(harness, user, workspace_page, expected_jobs=3)
        await asyncio.sleep(0.05)

        await user.should_not_see(marker="pitch-error")
        assert _only(user, "now-playing").text == "Shifted master · +3"
        assert _only(user, "pitch-semitones").text == "+3"
        jobs = harness.runner.jobs()
        assert [job.kind.value for job in jobs if job.kind.value == "render_pitch"] == [
            "render_pitch",
            "render_pitch",
        ]


async def test_a_non_retryable_render_failure_has_no_retry(harness: Harness) -> None:
    """Retry appears iff the raising code marks itself retryable."""
    async for user, workspace_page in _open_page(harness):
        await _import(harness, user, workspace_page)
        _step(user, 3)
        await asyncio.sleep(0.05)

        harness.pitch.fail_next = True
        harness.pitch.fail_code = errors.PITCH_ENGINE_MISSING
        harness.pitch.fail_retryable = False
        user.find(marker="pitch-render").click()
        await _finish_jobs(harness, user, workspace_page, expected_jobs=2)

        await user.should_see(marker="pitch-error")
        await user.should_see("The pitch-shift engine is missing.")
        await user.should_not_see(marker="pitch-retry")


# ---------------------------------------------------------------------------
# Re-separation and degraded preview
# ---------------------------------------------------------------------------


async def test_old_stems_sets_stay_selectable_and_labelled_after_a_reseparation(
    harness: Harness,
) -> None:
    async for user, workspace_page in _open_page(harness):
        await _import(harness, user, workspace_page)
        source_id = harness.service.snapshot().tracks[0].source_id
        first = harness.add_separation(source_id, version="v1")
        old = harness.add_render(source_id, semitones=3, scope="stems", inputs=first, tag="old")
        await _refresh(harness, user, workspace_page)
        assert _only(user, f"output-tab-{old}").text == "+3"

        second = harness.add_separation(source_id, version="v2")
        await _refresh(harness, user, workspace_page)
        old_tab = _only(user, f"output-tab-{old}")
        assert "based on an earlier separation" in old_tab.text
        # Every version pill keeps the shared box in stale state.
        assert "sf-control" in old_tab.classes
        assert "is-stale" in old_tab.classes
        assert "sf-control" in _only(user, "output-tab-original").classes
        assert len(user.find(marker="output-tab").elements) == 2

        new = harness.add_render(source_id, semitones=3, scope="stems", inputs=second, tag="new")
        await _refresh(harness, user, workspace_page)

        assert "based on an earlier separation" in _only(user, f"output-tab-{old}").text
        assert "based on an earlier separation" not in _only(user, f"output-tab-{new}").text
        assert "sf-control" in _only(user, f"output-tab-{new}").classes
        assert len(user.find(marker="output-tab").elements) == 3

        track = harness.service.snapshot().tracks[0]
        old_target = harness.service.pitch_render_preview_target(track, old, "vocals")
        new_target = harness.service.pitch_render_preview_target(track, new, "vocals")
        assert old_target is not None
        assert new_target is not None
        player = _only(user, "player")

        user.find(marker=f"output-tab-{old}").click()
        await asyncio.sleep(0.05)
        assert player.props["src"] == old_target.url
        assert "sf-control" in _only(user, f"output-tab-{old}").classes
        assert "is-stale" in _only(user, f"output-tab-{old}").classes
        assert len(user.find(marker="player").elements) == 1

        user.find(marker=f"output-tab-{new}").click()
        await asyncio.sleep(0.05)
        assert player.props["src"] == new_target.url
        assert player.props["src"] != old_target.url
        assert len(user.find(marker="player").elements) == 1


async def test_an_unavailable_render_row_offers_render_again(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async for user, workspace_page in _open_page(harness):
        await _import(harness, user, workspace_page)
        source_id = harness.service.snapshot().tracks[0].source_id
        render_key = harness.add_render(source_id, semitones=3)
        await _refresh(harness, user, workspace_page)

        user.find(marker=f"output-tab-{render_key}").click()
        await asyncio.sleep(0.05)
        monkeypatch.setattr(
            harness.service,
            "pitch_render_preview_target",
            lambda *_args, **_kwargs: None,
        )
        await _refresh(harness, user, workspace_page)

        assert _enabled(user, "output-play-master") is False
        await user.should_see(marker="output-repair-render")
        await user.should_not_see(marker="output-repair-import")


async def test_degraded_preview_keeps_the_pitch_card_usable_but_silent(
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
        pitch=FakePitch(),
        dialog=FakeDialog(),
        preflight_report=report,
    )
    harness.runner.start()
    try:
        async for user, workspace_page in _open_page(harness):
            await _import(harness, user, workspace_page)
            await user.should_see(marker="playback-banner")

            _step(user, 3)
            await asyncio.sleep(0.05)
            assert _only(user, "pitch-semitones").text == "+3"
            assert _enabled(user, "pitch-render") is True

            user.find(marker="pitch-render").click()
            await _finish_jobs(harness, user, workspace_page, expected_jobs=2)
            await asyncio.sleep(0.05)

            assert _only(user, "now-playing").text == "Shifted master · +3"
            key = harness.service.pitch_sets(harness.service.snapshot().tracks[0])[0].render_key
            assert len(user.find(marker=f"output-tab-{key}").elements) == 1
            player = _only(user, "player")
            assert player.props["src"] == ""

            user.find(marker=f"output-tab-{key}").click()
            await asyncio.sleep(0.05)
            user.find(marker="output-tab-original").click()
            await asyncio.sleep(0.05)
            assert player.props["src"] == ""
            assert len(user.find(marker="player").elements) == 1
            assert _enabled(user, "ab-original") is False
            assert _enabled(user, "ab-preview") is False
    finally:
        harness.runner.stop(timeout=10)
