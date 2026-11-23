"""``SettingsStore``: recent sources, export directory, and first-run flags.

A single JSON document under ``<config>/settings.json`` with ``os.replace``
atomic writes and one process lock. Never browser or NiceGUI storage.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, cast

from stemforge.domain.methods import method_by_id
from stemforge.domain.models import ModelConsent, OriginKind
from stemforge.platform.paths import AppPaths
from stemforge.platform.storage import DEFAULT_CACHE_CAP_BYTES

SCHEMA_VERSION: Final = 1
MAX_RECENT_FILES: Final = 10
SETTINGS_FILENAME: Final = "settings.json"
# The shipped YouTube consent wording has one version.
YOUTUBE_NOTICE_VERSION: Final = 1

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RecentFile:
    """One entry in the recent-files list."""

    source_id: str
    title: str
    origin: str
    origin_kind: OriginKind
    duration_s: float | None
    last_opened: datetime


@dataclass(frozen=True, slots=True)
class Settings:
    """The complete persisted settings document."""

    schema_version: int = SCHEMA_VERSION
    recent: tuple[RecentFile, ...] = ()
    export_dir: Path | None = None
    first_run: bool = True
    # The audio cache cap, counted over sources/ and renders/ only.
    cache_cap_bytes: int = DEFAULT_CACHE_CAP_BYTES
    # Explicit consent for the htdemucs download.
    model_consent: ModelConsent | None = None
    # The YouTube row toggle and the one-time ToS acknowledgment.
    youtube_enabled: bool = True
    youtube_notice_acknowledged: bool = False
    youtube_notice_version: int = 0
    youtube_notice_accepted_at: datetime | None = None
    # The user's Separation method preference; unset means auto.
    separation_method: str | None = None


class SettingsStore:
    """Reads and writes the single settings JSON document atomically."""

    def __init__(self, paths: AppPaths) -> None:
        self._paths = paths
        self._path = paths.config / SETTINGS_FILENAME
        self._lock = threading.Lock()
        self._settings = self._load()

    @property
    def paths(self) -> AppPaths:
        return self._paths

    @property
    def path(self) -> Path:
        return self._path

    @property
    def settings(self) -> Settings:
        with self._lock:
            return self._settings

    @property
    def first_run(self) -> bool:
        with self._lock:
            return self._settings.first_run

    @property
    def preferred_export_dir(self) -> Path | None:
        with self._lock:
            return self._settings.export_dir

    def model_consent(self) -> ModelConsent | None:
        """The persisted model-weights consent, if any."""
        with self._lock:
            return self._settings.model_consent

    def accept_model_licence(
        self, *, version: int, accepted_at: datetime | None = None
    ) -> ModelConsent:
        """Record an explicit, acknowledged download of the model weights."""
        if version < 1:
            raise ValueError("consent version must be positive")
        consent = ModelConsent(
            version=version,
            acknowledged=True,
            accepted_at=accepted_at or datetime.now(UTC),
        )
        with self._lock:
            self._write_locked(replace(self._settings, model_consent=consent))
        return consent

    def clear_model_consent(self) -> None:
        """Forget the consent record; the next download needs a new press."""
        with self._lock:
            if self._settings.model_consent is None:
                return
            self._write_locked(replace(self._settings, model_consent=None))

    def separation_method(self) -> str | None:
        """The persisted Separation method id, or ``None`` for auto."""
        with self._lock:
            return self._settings.separation_method

    def set_separation_method(self, method: str | None) -> None:
        """Persist the Separation method preference; ``None`` unsets it."""
        normalized = _known_method(method)
        with self._lock:
            if normalized == self._settings.separation_method:
                return
            self._write_locked(replace(self._settings, separation_method=normalized))

    # -- YouTube -----------------------------------------------------------

    @property
    def youtube_enabled(self) -> bool:
        with self._lock:
            return self._settings.youtube_enabled

    def set_youtube_enabled(self, enabled: bool) -> None:
        """Persist the Behaviour → YouTube import toggle."""
        with self._lock:
            if enabled == self._settings.youtube_enabled:
                return
            self._write_locked(replace(self._settings, youtube_enabled=enabled))

    def youtube_notice_is_current(self) -> bool:
        """Whether the one-time consent was acknowledged for the shipped notice."""
        with self._lock:
            settings = self._settings
            return bool(
                settings.youtube_notice_acknowledged
                and settings.youtube_notice_version >= YOUTUBE_NOTICE_VERSION
                and settings.youtube_notice_accepted_at is not None
            )

    def accept_youtube_notice(
        self,
        *,
        version: int = YOUTUBE_NOTICE_VERSION,
        accepted_at: datetime | None = None,
    ) -> datetime:
        """Record the one-time YouTube ToS acknowledgment and its timestamp."""
        if version < 1:
            raise ValueError("consent version must be positive")
        stamp = accepted_at or datetime.now(UTC)
        with self._lock:
            self._write_locked(
                replace(
                    self._settings,
                    youtube_notice_acknowledged=True,
                    youtube_notice_version=version,
                    youtube_notice_accepted_at=stamp,
                )
            )
        return stamp

    def recent_files(self) -> tuple[RecentFile, ...]:
        with self._lock:
            return self._settings.recent

    def record_recent(
        self,
        *,
        source_id: str,
        title: str,
        origin: str,
        origin_kind: OriginKind | None = None,
        duration_s: float | None = None,
        opened_at: datetime | None = None,
    ) -> RecentFile:
        """Insert or refresh one source at the front of the MRU list."""
        entry = RecentFile(
            source_id=source_id,
            title=title,
            origin=origin,
            origin_kind=origin_kind or _infer_origin_kind(origin),
            duration_s=duration_s,
            last_opened=opened_at or datetime.now(UTC),
        )
        with self._lock:
            remaining = tuple(item for item in self._settings.recent if item.source_id != source_id)
            recent = (entry, *remaining)[:MAX_RECENT_FILES]
            self._write_locked(replace(self._settings, recent=recent))
        return entry

    def remove_recent(self, source_id: str) -> bool:
        with self._lock:
            remaining = tuple(item for item in self._settings.recent if item.source_id != source_id)
            if len(remaining) == len(self._settings.recent):
                return False
            self._write_locked(replace(self._settings, recent=remaining))
            return True

    def clear_recent(self) -> None:
        with self._lock:
            if not self._settings.recent:
                return
            self._write_locked(replace(self._settings, recent=()))

    def set_export_dir(self, directory: Path | None) -> None:
        target = Path(directory).expanduser() if directory is not None else None
        with self._lock:
            if target == self._settings.export_dir:
                return
            self._write_locked(replace(self._settings, export_dir=target))

    def mark_first_run_complete(self) -> None:
        with self._lock:
            if not self._settings.first_run:
                return
            self._write_locked(replace(self._settings, first_run=False))

    def save(self) -> None:
        with self._lock:
            self._write_locked(self._settings)

    def _load(self) -> Settings:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return Settings()
        except OSError:
            _LOG.warning("settings file is unreadable; using defaults", exc_info=True)
            return Settings()

        try:
            payload: object = json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            return self._recover_corrupt(raw, reason="invalid JSON")

        if not isinstance(payload, dict):
            return self._recover_corrupt(raw, reason="not a JSON object")
        return self._parse(cast("Mapping[str, object]", payload), raw)

    def _parse(self, payload: Mapping[str, object], raw: str) -> Settings:
        version = payload.get("schema_version", SCHEMA_VERSION)
        if not isinstance(version, int) or isinstance(version, bool):
            return self._recover_corrupt(raw, reason="invalid schema_version")

        raw_recent = payload.get("recent", [])
        if not isinstance(raw_recent, list):
            return self._recover_corrupt(raw, reason="invalid recent list")
        recent = tuple(
            entry for entry in (_parse_recent(item) for item in raw_recent) if entry is not None
        )[:MAX_RECENT_FILES]

        raw_export_dir = payload.get("export_dir")
        if raw_export_dir is None:
            export_dir = None
        elif isinstance(raw_export_dir, str):
            export_dir = Path(raw_export_dir)
        else:
            return self._recover_corrupt(raw, reason="invalid export_dir")

        first_run = payload.get("first_run", True)
        if not isinstance(first_run, bool):
            return self._recover_corrupt(raw, reason="invalid first_run")

        cache_cap_bytes = payload.get("cache_cap_bytes", DEFAULT_CACHE_CAP_BYTES)
        if (
            not isinstance(cache_cap_bytes, int)
            or isinstance(cache_cap_bytes, bool)
            or cache_cap_bytes < 0
        ):
            return self._recover_corrupt(raw, reason="invalid cache_cap_bytes")

        youtube_enabled = payload.get("youtube_enabled", True)
        if not isinstance(youtube_enabled, bool):
            return self._recover_corrupt(raw, reason="invalid youtube_enabled")

        return Settings(
            schema_version=version,
            recent=recent,
            export_dir=export_dir,
            first_run=first_run,
            cache_cap_bytes=cache_cap_bytes,
            model_consent=_parse_model_consent(payload),
            youtube_enabled=youtube_enabled,
            youtube_notice_acknowledged=_parse_youtube_acknowledged(payload),
            youtube_notice_version=_parse_youtube_notice_version(payload),
            youtube_notice_accepted_at=_parse_youtube_notice_timestamp(payload),
            separation_method=_parse_separation_method(payload),
        )

    def _recover_corrupt(self, raw: str, *, reason: str) -> Settings:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        corrupt_path = self._paths.config / f"settings.corrupt-{stamp}.json"
        try:
            self._paths.config.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.replace(self._path, corrupt_path)
        except OSError:
            _LOG.warning("could not preserve the corrupt settings file", exc_info=True)
            try:
                corrupt_path.write_text(raw, encoding="utf-8")
            except OSError:
                _LOG.warning("could not write the corrupt settings copy", exc_info=True)

        _LOG.warning(
            "settings file was corrupt (%s); defaults recreated: %s (copy: %s)",
            reason,
            self._path,
            corrupt_path,
        )
        defaults = Settings()
        try:
            self._write_locked(defaults)
        except OSError:
            _LOG.warning("could not recreate settings defaults", exc_info=True)
        return defaults

    def _write_locked(self, settings: Settings) -> None:
        """Write atomically; the caller holds the lock."""
        self._paths.ensure()
        payload = _to_payload(settings)
        data = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{self._path.name}.",
            suffix=".tmp",
            dir=self._paths.config,
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self._path)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
        self._settings = settings


def _to_payload(settings: Settings) -> dict[str, object]:
    return {
        "schema_version": settings.schema_version,
        "recent": [
            {
                "source_id": entry.source_id,
                "title": entry.title,
                "origin_kind": entry.origin_kind.value,
                "origin": entry.origin,
                "duration_s": entry.duration_s,
                "last_opened": entry.last_opened.isoformat(),
            }
            for entry in settings.recent
        ],
        "export_dir": str(settings.export_dir) if settings.export_dir is not None else None,
        "first_run": settings.first_run,
        "cache_cap_bytes": settings.cache_cap_bytes,
        "models": {
            "consent": (
                {
                    "version": settings.model_consent.version,
                    "acknowledged": settings.model_consent.acknowledged,
                    "accepted_at": settings.model_consent.accepted_at.isoformat(),
                }
                if settings.model_consent is not None
                else None
            )
        },
        "youtube_enabled": settings.youtube_enabled,
        "youtube_notice_acknowledged": settings.youtube_notice_acknowledged,
        "youtube_notice_version": settings.youtube_notice_version,
        "youtube_notice_accepted_at": (
            settings.youtube_notice_accepted_at.isoformat()
            if settings.youtube_notice_accepted_at is not None
            else None
        ),
        "separation_method": settings.separation_method,
    }


def _known_method(value: object) -> str | None:
    """A registered method id, or ``None`` for unset or unrecognised input."""
    if not isinstance(value, str):
        return None
    return value if method_by_id(value) is not None else None


def _parse_separation_method(payload: Mapping[str, object]) -> str | None:
    """Read the method preference leniently; a bad value means auto, not corrupt settings."""
    value = payload.get("separation_method")
    if value is None:
        return None
    method = _known_method(value)
    if method is None:
        _LOG.warning("unknown Separation method %r in settings; using auto", value)
    return method


def _parse_youtube_acknowledged(payload: Mapping[str, object]) -> bool:
    record = payload.get("youtube_notice_acknowledged", False)
    return record if isinstance(record, bool) else False


def _parse_youtube_notice_version(payload: Mapping[str, object]) -> int:
    record = payload.get("youtube_notice_version", 0)
    if isinstance(record, int) and not isinstance(record, bool) and record >= 0:
        return record
    return 0


def _parse_youtube_notice_timestamp(payload: Mapping[str, object]) -> datetime | None:
    record = payload.get("youtube_notice_accepted_at")
    if not isinstance(record, str):
        return None
    try:
        return datetime.fromisoformat(record)
    except ValueError:
        return None


def _parse_model_consent(payload: Mapping[str, object]) -> ModelConsent | None:
    """Read ``models.consent`` leniently: a bad record means no consent, not corrupt settings."""
    models = payload.get("models")
    if not isinstance(models, Mapping):
        return None
    record = models.get("consent")
    if not isinstance(record, Mapping):
        return None
    version = record.get("version")
    acknowledged = record.get("acknowledged")
    accepted_at = record.get("accepted_at")
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version < 1
        or not isinstance(acknowledged, bool)
        or not isinstance(accepted_at, str)
    ):
        return None
    try:
        parsed_at = datetime.fromisoformat(accepted_at)
    except ValueError:
        return None
    return ModelConsent(version=version, acknowledged=acknowledged, accepted_at=parsed_at)


def _parse_recent(item: object) -> RecentFile | None:
    if not isinstance(item, dict):
        return None
    entry = cast("Mapping[str, object]", item)
    source_id = entry.get("source_id")
    title = entry.get("title")
    origin = entry.get("origin")
    if not isinstance(source_id, str) or not isinstance(title, str):
        return None
    if not isinstance(origin, str):
        return None

    raw_kind = entry.get("origin_kind")
    if isinstance(raw_kind, str):
        try:
            origin_kind = OriginKind(raw_kind)
        except ValueError:
            return None
    elif raw_kind is None:
        origin_kind = _infer_origin_kind(origin)
    else:
        return None

    raw_duration = entry.get("duration_s")
    duration_s = (
        float(raw_duration)
        if isinstance(raw_duration, (int, float)) and not isinstance(raw_duration, bool)
        else None
    )

    raw_opened = entry.get("last_opened")
    if isinstance(raw_opened, str):
        try:
            last_opened = datetime.fromisoformat(raw_opened)
        except ValueError:
            last_opened = datetime.now(UTC)
    else:
        last_opened = datetime.now(UTC)

    return RecentFile(
        source_id=source_id,
        title=title,
        origin=origin,
        origin_kind=origin_kind,
        duration_s=duration_s,
        last_opened=last_opened,
    )


def _infer_origin_kind(origin: str) -> OriginKind:
    return OriginKind.YOUTUBE if origin.startswith(("http://", "https://")) else OriginKind.FILE
