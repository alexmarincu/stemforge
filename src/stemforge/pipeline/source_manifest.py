"""The single writer for ``source.json`` (ADR 0004).

Local and YouTube imports share every field except the origin-specific record,
so the schema version, kind, timestamps, and app version have one source here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Final

from stemforge import __version__
from stemforge.artifacts import SCHEMA_VERSION
from stemforge.domain.models import OriginKind, StereoLayout

__all__ = ["source_manifest"]

SOURCE_KIND: Final = "source"


def source_manifest(
    *,
    source_id: str,
    origin: str,
    origin_kind: OriginKind,
    content_hash: str,
    gain_db: float,
    downmixed: bool,
    metadata: Mapping[str, object],
    files: Sequence[Mapping[str, object]],
    original_channels: int | None = None,
    stereo_layout: StereoLayout | None = None,
    extra: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build one schema-versioned source manifest.

    ``original_channels`` and ``stereo_layout`` are additive facts at schema
    version 1; a legacy manifest omits them and reads back as unknown.
    ``extra`` carries only the origin-specific fields (YouTube's video id,
    extractor record, and native hash); it cannot override the shared keys.
    """
    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "kind": SOURCE_KIND,
        "source_id": source_id,
        "created_at": datetime.now(UTC).isoformat(),
        "origin": origin,
        "origin_kind": origin_kind.value,
        "content_hash": content_hash,
        "app_version": __version__,
        "gain_db": gain_db,
        "downmixed": downmixed,
        "metadata": dict(metadata),
        "files": [dict(entry) for entry in files],
    }
    if original_channels is not None:
        payload["original_channels"] = original_channels
    if stereo_layout is not None:
        payload["stereo_layout"] = stereo_layout.value
    if extra:
        reserved = payload.keys() & extra.keys()
        if reserved:
            raise ValueError(f"extra fields collide with the source manifest: {sorted(reserved)}")
        payload.update(extra)
    return payload
