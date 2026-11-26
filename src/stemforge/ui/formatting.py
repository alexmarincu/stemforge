"""Formatting helpers and the ``(code, reason)`` error copy contract.

Coded error copy resolves through the locale catalog: this module maps a stable
code and reason to its catalog message IDs and composes the dynamic sentences.
``errors.py`` stays copy-free. Every code resolves, so a newer worker's unknown
code falls back to ``internal.unexpected``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final

from stemforge import errors, i18n
from stemforge.domain.display import (
    EXPORT_SHIFTED_LABEL_KEY,
    RENDER_LABEL_KEY,
    RENDER_MASTER_LABEL_KEY,
    RENDER_STEMS_LABEL_KEY,
    role_label_key,
)
from stemforge.domain.models import OriginKind

if TYPE_CHECKING:
    from stemforge.application import JobView
    from stemforge.domain.models import Track
    from stemforge.models.spec import ModelSpec

MAX_HEADLINE_LENGTH: Final = 90
_MIB: Final = 1024 * 1024
_GIB: Final = 1024 * _MIB


@dataclass(frozen=True, slots=True)
class ErrorCopy:
    """One rendered headline and exactly one recovery action."""

    headline: str
    action: str


@dataclass(frozen=True, slots=True)
class ModelLicenceCopy:
    """The model gate's user-facing prose."""

    title: str
    summary: str
    identity: str
    notice: str
    accept_label: str
    details_label: str
    decline_label: str


# The reason variants the catalog must carry, independent of any rendered copy.
# A reason only gets its own row when the recovery action genuinely differs;
# membership is explicit so an unknown reason from a newer worker falls back to
# the code's base row.
REASON_VARIANTS: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        (errors.IMPORT_UNSUPPORTED_SOURCE, "extension"),
        (errors.SEPARATION_MODEL_MISSING, "absent"),
        (errors.SEPARATION_MODEL_MISSING, "checksum"),
        (errors.SEPARATION_MODEL_DOWNLOAD_FAILED, "offline"),
        (errors.SEPARATION_MODEL_DOWNLOAD_FAILED, "http"),
        (errors.EXPORT_WRITE_FAILED, "permission"),
        (errors.EXPORT_WRITE_FAILED, "in_use"),
        (errors.EXPORT_WRITE_FAILED, "name_too_long"),
        (errors.EXPORT_WRITE_FAILED, "device_gone"),
        (errors.EXPORT_WRITE_FAILED, "other"),
        (errors.EXPORT_ORIGINAL_UNAVAILABLE, "missing"),
        (errors.EXPORT_ORIGINAL_UNAVAILABLE, "changed"),
        (errors.EXPORT_ORIGINAL_UNAVAILABLE, "not_local"),
        (errors.LICENSING_OPEN_FAILED, "log"),
        # Export owns this reason so its disk-full action is "choose another
        # location" rather than the cache's "clear cached audio".
        (errors.STORAGE_DISK_FULL, "export"),
    }
)


def model_licence_copy(spec: ModelSpec) -> ModelLicenceCopy:
    """Return the model-gate copy for one pinned model record."""
    megabytes = f"{spec.size_bytes / 1_000_000:.0f} MB"
    return ModelLicenceCopy(
        title=i18n.t("separation.model.title"),
        summary=i18n.t("separation.model.summary", size=megabytes),
        identity=i18n.t(
            "separation.model.identity",
            repo=spec.repo_id,
            revision=spec.revision[:8],
            size=f"{spec.size_bytes:,}",
        ),
        notice=i18n.t("separation.model.notice"),
        accept_label=i18n.t("separation.model.accept"),
        details_label=i18n.t("separation.model.details"),
        decline_label=i18n.t("common.cancel"),
    )


def copy_for(
    code: str,
    reason: str | None = None,
    *,
    context: Mapping[str, object] | None = None,
    origin: OriginKind | None = None,
) -> ErrorCopy:
    """Return the copy for ``(code, reason)``, falling back to ``internal.unexpected``.

    ``context`` carries the same machine values the raising code logged — a
    duration, a destination folder, or needed/free bytes — so the sentence can
    name them without parsing log text. ``origin`` selects the YouTube wording
    for a shared import code. Any interpolated headline that would exceed the
    details limit falls back to the static sentence rather than breaking the
    cap.
    """
    base = _base_copy(code, reason)
    result = base
    if code == errors.IMPORT_NO_AUDIO_STREAM and origin is OriginKind.YOUTUBE:
        result = ErrorCopy(
            i18n.t("error.import.no_audio_stream.video.headline"),
            i18n.t("error.import.no_audio_stream.video.action"),
        )
    elif code in {errors.IMPORT_TOO_LONG, errors.IMPORT_YOUTUBE_TOO_LONG}:
        seconds = _number(context, "duration_s")
        if seconds is not None:
            duration = format_duration(seconds)
            key = (
                "error.import.too_long.duration.headline"
                if code == errors.IMPORT_TOO_LONG
                else "error.import.youtube_too_long.duration.headline"
            )
            result = ErrorCopy(i18n.t(key, duration=duration), base.action)
    elif code == errors.EXPORT_WRITE_FAILED:
        folder = _folder_name(context)
        if folder is not None:
            result = ErrorCopy(
                i18n.t("error.export.write_failed.folder.headline", folder=folder),
                base.action,
            )
    elif code == errors.STORAGE_DISK_FULL:
        folder = _folder_name(context)
        required = _number(context, "required")
        free = _number(context, "free")
        if reason == "export" and folder is not None and required is not None:
            result = ErrorCopy(
                i18n.t(
                    "error.storage.disk_full.export.headline",
                    folder=folder,
                    required=format_bytes(round(required)),
                ),
                base.action,
            )
        elif required is not None and free is not None:
            result = ErrorCopy(
                i18n.t(
                    "error.storage.disk_full.import.headline",
                    required=format_bytes(round(required)),
                    free=format_bytes(round(free)),
                ),
                base.action,
            )
    elif code == errors.STORAGE_PERMISSION_DENIED:
        path = _path_label(context)
        if path is not None:
            result = ErrorCopy(
                i18n.t("error.storage.permission_denied.path.headline", path=path),
                base.action,
            )
    if len(result.headline) > MAX_HEADLINE_LENGTH:
        return base
    return result


def _base_copy(code: str, reason: str | None) -> ErrorCopy:
    """Render the code's base row, or its reason-specific row when one exists."""
    if reason is not None and (code, reason) in REASON_VARIANTS:
        return _catalog_copy(f"error.{code}.reason.{reason}")
    known_code = code if code in errors.ALL_CODES else errors.INTERNAL_UNEXPECTED
    return _catalog_copy(f"error.{known_code}")


def _catalog_copy(key: str) -> ErrorCopy:
    """Render one error headline and action from the catalog."""
    return ErrorCopy(i18n.t(f"{key}.headline"), i18n.t(f"{key}.action"))


def _number(context: Mapping[str, object] | None, key: str) -> float | None:
    value = (context or {}).get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _folder_name(context: Mapping[str, object] | None) -> str | None:
    """The last path component for a sentence; never the full path."""
    raw = (context or {}).get("path")
    if not isinstance(raw, (str, Path)):
        return None
    text = str(raw)
    if not text:
        return None
    name = Path(text).name or text
    if len(name) > 28:
        name = f"{name[:27]}…"
    return name


def _path_label(
    context: Mapping[str, object] | None,
    *,
    length: int = 60,
) -> str | None:
    raw = (context or {}).get("path")
    if not isinstance(raw, (str, Path)):
        return None
    text = str(raw)
    if not text:
        return None
    if len(text) <= length:
        return text
    return f"…{text[-(length - 1) :]}"


def reason_from_detail(detail: str | None) -> str | None:
    """Pick the stable copy reason out of a log-only detail string.

    Details are log text, not copy: prefer the explicit ``reason=`` token. A
    single-token detail is already a stable classifier and is used as-is; a
    sentence is not a reason.
    """
    text = (detail or "").strip()
    if not text:
        return None
    for part in text.replace("\n", " ").split():
        if part.startswith("reason="):
            return part.removeprefix("reason=")
    return text if " " not in text else None


def fatal_copy(code: str, *, path: str | None = None) -> ErrorCopy:
    """Return the exact preflight fatal sentence and fix line.

    The storage sentence names the unwritable path; every other fatal code uses
    its static sentence.
    """
    copy = copy_for(code)
    if path is not None and code in {
        errors.PREFLIGHT_STORAGE_UNWRITABLE,
        errors.STORAGE_PERMISSION_DENIED,
    }:
        return ErrorCopy(
            i18n.t(f"error.{code}.path.headline", path=path),
            copy.action,
        )
    return copy


def format_duration(seconds: float | None) -> str:
    """Format a duration as ``m:ss`` (or ``h:mm:ss``); an unknown value is an em dash."""
    if seconds is None or not math.isfinite(seconds) or seconds < 0:
        return "—"
    total = round(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_semitones(semitones: int) -> str:
    """Format a signed shift the way version tabs and the stepper do."""
    if semitones == 0:
        return "0"
    return f"+{semitones}" if semitones > 0 else f"\u2212{abs(semitones)}"


def role_label(role: str) -> str:
    """The display name for one role, resolved from its stable role key."""
    key = role_label_key(role)
    if i18n.has_message(key):
        return i18n.t(key)
    return role.replace("_", " ").title()


def display_label(
    key: str | None,
    context: Mapping[str, object] | None = None,
    *,
    fallback: str = "",
) -> str:
    """Resolve a stable display code, falling back to the stored English label.

    Role codes render their catalog name; render and shifted-export codes format
    the semitone count at render time. Any other code is a catalog template with
    its context passed through as named placeholders.
    """
    if not key or not i18n.has_message(key):
        return fallback
    values = dict(context or {})
    if key == EXPORT_SHIFTED_LABEL_KEY:
        role = values.get("role")
        semitones = _int_value(values.get("semitones"), 0)
        return i18n.t(
            key,
            role=role_label(role) if isinstance(role, str) else "",
            semitones=format_semitones(semitones),
        )
    if key in {RENDER_LABEL_KEY, RENDER_MASTER_LABEL_KEY, RENDER_STEMS_LABEL_KEY}:
        return i18n.t(
            key,
            semitones=format_semitones(_int_value(values.get("semitones"), 0)),
        )
    return i18n.t(key, **values)


def job_stage(job: JobView) -> str:
    """A Job's stage from its code, or the English string for an unknown code."""
    code = job.stage_code
    if code and i18n.has_message(code):
        return i18n.t(code, **dict(job.stage_context))
    return job.stage


def job_detail(job: JobView) -> str | None:
    """A Job's detail from its code, or the English string for an unknown code."""
    code = job.detail_code
    if code and i18n.has_message(code):
        return display_label(
            code,
            job.detail_context,
            fallback=job.detail or "",
        )
    return job.detail


def _int_value(value: object, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value


def level_adjusted_label(gain_db: float) -> str | None:
    """The quiet level-adjusted badge, or ``None`` when no attenuation was applied."""
    if gain_db == 0:
        return None
    return i18n.t("page.level_adjusted", gain=abs(gain_db))


def format_sample_rate(rate: int | None) -> str | None:
    """Format a sample rate (44.1 kHz, 48 kHz)."""
    if rate is None or rate <= 0:
        return None
    value = rate / 1000
    if value.is_integer():
        return f"{int(value)} kHz"
    return f"{value:.1f} kHz"


def format_channels(channels: int | None) -> str | None:
    """Name the channel layout when it is conventional, otherwise count it."""
    if channels is None or channels <= 0:
        return None
    if channels == 1:
        return i18n.t("page.channels_mono")
    if channels == 2:
        return i18n.t("page.channels_stereo")
    return i18n.t("page.channels_count", count=channels)


def origin_label(kind: OriginKind) -> str:
    """Name a Source origin the way the rail and detail header do."""
    if kind is OriginKind.YOUTUBE:
        return i18n.t("page.origin_youtube")
    return i18n.t("page.origin_local")


def track_icon(kind: OriginKind) -> str:
    """The rail's music/video type icon for a Source origin."""
    return "movie" if kind is OriginKind.YOUTUBE else "music_note"


def source_reference(origin: str, kind: OriginKind) -> str:
    """The local filename or the YouTube URL shown in the detail header."""
    return origin if kind is OriginKind.YOUTUBE else Path(origin).name


def detail_meta(track: Track) -> list[str]:
    """Origin · filename or URL · duration · 44.1 kHz stereo."""
    source = track.source
    meta = [origin_label(source.origin_kind)]
    reference = source_reference(source.origin, source.origin_kind)
    if reference:
        meta.append(reference)
    meta.append(format_duration(track.duration_s))
    sample_rate = format_sample_rate(source.metadata.sample_rate)
    if sample_rate:
        meta.append(sample_rate)
    channels = format_channels(source.metadata.channels)
    if channels:
        meta.append(channels)
    return meta


def format_bytes(size: int) -> str:
    """Format a cache size with one decimal, MB below 1 GiB and GB above."""
    if size <= 0:
        return "0.0 MB"
    if size < _GIB:
        return f"{size / _MIB:.1f} MB"
    return f"{size / _GIB:.1f} GB"


def cache_cleared_message(freed_bytes: int) -> str:
    """The one sentence both Clear cached audio surfaces show."""
    return i18n.t("page.cache_cleared", size=format_bytes(freed_bytes))


def imported_file_meta(suffix: str, size_bytes: int) -> str:
    """The Imported file block's container and size, e.g. ``FLAC · 12.3 MB``."""
    return i18n.t(
        "export.imported_file_meta",
        format=suffix.lstrip(".").upper(),
        size=format_bytes(size_bytes),
    )


def file_count_label(count: int) -> str:
    """Name a count of recent files, singular only at one."""
    return i18n.t("settings.file_count", count=count)


def export_batch_confirm(count: int) -> str:
    """The batch sheet's confirm label, singular only at one."""
    return i18n.t("export.batch.confirm", count=count)


def export_batch_remaining(count: int) -> str:
    """The results bar's recovery action for the items that were not written."""
    return i18n.t("export.batch.remaining", count=count)


def export_batch_headline(
    *,
    written: int,
    failed: int,
    not_exported: int,
    cancelled: bool,
    destination: Path | None = None,
) -> str:
    """The one results-bar sentence for a batch's terminal outcome.

    The counts come from the per-item record, never from the Job's state: a
    failed batch may have written files, and a cancelled one usually has.
    """
    total = written + failed + not_exported
    if cancelled:
        return i18n.t("page.export_batch.cancelled", written=written, total=total)
    if total and written == total and destination is not None:
        return i18n.t("page.export_batch.success", count=written, path=destination)
    if total == 0:
        return i18n.t("page.export_batch.none")
    if not_exported:
        return i18n.t(
            "page.export_batch.partial_stopped",
            written=written,
            total=total,
            count=failed + not_exported,
        )
    return i18n.t(
        "page.export_batch.partial_failed",
        count=failed,
        written=written,
        total=total,
    )


def cache_percent(used: int, cap: int) -> int:
    """The footer meter's width: 0 to 100, never above a full bar."""
    if cap <= 0 or used <= 0:
        return 0
    return min(100, max(0, round(used * 100 / cap)))


def format_relative_time(when: datetime, *, now: datetime | None = None) -> str:
    """Short, local, human recency labels for the recent-files list."""
    current = now if now is not None else datetime.now(UTC)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    seconds = max(0, int((current - when).total_seconds()))
    if seconds < 60:
        return i18n.t("page.recent_just_now")
    minutes = seconds // 60
    if minutes < 60:
        return i18n.t("page.recent_minutes_ago", count=minutes)
    hours = minutes // 60
    if hours < 24:
        return i18n.t("page.recent_hours_ago", count=hours)
    return i18n.t("page.recent_days_ago", count=hours // 24)
