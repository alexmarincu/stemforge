"""The pinned model identity, read from the bundled ``models.lock.json``.

The lock file is the single record of what may be downloaded. It is read-only
package data: the weights themselves are never bundled, mirrored, or committed
(ADR 0009, ticket 13 §7).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final

from stemforge import errors
from stemforge.errors import StemForgeError
from stemforge.platform import resources

MODEL_NAME: Final = "htdemucs"

_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """One downloadable model, pinned by revision, size, and SHA-256."""

    name: str
    repo_id: str
    revision: str
    filename: str
    size_bytes: int
    sha256: str
    download_url: str
    source_url: str
    licence: str
    licence_notice: str
    licence_notice_version: int = 1

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("model name must not be empty")
        if not self.revision:
            raise ValueError("model revision must not be empty")
        if not self.filename or Path(self.filename).name != self.filename:
            raise ValueError("model filename must be a bare file name")
        if self.size_bytes <= 0:
            raise ValueError("model size must be positive")
        if _SHA256_PATTERN.fullmatch(self.sha256) is None:
            raise ValueError("model sha256 must be 64 lowercase hex characters")
        if not self.download_url.startswith(("https://", "http://")):
            raise ValueError("model download_url must be an HTTP(S) URL")
        if self.licence_notice_version < 1:
            raise ValueError("model licence notice version must be positive")


def load_spec(name: str = MODEL_NAME, *, path: Path | None = None) -> ModelSpec:
    """Read one model record from a lock file (the bundled one by default)."""
    lock_path = resources.models_lock_path() if path is None else Path(path)
    try:
        payload: object = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StemForgeError(
            errors.SEPARATION_ENGINE_ERROR,
            "the bundled model pin is unreadable",
            detail=f"{lock_path}: {type(exc).__name__}: {exc}",
            context={"path": str(lock_path)},
        ) from exc

    models = payload.get("models") if isinstance(payload, dict) else None
    record = models.get(name) if isinstance(models, Mapping) else None
    if not isinstance(record, Mapping):
        raise StemForgeError(
            errors.SEPARATION_ENGINE_ERROR,
            "the bundled model pin has no record for this model",
            detail=f"{lock_path}: missing models.{name}",
            context={"path": str(lock_path), "model": name},
        )

    try:
        return ModelSpec(
            name=name,
            repo_id=_required_str(record, "repo_id"),
            revision=_required_str(record, "revision"),
            filename=_required_str(record, "filename"),
            size_bytes=_required_int(record, "size"),
            sha256=_required_str(record, "sha256"),
            download_url=_required_str(record, "download_url"),
            source_url=_required_str(record, "source_url"),
            licence=_required_str(record, "license"),
            licence_notice=_required_str(record, "licence_notice"),
            licence_notice_version=_required_int(record, "licence_notice_version"),
        )
    except ValueError as exc:
        raise StemForgeError(
            errors.SEPARATION_ENGINE_ERROR,
            "the bundled model pin is invalid",
            detail=f"{lock_path}: {exc}",
            context={"path": str(lock_path), "model": name},
        ) from exc


@lru_cache(maxsize=1)
def default_spec() -> ModelSpec:
    """The pinned ``htdemucs`` record, loaded once per process."""
    return load_spec()


def _required_str(record: Mapping[str, object], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _required_int(record: Mapping[str, object], key: str) -> int:
    value = record.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    return value
