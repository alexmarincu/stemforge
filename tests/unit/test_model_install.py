"""Model install: pins, consent, streaming, verification, and cancellation.

The tests drive only the public seams: ``ModelStore`` and ``ModelInstallJob``.
No test reaches the network; the download transport is injected and reads bytes
that live in the test process. Ticket 14 §8 allows no automatic retries, so a
transient transport failure is a single failed attempt.
"""

from __future__ import annotations

import json
import logging
import urllib.error
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stemforge import errors
from stemforge.domain.cancellation import CancelToken
from stemforge.domain.models import JobKind
from stemforge.errors import JobCancelled, StemForgeError
from stemforge.jobs.job import JobContext
from stemforge.models import (
    ModelConsent,
    ModelSpec,
    ModelStore,
    default_spec,
)
from stemforge.pipeline.model_job import ModelInstallJob, ModelInstallResult
from stemforge.platform.paths import AppPaths
from stemforge.ui import formatting

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
WEIGHT_SUFFIXES = frozenset(
    {".safetensors", ".ckpt", ".onnx", ".pt", ".pth", ".bin", ".h5", ".tflite", ".gguf"}
)

# A 4096-byte fixture and its literal SHA-256, independent of the code path.
PAYLOAD = bytes(range(256)) * 16
PAYLOAD_SHA256 = "c8f5d0341d54d951a71b136e6e2afcb14d11ed8489a7ae126a8fee0df6ecf193"
PROGRESS_PAYLOAD = b"stemforge-model-progress\n" * (3 * 1024 * 1024 // 25)
PROGRESS_SHA256 = "3730739d7c47dd6ea5a466d1b0db609d3a7280418c40c6bf61fa287bcca7abd3"

_LOG = logging.getLogger("stemforge.tests.model")


def _weight_files() -> tuple[Path, ...]:
    """Weight-like files in the checkout, captured when pytest collects this module."""
    found = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in WEIGHT_SUFFIXES:
            continue
        if {".git", "node_modules", "__pycache__"} & set(path.parts):
            continue
        # Local virtualenvs (``.venv``, ``.venv-light``, ``.venv-ci``, ...) are
        # checkouts' tooling, never repository content.
        if any(part.startswith(".venv") for part in path.parts):
            continue
        found.append(path)
    return tuple(sorted(found))


WEIGHTS_AT_COLLECTION = _weight_files()


def _spec(
    *,
    size: int = len(PAYLOAD),
    sha256: str = PAYLOAD_SHA256,
    filename: str = "test-weights.safetensors",
) -> ModelSpec:
    return ModelSpec(
        name="htdemucs",
        repo_id="example/HTDemucs",
        revision="test-revision",
        filename=filename,
        size_bytes=size,
        sha256=sha256,
        download_url="https://example.invalid/models/test-weights.safetensors",
        source_url="https://example.invalid/models/test-weights.safetensors",
        licence="test licence",
        licence_notice="test notice",
        licence_notice_version=1,
    )


@pytest.fixture
def store(app_paths: AppPaths) -> ModelStore:
    return ModelStore(app_paths, spec=_spec())


def _consent(version: int = 1) -> ModelConsent:
    return ModelConsent(version=version, acknowledged=True, accepted_at=datetime.now(UTC))


class _Response:
    """A minimal binary response backed by bytes in memory."""

    def __init__(
        self,
        data: bytes,
        *,
        content_length: bool = True,
        chunk_size: int = 64 * 1024,
        on_read: Callable[[int], None] | None = None,
    ) -> None:
        self._data = data
        self._offset = 0
        self._chunk_size = chunk_size
        self._on_read = on_read
        self.headers: Mapping[str, str] = (
            {"Content-Length": str(len(data))} if content_length else {}
        )
        self.closed = False

    def read(self, size: int = -1, /) -> bytes:
        if self._on_read is not None:
            self._on_read(self._offset)
        limit = self._chunk_size if size < 0 else min(size, self._chunk_size)
        chunk = self._data[self._offset : self._offset + limit]
        self._offset += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


class _ScriptedTransport:
    """Callable transport returning scripted responses or raising scripted errors."""

    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    def __call__(self, url: str) -> _Response:
        assert url
        self.calls += 1
        outcome = self.outcomes[min(self.calls, len(self.outcomes)) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, _Response)
        return outcome


def _context(
    token: CancelToken | None = None,
) -> tuple[JobContext, list[tuple[float, str | None, str | None]]]:
    updates: list[tuple[float, str | None, str | None]] = []

    def update(fraction: float, stage: str | None, detail: str | None) -> None:
        updates.append((fraction, stage, detail))

    ctx = JobContext(
        job_id="model-test",
        kind=JobKind.MODEL_INSTALL,
        cancel=token or CancelToken(),
        log=_LOG,
        update=update,
    )
    return ctx, updates


def _job(
    store: ModelStore,
    transport: Callable[[str], _Response],
    *,
    consent: ModelConsent | None = None,
    give_consent: bool = True,
) -> ModelInstallJob:
    return ModelInstallJob(
        store=store,
        consent=(_consent() if consent is None else consent) if give_consent else None,
        transport=transport,
    )


# --- The pinned identity ---------------------------------------------------


def test_pins_match_the_decision_record() -> None:
    spec = default_spec()

    assert spec.name == "htdemucs"
    assert spec.repo_id == "adefossez/HTDemucs"
    assert spec.revision == "cbc8a9b1a87023b7fd74e7b3412e6321c0eab003"
    assert spec.filename == "955717e8.safetensors"
    assert spec.size_bytes == 84_025_440
    assert spec.sha256 == "d9fa14133cfcc034a6758923bb3a8ca9f8dfd0b582134643bbf83f72c17576dd"
    assert spec.download_url == (
        "https://huggingface.co/adefossez/HTDemucs/resolve/"
        "cbc8a9b1a87023b7fd74e7b3412e6321c0eab003/955717e8.safetensors"
    )
    assert spec.licence_notice_version == 1


def test_bundled_lock_is_marked_not_distributed() -> None:
    from stemforge.platform import resources

    payload = json.loads(resources.models_lock_path().read_text(encoding="utf-8"))
    record = payload["models"]["htdemucs"]

    assert record["distributed"] is False
    assert record["sha256"] == default_spec().sha256
    assert record["download_url"] == default_spec().download_url
    assert "not covered by StemForge's GPL-3.0 licence" in record["licence_notice"]


def test_repository_ships_no_model_weights() -> None:
    assert WEIGHTS_AT_COLLECTION == ()


def test_model_licence_copy_states_the_unclear_licence() -> None:
    copy = formatting.model_licence_copy(default_spec())

    assert copy.title == "Separation model not installed"
    assert "84 MB" in copy.summary
    assert "verified by SHA-256" in copy.summary
    assert "not part of StemForge" in copy.notice
    assert "not covered by StemForge's GPL-3.0 licence" in copy.notice
    assert "likely non-commercial" in copy.notice
    assert copy.accept_label == "Download model"
    assert copy.details_label == "Licence details"


# --- Model store -----------------------------------------------------------


def test_install_bytes_writes_weights_then_the_marker(store: ModelStore) -> None:
    status = store.install_bytes(PAYLOAD)

    assert status.verified is True
    assert status.installed is True
    assert status.reason is None
    assert store.path().read_bytes() == PAYLOAD
    assert not store.partial_path().exists()
    marker = json.loads(store.marker_path().read_text(encoding="utf-8"))
    assert marker["model"] == "htdemucs"
    assert marker["revision"] == "test-revision"
    assert marker["sha256"] == PAYLOAD_SHA256
    assert marker["size"] == len(PAYLOAD)
    assert marker["verified_at"]
    assert store.is_installed() is True


def test_install_bytes_refuses_a_bad_hash_and_leaves_nothing(store: ModelStore) -> None:
    with pytest.raises(StemForgeError) as raised:
        store.install_bytes(b"not the pinned bytes")

    assert raised.value.code == errors.SEPARATION_MODEL_MISSING
    assert raised.value.detail == "reason=checksum"
    assert not store.path().exists()
    assert not store.partial_path().exists()
    assert not store.marker_path().exists()


def test_verify_rehashes_and_discards_a_tampered_copy(store: ModelStore) -> None:
    store.install_bytes(PAYLOAD)
    tampered = bytearray(PAYLOAD)
    tampered[0] ^= 0xFF
    store.path().write_bytes(bytes(tampered))

    status = store.verify()

    assert status.installed is False
    assert status.verified is False
    assert status.reason == "checksum"
    assert not store.path().exists()
    assert not store.marker_path().exists()


def test_verify_before_use_is_the_separation_seam(store: ModelStore) -> None:
    with pytest.raises(StemForgeError) as absent:
        store.verify_before_use()
    assert absent.value.code == errors.SEPARATION_MODEL_MISSING
    assert absent.value.detail == "reason=absent"

    store.install_bytes(PAYLOAD)

    assert store.verify_before_use().verified is True


def test_a_missing_file_with_a_stale_marker_is_absent(store: ModelStore) -> None:
    store.install_bytes(PAYLOAD)
    store.path().unlink()

    status = store.verify()

    assert status.reason == "absent"
    assert not store.marker_path().exists()


def test_missing_final_file_is_not_installed_even_with_a_marker(store: ModelStore) -> None:
    store.install_bytes(PAYLOAD)
    store.path().unlink()

    assert store.is_installed() is False
    assert store.status().reason == "absent"


def test_status_is_cheap_and_only_verify_claims_a_fresh_hash(store: ModelStore) -> None:
    store.install_bytes(PAYLOAD)

    cheap = store.status()

    assert cheap.installed is True
    assert cheap.verified is False
    assert cheap.needs_download is False
    assert cheap.verified_at is not None

    fresh = store.verify()

    assert fresh.verified is True
    assert fresh.sha256 == PAYLOAD_SHA256


def test_delete_removes_the_weights_partial_and_marker(store: ModelStore) -> None:
    store.install_bytes(PAYLOAD)
    store.partial_path().parent.mkdir(parents=True, exist_ok=True)
    store.partial_path().write_bytes(b"partial")

    assert store.delete() is True
    assert not store.path().exists()
    assert not store.partial_path().exists()
    assert not store.marker_path().exists()
    assert store.delete() is False


def test_store_lives_under_the_models_root(app_paths: AppPaths) -> None:
    store = ModelStore(app_paths, spec=_spec())

    assert store.root == app_paths.models
    assert store.path() == app_paths.models / "htdemucs" / "test-weights.safetensors"


# --- The install Job -------------------------------------------------------


def test_install_job_downloads_verifies_and_installs(store: ModelStore) -> None:
    transport = _ScriptedTransport([_Response(PAYLOAD)])
    ctx, updates = _context()

    result = _job(store, transport).run(ctx)

    assert isinstance(result, ModelInstallResult)
    assert result.downloaded is True
    assert result.status.verified is True
    assert result.status.sha256 == PAYLOAD_SHA256
    assert transport.calls == 1
    assert store.path().read_bytes() == PAYLOAD
    assert not store.partial_path().exists()
    stages = {stage for _, stage, _ in updates}
    assert {stage for stage in stages if stage} == {"Consent", "Download", "Verify", "Install"}


def test_progress_follows_content_length_and_never_regresses(store: ModelStore) -> None:
    store = ModelStore(store.paths, spec=_spec(size=len(PROGRESS_PAYLOAD), sha256=PROGRESS_SHA256))
    transport = _ScriptedTransport([_Response(PROGRESS_PAYLOAD, chunk_size=256 * 1024)])
    ctx, updates = _context()

    _job(store, transport).run(ctx)

    fractions = [fraction for fraction, _, _ in updates]
    assert fractions == sorted(fractions)
    assert fractions[0] == 0.0
    assert fractions[-1] == 1.0
    download = [fraction for fraction, stage, _ in updates if stage == "Download"]
    assert len(download) > 3
    assert all(0.02 <= value <= 0.90 for value in download)
    # Half of the declared Content-Length lands near the middle of the stage.
    assert any(abs(value - (0.02 + 0.88 * 0.5)) < 0.15 for value in download)


def test_progress_has_a_monotonic_fallback_without_content_length(store: ModelStore) -> None:
    store = ModelStore(store.paths, spec=_spec(size=len(PROGRESS_PAYLOAD), sha256=PROGRESS_SHA256))
    transport = _ScriptedTransport([_Response(PROGRESS_PAYLOAD, content_length=False)])
    ctx, updates = _context()

    _job(store, transport).run(ctx)

    download = [fraction for fraction, stage, _ in updates if stage == "Download"]
    assert len(download) > 3
    assert download == sorted(download)
    assert download[-1] < 0.90
    assert updates[-1][0] == 1.0


def test_cancel_deletes_the_partial_and_keeps_no_weights(store: ModelStore) -> None:
    token = CancelToken()
    response = _Response(
        PROGRESS_PAYLOAD,
        chunk_size=64 * 1024,
        on_read=lambda offset: token.cancel() if offset else None,
    )
    transport = _ScriptedTransport([response])
    ctx, _ = _context(token)

    with pytest.raises(JobCancelled):
        _job(store, transport).run(ctx)

    assert not store.path().exists()
    assert not store.partial_path().exists()
    assert not store.marker_path().exists()


def test_a_transient_network_failure_is_not_retried(store: ModelStore) -> None:
    transport = _ScriptedTransport(
        [urllib.error.URLError(OSError("temporary outage")), _Response(PAYLOAD)]
    )
    ctx, _ = _context()

    with pytest.raises(StemForgeError) as raised:
        _job(store, transport).run(ctx)

    assert raised.value.code == errors.SEPARATION_MODEL_DOWNLOAD_FAILED
    assert raised.value.detail == "reason=offline"
    assert raised.value.retryable is True
    assert transport.calls == 1
    assert not store.path().exists()
    assert not store.partial_path().exists()


def test_persistent_offline_failure_is_coded_and_leaves_no_partial(store: ModelStore) -> None:
    transport = _ScriptedTransport([urllib.error.URLError(OSError("no route to host"))])
    ctx, _ = _context()

    with pytest.raises(StemForgeError) as raised:
        _job(store, transport).run(ctx)

    assert raised.value.code == errors.SEPARATION_MODEL_DOWNLOAD_FAILED
    assert raised.value.detail == "reason=offline"
    assert raised.value.retryable is True
    assert transport.calls == 1
    assert not store.partial_path().exists()


def test_http_error_is_coded_as_an_http_failure(store: ModelStore) -> None:
    transport = _ScriptedTransport(
        [urllib.error.HTTPError(store.spec.download_url, 404, "Not Found", {}, None)]
    )
    ctx, _ = _context()

    with pytest.raises(StemForgeError) as raised:
        _job(store, transport).run(ctx)

    assert raised.value.code == errors.SEPARATION_MODEL_DOWNLOAD_FAILED
    assert raised.value.detail == "reason=http"
    assert transport.calls == 1


def test_a_transient_5xx_is_not_retried(store: ModelStore) -> None:
    transport = _ScriptedTransport(
        [
            urllib.error.HTTPError(store.spec.download_url, 503, "Unavailable", {}, None),
            _Response(PAYLOAD),
        ]
    )
    ctx, _ = _context()

    with pytest.raises(StemForgeError) as raised:
        _job(store, transport).run(ctx)

    assert raised.value.code == errors.SEPARATION_MODEL_DOWNLOAD_FAILED
    assert raised.value.detail == "reason=http"
    assert transport.calls == 1
    assert not store.partial_path().exists()


def test_size_mismatch_is_a_checksum_failure_and_leaves_no_files(store: ModelStore) -> None:
    store = ModelStore(store.paths, spec=_spec(size=len(PAYLOAD) + 1))
    transport = _ScriptedTransport([_Response(PAYLOAD)])
    ctx, _ = _context()

    with pytest.raises(StemForgeError) as raised:
        _job(store, transport).run(ctx)

    assert raised.value.code == errors.SEPARATION_MODEL_MISSING
    assert raised.value.detail == "reason=checksum"
    assert not store.path().exists()
    assert not store.partial_path().exists()
    assert not store.marker_path().exists()


def test_sha_mismatch_is_a_checksum_failure_and_leaves_no_files(store: ModelStore) -> None:
    store = ModelStore(store.paths, spec=_spec(sha256="0" * 64))
    transport = _ScriptedTransport([_Response(PAYLOAD)])
    ctx, _ = _context()

    with pytest.raises(StemForgeError) as raised:
        _job(store, transport).run(ctx)

    assert raised.value.code == errors.SEPARATION_MODEL_MISSING
    assert raised.value.detail == "reason=checksum"
    assert not store.path().exists()
    assert not store.partial_path().exists()


def test_consent_is_required_before_any_download(store: ModelStore) -> None:
    transport = _ScriptedTransport([_Response(PAYLOAD)])
    ctx, _ = _context()

    with pytest.raises(StemForgeError) as raised:
        _job(store, transport, give_consent=False).run(ctx)

    assert raised.value.code == errors.SEPARATION_MODEL_MISSING
    assert raised.value.detail == "reason=consent"
    assert transport.calls == 0
    assert not store.partial_path().exists()


def test_a_stale_consent_version_is_refused(store: ModelStore) -> None:
    transport = _ScriptedTransport([_Response(PAYLOAD)])
    ctx, _ = _context()

    with pytest.raises(StemForgeError) as raised:
        _job(store, transport, consent=_consent(version=0)).run(ctx)

    assert raised.value.detail == "reason=consent"
    assert transport.calls == 0


def test_already_installed_is_idempotent_and_never_downloads(store: ModelStore) -> None:
    store.install_bytes(PAYLOAD)
    transport = _ScriptedTransport([_Response(PAYLOAD)])
    ctx, updates = _context()

    result = _job(store, transport, give_consent=False).run(ctx)

    assert result.downloaded is False
    assert result.status.verified is True
    assert transport.calls == 0
    assert updates[-1][0] == 1.0


def test_install_job_rejects_a_pin_without_a_sha() -> None:
    with pytest.raises(ValueError, match="sha256"):
        ModelSpec(
            name="htdemucs",
            repo_id="example/repo",
            revision="rev",
            filename="weights.safetensors",
            size_bytes=1,
            sha256="",
            download_url="https://example.invalid/w",
            source_url="https://example.invalid/w",
            licence="test",
            licence_notice="test",
            licence_notice_version=1,
        )
