"""Executable entry point.

The frozen-build contract (ticket 06 §11) requires ``freeze_support()`` to be the
first statement in the main guard, and the heavy stack (NiceGUI, torch, Demucs) to
stay out of this module's imports. ``--self-test``, ``--smoke-test``, and
``--smoke-separation`` live in :mod:`stemforge.smoke`; ``--preflight`` runs the
startup checks and never opens a window; ``--import`` and ``--install-model`` run
one Job headlessly. A normal launch takes the single-instance lock before the
preflight report, then opens the native workspace window.
"""

from __future__ import annotations

import argparse
import logging
import multiprocessing
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from stemforge import __version__
from stemforge.platform import preflight

if TYPE_CHECKING:
    from stemforge.application import WorkspaceService
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
        "--import",
        dest="import_path",
        metavar="PATH",
        help="import one local audio file headlessly, then exit",
    )
    parser.add_argument(
        "--install-model",
        action="store_true",
        help="download and verify the pinned htdemucs model, then exit",
    )
    parser.add_argument(
        "--accept-model-licence",
        action="store_true",
        help="accept the model-weights licence notice for an unattended --install-model",
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
    if args.import_path is not None:
        from stemforge import errors

        print(f"error {errors.PREFLIGHT_STORAGE_UNWRITABLE}: {_one_line(str(error))}")
        return 1
    if args.install_model:
        from stemforge import errors

        print(f"error {errors.PREFLIGHT_STORAGE_UNWRITABLE}: {_one_line(str(error))}")
        return 1
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
    """Open the native workspace window and run the app until it closes."""
    from nicegui import app, ui

    from stemforge.application import ServiceEngines, WorkspaceService
    from stemforge.engines.media import FfmpegTool
    from stemforge.integration.dialogs import NativeOpenDialog
    from stemforge.integration.media import register_media_route
    from stemforge.integration.window import DEFAULT_WINDOW_SIZE, configure_window
    from stemforge.logging_setup import configure_logging
    from stemforge.platform import resources
    from stemforge.settings import SettingsStore
    from stemforge.ui import page

    settings = SettingsStore(paths)
    configure_logging(paths)
    service = WorkspaceService(
        settings,
        paths=paths,
        engines=ServiceEngines(media=FfmpegTool()),
    )
    # Ticket 07 §7: the sweep runs once behind the single-instance lock, before
    # the Workspace is built; its residue alone triggers the recovery banner.
    sweep = service.sweep_stale()
    page.configure(
        page.UiContext(
            service=service,
            dialogs=NativeOpenDialog(),
            show_recovery=sweep.deleted_anything,
            preflight=report,
        )
    )
    configure_window(paths)
    # Ticket 05 §6: one cache-rooted route, registered before the window starts.
    register_media_route(app, paths.cache)

    runner = service.runner

    def shutdown() -> None:
        runner.stop()
        try:
            settings.save()
        except OSError:
            logging.getLogger("stemforge").warning("could not save settings on shutdown")

    app.on_startup(runner.start)
    app.on_shutdown(shutdown)
    favicon = resources.assets_dir() / "stemforge-512.png"
    try:
        ui.run(
            native=True,
            reload=False,
            show=False,
            window_size=DEFAULT_WINDOW_SIZE,
            title=page.TITLE,
            favicon=favicon if favicon.is_file() else None,
            fastapi_docs=False,
            endpoint_documentation="none",
            show_welcome_message=False,
            uvicorn_logging_level="warning",
        )
    finally:
        runner.stop()
    return 0


def _one_line(text: str | None, *, limit: int = 240) -> str:
    """Collapse a coded error's detail to one printable line."""
    if not text:
        return "see the job log"
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else f"{collapsed[: limit - 3]}..."


def _run_import(path: str, paths: AppPaths) -> int:
    """Run one local import Job synchronously and print a one-line result."""
    from stemforge import errors
    from stemforge.application import ServiceEngines, WorkspaceService
    from stemforge.engines.media import FfmpegTool
    from stemforge.errors import StemForgeError
    from stemforge.jobs import JobState
    from stemforge.logging_setup import configure_logging
    from stemforge.settings import SettingsStore

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


def _print_model_notice(service: WorkspaceService) -> None:
    from stemforge.ui import formatting

    copy = formatting.model_licence_copy(service.model_spec())
    print(copy.title)
    print(copy.summary)
    print(copy.identity)
    print(copy.notice)


def _run_install_model(args: argparse.Namespace, paths: AppPaths) -> int:
    """Download and verify the pinned model, honouring the consent gate."""
    from stemforge import errors
    from stemforge.application import WorkspaceService
    from stemforge.errors import StemForgeError
    from stemforge.jobs import JobState
    from stemforge.logging_setup import configure_logging
    from stemforge.settings import SettingsStore

    settings = SettingsStore(paths)
    configure_logging(paths)
    service = WorkspaceService(settings, paths=paths)
    spec = service.model_spec()

    status = service.models.verify()
    if (
        not status.verified
        and not service.model_consent_is_current()
        and not args.accept_model_licence
    ):
        _print_model_notice(service)
        if not sys.stdin.isatty():
            print(
                f"error {errors.SEPARATION_MODEL_MISSING}: consent required; "
                "rerun with --accept-model-licence"
            )
            return 1
        try:
            answer = input("Type 'yes' to accept and download: ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in {"yes", "y"}:
            print(f"error {errors.SEPARATION_MODEL_MISSING}: consent declined")
            return 1

    runner = service.runner
    runner.start()
    try:
        job_id = service.install_model(accept_licence=args.accept_model_licence)
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
        verb = "installed" if not status.verified else "verified"
        print(
            f"{verb} {spec.name} revision={spec.revision[:8]} "
            f"sha256={spec.sha256} bytes={spec.size_bytes}"
        )
        return 0
    if snapshot.state is JobState.CANCELLED:
        print(f"cancelled {snapshot.kind.value} job {snapshot.job_id}")
        return 2

    code = snapshot.error_code or errors.INTERNAL_UNEXPECTED
    print(f"error {code}: {_one_line(snapshot.error_detail)}")
    return 1


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


def _dispatch_worker(argv: Sequence[str]) -> int:
    """Route ``--worker <name>`` to the worker module without app setup."""
    from stemforge import worker_protocol
    from stemforge.workers import dispatch

    index = argv.index("--worker")
    if index + 1 >= len(argv):
        print("stemforge: --worker needs a worker name", file=sys.stderr)
        return worker_protocol.EXIT_INVALID_INPUT
    name = argv[index + 1]
    rest = [*argv[:index], *argv[index + 2 :]]
    return dispatch(name, rest)


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the entry-point flags and return a process exit code."""
    raw = list(sys.argv[1:] if argv is None else argv)

    # Ticket 06 §7 order: worker dispatch happens before environment setup and
    # the instance lock, so a second process can still run the bundled worker.
    # The worker's own flags are not the app's, so this scan precedes argparse.
    if "--worker" in raw:
        return _dispatch_worker(raw)

    args = _build_parser().parse_args(raw)
    try:
        paths = _configure()
    except OSError as error:
        return _configuration_failure(args, error)

    if args.import_path is not None:
        return _run_import(args.import_path, paths)
    if args.install_model:
        return _run_install_model(args, paths)

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
