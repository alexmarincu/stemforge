"""Render manifests as the tab labels, scope, and staleness the UI reads."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from stemforge.artifacts import ArtifactStore, RenderArtifact, SeparationArtifact
from stemforge.domain.methods import method_by_id, method_id_or_default
from stemforge.domain.models import RenderScope
from stemforge.engines.pitch import MAX_SEMITONES, MIN_SEMITONES

# A method label is appended only when two methods would otherwise share a tab.
METHOD_LABEL_SEPARATOR: Final = " · "


@dataclass(frozen=True, slots=True)
class RenderView:
    """One render artifact with the label, scope, and staleness the UI needs.

    A stems view names the Separation method it was made from and the roles it
    carries; an original-scope view has neither a method nor Stem roles.
    """

    artifact: RenderArtifact
    semitones: int
    scope: RenderScope
    label: str
    based_on_earlier_separation: bool
    method: str | None = None
    output_roles: tuple[str, ...] = ("original",)


def render_views(artifacts: ArtifactStore, source_id: str) -> tuple[RenderView, ...]:
    """Resolve render manifests into tab labels, scope, method, and staleness."""
    found = [item for item in artifacts.iter_renders() if item.source_id == source_id]
    parsed: list[tuple[RenderArtifact, RenderScope, int, str | None]] = []
    for artifact in found:
        scope = render_scope(artifact)
        semitones = render_semitones(artifact)
        if scope is None or semitones is None:
            continue
        parsed.append((artifact, scope, semitones, render_method(artifact)))
    parsed.sort(key=lambda item: (item[0].created_at, item[0].render_key))

    scope_collisions = {(semitones, scope) for _, scope, semitones, _ in parsed}
    labelled: list[tuple[RenderArtifact, RenderScope, int, str | None, str]] = []
    for artifact, scope, semitones, method in parsed:
        label = semitone_label(semitones)
        other = RenderScope.STEMS if scope is RenderScope.ORIGINAL else RenderScope.ORIGINAL
        if (semitones, other) in scope_collisions:
            label += " master" if scope is RenderScope.ORIGINAL else " stems"
        labelled.append((artifact, scope, semitones, method, label))

    methods_by_label: dict[str, set[str]] = {}
    for _artifact, scope, _semitones, method, label in labelled:
        if scope is RenderScope.STEMS and method is not None:
            methods_by_label.setdefault(label, set()).add(method)

    separations: dict[str, SeparationArtifact | None] = {}
    views: list[RenderView] = []
    for artifact, scope, semitones, method, label in labelled:
        if len(methods_by_label.get(label, ())) > 1 and method is not None:
            descriptor = method_by_id(method)
            qualifier = descriptor.label if descriptor is not None else method
            label = f"{label}{METHOD_LABEL_SEPARATOR}{qualifier}"
        if scope is RenderScope.STEMS and method is not None:
            if method not in separations:
                separations[method] = artifacts.find_separation(source_id, method=method)
            separation = separations[method]
        else:
            separation = None
        views.append(
            RenderView(
                artifact=artifact,
                semitones=semitones,
                scope=scope,
                label=label,
                based_on_earlier_separation=(
                    scope is RenderScope.STEMS
                    and based_on_earlier_separation(artifact, separation, method)
                ),
                method=method,
                output_roles=render_output_roles(artifact, scope, method),
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


def render_method(artifact: RenderArtifact) -> str | None:
    """The method a stems Render records; absent or unknown means Demucs."""
    if render_scope(artifact) is not RenderScope.STEMS:
        return None
    return method_id_or_default(artifact.manifest.get("separation_method"))


def render_output_roles(
    artifact: RenderArtifact,
    scope: RenderScope,
    method: str | None,
) -> tuple[str, ...]:
    """The roles a committed Render set carries, in presentation order."""
    if scope is RenderScope.ORIGINAL:
        return ("original",)
    raw = artifact.manifest.get("output_roles")
    if isinstance(raw, list):
        recorded = tuple(item for item in raw if isinstance(item, str) and item)
        if recorded and len(recorded) == len(raw):
            return recorded
    descriptor = method_by_id(method) if method is not None else None
    if descriptor is not None:
        return descriptor.output_roles
    roles = tuple(
        entry.role
        for entry in artifact.files
        if entry.role is not None and not entry.role.endswith("_preview")
    )
    return roles or ("original",)


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
    method: str | None = None,
) -> bool:
    """Whether a stems set no longer matches its own method's current set.

    The render manifest is the source of truth. A recorded set key is compared
    directly, then the recorded input hashes are checked against that method's
    roles: the Stems for a summed Instrumental, every stored output for a
    method that shifts its Instrumental directly. Comparison never re-reads the
    audio: a detail refresh must not hash hundreds of megabytes. A set that
    records no inputs cannot be proven stale and stays unlabelled.
    """
    recorded = recorded_input_hashes(artifact.manifest)
    if not recorded:
        return False
    if separation is None:
        return True
    recorded_set_key = artifact.manifest.get("separation_set_key")
    if isinstance(recorded_set_key, str) and recorded_set_key != separation.set_key:
        return True
    recorded_version = artifact.manifest.get("separation_method_version")
    if (
        isinstance(recorded_version, int)
        and not isinstance(recorded_version, bool)
        and recorded_version != separation.method_version
    ):
        return True
    descriptor = method_by_id(method) if method is not None else None
    roles = descriptor.render_input_roles if descriptor is not None else tuple(recorded)
    current = {
        entry.role: entry.sha256
        for entry in separation.files
        if entry.role in roles and entry.sha256 is not None
    }
    if not current:
        return False
    comparison = {role for role in roles if role in recorded or role in current}
    return any(recorded.get(role) != current.get(role) for role in comparison)
