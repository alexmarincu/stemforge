"""The pure domain: immutable models and Workspace invariants (ticket 06 §5)."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from stemforge.domain.models import (
    ArtifactKind,
    ArtifactRef,
    ArtifactSet,
    JobKind,
    JobState,
    OriginKind,
    RenderRequest,
    RenderScope,
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
def test_artifact_models_are_immutable_and_grouped_by_a_set() -> None:
    artifact = ArtifactRef(
        artifact_id="a" * 64,
        source_id="src-1",
        kind=ArtifactKind.STEM,
        name="vocals",
        master_path=Path("/cache/stems/vocals.wav"),
        preview_path=Path("/cache/stems/vocals.mp3"),
        gain_db=-1.5,
        manifest_path=Path("/cache/stems/manifest.json"),
    )
    artifact_set = ArtifactSet(
        source_id="src-1",
        render_key=None,
        artifacts=(artifact,),
        gain_db=-1.5,
        peak_dbfs=-1.5,
    )

    assert artifact_set.artifacts == (artifact,)
    assert artifact_set.gain_db == -1.5
    with pytest.raises(dataclasses.FrozenInstanceError):
        artifact.name = "drums"  # type: ignore[misc]


@pytest.mark.unit
def test_job_kinds_and_states_match_the_decision_record() -> None:
    assert {kind.value for kind in JobKind} == {
        "import_local",
        "import_youtube",
        "separate",
        "render_pitch",
        "export",
        "model_install",
    }
    assert {state.value for state in JobState} == {
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
    }


@pytest.mark.unit
def test_render_request_carries_scope_and_semitones() -> None:
    request = RenderRequest(track_id="trk-1", semitones=3)

    assert request.scope is RenderScope.ORIGINAL
    assert request.semitones == 3
    assert RenderScope.STEMS.value == "stems"
