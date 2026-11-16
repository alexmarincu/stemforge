"""``JobRunner``: FIFO execution, dedupe, cancellation, snapshots, and logs.

The runner is the only thing that owns Job state, so these tests drive it only
through its public API and observe immutable snapshots (ticket 06 §3). Jobs are
plain callables that coordinate through events, which proves one-active-at-a-time
without sleeping on a loaded machine.
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path

import pytest

from stemforge import errors
from stemforge.errors import StemForgeError
from stemforge.jobs import (
    CancelToken,
    Job,
    JobContext,
    JobKind,
    JobRunner,
    JobSnapshot,
    JobState,
)
from stemforge.logging_setup import JOB_LOG_MAX_AGE_DAYS, JOB_LOG_RETENTION, sweep_job_logs
from stemforge.platform.paths import AppPaths

pytestmark = pytest.mark.unit

Run = Callable[[JobContext], Mapping[str, str] | None]


@pytest.fixture
def paths(tmp_path: Path) -> AppPaths:
    return AppPaths.for_home(tmp_path / "home").ensure()


@pytest.fixture
def runner(paths: AppPaths) -> Iterator[JobRunner]:
    instance = JobRunner(paths=paths)
    yield instance
    instance.stop(timeout=5)


def _job(
    identity: tuple[str, ...],
    run: Run,
    *,
    kind: JobKind = JobKind.IMPORT_LOCAL,
    title: str = "",
) -> Job:
    return Job(kind=kind, identity=identity, run=run, title=title)


def _blocking_job(
    name: str,
    *,
    started: threading.Event,
    release: threading.Event,
    order: list[str] | None = None,
) -> Run:
    def run(ctx: JobContext) -> Mapping[str, str] | None:
        if order is not None:
            order.append(name)
        started.set()
        if not release.wait(timeout=5):
            raise RuntimeError(f"{name} was never released")
        return {"name": name}

    return run


def _spawn_log(paths: AppPaths, name: str, age_days: float) -> Path:
    jobs_dir = paths.logs / "jobs"
    jobs_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = jobs_dir / name
    target.write_text("stale\n", encoding="utf-8")
    stamp = time.time() - age_days * 86_400
    target.touch()
    os.utime(target, (stamp, stamp))
    return target


# --- FIFO and concurrency --------------------------------------------------


def test_jobs_run_fifo_and_only_one_at_a_time(runner: JobRunner) -> None:
    order: list[str] = []
    started = {name: threading.Event() for name in ("a", "b", "c")}
    release = {name: threading.Event() for name in ("a", "b", "c")}

    runner.start()
    for name in ("a", "b", "c"):
        runner.submit(
            _job(
                (name,),
                _blocking_job(name, started=started[name], release=release[name], order=order),
            )
        )

    assert started["a"].wait(5)
    assert not started["b"].wait(0.2)
    assert order == ["a"]

    release["a"].set()
    assert started["b"].wait(5)
    assert not started["c"].wait(0.2)
    assert order == ["a", "b"]

    release["b"].set()
    assert started["c"].wait(5)
    release["c"].set()

    assert runner.wait_idle(timeout=5) is True
    assert order == ["a", "b", "c"]


def test_dedupe_returns_the_active_job_id_and_runs_once(runner: JobRunner) -> None:
    started = threading.Event()
    release = threading.Event()
    runs = 0
    lock = threading.Lock()

    def run(ctx: JobContext) -> Mapping[str, str] | None:
        nonlocal runs
        with lock:
            runs += 1
        started.set()
        assert release.wait(5)
        return {}

    runner.start()
    first = runner.submit(_job(("same", "key"), run))
    second = runner.submit(_job(("same", "key"), run))

    assert first == second
    assert started.wait(5)
    release.set()
    assert runner.wait_idle(timeout=5)
    assert runs == 1
    assert runner.snapshot(first).state is JobState.SUCCEEDED


def test_a_finished_job_can_be_submitted_again_with_a_new_id(runner: JobRunner) -> None:
    started = threading.Event()

    def run(ctx: JobContext) -> Mapping[str, str] | None:
        started.set()
        return {}

    runner.start()
    first = runner.submit(_job(("again",), run))
    assert started.wait(5)
    assert runner.wait_idle(timeout=5)

    second = runner.submit(_job(("again",), run))

    assert second != first
    assert runner.wait_idle(timeout=5)
    assert runner.snapshot(first).state is JobState.SUCCEEDED
    assert runner.snapshot(second).state is JobState.SUCCEEDED


def test_jobs_are_listed_newest_first(runner: JobRunner) -> None:
    runner.start()
    job_ids = [runner.submit(_job((str(index),), lambda ctx: {})) for index in range(3)]

    assert runner.wait_idle(timeout=5)

    assert [snapshot.job_id for snapshot in runner.jobs()] == list(reversed(job_ids))


def test_finished_history_is_capped_at_twenty(runner: JobRunner) -> None:
    runner.start()
    job_ids = [runner.submit(_job((str(index),), lambda ctx: {})) for index in range(25)]

    assert runner.wait_idle(timeout=5)

    remembered = [snapshot.job_id for snapshot in runner.jobs()]
    assert len(remembered) == 20
    assert remembered == list(reversed(job_ids[-20:]))
    with pytest.raises(KeyError):
        runner.snapshot(job_ids[0])


# --- Cancellation ----------------------------------------------------------


def test_cancelling_a_queued_job_removes_it_before_it_starts(runner: JobRunner) -> None:
    blocker_started = threading.Event()
    blocker_release = threading.Event()
    queued_ran = threading.Event()

    runner.start()
    runner.submit(
        _job(
            ("blocker",),
            _blocking_job("blocker", started=blocker_started, release=blocker_release),
        )
    )
    assert blocker_started.wait(5)

    queued = runner.submit(_job(("queued",), lambda ctx: queued_ran.set()))

    assert runner.cancel(queued) is True
    snapshot = runner.snapshot(queued)
    assert snapshot.state is JobState.CANCELLED
    assert snapshot.started_at is None
    assert snapshot.finished_at is not None
    assert snapshot.log_path is None
    assert snapshot.error_code is None

    blocker_release.set()
    assert runner.wait_idle(timeout=5)
    assert not queued_ran.is_set()


def test_cancelling_a_running_job_records_cancelled_and_keeps_the_runner_alive(
    runner: JobRunner,
) -> None:
    started = threading.Event()
    later_ran = threading.Event()

    def run(ctx: JobContext) -> Mapping[str, str] | None:
        started.set()
        while True:
            ctx.cancel.check()
            time.sleep(0.005)

    runner.start()
    job_id = runner.submit(_job(("cancel-me",), run))
    assert started.wait(5)
    runner.submit(_job(("later",), lambda ctx: later_ran.set()))

    assert runner.cancel(job_id) is True
    assert runner.wait_idle(timeout=5)

    snapshot = runner.snapshot(job_id)
    assert snapshot.state is JobState.CANCELLED
    assert snapshot.error_code is None
    assert snapshot.finished_at is not None
    assert later_ran.wait(5)
    assert runner.cancel(job_id) is False
    assert runner.cancel("unknown-job") is False


# --- Snapshots and failures ------------------------------------------------


def test_snapshots_poll_queued_running_and_succeeded_states(runner: JobRunner) -> None:
    started = threading.Event()
    release = threading.Event()

    def run(ctx: JobContext) -> Mapping[str, str] | None:
        ctx.progress(0.25, stage="Decoding", detail="half")
        started.set()
        assert release.wait(5)
        ctx.progress(0.1, stage="Decoding")
        ctx.progress(1.0, stage="Saving")
        return {"source_id": "src-1", "track_id": "trk-1"}

    job_id = runner.submit(_job(("snapshot",), run))
    queued = runner.snapshot(job_id)
    assert queued.state is JobState.QUEUED
    assert queued.progress == 0.0
    assert queued.started_at is None

    runner.start()
    assert started.wait(5)
    running = runner.snapshot(job_id)
    assert running.state is JobState.RUNNING
    assert running.progress == 0.25
    assert running.stage == "Decoding"
    assert running.detail == "half"
    assert running.started_at is not None
    assert running.finished_at is None

    release.set()
    assert runner.wait_idle(timeout=5)
    finished = runner.snapshot(job_id)
    assert finished.state is JobState.SUCCEEDED
    assert finished.progress == 1.0
    assert finished.stage == "Saving"
    assert finished.finished_at is not None
    assert dict(finished.result_keys) == {"source_id": "src-1", "track_id": "trk-1"}


def test_a_failed_job_is_coded_and_isolated_from_the_next_job(runner: JobRunner) -> None:
    later_ran = threading.Event()

    def fail(ctx: JobContext) -> Mapping[str, str] | None:
        ctx.progress(0.4, stage="Decoding")
        raise StemForgeError(
            errors.DECODE_FAILED,
            "ffmpeg could not decode the source",
            retryable=True,
            detail="boom",
        )

    runner.start()
    failed_id = runner.submit(_job(("fails",), fail))
    later_id = runner.submit(_job(("later",), lambda ctx: later_ran.set()))

    assert runner.wait_idle(timeout=5)

    failed = runner.snapshot(failed_id)
    assert failed.state is JobState.FAILED
    assert failed.error_code == errors.DECODE_FAILED
    assert failed.error_detail == "boom"
    assert failed.retryable is True
    assert failed.progress == 0.4
    assert later_ran.wait(5)
    assert runner.snapshot(later_id).state is JobState.SUCCEEDED


def test_an_unexpected_exception_becomes_internal_unexpected(runner: JobRunner) -> None:
    def explode(ctx: JobContext) -> Mapping[str, str] | None:
        raise RuntimeError("kaboom")

    runner.start()
    job_id = runner.submit(_job(("explodes",), explode))

    assert runner.wait_idle(timeout=5)

    snapshot = runner.snapshot(job_id)
    assert snapshot.state is JobState.FAILED
    assert snapshot.error_code == errors.INTERNAL_UNEXPECTED
    assert snapshot.error_detail is not None
    assert "RuntimeError" in snapshot.error_detail
    assert "kaboom" in snapshot.error_detail


def test_snapshot_raises_for_an_unknown_job(runner: JobRunner) -> None:
    with pytest.raises(KeyError):
        runner.snapshot("nope")


# --- Idle waits ------------------------------------------------------------


def test_wait_idle_times_out_while_a_job_runs_then_succeeds(runner: JobRunner) -> None:
    started = threading.Event()
    release = threading.Event()

    runner.start()
    runner.submit(_job(("slow",), _blocking_job("slow", started=started, release=release)))
    assert started.wait(5)

    assert runner.wait_idle(timeout=0.05) is False

    release.set()
    assert runner.wait_idle(timeout=5) is True


# --- Logs and retention ----------------------------------------------------


def test_each_job_writes_one_capped_log_file(runner: JobRunner, paths: AppPaths) -> None:
    def run(ctx: JobContext) -> Mapping[str, str] | None:
        ctx.log.info("engine stderr verbatim")
        return {}

    runner.start()
    job_id = runner.submit(_job(("logged",), run))
    assert runner.wait_idle(timeout=5)

    log_path = runner.snapshot(job_id).log_path
    assert log_path is not None
    assert log_path.is_file()
    assert log_path.parent == paths.logs / "jobs"
    assert re.fullmatch(rf"\d{{8}}T\d{{6}}Z-{re.escape(job_id)}\.log", log_path.name)

    text = log_path.read_text(encoding="utf-8")
    assert "engine stderr verbatim" in text
    assert "started" in text
    assert "succeeded" in text


def test_sweep_keeps_the_newest_twenty_job_logs(paths: AppPaths) -> None:
    logs = [
        _spawn_log(paths, f"20260101T0000{index:02d}Z-job-{index:02d}.log", 0.1 + index / 100)
        for index in range(25)
    ]

    expected = {
        path.name for path in sorted(logs, key=lambda item: item.stat().st_mtime, reverse=True)[:20]
    }

    removed = sweep_job_logs(paths)

    remaining = sorted((paths.logs / "jobs").glob("*.log"))
    assert len(remaining) == JOB_LOG_RETENTION == 20
    assert len(removed) == 5
    assert {path.name for path in remaining} == expected


def test_sweep_deletes_logs_older_than_seven_days(paths: AppPaths) -> None:
    recent = _spawn_log(paths, "20261116T120000Z-job-recent.log", 0.5)
    old = _spawn_log(paths, "20261101T120000Z-job-old.log", 8.0)

    removed = sweep_job_logs(paths)

    assert (paths.logs / "jobs" / recent.name).is_file()
    assert not (paths.logs / "jobs" / old.name).exists()
    assert set(removed) == {old}
    assert JOB_LOG_MAX_AGE_DAYS == 7


def test_starting_the_runner_sweeps_job_logs_once(paths: AppPaths) -> None:
    kept = _spawn_log(paths, "20261116T120000Z-job-kept.log", 0.5)
    old = _spawn_log(paths, "20261101T120000Z-job-old.log", 8.0)

    runner = JobRunner(paths=paths)
    try:
        runner.start()
    finally:
        runner.stop(timeout=5)

    assert (paths.logs / "jobs" / kept.name).is_file()
    assert not (paths.logs / "jobs" / old.name).exists()


def test_cancel_token_is_still_reexported_from_the_job_module() -> None:
    from stemforge.jobs.job import CancelToken as DocumentedToken

    assert DocumentedToken is CancelToken


def test_snapshot_is_an_immutable_value() -> None:
    snapshot = JobSnapshot(job_id="j", kind=JobKind.IMPORT_LOCAL, state=JobState.QUEUED)

    with pytest.raises(AttributeError):
        snapshot.state = JobState.FAILED  # type: ignore[misc]
    assert snapshot.progress == 0.0
