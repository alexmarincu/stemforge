"""Headless Separation-card flow: model gate, download, Jobs, and Outputs rows.

Everything runs through NiceGUI's user simulation. The separation adapter is a
fake that writes tiny stand-ins and never spawns Demucs; the media adapter is a
fake that never spawns FFmpeg; the model transport reads bytes from memory. Every
write stays under ``tmp_path`` and no test touches the network.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
import urllib.error
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from nicegui.testing.user_simulation import user_simulation

from stemforge import errors
from stemforge.application import ServiceEngines, WorkspaceService
from stemforge.artifacts import ArtifactStore
from stemforge.domain.cancellation import CancelToken
from stemforge.engines.media import CanonicalMetadata, DecodeResult, MediaInfo
from stemforge.engines.separation import SeparationOutputs
from stemforge.errors import StemForgeError
from stemforge.integration.dialogs import normalize_selected_paths
from stemforge.jobs.runner import JobRunner
from stemforge.models import ModelSpec, ModelStore
from stemforge.platform.paths import AppPaths
from stemforge.settings import SettingsStore
from stemforge.ui import page

pytestmark = pytest.mark.ui

SAMPLE_RATE = 44_100
CHANNELS = 2
DURATION_S = 42.0
PAYLOAD = bytes(range(256)) * 256
PAYLOAD_SHA256 = hashlib.sha256(PAYLOAD).hexdigest()


def _tiny_spec() -> ModelSpec:
    return ModelSpec(
        name="htdemucs",
        repo_id="example/HTDemucs",
        revision="test-revision",
        filename="test-weights.safetensors",
        size_bytes=len(PAYLOAD),
        sha256=PAYLOAD_SHA256,
        download_url="https://example.invalid/models/test-weights.safetensors",
        source_url="https://example.invalid/models/test-weights.safetensors",
        licence="test licence",
        licence_notice="test notice",
        licence_notice_version=1,
    )


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeMedia:
    """A scripted ``MediaTool`` that writes tiny stand-ins, never real audio."""

    def __init__(self) -> None:
        self.duration_s = DURATION_S

    def probe(self, path: Path) -> MediaInfo:
        return MediaInfo(
            codec="pcm_f32le",
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

    def sum_wavs(
        self,
        srcs: Sequence[Path],
        dst: Path,
        *,
        cancel: CancelToken,
        log: logging.Logger,
    ) -> None:
        cancel.check()
        dst.write_bytes(b"sum:" + b"|".join(path.read_bytes() for path in srcs))

    def peak_dbfs(self, path: Path, *, cancel: CancelToken, log: logging.Logger) -> float:
        cancel.check()
        return -3.0


class FakeSeparation:
    """A scripted SeparationEngine: no worker, torch, or real audio codec."""

    version = "4.1.0-test"
    block_seconds = 480.0
    crossfade_seconds = 0.5
    overlap = 0.25
    shifts = 1

    def __init__(self) -> None:
        self.fail_code: str | None = None
        self.retryable = True
        self._gate: threading.Event | None = None
        self.started = threading.Event()

    def block(self) -> threading.Event:
        self._gate = threading.Event()
        return self._gate

    def separate(
        self,
        canonical: Path,
        out_dir: Path,
        *,
        model_path: Path,
        expected_duration_s: float,
        progress: object,
        cancel: CancelToken,
        log: logging.Logger,
        threads: int | None = None,
        model_revision: str = "",
        env: Mapping[str, str] | None = None,
        timeout_s: float | None = None,
        on_spawn: object | None = None,
    ) -> SeparationOutputs:
        del model_path, threads, model_revision, env, timeout_s, on_spawn
        cancel.check()
        emit = progress
        assert callable(emit)
        self.started.set()
        emit(0.1, "Separate", "starting")
        if self._gate is not None:
            self._gate.wait(timeout=10)
        cancel.check()
        if self.fail_code is not None:
            raise StemForgeError(
                self.fail_code,
                "fake separation failure",
                retryable=self.retryable,
                detail="reason=fake",
            )
        stems: dict[str, Path] = {}
        for index, role in enumerate(("vocals", "drums", "bass", "other", "instrumental")):
            path = out_dir / f"{role}.wav"
            path.write_bytes(f"stem-{role}-{expected_duration_s}".encode())
            stems[role] = path
            emit(0.2 + 0.2 * index, "Separate", role)
        return SeparationOutputs(outputs=stems)


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


class MemoryResponse:
    """A binary response backed by bytes in memory, optionally gated."""

    def __init__(
        self,
        data: bytes,
        *,
        gate: threading.Event | None = None,
        chunk_size: int = 1024,
        gate_after: int = 1,
    ) -> None:
        self._data = data
        self._offset = 0
        self._chunk_size = chunk_size
        self._gate = gate
        self._gate_after = gate_after
        self._reads = 0
        self.read_started = threading.Event()
        self.blocked = threading.Event()
        self.headers: Mapping[str, str] = {"Content-Length": str(len(data))}
        self.status = 200
        self.closed = False

    def read(self, size: int = -1, /) -> bytes:
        self._reads += 1
        if self._reads == 1:
            self.read_started.set()
        if self._reads == self._gate_after and self._gate is not None:
            self.blocked.set()
            self._gate.wait(timeout=10)
        limit = self._chunk_size if size < 0 else min(size, self._chunk_size)
        chunk = self._data[self._offset : self._offset + limit]
        self._offset += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


class ScriptedTransport:
    """A callable transport returning scripted responses or raising errors."""

    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    def __call__(self, url: str, headers: Mapping[str, str] | None = None) -> MemoryResponse:
        assert url
        del headers
        self.calls += 1
        outcome = self.outcomes[min(self.calls, len(self.outcomes)) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, MemoryResponse)
        return outcome


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


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
    separation: FakeSeparation
    dialog: FakeDialog
    transport: ScriptedTransport
    tiny_model: bool = True
    install_model: bool = False
    paths: AppPaths = field(init=False)
    settings: SettingsStore = field(init=False)
    artifacts: ArtifactStore = field(init=False)
    runner: JobRunner = field(init=False)
    models: ModelStore = field(init=False)
    service: WorkspaceService = field(init=False)
    context: page.UiContext = field(init=False)

    def __post_init__(self) -> None:
        self.paths = AppPaths.for_home(self.root / "home").ensure()
        self.settings = SettingsStore(self.paths)
        self.artifacts = ArtifactStore(self.paths)
        self.runner = JobRunner(paths=self.paths)
        self.models = (
            ModelStore(self.paths, spec=_tiny_spec()) if self.tiny_model else ModelStore(self.paths)
        )
        if self.install_model:
            assert self.tiny_model
            self.models.install_bytes(PAYLOAD)
        self.service = WorkspaceService(
            self.settings,
            paths=self.paths,
            artifacts=self.artifacts,
            runner=self.runner,
            engines=ServiceEngines(media=self.media, separation=self.separation),
            models=self.models,
            model_transport=self.transport,
        )
        self.context = page.UiContext(service=self.service, dialogs=self.dialog)

    def source_file(self, name: str = "My Song.WAV") -> Path:
        path = self.root / name
        path.write_bytes(b"fake source bytes for " + name.encode())
        return path

    def add_separation(self, source_id: str, *, version: str = "v1") -> None:
        staging = self.artifacts.stage(f"ui-separation-{version}")
        files: list[Mapping[str, object]] = []
        for role in ("vocals", "drums", "bass", "other", "instrumental"):
            data = f"{version}-{role}-audio".encode()
            (staging / f"{role}.wav").write_bytes(data)
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
        self.artifacts.commit_separation(staging, source_id, manifest)


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[Harness]:
    item = Harness(
        root=tmp_path,
        media=FakeMedia(),
        separation=FakeSeparation(),
        dialog=FakeDialog(),
        transport=ScriptedTransport([MemoryResponse(PAYLOAD)]),
        install_model=True,
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
    for _ in range(400):
        jobs = harness.runner.jobs()
        if jobs and (expected_jobs is None or len(jobs) >= expected_jobs):
            break
        await asyncio.sleep(0.01)
    assert await asyncio.to_thread(harness.runner.wait_idle, 15) is True
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


async def _poll(harness: Harness, user, workspace_page: page.WorkspacePage) -> None:
    with user:
        workspace_page.poll()
    await asyncio.sleep(0.05)


# ---------------------------------------------------------------------------
# Model gate and download
# ---------------------------------------------------------------------------


async def test_the_gate_shows_size_revision_licence_and_consent(tmp_path: Path) -> None:
    harness = Harness(
        root=tmp_path,
        media=FakeMedia(),
        separation=FakeSeparation(),
        dialog=FakeDialog(),
        transport=ScriptedTransport([MemoryResponse(PAYLOAD)]),
        tiny_model=False,
    )
    harness.runner.start()
    try:
        async for user, workspace_page in _open_page(harness):
            await _import(harness, user, workspace_page)

            await user.should_see(marker="separation-gate")
            await user.should_see("Separation model not installed")
            await user.should_see("84 MB")
            assert "cbc8a9b1" in _only(user, "model-identity").text
            assert (
                "not covered by StemForge's GPL-3.0 licence"
                in _only(user, "model-licence-notice").text
            )
            await user.should_see("Press Download model to accept this notice.")
            assert "separation.model_missing" in _only(user, "separation-gate-error-code").text
            assert _enabled(user, "model-download") is True
            await user.should_not_see(marker="separation-action")

            # Ticket 08 §10: Licence details opens About, not a modal of its own.
            user.find(marker="model-licence-details").click()
            await user.should_see(marker="settings-dialog")
            user.find(marker="settings-close").click()
            await asyncio.sleep(0.05)

            # The rest of the app stays usable without the model.
            assert _enabled(user, "add-audio") is True
            user.find(marker="pitch-up").click()
            await asyncio.sleep(0.05)
            assert _only(user, "pitch-semitones").text == "+1"
    finally:
        harness.runner.stop(timeout=10)


async def test_download_progress_reaches_the_job_bar_and_cancel_returns_the_gate(
    tmp_path: Path,
) -> None:
    gate = threading.Event()
    response = MemoryResponse(PAYLOAD, gate=gate, gate_after=2)
    harness = Harness(
        root=tmp_path,
        media=FakeMedia(),
        separation=FakeSeparation(),
        dialog=FakeDialog(),
        transport=ScriptedTransport([response]),
        tiny_model=True,
    )
    harness.runner.start()
    try:
        async for user, workspace_page in _open_page(harness):
            await _import(harness, user, workspace_page)
            user.find(marker="model-download").click()
            assert await asyncio.to_thread(response.blocked.wait, 5)
            await _poll(harness, user, workspace_page)

            await user.should_see(marker="job-bar")
            await user.should_see("Downloading model")
            await user.should_see(marker="model-download-progress")
            assert "Download" in _only(user, "job-stage").text
            assert _only(user, "job-percent").text != "0%"
            assert _only(user, "model-download-progress").text.endswith("%")
            assert "MB" in _only(user, "model-download-progress").text
            assert _enabled(user, "model-download-cancel") is True

            user.find(marker="cancel-job").click()
            gate.set()
            await _finish_jobs(harness, user, workspace_page)
            await _poll(harness, user, workspace_page)

            await user.should_see(marker="separation-gate")
            assert _enabled(user, "model-download") is True
            assert harness.models.is_installed() is False
            assert [job for job in harness.runner.jobs() if job.kind.value == "separate"] == []
    finally:
        harness.runner.stop(timeout=10)


async def test_offline_download_is_coded_and_retry_installs_the_model(tmp_path: Path) -> None:
    transport = ScriptedTransport([urllib.error.URLError(OSError("offline"))])
    harness = Harness(
        root=tmp_path,
        media=FakeMedia(),
        separation=FakeSeparation(),
        dialog=FakeDialog(),
        transport=transport,
        tiny_model=True,
    )
    harness.runner.start()
    try:
        async for user, workspace_page in _open_page(harness):
            await _import(harness, user, workspace_page)
            user.find(marker="model-download").click()
            await _finish_jobs(harness, user, workspace_page, expected_jobs=1)
            await _poll(harness, user, workspace_page)

            assert (
                "separation.model_download_failed"
                in _only(user, "separation-model-error-code").text
            )
            await user.should_see("The model download failed.")
            assert _enabled(user, "model-download-retry") is True
            assert harness.models.is_installed() is False

            transport.outcomes = [MemoryResponse(PAYLOAD)]
            user.find(marker="model-download-retry").click()
            await _finish_jobs(harness, user, workspace_page, expected_jobs=2)
            await _poll(harness, user, workspace_page)

            await user.should_not_see(marker="separation-gate")
            assert harness.models.is_installed() is True
            assert _enabled(user, "separation-action") is True
            # Issue 42: the sibling card primary shares the Pitch primary box.
            assert "sf-control" in _only(user, "separation-action").classes
            assert "sf-control--primary" in _only(user, "separation-action").classes
            assert [job for job in harness.runner.jobs() if job.kind.value == "separate"] == []
    finally:
        harness.runner.stop(timeout=10)


async def test_a_missing_model_never_crashes_and_renders_the_gate(tmp_path: Path) -> None:
    harness = Harness(
        root=tmp_path,
        media=FakeMedia(),
        separation=FakeSeparation(),
        dialog=FakeDialog(),
        transport=ScriptedTransport([MemoryResponse(PAYLOAD)]),
        tiny_model=True,
        install_model=False,
    )
    harness.runner.start()
    try:
        async for user, workspace_page in _open_page(harness):
            track_id = await _import(harness, user, workspace_page)

            await user.should_see(marker="separation-gate")
            await user.should_see(marker="separation-gate-error-code")
            await user.should_see(marker="separation-card")
            assert _enabled(user, f"track-play-{track_id}") is True
            assert harness.service.snapshot().tracks[0].track_id == track_id
    finally:
        harness.runner.stop(timeout=10)


async def test_one_successful_install_resubmits_a_pending_separation_exactly_once(
    tmp_path: Path,
) -> None:
    harness = Harness(
        root=tmp_path,
        media=FakeMedia(),
        separation=FakeSeparation(),
        dialog=FakeDialog(),
        transport=ScriptedTransport([MemoryResponse(PAYLOAD)]),
        tiny_model=True,
        install_model=True,
    )
    harness.runner.start()
    try:
        async for user, workspace_page in _open_page(harness):
            track_id = await _import(harness, user, workspace_page)
            assert _enabled(user, "separation-action") is True

            # The model disappears after the card was built; pressing Separate
            # is the historical "pending" path ticket 08 §10 describes.
            harness.models.delete()
            user.find(marker="separation-action").click()
            await _poll(harness, user, workspace_page)

            await user.should_see(marker="separation-gate")
            assert harness.service.pending_separation_track_id() == track_id
            assert [job for job in harness.runner.jobs() if job.kind.value == "separate"] == []

            user.find(marker="model-download").click()
            for _ in range(400):
                jobs = harness.runner.jobs()
                kinds = [job.kind.value for job in jobs]
                if "model_install" in kinds and "separate" in kinds:
                    break
                await asyncio.sleep(0.01)
            await _finish_jobs(harness, user, workspace_page)
            await _finish_jobs(harness, user, workspace_page)
            await _poll(harness, user, workspace_page)

            kinds = [job.kind.value for job in harness.runner.jobs()]
            assert kinds.count("model_install") == 1
            assert kinds.count("separate") == 1
            assert harness.service.pending_separation_track_id() is None

            # Further polls must not chain another Separation.
            await _poll(harness, user, workspace_page)
            await _poll(harness, user, workspace_page)
            assert len([job for job in harness.runner.jobs() if job.kind.value == "separate"]) == 1
            await user.should_see(marker="output-row-vocals")
    finally:
        harness.runner.stop(timeout=10)


async def test_a_checksum_failure_shows_the_gate_and_latches_one_resubmit(
    tmp_path: Path,
) -> None:
    harness = Harness(
        root=tmp_path,
        media=FakeMedia(),
        separation=FakeSeparation(),
        dialog=FakeDialog(),
        transport=ScriptedTransport([MemoryResponse(PAYLOAD)]),
        tiny_model=True,
        install_model=True,
    )
    harness.runner.start()
    try:
        async for user, workspace_page in _open_page(harness):
            track_id = await _import(harness, user, workspace_page)
            harness.models.path().write_bytes(b"corrupted" + PAYLOAD[9:])

            user.find(marker="separation-action").click()
            await _finish_jobs(harness, user, workspace_page, expected_jobs=2)
            await _poll(harness, user, workspace_page)

            await user.should_see(marker="separation-gate")
            await user.should_see("The separation model needs to be downloaded again.")
            await user.should_see("The cached copy didn't match its checksum.")
            await user.should_not_see(marker="separation-retry")
            assert harness.service.pending_separation_track_id() == track_id
            assert harness.models.is_installed() is False

            user.find(marker="model-download").click()
            await _finish_jobs(harness, user, workspace_page)
            await _finish_jobs(harness, user, workspace_page)
            await _poll(harness, user, workspace_page)
            kinds = [job.kind.value for job in harness.runner.jobs()]
            assert kinds.count("separate") == 2
            assert kinds.count("model_install") == 1
    finally:
        harness.runner.stop(timeout=10)


async def test_an_install_checksum_mismatch_uses_the_checksum_gate_copy(
    tmp_path: Path,
) -> None:
    """Ticket 14 §4.3: a download that fails its pins is a checksum failure."""
    corrupt = b"x" * len(PAYLOAD)
    harness = Harness(
        root=tmp_path,
        media=FakeMedia(),
        separation=FakeSeparation(),
        dialog=FakeDialog(),
        transport=ScriptedTransport([MemoryResponse(corrupt)]),
        tiny_model=True,
    )
    harness.runner.start()
    try:
        async for user, workspace_page in _open_page(harness):
            await _import(harness, user, workspace_page)
            user.find(marker="model-download").click()
            await _finish_jobs(harness, user, workspace_page, expected_jobs=1)
            await _poll(harness, user, workspace_page)

            await user.should_see(marker="separation-gate")
            await user.should_see("The separation model needs to be downloaded again.")
            await user.should_see("The cached copy didn't match its checksum.")
            await user.should_not_see("Separation model not installed")
            assert _enabled(user, "model-download-retry") is True
            assert harness.models.is_installed() is False
            assert [job for job in harness.runner.jobs() if job.kind.value == "separate"] == []
    finally:
        harness.runner.stop(timeout=10)


# ---------------------------------------------------------------------------
# Separation progress, stall, and cancel
# ---------------------------------------------------------------------------


async def test_progress_cancel_and_stall_use_the_injected_clock(tmp_path: Path) -> None:
    harness = Harness(
        root=tmp_path,
        media=FakeMedia(),
        separation=FakeSeparation(),
        dialog=FakeDialog(),
        transport=ScriptedTransport([MemoryResponse(PAYLOAD)]),
        tiny_model=True,
        install_model=True,
    )
    harness.runner.start()
    try:
        async for user, workspace_page in _open_page(harness):
            await _import(harness, user, workspace_page)
            clock = FakeClock()
            workspace_page._separation.clock = clock
            workspace_page._separation.stall_seconds = 30.0
            gate = harness.separation.block()

            user.find(marker="separation-action").click()
            assert await asyncio.to_thread(harness.separation.started.wait, 5)
            await _poll(harness, user, workspace_page)

            await user.should_see(marker="job-bar")
            await user.should_see("Separating")
            await user.should_see(marker="separation-progress")
            assert _enabled(user, "separation-cancel") is True
            await user.should_not_see(marker="separation-stall")

            clock.advance(31)
            await _poll(harness, user, workspace_page)
            await user.should_see(marker="separation-stall")
            await user.should_see("No progress for 30 seconds. Separation may still be working.")
            assert _enabled(user, "separation-stall-cancel") is True

            user.find(marker="separation-keep-waiting").click()
            await asyncio.sleep(0.05)
            await user.should_not_see(marker="separation-stall")

            clock.advance(31)
            await _poll(harness, user, workspace_page)
            await user.should_not_see(marker="separation-stall")

            user.find(marker="separation-cancel").click()
            gate.set()
            await _finish_jobs(harness, user, workspace_page)
            await _poll(harness, user, workspace_page)

            separation_jobs = [job for job in harness.runner.jobs() if job.kind.value == "separate"]
            assert [job.state.value for job in separation_jobs] == ["cancelled"]
            await user.should_not_see(marker="separation-error")
            await user.should_not_see(marker="output-row-vocals")
            assert _only(user, "separation-status").text == "Not separated · htdemucs · CPU"
            assert list(harness.artifacts.staging_dir.iterdir()) == []
    finally:
        harness.runner.stop(timeout=10)


# ---------------------------------------------------------------------------
# Result rows, playback, A/B, export target
# ---------------------------------------------------------------------------


async def test_primary_rows_are_visible_secondary_hidden_and_every_row_plays(
    harness: Harness,
) -> None:
    async for user, workspace_page in _open_page(harness):
        track_id = await _import(harness, user, workspace_page)
        user.find(marker="separation-action").click()
        await _finish_jobs(harness, user, workspace_page, expected_jobs=2)
        await _poll(harness, user, workspace_page)

        # Ticket 08 §3 and §7: the rail reflects stem state; stems are Outputs rows.
        assert "4 stems" in _only(user, f"track-meta-{track_id}").text
        assert "is-ok" in _only(user, f"track-dot-{track_id}").classes
        await user.should_see(marker="output-row-vocals")
        await user.should_see(marker="output-row-instrumental")
        await user.should_not_see(marker="output-row-drums")
        await user.should_not_see(marker="output-row-bass")
        await user.should_not_see(marker="output-row-other")
        await user.should_see("Show 3 more stems")

        user.find(marker="outputs-show-more").click()
        await asyncio.sleep(0.05)
        for role in ("drums", "bass", "other"):
            await user.should_see(marker=f"output-row-{role}")

        track = harness.service.snapshot().tracks[0]
        (item,) = harness.service.separation_sets(track)
        player = _only(user, "player")
        for role in ("vocals", "instrumental", "drums", "bass", "other"):
            target = harness.service.separation_preview_target(track, item.set_key, role)
            assert target is not None
            user.find(marker=f"output-play-{role}").click()
            await asyncio.sleep(0.05)
            assert player.props["src"] == target.url
            assert len(user.find(marker="player").elements) == 1

        # Ticket 08 §8: switching artifacts never carries the position over.
        user.find(marker="output-play-vocals").click()
        await asyncio.sleep(0.05)
        user.find(marker="player").trigger("timeupdate", 42.0)
        await asyncio.sleep(0.05)
        assert _only(user, "elapsed").text == "0:42"
        user.find(marker="output-play-instrumental").click()
        await asyncio.sleep(0.05)
        assert _only(user, "elapsed").text == "0:00"

        export = next(
            target for target in harness.service.export_targets(track) if target.key == "stem:drums"
        )
        user.find(marker="output-export-drums").click()
        await asyncio.sleep(0.05)
        assert workspace_page._export_sheet._target_key == export.key
        assert _only(user, "export-source-label").text == "Drums"
        user.find(marker="export-cancel").click()
        await asyncio.sleep(0.05)

        # A/B stays the transport's Original | latest-render shortcut; playing
        # a stem does not repurpose it (ticket 08 §7).
        original = harness.service.preview_targets(track_id)[0]
        user.find(marker="output-play-vocals").click()
        await asyncio.sleep(0.05)
        assert player.props["src"].endswith("/vocals.mp3")
        assert _only(user, "ab-preview").text == "Latest render"
        assert _enabled(user, "ab-preview") is False
        user.find(marker="output-play-original").click()
        await asyncio.sleep(0.05)
        assert player.props["src"] == original.url
        assert len(user.find(marker="player").elements) == 1


async def test_rendering_committed_stems_never_rehashes_audio(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async for user, workspace_page in _open_page(harness):
        await _import(harness, user, workspace_page)
        track = harness.service.snapshot().tracks[0]
        harness.add_separation(track.source_id)

        def unexpected(path: Path) -> str:
            raise AssertionError(f"the card re-hashed {path}")

        # A refresh after Separation must read manifests and stats, never the
        # hundreds of megabytes those manifests describe (issue #27).
        monkeypatch.setattr("stemforge.artifacts._sha256_file", unexpected)
        with user:
            workspace_page._detail.refresh()
        await asyncio.sleep(0.05)

        await user.should_see(marker="output-row-vocals")
        await user.should_see(marker="output-row-instrumental")
        for role in ("vocals", "instrumental"):
            assert _enabled(user, f"output-play-{role}") is True
            assert _enabled(user, f"output-export-{role}") is True


async def test_a_failed_reseparation_keeps_the_last_stems_playable(harness: Harness) -> None:
    async for user, workspace_page in _open_page(harness):
        track_id = await _import(harness, user, workspace_page)
        user.find(marker="separation-action").click()
        await _finish_jobs(harness, user, workspace_page, expected_jobs=2)
        await _poll(harness, user, workspace_page)

        track = harness.service.snapshot().tracks[0]
        (first,) = harness.service.separation_sets(track)
        target = harness.service.separation_preview_target(track, first.set_key, "vocals")
        assert target is not None
        user.find(marker="output-play-vocals").click()
        await asyncio.sleep(0.05)
        assert _only(user, "player").props["src"] == target.url

        harness.separation.fail_code = errors.SEPARATION_WORKER_CRASHED
        user.find(marker="separation-action").click()
        await _finish_jobs(harness, user, workspace_page, expected_jobs=3)
        await _poll(harness, user, workspace_page)

        await user.should_see(marker="separation-error")
        assert errors.SEPARATION_WORKER_CRASHED in _only(user, "separation-error-code").text
        await user.should_see("Stems from the last successful Separation are still available.")
        await user.should_see(marker="output-row-vocals")
        user.find(marker="output-play-vocals").click()
        await asyncio.sleep(0.05)
        assert _only(user, "player").props["src"] == target.url
        assert len(user.find(marker="player").elements) == 1
        assert track.track_id == track_id


# ---------------------------------------------------------------------------
# Coded failures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code",
    [
        errors.SEPARATION_WORKER_CRASHED,
        errors.SEPARATION_ENGINE_ERROR,
        errors.SEPARATION_OUTPUT_INVALID,
    ],
)
async def test_a_retryable_separation_failure_is_inline_and_retry_resubmits(
    harness: Harness,
    code: str,
) -> None:
    async for user, workspace_page in _open_page(harness):
        await _import(harness, user, workspace_page)
        harness.separation.fail_code = code
        harness.separation.retryable = True
        user.find(marker="separation-action").click()
        await _finish_jobs(harness, user, workspace_page, expected_jobs=2)
        await _poll(harness, user, workspace_page)

        await user.should_see(marker="separation-error")
        assert code in _only(user, "separation-error-code").text
        assert _enabled(user, "separation-retry") is True

        harness.separation.fail_code = None
        user.find(marker="separation-retry").click()
        await _finish_jobs(harness, user, workspace_page, expected_jobs=3)
        await _poll(harness, user, workspace_page)

        await user.should_not_see(marker="separation-error")
        await user.should_see(marker="output-row-vocals")
        jobs = [job for job in harness.runner.jobs() if job.kind.value == "separate"]
        assert len(jobs) == 2


async def test_a_protocol_mismatch_is_inline_without_a_retry(harness: Harness) -> None:
    async for user, workspace_page in _open_page(harness):
        await _import(harness, user, workspace_page)
        harness.separation.fail_code = errors.SEPARATION_PROTOCOL_MISMATCH
        harness.separation.retryable = False
        user.find(marker="separation-action").click()
        await _finish_jobs(harness, user, workspace_page, expected_jobs=2)
        await _poll(harness, user, workspace_page)

        await user.should_see(marker="separation-error")
        assert errors.SEPARATION_PROTOCOL_MISMATCH in _only(user, "separation-error-code").text
        await user.should_not_see(marker="separation-retry")
        await user.should_see(marker="separation-action")
