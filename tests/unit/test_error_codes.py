"""The error catalogue and the ``(code, reason)`` copy contract."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path

import pytest

from stemforge import errors, i18n
from stemforge.ui import formatting

ENGLISH_CATALOG = (
    Path(__file__).resolve().parents[2] / "src" / "stemforge" / "i18n" / "catalogs" / "en.json"
)

# An independent copy of the error catalogue, compared against the implementation as a whole.
EXPECTED_ERROR_CODES = frozenset(
    {
        "import.no_audio_stream",
        "import.unsupported_source",
        "import.download_failed",
        "import.youtube_unavailable",
        "import.youtube_age_restricted",
        "import.file_unavailable",
        "import.too_long",
        "import.youtube_url_invalid",
        "import.youtube_playlist",
        "import.youtube_too_long",
        "import.youtube_live",
        "import.youtube_rate_limited",
        "import.youtube_extractor_outdated",
        "import.network",
        "decode.failed",
        "separation.model_missing",
        "separation.model_download_failed",
        "separation.worker_crashed",
        "separation.engine_error",
        "separation.protocol_mismatch",
        "separation.output_invalid",
        "pitch.engine_missing",
        "pitch.render_failed",
        "pitch.output_invalid",
        "media.probe_failed",
        "media.encode_failed",
        "media.validation_failed",
        "export.write_failed",
        "export.original_unavailable",
        "logging.open_failed",
        "storage.disk_full",
        "storage.cache_full",
        "storage.permission_denied",
        "internal.unexpected",
        "preflight.platform_unsupported",
        "preflight.window_unavailable",
        "preflight.webview2_missing",
        "preflight.bundle_damaged",
        "preflight.storage_unwritable",
        "preflight.preview_unavailable",
        "playback.unavailable",
        "licensing.files_missing",
        "licensing.open_failed",
        "cancelled",
    }
)

# The fifteen reason-specific variants, independent of the legacy table.
EXPECTED_REASON_VARIANTS = frozenset(
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
        (errors.STORAGE_DISK_FULL, "export"),
    }
)

_FILENAME_EXTENSION = re.compile(
    r"\.(?:wav|mp3|flac|m4a|json|exe|dll|safetensors)\b",
    re.IGNORECASE,
)


def _english_catalog() -> dict[str, object]:
    payload: object = json.loads(ENGLISH_CATALOG.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _catalog_translator(tmp_path: Path, overrides: Mapping[str, str]) -> i18n.Translator:
    payload = _english_catalog()
    payload.update(overrides)
    (tmp_path / "en.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    language = i18n.Translator(catalog_dir=tmp_path)
    assert language.set_language("en") == "en"
    return language


def _base_keys(code: str) -> tuple[str, str]:
    return f"error.{code}.headline", f"error.{code}.action"


def _reason_keys(code: str, reason: str) -> tuple[str, str]:
    return (
        f"error.{code}.reason.{reason}.headline",
        f"error.{code}.reason.{reason}.action",
    )


@pytest.mark.unit
def test_catalogue_contains_every_expected_code() -> None:
    assert errors.ALL_CODES == EXPECTED_ERROR_CODES


@pytest.mark.unit
def test_every_code_renders_one_bounded_headline_and_action() -> None:
    for code in sorted(errors.ALL_CODES):
        copy = formatting.copy_for(code)
        assert copy.headline, code
        assert len(copy.headline) <= formatting.MAX_HEADLINE_LENGTH == 90, code
        assert copy.action.strip(), code
        assert "\n" not in copy.headline, code
        assert "\n" not in copy.action, code
        assert code not in copy.headline, code


@pytest.mark.unit
def test_reason_specific_copy_is_also_bounded() -> None:
    for code, reason in formatting.REASON_VARIANTS:
        copy = formatting.copy_for(code, reason)
        assert code in errors.ALL_CODES, (code, reason)
        assert copy.headline, (code, reason)
        assert len(copy.headline) <= formatting.MAX_HEADLINE_LENGTH, (code, reason)
        assert copy.action.strip(), (code, reason)


@pytest.mark.unit
def test_the_reason_variant_set_is_the_expected_fifteen() -> None:
    assert formatting.REASON_VARIANTS == EXPECTED_REASON_VARIANTS


@pytest.mark.unit
@pytest.mark.parametrize("code", sorted(errors.ALL_CODES))
def test_every_code_has_a_catalog_headline_and_action(code: str) -> None:
    catalog = _english_catalog()
    headline_key, action_key = _base_keys(code)

    headline = catalog.get(headline_key)
    action = catalog.get(action_key)

    assert isinstance(headline, str), headline_key
    assert headline.strip(), headline_key
    assert isinstance(action, str), action_key
    assert action.strip(), action_key


@pytest.mark.unit
@pytest.mark.parametrize(("code", "reason"), sorted(EXPECTED_REASON_VARIANTS))
def test_every_reason_variant_has_a_catalog_headline_and_action(code: str, reason: str) -> None:
    catalog = _english_catalog()
    headline_key, action_key = _reason_keys(code, reason)

    headline = catalog.get(headline_key)
    action = catalog.get(action_key)

    assert isinstance(headline, str), headline_key
    assert headline.strip(), headline_key
    assert isinstance(action, str), action_key
    assert action.strip(), action_key


@pytest.mark.unit
def test_copy_for_renders_the_catalog_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    language = _catalog_translator(
        tmp_path,
        {
            "error.decode.failed.headline": "Catalog headline.",
            "error.decode.failed.action": "Catalog action.",
            "error.export.write_failed.reason.in_use.headline": "Catalog reason headline.",
            "error.export.write_failed.reason.in_use.action": "Catalog reason action.",
        },
    )
    monkeypatch.setattr(formatting, "i18n", language)

    base = formatting.copy_for(errors.DECODE_FAILED)
    reason = formatting.copy_for(errors.EXPORT_WRITE_FAILED, "in_use")

    assert (base.headline, base.action) == ("Catalog headline.", "Catalog action.")
    assert (reason.headline, reason.action) == (
        "Catalog reason headline.",
        "Catalog reason action.",
    )


@pytest.mark.unit
def test_an_unknown_code_renders_the_catalog_internal_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    language = _catalog_translator(
        tmp_path,
        {
            "error.internal.unexpected.headline": "Catalog fallback headline.",
            "error.internal.unexpected.action": "Catalog fallback action.",
        },
    )
    monkeypatch.setattr(formatting, "i18n", language)

    copy = formatting.copy_for("a.code.from.a.newer.worker")

    assert (copy.headline, copy.action) == (
        "Catalog fallback headline.",
        "Catalog fallback action.",
    )


@pytest.mark.unit
def test_an_unknown_reason_renders_the_catalog_code_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    language = _catalog_translator(
        tmp_path,
        {
            "error.export.write_failed.headline": "Catalog code headline.",
            "error.export.write_failed.action": "Catalog code action.",
            "error.export.write_failed.reason.in_use.headline": "Catalog reason headline.",
            "error.export.write_failed.reason.in_use.action": "Catalog reason action.",
        },
    )
    monkeypatch.setattr(formatting, "i18n", language)

    copy = formatting.copy_for(errors.EXPORT_WRITE_FAILED, "a-reason-from-a-newer-worker")

    assert (copy.headline, copy.action) == (
        "Catalog code headline.",
        "Catalog code action.",
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "code",
    [errors.PREFLIGHT_STORAGE_UNWRITABLE, errors.STORAGE_PERMISSION_DENIED],
)
def test_the_fatal_storage_sentence_reads_its_catalog_template(
    code: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    language = _catalog_translator(
        tmp_path,
        {
            f"error.{code}.path.headline": "Catalog cannot write to {path}.",
            f"error.{code}.action": "Catalog storage action.",
        },
    )
    monkeypatch.setattr(formatting, "i18n", language)

    copy = formatting.fatal_copy(code, path="/home/alex/stemforge")

    assert (copy.headline, copy.action) == (
        "Catalog cannot write to /home/alex/stemforge.",
        "Catalog storage action.",
    )


@pytest.mark.unit
def test_every_error_catalog_headline_keeps_the_layout_invariants() -> None:
    catalog = _english_catalog()
    headlines = {
        key: value
        for key, value in catalog.items()
        if key.startswith("error.") and key.endswith(".headline") and isinstance(value, str)
    }

    assert headlines
    for key, headline in headlines.items():
        assert headline.strip(), key
        assert len(headline) <= formatting.MAX_HEADLINE_LENGTH, key
        assert "\n" not in headline, key
        assert "Traceback" not in headline, key
        assert not _FILENAME_EXTENSION.search(headline), key
        assert not any(code in headline for code in errors.ALL_CODES), key


@pytest.mark.unit
def test_unknown_codes_fall_back_to_internal_unexpected() -> None:
    fallback = formatting.copy_for(errors.INTERNAL_UNEXPECTED)

    assert formatting.copy_for("a.code.from.a.newer.worker") == fallback
    assert fallback.headline == "Something went wrong."
    assert len(fallback.headline) <= 90
    assert "log" in fallback.action.lower()


@pytest.mark.unit
def test_unknown_reason_falls_back_to_the_code_copy() -> None:
    assert formatting.copy_for(errors.EXPORT_WRITE_FAILED, "not-a-reason") == formatting.copy_for(
        errors.EXPORT_WRITE_FAILED
    )


@pytest.mark.unit
def test_reason_picks_the_specific_action() -> None:
    permission = formatting.copy_for(errors.EXPORT_WRITE_FAILED, "permission")
    in_use = formatting.copy_for(errors.EXPORT_WRITE_FAILED, "in_use")
    absent = formatting.copy_for(errors.SEPARATION_MODEL_MISSING, "absent")
    checksum = formatting.copy_for(errors.SEPARATION_MODEL_MISSING, "checksum")

    assert "permissions" in permission.action.lower()
    assert "close the file" in in_use.action.lower()
    assert absent != checksum
    assert "download" in absent.action.lower()
    assert "checksum" in checksum.action.lower()


@pytest.mark.unit
def test_reachable_copy_matches_the_catalogue() -> None:
    assert (
        formatting.copy_for(errors.PREFLIGHT_PLATFORM_UNSUPPORTED).headline
        == "StemForge runs on 64-bit Linux and 64-bit Windows."
    )
    assert (
        formatting.copy_for(errors.PREFLIGHT_PREVIEW_UNAVAILABLE).headline
        == "Playback isn't available on this system."
    )
    assert "STEMFORGE_HOME" in formatting.copy_for(errors.STORAGE_PERMISSION_DENIED).action
    assert formatting.copy_for(errors.CANCELLED).headline == "Cancelled."


@pytest.mark.unit
def test_job_cancelled_is_not_a_failure_and_keeps_a_copy() -> None:
    cancelled = errors.JobCancelled()

    assert isinstance(cancelled, errors.StemForgeError)
    assert cancelled.code == errors.CANCELLED
    assert formatting.copy_for(errors.CANCELLED).headline == "Cancelled."
