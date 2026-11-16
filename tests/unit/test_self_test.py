"""``python -m stemforge --self-test``: exit 0, summary, and no heavy imports."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

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


def _run_self_test(
    home: Path, cwd: Path, *, harness: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "stemforge", "--self-test"],
        cwd=cwd,
        env=_environment(home, harness=harness),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


@pytest.mark.unit
def test_self_test_exits_zero_and_writes_the_settings_and_log(tmp_path: Path) -> None:
    home = tmp_path / "home"

    result = _run_self_test(home, tmp_path)

    assert result.returncode == 0, result.stderr
    assert "StemForge 0.1.0 self-test" in result.stdout
    assert "self-test ok" in result.stdout
    assert "workspace: 0 tracks" in result.stdout
    for path in (
        home / "data",
        home / "cache",
        home / "logs",
        home / "data" / "models",
        home / "config",
        home / "data" / "webview",
    ):
        assert str(path) in result.stdout
    assert (home / "config" / "settings.json").is_file()
    assert (home / "logs" / "stemforge.log").is_file()
    log_text = (home / "logs" / "stemforge.log").read_text(encoding="utf-8")
    assert "self-test" in log_text


@pytest.mark.unit
def test_self_test_does_not_import_nicegui_torch_or_demucs(tmp_path: Path) -> None:
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

    result = _run_self_test(tmp_path / "home", tmp_path, harness=harness)

    assert result.returncode == 0, result.stderr
    reports = [
        line for line in result.stdout.splitlines() if line.startswith("STEMFORGE_HEAVY_MODULES=")
    ]
    assert reports == ["STEMFORGE_HEAVY_MODULES="]


@pytest.mark.unit
def test_the_entry_point_still_imports_nothing_heavy_at_module_scope() -> None:
    source = (PROJECT_ROOT / "src" / "stemforge" / "__main__.py").read_text(encoding="utf-8")

    for module in ("nicegui", "torch", "demucs"):
        assert f"import {module}" not in source
