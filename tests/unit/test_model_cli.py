"""The headless ``--install-model`` mode: consent, result lines, and exit codes."""

from __future__ import annotations

from pathlib import Path

import pytest

from stemforge import __main__ as entry
from stemforge import errors, i18n
from stemforge.errors import JobCancelled
from stemforge.models import ModelStatus, ModelStore
from stemforge.pipeline.model_job import ModelInstallJob, ModelInstallResult
from stemforge.platform.paths import AppPaths
from stemforge.settings import SettingsStore

pytestmark = pytest.mark.unit


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> AppPaths:
    monkeypatch.setenv("STEMFORGE_HOME", str(tmp_path / "home"))
    return AppPaths.from_environment().ensure()


def _status(store: ModelStore, *, verified: bool) -> ModelStatus:
    return ModelStatus(
        name=store.spec.name,
        path=store.path(),
        expected_size_bytes=store.spec.size_bytes,
        expected_sha256=store.spec.sha256,
        installed=verified,
        verified=verified,
        reason=None if verified else "absent",
        size_bytes=store.spec.size_bytes if verified else None,
        sha256=store.spec.sha256 if verified else None,
    )


def test_install_model_without_consent_fails_non_interactively(
    home: AppPaths,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = entry.main(["--install-model"])

    output = capsys.readouterr().out
    assert code == 1
    assert "Separation model not installed" in output
    assert "not covered by StemForge's GPL-3.0 licence" in output
    assert f"error {errors.SEPARATION_MODEL_MISSING}: consent required" in output
    assert SettingsStore(home).model_consent() is None
    assert not (home.models / "htdemucs").exists()


def test_headless_install_model_keeps_english_on_a_romanian_process(
    home: AppPaths,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    i18n.set_language("ro", system_locale="ro_RO.UTF-8")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = entry.main(["--install-model"])

    output = capsys.readouterr().out
    assert code == 1
    assert "Separation model not installed" in output
    assert "not covered by StemForge's GPL-3.0 licence" in output
    assert i18n.language() == "en"


def test_install_model_accepts_and_reports_the_pinned_result(
    home: AppPaths,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(ModelStore, "verify", lambda store: _status(store, verified=False))

    def install(job: ModelInstallJob, _ctx: object) -> ModelInstallResult:
        return ModelInstallResult(status=_status(job.store, verified=True), downloaded=True)

    monkeypatch.setattr(ModelInstallJob, "run", install)

    code = entry.main(["--install-model", "--accept-model-licence"])

    output = capsys.readouterr().out
    assert code == 0
    assert output.startswith("installed htdemucs revision=cbc8a9b1 ")
    assert "sha256=d9fa14133cfcc034a6758923bb3a8ca9f8dfd0b582134643bbf83f72c17576dd" in output
    assert "bytes=84025440" in output
    consent = SettingsStore(home).model_consent()
    assert consent is not None
    assert consent.version == 1
    assert consent.acknowledged is True


def test_install_model_interactive_yes_records_consent_and_installs_once(
    home: AppPaths,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    attempts: list[ModelInstallJob] = []
    monkeypatch.setattr(ModelStore, "verify", lambda store: _status(store, verified=False))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "yes")

    def install(job: ModelInstallJob, _ctx: object) -> ModelInstallResult:
        attempts.append(job)
        return ModelInstallResult(status=_status(job.store, verified=True), downloaded=True)

    monkeypatch.setattr(ModelInstallJob, "run", install)

    code = entry.main(["--install-model"])

    output = capsys.readouterr().out
    assert code == 0
    assert len(attempts) == 1
    assert "model_missing" not in output
    assert "installed htdemucs " in output
    consent = SettingsStore(home).model_consent()
    assert consent is not None
    assert consent.acknowledged is True
    assert consent.version == 1


def test_install_model_interactive_no_declines_without_installing(
    home: AppPaths,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    attempts: list[ModelInstallJob] = []
    monkeypatch.setattr(ModelStore, "verify", lambda store: _status(store, verified=False))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "no")
    monkeypatch.setattr(ModelInstallJob, "run", lambda job, _ctx: attempts.append(job))

    code = entry.main(["--install-model"])

    output = capsys.readouterr().out
    assert code == 1
    assert attempts == []
    assert f"error {errors.SEPARATION_MODEL_MISSING}: consent declined" in output
    assert SettingsStore(home).model_consent() is None


def test_install_model_reports_an_already_verified_cache_without_consent(
    home: AppPaths,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(ModelStore, "verify", lambda store: _status(store, verified=True))
    monkeypatch.setattr(ModelStore, "is_installed", lambda _store: True)

    def install(job: ModelInstallJob, _ctx: object) -> ModelInstallResult:
        return ModelInstallResult(status=_status(job.store, verified=True), downloaded=False)

    monkeypatch.setattr(ModelInstallJob, "run", install)

    code = entry.main(["--install-model"])

    output = capsys.readouterr().out
    assert code == 0
    assert output.startswith("verified htdemucs ")
    assert SettingsStore(home).model_consent() is None


def test_install_model_cancel_exits_two(
    home: AppPaths,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(ModelStore, "verify", lambda store: _status(store, verified=True))
    monkeypatch.setattr(ModelStore, "is_installed", lambda _store: True)

    def cancel(_job: ModelInstallJob, _ctx: object) -> None:
        raise JobCancelled()

    monkeypatch.setattr(ModelInstallJob, "run", cancel)

    code = entry.main(["--install-model"])

    output = capsys.readouterr().out
    assert code == 2
    assert output.startswith("cancelled model_install job ")
