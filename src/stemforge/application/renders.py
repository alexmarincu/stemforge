"""Render manifests as the tab labels, scope, and staleness the UI reads."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from stemforge.artifacts import ArtifactStore, RenderArtifact, SeparationArtifact
from stemforge.domain.models import RenderScope
from stemforge.domain.roles import STEM_ROLES
from stemforge.engines.pitch import MAX_SEMITONES, MIN_SEMITONES


@dataclass(frozen=True, slots=True)
class RenderView:
    """One render artifact with the label, scope, and staleness the UI needs."""

    artifact: RenderArtifact
    semitones: int
    scope: RenderScope
    label: str
    based_on_earlier_separation: bool


def render_views(artifacts: ArtifactStore, source_id: str) -> tuple[RenderView, ...]:
    """Resolve render manifests into tab labels, scope, and staleness."""
    found = [item for item in artifacts.iter_renders() if item.source_id == source_id]
    parsed: list[tuple[RenderArtifact, RenderScope, int]] = []
    for artifact in found:
        scope = render_scope(artifact)
        semitones = render_semitones(artifact)
        if scope is None or semitones is None:
            continue
        parsed.append((artifact, scope, semitones))
    parsed.sort(key=lambda item: (item[0].created_at, item[0].render_key))
    collisions = {(semitones, scope) for _, scope, semitones in parsed}
    separation = artifacts.find_separation(source_id) if parsed else None
    views: list[RenderView] = []
    for artifact, scope, semitones in parsed:
        label = semitone_label(semitones)
        other = RenderScope.STEMS if scope is RenderScope.ORIGINAL else RenderScope.ORIGINAL
        if (semitones, other) in collisions:
            label += " master" if scope is RenderScope.ORIGINAL else " stems"
        based_on_earlier = scope is RenderScope.STEMS and based_on_earlier_separation(
            artifact, separation
        )
        views.append(
            RenderView(
                artifact=artifact,
                semitones=semitones,
                scope=scope,
                label=label,
                based_on_earlier_separation=based_on_earlier,
            )
        )
    return tuple(views)


def semitone_label(semitones: int) -> str:
    """The tab text for one shift: ``+3``, or a minus-sign prefix when negative."""
    return f"+{semitones}" if semitones > 0 else f"\u2212{abs(semitones)}"


def render_scope(artifact: RenderArtifact) -> RenderScope | None:
    value = artifact.manifest.get("scope")
    if not isinstance(value, str):
        return None
    try:
        return RenderScope(value)
    except ValueError:
        return None


def render_semitones(artifact: RenderArtifact) -> int | None:
    value = artifact.manifest.get("semitones")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if not MIN_SEMITONES <= value <= MAX_SEMITONES or value == 0:
        return None
    return value


def recorded_input_hashes(manifest: Mapping[str, object]) -> dict[str, str]:
    raw = manifest.get("inputs")
    if not isinstance(raw, list):
        return {}
    hashes: dict[str, str] = {}
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        role = entry.get("role")
        digest = entry.get("sha256")
        if isinstance(role, str) and isinstance(digest, str) and digest:
            hashes[role] = digest
    return hashes


def based_on_earlier_separation(
    artifact: RenderArtifact,
    separation: SeparationArtifact | None,
) -> bool:
    """Whether a stems set's recorded inputs no longer match the current Stems.

    The manifest is the source of truth. Comparison uses the
    separation manifest's recorded stem hashes, never a fresh read of the audio:
    a detail refresh must not hash hundreds of megabytes. If the separation
    records no hashes, the set cannot be proven stale and stays unlabelled.
    """
    recorded = recorded_input_hashes(artifact.manifest)
    if not recorded:
        return False
    if separation is None:
        return True
    current = {
        entry.role: entry.sha256
        for entry in separation.files
        if entry.role in STEM_ROLES and entry.sha256 is not None
    }
    if not current:
        return False
    roles = {role for role in STEM_ROLES if role in recorded or role in current}
    return any(recorded.get(role) != current.get(role) for role in roles)
