"""ArtifactStore against a real temporary cache: persistence, crashes, LRU, removal."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import textwrap
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stemforge.artifacts import ArtifactStore, require_free_space
from stemforge.platform.paths import AppPaths


def _entry(name: str, data: bytes, role: str | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "path": name,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    if role is not None:
        payload["role"] = role
    return payload


def _commit_source(store: ArtifactStore, source_id: str, data: bytes = b"canonical") -> None:
    staging = store.stage(f"job-{source_id}")
    (staging / "canonical.wav").write_bytes(data)
    store.commit_source(
        staging,
        source_id,
        {
            "schema_version": 1,
            "kind": "source",
            "source_id": source_id,
            "created_at": datetime(2026, 11, 16, 12, 0, tzinfo=UTC).isoformat(),
            "metadata": {"title": source_id},
            "files": [_entry("canonical.wav", data, "canonical")],
        },
    )


def _commit_render(store: ArtifactStore, render_key: str, source_id: str) -> None:
    staging = store.stage(f"job-{render_key}")
    (staging / "original.wav").write_bytes(b"rendered")
    (staging / "original.mp3").write_bytes(b"preview")
    store.commit_render(
        staging,
        render_key,
        {
            "schema_version": 1,
            "kind": "render",
            "render_key": render_key,
            "source_id": source_id,
            "created_at": datetime(2026, 11, 16, 12, 0, tzinfo=UTC).isoformat(),
            "semitones": 2,
            "files": [
                _entry("original.wav", b"rendered", "original"),
                _entry("original.mp3", b"preview", "original_preview"),
            ],
        },
    )


def _dir_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


@pytest.mark.integration
def test_cache_survives_a_restart_and_sweep_keeps_completed_work(tmp_path: Path) -> None:
    paths = AppPaths.for_home(tmp_path / "home").ensure()
    first = ArtifactStore(paths)
    _commit_source(first, "abc123")
    _commit_render(first, "render123", "abc123")
    (first.tmp_dir / "leftover.bin").write_bytes(b"tmp")

    second = ArtifactStore(paths)

    assert second.find_source("abc123") is not None
    assert second.find_render("render123") is not None
    assert second.usage_bytes() > 0
    report = second.sweep_stale()
    assert report.deleted_anything is True
    assert second.find_source("abc123") is not None
    assert second.find_render("render123") is not None


@pytest.mark.integration
def test_a_crash_between_stage_and_commit_leaves_no_visible_artifact(tmp_path: Path) -> None:
    home = tmp_path / "home"
    script = textwrap.dedent(
        """
        import os
        import sys

        from stemforge.artifacts import ArtifactStore
        from stemforge.platform.paths import AppPaths

        store = ArtifactStore(AppPaths.for_home(sys.argv[1]))
        staging = store.stage("crashed-job")
        (staging / "canonical.wav").write_bytes(b"unfinished")
        os._exit(9)
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(home)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == 9, result.stderr
    assert (home / "cache" / "staging" / "crashed-job" / "canonical.wav").is_file()

    store = ArtifactStore(AppPaths.for_home(home))
    assert store.find_source("crashed-job") is None
    report = store.sweep_stale()

    assert report.deleted_anything is True
    assert report.removed_staging == (home / "cache" / "staging" / "crashed-job",)
    assert list((home / "cache" / "sources").iterdir()) == []
    assert list((home / "cache" / "staging").iterdir()) == []


@pytest.mark.integration
def test_lru_evicts_whole_source_subtrees_on_a_real_cache(tmp_path: Path) -> None:
    paths = AppPaths.for_home(tmp_path / "home").ensure()
    store = ArtifactStore(paths)
    _commit_source(store, "oldest", b"a" * 1000)
    _commit_source(store, "middle", b"b" * 2000)
    _commit_source(store, "newest", b"c" * 3000)
    for key, when in (("oldest", 100.0), ("middle", 200.0), ("newest", 300.0)):
        marker = store.sources_dir / key / ".used"
        os.utime(marker, (when, when))
    sizes = {key: _dir_size(store.sources_dir / key) for key in ("oldest", "middle", "newest")}
    capped = ArtifactStore(paths, cap_bytes=sizes["middle"] + sizes["newest"])

    report = capped.enforce_cap(set())

    assert [entry.key for entry in report.evicted] == ["oldest"]
    assert not (paths.cache / "sources" / "oldest").exists()
    assert (paths.cache / "sources" / "middle").is_dir()
    assert (paths.cache / "sources" / "newest").is_dir()
    assert capped.usage_bytes() == report.usage_after


@pytest.mark.integration
def test_remove_source_and_clear_cache_on_a_real_cache(tmp_path: Path) -> None:
    paths = AppPaths.for_home(tmp_path / "home").ensure()
    store = ArtifactStore(paths)
    _commit_source(store, "abc123")
    _commit_render(store, "derived", "abc123")
    _commit_source(store, "other")
    (paths.cache / "deno").mkdir()
    (paths.cache / "deno" / "cache.bin").write_bytes(b"deno")
    model = paths.models / "weights.bin"
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"weights")

    removed = store.remove_source("abc123")

    assert removed.source_removed is True
    assert removed.renders_removed == ("derived",)
    assert not (paths.cache / "sources" / "abc123").exists()
    assert not (paths.cache / "renders" / "derived").exists()
    assert (paths.cache / "sources" / "other").is_dir()

    cleared = store.clear_cache()

    assert cleared.sources_removed == ("other",)
    assert list((paths.cache / "sources").iterdir()) == []
    assert not (paths.cache / "deno").exists()
    assert model.is_file()


@pytest.mark.integration
def test_stemforge_home_subprocess_writes_only_under_the_portable_root(tmp_path: Path) -> None:
    home = tmp_path / "portable"
    work = tmp_path / "work"
    work.mkdir()
    script = textwrap.dedent(
        """
        import sys

        from stemforge.artifacts import ArtifactStore
        from stemforge.platform.paths import AppPaths

        paths = AppPaths.from_environment()
        store = ArtifactStore(paths)
        staging = store.stage("job")
        (staging / "canonical.wav").write_bytes(b"audio")
        store.commit_source(
            staging,
            "abc123",
            {
                "schema_version": 1,
                "kind": "source",
                "source_id": "abc123",
                "files": [{"path": "canonical.wav", "role": "canonical"}],
            },
        )
        print(paths.cache)
        """
    )
    env = os.environ.copy()
    env["STEMFORGE_HOME"] = str(home)
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str((home / "cache").resolve())
    assert (home / "cache" / "sources" / "abc123" / "source.json").is_file()
    assert list(work.iterdir()) == []


@pytest.mark.integration
def test_free_space_check_passes_on_the_real_cache_volume(tmp_path: Path) -> None:
    paths = AppPaths.for_home(tmp_path / "home").ensure()

    require_free_space(paths.cache, 1, floor=0)


@pytest.mark.integration
def test_a_valid_manifest_is_inspectable_json(tmp_path: Path) -> None:
    paths = AppPaths.for_home(tmp_path / "home").ensure()
    store = ArtifactStore(paths)
    _commit_source(store, "abc123")

    payload = json.loads((paths.cache / "sources" / "abc123" / "source.json").read_text())

    assert payload["schema_version"] == 1
    assert payload["kind"] == "source"
    assert payload["files"][0]["path"] == "canonical.wav"
    assert not any(str(path).startswith("/") for path in payload["files"][0].values())
