"""The neutral Interface-language layer: lookup, fallback, and resolution."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest

from stemforge import i18n

pytestmark = pytest.mark.unit


def _write_catalog(directory: Path, code: str, payload: object) -> None:
    (directory / f"{code}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def test_t_resolves_an_english_message_and_interpolates_named_placeholders() -> None:
    language = i18n.Translator()
    language.set_language("en")

    assert language.t("app.window_title", app="StemForge", track="Demo") == "StemForge — Demo"


def test_a_missing_non_english_key_falls_back_to_english_and_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_catalog(tmp_path, "en", {"greeting": "Hello, {name}!"})
    _write_catalog(tmp_path, "ro", {})
    language = i18n.Translator(catalog_dir=tmp_path)
    language.set_language("ro")

    with caplog.at_level(logging.WARNING, logger="stemforge.i18n"):
        rendered = language.t("greeting", name="Ana")

    assert rendered == "Hello, Ana!"
    assert any("greeting" in record.getMessage() for record in caplog.records)


def test_a_missing_english_key_renders_the_key_and_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_catalog(tmp_path, "en", {})
    language = i18n.Translator(catalog_dir=tmp_path)
    language.set_language("en")

    with caplog.at_level(logging.WARNING, logger="stemforge.i18n"):
        rendered = language.t("no.such.message")

    assert rendered == "no.such.message"
    assert any("no.such.message" in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize(
    "damaged",
    [
        b"{not json",
        b"[]",
        b'{"greeting": 12}',
        b'{"greeting": {"one": "one only"}}',
        b"\xff\xfe",
    ],
    ids=["invalid-json", "not-an-object", "non-string-entry", "wrong-plural-forms", "not-utf8"],
)
def test_a_damaged_catalog_opens_in_english_with_a_logged_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, damaged: bytes
) -> None:
    _write_catalog(tmp_path, "en", {"greeting": "Hello"})
    (tmp_path / "ro.json").write_bytes(damaged)
    language = i18n.Translator(catalog_dir=tmp_path)

    with caplog.at_level(logging.WARNING, logger="stemforge.i18n"):
        resolved = language.set_language("ro")
        rendered = language.t("greeting")

    assert resolved == "en"
    assert language.language == "en"
    assert rendered == "Hello"
    assert any("ro.json" in record.getMessage() for record in caplog.records)


def test_a_missing_locale_catalog_opens_in_english_with_a_logged_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_catalog(tmp_path, "en", {"greeting": "Hello"})
    language = i18n.Translator(catalog_dir=tmp_path)

    with caplog.at_level(logging.WARNING, logger="stemforge.i18n"):
        resolved = language.set_language("ro")

    assert resolved == "en"
    assert language.t("greeting") == "Hello"
    assert any("ro.json" in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize(
    "system_locale",
    ["ro", "ro_RO", "ro_RO.UTF-8", "RO-ro", "ro-RO"],
)
def test_system_resolves_romanian_locales_to_romanian(system_locale: str) -> None:
    assert i18n.resolve_language("system", system_locale) == "ro"


@pytest.mark.parametrize(
    "system_locale",
    [None, "", "en", "en_US.UTF-8", "de_DE.UTF-8", "fr_FR.UTF-8"],
)
def test_system_resolves_every_other_locale_to_english(system_locale: str | None) -> None:
    assert i18n.resolve_language("system", system_locale) == "en"


def test_an_explicit_preference_wins_over_the_system_locale() -> None:
    assert i18n.resolve_language("en", "ro_RO.UTF-8") == "en"
    assert i18n.resolve_language("ro", "en_US.UTF-8") == "ro"


def test_an_unknown_preference_falls_back_to_system_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="stemforge.i18n"):
        resolved = i18n.resolve_language("fr", "ro_RO.UTF-8")

    assert resolved == "ro"
    assert i18n.normalize_preference("fr") == "system"
    assert any("fr" in record.getMessage() for record in caplog.records)


def test_a_missing_preference_is_system_without_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="stemforge.i18n"):
        assert i18n.normalize_preference(None) == "system"

    assert caplog.records == []


def test_locale_metadata_carries_the_autonym_and_plural_rule() -> None:
    assert i18n.locale_info("en").autonym == "English"
    assert i18n.locale_info("en").plural_rule == "english"
    assert i18n.locale_info("ro").autonym == "Română"
    assert i18n.locale_info("ro").plural_rule == "romanian"
    assert i18n.locale_info("ro").plural_forms == ("one", "few", "other")


def test_plural_lookup_uses_the_romanian_forms(tmp_path: Path) -> None:
    _write_catalog(
        tmp_path,
        "en",
        {"items": {"one": "{count} item", "other": "{count} items"}},
    )
    _write_catalog(
        tmp_path,
        "ro",
        {
            "items": {
                "one": "{count} articol",
                "few": "{count} articole",
                "other": "{count} de articole",
            }
        },
    )
    language = i18n.Translator(catalog_dir=tmp_path)
    assert language.set_language("ro") == "ro"

    expected = {
        0: "0 articole",
        1: "1 articol",
        2: "2 articole",
        19: "19 articole",
        20: "20 de articole",
        101: "101 articole",
        120: "120 de articole",
    }

    assert {count: language.t("items", count=count) for count in expected} == expected


def test_plural_lookup_uses_the_english_forms(tmp_path: Path) -> None:
    _write_catalog(
        tmp_path,
        "en",
        {"items": {"one": "{count} item", "other": "{count} items"}},
    )
    language = i18n.Translator(catalog_dir=tmp_path)
    assert language.set_language("en") == "en"

    assert [language.t("items", count=count) for count in (0, 1, 2, 19, 20, 101, 120)] == [
        "0 items",
        "1 item",
        "2 items",
        "19 items",
        "20 items",
        "101 items",
        "120 items",
    ]


def test_a_missing_romanian_plural_key_uses_the_english_plural_rule(tmp_path: Path) -> None:
    _write_catalog(
        tmp_path,
        "en",
        {"items": {"one": "{count} item", "other": "{count} items"}},
    )
    _write_catalog(tmp_path, "ro", {})
    language = i18n.Translator(catalog_dir=tmp_path)
    language.set_language("ro")

    assert language.t("items", count=2) == "2 items"


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ({"LC_ALL": "ro_RO.UTF-8", "LC_MESSAGES": "en_US.UTF-8", "LANG": "de_DE.UTF-8"}, "ro"),
        ({"LC_ALL": "", "LC_MESSAGES": "ro_RO.UTF-8", "LANG": "en_US.UTF-8"}, "ro"),
        ({"LC_ALL": "", "LC_MESSAGES": "", "LANG": "en_US.UTF-8"}, "en_US"),
        ({"LC_ALL": "", "LC_MESSAGES": "", "LANG": "Romanian_Romania"}, "ro"),
        ({"LC_ALL": "", "LC_MESSAGES": "", "LANG": "C.UTF-8"}, "en"),
    ],
)
def test_system_locale_reads_the_message_variables_and_normalises_romanian(
    monkeypatch: pytest.MonkeyPatch,
    environment: dict[str, str],
    expected: str,
) -> None:
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.setenv(name, environment[name])

    assert i18n.system_locale() == expected


def test_system_locale_falls_back_to_the_stdlib_locale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("locale.getlocale", lambda: ("ro_RO", "UTF-8"))

    assert i18n.system_locale() == "ro"


def test_system_locale_is_absent_when_nothing_reports_a_locale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("locale.getlocale", lambda: (None, None))

    assert i18n.system_locale() is None


def test_framework_language_maps_the_catalog_codes_to_nicegui() -> None:
    assert i18n.framework_language("ro") == "ro"
    assert i18n.framework_language("en") == "en-GB"
    assert i18n.framework_language("unknown") == "en-GB"


def test_the_english_fixture_pins_the_host_locale_for_the_whole_suite() -> None:
    """Environment guard: a developer's shell locale must not change the suite.

    The copy assertions are exact English strings, so this fixture cannot be
    allowed to inherit the host locale.
    """
    assert i18n.system_locale() == "en_US"
    assert i18n.language() == "en"


def test_the_process_translator_switches_language_without_reading_the_host_locale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail() -> object:
        raise AssertionError("the host locale must be supplied by the caller")

    monkeypatch.setattr("locale.getlocale", fail)

    assert i18n.set_language("system") == "en"
    assert i18n.language() == "en"
    assert i18n.preference() == "system"
    assert i18n.set_language("en") == "en"
    assert i18n.preference() == "en"
    assert i18n.t("app.name") == "StemForge"


def test_the_translation_layer_imports_before_the_window_and_without_nicegui() -> None:
    script = "\n".join(
        [
            "import sys",
            "import stemforge.i18n",
            "assert 'nicegui' not in sys.modules",
            "assert stemforge.i18n.t('app.name') == 'StemForge'",
        ]
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_a_plural_message_without_a_count_does_not_crash(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_catalog(
        tmp_path,
        "en",
        {"items": {"one": "{count} item", "other": "{count} items"}},
    )
    language = i18n.Translator(catalog_dir=tmp_path)
    language.set_language("en")

    with caplog.at_level(logging.WARNING, logger="stemforge.i18n"):
        rendered = language.t("items")

    assert rendered == "{count} items"
    assert any("count" in record.getMessage() for record in caplog.records)


def test_a_message_with_an_unknown_placeholder_does_not_crash(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_catalog(tmp_path, "en", {"greeting": "Hello, {name.missing}!"})
    language = i18n.Translator(catalog_dir=tmp_path)
    language.set_language("en")

    with caplog.at_level(logging.WARNING, logger="stemforge.i18n"):
        rendered = language.t("greeting", name="Ana")

    assert rendered == "Hello, {name.missing}!"
    assert any("greeting" in record.getMessage() for record in caplog.records)


def test_use_source_locale_renders_english_and_restores_the_preference() -> None:
    i18n.set_language("ro", system_locale="ro_RO.UTF-8")

    with i18n.use_source_locale():
        assert i18n.language() == "en"
        assert i18n.t("common.cancel") == "Cancel"

    assert i18n.language() == "ro"
    assert i18n.preference() == "ro"
    assert i18n.t("common.cancel") == "Anulează"
