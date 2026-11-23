"""SettingsStore: atomic JSON, corruption recovery, and the recent-files MRU."""

from __future__ import annotations

import json
import logging
import shutil
import stat
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stemforge.domain.models import OriginKind
from stemforge.platform.paths import AppPaths
from stemforge.settings import MAX_RECENT_FILES, SettingsStore


@pytest.fixture
def paths(tmp_path: Path) -> AppPaths:
    return AppPaths.for_home(tmp_path / "home").ensure()


@pytest.mark.unit
def test_defaults_are_used_when_no_settings_file_exists(paths: AppPaths) -> None:
    store = SettingsStore(paths)

    assert store.path == paths.config / "settings.json"
    assert store.settings.schema_version == 1
    assert store.first_run is True
    assert store.preferred_export_dir is None
    assert store.recent_files() == ()
    assert store.settings.cache_cap_bytes == 8 * 1024**3
    assert not store.path.exists()


@pytest.mark.unit
def test_record_recent_persists_the_full_entry(paths: AppPaths) -> None:
    store = SettingsStore(paths)
    opened_at = datetime(2026, 11, 16, 12, 30, tzinfo=UTC)

    entry = store.record_recent(
        source_id="src-1",
        title="Song",
        origin="/music/song.wav",
        origin_kind=OriginKind.FILE,
        duration_s=184.5,
        opened_at=opened_at,
    )

    assert entry.source_id == "src-1"
    assert entry.origin_kind is OriginKind.FILE
    assert entry.last_opened == opened_at
    reloaded = SettingsStore(paths)
    assert reloaded.recent_files() == (entry,)
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert payload["recent"][0] == {
        "source_id": "src-1",
        "title": "Song",
        "origin_kind": "file",
        "origin": "/music/song.wav",
        "duration_s": 184.5,
        "last_opened": opened_at.isoformat(),
    }


@pytest.mark.unit
def test_record_recent_keeps_the_ten_newest_and_moves_a_repeat_to_the_front(
    paths: AppPaths,
) -> None:
    store = SettingsStore(paths)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for index in range(12):
        store.record_recent(
            source_id=f"src-{index}",
            title=f"Track {index}",
            origin=f"/music/{index}.wav",
            origin_kind=OriginKind.FILE,
            opened_at=start + timedelta(minutes=index),
        )

    assert len(store.recent_files()) == MAX_RECENT_FILES == 10
    assert [entry.source_id for entry in store.recent_files()] == [
        f"src-{index}" for index in range(11, 1, -1)
    ]

    newest = start + timedelta(days=1)
    store.record_recent(
        source_id="src-5",
        title="Track 5 renamed",
        origin="/music/5.wav",
        origin_kind=OriginKind.FILE,
        opened_at=newest,
    )

    recent = store.recent_files()
    assert len(recent) == 10
    assert recent[0].source_id == "src-5"
    assert recent[0].title == "Track 5 renamed"
    assert recent[0].last_opened == newest
    assert len({entry.source_id for entry in recent}) == 10
    assert [entry.source_id for entry in SettingsStore(paths).recent_files()] == [
        entry.source_id for entry in recent
    ]


@pytest.mark.unit
def test_remove_and_clear_recent(paths: AppPaths) -> None:
    store = SettingsStore(paths)
    for source_id in ("one", "two"):
        store.record_recent(
            source_id=source_id,
            title=source_id,
            origin=f"/music/{source_id}.wav",
            origin_kind=OriginKind.FILE,
        )

    assert store.remove_recent("one") is True
    assert store.remove_recent("one") is False
    assert [entry.source_id for entry in store.recent_files()] == ["two"]

    store.clear_recent()

    assert store.recent_files() == ()
    assert SettingsStore(paths).recent_files() == ()


@pytest.mark.unit
def test_export_directory_and_first_run_persist(paths: AppPaths) -> None:
    store = SettingsStore(paths)
    destination = paths.data / "exports"
    destination.mkdir()

    store.set_export_dir(destination)
    store.mark_first_run_complete()

    reloaded = SettingsStore(paths)
    assert reloaded.preferred_export_dir == destination
    assert reloaded.first_run is False


@pytest.mark.unit
def test_save_writes_valid_json(paths: AppPaths) -> None:
    store = SettingsStore(paths)

    store.save()

    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert payload == {
        "schema_version": 1,
        "recent": [],
        "export_dir": None,
        "first_run": True,
        "cache_cap_bytes": 8 * 1024**3,
        "models": {"consent": None},
        "youtube_enabled": True,
        "youtube_notice_acknowledged": False,
        "youtube_notice_version": 0,
        "youtube_notice_accepted_at": None,
        "separation_method": None,
    }


@pytest.mark.unit
def test_separation_method_defaults_to_unset_and_round_trips(paths: AppPaths) -> None:
    store = SettingsStore(paths)

    assert store.separation_method() is None

    store.set_separation_method("karaoke")

    assert store.separation_method() == "karaoke"
    assert SettingsStore(paths).separation_method() == "karaoke"
    assert json.loads(store.path.read_text(encoding="utf-8"))["separation_method"] == "karaoke"

    store.set_separation_method(None)

    assert store.separation_method() is None
    assert SettingsStore(paths).separation_method() is None


@pytest.mark.unit
@pytest.mark.parametrize("value", ["repet-sim", 42, True, ["demucs"], {"id": "demucs"}])
def test_an_unknown_or_malformed_separation_method_is_unset(paths: AppPaths, value: object) -> None:
    (paths.config / "settings.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "separation_method": value,
                "first_run": False,
                "cache_cap_bytes": 4096,
            }
        ),
        encoding="utf-8",
    )

    store = SettingsStore(paths)

    assert store.separation_method() is None
    assert store.first_run is False
    assert store.settings.cache_cap_bytes == 4096
    assert not list(paths.config.glob("settings.corrupt-*.json"))


@pytest.mark.unit
def test_model_consent_defaults_to_none(paths: AppPaths) -> None:
    store = SettingsStore(paths)

    assert store.model_consent() is None


@pytest.mark.unit
def test_accept_model_licence_persists_the_acknowledgment(paths: AppPaths) -> None:
    store = SettingsStore(paths)
    accepted_at = datetime(2026, 11, 17, 9, 30, tzinfo=UTC)

    consent = store.accept_model_licence(version=1, accepted_at=accepted_at)

    assert consent.version == 1
    assert consent.acknowledged is True
    assert consent.accepted_at == accepted_at
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert payload["models"] == {
        "consent": {
            "version": 1,
            "acknowledged": True,
            "accepted_at": accepted_at.isoformat(),
        }
    }
    reloaded = SettingsStore(paths)
    assert reloaded.model_consent() == consent


@pytest.mark.unit
def test_clear_model_consent_persists_the_removal(paths: AppPaths) -> None:
    store = SettingsStore(paths)
    store.accept_model_licence(version=1)

    store.clear_model_consent()

    assert store.model_consent() is None
    assert SettingsStore(paths).model_consent() is None
    assert json.loads(store.path.read_text(encoding="utf-8"))["models"]["consent"] is None


@pytest.mark.unit
def test_an_invalid_consent_record_means_no_consent_and_keeps_other_settings(
    paths: AppPaths,
) -> None:
    paths.config.mkdir(parents=True, exist_ok=True)
    (paths.config / "settings.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "recent": [],
                "export_dir": None,
                "first_run": False,
                "cache_cap_bytes": 1024,
                "models": {"consent": {"version": 0, "acknowledged": "yes"}},
            }
        ),
        encoding="utf-8",
    )

    store = SettingsStore(paths)

    assert store.model_consent() is None
    assert store.first_run is False
    assert store.settings.cache_cap_bytes == 1024
    assert not list(paths.config.glob("settings.corrupt-*.json"))


@pytest.mark.unit
def test_accept_model_licence_rejects_a_stale_version(paths: AppPaths) -> None:
    store = SettingsStore(paths)

    with pytest.raises(ValueError, match="version"):
        store.accept_model_licence(version=0)


@pytest.mark.unit
def test_youtube_settings_default_to_enabled_without_a_consent_record(
    paths: AppPaths,
) -> None:
    store = SettingsStore(paths)

    assert store.youtube_enabled is True
    assert store.youtube_notice_is_current() is False


@pytest.mark.unit
def test_youtube_enabled_and_notice_acknowledgment_persist(paths: AppPaths) -> None:
    store = SettingsStore(paths)
    accepted_at = datetime(2026, 11, 17, 10, 15, tzinfo=UTC)

    store.set_youtube_enabled(False)
    store.accept_youtube_notice(version=1, accepted_at=accepted_at)

    assert store.youtube_enabled is False
    assert store.youtube_notice_is_current() is True
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert payload["youtube_enabled"] is False
    assert payload["youtube_notice_acknowledged"] is True
    assert payload["youtube_notice_version"] == 1
    assert payload["youtube_notice_accepted_at"] == accepted_at.isoformat()

    reloaded = SettingsStore(paths)
    assert reloaded.youtube_enabled is False
    assert reloaded.youtube_notice_is_current() is True


@pytest.mark.unit
def test_re_enabling_youtube_keeps_the_acknowledgment(paths: AppPaths) -> None:
    store = SettingsStore(paths)
    store.accept_youtube_notice(version=1)

    store.set_youtube_enabled(False)
    store.set_youtube_enabled(True)

    reloaded = SettingsStore(paths)
    assert reloaded.youtube_enabled is True
    assert reloaded.youtube_notice_is_current() is True


@pytest.mark.unit
def test_an_incomplete_notice_record_means_no_consent_and_keeps_other_settings(
    paths: AppPaths,
) -> None:
    (paths.config / "settings.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "youtube_enabled": False,
                "youtube_notice_acknowledged": True,
                "youtube_notice_version": 0,
                "youtube_notice_accepted_at": None,
            }
        ),
        encoding="utf-8",
    )

    store = SettingsStore(paths)

    assert store.youtube_notice_is_current() is False
    assert store.youtube_enabled is False
    assert not list(paths.config.glob("settings.corrupt-*.json"))


@pytest.mark.unit
def test_accept_youtube_notice_rejects_a_zero_version(paths: AppPaths) -> None:
    store = SettingsStore(paths)

    with pytest.raises(ValueError, match="version"):
        store.accept_youtube_notice(version=0)


@pytest.mark.unit
def test_atomic_replace_leaves_the_old_file_intact_when_replace_fails(
    paths: AppPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SettingsStore(paths)
    store.set_export_dir(paths.data / "first")
    before = store.path.read_bytes()

    def crash(source: object, destination: object) -> None:
        raise OSError("simulated crash between write and replace")

    monkeypatch.setattr("stemforge.settings.os.replace", crash)

    with pytest.raises(OSError, match="simulated crash"):
        store.set_export_dir(paths.data / "second")

    assert store.preferred_export_dir == paths.data / "first"
    assert store.path.read_bytes() == before
    assert json.loads(before)["export_dir"] == str(paths.data / "first")
    assert list(paths.config.glob(".settings.json.*.tmp")) == []


@pytest.mark.unit
def test_corrupt_json_is_renamed_and_defaults_are_recreated(paths: AppPaths) -> None:
    settings_path = paths.config / "settings.json"
    settings_path.write_text('{"recent": [', encoding="utf-8")

    records: list[logging.LogRecord] = []

    class Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("stemforge.settings")
    previous_level = logger.level
    logger.setLevel(logging.WARNING)
    handler = Collector()
    logger.addHandler(handler)
    try:
        store = SettingsStore(paths)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    assert store.first_run is True
    assert store.recent_files() == ()
    corrupt = list(paths.config.glob("settings.corrupt-*.json"))
    assert len(corrupt) == 1
    assert corrupt[0].read_text(encoding="utf-8") == '{"recent": ['
    assert json.loads(settings_path.read_text(encoding="utf-8"))["recent"] == []
    assert any("corrupt" in record.getMessage().lower() for record in records)


@pytest.mark.unit
def test_json_with_the_wrong_shape_is_treated_as_corrupt(paths: AppPaths) -> None:
    (paths.config / "settings.json").write_text("[]", encoding="utf-8")

    store = SettingsStore(paths)

    assert store.first_run is True
    assert list(paths.config.glob("settings.corrupt-*.json"))


@pytest.mark.unit
def test_malformed_recent_entries_are_skipped(paths: AppPaths) -> None:
    (paths.config / "settings.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "recent": [
                    {"source_id": "keep", "title": "Keep", "origin": "/k.wav"},
                    {"source_id": 42},
                    "not an object",
                ],
                "export_dir": None,
                "first_run": False,
            }
        ),
        encoding="utf-8",
    )

    store = SettingsStore(paths)

    assert [entry.source_id for entry in store.recent_files()] == ["keep"]
    assert store.first_run is False


@pytest.mark.unit
def test_a_write_recreates_missing_roots(paths: AppPaths) -> None:
    store = SettingsStore(paths)
    shutil.rmtree(paths.config)

    store.set_export_dir(paths.data / "exports")

    assert paths.config.is_dir()
    assert store.path.is_file()
    assert SettingsStore(paths).preferred_export_dir == paths.data / "exports"


@pytest.mark.unit
@pytest.mark.platform_linux
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_settings_file_is_private(paths: AppPaths) -> None:
    store = SettingsStore(paths)

    store.save()

    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


@pytest.mark.unit
def test_cache_cap_bytes_is_loaded_and_negative_values_are_recovered(paths: AppPaths) -> None:
    (paths.config / "settings.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "recent": [],
                "export_dir": None,
                "first_run": False,
                "cache_cap_bytes": 4096,
            }
        ),
        encoding="utf-8",
    )

    assert SettingsStore(paths).settings.cache_cap_bytes == 4096

    (paths.config / "settings.json").write_text(
        json.dumps({"schema_version": 1, "cache_cap_bytes": -1}),
        encoding="utf-8",
    )

    recovered = SettingsStore(paths)
    assert recovered.settings.cache_cap_bytes == 8 * 1024**3
    assert list(paths.config.glob("settings.corrupt-*.json"))
