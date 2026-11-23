"""The PowerShell syntax gate discovers every script and inline workflow step.

Extraction is pure Python and runs in the unit layer. The end-to-end parse
needs a PowerShell host, so it is exercised from the integration layer.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml
from scripts import check_powershell

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("shell", "expected"),
    [
        ("pwsh", True),
        ("powershell", True),
        ("pwsh.exe", True),
        ("pwsh -NoProfile -File {0}", True),
        ("bash", False),
        ("python {0}", False),
        (None, False),
    ],
)
def test_only_powershell_shells_are_inline_sources(shell: object, expected: bool) -> None:
    assert check_powershell.is_powershell_shell(shell) is expected


def _write_workflow(tmp_path: Path, body: str) -> None:
    workflow = tmp_path / ".github" / "workflows" / "windows.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(body, encoding="utf-8")


def test_an_inline_powershell_step_without_run_text_is_rejected(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        """\
jobs:
  windows:
    runs-on: windows-2022
    steps:
      - name: missing run
        shell: pwsh
""",
    )

    with pytest.raises(ValueError, match="missing run"):
        check_powershell.discover_inline_blocks(tmp_path)


def test_the_cli_reports_a_powershell_step_without_run_text(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_workflow(
        tmp_path,
        """\
jobs:
  windows:
    runs-on: windows-2022
    steps:
      - name: missing run
        shell: pwsh
""",
    )

    assert check_powershell.main(["--root", str(tmp_path)]) == 1

    assert "no run text" in capsys.readouterr().err


def test_the_cli_reports_invalid_workflow_yaml(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_workflow(tmp_path, "jobs: [\n")

    assert check_powershell.main(["--root", str(tmp_path)]) == 1

    assert "check_powershell" in capsys.readouterr().err


def test_an_unstartable_shell_is_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(check_powershell, "find_shell", lambda engine: None)

    assert check_powershell.parse_sources(tmp_path, [], []) == 2

    assert "no powershell host" in capsys.readouterr().err.lower()


def test_scripts_and_blocks_use_the_shell_that_runs_them(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    available = {"pwsh": "/tools/pwsh", "powershell": "/tools/powershell"}
    manifests: dict[str, dict[str, object]] = {}

    def fake_run(argv: list[str], capture_output: bool) -> subprocess.CompletedProcess[bytes]:
        manifest_path = Path(argv[argv.index("-Manifest") + 1])
        manifests[argv[0]] = json.loads(manifest_path.read_text(encoding="utf-8"))
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    script = tmp_path / "scripts" / "tool.ps1"
    script.parent.mkdir(parents=True)
    script.write_text("exit 0", encoding="utf-8")

    monkeypatch.setattr(check_powershell, "find_shell", available.get)
    monkeypatch.setattr(check_powershell.subprocess, "run", fake_run)

    status = check_powershell.parse_sources(
        tmp_path,
        [script],
        [
            check_powershell.InlineBlock("pwsh block", "exit 0", "pwsh"),
            check_powershell.InlineBlock("powershell block", "exit 0", "powershell"),
        ],
    )

    assert status == 0
    scripts = manifests["/tools/powershell"]["scripts"]
    assert isinstance(scripts, list)
    assert [Path(entry).as_posix() for entry in scripts] == ["scripts/tool.ps1"]
    assert manifests["/tools/powershell"]["blocks"] == [
        {"label": "powershell block", "text": "exit 0"}
    ]
    assert manifests["/tools/pwsh"]["blocks"] == [{"label": "pwsh block", "text": "exit 0"}]


def test_windows_powershell_blocks_fall_back_to_pwsh_off_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    shells: list[str] = []

    def fake_run(argv: list[str], capture_output: bool) -> subprocess.CompletedProcess[bytes]:
        shells.append(argv[0])
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(
        check_powershell,
        "find_shell",
        lambda engine: "/tools/pwsh" if engine == "pwsh" else None,
    )
    monkeypatch.setattr(check_powershell.subprocess, "run", fake_run)

    status = check_powershell.parse_sources(
        tmp_path, [], [check_powershell.InlineBlock("powershell block", "exit 0", "powershell")]
    )

    assert status == 0
    assert shells == ["/tools/pwsh"]


def test_an_empty_gate_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert check_powershell.main(["--root", str(tmp_path)]) == 1

    assert "no PowerShell sources found" in capsys.readouterr().err


def test_github_expressions_are_neutralized_without_moving_lines() -> None:
    text = (
        "-WithSeparation:${{ inputs.with_separation }}\n"
        'Write-Error "${{ inputs.name }}: build failed"\n'
    )
    multiline = "value ${{ format(\n  '{0}'\n) }} end\n"

    assert (
        check_powershell.neutralize_github_expressions(text)
        == '-WithSeparation:${null}\nWrite-Error "${null}: build failed"\n'
    )
    assert check_powershell.neutralize_github_expressions(multiline).count("\n") == 3


def test_every_explicit_powershell_step_is_collected() -> None:
    expected: list[str] = []
    for path in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job in payload["jobs"].values():
            for step in job.get("steps", []):
                if check_powershell.is_powershell_shell(step.get("shell")):
                    expected.append(step["run"])

    blocks = check_powershell.discover_inline_blocks(REPO_ROOT)

    assert sorted(expected) == sorted(block.text for block in blocks)
    assert len({block.label for block in blocks}) == len(blocks)


def test_the_windows_bundle_parse_guard_is_checked() -> None:
    blocks = check_powershell.discover_inline_blocks(REPO_ROOT)
    guards = [
        block
        for block in blocks
        if block.label.startswith("windows-bundle.yml:") and "${file}:" in block.text
    ]

    assert len(guards) == 1
