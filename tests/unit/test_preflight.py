"""Preflight checks, the report schema, fatal delivery, and the CLI flags."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from stemforge import errors
from stemforge.platform import preflight
from stemforge.platform.paths import AppPaths
from stemforge.ui import formatting


def _check(
    name: str,
    *,
    status: str = preflight.STATUS_OK,
    message: str = "ok",
    fatal: bool = False,
) -> preflight.CheckResult:
    return preflight.CheckResult(name=name, status=status, message=message, fatal=fatal)


def _report(*checks: preflight.CheckResult, **kwargs: object) -> preflight.PreflightReport:
    return preflight.PreflightReport(checks=checks, **kwargs)  # type: ignore[arg-type]


@pytest.mark.unit
def test_report_keeps_every_check_and_finds_the_first_fatal() -> None:
    report = _report(
        _check("platform"),
        _check("window", status=preflight.STATUS_FAIL, message="no window", fatal=True),
        _check("bundle", status=preflight.STATUS_FAIL, message="no bundle", fatal=True),
        system="linux",
    )

    assert len(report.checks) == 3
    assert report.fatal is True
    assert report.exit_code == preflight.EXIT_WINDOW_UNAVAILABLE


@pytest.mark.unit
@pytest.mark.parametrize(
    ("check_name", "system", "expected"),
    [
        ("platform", "linux", preflight.EXIT_PLATFORM_UNSUPPORTED),
        ("window", "linux", preflight.EXIT_WINDOW_UNAVAILABLE),
        ("window", "win32", preflight.EXIT_WEBVIEW2_UNAVAILABLE),
        ("storage", "linux", preflight.EXIT_STORAGE_UNWRITABLE),
        ("bundle", "linux", preflight.EXIT_BUNDLE_DAMAGED),
    ],
)
def test_each_fatal_check_maps_to_its_exit_code(
    check_name: str, system: str, expected: int
) -> None:
    report = _report(
        _check(check_name, status=preflight.STATUS_FAIL, message="failed", fatal=True),
        system=system,
    )

    assert report.exit_code == expected


@pytest.mark.unit
def test_run_preflight_records_injected_checkers_in_order(app_paths: AppPaths) -> None:
    results = [
        _check("platform"),
        _check("window", status=preflight.STATUS_FAIL, message="no window", fatal=True),
        _check("bundle"),
    ]

    report = preflight.run_preflight(
        app_paths,
        checkers=[lambda result=result: result for result in results],
    )

    assert report.checks == tuple(results)
    expected = (
        preflight.EXIT_WEBVIEW2_UNAVAILABLE
        if sys.platform.startswith("win")
        else preflight.EXIT_WINDOW_UNAVAILABLE
    )
    assert report.exit_code == expected


@pytest.mark.unit
def test_preview_degraded_exits_four_only_in_cli_mode() -> None:
    checks = (
        _check("platform"),
        _check("window"),
        _check("preview", status=preflight.STATUS_DEGRADED, message="no GStreamer"),
    )

    cli = _report(*checks)
    gui = _report(*checks, fail_on_degraded=False)

    assert cli.degraded is True
    assert cli.exit_code == preflight.EXIT_PREVIEW_UNAVAILABLE
    assert gui.degraded is True
    assert gui.exit_code == preflight.EXIT_OK


@pytest.mark.unit
def test_warnings_do_not_change_the_exit_code() -> None:
    report = _report(
        _check("platform"),
        _check("resources", status=preflight.STATUS_WARN, message="Only 1.5 GB free."),
    )

    assert report.fatal is False
    assert report.exit_code == preflight.EXIT_OK
    assert report.warnings == ("Only 1.5 GB free.",)


@pytest.mark.unit
def test_json_report_has_the_stable_schema() -> None:
    report = _report(
        _check("platform", message="Linux x86_64"),
        _check("window", status=preflight.STATUS_FAIL, message="gi is missing", fatal=True),
        system="linux",
    )

    payload = json.loads(report.to_json())

    assert set(payload) == {"version", "ok", "exit_code", "degraded", "checks", "warnings"}
    assert payload["version"] == preflight.REPORT_VERSION
    assert payload["ok"] is False
    assert payload["exit_code"] == preflight.EXIT_WINDOW_UNAVAILABLE
    assert payload["degraded"] is False
    assert payload["warnings"] == []
    assert payload["checks"] == [
        {
            "name": "platform",
            "status": "ok",
            "message": "Linux x86_64",
            "fatal": False,
        },
        {
            "name": "window",
            "status": "fail",
            "message": "gi is missing",
            "fatal": True,
        },
    ]


@pytest.mark.unit
def test_report_file_is_written_with_the_same_json(tmp_path: Path) -> None:
    report = _report(_check("platform"))
    target = tmp_path / "reports" / "preflight.json"

    preflight.write_report(target, report)

    assert target.read_text(encoding="utf-8") == report.to_json() + "\n"


@pytest.mark.unit
def test_fatal_message_body_and_copy_include_the_log_path() -> None:
    message = preflight.build_fatal_message(
        code=errors.PREFLIGHT_WINDOW_UNAVAILABLE,
        sentence="StemForge needs GTK 3 and WebKitGTK 4.1 to open its window.",
        fix_line="Install GTK 3 and WebKitGTK 4.1.",
        log_path=Path("/logs/stemforge.log"),
        copy_text="{diagnostics}",
    )

    assert message.title == "StemForge can't start"
    assert message.body == (
        "StemForge needs GTK 3 and WebKitGTK 4.1 to open its window.\n\n"
        "Install GTK 3 and WebKitGTK 4.1.\n\n"
        f"Details: {Path('/logs/stemforge.log')}"
    )
    assert message.code == errors.PREFLIGHT_WINDOW_UNAVAILABLE
    assert message.copy_text == "{diagnostics}"


@pytest.mark.unit
def test_fatal_message_says_logs_are_unavailable_without_a_log() -> None:
    message = preflight.build_fatal_message(
        code=errors.PREFLIGHT_BUNDLE_DAMAGED,
        sentence="StemForge is missing files from its installation.",
        fix_line="Reinstall StemForge.",
        log_path=None,
        copy_text="json",
    )

    assert message.body.endswith("Logs are unavailable.")


@pytest.mark.unit
def test_fatal_box_uses_the_injected_backend() -> None:
    message = preflight.build_fatal_message(
        code=errors.PREFLIGHT_PLATFORM_UNSUPPORTED,
        sentence="StemForge runs on 64-bit Linux and 64-bit Windows.",
        fix_line="Use a supported system.",
        log_path=None,
        copy_text="diagnostics",
    )
    calls: list[tuple[str, str, str | None]] = []

    def backend(title: str, body: str, copy_text: str | None) -> None:
        calls.append((title, body, copy_text))

    preflight.show_fatal_message(message, backend=backend)

    assert calls == [("StemForge can't start", message.body, "diagnostics")]


@pytest.mark.unit
def test_already_running_message_uses_the_injected_backend() -> None:
    calls: list[tuple[str, str, str | None]] = []

    def backend(title: str, body: str, copy_text: str | None) -> None:
        calls.append((title, body, copy_text))

    preflight.show_already_running(backend=backend)

    assert calls == [
        (
            "StemForge is already running",
            "StemForge is already running. Only one copy can run at a time.",
            None,
        )
    ]


@pytest.mark.unit
def test_fatal_copy_matches_ticket_14_exactly() -> None:
    window = formatting.fatal_copy(errors.PREFLIGHT_WINDOW_UNAVAILABLE)
    storage = formatting.fatal_copy(errors.PREFLIGHT_STORAGE_UNWRITABLE, path="/tmp/stemforge")
    bundle = formatting.fatal_copy(errors.PREFLIGHT_BUNDLE_DAMAGED)
    webview = formatting.fatal_copy(errors.PREFLIGHT_WEBVIEW2_MISSING)
    platform = formatting.fatal_copy(errors.PREFLIGHT_PLATFORM_UNSUPPORTED)

    assert window.headline == "StemForge needs GTK 3 and WebKitGTK 4.1 to open its window."
    assert "libwebkit2gtk-4.1-0" in window.action
    assert storage.headline == "StemForge can't write to /tmp/stemforge."
    assert "STEMFORGE_HOME" in storage.action
    assert bundle.headline == "StemForge is missing files from its installation."
    assert "extract the archive again" in bundle.action
    assert webview.headline == "StemForge needs the Microsoft Edge WebView2 Runtime."
    assert "developer.microsoft.com/microsoft-edge/webview2" in webview.action
    assert platform.headline == "StemForge runs on 64-bit Linux and 64-bit Windows."
    assert "glibc 2.35" in platform.action


@pytest.mark.unit
def test_platform_check_accepts_linux_x86_64_with_a_current_glibc() -> None:
    result = preflight.check_platform(system="linux", machine="x86_64", glibc=(2, 44))

    assert result.status == preflight.STATUS_OK
    assert result.fatal is False


@pytest.mark.unit
def test_platform_check_rejects_old_glibc() -> None:
    result = preflight.check_platform(system="linux", machine="x86_64", glibc=(2, 31))

    assert result.status == preflight.STATUS_FAIL
    assert result.fatal is True


@pytest.mark.unit
def test_platform_check_rejects_non_x86_64_linux() -> None:
    result = preflight.check_platform(system="linux", machine="aarch64", glibc=(2, 44))

    assert result.status == preflight.STATUS_FAIL
    assert result.fatal is True


@pytest.mark.unit
def test_platform_check_accepts_windows_x64() -> None:
    result = preflight.check_platform(system="win32", machine="AMD64")

    assert result.status == preflight.STATUS_OK
    assert result.fatal is False


@pytest.mark.unit
def test_platform_check_rejects_windows_arm64() -> None:
    result = preflight.check_platform(system="win32", machine="ARM64")

    assert result.status == preflight.STATUS_FAIL
    assert result.fatal is True


@pytest.mark.unit
def test_window_check_accepts_gtk_webkitgtk_and_soup() -> None:
    probe = preflight.WindowProbe(
        gi=True,
        gtk="3.24.41",
        webkit="4.1",
        soup="3.0",
        webkit_version=(2, 46, 4),
    )

    result = preflight.check_window(system="linux", probe=probe)

    assert result.status == preflight.STATUS_OK
    assert result.fatal is False


@pytest.mark.unit
def test_window_check_fails_without_gi() -> None:
    result = preflight.check_window(system="linux", probe=preflight.WindowProbe(gi=False))

    assert result.status == preflight.STATUS_FAIL
    assert result.fatal is True


@pytest.mark.unit
def test_window_check_requires_webkitgtk_2_44() -> None:
    probe = preflight.WindowProbe(
        gi=True,
        gtk="3.24.41",
        webkit="4.1",
        soup="3.0",
        webkit_version=(2, 42, 0),
    )

    result = preflight.check_window(system="linux", probe=probe)

    assert result.status == preflight.STATUS_FAIL
    assert "2.44" in result.message


@pytest.mark.unit
def test_window_check_requires_soup_3() -> None:
    probe = preflight.WindowProbe(
        gi=True,
        gtk="3.24.41",
        webkit="4.1",
        soup=None,
        webkit_version=(2, 46, 4),
    )

    result = preflight.check_window(system="linux", probe=probe)

    assert result.status == preflight.STATUS_FAIL
    assert "Soup 3" in result.message


@pytest.mark.unit
def test_window_check_accepts_webview2_and_dotnet() -> None:
    probe = preflight.WindowProbe(
        webview2="120.0.2210.91",
        dotnet_release=528040,
    )

    result = preflight.check_window(system="win32", probe=probe)

    assert result.status == preflight.STATUS_OK
    assert result.fatal is False


@pytest.mark.unit
def test_window_check_fails_without_webview2() -> None:
    result = preflight.check_window(
        system="win32", probe=preflight.WindowProbe(dotnet_release=528040)
    )

    assert result.status == preflight.STATUS_FAIL
    assert result.fatal is True


@pytest.mark.unit
def test_window_check_requires_dotnet_4_8() -> None:
    result = preflight.check_window(
        system="win32",
        probe=preflight.WindowProbe(webview2="120.0.2210.91", dotnet_release=394801),
    )

    assert result.status == preflight.STATUS_FAIL
    assert result.fatal is True


@pytest.mark.unit
def test_bundle_check_degrades_to_the_dev_fallback_without_vendor(
    tmp_path: Path,
) -> None:
    result = preflight.check_bundle(
        bundle_root=tmp_path / "repo",
        data_root=tmp_path / "repo",
        frozen=False,
    )

    assert result.status == preflight.STATUS_OK
    assert result.fatal is False
    assert "development" in result.message.lower()


@pytest.mark.unit
def test_bundle_check_fails_when_frozen_without_vendor(tmp_path: Path) -> None:
    result = preflight.check_bundle(
        bundle_root=tmp_path / "bundle",
        data_root=tmp_path / "bundle" / "_internal",
        frozen=True,
    )

    assert result.status == preflight.STATUS_FAIL
    assert result.fatal is True


@pytest.mark.unit
def test_bundle_check_verifies_tools_and_locks(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    internal = bundle / "_internal"
    for tool_dir, tool_name in (
        ("ffmpeg", "ffmpeg"),
        ("ffmpeg", "ffprobe"),
        ("rubberband", "rubberband"),
        ("deno", "deno"),
    ):
        path = bundle / "vendor" / tool_dir / tool_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        path.chmod(0o755)
    (internal / "models").mkdir(parents=True)
    (internal / "models" / "models.lock.json").write_text("{}", encoding="utf-8")
    (internal / "vendor.lock.json").write_text(
        json.dumps(
            {
                "components": {
                    "ffmpeg": {"version": "9.0.2"},
                    "ffprobe": {"version": "9.0.2"},
                    "rubberband": {"version": "4.0.0"},
                    "deno": {"version": "2.9.8"},
                }
            }
        ),
        encoding="utf-8",
    )

    versions = {
        "ffmpeg": "ffmpeg version n9.0.2-57-gd838f9f9ae",
        "ffprobe": "ffprobe version n9.0.2-57-gd838f9f9ae",
        "rubberband": "Rubber Band 4.0.0",
        "deno": "deno 2.9.8 (stable)",
    }
    result = preflight.check_bundle(
        bundle_root=bundle,
        data_root=internal,
        frozen=True,
        system="linux",
        version_of=lambda path, name: versions[name],
    )

    assert result.status == preflight.STATUS_OK
    assert result.fatal is False


@pytest.mark.unit
def test_bundle_check_reports_a_version_mismatch(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    internal = bundle / "_internal"
    for tool_dir, tool_name in (
        ("ffmpeg", "ffmpeg"),
        ("ffmpeg", "ffprobe"),
        ("rubberband", "rubberband"),
        ("deno", "deno"),
    ):
        path = bundle / "vendor" / tool_dir / tool_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        path.chmod(0o755)
    (internal / "models").mkdir(parents=True)
    (internal / "models" / "models.lock.json").write_text("{}", encoding="utf-8")
    (internal / "vendor.lock.json").write_text(
        json.dumps({"versions": {"deno": "9.9.9"}}), encoding="utf-8"
    )

    result = preflight.check_bundle(
        bundle_root=bundle,
        data_root=internal,
        frozen=True,
        system="linux",
        version_of=lambda path, name: "deno 2.9.8 (stable)",
    )

    assert result.status == preflight.STATUS_FAIL
    assert result.fatal is True
    assert "deno" in result.message
    assert "9.9.9" not in result.message


@pytest.mark.unit
def test_bundle_check_requires_a_readable_models_lock(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    internal = bundle / "_internal"
    for tool_dir, tool_name in (
        ("ffmpeg", "ffmpeg"),
        ("ffmpeg", "ffprobe"),
        ("rubberband", "rubberband"),
        ("deno", "deno"),
    ):
        path = bundle / "vendor" / tool_dir / tool_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        path.chmod(0o755)
    internal.mkdir(parents=True)
    (internal / "vendor.lock.json").write_text("{}", encoding="utf-8")

    result = preflight.check_bundle(
        bundle_root=bundle,
        data_root=internal,
        frozen=True,
        system="linux",
        version_of=lambda path, name: name,
    )

    assert result.status == preflight.STATUS_FAIL
    assert result.fatal is True
    assert "models.lock.json" in result.message


@pytest.mark.unit
def test_bundle_check_reports_a_non_executable_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "bundle"
    internal = bundle / "_internal"
    for tool_dir, tool_name in (
        ("ffmpeg", "ffmpeg"),
        ("ffmpeg", "ffprobe"),
        ("rubberband", "rubberband"),
        ("deno", "deno"),
    ):
        path = bundle / "vendor" / tool_dir / tool_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    (internal / "models").mkdir(parents=True)
    (internal / "models" / "models.lock.json").write_text("{}", encoding="utf-8")
    (internal / "vendor.lock.json").write_text("{}", encoding="utf-8")
    # Windows has no executable bit (``os.access(X_OK)`` is always true), so the
    # POSIX rule is exercised with a patched ``os.access``.
    monkeypatch.setattr(preflight.os, "access", lambda path, mode: False)

    result = preflight.check_bundle(
        bundle_root=bundle,
        data_root=internal,
        frozen=True,
        system="linux",
        version_of=lambda path, name: name,
    )

    assert result.status == preflight.STATUS_FAIL
    assert result.fatal is True
    assert "not executable: ffmpeg" in result.message


def _real_lock() -> dict[str, object]:
    root = Path(__file__).resolve().parents[2]
    payload = json.loads((root / "packaging" / "vendor.lock.json").read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _build_vendor_from_lock(
    tmp_path: Path, target: str, *, skip: tuple[str, ...] = ()
) -> tuple[Path, Path, dict[str, str]]:
    """Lay out every file the lock names, without executing the real tools."""
    bundle = tmp_path / "bundle"
    internal = bundle / "_internal"
    internal.mkdir(parents=True)
    (internal / "models").mkdir()
    (internal / "models" / "models.lock.json").write_text("{}", encoding="utf-8")
    (internal / "vendor.lock.json").write_text(
        (Path(__file__).resolve().parents[2] / "packaging" / "vendor.lock.json").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )

    lock = _real_lock()
    components = lock["components"]
    assert isinstance(components, dict)
    versions: dict[str, str] = {}
    for name, record in components.items():
        assert isinstance(record, dict)
        versions[name] = str(record["version"])
        targets = record["targets"]
        assert isinstance(targets, dict)
        entry = targets[target]
        assert isinstance(entry, dict)
        for item in entry["install"]:
            assert isinstance(item, dict)
            destination = str(item["to"])
            if destination in skip:
                continue
            path = bundle / "vendor" / destination
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
            if item["executable"]:
                path.chmod(0o755)
    return bundle, internal, versions


@pytest.mark.unit
def test_bundle_check_verifies_the_real_lock_and_shared_library(tmp_path: Path) -> None:
    bundle, internal, versions = _build_vendor_from_lock(tmp_path, "linux-x86_64")

    result = preflight.check_bundle(
        bundle_root=bundle,
        data_root=internal,
        frozen=True,
        system="linux",
        target="linux-x86_64",
        version_of=lambda path, name: f"{name} {versions[name]}",
    )

    assert result.status == preflight.STATUS_OK
    assert result.fatal is False
    assert "vendor.lock.json" in result.message


@pytest.mark.unit
def test_bundle_check_names_a_missing_shared_library(tmp_path: Path) -> None:
    bundle, internal, versions = _build_vendor_from_lock(
        tmp_path, "linux-x86_64", skip=("rubberband/libsndfile.so.1",)
    )

    result = preflight.check_bundle(
        bundle_root=bundle,
        data_root=internal,
        frozen=True,
        system="linux",
        target="linux-x86_64",
        version_of=lambda path, name: f"{name} {versions[name]}",
    )

    assert result.status == preflight.STATUS_FAIL
    assert result.fatal is True
    assert "vendor/rubberband/libsndfile.so.1" in result.message
    assert "missing" in result.message


@pytest.mark.unit
def test_bundle_check_applies_the_windows_exe_rule(tmp_path: Path) -> None:
    bundle, internal, versions = _build_vendor_from_lock(tmp_path, "windows-x64")
    # The lock names only .exe files for Windows; a suffix-less tool must fail
    # the executable rule even on a filesystem that thinks chmod did something.
    (bundle / "vendor" / "deno" / "deno.exe").rename(bundle / "vendor" / "deno" / "deno")

    result = preflight.check_bundle(
        bundle_root=bundle,
        data_root=internal,
        frozen=True,
        system="win32",
        target="windows-x64",
        version_of=lambda path, name: f"{name} {versions[name]}",
    )

    assert result.status == preflight.STATUS_FAIL
    assert "missing: vendor/deno/deno.exe" in result.message


@pytest.mark.unit
def test_bundle_check_reports_a_real_lock_version_mismatch(tmp_path: Path) -> None:
    bundle, internal, _versions = _build_vendor_from_lock(tmp_path, "linux-x86_64")

    result = preflight.check_bundle(
        bundle_root=bundle,
        data_root=internal,
        frozen=True,
        system="linux",
        target="linux-x86_64",
        version_of=lambda path, name: (
            "Rubber Band 3.3.0" if name == "rubberband" else "deno 2.9.8 (stable)"
        ),
    )

    assert result.status == preflight.STATUS_FAIL
    assert "version mismatch: rubberband" in result.message
    assert "3.3.0" not in result.message


@pytest.mark.unit
def test_bundle_check_reports_a_version_probe_failure(tmp_path: Path) -> None:
    bundle, internal, _versions = _build_vendor_from_lock(tmp_path, "linux-x86_64")

    result = preflight.check_bundle(
        bundle_root=bundle,
        data_root=internal,
        frozen=True,
        system="linux",
        target="linux-x86_64",
        version_of=lambda path, name: None,
    )

    assert result.status == preflight.STATUS_FAIL
    assert "--version failed: ffmpeg" in result.message


@pytest.mark.unit
def test_shared_library_name_lookup_is_platform_specific() -> None:
    assert preflight.shared_library_name("rubberband", system="linux") == "libsndfile.so.1"
    assert preflight.shared_library_name("rubberband", system="win32") == "sndfile.dll"
    assert preflight.shared_library_name("deno", system="linux") is None


@pytest.mark.unit
def test_read_vendor_lock_versions_reads_the_packaged_lock() -> None:
    versions = preflight.read_vendor_lock_versions(
        Path(__file__).resolve().parents[2] / "packaging" / "vendor.lock.json"
    )

    assert versions == {
        "ffmpeg": "n9.0.2-57-gd838f9f9ae",
        "ffprobe": "n9.0.2-57-gd838f9f9ae",
        "rubberband": "4.0.0",
        "deno": "2.9.8",
    }


@pytest.mark.unit
def test_storage_check_probes_every_root(app_paths: AppPaths) -> None:
    result = preflight.check_storage(app_paths, home_override=None)

    assert result.status == preflight.STATUS_OK
    assert result.fatal is False


@pytest.mark.unit
def test_storage_check_fails_when_home_is_a_file(tmp_path: Path) -> None:
    home = tmp_path / "not-a-directory"
    home.write_text("", encoding="utf-8")

    result = preflight.check_storage(AppPaths.for_home(home), home_override=str(home))

    assert result.status == preflight.STATUS_FAIL
    assert result.fatal is True
    assert str(home) in result.message


@pytest.mark.unit
def test_storage_check_records_a_root_creation_error_instead_of_raising(
    tmp_path: Path,
) -> None:
    home = tmp_path / "blocked"
    home.write_text("", encoding="utf-8")

    result = preflight.check_storage(AppPaths.for_home(home), home_override=None)

    assert result.status == preflight.STATUS_FAIL
    assert result.fatal is True
    assert str(home) in result.message


@pytest.mark.unit
def test_preview_check_is_ok_when_all_elements_are_present() -> None:
    probe = preflight.PreviewProbe(
        method="gst-python",
        present=("wavparse", "mpg123audiodec", "autoaudiosink"),
    )

    result = preflight.check_preview(system="linux", probe=probe)

    assert result.status == preflight.STATUS_OK
    assert result.fatal is False


@pytest.mark.unit
def test_preview_check_is_degraded_when_an_element_is_missing() -> None:
    probe = preflight.PreviewProbe(
        method="gst-inspect",
        present=("wavparse",),
        missing=("mpg123audiodec", "autoaudiosink"),
    )

    result = preflight.check_preview(system="linux", probe=probe)

    assert result.status == preflight.STATUS_DEGRADED
    assert result.fatal is False
    assert "mpg123audiodec" in result.message


@pytest.mark.unit
def test_preview_check_assumes_present_when_detection_is_unknown() -> None:
    result = preflight.check_preview(system="linux", probe=preflight.PreviewProbe(method="unknown"))

    assert result.status == preflight.STATUS_OK
    assert result.fatal is False


@pytest.mark.unit
def test_preview_check_is_covered_by_webview2_on_windows() -> None:
    result = preflight.check_preview(system="win32", probe=None)

    assert result.status == preflight.STATUS_OK
    assert result.fatal is False


@pytest.mark.unit
def test_resources_warn_under_the_disk_and_ram_floors(app_paths: AppPaths) -> None:
    result = preflight.check_resources(
        app_paths, disk_free=19 * preflight.GIB, total_ram=7 * preflight.GIB
    )

    assert result.status == preflight.STATUS_WARN
    assert result.fatal is False
    assert "disk" in result.message.lower()
    assert "RAM" in result.message


@pytest.mark.unit
def test_resources_are_quiet_above_the_floors(app_paths: AppPaths) -> None:
    result = preflight.check_resources(
        app_paths, disk_free=100 * preflight.GIB, total_ram=16 * preflight.GIB
    )

    assert result.status == preflight.STATUS_OK
    assert result.fatal is False


@pytest.mark.unit
def test_resources_below_the_disk_floor_still_do_not_fail(app_paths: AppPaths) -> None:
    result = preflight.check_resources(
        app_paths, disk_free=preflight.GIB, total_ram=16 * preflight.GIB
    )

    assert result.status == preflight.STATUS_WARN
    assert result.fatal is False
    assert "may not be able" in result.message


@pytest.mark.unit
def test_cli_json_prints_the_schema_and_reports_the_exit_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from stemforge import __main__ as entry

    report = _report(
        _check("platform"),
        _check("window", status=preflight.STATUS_FAIL, message="gi is missing", fatal=True),
        system="linux",
    )
    monkeypatch.setattr(entry.preflight, "run_preflight", lambda *a, **k: report)
    monkeypatch.setattr(entry.preflight, "show_fatal_message", lambda *a, **k: None)

    code = entry.main(["--preflight", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == preflight.EXIT_WINDOW_UNAVAILABLE
    assert payload["exit_code"] == preflight.EXIT_WINDOW_UNAVAILABLE


@pytest.mark.unit
def test_cli_degraded_preview_exits_four_and_prints_the_hint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from stemforge import __main__ as entry

    report = _report(
        _check("platform"),
        _check("preview", status=preflight.STATUS_DEGRADED, message="no GStreamer"),
    )
    shown: list[preflight.FatalMessage] = []
    monkeypatch.setattr(entry.preflight, "run_preflight", lambda *a, **k: report)
    monkeypatch.setattr(
        entry.preflight, "show_fatal_message", lambda message, **k: shown.append(message)
    )

    code = entry.main(["--preflight"])

    out = capsys.readouterr().out
    assert code == preflight.EXIT_PREVIEW_UNAVAILABLE
    assert shown == []
    assert "Playback isn't available on this system." in out
    assert "gstreamer1.0-plugins-good" in out


@pytest.mark.unit
def test_cli_turns_an_unwritable_stemforge_home_into_exit_six(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from stemforge import __main__ as entry

    home = tmp_path / "blocked"
    home.write_text("", encoding="utf-8")
    monkeypatch.setenv("STEMFORGE_HOME", str(home))
    shown: list[preflight.FatalMessage] = []
    monkeypatch.setattr(
        entry.preflight, "show_fatal_message", lambda message, **k: shown.append(message)
    )

    code = entry.main(["--preflight", "--quiet"])

    assert code == preflight.EXIT_STORAGE_UNWRITABLE
    assert len(shown) == 1
    assert str(home) in shown[0].body
    assert "STEMFORGE_HOME" in shown[0].body
    assert capsys.readouterr().out == ""


@pytest.mark.unit
def test_cli_human_output_is_one_line_per_check(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from stemforge import __main__ as entry

    report = _report(_check("platform"), _check("window"))
    monkeypatch.setattr(entry.preflight, "run_preflight", lambda *a, **k: report)

    assert entry.main(["--preflight"]) == preflight.EXIT_OK
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("platform")
    assert lines[1].startswith("window")


@pytest.mark.unit
def test_cli_writes_the_report_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from stemforge import __main__ as entry

    report = _report(_check("platform"))
    target = tmp_path / "preflight.json"
    monkeypatch.setattr(entry.preflight, "run_preflight", lambda *a, **k: report)

    code = entry.main(["--preflight", "--quiet", "--report", str(target)])

    assert code == preflight.EXIT_OK
    assert json.loads(target.read_text(encoding="utf-8"))["version"] == preflight.REPORT_VERSION
    assert capsys.readouterr().out == ""


@pytest.mark.unit
def test_fatal_cli_writes_the_report_before_the_box_and_copies_its_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from stemforge import __main__ as entry

    report = _report(
        _check("platform"),
        _check("window", status=preflight.STATUS_FAIL, message="gi is missing", fatal=True),
        system="linux",
    )
    target = tmp_path / "preflight.json"
    shown: list[preflight.FatalMessage] = []
    monkeypatch.setattr(entry.preflight, "run_preflight", lambda *a, **k: report)
    monkeypatch.setattr(
        entry.preflight, "show_fatal_message", lambda message, **k: shown.append(message)
    )

    code = entry.main(["--preflight", "--quiet", "--report", str(target)])

    assert code == preflight.EXIT_WINDOW_UNAVAILABLE
    assert target.is_file()
    assert len(shown) == 1
    assert shown[0].copy_text == target.read_text(encoding="utf-8")
    assert capsys.readouterr().out == ""


@pytest.mark.unit
def test_no_window_opens_on_a_fatal_preflight(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from stemforge import __main__ as entry

    report = _report(
        _check("window", status=preflight.STATUS_FAIL, message="gi is missing", fatal=True),
        system="linux",
    )
    shown: list[preflight.FatalMessage] = []
    run_ui: list[object] = []
    monkeypatch.setattr(entry.preflight, "run_preflight", lambda *a, **k: report)
    monkeypatch.setattr(
        entry.preflight, "show_fatal_message", lambda message, **k: shown.append(message)
    )
    monkeypatch.setattr(entry, "_run_ui", lambda *a, **k: run_ui.append(object()) or 0)

    code = entry.main([])

    captured = capsys.readouterr()
    assert code == preflight.EXIT_WINDOW_UNAVAILABLE
    assert run_ui == []
    assert len(shown) == 1
    assert shown[0].title == "StemForge can't start"
    assert "StemForge needs GTK 3 and WebKitGTK 4.1" in shown[0].body
    assert captured.out == ""


@pytest.mark.unit
def test_second_instance_shows_the_native_message_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from stemforge import __main__ as entry

    shown: list[bool] = []
    run_ui: list[object] = []
    monkeypatch.setattr("stemforge.platform.instance.InstanceLock.acquire", lambda self: False)
    monkeypatch.setattr(entry.preflight, "show_already_running", lambda **k: shown.append(True))
    monkeypatch.setattr(entry, "_run_ui", lambda *a, **k: run_ui.append(object()) or 0)

    assert entry.main([]) == 0
    assert shown == [True]
    assert run_ui == []
    assert capsys.readouterr().out == ""


@pytest.mark.unit
def test_missing_preview_only_degrades_a_normal_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from stemforge import __main__ as entry

    report = _report(
        _check("platform"),
        _check("preview", status=preflight.STATUS_DEGRADED, message="no GStreamer"),
        fail_on_degraded=False,
    )
    run_ui: list[preflight.PreflightReport] = []
    monkeypatch.setattr(entry.preflight, "run_preflight", lambda *a, **k: report)
    monkeypatch.setattr(entry, "_run_ui", lambda paths, report: run_ui.append(report) or 0)

    assert entry.main([]) == preflight.EXIT_OK
    assert run_ui == [report]
    assert run_ui[0].degraded is True


@pytest.mark.unit
def test_browser_mode_keeps_a_missing_native_window_from_blocking_the_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from stemforge import __main__ as entry

    report = _report(
        _check("platform"),
        _check("window", status=preflight.STATUS_FAIL, message="gi is missing", fatal=True),
        _check("preview", status=preflight.STATUS_DEGRADED, message="no GStreamer"),
        system="linux",
        fail_on_degraded=False,
    )
    run_ui: list[preflight.PreflightReport] = []
    monkeypatch.setenv("STEMFORGE_BROWSER", "1")
    monkeypatch.setattr(entry.preflight, "run_preflight", lambda *a, **k: report)
    monkeypatch.setattr(entry, "_run_ui", lambda paths, report: run_ui.append(report) or 0)
    monkeypatch.setattr("stemforge.platform.instance.InstanceLock.acquire", lambda self: True)
    monkeypatch.setattr("stemforge.platform.instance.InstanceLock.release", lambda self: None)

    assert entry.main([]) == preflight.EXIT_OK
    assert len(run_ui) == 1
    assert run_ui[0].fatal is False
    window = next(check for check in run_ui[0].checks if check.name == preflight.CHECK_WINDOW)
    assert window.status == preflight.STATUS_WARN
    assert "browser mode" in window.message.lower()


@pytest.mark.unit
def test_browser_mode_never_hides_a_non_window_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from stemforge import __main__ as entry

    report = _report(
        _check("bundle", status=preflight.STATUS_FAIL, message="vendor is missing", fatal=True),
        system="linux",
    )
    shown: list[preflight.FatalMessage] = []
    run_ui: list[object] = []
    monkeypatch.setenv("STEMFORGE_BROWSER", "1")
    monkeypatch.setattr(entry.preflight, "run_preflight", lambda *a, **k: report)
    monkeypatch.setattr(
        entry.preflight, "show_fatal_message", lambda message, **k: shown.append(message)
    )
    monkeypatch.setattr(entry, "_run_ui", lambda *a, **k: run_ui.append(object()) or 0)
    monkeypatch.setattr("stemforge.platform.instance.InstanceLock.acquire", lambda self: True)
    monkeypatch.setattr("stemforge.platform.instance.InstanceLock.release", lambda self: None)

    assert entry.main([]) == preflight.EXIT_BUNDLE_DAMAGED
    assert run_ui == []
    assert len(shown) == 1
