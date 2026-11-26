"""The neutral Interface-language layer backed by per-locale JSON catalogs.

English is the source locale and the per-key fallback. A message is either a
string with ``{named}`` placeholders or a plural map carrying the locale's forms;
``count`` selects the form. Catalogs are data, so this module imports neither
NiceGUI nor another StemForge package and can be used before the window exists.
"""

from __future__ import annotations

import json
import locale
import logging
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final, Literal

__all__ = [
    "LOCALES",
    "SOURCE_LOCALE",
    "SYSTEM_PREFERENCE",
    "Locale",
    "Translator",
    "framework_language",
    "has_message",
    "language",
    "locale_info",
    "normalize_preference",
    "plural_form",
    "preference",
    "resolve_language",
    "set_language",
    "system_locale",
    "t",
]

_LOG = logging.getLogger(__name__)

SOURCE_LOCALE: Final = "en"
SYSTEM_PREFERENCE: Final = "system"

Message = str | Mapping[str, str]


@dataclass(frozen=True, slots=True)
class Locale:
    """Display metadata and the plural system one catalog follows."""

    code: str
    autonym: str
    plural_rule: str
    plural_forms: tuple[str, ...]


LOCALES: Mapping[str, Locale] = MappingProxyType(
    {
        "en": Locale("en", "English", "english", ("one", "other")),
        "ro": Locale("ro", "Română", "romanian", ("one", "few", "other")),
    }
)

_PREFERENCES: Final = (SYSTEM_PREFERENCE, *LOCALES)


def _english_plural_form(count: int) -> str:
    return "one" if count == 1 else "other"


def _romanian_plural_form(count: int) -> str:
    if count == 1:
        return "one"
    remainder = count % 100
    if count == 0 or 1 <= remainder <= 19:
        return "few"
    return "other"


_PLURAL_RULES: Mapping[str, Callable[[int], str]] = MappingProxyType(
    {
        "english": _english_plural_form,
        "romanian": _romanian_plural_form,
    }
)


class Translator:
    """One process-wide or test-scoped view of the locale catalogs."""

    def __init__(self, *, catalog_dir: Path | None = None) -> None:
        self._catalog_dir = Path(catalog_dir) if catalog_dir is not None else _catalog_dir()
        self._preference = SYSTEM_PREFERENCE
        self._language = SOURCE_LOCALE
        self._english = (
            _load_catalog(self._catalog_dir / f"{SOURCE_LOCALE}.json", LOCALES["en"]) or {}
        )
        self._active: Mapping[str, Message] = self._english

    @property
    def language(self) -> str:
        """The effective Interface language."""
        return self._language

    @property
    def preference(self) -> str:
        """The persisted ``system``/``en``/``ro`` preference."""
        return self._preference

    def set_language(
        self, preference: object = SYSTEM_PREFERENCE, *, system_locale: str | None = None
    ) -> str:
        """Resolve and activate the Interface language; English is the fallback."""
        normalized = normalize_preference(preference)
        resolved = resolve_language(normalized, system_locale)
        if resolved == SOURCE_LOCALE:
            catalog: dict[str, Message] | None = self._english
        else:
            catalog = _load_catalog(self._catalog_dir / f"{resolved}.json", LOCALES[resolved])
        if resolved != SOURCE_LOCALE and catalog is None:
            resolved = SOURCE_LOCALE
            catalog = self._english
        self._preference = normalized
        self._language = resolved
        self._active = catalog if catalog is not None else {}
        return resolved

    def has_message(self, message_id: str) -> bool:
        """Whether the source locale defines ``message_id``.

        A renderer uses this to prefer a code's catalog copy and fall back to the
        English string a snapshot already carries when the code is unknown.
        """
        return message_id in self._english

    def t(self, message_id: str, /, **placeholders: object) -> str:
        """Render one message ID, falling back to English and then the key."""
        entry = self._active.get(message_id)
        locale_code = self._language
        if entry is None and self._language != SOURCE_LOCALE:
            fallback = self._english.get(message_id)
            if fallback is not None:
                _LOG.warning(
                    "message %r is missing from the %s catalog; using English",
                    message_id,
                    self._language,
                )
                entry = fallback
                locale_code = SOURCE_LOCALE
        if entry is None:
            _LOG.warning("message %r is missing from the English catalog", message_id)
            return message_id
        return _render(message_id, entry, locale_code, placeholders)


def t(message_id: str, /, **placeholders: object) -> str:
    """Render through the process-wide translator."""
    return _default.t(message_id, **placeholders)


def has_message(message_id: str) -> bool:
    """Whether the source locale defines ``message_id``."""
    return _default.has_message(message_id)


def set_language(
    preference: object = SYSTEM_PREFERENCE, *, system_locale: str | None = None
) -> str:
    """Resolve and activate the process-wide Interface language."""
    return _default.set_language(preference, system_locale=system_locale)


def language() -> str:
    """The process-wide effective Interface language."""
    return _default.language


def preference() -> str:
    """The process-wide persisted language preference."""
    return _default.preference


def locale_info(code: str) -> Locale:
    """The autonym and plural rule for one shipped catalog."""
    return LOCALES[code]


def system_locale() -> str | None:
    """The host's message locale, read on demand and never at import time.

    ``LC_ALL`` wins over ``LC_MESSAGES`` and ``LANG``; when none is set the
    stdlib locale is the fallback. Windows reports a display name such as
    ``Romanian_Romania``, so a Romanian system normalises to the catalog code.
    """
    value: str | None = None
    for variable in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(variable, "").strip()
        if value:
            return _normalize_system_locale(value)
    try:
        value = locale.getlocale()[0]
    except (TypeError, ValueError):
        return None
    return _normalize_system_locale(value)


def _normalize_system_locale(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.casefold().startswith("romanian"):
        return "ro"
    base = re.split(r"[.@]", text, maxsplit=1)[0]
    folded = base.casefold()
    if folded.startswith("ro") and (len(base) == 2 or base[2] in "-_"):
        return "ro"
    if folded in {"c", "posix"}:
        return "en"
    return base or None


def framework_language(code: str) -> Literal["ro", "en-GB"]:
    """Map a catalog code to the language NiceGUI's Quasar layer expects."""
    return "ro" if code == "ro" else "en-GB"


def normalize_preference(value: object) -> str:
    """Map a persisted value to ``system``/``en``/``ro``; unknown means system."""
    if value is None:
        return SYSTEM_PREFERENCE
    if isinstance(value, str) and value in _PREFERENCES:
        return value
    _LOG.warning("unknown Interface language %r; using system", value)
    return SYSTEM_PREFERENCE


def resolve_language(preference: object, system_locale: str | None = None) -> str:
    """Resolve one preference and host locale to the effective catalog code."""
    normalized = normalize_preference(preference)
    if normalized != SYSTEM_PREFERENCE:
        return normalized
    if isinstance(system_locale, str) and system_locale.casefold().startswith("ro"):
        return "ro"
    return "en"


def plural_form(locale_code: str, count: int) -> str:
    """Select the CLDR form the locale uses for an integer count."""
    return _PLURAL_RULES[locale_info(locale_code).plural_rule](count)


def _render(
    message_id: str,
    entry: Message,
    locale_code: str,
    placeholders: Mapping[str, object],
) -> str:
    if isinstance(entry, str):
        return _format(message_id, entry, placeholders)

    count = placeholders.get("count")
    if isinstance(count, int) and not isinstance(count, bool):
        form = plural_form(locale_code, count)
    else:
        _LOG.warning("message %r needs an integer count; using the other form", message_id)
        form = "other"
    return _format(message_id, entry.get(form, entry["other"]), placeholders)


def _format(message_id: str, text: str, placeholders: Mapping[str, object]) -> str:
    try:
        return text.format_map(placeholders)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        _LOG.warning("message %r could not be formatted; rendering it verbatim", message_id)
        return text


def _catalog_dir() -> Path:
    return Path(__file__).resolve().parent / "catalogs"


def _load_catalog(path: Path, locale: Locale) -> dict[str, Message] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        _LOG.warning("locale catalog %s is unreadable (%s); using English", path, exc)
        return None
    try:
        payload: object = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        _LOG.warning("locale catalog %s is damaged (%s); using English", path, exc)
        return None
    if not isinstance(payload, dict):
        _LOG.warning("locale catalog %s is damaged (not a JSON object); using English", path)
        return None

    entries: dict[str, Message] = {}
    forms = set(locale.plural_forms)
    for key, value in payload.items():
        if not isinstance(key, str) or not key:
            _LOG.warning("locale catalog %s is damaged (invalid key); using English", path)
            return None
        if isinstance(value, str):
            entries[key] = value
            continue
        if (
            isinstance(value, dict)
            and set(value) == forms
            and all(isinstance(form, str) for form in value.values())
        ):
            entries[key] = MappingProxyType(dict(value))
            continue
        _LOG.warning("locale catalog %s is damaged (entry %r); using English", path, key)
        return None
    return entries


_default = Translator()
