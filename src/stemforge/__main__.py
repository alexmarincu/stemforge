"""Executable entry point.

The frozen-build contract (ticket 06 §11) requires ``freeze_support()`` to be the
first statement in the main guard, and the heavy stack (NiceGUI, torch, Demucs) to
stay out of this module's imports. ``--self-test`` is a headless spine check;
``--preflight``, ``--smoke-test``, and ``--worker`` remain safe stubs until
tickets 07 and 10 fill them in.
"""

from __future__ import annotations

import argparse
import logging
import multiprocessing
from collections.abc import Sequence

from stemforge import __version__


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stemforge",
        description="StemForge: local stem separation, pitch shifting, and export.",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="check the bundled runtime and vendor binaries, then exit",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="boot the local server and check that it responds, then exit",
    )
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="print the startup environment checks, then exit",
    )
    parser.add_argument(
        "--import",
        dest="import_path",
        metavar="PATH",
        help="import one local audio file headlessly, then exit",
    )
    parser.add_argument(
        "--worker",
        metavar="NAME",
        help="run an internal worker (separation); dispatched by the frozen binary",
    )
    return parser


def _run_self_test() -> int:
    """Resolve paths, load settings, configure logs, and build the service headless."""
    from stemforge.application import WorkspaceService
    from stemforge.logging_setup import configure_logging
    from stemforge.platform.environment import configure
    from stemforge.settings import SettingsStore

    paths = configure()
    settings = SettingsStore(paths)
    configure_logging(paths)
    settings.save()
    service = WorkspaceService(settings, paths=paths)
    snapshot = service.snapshot()

    logging.getLogger("stemforge").info(
        "self-test: data=%s cache=%s logs=%s models=%s config=%s",
        paths.data,
        paths.cache,
        paths.logs,
        paths.models,
        paths.config,
    )
    logging.getLogger("stemforge").info(
        "self-test ok: workspace=%d tracks, recent=%d",
        len(snapshot),
        len(service.recent_files()),
    )

    print(f"StemForge {__version__} self-test")
    print(f"data: {paths.data}")
    print(f"cache: {paths.cache}")
    print(f"logs: {paths.logs}")
    print(f"models: {paths.models}")
    print(f"config: {paths.config}")
    print(f"webview: {paths.webview}")
    print(f"settings: {settings.path}")
    print(f"workspace: {len(snapshot)} tracks")
    print(f"recent: {len(service.recent_files())}")
    print("self-test ok")
    return 0


def _one_line(text: str | None, *, limit: int = 240) -> str:
    """Collapse a coded error's detail to one printable line."""
    if not text:
        return "see the job log"
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else f"{collapsed[: limit - 3]}..."


def _run_import(path: str) -> int:
    """Run one local import Job synchronously and print a one-line result."""
    from pathlib import Path

    from stemforge import errors
    from stemforge.application import ServiceEngines, WorkspaceService
    from stemforge.engines.media import FfmpegTool
    from stemforge.errors import StemForgeError
    from stemforge.jobs import JobState
    from stemforge.logging_setup import configure_logging
    from stemforge.platform.environment import configure
    from stemforge.settings import SettingsStore

    paths = configure()
    settings = SettingsStore(paths)
    configure_logging(paths)
    service = WorkspaceService(
        settings,
        paths=paths,
        engines=ServiceEngines(media=FfmpegTool()),
    )
    runner = service.runner
    runner.start()
    try:
        job_id = service.import_local(path)
        runner.wait_idle()
        snapshot = runner.snapshot(job_id)
    except StemForgeError as error:
        print(f"error {error.code}: {_one_line(error.detail)}")
        return 1
    except Exception as error:
        print(f"error {errors.INTERNAL_UNEXPECTED}: {_one_line(str(error))}")
        return 1
    finally:
        runner.stop()

    if snapshot.state is JobState.SUCCEEDED:
        source_id = snapshot.result_keys.get("source_id", "")
        track_id = snapshot.result_keys.get("track_id", "")
        track = next(
            (item for item in service.snapshot().tracks if item.track_id == track_id),
            None,
        )
        title = track.title if track is not None else Path(path).stem
        print(f'imported "{title}" source={source_id} track={track_id}')
        return 0
    if snapshot.state is JobState.CANCELLED:
        print(f"cancelled {snapshot.kind.value} job {snapshot.job_id}")
        return 2

    code = snapshot.error_code or errors.INTERNAL_UNEXPECTED
    print(f"error {code}: {_one_line(snapshot.error_detail)}")
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the entry-point flags and return a process exit code."""
    args = _build_parser().parse_args(argv)

    if args.worker is not None:
        print(f"StemForge {__version__}: worker {args.worker} is a safe stub in this build.")
        return 0
    if args.self_test:
        return _run_self_test()
    if args.import_path is not None:
        return _run_import(args.import_path)

    mode = "ui"
    if args.smoke_test:
        mode = "smoke-test"
    elif args.preflight:
        mode = "preflight"

    print(f"StemForge {__version__}: {mode} is not implemented in this scaffold.")
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
