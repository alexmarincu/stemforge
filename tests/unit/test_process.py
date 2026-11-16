"""``platform.process``: captured runs, cancellation, and tree termination."""

from __future__ import annotations

import sys
import time

import pytest

from stemforge.domain.cancellation import CancelToken
from stemforge.platform import process

pytestmark = pytest.mark.unit


def _python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_run_capture_returns_stdout_stderr_and_status() -> None:
    run = process.run_capture(_python("import sys; print('out'); print('err', file=sys.stderr)"))

    assert run.ok is True
    assert run.returncode == 0
    assert run.stdout.strip() == "out"
    assert run.stderr.strip() == "err"


def test_run_capture_reports_a_nonzero_exit() -> None:
    run = process.run_capture(_python("raise SystemExit(3)"))

    assert run.ok is False
    assert run.returncode == 3


def test_run_capture_streams_lines_to_the_callback() -> None:
    lines: list[str] = []

    process.run_capture(
        _python("print('one'); print('two')"),
        on_stdout_line=lines.append,
    )

    assert lines == ["one", "two"]


def test_run_capture_times_out_and_kills_the_process() -> None:
    started = time.monotonic()

    with pytest.raises(TimeoutError):
        process.run_capture(_python("import time; time.sleep(30)"), timeout_s=0.2)

    assert time.monotonic() - started < 10


def test_run_capture_honours_the_cancel_check_finally() -> None:
    token = CancelToken()
    token.cancel()

    with pytest.raises(Exception, match="Job cancelled"):
        process.run_capture(_python("import time; time.sleep(30)"), cancel_check=token.check)


def test_run_capture_kills_the_process_when_cancelled_mid_run() -> None:
    token = CancelToken()
    checks = {"count": 0}

    def check() -> None:
        checks["count"] += 1
        if checks["count"] >= 3:
            token.cancel()
        token.check()

    started = time.monotonic()
    with pytest.raises(Exception, match="Job cancelled"):
        process.run_capture(_python("import time; time.sleep(30)"), cancel_check=check)

    assert time.monotonic() - started < 15


def test_spawn_returns_a_running_process_with_a_text_pipe() -> None:
    proc = process.spawn(_python("print('hello')"))
    try:
        assert proc.stdout is not None
        assert process.iter_lines(proc.stdout).__next__() == "hello"
        assert proc.wait(timeout=10) == 0
    finally:
        process.terminate_tree(proc)


def test_terminate_tree_is_idempotent_after_exit() -> None:
    proc = process.spawn(_python("pass"))
    assert proc.wait(timeout=10) == 0

    process.terminate_tree(proc)
    process.terminate_tree(proc)
