"""One source-manifest writer for local and YouTube imports."""

from __future__ import annotations

import pytest

from stemforge.artifacts import SCHEMA_VERSION
from stemforge.domain.models import OriginKind

pytestmark = pytest.mark.unit


def test_source_manifest_carries_the_store_schema_and_shared_fields() -> None:
    from stemforge.pipeline.source_manifest import source_manifest

    manifest = source_manifest(
        source_id="src-1",
        origin="/music/song.wav",
        origin_kind=OriginKind.FILE,
        content_hash="a" * 64,
        gain_db=-1.5,
        downmixed=True,
        metadata={"title": "Song"},
        files=[{"path": "canonical.wav", "role": "canonical"}],
    )

    assert manifest["schema_version"] == SCHEMA_VERSION
    assert manifest["kind"] == "source"
    assert manifest["source_id"] == "src-1"
    assert manifest["origin"] == "/music/song.wav"
    assert manifest["origin_kind"] == "file"
    assert manifest["content_hash"] == "a" * 64
    assert manifest["gain_db"] == -1.5
    assert manifest["downmixed"] is True
    assert manifest["metadata"] == {"title": "Song"}
    assert manifest["files"] == [{"path": "canonical.wav", "role": "canonical"}]
    assert manifest["created_at"]
    assert manifest["app_version"]


def test_source_manifest_adds_origin_specific_fields() -> None:
    from stemforge.pipeline.source_manifest import source_manifest

    manifest = source_manifest(
        source_id="yt-abc",
        origin="https://www.youtube.com/watch?v=abc",
        origin_kind=OriginKind.YOUTUBE,
        content_hash="b" * 64,
        gain_db=0.0,
        downmixed=False,
        metadata={"title": "Video"},
        files=[],
        extra={"video_id": "abc", "native_bytes": 12},
    )

    assert manifest["origin_kind"] == "youtube"
    assert manifest["video_id"] == "abc"
    assert manifest["native_bytes"] == 12
