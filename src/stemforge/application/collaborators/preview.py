"""The playback-preview feature the UI calls through ``WorkspaceService``."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from stemforge.application.identity import separation_key
from stemforge.application.renders import RenderView, render_views
from stemforge.application.state import WorkspaceState
from stemforge.application.views import SEPARATION_ROLE_LABELS, PreviewTarget
from stemforge.artifacts import ArtifactStore
from stemforge.domain.methods import method_by_id
from stemforge.domain.models import RenderScope, Track
from stemforge.domain.roles import INSTRUMENTAL_ROLE, STEM_ROLES


class PreviewService:
    """Owns cache-root URL confinement and every playable preview target."""

    def __init__(self, artifacts: ArtifactStore, workspace: WorkspaceState) -> None:
        self._artifacts = artifacts
        self._workspace = workspace

    def has_preview(self, track_id: str) -> bool:
        track = self._workspace.find(track_id)
        return track is not None and self._preview_url(track.source.preview_path) is not None

    def preview_targets(self, track_id: str) -> tuple[PreviewTarget, ...]:
        """The playable previews for one Track, original first.

        Only files inside the cache root are exposed. A missing preview is simply
        absent, which lets the UI mark its row unavailable instead of requesting a
        URL that can only 404. Render sets follow in creation order, newest last;
        a stems set previews its shifted Instrumental because that is the one
        primary output the set always carries.
        """
        track = self._workspace.find(track_id)
        if track is None:
            return ()
        return self._preview_targets(track)

    def pitch_render_preview_target(
        self,
        track: Track,
        render_key: str,
        role: str,
    ) -> PreviewTarget | None:
        """The preview of one member of a Render set.

        A master set carries only its shifted master; a stems set carries Vocals,
        Instrumental, Drums, Bass, and Other. The key is stable and unique across
        versions so the single player can tell the rows apart.
        """
        view = next(
            (
                item
                for item in render_views(self._artifacts, track.source_id)
                if item.artifact.render_key == render_key
            ),
            None,
        )
        if view is None:
            return None
        if view.scope is RenderScope.ORIGINAL:
            if role != "original":
                return None
            file_role = "original_preview"
        else:
            if role not in (*STEM_ROLES, INSTRUMENTAL_ROLE):
                return None
            file_role = f"{role}_preview"
        url = self._preview_url(view.artifact.path_for(file_role))
        if url is None:
            return None
        return PreviewTarget(
            key=f"render:{view.artifact.render_key}:{role}",
            label=SEPARATION_ROLE_LABELS.get(role, role.title()),
            url=url,
            gain_db=view.artifact.gain_db,
        )

    def pitch_preview_target(self, track: Track, render_key: str) -> PreviewTarget | None:
        return next(
            (target for target in self._preview_targets(track) if target.key == render_key),
            None,
        )

    def separation_preview_target(
        self,
        track: Track,
        set_key: str,
        stem: str,
    ) -> PreviewTarget | None:
        """The playable preview of one role in one Separation set.

        The set key is looked up across every method, and the role must belong to
        that method's descriptor. A missing set, an unknown role, or a cleared
        preview returns ``None`` so the row can be marked unavailable instead of
        pointing at a URL that can only 404.
        """
        artifact = next(
            (
                item
                for item in self._artifacts.separation_sets(track.source_id)
                if separation_key(item) == set_key
            ),
            None,
        )
        if artifact is None:
            return None
        descriptor = method_by_id(artifact.method)
        if descriptor is None or stem not in descriptor.output_roles:
            return None
        path = artifact.path_for(f"{stem}_preview")
        url = self._preview_url(path)
        if url is None:
            return None
        return PreviewTarget(
            key=f"{set_key}:{stem}",
            label=SEPARATION_ROLE_LABELS[stem],
            url=url,
            gain_db=artifact.gain_db,
        )

    def _preview_targets(self, track: Track) -> tuple[PreviewTarget, ...]:
        targets: list[PreviewTarget] = []
        source_url = self._preview_url(track.source.preview_path)
        if source_url is not None:
            targets.append(
                PreviewTarget(
                    key="original",
                    label="Original",
                    url=source_url,
                    gain_db=track.gain_db,
                )
            )
        for view in render_views(self._artifacts, track.source_id):
            url = self._render_preview_url(view)
            if url is None:
                continue
            targets.append(
                PreviewTarget(
                    key=view.artifact.render_key,
                    label=view.label,
                    url=url,
                    gain_db=view.artifact.gain_db,
                )
            )
        return tuple(targets)

    def _render_preview_url(self, view: RenderView) -> str | None:
        role = "original_preview" if view.scope is RenderScope.ORIGINAL else "instrumental_preview"
        return self._preview_url(view.artifact.path_for(role))

    def _preview_url(self, path: Path | None) -> str | None:
        if path is None:
            return None
        try:
            root = self._artifacts.root.resolve(strict=False)
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(root) or not resolved.is_file():
                return None
            relative = resolved.relative_to(root).as_posix()
        except (OSError, ValueError):
            return None
        return "/media/" + quote(relative, safe="/")
