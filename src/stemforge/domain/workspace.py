"""The ``Workspace`` aggregate and its invariants.

One Track per Source, tracks in insertion order, and snapshots that cannot change
under a reader. The Workspace is pure: the service owns locking and the filesystem
(ticket 06 §5).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from .models import Track


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    """An immutable view of the Workspace taken under the service lock."""

    tracks: tuple[Track, ...] = ()

    def __len__(self) -> int:
        return len(self.tracks)

    def __iter__(self) -> Iterator[Track]:
        return iter(self.tracks)


class Workspace:
    """The ordered set of Tracks visible in the UI."""

    def __init__(self, tracks: Iterable[Track] = ()) -> None:
        self._tracks: tuple[Track, ...] = ()
        for track in tracks:
            self.add_track(track)

    @property
    def tracks(self) -> tuple[Track, ...]:
        return self._tracks

    def __len__(self) -> int:
        return len(self._tracks)

    def __iter__(self) -> Iterator[Track]:
        return iter(self._tracks)

    def find(self, track_id: str) -> Track | None:
        for track in self._tracks:
            if track.track_id == track_id:
                return track
        return None

    def find_by_source(self, source_id: str) -> Track | None:
        for track in self._tracks:
            if track.source_id == source_id:
                return track
        return None

    def add_track(self, track: Track) -> Track:
        if self.find(track.track_id) is not None:
            raise ValueError(f"duplicate track id: {track.track_id}")
        if self.find_by_source(track.source_id) is not None:
            raise ValueError(f"source already loaded: {track.source_id}")
        self._tracks = (*self._tracks, track)
        return track

    def remove_track(self, track_id: str) -> Track:
        track = self.find(track_id)
        if track is None:
            raise KeyError(track_id)
        self._tracks = tuple(item for item in self._tracks if item.track_id != track_id)
        return track

    def snapshot(self) -> WorkspaceSnapshot:
        return WorkspaceSnapshot(tracks=self._tracks)
