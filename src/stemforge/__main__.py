"""Executable entry point.

The frozen-build contract (ticket 06 §11) requires ``freeze_support()`` to be the
first statement in the main guard, and the heavy stack (NiceGUI, torch, Demucs) to
stay out of this module's imports. Ticket 10 fills in ``--self-test``,
``--smoke-test``, and ``--worker`` dispatch; this scaffold parses the flags and
returns a safe not-implemented result.
"""

from __future__ import annotations

import argparse
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


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the entry-point flags and return a process exit code."""
    args = _build_parser().parse_args(argv)

    mode = "ui"
    if args.worker is not None:
        mode = f"worker {args.worker}"
    elif args.self_test:
        mode = "self-test"
    elif args.smoke_test:
        mode = "smoke-test"
    elif args.preflight:
        mode = "preflight"

    print(f"StemForge {__version__}: {mode} is not implemented in this scaffold.")
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
