"""Integration: the model store through settings, the Job, and real hashing.

The "server" is a file in ``tmp_path``; the transport streams its bytes through
the same hashing, partial, ``os.replace``, and marker path production uses. No
test opens a socket, and every write stays under ``STEMFORGE_HOME``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from stemforge import errors
from stemforge.application import WorkspaceService
from stemforge.domain.models import JobState
from stemforge.errors import StemForgeError
from stemforge.models import ModelSpec, ModelStore
from stemforge.platform.paths import AppPaths
from stemforge.settings import SettingsStore

pytestmark = pytest.mark.integration

PAYLOAD = (b"stemforge-model-integration\n" * 65_536)[: 2 * 1024 * 1024]
PAYLOAD_SHA256 = hashlib.sha256(PAYLOAD).hexdigest()


class _FileResponse:
    """A binary response that streams a file from a temp directory."""

    def __init__(self, source: Path) -> None:
        self._handle = source.open("rb")
        self.headers = {"Content-Length": str(source.stat().st_size)}

    def read(self, size: int = -1, /) -> bytes:
        return self._handle.read(size)

    def close(self) -> None:
        self._handle.close()


def _file_transport(source: Path) -> Callable[[str], _FileResponse]:
    def transport(url: str) -> _FileResponse:
        assert url
        return _FileResponse(source)

    return transport


def _spec(size: int, sha256: str) -> ModelSpec:
    return ModelSpec(
        name="htdemucs",
        repo_id="example/HTDemucs",
        revision="integration-revision",
        filename="integration-weights.safetensors",
        size_bytes=size,
        sha256=sha256,
        download_url="https://example.invalid/integration-weights.safetensors",
        source_url="https://example.invalid/integration-weights.safetensors",
        licence="test licence",
        licence_notice="test notice",
        licence_notice_version=1,
    )


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[AppPaths, Path]:
    portable = tmp_path / "portable"
    monkeypatch.setenv("STEMFORGE_HOME", str(portable))
    paths = AppPaths.from_environment().ensure()
    return paths, portable


@pytest.fixture
def payload_file(tmp_path: Path) -> Path:
    source = tmp_path / "server" / "integration-weights.safetensors"
    source.parent.mkdir(parents=True)
    source.write_bytes(PAYLOAD)
    return source


def _service(paths: AppPaths) -> WorkspaceService:
    settings = SettingsStore(paths)
    return WorkspaceService(
        settings,
        paths=paths,
        models=ModelStore(paths, spec=_spec(len(PAYLOAD), PAYLOAD_SHA256)),
    )


def test_model_store_lives_under_stemforge_home(
    home: tuple[AppPaths, Path], payload_file: Path
) -> None:
    paths, portable = home
    service = _service(paths)

    assert service.models.root == portable / "data" / "models"

    service.accept_model_licence()
    runner = service.runner
    runner.start()
    try:
        job_id = service.install_model(transport=_file_transport(payload_file))
        assert runner.wait_idle(timeout=30) is True
        snapshot = runner.snapshot(job_id)
    finally:
        runner.stop()

    assert snapshot.state is JobState.SUCCEEDED
    assert snapshot.error_code is None
    assert service.models.path().read_bytes() == PAYLOAD
    assert service.models.path().is_relative_to(portable)
    assert not service.models.partial_path().exists()
    marker = json.loads(service.models.marker_path().read_text(encoding="utf-8"))
    assert marker["sha256"] == PAYLOAD_SHA256
    assert marker["size"] == len(PAYLOAD)
    assert marker["revision"] == "integration-revision"
    assert service.model_status().installed is True
    assert service.models.verify().verified is True


def test_consent_is_persisted_before_the_job_downloads(
    home: tuple[AppPaths, Path], payload_file: Path
) -> None:
    paths, _ = home
    service = _service(paths)

    with pytest.raises(StemForgeError) as refused:
        service.install_model(transport=_file_transport(payload_file))
    assert refused.value.code == errors.SEPARATION_MODEL_MISSING
    assert refused.value.detail == "reason=consent"
    assert service.runner.jobs() == []

    service.accept_model_licence()

    assert SettingsStore(paths).model_consent() is not None
    assert service.model_consent_is_current() is True


def test_tampering_after_install_is_caught_before_use(
    home: tuple[AppPaths, Path], payload_file: Path
) -> None:
    paths, _ = home
    service = _service(paths)
    service.accept_model_licence()
    runner = service.runner
    runner.start()
    try:
        job_id = service.install_model(transport=_file_transport(payload_file))
        assert runner.wait_idle(timeout=30) is True
        assert runner.snapshot(job_id).state is JobState.SUCCEEDED
    finally:
        runner.stop()

    tampered = bytearray(PAYLOAD)
    tampered[-1] ^= 0x01
    service.models.path().write_bytes(bytes(tampered))

    with pytest.raises(StemForgeError) as raised:
        service.models.verify_before_use()
    assert raised.value.code == errors.SEPARATION_MODEL_MISSING
    assert raised.value.detail == "reason=checksum"
    assert not service.models.path().exists()
    assert not service.models.marker_path().exists()
    assert service.model_status().needs_download is True


def test_install_bytes_diagnostic_uses_the_real_install_path(
    home: tuple[AppPaths, Path],
) -> None:
    paths, portable = home
    store = ModelStore(paths, spec=_spec(len(PAYLOAD), PAYLOAD_SHA256))

    status = store.install_bytes(PAYLOAD)

    assert status.verified is True
    assert store.path().read_bytes() == PAYLOAD
    assert store.path().is_relative_to(portable / "data" / "models")
    assert store.marker_path().is_file()
