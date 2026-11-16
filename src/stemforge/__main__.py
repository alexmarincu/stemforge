"""Executable entry point.

The frozen-build contract (ticket 06 §11) requires ``freeze_support()`` to be the
first statement in the main guard, and the heavy stack (NiceGUI, torch, Demucs) to
stay out of this module's imports. ``--self-test``, ``--smoke-test``, and
``--smoke-separation`` live in :mod:`stemforge.smoke`; ``--preflight`` and
``--worker`` remain safe stubs until tickets 14 and 13 fill them in.
"""

from __future__ import annotations

import argparse
import multiprocessing
from collections.abc import Sequence
from pathlib import Path

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
        "--smoke-separation",
        action="store_true",
        help="run a one-second separation through the worker, then exit",
    )
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="print the startup environment checks, then exit",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="only report preflight failures (used by run.sh)",
    )
    parser.add_argument(
        "--report",
        metavar="PATH",
        type=Path,
        help="write the JSON report of a test flag to PATH",
    )
    parser.add_argument(
        "--worker",
        metavar="NAME",
        help="run an internal worker (separation); dispatched by the frozen binary",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the entry-point flags and return a process exit code."""
    args = _build_parser().parse_args(argv)

    if args.worker is not None:
        print(f"StemForge {__version__}: worker {args.worker} is a safe stub in this build.")
        return 0

    if args.self_test or args.smoke_test or args.smoke_separation:
        from stemforge import smoke

        if args.self_test:
            return smoke.self_test(report=args.report)
        if args.smoke_test:
            return smoke.smoke_test(report=args.report)
        return smoke.smoke_separation(report=args.report)

    mode = "ui"
    if args.preflight:
        mode = "preflight"

    print(f"StemForge {__version__}: {mode} is not implemented in this scaffold.")
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
