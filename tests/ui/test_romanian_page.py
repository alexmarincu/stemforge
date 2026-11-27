"""The Romanian page-render smoke test: main screen, About, and Export."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from media_fake import FakeMedia
from nicegui import app
from nicegui.testing.user_simulation import user_simulation

from stemforge import i18n
from stemforge.application import ServiceEngines, WorkspaceService
from stemforge.artifacts import ArtifactStore
from stemforge.integration.dialogs import normalize_selected_paths
from stemforge.jobs.runner import JobRunner
from stemforge.platform import preflight
from stemforge.platform.paths import AppPaths
from stemforge.settings import SettingsStore
from stemforge.ui import page

pytestmark = pytest.mark.ui


class FakeDialog:
    def __init__(self, selections: Sequence[Sequence[str]] = ()) -> None:
        self._selections = [tuple(item) for item in selections]

    async def choose_audio_files(self) -> tuple[Path, ...]:
        if not self._selections:
            return ()
        return normalize_selected_paths(self._selections.pop(0))


@dataclass
class Harness:
    root: Path
    paths: AppPaths = field(init=False)
    settings: SettingsStore = field(init=False)
    runner: JobRunner = field(init=False)
    service: WorkspaceService = field(init=False)
    media: FakeMedia = field(default_factory=FakeMedia)
    dialog: FakeDialog = field(default_factory=FakeDialog)
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
        )
        self.context = page.UiContext(
            service=self.service,
            dialogs=self.dialog,
        )

    def source_file(self, name: str) -> Path:
        path = self.root / name
        path.write_bytes(b"fake audio for " + name.encode())
        return path


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


def _only(user, marker: str):
    elements = list(user.find(marker=marker).elements)
    assert len(elements) == 1, f"expected exactly one {marker}, found {len(elements)}"
    return elements[0]


async def _finish_jobs(harness: Harness, user, workspace_page: page.WorkspacePage) -> None:
    for _ in range(300):
        if harness.runner.jobs():
            break
        await asyncio.sleep(0.01)
    assert await asyncio.to_thread(harness.runner.wait_idle, 10) is True
    with user:
        workspace_page.poll()


async def test_the_resource_warning_keeps_the_english_report_body_in_romanian(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness.context.preflight = preflight.PreflightReport(
        checks=(
            preflight.CheckResult(
                name=preflight.CHECK_RESOURCES,
                status=preflight.STATUS_WARN,
                message="This computer has 4.0 GB of RAM.",
                fatal=False,
            ),
        ),
        fail_on_degraded=False,
    )
    monkeypatch.setattr(i18n, "system_locale", lambda: "ro_RO.UTF-8")
    assert i18n.set_language("system", system_locale="ro_RO.UTF-8") == "ro"

    async for user, _workspace_page in _open_page(harness):
        await user.should_see(marker="resource-banner")
        await user.should_see("This computer has 4.0 GB of RAM.")
        assert _only(user, "resource-dismiss").text == i18n.t("common.close")


async def test_the_romanian_page_covers_the_main_screen_about_and_export(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(i18n, "system_locale", lambda: "ro_RO.UTF-8")
    assert i18n.set_language("system", system_locale="ro_RO.UTF-8") == "ro"
    app.config.language = i18n.framework_language(i18n.language())

    async for user, workspace_page in _open_page(harness):
        await user.should_see("Importă o piesă")
        await user.should_see(marker="dropzone")
        await user.should_see(
            "Trage un fișier local sau importă de pe YouTube. Totul rulează pe această mașină."
        )

        user.find(marker="settings-about").click()
        await user.should_see("Setări și despre")
        await user.should_see(marker="about-licence-authoritative")
        assert _only(user, "about-licence-authoritative").text == (
            "Versiunile în engleză ale fișierelor de licență sunt autoritative."
        )
        await user.should_see(marker="about-translation-credit")
        assert _only(user, "about-translation-credit").text == (
            "Traducere în română: StemForge contributors."
        )
        await user.should_see("Comportament")
        await user.should_see("Limbă")
        assert _only(user, "language-hint").text == "Modificările se aplică imediat."
        select = _only(user, "language-select")
        assert select.value == "system"
        assert select.options["system"] == "Sistem (Română)"
        assert select.options["en"] == "English"
        assert select.options["ro"] == "Română"
        user.find(marker="settings-close").click()

        harness.media.fail_probe = True
        harness.dialog._selections = [(str(harness.source_file("Broken.wav")),)]
        user.find(marker="add-audio").click()
        await _finish_jobs(harness, user, workspace_page)
        await user.should_see(marker="import-error")
        await user.should_see(
            "StemForge nu poate citi fișierul. Este deteriorat sau într-un format neacceptat."
        )

        harness.media.fail_probe = False
        gate = harness.media.block_decoding()
        harness.dialog._selections = [(str(harness.source_file("Song.wav")),)]
        user.find(marker="add-audio").click()
        assert await asyncio.to_thread(harness.media.decode_started.wait, 5) is True
        with user:
            workspace_page.poll()
        await user.should_see(marker="job-bar")
        assert _only(user, "job-stage").text.startswith("Se importă")
        gate.set()
        await _finish_jobs(harness, user, workspace_page)

        track = harness.service.snapshot().tracks[0]
        await user.should_see(marker=f"track-row-{track.track_id}")
        user.find(marker=f"track-row-{track.track_id}").click()
        user.find(marker="export-action").click()

        await user.should_see(marker="export-sheet")
        for label in (
            "Format",
            "Nume fișier",
            "Salvează în",
            "Fișier importat",
            "Salvează fișierul importat…",
        ):
            await user.should_see(label)
        assert _only(user, "export-source-label").text == "Original"
