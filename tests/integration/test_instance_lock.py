"""The single-instance lock refuses a second process and releases on exit."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# The harness prints exactly one word after acquire/release so the test can
# synchronize without sleeping.
HARNESS = """
import sys
import time
from pathlib import Path
from stemforge.platform.instance import InstanceLock

lock = InstanceLock(Path(sys.argv[1]))
acquired = lock.acquire()
print("acquired" if acquired else "busy", flush=True)
if acquired:
    hold = float(sys.argv[2])
    if hold < 0:
        sys.stdin.readline()
    else:
        time.sleep(hold)
lock.release()
"""


def _environment() -> dict[str, str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{PROJECT_ROOT / 'src'}{os.pathsep}{existing}" if existing else str(PROJECT_ROOT / "src")
    )
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _start(lock_path: Path, hold: float) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-c", HARNESS, str(lock_path), str(hold)],
        cwd=PROJECT_ROOT,
        env=_environment(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _run_once(lock_path: Path, hold: float = 0.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", HARNESS, str(lock_path), str(hold)],
        cwd=PROJECT_ROOT,
        env=_environment(),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.mark.integration
def test_a_second_process_is_refused_while_the_first_holds_the_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "config" / "instance.lock"
    first = _start(lock_path, hold=-1)
    try:
        assert first.stdout is not None
        assert first.stdout.readline().strip() == "acquired"

        second = _run_once(lock_path)

        assert second.returncode == 0
        assert second.stdout.strip() == "busy"
        assert first.poll() is None

        assert first.stdin is not None
        first.stdin.write("\n")
        first.stdin.flush()
        assert first.wait(timeout=30) == 0
        assert first.stdout.read().strip() == ""
        assert first.stderr is not None
        assert first.stderr.read() == ""
    finally:
        if first.poll() is None:
            first.kill()
            first.wait(timeout=30)


@pytest.mark.integration
def test_the_lock_is_available_again_after_the_holder_exits(tmp_path: Path) -> None:
    lock_path = tmp_path / "config" / "instance.lock"

    first = _run_once(lock_path)
    second = _run_once(lock_path)

    assert first.returncode == 0
    assert first.stdout.strip() == "acquired"
    assert second.returncode == 0
    assert second.stdout.strip() == "acquired"
