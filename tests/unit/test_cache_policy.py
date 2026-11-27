"""Pure LRU/cap policy for the audio cache."""

from __future__ import annotations

import pytest

from stemforge.artifacts import CACHE_KIND_RENDER, CACHE_KIND_SOURCE, CacheEntry, plan_evictions


def _entry(
    key: str,
    *,
    size: int = 100,
    used_at: float = 0.0,
    kind: str = CACHE_KIND_SOURCE,
    source_id: str | None = None,
) -> CacheEntry:
    return CacheEntry(
        kind=kind,
        key=key,
        size_bytes=size,
        used_at=used_at,
        source_id=source_id
        if source_id is not None
        else (key if kind == CACHE_KIND_SOURCE else None),
    )


@pytest.mark.unit
def test_usage_at_or_under_the_cap_evicts_nothing() -> None:
    entries = [_entry("a", size=60), _entry("b", size=40)]

    plan = plan_evictions(entries, cap_bytes=100, live_keys=set())

    assert plan.evicted == ()
    assert plan.usage_before == 100
    assert plan.remaining_bytes == 100
    assert plan.over_cap is False


@pytest.mark.unit
def test_oldest_entries_are_evicted_first_until_under_the_cap() -> None:
    entries = [
        _entry("newest", size=100, used_at=30.0),
        _entry("oldest", size=100, used_at=10.0),
        _entry("middle", size=100, used_at=20.0),
    ]

    plan = plan_evictions(entries, cap_bytes=150, live_keys=set())

    assert [entry.key for entry in plan.evicted] == ["oldest", "middle"]
    assert plan.usage_before == 300
    assert plan.remaining_bytes == 100
    assert plan.over_cap is False


@pytest.mark.unit
def test_equal_recency_is_broken_deterministically_by_kind_then_key() -> None:
    entries = [
        _entry("b", used_at=1.0),
        _entry("a", used_at=1.0),
        _entry("z", used_at=1.0, kind=CACHE_KIND_RENDER),
    ]

    plan = plan_evictions(entries, cap_bytes=100, live_keys=set())

    assert [entry.key for entry in plan.evicted] == ["a", "b"]


@pytest.mark.unit
def test_live_keys_are_never_evicted() -> None:
    entries = [
        _entry("old-live", size=100, used_at=1.0),
        _entry("old-dead", size=100, used_at=2.0),
        _entry("new-dead", size=100, used_at=3.0),
    ]

    plan = plan_evictions(entries, cap_bytes=200, live_keys={"old-live"})

    assert [entry.key for entry in plan.evicted] == ["old-dead"]


@pytest.mark.unit
def test_a_live_source_protects_its_derived_renders() -> None:
    entries = [
        _entry("source-a", size=100, used_at=1.0),
        _entry("render-a", size=100, used_at=2.0, kind=CACHE_KIND_RENDER, source_id="source-a"),
        _entry("render-b", size=100, used_at=3.0, kind=CACHE_KIND_RENDER, source_id="source-b"),
    ]

    plan = plan_evictions(entries, cap_bytes=100, live_keys={"source-a"})

    assert [entry.key for entry in plan.evicted] == ["render-b"]


@pytest.mark.unit
def test_everything_left_live_reports_over_cap_instead_of_evicting() -> None:
    entries = [
        _entry("live-a", size=100, used_at=1.0),
        _entry("live-b", size=100, used_at=2.0),
    ]

    plan = plan_evictions(entries, cap_bytes=50, live_keys={"live-a", "live-b"})

    assert plan.evicted == ()
    assert plan.remaining_bytes == 200
    assert plan.over_cap is True


@pytest.mark.unit
def test_one_entry_larger_than_the_cap_is_evicted_alone() -> None:
    entries = [_entry("huge", size=500, used_at=1.0), _entry("small", size=10, used_at=2.0)]

    plan = plan_evictions(entries, cap_bytes=100, live_keys=set())

    assert [entry.key for entry in plan.evicted] == ["huge"]
    assert plan.remaining_bytes == 10
    assert plan.over_cap is False


@pytest.mark.unit
def test_a_zero_cap_evicts_every_non_live_entry() -> None:
    entries = [_entry("a", size=10), _entry("b", size=20)]

    plan = plan_evictions(entries, cap_bytes=0, live_keys=set())

    assert [entry.key for entry in plan.evicted] == ["a", "b"]
    assert plan.remaining_bytes == 0
    assert plan.over_cap is False


@pytest.mark.unit
def test_reserved_room_is_evicted_even_when_usage_is_under_the_cap() -> None:
    entries = [_entry("old", size=60, used_at=1.0), _entry("new", size=40, used_at=2.0)]

    plan = plan_evictions(entries, cap_bytes=100, live_keys=set(), needed_bytes=50)

    assert [entry.key for entry in plan.evicted] == ["old"]
    assert plan.remaining_bytes == 40
    assert plan.over_cap is False


@pytest.mark.unit
def test_reserved_room_with_everything_live_reports_over_cap() -> None:
    entries = [_entry("live", size=100)]

    plan = plan_evictions(entries, cap_bytes=100, live_keys={"live"}, needed_bytes=1)

    assert plan.evicted == ()
    assert plan.remaining_bytes == 100
    assert plan.over_cap is True


@pytest.mark.unit
def test_disk_estimate_math_is_pinned() -> None:
    from math import ceil

    from stemforge.artifacts import (
        MIB,
        estimate_export_bytes,
        estimate_import_bytes,
        estimate_model_install_bytes,
        estimate_render_bytes,
        estimate_separation_bytes,
        free_space_requirement,
    )

    minutes = 10
    pcm = 21_200_000
    preview = 1_440_000

    assert estimate_import_bytes(minutes * 60, native_bytes=10_000_000) == ceil(
        (10_000_000 + (pcm + preview) * minutes) * 1.2
    )
    assert estimate_import_bytes(minutes * 60, youtube=True) == ceil(24_000_000 * minutes * 1.2)
    assert estimate_separation_bytes(minutes * 60) == ceil(5 * (pcm + preview) * minutes * 1.2)
    assert estimate_render_bytes(minutes * 60) == ceil((pcm + preview) * minutes * 1.2)
    assert estimate_render_bytes(minutes * 60, outputs=5) == ceil(
        5 * (pcm + preview) * minutes * 1.2
    )
    assert estimate_export_bytes(minutes * 60) == ceil(15_900_000 * minutes * 1.2 + 64 * MIB)
    assert estimate_model_install_bytes() == 2 * 84_025_440 + 256 * MIB
    assert free_space_requirement(1000) == 1024**3
    assert free_space_requirement(2 * 1024**3) == ceil(2 * 1024**3 * 1.5)
    assert free_space_requirement(1000, floor=0) == 1500


@pytest.mark.unit
def test_estimates_reject_negative_or_missing_inputs() -> None:
    from stemforge.artifacts import estimate_import_bytes, estimate_render_bytes

    with pytest.raises(ValueError, match="duration"):
        estimate_import_bytes(-1)
    with pytest.raises(ValueError, match="native_bytes"):
        estimate_import_bytes(60)
    with pytest.raises(ValueError, match="outputs"):
        estimate_render_bytes(60, outputs=0)


@pytest.mark.unit
def test_require_free_space_uses_the_floor_and_raises_disk_full(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil
    from collections import namedtuple

    from stemforge.artifacts import require_free_space
    from stemforge.errors import StemForgeError

    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda path: Usage(10**12, 0, 500 * 1024**2))

    with pytest.raises(StemForgeError) as caught:
        require_free_space(tmp_path, 1024)

    assert caught.value.code == "storage.disk_full"
    assert caught.value.context["free"] == 500 * 1024**2

    monkeypatch.setattr(shutil, "disk_usage", lambda path: Usage(10**12, 0, 2 * 1024**3))
    require_free_space(tmp_path, 1024)
    require_free_space(tmp_path / "not-yet-created", 1024)


@pytest.mark.unit
def test_export_space_checks_measure_the_volume_and_skip_the_one_gib_floor(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil
    from collections import namedtuple

    from stemforge.artifacts import estimate_export_bytes, require_free_space
    from stemforge.errors import StemForgeError

    Usage = namedtuple("Usage", "total used free")
    free = 100 * 1024**2
    monkeypatch.setattr(shutil, "disk_usage", lambda path: Usage(10**12, 0, free))
    assert estimate_export_bytes(60) < 100 * 1024**2
    estimate = estimate_export_bytes(60)

    require_free_space(tmp_path, estimate, floor=0)

    with pytest.raises(StemForgeError) as caught:
        require_free_space(tmp_path, free + 1, floor=0)

    assert caught.value.code == "storage.disk_full"
    assert caught.value.context["free"] == free
    assert caught.value.context["required"] == free + 1

    # With the default floor the same volume is short by the one GiB reserve.
    with pytest.raises(StemForgeError) as floored:
        require_free_space(tmp_path, estimate)

    assert floored.value.code == "storage.disk_full"
    assert floored.value.context["free"] == free
    assert floored.value.context["required"] == 1024**3
