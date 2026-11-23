"""The PowerShell syntax gate fails on a broken script or inline workflow step.

These tests run the real parser: the gate exists so a syntax error on a Windows
runner is caught by PR CI, and a fake parser cannot prove that contract.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from scripts import check_powershell

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def powershell_host() -> None:
    shells = [
        shell
        for engine in check_powershell.POWERSHELL_ENGINES
        if (shell := check_powershell.find_shell(engine)) is not None
    ]
    if not shells:
        pytest.skip("no PowerShell host is installed")
    for shell in shells:
        probe = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-Command", "exit 0"],
            capture_output=True,
        )
        if probe.returncode == 0:
            return
    pytest.skip("no PowerShell host can start")


def test_a_broken_script_fails_the_gate(
    tmp_path: Path, powershell_host: None, capsys: pytest.CaptureFixture[str]
) -> None:
    script = tmp_path / "scripts" / "broken.ps1"
    script.parent.mkdir(parents=True)
    script.write_text("if (\n", encoding="utf-8")

    assert check_powershell.main(["--root", str(tmp_path)]) == 1

    assert "broken.ps1:1:" in capsys.readouterr().err


def test_a_broken_inline_block_fails_the_gate(
    tmp_path: Path, powershell_host: None, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = tmp_path / ".github" / "workflows" / "windows.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        """\
jobs:
  windows:
    runs-on: windows-2022
    steps:
      - name: broken guard
        shell: pwsh
        run: Write-Error "$file:"
""",
        encoding="utf-8",
    )

    assert check_powershell.main(["--root", str(tmp_path)]) == 1

    captured = capsys.readouterr()
    assert "windows.yml:windows:broken guard:1:" in captured.err
    assert "Variable reference is not valid" in captured.err


def test_an_expression_inside_quoted_text_still_parses(
    tmp_path: Path, powershell_host: None
) -> None:
    workflow = tmp_path / ".github" / "workflows" / "windows.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        """\
jobs:
  windows:
    runs-on: windows-2022
    steps:
      - name: quoted expression
        shell: pwsh
        run: |
          Write-Output "${{ inputs.name }}: build failed"
""",
        encoding="utf-8",
    )

    assert check_powershell.main(["--root", str(tmp_path)]) == 0


def test_the_repository_passes_the_gate(powershell_host: None) -> None:
    assert check_powershell.main(["--root", str(REPO_ROOT)]) == 0
