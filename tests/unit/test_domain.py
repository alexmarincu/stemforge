"""The pure domain: immutable models and Workspace invariants."""

from __future__ import annotations

import dataclasses

import pytest

from stemforge.domain.models import (
    JobKind,
    JobState,
    OriginKind,
    Source,
    SourceMetadata,
    Track,
)
from stemforge.domain.workspace import Workspace, WorkspaceSnapshot


def _source(source_id: str = "src-1", title: str = "Song") -> Source:
    return Source(
        source_id=source_id,
        origin=f"/music/{source_id}.wav",
        origin_kind=OriginKind.FILE,
        metadata=SourceMetadata(title=title),
    )


def _track(track_id: str = "trk-1", source: Source | None = None) -> Track:
    track_source = source or _source()
    return Track(track_id=track_id, source=track_source, title=track_source.metadata.title)


@pytest.mark.unit
def test_workspace_is_empty_before_any_track_is_added() -> None:
    workspace = Workspace()

    assert len(workspace) == 0
    assert workspace.tracks == ()
    assert workspace.snapshot().tracks == ()


@pytest.mark.unit
def test_workspace_keeps_tracks_in_insertion_order() -> None:
    first = _track("trk-1", _source("src-1"))
    second = _track("trk-2", _source("src-2"))

    workspace = Workspace()
    workspace.add_track(first)
    workspace.add_track(second)

    assert workspace.tracks == (first, second)
    assert list(workspace) == [first, second]


@pytest.mark.unit
def test_workspace_allows_only_one_track_per_source() -> None:
    workspace = Workspace()
    workspace.add_track(_track("trk-1", _source("src-1")))

    with pytest.raises(ValueError, match="src-1"):
        workspace.add_track(_track("trk-2", _source("src-1")))

    assert len(workspace) == 1


@pytest.mark.unit
def test_workspace_rejects_a_duplicate_track_id() -> None:
    workspace = Workspace()
    workspace.add_track(_track("trk-1", _source("src-1")))

    with pytest.raises(ValueError, match="trk-1"):
        workspace.add_track(_track("trk-1", _source("src-2")))


@pytest.mark.unit
def test_workspace_remove_returns_the_track_and_keeps_the_rest() -> None:
    first = _track("trk-1", _source("src-1"))
    second = _track("trk-2", _source("src-2"))
    workspace = Workspace()
    workspace.add_track(first)
    workspace.add_track(second)

    removed = workspace.remove_track("trk-1")

    assert removed is first
    assert workspace.tracks == (second,)
    assert workspace.find("trk-1") is None


@pytest.mark.unit
def test_workspace_remove_unknown_track_raises() -> None:
    with pytest.raises(KeyError):
        Workspace().remove_track("missing")


@pytest.mark.unit
def test_workspace_finds_a_track_by_source() -> None:
    track = _track("trk-1", _source("src-1"))
    workspace = Workspace()
    workspace.add_track(track)

    assert workspace.find_by_source("src-1") is track
    assert workspace.find_by_source("src-2") is None


@pytest.mark.unit
def test_workspace_snapshot_does_not_change_when_the_workspace_does() -> None:
    first = _track("trk-1", _source("src-1"))
    workspace = Workspace()
    workspace.add_track(first)

    snapshot = workspace.snapshot()
    workspace.add_track(_track("trk-2", _source("src-2")))
    workspace.remove_track("trk-1")

    assert snapshot.tracks == (first,)
    assert len(snapshot) == 1
    assert list(snapshot) == [first]


@pytest.mark.unit
def test_workspace_snapshot_is_frozen() -> None:
    snapshot = Workspace().snapshot()

    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.tracks = ()  # type: ignore[misc]


@pytest.mark.unit
def test_workspace_snapshot_holds_an_immutable_tuple() -> None:
    snapshot = WorkspaceSnapshot(tracks=())

    assert isinstance(snapshot.tracks, tuple)


@pytest.mark.unit
def test_workspace_can_be_built_from_an_existing_track_list() -> None:
    first = _track("trk-1", _source("src-1"))
    second = _track("trk-2", _source("src-2"))

    workspace = Workspace([first, second])

    assert workspace.tracks == (first, second)


@pytest.mark.unit
def test_source_models_are_immutable_values() -> None:
    source = _source()
    track = _track(source=source)

    with pytest.raises(dataclasses.FrozenInstanceError):
        source.origin = "/elsewhere"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        track.semitones = 3  # type: ignore[misc]


@pytest.mark.unit
def test_track_defaults_to_no_pitch_shift_and_exposes_its_source_id() -> None:
    track = _track()

    assert track.semitones == 0
    assert track.source_id == "src-1"
    assert track.title == "Song"


@pytest.mark.unit
def test_track_exposes_duration_and_gain_without_reaching_into_source() -> None:
    source = _source()
    source = dataclasses.replace(
        source,
        metadata=SourceMetadata(title="Song", duration_s=187.0),
        gain_db=-1.5,
    )
    track = _track(source=source)

    assert track.duration_s == 187.0
    assert track.gain_db == -1.5


@pytest.mark.unit
def test_dead_domain_types_are_not_kept_alive() -> None:
    import stemforge.domain.models as models

    for name in ("ArtifactRef", "ArtifactSet", "ArtifactKind", "RenderRequest"):
        assert not hasattr(models, name), f"{name} has no production user"


@pytest.mark.unit
def test_roles_have_one_canonical_definition() -> None:
    from stemforge.domain.roles import (
        INSTRUMENTAL_ROLE,
        NON_VOCAL_ROLES,
        OUTPUT_ROLES,
        STEM_ROLES,
    )
    from stemforge.worker_protocol import STEM_NAMES

    assert STEM_ROLES == STEM_NAMES == ("vocals", "drums", "bass", "other")
    assert INSTRUMENTAL_ROLE == "instrumental"
    assert NON_VOCAL_ROLES == ("drums", "bass", "other")
    assert OUTPUT_ROLES == ("vocals", "drums", "bass", "other", "instrumental")


@pytest.mark.unit
def test_job_kinds_and_states_match_the_decision_record() -> None:
    assert {kind.value for kind in JobKind} == {
        "import_local",
        "import_youtube",
        "separate",
        "render_pitch",
        "export",
        "export_batch",
        "save_original",
        "model_install",
    }
    assert {state.value for state in JobState} == {
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
    }
