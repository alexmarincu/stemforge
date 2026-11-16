"""The in-process shape of the single-instance lock."""

from __future__ import annotations

from pathlib import Path

import pytest

from stemforge.platform.instance import InstanceAlreadyRunning, InstanceLock


@pytest.mark.unit
def test_context_manager_acquires_and_releases(tmp_path: Path) -> None:
    lock_path = tmp_path / "config" / "instance.lock"

    with InstanceLock(lock_path) as lock:
        assert lock.held is True
        assert lock_path.is_file()

    assert lock.held is False
    with InstanceLock(lock_path) as again:
        assert again.held is True


@pytest.mark.unit
def test_entering_a_held_lock_raises_instead_of_blocking(tmp_path: Path) -> None:
    lock_path = tmp_path / "instance.lock"

    with (
        InstanceLock(lock_path),
        pytest.raises(InstanceAlreadyRunning),
        InstanceLock(lock_path),
    ):
        pass


@pytest.mark.unit
def test_release_is_idempotent(tmp_path: Path) -> None:
    lock = InstanceLock(tmp_path / "instance.lock")

    assert lock.acquire() is True
    lock.release()
    lock.release()

    assert lock.held is False
