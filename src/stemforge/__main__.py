"""Executable entry point.

The frozen-build contract (ticket 06 §11) requires ``freeze_support()`` to be the
first statement in the main guard, and the heavy stack (NiceGUI, torch, Demucs) to
stay out of this module's imports. ``--self-test``, ``--smoke-test``, and
``--smoke-separation`` live in :mod:`stemforge.smoke`; ``--preflight`` runs the
startup checks and never opens a window. A normal launch takes the
single-instance lock before the preflight report, then stops at the ``_run_ui``
seam until the UI lands.
"""

from __future__ import annotations

import argparse
import multiprocessing
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from stemforge import __version__
from stemforge.platform import preflight

if TYPE_CHECKING:
    from stemforge.platform.paths import AppPaths


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
        help="suppress the human-readable preflight report",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the preflight report as JSON",
    )
    parser.add_argument(
        "--report",
        metavar="PATH",
        type=Path,
        help="write the JSON report to PATH",
    )
    parser.add_argument(
        "--worker",
        metavar="NAME",
        help="run an internal worker (separation); dispatched by the frozen binary",
    )
    return parser


def _configure() -> AppPaths:
    from stemforge.platform.environment import configure

    return configure()


def _paths_without_creating() -> AppPaths:
    from stemforge.platform.paths import AppPaths

    return AppPaths.from_environment()


def _configuration_failure(args: argparse.Namespace, error: OSError) -> int:
    """Turn a failed ``environment.configure()`` into the storage fatal result."""
    paths = _paths_without_creating()
    target = _storage_error_path(paths, error)
    report = preflight.PreflightReport(
        checks=(
            preflight.CheckResult(
                name=preflight.CHECK_STORAGE,
                status=preflight.STATUS_FAIL,
                message=f"StemForge can't write to {target}.",
                fatal=True,
            ),
        ),
        system=sys.platform,
        fail_on_degraded=False,
    )
    diagnostics = _diagnostics_text(report, paths)
    if args.preflight:
        if args.report is not None:
            preflight.write_report(args.report, report)
        _show_fatal(report, paths, report_path=args.report, diagnostics=diagnostics)
        if args.json:
            print(report.to_json())
        elif not args.quiet:
            print(preflight.format_human(report))
            headline, action = _fatal_copy(report, _fatal_code(report))
            print()
            print(headline)
            print(action)
    else:
        _show_fatal(report, paths, report_path=args.report, diagnostics=diagnostics)
    return report.exit_code


def _storage_error_path(paths: AppPaths, error: OSError) -> Path:
    filename = getattr(error, "filename", None)
    if filename:
        return Path(filename)
    override = os.environ.get("STEMFORGE_HOME")
    if override:
        return Path(override).expanduser()
    return paths.data


def _run_preflight(paths: AppPaths, args: argparse.Namespace) -> int:
    """Run the CLI preflight, print or write the report, and deliver a fatal box."""
    report = preflight.run_preflight(paths, fail_on_degraded=True)
    report_json = report.to_json()
    diagnostics = _diagnostics_text(report, paths)
    if args.report is not None:
        preflight.write_report(args.report, report)
    if report.fatal:
        _show_fatal(report, paths, report_path=args.report, diagnostics=diagnostics)
    if args.json:
        print(report_json)
    elif not args.quiet:
        print(preflight.format_human(report))
        if report.fatal or report.degraded:
            headline, action = _fatal_copy(report, _fatal_code(report))
            print()
            print(headline)
            print(action)
    return report.exit_code


def _diagnostics_text(report: preflight.PreflightReport, paths: AppPaths) -> str:
    """The text ``Copy details`` falls back to when no report file was written."""
    lines = [
        f"StemForge {__version__} preflight",
        f"data={paths.data}",
        f"cache={paths.cache}",
        f"logs={paths.logs}",
        f"models={paths.models}",
        f"config={paths.config}",
        preflight.format_human(report),
    ]
    return "\n".join(lines)


def _run_ui(paths: AppPaths, report: preflight.PreflightReport) -> int:
    """The window seam; ticket 8 replaces this with ``ui.run(native=True)``."""
    del paths
    print(f"StemForge {__version__}: preflight ok.")
    if report.degraded:
        print("Preview isn't available on this system; starting in degraded mode.")
    print("The UI is not implemented in this scaffold.")
    return 0


def _fatal_code(report: preflight.PreflightReport) -> str:
    from stemforge import errors

    check = report.first_fatal
    if check is None:
        if report.degraded:
            return errors.PREFLIGHT_PREVIEW_UNAVAILABLE
        return errors.INTERNAL_UNEXPECTED
    if check.name == preflight.CHECK_PLATFORM:
        return errors.PREFLIGHT_PLATFORM_UNSUPPORTED
    if check.name == preflight.CHECK_WINDOW:
        if report.system.startswith("win"):
            return errors.PREFLIGHT_WEBVIEW2_MISSING
        return errors.PREFLIGHT_WINDOW_UNAVAILABLE
    if check.name == preflight.CHECK_BUNDLE:
        return errors.PREFLIGHT_BUNDLE_DAMAGED
    if check.name == preflight.CHECK_STORAGE:
        return errors.PREFLIGHT_STORAGE_UNWRITABLE
    return errors.INTERNAL_UNEXPECTED


def _fatal_copy(report: preflight.PreflightReport, code: str) -> tuple[str, str]:
    from stemforge import errors
    from stemforge.ui import formatting

    check = report.first_fatal
    path = None
    if check is not None and code == errors.PREFLIGHT_STORAGE_UNWRITABLE:
        path = _storage_path_from_message(check.message)
    copy = formatting.fatal_copy(code, path=path)
    return copy.headline, copy.action


def _storage_path_from_message(message: str) -> str | None:
    prefix = "StemForge can't write to "
    if message.startswith(prefix) and message.endswith("."):
        return message[len(prefix) : -1]
    return None


def _show_fatal(
    report: preflight.PreflightReport,
    paths: AppPaths,
    *,
    report_path: Path | None,
    diagnostics: str,
) -> None:
    code = _fatal_code(report)
    sentence, fix_line = _fatal_copy(report, code)
    log_path = paths.logs / "stemforge.log"
    copy_text = diagnostics
    if report_path is not None and report_path.is_file():
        copy_text = report_path.read_text(encoding="utf-8")
    message = preflight.build_fatal_message(
        code=code,
        sentence=sentence,
        fix_line=fix_line,
        log_path=log_path if log_path.is_file() else None,
        copy_text=copy_text,
    )
    preflight.show_fatal_message(message)


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the entry-point flags and return a process exit code."""
    args = _build_parser().parse_args(argv)
    try:
        paths = _configure()
    except OSError as error:
        return _configuration_failure(args, error)

    # Ticket 06 §7 order: worker dispatch happens before the instance lock, so a
    # second process can still run the bundled Separation worker.
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

    if args.preflight:
        return _run_preflight(paths, args)

    from stemforge.platform.instance import InstanceLock

    lock = InstanceLock(paths.config / "instance.lock")
    if not lock.acquire():
        preflight.show_already_running()
        return 0
    try:
        report = preflight.run_preflight(paths, fail_on_degraded=False)
        if args.report is not None:
            preflight.write_report(args.report, report)
        if report.fatal:
            _show_fatal(
                report,
                paths,
                report_path=args.report,
                diagnostics=_diagnostics_text(report, paths),
            )
            return report.exit_code
        return _run_ui(paths, report)
    finally:
        lock.release()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
