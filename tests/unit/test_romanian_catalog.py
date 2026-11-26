"""The Romanian catalog is complete, structurally sound, and on-vocabulary."""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from string import Formatter

import pytest

from stemforge import errors, i18n
from stemforge.domain import display, progress
from stemforge.domain.roles import INSTRUMENTAL_ROLE, STEM_ROLES
from stemforge.ui import formatting

pytestmark = pytest.mark.unit

CATALOG_DIR = Path(__file__).resolve().parents[2] / "src" / "stemforge" / "i18n" / "catalogs"
ENGLISH = json.loads((CATALOG_DIR / "en.json").read_text(encoding="utf-8"))
ROMANIAN = json.loads((CATALOG_DIR / "ro.json").read_text(encoding="utf-8"))

COMMA_BELOW = frozenset("șȘțȚ")


def _fields(value: object) -> set[str]:
    texts = [value] if isinstance(value, str) else list(value.values())
    names: set[str] = set()
    for text in texts:
        assert isinstance(text, str)
        for _, name, _, _ in Formatter().parse(text):
            if name is not None:
                names.add(name)
    return names


def _samples(names: set[str]) -> dict[str, object]:
    return {name: 2 if name == "count" else f"<{name}>" for name in names}


def _roman_translator() -> i18n.Translator:
    translator = i18n.Translator()
    assert translator.set_language("ro", system_locale="ro_RO.UTF-8") == "ro"
    return translator


def _values(payload: Mapping[str, object]) -> Iterator[tuple[str, str]]:
    for key, value in payload.items():
        if isinstance(value, str):
            yield key, value
        else:
            assert isinstance(value, Mapping)
            for form, text in value.items():
                assert isinstance(text, str)
                yield f"{key}.{form}", text


def test_every_english_key_has_a_romanian_key_and_no_romanian_key_is_orphaned() -> None:
    assert set(ROMANIAN) == set(ENGLISH)


def test_romanian_placeholders_match_english_per_key() -> None:
    for key, value in ENGLISH.items():
        assert _fields(value) == _fields(ROMANIAN[key]), key


def test_no_placeholder_is_positional() -> None:
    for key, text in (*_values(ENGLISH), *_values(ROMANIAN)):
        for _, name, _, _ in Formatter().parse(text):
            if name is None:
                continue
            assert name, key
            assert not name.isdigit(), (key, name)


def test_plural_maps_carry_exactly_the_locale_forms() -> None:
    romanian_forms = set(i18n.locale_info("ro").plural_forms)
    english_forms = set(i18n.locale_info("en").plural_forms)

    for key, value in ROMANIAN.items():
        assert isinstance(value, dict) == isinstance(ENGLISH[key], dict), key
        if not isinstance(value, dict):
            continue
        assert set(value) == romanian_forms, key
        assert set(ENGLISH[key]) == english_forms, key
        assert "count" in _fields(value), key


def test_every_count_string_accepts_an_integer_count() -> None:
    translator = _roman_translator()

    for key, value in ROMANIAN.items():
        names = _fields(value)
        if isinstance(value, dict) or "count" in names:
            assert "count" in names, key
            rendered = translator.t(key, **_samples(names))
            assert "{" not in rendered, key


def test_the_romanian_plural_forms_are_correct_for_the_cldr_boundaries() -> None:
    translator = _roman_translator()
    expected = {
        0: "0 fișiere",
        1: "1 fișier",
        2: "2 fișiere",
        19: "19 fișiere",
        20: "20 de fișiere",
        101: "101 fișiere",
        120: "120 de fișiere",
    }

    assert {
        count: translator.t("settings.file_count", count=count) for count in expected
    } == expected
    assert translator.t("export.batch.confirm", count=1) == "Exportă 1 fișier"
    assert translator.t("export.batch.confirm", count=2) == "Exportă 2 fișiere"
    assert translator.t("export.batch.confirm", count=20) == "Exportă 20 de fișiere"


def test_every_error_code_and_reason_resolves_in_romanian(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    translator = _roman_translator()
    monkeypatch.setattr(formatting, "i18n", translator)

    for code in errors.ALL_CODES:
        rendered = formatting.copy_for(code)
        assert rendered.headline == ROMANIAN[f"error.{code}.headline"], code
        assert rendered.action == ROMANIAN[f"error.{code}.action"], code
        assert not rendered.headline.startswith("error."), code

    for code, reason in formatting.REASON_VARIANTS:
        rendered = formatting.copy_for(code, reason)
        prefix = f"error.{code}.reason.{reason}"
        assert rendered.headline == ROMANIAN[f"{prefix}.headline"], (code, reason)
        assert rendered.action == ROMANIAN[f"{prefix}.action"], (code, reason)


def test_static_romanian_error_headlines_keep_the_layout_cap() -> None:
    headlines = {
        key: value
        for key, value in ROMANIAN.items()
        if key.startswith("error.") and key.endswith(".headline") and isinstance(value, str)
    }

    assert headlines
    for key, headline in headlines.items():
        assert len(headline) <= formatting.MAX_HEADLINE_LENGTH, key
        assert "\n" not in headline, key
        assert not any(code in headline for code in errors.ALL_CODES), key


def test_dynamic_romanian_error_sentences_fit_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    translator = _roman_translator()
    monkeypatch.setattr(formatting, "i18n", translator)
    long_folder = "x" * 28
    long_path = "/" + "y" * 59

    cases = (
        formatting.copy_for(errors.IMPORT_TOO_LONG, context={"duration_s": 1800}),
        formatting.copy_for(errors.IMPORT_YOUTUBE_TOO_LONG, context={"duration_s": 900}),
        formatting.copy_for(errors.EXPORT_WRITE_FAILED, context={"path": long_folder}),
        formatting.copy_for(
            errors.STORAGE_DISK_FULL,
            "export",
            context={"path": long_folder, "required": 999_000_000},
        ),
        formatting.copy_for(
            errors.STORAGE_DISK_FULL,
            context={"required": 999_000_000, "free": 1_000_000},
        ),
        formatting.copy_for(errors.STORAGE_PERMISSION_DENIED, context={"path": long_path}),
    )

    for rendered in cases:
        assert 0 < len(rendered.headline) <= formatting.MAX_HEADLINE_LENGTH, rendered
        assert not rendered.headline.startswith("error."), rendered


def test_an_overlong_dynamic_row_falls_back_to_the_romanian_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = copy.deepcopy(ROMANIAN)
    payload["error.export.write_failed.folder.headline"] = "X" * 90 + " {folder}"
    (tmp_path / "en.json").write_text(json.dumps(ENGLISH, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "ro.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    translator = i18n.Translator(catalog_dir=tmp_path)
    assert translator.set_language("ro") == "ro"
    monkeypatch.setattr(formatting, "i18n", translator)

    rendered = formatting.copy_for(errors.EXPORT_WRITE_FAILED, context={"path": "Music"})

    assert rendered.headline == ROMANIAN["error.export.write_failed.headline"]
    assert rendered.action == ROMANIAN["error.export.write_failed.action"]


def test_the_unknown_code_falls_back_to_the_romanian_internal_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    translator = _roman_translator()
    monkeypatch.setattr(formatting, "i18n", translator)

    fallback = formatting.copy_for("a.code.from.a.newer.worker")

    assert fallback == formatting.copy_for(errors.INTERNAL_UNEXPECTED)
    assert fallback.headline == ROMANIAN["error.internal.unexpected.headline"]


def test_every_job_and_display_code_resolves_in_romanian() -> None:
    translator = _roman_translator()
    codes = (
        set(progress.STAGE_CODES)
        | set(progress.DETAIL_CODES)
        | set(display.DISPLAY_CODES)
        | {display.role_label_key(role) for role in (*STEM_ROLES, INSTRUMENTAL_ROLE)}
    )

    for code in codes:
        assert code in ROMANIAN, code
        rendered = translator.t(code, **_samples(_fields(ROMANIAN[code])))
        assert rendered, code
        assert "{" not in rendered, code


def test_the_romanian_catalog_uses_comma_below_diacritics() -> None:
    text = "".join(value for _, value in _values(ROMANIAN))

    assert set(text) & COMMA_BELOW
