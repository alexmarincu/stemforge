"""The diagnostic flags: ``--self-test``, ``--smoke-test``, ``--smoke-separation``.

Each flag prints one JSON report, writes the same report with ``--report``, and
uses its exit code as the authoritative result.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _environment(home: Path, *, harness: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["STEMFORGE_HOME"] = str(home)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("STEMFORGE_LOG_LEVEL", None)
    if harness is not None:
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = f"{harness}{os.pathsep}{existing}" if existing else str(harness)
    return env


def _run(
    *args: str, home: Path, cwd: Path, harness: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "stemforge", *args],
        cwd=cwd,
        env=_environment(home, harness=harness),
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )


def _report(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return json.loads(completed.stdout)


def _check(report: dict[str, Any], name: str) -> dict[str, Any]:
    for check in report["checks"]:
        if check["name"] == name:
            return check
    raise AssertionError(f"{name} missing from report")


@pytest.mark.unit
def test_self_test_reports_a_healthy_source_checkout(tmp_path: Path) -> None:
    home = tmp_path / "home"

    result = _run("--self-test", home=home, cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    report = _report(result)
    assert report["check"] == "self-test"
    assert report["ok"] is True
    assert report["frozen"] is False
    assert _check(report, "import.nicegui")["status"] == "ok"
    assert _check(report, "import.pywebview")["status"] == "ok"
    assert _check(report, "assets")["status"] == "ok"
    assert _check(report, "models.lock")["status"] == "ok"
    assert _check(report, "workspace")["status"] == "ok"
    assert _check(report, "workspace")["detail"] == "0 tracks"
    for name in ("paths.data", "paths.cache", "paths.logs", "paths.models", "paths.config"):
        assert _check(report, name)["status"] == "ok"


@pytest.mark.unit
def test_self_test_writes_the_settings_file_and_log(tmp_path: Path) -> None:
    home = tmp_path / "home"

    result = _run("--self-test", home=home, cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert (home / "config" / "settings.json").is_file()
    log_text = (home / "logs" / "stemforge.log").read_text(encoding="utf-8")
    assert "self-test" in log_text


@pytest.mark.unit
def test_self_test_writes_the_report_file(tmp_path: Path) -> None:
    report_path = tmp_path / "reports" / "self-test.json"

    result = _run("--self-test", "--report", str(report_path), home=tmp_path / "home", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    written = json.loads(report_path.read_text(encoding="utf-8"))
    assert written == _report(result)


@pytest.mark.unit
def test_self_test_reports_missing_vendor_binaries_without_failing(tmp_path: Path) -> None:
    home = tmp_path / "home"

    result = _run("--self-test", home=home, cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    report = _report(result)
    binary_names = {"vendor.ffmpeg", "vendor.ffprobe", "vendor.rubberband", "vendor.deno"}
    binary_checks = [check for check in report["checks"] if check["name"] in binary_names]
    assert {check["name"] for check in binary_checks} == binary_names
    assert all(check["required"] is False for check in binary_checks if check["status"] != "ok")


@pytest.mark.unit
@pytest.mark.skipif(
    (PROJECT_ROOT / "vendor" / "ffmpeg" / "ffmpeg").is_file(),
    reason="a checkout with vendor/ present has the binaries this test removes",
)
def test_self_test_requires_vendor_binaries_when_asked(tmp_path: Path) -> None:
    home = tmp_path / "home"
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()

    result = subprocess.run(
        [sys.executable, "-m", "stemforge", "--self-test"],
        cwd=tmp_path,
        env={
            **_environment(home),
            "PATH": str(empty_path),
            "STEMFORGE_REQUIRE_VENDOR": "1",
        },
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )

    assert result.returncode != 0
    report = _report(result)
    assert report["ok"] is False
    assert _check(report, "vendor.ffmpeg")["required"] is True


@pytest.mark.unit
@pytest.mark.skipif(
    not (PROJECT_ROOT / "vendor" / "ffmpeg" / "ffmpeg").is_file(),
    reason="vendor/ is absent; run scripts/vendor-fetch.sh",
)
def test_self_test_executes_the_vendored_binaries(tmp_path: Path) -> None:
    result = _run("--self-test", home=tmp_path / "home", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    report = _report(result)
    for name in ("vendor.ffmpeg", "vendor.ffprobe", "vendor.rubberband", "vendor.deno"):
        check = _check(report, name)
        assert check["status"] == "ok", check
        assert check["detail"].startswith(str(PROJECT_ROOT / "vendor"))


@pytest.mark.unit
def test_self_test_imports_the_gui_stack_but_not_demucs(tmp_path: Path) -> None:
    harness = tmp_path / "harness"
    harness.mkdir()
    (harness / "sitecustomize.py").write_text(
        "\n".join(
            [
                "import atexit",
                "import sys",
                "",
                "def _report_heavy_modules():",
                "    heavy = sorted(",
                "        {name.split('.', 1)[0] for name in sys.modules}",
                "        & {'nicegui', 'torch', 'demucs'}",
                "    )",
                "    print('STEMFORGE_HEAVY_MODULES=' + ','.join(heavy))",
                "",
                "atexit.register(_report_heavy_modules)",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = _run("--self-test", home=tmp_path / "home", cwd=tmp_path, harness=harness)

    assert result.returncode == 0, result.stderr
    reports = [
        line for line in result.stdout.splitlines() if line.startswith("STEMFORGE_HEAVY_MODULES=")
    ]
    assert len(reports) == 1
    imported = set(reports[0].split("=", 1)[1].split(",")) - {""}
    # torch is optional; when installed, --self-test asserts +cpu.
    assert "nicegui" in imported
    assert "demucs" not in imported
    assert imported <= {"nicegui", "torch"}


@pytest.mark.unit
def test_smoke_test_serves_the_root_page(tmp_path: Path) -> None:
    result = _run("--smoke-test", home=tmp_path / "home", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    report = _report(result)
    assert report["check"] == "smoke-test"
    assert report["ok"] is True
    assert report["frozen"] is False
    assert report["server"]["status"] == 200
    assert report["server"]["url"] == f"http://127.0.0.1:{report['server']['port']}/"
    assert report["server"]["seconds"] < 30
    assert _check(report, "import.nicegui")["status"] == "ok"


@pytest.mark.unit
def test_smoke_test_writes_the_report_file(tmp_path: Path) -> None:
    report_path = tmp_path / "reports" / "smoke-test.json"

    result = _run(
        "--smoke-test", "--report", str(report_path), home=tmp_path / "home", cwd=tmp_path
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(report_path.read_text(encoding="utf-8"))["server"]["status"] == 200


@pytest.mark.unit
def test_smoke_separation_reports_the_missing_model_clearly(tmp_path: Path) -> None:
    report_path = tmp_path / "reports" / "smoke-separation.json"

    result = _run(
        "--smoke-separation",
        "--report",
        str(report_path),
        home=tmp_path / "home",
        cwd=tmp_path,
    )

    assert result.returncode == 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["check"] == "smoke-separation"
    assert report["ok"] is False
    check = _check(report, "model.weights")
    assert check["status"] == "failed"
    assert "not installed" in check["detail"]
    assert "--install-model --accept-model-licence" in check["detail"]
    assert "model" in report


@pytest.mark.unit
def test_smoke_test_does_not_run_a_separation(tmp_path: Path) -> None:
    result = _run("--smoke-test", home=tmp_path / "home", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    report = _report(result)
    names = {check["name"] for check in report["checks"]}
    assert not any(name.startswith("separation.") for name in names)
    assert "smoke-separation" not in report["check"]
