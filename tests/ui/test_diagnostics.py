"""Headless tests for Details, Copy diagnostics, and the copy-only modal.

The clipboard and OS opener are injected; no test touches the real browser
clipboard, a native dialog, or a file manager. The report text is a fixed
fixture, so the assertion is about what the UI does with it, not about the
report builder (covered in ``tests/unit/test_diagnostics.py``).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from nicegui.testing.user_simulation import user_simulation

from stemforge.application import ServiceEngines, WorkspaceService
from stemforge.artifacts import ArtifactStore
from stemforge.domain.cancellation import CancelToken
from stemforge.engines.media import CanonicalMetadata, DecodeResult, MediaInfo
from stemforge.errors import StemForgeError
from stemforge.jobs.runner import JobRunner
from stemforge.platform.paths import AppPaths
from stemforge.settings import SettingsStore
from stemforge.ui import page
from stemforge.ui.components.diagnostics import BROWSER_COPY_JS

pytestmark = pytest.mark.ui

REPORT = "StemForge 1.0.0 · source · Linux 6.8 x86_64 · x11\nPath: /tmp/home"


class FailingMedia:
    """A local-import stand-in that fails before any file is written."""

    def probe(self, path: Path) -> MediaInfo:
        raise StemForgeError("media.probe_failed", "fake probe failure")

    def decode_canonical(
        self, src: Path, dst: Path, *, cancel: CancelToken, log: logging.Logger
    ) -> DecodeResult:
        raise AssertionError("the probe fails first")

    def extract_metadata(self, src: Path) -> CanonicalMetadata:
        raise AssertionError("the probe fails first")

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
        raise AssertionError("the probe fails first")

    def peak_dbfs(self, path: Path, *, cancel: CancelToken, log: logging.Logger) -> float:
        return -3.0


class FakeDialog:
    def __init__(self, selections: tuple[Path, ...]) -> None:
        self._selections = selections

    async def choose_audio_files(self) -> tuple[Path, ...]:
        return self._selections


class RecordingClipboard:
    def __init__(self, *, succeeds: bool = True) -> None:
        self.succeeds = succeeds
        self.texts: list[str] = []

    async def write_text(self, text: str) -> bool:
        self.texts.append(text)
        return self.succeeds


class RecordingOpener:
    def __init__(self) -> None:
        self.calls: list[Path] = []

    def open_path(self, path: Path) -> None:
        self.calls.append(path)


@dataclass
class Harness:
    root: Path
    selections: tuple[Path, ...] = ()
    clipboard: RecordingClipboard = field(default_factory=RecordingClipboard)
    opener: RecordingOpener = field(default_factory=RecordingOpener)
    job_ids: list[str | None] = field(default_factory=list)
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
            engines=ServiceEngines(media=FailingMedia()),
        )
        self.context = page.UiContext(
            service=self.service,
            dialogs=FakeDialog(self.selections),
            open_path=self.opener,
            diagnostics=self._diagnostics,
            clipboard=self.clipboard,
        )

    def _diagnostics(self, job_id: str | None) -> str:
        self.job_ids.append(job_id)
        return REPORT


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[Harness]:
    source = tmp_path / "broken.wav"
    source.write_bytes(b"not audio")
    item = Harness(root=tmp_path, selections=(source,))
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


async def _run_failing_import(user, workspace_page, harness: Harness) -> None:
    user.find(marker="add-audio").click()
    await asyncio.sleep(0.1)
    for _ in range(400):
        with user:
            workspace_page.poll()
        if any(job.finished for job in harness.runner.jobs()):
            break
        await asyncio.sleep(0.01)
    await user.should_see(marker="import-error")


async def test_about_has_copy_diagnostics_and_a_read_only_modal(harness: Harness) -> None:
    async for user, _page in _open_page(harness):
        user.find(marker="settings-about").click()
        await user.should_see(marker="settings-dialog")
        user.find(marker="about-copy-diagnostics").click()
        await user.should_see(marker="diagnostics-dialog")
        await user.should_see("Contains local file paths. Review before sharing.")
        await user.should_see(marker="diagnostics-text")

        user.find(marker="diagnostics-copy").click()
        await asyncio.sleep(0.05)

        assert harness.clipboard.texts == [REPORT]
        assert harness.job_ids == [None]
        await user.should_see("Copied.")
        await user.should_see(marker="diagnostics-text")


async def test_copy_failure_keeps_the_text_selectable_and_says_so(
    harness: Harness,
) -> None:
    harness.clipboard.succeeds = False
    async for user, _page in _open_page(harness):
        user.find(marker="settings-about").click()
        user.find(marker="about-copy-diagnostics").click()
        await user.should_see(marker="diagnostics-dialog")
        user.find(marker="diagnostics-copy").click()
        await asyncio.sleep(0.05)

        await user.should_see("Copy failed. Select the text and copy it manually.")
        await user.should_see(marker="diagnostics-text")


async def test_import_failure_details_show_the_job_log_and_copy_the_report(
    harness: Harness,
) -> None:
    async for user, workspace_page in _open_page(harness):
        await _run_failing_import(user, workspace_page, harness)
        await user.should_see(marker="import-details")
        await user.should_see(marker="import-details-code")
        await user.should_see(marker="import-details-log")
        code = user.find(marker="import-details-code").elements.pop().text
        assert code.startswith("media.probe_failed · job ")
        log_path = Path(user.find(marker="import-details-log").elements.pop().text)
        assert log_path.is_file()
        assert log_path.parent == harness.paths.logs / "jobs"

        user.find(marker="open-log").click()
        await asyncio.sleep(0.05)
        assert harness.opener.calls == [log_path]

        user.find(marker="copy-diagnostics").click()
        await asyncio.sleep(0.05)
        await user.should_see(marker="diagnostics-dialog")
        user.find(marker="diagnostics-copy").click()
        await asyncio.sleep(0.05)

        assert harness.clipboard.texts == [REPORT]
        assert harness.job_ids
        assert harness.job_ids[0] is not None
        assert not (harness.root / "diagnostics.txt").exists()


async def test_browser_copy_has_the_exec_command_fallback() -> None:
    assert "navigator.clipboard.writeText" in BROWSER_COPY_JS
    assert "document.execCommand('copy')" in BROWSER_COPY_JS
