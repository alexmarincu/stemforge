"""The scaffold contract: version, taxonomy, entry point, and module boundaries."""

from __future__ import annotations

import ast
import importlib
import os
import re
import sys
import tomllib
from pathlib import Path

import pytest

import stemforge
import stemforge.__main__
from stemforge import errors

CORE_MODULES = [
    "stemforge",
    "stemforge.__main__",
    "stemforge.application",
    "stemforge.artifacts",
    "stemforge.errors",
    "stemforge.logging_setup",
    "stemforge.settings",
    "stemforge.smoke",
    "stemforge.worker_protocol",
    "stemforge.domain.models",
    "stemforge.domain.workspace",
    "stemforge.jobs.job",
    "stemforge.jobs.runner",
    "stemforge.pipeline.export_job",
    "stemforge.pipeline.gain",
    "stemforge.pipeline.import_job",
    "stemforge.pipeline.model_job",
    "stemforge.pipeline.pitch_job",
    "stemforge.pipeline.separation_job",
    "stemforge.engines.media",
    "stemforge.engines.pitch",
    "stemforge.engines.separation",
    "stemforge.engines.youtube",
    "stemforge.workers",
    "stemforge.workers.separation",
    "stemforge.platform.binaries",
    "stemforge.platform.environment",
    "stemforge.platform.instance",
    "stemforge.platform.paths",
    "stemforge.platform.preflight",
    "stemforge.platform.process",
    "stemforge.platform.resources",
]

# Independent copy of the taxonomy in ticket 06 §9 plus the ticket 14 amendments.
EXPECTED_CODES = frozenset(
    {
        "import.no_audio_stream",
        "import.unsupported_source",
        "import.download_failed",
        "import.youtube_unavailable",
        "import.youtube_age_restricted",
        "import.file_unavailable",
        "import.too_long",
        "import.youtube_url_invalid",
        "import.youtube_playlist",
        "import.youtube_too_long",
        "import.youtube_live",
        "import.youtube_rate_limited",
        "import.youtube_extractor_outdated",
        "import.network",
        "decode.failed",
        "separation.model_missing",
        "separation.model_download_failed",
        "separation.worker_crashed",
        "separation.engine_error",
        "separation.protocol_mismatch",
        "separation.output_invalid",
        "pitch.engine_missing",
        "pitch.render_failed",
        "pitch.output_invalid",
        "media.probe_failed",
        "media.encode_failed",
        "media.validation_failed",
        "export.write_failed",
        "logging.open_failed",
        "storage.disk_full",
        "storage.cache_full",
        "storage.permission_denied",
        "internal.unexpected",
        "preflight.platform_unsupported",
        "preflight.window_unavailable",
        "preflight.webview2_missing",
        "preflight.bundle_damaged",
        "preflight.storage_unwritable",
        "preflight.preview_unavailable",
        "playback.unavailable",
        "licensing.files_missing",
        "licensing.open_failed",
        "cancelled",
    }
)

EXPECTED_NAMESPACES = frozenset(
    {
        "decode",
        "export",
        "import",
        "internal",
        "licensing",
        "logging",
        "media",
        "pitch",
        "playback",
        "preflight",
        "separation",
        "storage",
    }
)

HEAVY_MODULES = frozenset({"nicegui", "torch", "demucs"})
ALLOWED_FOUNDATION_THIRD_PARTY = frozenset({"platformdirs"})


def _source(module: object) -> Path:
    module_file = getattr(module, "__file__", None)
    assert module_file is not None
    return Path(module_file)


def _main_module_source() -> str:
    return (_source(stemforge.__main__).parent / "__main__.py").read_text(encoding="utf-8")


def _import_roots(source: str) -> set[str]:
    """Top-level module names imported by a source file."""
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".", 1)[0])
    return roots


def _module_scope_import_roots(source: str) -> set[str]:
    """Top-level module names imported at module scope only."""
    roots: set[str] = set()
    for node in ast.parse(source).body:
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".", 1)[0])
    return roots


def _main_guard(source: str) -> ast.If:
    for node in ast.parse(source).body:
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare):
            left = node.test.left
            if isinstance(left, ast.Name) and left.id == "__name__":
                return node
    raise AssertionError("__main__.py has no main guard")


@pytest.mark.unit
def test_version_is_a_release_semver() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", stemforge.__version__)


@pytest.mark.unit
def test_pyproject_reads_the_version_from_the_package() -> None:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))

    assert "version" not in payload["project"]
    assert payload["project"]["dynamic"] == ["version"]
    assert payload["tool"]["hatch"]["version"]["path"] == "src/stemforge/__init__.py"


@pytest.mark.unit
def test_error_taxonomy_matches_the_decision_record() -> None:
    assert errors.ALL_CODES == EXPECTED_CODES


@pytest.mark.unit
def test_error_codes_are_namespaced() -> None:
    for code in errors.ALL_CODES - {"cancelled"}:
        namespace, _, member = code.partition(".")
        assert namespace in EXPECTED_NAMESPACES, code
        assert member, code


@pytest.mark.unit
def test_error_carries_code_message_and_metadata() -> None:
    error = errors.StemForgeError(
        errors.IMPORT_NO_AUDIO_STREAM,
        "no audio stream",
        retryable=True,
        detail="reason=video_only",
        context={"path": "clip.mp4"},
    )

    assert error.code == "import.no_audio_stream"
    assert error.message == "no audio stream"
    assert error.retryable is True
    assert error.detail == "reason=video_only"
    assert error.context["path"] == "clip.mp4"
    assert str(error) == "no audio stream"


@pytest.mark.unit
def test_error_defaults_are_empty_and_not_retryable() -> None:
    error = errors.StemForgeError(errors.INTERNAL_UNEXPECTED, "boom")

    assert error.retryable is False
    assert error.detail is None
    assert dict(error.context) == {}


@pytest.mark.unit
def test_error_context_is_read_only() -> None:
    error = errors.StemForgeError(errors.INTERNAL_UNEXPECTED, "boom")

    with pytest.raises(TypeError):
        error.context["injected"] = "value"  # type: ignore[index]


@pytest.mark.unit
def test_job_cancelled_is_a_coded_stemforge_error() -> None:
    cancelled = errors.JobCancelled()

    assert isinstance(cancelled, errors.StemForgeError)
    assert cancelled.code == errors.CANCELLED == "cancelled"
    assert cancelled.retryable is False


@pytest.mark.unit
def test_job_cancelled_keeps_a_custom_message() -> None:
    assert errors.JobCancelled("stopped by the user").message == "stopped by the user"


@pytest.mark.unit
def test_self_test_mode_exits_zero_with_a_message(capsys: pytest.CaptureFixture[str]) -> None:
    assert stemforge.__main__.main(["--self-test"]) == 0
    assert "self-test" in capsys.readouterr().out


@pytest.mark.unit
def test_entry_point_only_imports_the_heavy_stack_lazily() -> None:
    # The UI lands in _run_ui; what matters is that importing __main__ (and
    # therefore --self-test/--worker dispatch) does not import NiceGUI or torch.
    assert not _module_scope_import_roots(_main_module_source()) & HEAVY_MODULES


@pytest.mark.unit
def test_package_init_does_not_import_the_heavy_stack() -> None:
    source = (_source(stemforge).parent / "__init__.py").read_text(encoding="utf-8")
    assert not _import_roots(source) & HEAVY_MODULES


@pytest.mark.unit
def test_foundation_modules_do_not_import_other_stemforge_packages() -> None:
    root = _source(stemforge).parent
    foundation = [
        root / "errors.py",
        root / "worker_protocol.py",
        *sorted((root / "platform").glob("*.py")),
        *sorted((root / "domain").glob("*.py")),
    ]

    for path in foundation:
        source = path.read_text(encoding="utf-8")
        roots = _module_scope_import_roots(source)
        assert "stemforge" not in roots, f"{path.name} imports another stemforge module"
        unexpected = roots - sys.stdlib_module_names - ALLOWED_FOUNDATION_THIRD_PARTY
        assert not unexpected, f"{path.name} imports {sorted(unexpected)}"


@pytest.mark.unit
@pytest.mark.parametrize("module_name", CORE_MODULES)
def test_core_modules_are_importable(module_name: str) -> None:
    assert importlib.import_module(module_name) is not None


@pytest.mark.unit
def test_session_home_is_a_temporary_directory() -> None:
    home = Path(os.environ["STEMFORGE_HOME"])
    assert home.is_dir()
    assert home.name.startswith("stemforge-tests-")
