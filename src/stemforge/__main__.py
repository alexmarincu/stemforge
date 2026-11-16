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


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the entry-point flags and return a process exit code."""
    args = _build_parser().parse_args(argv)

    if args.worker is not None:
        print(f"StemForge {__version__}: worker {args.worker} is a safe stub in this build.")
        return 0
    if args.self_test:
        return _run_self_test()

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
