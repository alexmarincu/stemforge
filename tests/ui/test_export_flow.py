"""Headless Export flow: sheet, native save seam, Job bar, errors, and Retry.

Everything runs through NiceGUI's user simulation. The media adapter is a fake
that writes stand-ins and never spawns FFmpeg; the save dialog backend is
injected and never opens pywebview. Every write stays under ``tmp_path`` and no
test touches the network.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
    ExportFormat,
    MediaInfo,
)
from stemforge.errors import StemForgeError
from stemforge.integration.dialogs import (
    SAVE_FILTERS,
    NativeSaveDialog,
    normalize_selected_paths,
)
from stemforge.jobs.runner import JobRunner
from stemforge.platform.paths import AppPaths
from stemforge.settings import SettingsStore
from stemforge.ui import page

pytestmark = pytest.mark.ui

SAMPLE_RATE = 44_100
CHANNELS = 2
DURATION_S = 42.0
LOG = logging.getLogger("test.export-ui")


class FakeMedia:
    """A scripted ``MediaTool`` that writes tiny stand-ins, never real audio."""

    def __init__(self) -> None:
        self.duration_s = DURATION_S
        self.tags_by_path: dict[Path, dict[str, str]] = {}
        self.encode_error: StemForgeError | None = None
        self.corrupt_tags = False
        self.encode_gate: threading.Event | None = None
        self.encode_started = threading.Event()

    def probe(self, path: Path) -> MediaInfo:
        return MediaInfo(
            codec="pcm_s24le",
            sample_rate=SAMPLE_RATE,
            channels=CHANNELS,
            duration_s=self.duration_s,
            format="wav",
            tags=dict(self.tags_by_path.get(path, {})),
            bit_depth=24,
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
        del src, dst_dir
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
        del gain_db, title, artist
        cancel.check()
        dst.write_bytes(b"preview:" + src.read_bytes())

    def encode_export(
        self,
        src: Path,
        dst: Path,
        *,
        fmt: ExportFormat,
        metadata: CanonicalMetadata,
        gain_db: float,
        cover: Path | None,
        cancel: CancelToken,
        log: logging.Logger,
    ) -> None:
        del fmt, gain_db, cover
        cancel.check()
        self.encode_started.set()
        if self.encode_gate is not None:
            self.encode_gate.wait(timeout=10)
        cancel.check()
        if self.encode_error is not None:
            raise self.encode_error
        dst.write_bytes(b"encoded:" + src.read_bytes())
        tags = dict(metadata.tags())
        if self.corrupt_tags and "title" in tags:
            tags["title"] = "not the record"
        self.tags_by_path[dst] = tags
        log.debug("fake export wrote %s", dst)

    def sum_wavs(
        self,
        srcs: Sequence[Path],
        dst: Path,
        *,
        cancel: CancelToken,
        log: logging.Logger,
    ) -> None:
        del log
        cancel.check()
        dst.write_bytes(b"sum:" + b"|".join(path.read_bytes() for path in srcs))

    def peak_dbfs(self, path: Path, *, cancel: CancelToken, log: logging.Logger) -> float:
        del path, log
        cancel.check()
        return -3.0


class FakeOpenDialog:
    """An injected open dialog; each call pops the next scripted selection."""

    def __init__(self, selections: Sequence[Sequence[str]] = ()) -> None:
        self._selections = [tuple(item) for item in selections]

    async def choose_audio_files(self) -> tuple[Path, ...]:
        if not self._selections:
            return ()
        return normalize_selected_paths(self._selections.pop(0))


class FakeSaveBackend:
    """The pywebview save seam; records the request and returns a script."""

    def __init__(self, results: Sequence[str | None] = ()) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, str | None, tuple[str, ...]]] = []

    async def save_file(
        self,
        *,
        default_name: str,
        directory: str | None,
        file_types: tuple[str, ...],
    ) -> Sequence[str] | None:
        self.calls.append((default_name, directory, file_types))
        if not self.results:
            return None
        result = self.results.pop(0)
        return None if result is None else [result]


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
    open_dialog: FakeOpenDialog
    save_backend: FakeSaveBackend
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
            dialogs=self.open_dialog,
            save_dialog=NativeSaveDialog(backend=self.save_backend),
        )

    def source_file(self, name: str = "My Song.WAV") -> Path:
        path = self.root / name
        path.write_bytes(b"fake source bytes for " + name.encode())
        return path

    def add_separation(self, source_id: str) -> None:
        staging = self.artifacts.stage("ui-export-separation")
        files: list[Mapping[str, object]] = []
        for role in ("vocals", "drums", "bass", "other", "instrumental"):
            data = f"{role}-audio".encode()
            (staging / f"{role}.wav").write_bytes(data)
            files.append(_file_entry(f"{role}.wav", role, data))
        self.artifacts.commit_separation(
            staging,
            source_id,
            {
                "schema_version": 1,
                "kind": "separation",
                "source_id": source_id,
                "files": files,
            },
        )

    def add_render(self, source_id: str, *, semitones: int = 3, stems: bool = False) -> str:
        key = f"render-{'stems' if stems else 'original'}-{semitones}"
        staging = self.artifacts.stage(f"ui-export-{key}")
        roles = ("original",) if not stems else ("vocals", "drums", "bass", "other", "instrumental")
        files: list[Mapping[str, object]] = []
        for role in roles:
            data = f"render-{role}-{semitones}".encode()
            (staging / f"{role}.wav").write_bytes(data)
            files.append(_file_entry(f"{role}.wav", role, data))
        preview_role = "instrumental" if stems else "original"
        preview = f"render-{preview_role}-preview-{semitones}".encode()
        (staging / f"{preview_role}_preview.mp3").write_bytes(preview)
        files.append(_file_entry(f"{preview_role}_preview.mp3", f"{preview_role}_preview", preview))
        self.artifacts.commit_render(
            staging,
            key,
            {
                "schema_version": 1,
                "kind": "render",
                "source_id": source_id,
                "render_key": key,
                "created_at": datetime.now(UTC).isoformat(),
                "scope": "stems" if stems else "original",
                "semitones": semitones,
                "gain_db": 0.0,
                "files": files,
            },
        )
        return key


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[Harness]:
    item = Harness(
        root=tmp_path,
        media=FakeMedia(),
        open_dialog=FakeOpenDialog(),
        save_backend=FakeSaveBackend(),
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
    harness.open_dialog._selections = [(str(harness.source_file(name)),)]
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


def _assert_no_partial(directory: Path) -> None:
    assert not list(directory.rglob("*.partial")), list(directory.rglob("*"))


def _sheet_open(workspace_page: page.WorkspacePage) -> bool:
    dialog = workspace_page._export_sheet._dialog
    return dialog is not None and bool(dialog.value)


async def _open_sheet(user, workspace_page: page.WorkspacePage) -> None:
    user.find(marker="export-action").click()
    await asyncio.sleep(0.05)
    assert _sheet_open(workspace_page)


# ---------------------------------------------------------------------------
# Sheet contents and selection
# ---------------------------------------------------------------------------


async def test_the_sheet_is_format_only_and_keeps_the_entry_point_source(
    harness: Harness,
) -> None:
    async for user, workspace_page in _open_page(harness):
        track_id = await _import(harness, user, workspace_page)
        source_id = harness.service.snapshot().tracks[0].source_id
        harness.add_separation(source_id)
        harness.add_render(source_id, semitones=3)
        harness.add_render(source_id, semitones=-2, stems=True)
        with user:
            workspace_page.poll()
        user.find(marker=f"track-row-{track_id}").click()
        await asyncio.sleep(0.05)

        await _open_sheet(user, workspace_page)

        await user.should_see("Export")
        await user.should_see("WAV 24-bit")
        await user.should_see("FLAC 24-bit")
        await user.should_see("MP3 320")
        for marker in (
            "export-format-wav24",
            "export-format-flac24",
            "export-format-mp3_320",
        ):
            await user.should_see(marker=marker)
        assert "is-active" in _only(user, "export-format-wav24").classes
        assert _only(user, "export-source-label").text == "Original"
        assert _only(user, "export-default-name").text.endswith(".wav")
        await user.should_see("about 11.1 MB")
        await user.should_see("about 6.6 MB")
        await user.should_see("about 1.7 MB")
        user.find(marker="export-format-mp3_320").click()
        await asyncio.sleep(0.05)
        assert "is-active" in _only(user, "export-format-mp3_320").classes
        assert _only(user, "export-default-name").text.endswith(".mp3")
        assert len(user.find(marker="export-confirm").elements) == 1
        # Ticket 08 §9: the entry point chooses the source; the sheet does not.
        assert _count(user, "export-target-master") == 0
        assert _count(user, "export-target-stem:vocals") == 0
        await user.should_not_see("Save original")
        await user.should_not_see("Export all")
        await user.should_not_see("batch")


async def test_a_format_and_target_choice_drives_the_native_save_request_and_success(
    harness: Harness,
) -> None:
    async for user, workspace_page in _open_page(harness):
        track_id = await _import(harness, user, workspace_page)
        source_id = harness.service.snapshot().tracks[0].source_id
        harness.add_separation(source_id)
        preferred = harness.root / "preferred"
        preferred.mkdir()
        harness.settings.set_export_dir(preferred)
        with user:
            workspace_page.poll()
        user.find(marker=f"track-row-{track_id}").click()
        await asyncio.sleep(0.05)

        user.find(marker="output-export-vocals").click()
        await asyncio.sleep(0.05)
        assert _only(user, "export-source-label").text == "Vocals"
        user.find(marker="export-format-flac24").click()
        await asyncio.sleep(0.05)
        assert "is-active" in _only(user, "export-format-flac24").classes
        assert _only(user, "export-default-name").text.endswith(" - Vocals.flac")

        destination = harness.root / "exports"
        destination.mkdir()
        harness.save_backend.results = [str(destination / "Chosen Vocals.flac")]
        user.find(marker="export-confirm").click()
        await asyncio.sleep(0.05)
        await _finish_jobs(harness, user, workspace_page, expected_jobs=2)

        assert len(harness.save_backend.calls) == 1
        default_name, directory, file_types = harness.save_backend.calls[0]
        assert default_name.endswith(" - Vocals.flac")
        assert directory == str(preferred)
        assert file_types == (SAVE_FILTERS[".flac"],)

        final = destination / "Chosen Vocals.flac"
        assert final.is_file()
        assert final.read_bytes().startswith(b"encoded:")
        _assert_no_partial(destination)
        await user.should_see(marker="export-success")
        assert str(final) in _only(user, "export-destination").text
        assert harness.settings.preferred_export_dir == destination
        assert sorted(job.kind.value for job in harness.runner.jobs()) == [
            "export",
            "import_local",
        ]


async def test_a_typed_wrong_extension_is_corrected_without_switching_format(
    harness: Harness,
) -> None:
    async for user, workspace_page in _open_page(harness):
        track_id = await _import(harness, user, workspace_page)
        source_id = harness.service.snapshot().tracks[0].source_id
        harness.add_separation(source_id)
        with user:
            workspace_page.poll()
        user.find(marker=f"track-row-{track_id}").click()
        await asyncio.sleep(0.05)

        user.find(marker="output-export-vocals").click()
        await asyncio.sleep(0.05)
        assert _only(user, "export-source-label").text == "Vocals"
        assert "is-active" in _only(user, "export-format-wav24").classes

        destination = harness.root / "exports"
        destination.mkdir()
        harness.save_backend.results = [str(destination / "Chosen Mix.flac")]
        user.find(marker="export-confirm").click()
        await asyncio.sleep(0.05)
        await user.should_see("Extension changed to .wav for WAV 24-bit.")
        await _finish_jobs(harness, user, workspace_page, expected_jobs=2)

        final = destination / "Chosen Mix.wav"
        assert final.is_file()
        assert not (destination / "Chosen Mix.flac").exists()
        _assert_no_partial(destination)
        await user.should_see(marker="export-success")
        assert "Extension changed to .wav for WAV 24-bit." in _only(user, "export-correction").text
        assert (
            harness.service.export_result(
                next(job.job_id for job in harness.runner.jobs() if job.kind.value == "export")
            ).format
            is ExportFormat.WAV24
        )


async def test_cancelling_the_native_dialog_starts_no_job(harness: Harness) -> None:
    async for user, workspace_page in _open_page(harness):
        await _import(harness, user, workspace_page)
        await _open_sheet(user, workspace_page)
        harness.save_backend.results = [None]

        user.find(marker="export-confirm").click()
        await asyncio.sleep(0.05)

        assert harness.save_backend.calls
        default_name, directory, file_types = harness.save_backend.calls[0]
        assert default_name == "Song.wav"
        assert directory is None
        assert file_types == (SAVE_FILTERS[".wav"],)
        assert [job.kind.value for job in harness.runner.jobs()] == ["import_local"]
        assert not (harness.root / "exports").exists() or not list(
            (harness.root / "exports").iterdir()
        )
        assert _sheet_open(workspace_page)
        assert _enabled(user, "export-confirm") is True

        user.find(marker="export-cancel").click()
        await asyncio.sleep(0.05)
        assert not _sheet_open(workspace_page)


async def test_cancelling_the_export_job_leaves_no_file_or_partial(
    harness: Harness,
) -> None:
    async for user, workspace_page in _open_page(harness):
        await _import(harness, user, workspace_page)
        gate = threading.Event()
        harness.media.encode_gate = gate
        harness.media.encode_started.clear()
        destination = harness.root / "exports"
        destination.mkdir()
        harness.save_backend.results = [str(destination / "Song.wav")]
        await _open_sheet(user, workspace_page)

        user.find(marker="export-confirm").click()
        assert await asyncio.to_thread(harness.media.encode_started.wait, 5)
        with user:
            workspace_page.poll()
        await user.should_see(marker="job-bar")
        user.find(marker="cancel-job").click()
        gate.set()
        await _finish_jobs(harness, user, workspace_page, expected_jobs=2)
        await asyncio.sleep(0.05)

        export_jobs = [job for job in harness.runner.jobs() if job.kind.value == "export"]
        assert [job.state.value for job in export_jobs] == ["cancelled"]
        assert list(destination.iterdir()) == []
        _assert_no_partial(destination)
        assert list(harness.artifacts.staging_dir.iterdir()) == []
        await user.should_see("Export cancelled.")


async def test_output_rows_and_the_transport_open_the_sheet_on_that_target(
    harness: Harness,
) -> None:
    async for user, workspace_page in _open_page(harness):
        track_id = await _import(harness, user, workspace_page)
        source_id = harness.service.snapshot().tracks[0].source_id
        render_key = harness.add_render(source_id, semitones=3)
        with user:
            workspace_page.poll()
        user.find(marker=f"track-row-{track_id}").click()
        await asyncio.sleep(0.05)

        user.find(marker="output-export-original").click()
        await asyncio.sleep(0.05)
        assert _sheet_open(workspace_page)
        assert workspace_page._export_sheet._target_key == "master"
        assert _only(user, "export-source-label").text == "Original"
        user.find(marker="export-cancel").click()
        await asyncio.sleep(0.05)

        # The transport exports what is loaded: the active render's first row.
        user.find(marker="ab-preview").click()
        await asyncio.sleep(0.05)
        user.find(marker="export-action").click()
        await asyncio.sleep(0.05)
        assert _sheet_open(workspace_page)
        assert workspace_page._export_sheet._target_key == f"render:{render_key}:original"
        assert _only(user, "export-source-label").text == "Original +3 st"


async def test_a_separation_row_export_opens_the_sheet_on_that_stem(
    harness: Harness,
) -> None:
    async for user, workspace_page in _open_page(harness):
        track_id = await _import(harness, user, workspace_page)
        source_id = harness.service.snapshot().tracks[0].source_id
        harness.add_separation(source_id)
        with user:
            workspace_page.poll()
        user.find(marker=f"track-row-{track_id}").click()
        await asyncio.sleep(0.05)
        user.find(marker="outputs-show-more").click()
        await asyncio.sleep(0.05)

        user.find(marker="output-export-drums").click()
        await asyncio.sleep(0.05)
        assert _sheet_open(workspace_page)
        assert workspace_page._export_sheet._target_key == "stem:drums"
        assert _only(user, "export-source-label").text == "Drums"


# ---------------------------------------------------------------------------
# Failures, Retry, and cleanup
# ---------------------------------------------------------------------------


async def test_a_write_failure_is_inline_with_retry_and_leaves_no_file_or_partial(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from stemforge.pipeline import export_job

    async for user, workspace_page in _open_page(harness):
        track_id = await _import(harness, user, workspace_page)
        source_id = harness.service.snapshot().tracks[0].source_id
        harness.add_separation(source_id)
        with user:
            workspace_page.poll()
        user.find(marker=f"track-row-{track_id}").click()
        await asyncio.sleep(0.05)

        user.find(marker="output-export-vocals").click()
        await asyncio.sleep(0.05)
        user.find(marker="export-format-flac24").click()
        await asyncio.sleep(0.05)
        destination = harness.root / "exports"
        destination.mkdir()
        harness.save_backend.results = [str(destination / "Blocked.flac")]

        def fail_commit(partial: Path, **kwargs: object) -> Path:
            raise PermissionError(13, "injected permission failure")

        monkeypatch.setattr(export_job, "_commit_without_overwrite", fail_commit)
        user.find(marker="export-confirm").click()
        await asyncio.sleep(0.05)
        await _finish_jobs(harness, user, workspace_page, expected_jobs=2)

        await user.should_see(marker="export-failure")
        error_code = _only(user, "export-error-code").text
        assert "export.write_failed" in error_code
        assert error_code.startswith("export.write_failed · job ")
        await user.should_see("Couldn't save the file.")
        await user.should_see("Choose another folder, or check its permissions.")
        assert _enabled(user, "export-retry") is True
        assert list(destination.iterdir()) == []
        _assert_no_partial(destination)
        assert list(harness.artifacts.staging_dir.iterdir()) == []

        # Retry re-submits the identical spec without reopening the dialog, and
        # keeps the chosen FLAC format and Vocals source.
        monkeypatch.setattr(
            export_job,
            "_commit_without_overwrite",
            lambda partial, **kwargs: Path(partial).replace(
                Path(kwargs["destination"]) / "Blocked.flac"
            ),
        )
        user.find(marker="export-retry").click()
        await asyncio.sleep(0.05)
        await _finish_jobs(harness, user, workspace_page, expected_jobs=3)

        assert len(harness.save_backend.calls) == 1
        assert (destination / "Blocked.flac").is_file()
        assert not (destination / "Blocked.wav").exists()
        await user.should_see(marker="export-success")


async def test_an_encode_failure_is_coded_retryable_and_cleans_up(harness: Harness) -> None:
    async for user, workspace_page in _open_page(harness):
        await _import(harness, user, workspace_page)
        await _open_sheet(user, workspace_page)
        destination = harness.root / "exports"
        destination.mkdir()
        harness.save_backend.results = [str(destination / "Song.wav")]
        harness.media.encode_error = StemForgeError(
            errors.MEDIA_ENCODE_FAILED,
            "fake encoder failure",
            detail="stderr tail",
        )

        user.find(marker="export-confirm").click()
        await asyncio.sleep(0.05)
        await _finish_jobs(harness, user, workspace_page, expected_jobs=2)

        assert errors.MEDIA_ENCODE_FAILED in _only(user, "export-error-code").text
        await user.should_see("Couldn't encode the file.")
        assert _enabled(user, "export-retry") is True
        assert list(destination.iterdir()) == []
        _assert_no_partial(destination)
        assert list(harness.artifacts.staging_dir.iterdir()) == []


async def test_a_validation_failure_is_coded_retryable_and_cleans_up(harness: Harness) -> None:
    async for user, workspace_page in _open_page(harness):
        await _import(harness, user, workspace_page)
        await _open_sheet(user, workspace_page)
        destination = harness.root / "exports"
        destination.mkdir()
        harness.save_backend.results = [str(destination / "Song.wav")]
        harness.media.corrupt_tags = True

        user.find(marker="export-confirm").click()
        await asyncio.sleep(0.05)
        await _finish_jobs(harness, user, workspace_page, expected_jobs=2)

        assert errors.MEDIA_VALIDATION_FAILED in _only(user, "export-error-code").text
        await user.should_see("The generated audio didn't pass validation.")
        assert _enabled(user, "export-retry") is True
        assert list(destination.iterdir()) == []
        _assert_no_partial(destination)


async def test_a_full_destination_is_coded_with_the_export_action(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from collections import namedtuple

    Usage = namedtuple("Usage", "total used free")
    async for user, workspace_page in _open_page(harness):
        await _import(harness, user, workspace_page)
        await _open_sheet(user, workspace_page)
        destination = harness.root / "exports"
        destination.mkdir()
        harness.save_backend.results = [str(destination / "Song.wav")]
        monkeypatch.setattr(
            "stemforge.artifacts.shutil.disk_usage",
            lambda _path: Usage(10**12, 10**12 - 1024, 1024),
        )

        user.find(marker="export-confirm").click()
        await asyncio.sleep(0.05)
        await _finish_jobs(harness, user, workspace_page, expected_jobs=2)

        assert errors.STORAGE_DISK_FULL in _only(user, "export-error-code").text
        await user.should_see("Not enough disk space.")
        await user.should_see("Choose another location.")
        assert _enabled(user, "export-retry") is True
        assert list(destination.iterdir()) == []


async def test_playback_and_ab_stay_enabled_during_an_export_job(harness: Harness) -> None:
    async for user, workspace_page in _open_page(harness):
        track_id = await _import(harness, user, workspace_page)
        source_id = harness.service.snapshot().tracks[0].source_id
        harness.add_render(source_id, semitones=2)
        with user:
            workspace_page.poll()
        user.find(marker=f"track-row-{track_id}").click()
        await asyncio.sleep(0.05)

        gate = threading.Event()
        harness.media.encode_gate = gate
        harness.media.encode_started.clear()
        destination = harness.root / "exports"
        destination.mkdir()
        harness.save_backend.results = [str(destination / "Slow.wav")]
        await _open_sheet(user, workspace_page)
        user.find(marker="export-confirm").click()
        assert await asyncio.to_thread(harness.media.encode_started.wait, 5)
        await asyncio.sleep(0.05)
        with user:
            workspace_page.poll()

        await user.should_see(marker="job-bar")
        await user.should_see("Exporting")
        # Playback and A/B remain live; only second-Job actions disable.
        assert _enabled(user, "transport-play") is True
        assert _enabled(user, "seek") is True
        assert _enabled(user, "ab-original") is True
        assert _enabled(user, "ab-preview") is True
        assert _enabled(user, "export-action") is False
        assert len(user.find(marker="player").elements) == 1

        gate.set()
        await _finish_jobs(harness, user, workspace_page, expected_jobs=2)
        assert _enabled(user, "export-action") is True
        assert _enabled(user, "add-audio") is True
