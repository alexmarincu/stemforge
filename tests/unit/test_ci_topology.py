"""The CI topology contract (ticket 11 §5) and the nightly policy (ticket 11 §5).

Ticket 11 fixes the PR job graph, its runners, its ceilings, the lightweight
sync, and the two coverage gates. These tests assert the workflows against that
specification, not against their own previous text: the expected jobs, marker
strings, artifact names, and thresholds are transcribed from the ticket.

``release.yml`` is ticket 10/23's file. This module pins its digest so a CI
topology change cannot quietly rewrite the release gate; if a release change is
intentional, update the pin in the same commit.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
CI = WORKFLOW_DIR / "ci.yml"
NIGHTLY = WORKFLOW_DIR / "nightly.yml"
RELEASE = WORKFLOW_DIR / "release.yml"
LINUX_BUNDLE = WORKFLOW_DIR / "linux-bundle.yml"
WINDOWS_BUNDLE = WORKFLOW_DIR / "windows-bundle.yml"

# Ticket 11 §5: "uv sync --frozen --no-install-package torch
# --no-install-package torchaudio --no-install-package demucs".
LIGHTWEIGHT_SYNC = (
    "uv sync --frozen --no-install-package torch "
    "--no-install-package torchaudio --no-install-package demucs"
)

# Ticket 11 §5, job -> (runner, ceiling in minutes).
CI_JOBS: dict[str, tuple[str, int]] = {
    "lint": ("ubuntu-24.04", 5),
    "unit-linux": ("ubuntu-24.04", 5),
    "unit-windows": ("windows-2022", 5),
    "integration-linux": ("ubuntu-22.04", 12),
    "integration-windows": ("windows-2022", 25),
    "ui-linux": ("ubuntu-24.04", 5),
    "licensing": ("ubuntu-24.04", 5),
    "coverage": ("ubuntu-24.04", 3),
    "frozen-linux": ("ubuntu-22.04", 15),
}

# Ticket 11 §5: unit and UI run without the heavy stack, and so does the
# Windows integration job.
LIGHTWEIGHT_JOBS = ("unit-linux", "unit-windows", "ui-linux", "integration-windows")
# Ticket 11 §5: "integration-linux, licensing, and frozen-linux install the
# full lock".
FULL_SYNC_JOBS = ("integration-linux", "licensing", "frozen-linux")

# Ticket 11 §6, the coverage job's two commands.
CORE_COVERAGE_INCLUDE = (
    "src/stemforge/domain/*,src/stemforge/jobs/*,src/stemforge/artifacts.py,"
    "src/stemforge/pipeline/*,src/stemforge/engines/*,src/stemforge/settings.py,"
    "src/stemforge/application/*,src/stemforge/worker_protocol.py,"
    "src/stemforge/errors.py"
)
COVERAGE_PRODUCERS = {
    "unit-linux": ".coverage.unit-linux",
    "integration-linux": ".coverage.integration-linux",
    "ui-linux": ".coverage.ui-linux",
}

# Recorded topology for the release workflow.
RELEASE_WORKFLOW_SHA256 = "8806a1a1dfa6d8cba79db0a9e7217104f335d4b600c768e332c2136eaff74345"


def _load(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict), path
    return payload


def _triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    # PyYAML parses the bare ``on`` key as boolean True under YAML 1.1.
    triggers = workflow.get("on", workflow.get(True))
    assert isinstance(triggers, dict)
    return triggers


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    steps = job.get("steps")
    assert isinstance(steps, list), job
    return steps


def _commands(job: dict[str, Any]) -> list[str]:
    return [step["run"] for step in _steps(job) if isinstance(step.get("run"), str)]


def _flat(job: dict[str, Any]) -> str:
    """One normalized string per job, for substring assertions."""
    return " ".join(
        yaml.safe_dump(job, sort_keys=False).replace("\\", " ").split(),
    )


def _uploads(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        step
        for step in _steps(job)
        if str(step.get("uses", "")).startswith("actions/upload-artifact")
    ]


def _chain_minutes(jobs: dict[str, Any], name: str) -> int:
    job = jobs[name]
    needs = job.get("needs", [])
    if isinstance(needs, str):
        needs = [needs]
    return int(job["timeout-minutes"]) + max(
        (_chain_minutes(jobs, need) for need in needs), default=0
    )


@pytest.fixture(scope="module")
def ci() -> dict[str, Any]:
    return _load(CI)


@pytest.fixture(scope="module")
def nightly() -> dict[str, Any]:
    return _load(NIGHTLY)


@pytest.fixture(scope="module")
def linux_bundle() -> dict[str, Any]:
    return _load(LINUX_BUNDLE)


@pytest.fixture(scope="module")
def windows_bundle() -> dict[str, Any]:
    return _load(WINDOWS_BUNDLE)


# ---------------------------------------------------------------------------
# Every workflow parses and only release.yml talks to the release API
# ---------------------------------------------------------------------------


def test_every_workflow_file_parses() -> None:
    paths = sorted(WORKFLOW_DIR.glob("*.yml"))
    assert {path.name for path in paths} >= {"ci.yml", "nightly.yml", "release.yml"}
    for path in paths:
        workflow = _load(path)
        assert isinstance(workflow.get("jobs"), dict), path


@pytest.mark.parametrize(
    "path",
    [CI, NIGHTLY, LINUX_BUNDLE, WINDOWS_BUNDLE],
    ids=["ci", "nightly", "linux-bundle", "windows-bundle"],
)
def test_only_the_release_workflow_publishes(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for forbidden in ("gh release", "--draft=false", "release upload"):
        assert forbidden not in text, (path.name, forbidden)


# ---------------------------------------------------------------------------
# ci.yml: the nine jobs, runners, and ceilings
# ---------------------------------------------------------------------------


def test_ci_has_exactly_the_nine_ticket_11_jobs(ci: dict[str, Any]) -> None:
    assert set(ci["jobs"]) == set(CI_JOBS)


def test_ci_runners_and_ceilings_match_the_ticket(ci: dict[str, Any]) -> None:
    for name, (runner, ceiling) in CI_JOBS.items():
        job = ci["jobs"][name]
        assert job["runs-on"] == runner, name
        assert job["timeout-minutes"] == ceiling, name


def test_ci_jobs_run_in_parallel_except_the_coverage_join(ci: dict[str, Any]) -> None:
    jobs = ci["jobs"]
    for name, job in jobs.items():
        if name == "coverage":
            assert set(job["needs"]) == set(COVERAGE_PRODUCERS)
        else:
            assert "needs" not in job, name


def test_the_pr_wall_stays_under_thirty_minutes_by_construction(ci: dict[str, Any]) -> None:
    jobs = ci["jobs"]
    longest = max(_chain_minutes(jobs, name) for name in jobs)
    assert longest < 30, longest


def test_ci_keeps_its_push_and_pull_request_triggers(ci: dict[str, Any]) -> None:
    triggers = _triggers(ci)
    assert triggers["push"]["branches"] == ["main"]
    assert "pull_request" in triggers


# ---------------------------------------------------------------------------
# ci.yml: the lightweight sync and the marker strings
# ---------------------------------------------------------------------------


def test_lightweight_jobs_skip_torch_torchaudio_and_demucs(ci: dict[str, Any]) -> None:
    for name in LIGHTWEIGHT_JOBS:
        commands = _commands(ci["jobs"][name])
        assert LIGHTWEIGHT_SYNC in commands, name
        assert not any("--group build" in command for command in commands), name


def test_full_sync_jobs_install_the_whole_lock(ci: dict[str, Any]) -> None:
    for name in FULL_SYNC_JOBS:
        commands = _commands(ci["jobs"][name])
        assert not any("--no-install-package" in command for command in commands), name
        assert any(command.startswith("uv sync --frozen") for command in commands), name


def test_each_test_job_selects_its_own_marker(ci: dict[str, Any]) -> None:
    expected = {
        "unit-linux": "unit",
        "unit-windows": "unit",
        "ui-linux": "ui",
        "integration-linux": "integration",
        "integration-windows": "integration and not slow",
    }
    for name, marker in expected.items():
        commands = _commands(ci["jobs"][name])
        matches = [
            command for command in commands if "-m pytest" in command or "pytest -m" in command
        ]
        assert matches, name
        assert any(
            command.replace('"', "").rstrip().endswith(f"-m {marker}") for command in matches
        ), (name, matches)
    # Windows integration must never collect the torch/demucs ``slow`` layer.
    integration_windows = _flat(ci["jobs"]["integration-windows"])
    assert '-m "integration and not slow"' in integration_windows


def test_lint_keeps_every_ticket_11_section_6_gate(ci: dict[str, Any]) -> None:
    text = _flat(ci["jobs"]["lint"])
    for gate in (
        "uv run ruff format --check .",
        "uv run ruff check .",
        "uv run mypy src/stemforge",
        "uv run lint-imports",
        "uv lock --check",
        "shellcheck -x",
    ):
        assert gate in text, gate


def test_licensing_runs_the_notice_gates(ci: dict[str, Any]) -> None:
    text = _flat(ci["jobs"]["licensing"])
    assert "generate_notices.py --check-release" in text
    assert "generate_notices.py --check" in text
    # The notice tests need the full installed metadata, so they live here
    # rather than in the torch-free unit jobs.
    assert "pytest -m licensing" in text


def test_windows_integration_analyses_powershell_and_preflights(ci: dict[str, Any]) -> None:
    text = _flat(ci["jobs"]["integration-windows"])
    assert "Invoke-ScriptAnalyzer" in text
    assert "-Severity Error" in text
    assert "packaging/windows/*.ps1" in text
    assert "--preflight" in text
    assert "--json" in text


def test_windows_integration_fails_closed_when_the_vendor_build_fails(
    ci: dict[str, Any],
) -> None:
    job = ci["jobs"]["integration-windows"]
    text = _flat(job)
    assert "packaging/vendor.lock.json" in text  # the existing vendor cache key
    # The vendor set is fetched once and then verified, exactly like the bundles,
    # and a failed build exits instead of falling through to the tests.
    fetch_steps = [step for step in _steps(job) if "vendor-fetch.ps1" in str(step.get("run", ""))]
    assert len(fetch_steps) == 2
    for step in fetch_steps:
        assert "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }" in str(step["run"])
    assert "-VerifyOnly" in text


def test_frozen_linux_is_the_linux_smoke_only(ci: dict[str, Any]) -> None:
    job = ci["jobs"]["frozen-linux"]
    text = _flat(job)
    assert "packaging/linux/build.sh" in text
    assert "packaging/linux/smoke-test.sh" in text
    assert "package.sh" not in text
    assert "AppImage" not in text


# ---------------------------------------------------------------------------
# ci.yml: the combined coverage gate (ticket 11 §6)
# ---------------------------------------------------------------------------


def test_coverage_producers_write_a_per_job_data_file(ci: dict[str, Any]) -> None:
    for name, data_file in COVERAGE_PRODUCERS.items():
        job = ci["jobs"][name]
        assert job["env"]["COVERAGE_FILE"] == data_file, name
        uploads = _uploads(job)
        assert len(uploads) == 1, name
        upload = uploads[0]
        assert upload["with"]["name"] == f"coverage-{name}"
        assert upload["with"]["path"] == data_file
        assert upload["with"]["retention-days"] == 7
        # upload-artifact v4 skips dotfiles unless this is explicit.
        assert upload["with"]["include-hidden-files"] is True
        assert "coverage run -m pytest" in _flat(job), name


def test_coverage_downloads_combines_and_enforces_both_thresholds(
    ci: dict[str, Any],
) -> None:
    job = ci["jobs"]["coverage"]
    steps = _steps(job)
    downloads = [
        step for step in steps if str(step.get("uses", "")).startswith("actions/download-artifact")
    ]
    assert len(downloads) == 1
    assert downloads[0]["with"]["pattern"] == "coverage-*"
    assert downloads[0]["with"]["merge-multiple"] is True

    commands = " ".join(" ".join(command.split()) for command in _commands(job))
    commands = commands.replace("\\", " ")
    assert "coverage combine" in commands
    assert "--fail-under=85" in commands
    assert f'--include="{CORE_COVERAGE_INCLUDE}"' in commands
    assert "--fail-under=70" in commands
    overall = [command for command in _commands(job) if "--fail-under=70" in command]
    assert overall
    assert all("--include=" not in command for command in overall)


# ---------------------------------------------------------------------------
# nightly.yml: 03:00 UTC, two frozen separation jobs, evidence only
# ---------------------------------------------------------------------------


def test_nightly_runs_at_three_utc_and_never_on_prs_or_pushes(
    nightly: dict[str, Any],
) -> None:
    triggers = _triggers(nightly)
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert [entry["cron"] for entry in triggers["schedule"]] == ["0 3 * * *"]


def test_nightly_has_exactly_the_two_frozen_separation_jobs(
    nightly: dict[str, Any],
) -> None:
    assert set(nightly["jobs"]) == {
        "linux-frozen-separation",
        "windows-frozen-separation",
    }


def test_nightly_reuses_the_bundle_workflows_with_the_release_ceilings(
    nightly: dict[str, Any],
) -> None:
    linux = nightly["jobs"]["linux-frozen-separation"]
    windows = nightly["jobs"]["windows-frozen-separation"]

    assert linux["uses"] == "./.github/workflows/linux-bundle.yml"
    assert windows["uses"] == "./.github/workflows/windows-bundle.yml"
    assert linux["with"] == {"separation": True, "timeout-minutes": 35}
    # Evidence only: never the portable ZIP or the Inno setup.
    assert windows["with"]["with_separation"] is True
    assert windows["with"]["upload_binaries"] is False
    assert windows["with"]["timeout-minutes"] == 50


def test_the_reusable_bundle_jobs_accept_the_ceiling(
    linux_bundle: dict[str, Any], windows_bundle: dict[str, Any]
) -> None:
    linux_inputs = _triggers(linux_bundle)["workflow_call"]["inputs"]
    windows_inputs = _triggers(windows_bundle)["workflow_call"]["inputs"]
    for inputs, ceiling in ((linux_inputs, 45), (windows_inputs, 35)):
        assert inputs["timeout-minutes"]["type"] == "number"
        assert inputs["timeout-minutes"]["default"] == ceiling

    linux_job = linux_bundle["jobs"]["bundle"]
    windows_job = windows_bundle["jobs"]["windows-bundle"]
    assert linux_job["runs-on"] == "ubuntu-22.04"
    assert windows_job["runs-on"] == "windows-2022"
    assert linux_job["timeout-minutes"] == "${{ inputs.timeout-minutes || 45 }}"
    assert windows_job["timeout-minutes"] == "${{ inputs.timeout-minutes || 35 }}"


def test_nightly_builds_and_runs_the_real_separation(
    nightly: dict[str, Any], linux_bundle: dict[str, Any], windows_bundle: dict[str, Any]
) -> None:
    assert nightly["permissions"] == {"contents": "read"}

    linux_text = _flat(linux_bundle["jobs"]["bundle"])
    assert "scripts/vendor-fetch.sh" in linux_text
    assert "packaging/linux/build.sh" in linux_text
    assert "--install-model" in linux_text
    assert "--accept-model-licence" in linux_text
    assert "--smoke-separation" in linux_text or "--with-separation" in linux_text
    assert "--preflight --json" in linux_text
    assert "--appimage-extract-and-run" in linux_text

    windows_text = _flat(windows_bundle["jobs"]["windows-bundle"])
    assert "packaging/windows/build.ps1" in windows_text
    assert "smoke-test.ps1" in windows_text
    assert "-WithSeparation" in windows_text


def test_nightly_uploads_evidence_with_seven_day_retention(
    linux_bundle: dict[str, Any], windows_bundle: dict[str, Any]
) -> None:
    for job in (linux_bundle["jobs"]["bundle"], windows_bundle["jobs"]["windows-bundle"]):
        uploads = _uploads(job)
        assert uploads
        assert all(upload["with"]["retention-days"] == 7 for upload in uploads), job["name"]
    # The Windows bundle can upload the built binaries on a manual dispatch; the
    # nightly must pass ``upload_binaries: false`` so the release assets stay in
    # release.yml (asserted in the ceiling test above).


# ---------------------------------------------------------------------------
# release.yml is not touched by this change
# ---------------------------------------------------------------------------


def test_release_workflow_is_untouched() -> None:
    # Git checks out CRLF on the Windows runner; pin the LF bytes so the same
    # workflow has one digest on every OS.
    payload = RELEASE.read_bytes().replace(b"\r\n", b"\n")
    digest = hashlib.sha256(payload).hexdigest()
    assert digest == RELEASE_WORKFLOW_SHA256, (
        "release.yml changed: ticket 10/23 owns that file. If the change is intentional, "
        "update the pin here in the same commit and re-run the release-pipeline tests."
    )
