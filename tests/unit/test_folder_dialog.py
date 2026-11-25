"""The native folder-dialog wrapper: trusted absolute existing directories only.

The backend is injected so these tests never import pywebview's window stack and
never open a dialog. A folder choice is seedable but never created by the app.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from stemforge.integration.dialogs import NativeFolderDialog, normalize_folder_path

pytestmark = pytest.mark.unit


class FakeFolderBackend:
    """Records the folder request and returns a scripted selection."""

    def __init__(self, result: Sequence[str] | None) -> None:
        self.result = result
        self.calls: list[str | None] = []

    async def choose_folder(self, *, directory: str | None) -> Sequence[str] | None:
        self.calls.append(directory)
        return self.result


async def test_a_folder_choice_is_a_trusted_absolute_directory(tmp_path: Path) -> None:
    backend = FakeFolderBackend([str(tmp_path)])
    dialog = NativeFolderDialog(backend=backend)

    chosen = await dialog.choose_folder(directory=tmp_path)

    assert chosen == tmp_path
    assert backend.calls == [str(tmp_path)]


async def test_cancel_returns_none() -> None:
    dialog = NativeFolderDialog(backend=FakeFolderBackend(None))

    assert await dialog.choose_folder() is None


async def test_an_empty_result_returns_none() -> None:
    dialog = NativeFolderDialog(backend=FakeFolderBackend([]))

    assert await dialog.choose_folder() is None


async def test_a_missing_seed_directory_is_not_sent_to_pywebview(tmp_path: Path) -> None:
    backend = FakeFolderBackend([str(tmp_path)])
    dialog = NativeFolderDialog(backend=backend)

    await dialog.choose_folder(directory=tmp_path / "gone")

    assert backend.calls == [None]


@pytest.mark.parametrize(
    "fake_path",
    [
        r"C:\fakepath\exports",
        "file:///home/me/exports",
        "https://example.test/exports",
        "relative/exports",
        "",
        "   ",
    ],
)
async def test_browser_fake_paths_are_never_accepted(fake_path: str) -> None:
    dialog = NativeFolderDialog(backend=FakeFolderBackend([fake_path]))

    assert await dialog.choose_folder() is None


async def test_a_file_is_not_a_folder(tmp_path: Path) -> None:
    target = tmp_path / "song.wav"
    target.write_bytes(b"not a folder")
    dialog = NativeFolderDialog(backend=FakeFolderBackend([str(target)]))

    assert await dialog.choose_folder() is None


def test_normalize_folder_path_requires_an_existing_directory(tmp_path: Path) -> None:
    assert normalize_folder_path(str(tmp_path)) == tmp_path
    assert normalize_folder_path(str(tmp_path / "gone")) is None
    assert normalize_folder_path(None) is None
