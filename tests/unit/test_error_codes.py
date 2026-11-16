"""The error catalogue and the ``(code, reason)`` copy contract (ticket 14 §9)."""

from __future__ import annotations

import pytest

from stemforge import errors
from stemforge.ui import formatting

# An independent copy of ticket 06 §9 plus the ticket 09/14 amendments.
TICKET_14_CODES = frozenset(
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
        "cancelled",
    }
)


@pytest.mark.unit
def test_catalogue_contains_every_ticket_14_code() -> None:
    assert errors.ALL_CODES == TICKET_14_CODES


@pytest.mark.unit
def test_every_code_has_exactly_one_headline_and_action() -> None:
    assert set(formatting.COPY_BY_CODE) == errors.ALL_CODES
    for code, copy in formatting.COPY_BY_CODE.items():
        assert copy.headline, code
        assert len(copy.headline) <= formatting.MAX_HEADLINE_LENGTH == 90, code
        assert copy.action.strip(), code
        assert "\n" not in copy.headline, code
        assert "\n" not in copy.action, code
        assert code not in copy.headline, code


@pytest.mark.unit
def test_reason_specific_copy_is_also_bounded() -> None:
    for (code, reason), copy in formatting.REASON_COPY.items():
        assert code in errors.ALL_CODES, (code, reason)
        assert copy.headline, (code, reason)
        assert len(copy.headline) <= formatting.MAX_HEADLINE_LENGTH, (code, reason)
        assert copy.action.strip(), (code, reason)


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
def test_reachable_copy_matches_the_ticket() -> None:
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
