"""UI presentation helpers: durations, audio facts, cache sizes, and recency."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from stemforge import errors, i18n
from stemforge.domain.display import LabelRef
from stemforge.domain.models import OriginKind, Source, SourceMetadata, Track
from stemforge.domain.progress import CodedText
from stemforge.ui import formatting

pytestmark = pytest.mark.unit

ENGLISH_CATALOG = (
    Path(__file__).resolve().parents[2] / "src" / "stemforge" / "i18n" / "catalogs" / "en.json"
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


def _template(key: str) -> str:
    value = _english_catalog()[key]
    assert isinstance(value, str)
    return value


def test_duration_is_m_ss_and_handles_hours() -> None:
    assert formatting.format_duration(187.4) == "3:07"
    assert formatting.format_duration(3.0) == "0:03"
    assert formatting.format_duration(3_725) == "1:02:05"
    assert formatting.format_duration(None) == "—"
    assert formatting.format_duration(-1) == "—"


def test_audio_facts_are_compact_and_readable() -> None:
    assert formatting.format_sample_rate(44_100) == "44.1 kHz"
    assert formatting.format_sample_rate(48_000) == "48 kHz"
    assert formatting.format_sample_rate(None) is None
    assert formatting.format_channels(1) == "mono"
    assert formatting.format_channels(2) == "stereo"
    assert formatting.format_channels(6) == "6 ch"
    assert formatting.format_channels(None) is None


def _track(origin: str, kind: OriginKind, *, title: str = "Song") -> Track:
    return Track(
        track_id="track",
        title=title,
        source=Source(
            source_id="source",
            origin=origin,
            origin_kind=kind,
            metadata=SourceMetadata(
                title=title,
                duration_s=187.0,
                sample_rate=44_100,
                channels=2,
            ),
        ),
    )


def test_detail_meta_names_source_duration_and_format() -> None:
    """Origin · filename or URL · duration · 44.1 kHz stereo."""
    local = formatting.detail_meta(_track("/music/My Song.WAV", OriginKind.FILE))
    youtube = formatting.detail_meta(
        _track("https://www.youtube.com/watch?v=dQw4w9WgXcQ", OriginKind.YOUTUBE)
    )

    assert local == ["Local file", "My Song.WAV", "3:07", "44.1 kHz", "stereo"]
    assert youtube == [
        "YouTube",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "3:07",
        "44.1 kHz",
        "stereo",
    ]


def test_track_icons_distinguish_local_audio_from_youtube_video() -> None:
    """The rail's 26 px type icon follows the Source origin."""
    assert formatting.track_icon(OriginKind.FILE) == "music_note"
    assert formatting.track_icon(OriginKind.YOUTUBE) == "movie"


def test_cache_sizes_use_one_decimal_and_binary_units() -> None:
    assert formatting.format_bytes(3_650_000_000) == "3.4 GB"
    assert formatting.format_bytes(120_000_000) == "114.4 MB"
    assert formatting.format_bytes(0) == "0.0 MB"


def test_cache_meter_never_exceeds_a_full_bar() -> None:
    assert formatting.cache_percent(0, 8 * 1024**3) == 0
    assert formatting.cache_percent(4 * 1024**3, 8 * 1024**3) == 50
    assert formatting.cache_percent(9 * 1024**3, 8 * 1024**3) == 100
    assert formatting.cache_percent(10, 0) == 0


@pytest.mark.parametrize(
    ("detail", "reason"),
    [
        (None, None),
        ("", None),
        ("reason=offline", "offline"),
        ("decode failed\nreason=checksum", "checksum"),
        ("single", "single"),
        ("a whole sentence is not a reason", None),
    ],
)
def test_reason_from_detail_keeps_the_stable_classifier(
    detail: str | None, reason: str | None
) -> None:
    assert formatting.reason_from_detail(detail) == reason


def test_relative_time_is_human_and_stable() -> None:
    now = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)

    assert formatting.format_relative_time(now - timedelta(seconds=10), now=now) == "just now"
    assert formatting.format_relative_time(now - timedelta(minutes=12), now=now) == "12 min ago"
    assert formatting.format_relative_time(now - timedelta(hours=3), now=now) == "3 h ago"
    assert formatting.format_relative_time(now - timedelta(days=2), now=now) == "2 d ago"


def test_the_dialog_rejection_has_sensible_copy() -> None:
    copy = formatting.copy_for(errors.IMPORT_UNSUPPORTED_SOURCE, "extension")

    assert copy.headline == "That file type isn't supported."
    assert "WAV, MP3, FLAC, or M4A" in copy.action


@pytest.mark.parametrize(
    ("reason", "action"),
    [
        ("permission", "Choose another folder, or check its permissions."),
        ("in_use", "Close the file in other apps and try again."),
        ("name_too_long", "Choose a shorter name or folder."),
        ("device_gone", "Reconnect the drive and try again."),
        ("other", "Try another folder; the details are in the job log."),
    ],
)
def test_every_export_write_reason_has_its_sentence(reason: str, action: str) -> None:
    copy = formatting.copy_for(errors.EXPORT_WRITE_FAILED, reason)

    assert copy.headline == "Couldn't save the file."
    assert copy.action == action


def test_export_disk_full_chooses_another_location() -> None:
    copy = formatting.copy_for(errors.STORAGE_DISK_FULL, "export")

    assert copy.headline == "Not enough disk space."
    assert copy.action == "Choose another location."


def test_import_copy_names_the_actual_duration() -> None:
    """``This track is <m:ss> long. The limit is 30:00.``"""
    copy = formatting.copy_for(errors.IMPORT_TOO_LONG, context={"duration_s": 1_872.0})

    assert copy.headline == "This track is 31:12 long. The limit is 30:00."
    assert copy.action == "Trim it and import again."


def test_youtube_too_long_copy_names_the_actual_duration() -> None:
    """``This video is 24:10 — longer than the 15-minute limit.``"""
    copy = formatting.copy_for(errors.IMPORT_YOUTUBE_TOO_LONG, context={"duration_s": 1_450.0})

    assert copy.headline == "This video is 24:10 — longer than the 15-minute limit."


def test_youtube_no_audio_is_a_video_not_a_file() -> None:
    """The shared import code carries its own video wording."""
    local = formatting.copy_for(errors.IMPORT_NO_AUDIO_STREAM)
    video = formatting.copy_for(errors.IMPORT_NO_AUDIO_STREAM, origin=OriginKind.YOUTUBE)

    assert local.headline == "This file has no audio track."
    assert video.headline == "This video has no audio track."
    assert video.action == "Choose a video with audio."


def test_export_write_copy_names_the_folder_not_the_full_path() -> None:
    """The headline names the last path component."""
    copy = formatting.copy_for(
        errors.EXPORT_WRITE_FAILED,
        "permission",
        context={"path": "/home/alex/exports"},
    )

    assert copy.headline == "Couldn't save the file to exports."
    assert copy.action == "Choose another folder, or check its permissions."


def test_export_disk_full_copy_names_the_folder_and_needed_size() -> None:
    copy = formatting.copy_for(
        errors.STORAGE_DISK_FULL,
        "export",
        context={"path": "/home/alex/exports", "required": 120_000_000, "free": 1_048_576},
    )

    assert copy.headline == ("Not enough space at exports. This file needs about 114.4 MB.")
    assert copy.action == "Choose another location."


def test_import_disk_full_copy_reports_needed_and_free() -> None:
    """Report needed vs free, then the cache action."""
    copy = formatting.copy_for(
        errors.STORAGE_DISK_FULL,
        context={"path": "/home/alex/stemforge", "required": 120_000_000, "free": 10_485_760},
    )

    assert copy.headline == (
        "Not enough disk space. This import needs about 114.4 MB and only 10.0 MB is free."
    )
    assert copy.action == "Clear cached audio to make room."


def test_storage_permission_copy_names_the_path_when_known() -> None:
    copy = formatting.copy_for(
        errors.STORAGE_PERMISSION_DENIED,
        context={"path": "/home/alex/stemforge"},
    )

    assert copy.headline == "StemForge can't write to /home/alex/stemforge."


def test_dynamic_copy_never_exceeds_the_headline_cap() -> None:
    """Interpolated copy keeps the 90-character ceiling."""
    folder = "a-very-long-export-folder-name-that-would-overflow"
    copy = formatting.copy_for(
        errors.STORAGE_DISK_FULL,
        "export",
        context={
            "path": f"/home/alex/{folder}",
            "required": 120_000_000,
            "free": 1_048_576,
        },
    )

    assert len(copy.headline) <= formatting.MAX_HEADLINE_LENGTH
    assert copy.headline.startswith("Not enough space at a-very-long-export-folder-")
    assert "…" in copy.headline


@pytest.mark.parametrize(
    ("key", "placeholder"),
    [
        ("error.import.too_long.duration.headline", "{duration}"),
        ("error.import.youtube_too_long.duration.headline", "{duration}"),
        ("error.export.write_failed.folder.headline", "{folder}"),
        ("error.storage.disk_full.export.headline", "{folder}"),
        ("error.storage.disk_full.export.headline", "{required}"),
        ("error.storage.disk_full.import.headline", "{required}"),
        ("error.storage.disk_full.import.headline", "{free}"),
        ("error.storage.permission_denied.path.headline", "{path}"),
        ("error.preflight.storage_unwritable.path.headline", "{path}"),
    ],
)
def test_dynamic_error_templates_carry_named_placeholders(key: str, placeholder: str) -> None:
    assert placeholder in _template(key)


def test_dynamic_sentences_render_their_catalog_templates() -> None:
    duration = formatting.copy_for(errors.IMPORT_TOO_LONG, context={"duration_s": 1_872.0})
    video_duration = formatting.copy_for(
        errors.IMPORT_YOUTUBE_TOO_LONG, context={"duration_s": 1_450.0}
    )
    folder = formatting.copy_for(
        errors.EXPORT_WRITE_FAILED,
        "permission",
        context={"path": "/home/alex/exports"},
    )
    export_space = formatting.copy_for(
        errors.STORAGE_DISK_FULL,
        "export",
        context={"path": "/home/alex/exports", "required": 120_000_000},
    )
    import_space = formatting.copy_for(
        errors.STORAGE_DISK_FULL,
        context={"path": "/home/alex/stemforge", "required": 120_000_000, "free": 10_485_760},
    )
    permission = formatting.copy_for(
        errors.STORAGE_PERMISSION_DENIED,
        context={"path": "/home/alex/stemforge"},
    )
    video_no_audio = formatting.copy_for(errors.IMPORT_NO_AUDIO_STREAM, origin=OriginKind.YOUTUBE)

    assert duration.headline == _template("error.import.too_long.duration.headline").format(
        duration="31:12"
    )
    assert video_duration.headline == _template(
        "error.import.youtube_too_long.duration.headline"
    ).format(duration="24:10")
    assert folder.headline == _template("error.export.write_failed.folder.headline").format(
        folder="exports"
    )
    assert export_space.headline == _template("error.storage.disk_full.export.headline").format(
        folder="exports", required="114.4 MB"
    )
    assert import_space.headline == _template("error.storage.disk_full.import.headline").format(
        required="114.4 MB", free="10.0 MB"
    )
    assert permission.headline == _template("error.storage.permission_denied.path.headline").format(
        path="/home/alex/stemforge"
    )
    assert video_no_audio.headline == _template("error.import.no_audio_stream.video.headline")
    assert video_no_audio.action == _template("error.import.no_audio_stream.video.action")


def test_a_dynamic_sentence_reads_the_catalog_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    language = _catalog_translator(
        tmp_path,
        {"error.import.too_long.duration.headline": "Catalog says {duration}."},
    )
    monkeypatch.setattr(formatting, "i18n", language)

    copy = formatting.copy_for(errors.IMPORT_TOO_LONG, context={"duration_s": 1_872.0})

    assert copy.headline == "Catalog says 31:12."


def test_a_dynamic_headline_at_the_cap_falls_back_to_the_catalog_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    language = _catalog_translator(
        tmp_path,
        {
            "error.import.too_long.headline": "Catalog base headline.",
            "error.import.too_long.action": "Catalog base action.",
            "error.import.too_long.duration.headline": (
                "This track is {duration} long, which is far beyond the thirty-minute "
                "limit this build allows for a local import."
            ),
        },
    )
    monkeypatch.setattr(formatting, "i18n", language)

    copy = formatting.copy_for(errors.IMPORT_TOO_LONG, context={"duration_s": 1_872.0})

    assert len(copy.headline) <= formatting.MAX_HEADLINE_LENGTH
    assert (copy.headline, copy.action) == ("Catalog base headline.", "Catalog base action.")


def test_dynamic_copy_falls_back_when_context_is_missing() -> None:
    """A Job recorded by an older runner still gets the generic sentence."""
    assert (
        formatting.copy_for(errors.IMPORT_TOO_LONG).headline
        == "This track is too long. The limit is 30:00."
    )
    assert (
        formatting.copy_for(errors.EXPORT_WRITE_FAILED, "permission").headline
        == "Couldn't save the file."
    )
    assert (
        formatting.copy_for(errors.STORAGE_DISK_FULL, "export").headline == "Not enough disk space."
    )


def test_a_log_folder_failure_is_not_reported_as_a_licence_failure() -> None:
    copy = formatting.copy_for(errors.LOGGING_OPEN_FAILED)

    assert copy.headline == "Couldn't open the log folder."
    assert "logs folder" in copy.action


def test_the_level_adjusted_badge_is_quiet_and_exact() -> None:
    assert formatting.level_adjusted_label(0.0) is None
    assert formatting.level_adjusted_label(-1.5) == "level adjusted \u22121.5 dB"
    assert formatting.level_adjusted_label(-6.0) == "level adjusted \u22126.0 dB"


def test_save_original_copy_uses_the_glossary_terms() -> None:
    copy = " ".join(
        i18n.t(key)
        for key in (
            "export.imported_file",
            "export.imported_file_unchanged",
            "export.save_original_action",
            "page.save_original_cancelled",
            "page.save_original_saved_to",
            "page.save_original_extension_changed",
            "export.choose_location",
        )
    ).lower()

    assert "save original" in copy
    assert "imported file" in copy
    for avoided in ("passthrough", "native export", "raw export"):
        assert avoided not in copy


def test_a_job_stage_prefers_its_code_and_falls_back_to_the_english_text() -> None:
    coded = SimpleNamespace(
        stage=CodedText("Decoding", code="job.stage.import.decoding", context={"part": 1}),
        detail=CodedText(
            "song.wav",
            code="job.detail.import.native_file",
            context={"name": "song.wav"},
        ),
    )
    unknown = SimpleNamespace(
        stage=CodedText("Future stage", code="job.stage.future.unknown"),
        detail=CodedText("future detail", code="job.detail.future.unknown"),
    )

    assert formatting.job_stage(coded) == "Decoding"
    assert formatting.job_detail(coded) == "song.wav"
    assert formatting.job_stage(unknown) == "Future stage"
    assert formatting.job_detail(unknown) == "future detail"


def test_display_labels_resolve_roles_shifts_and_batch_labels() -> None:
    assert formatting.label_text(LabelRef(key="role.vocals")) == "Vocals"
    assert formatting.display_label("role.vocals") == "Vocals"
    assert formatting.display_label("common.original") == "Original"
    assert (
        formatting.display_label(
            "pitch.render.master",
            {"semitones": 3},
        )
        == "+3 master"
    )
    assert (
        formatting.display_label(
            "export.target.shifted",
            {"role": "vocals", "semitones": -2},
        )
        == "Vocals \u22122 st"
    )
    assert formatting.display_label("role.no_such_role", fallback="Banjo") == "Banjo"


def test_an_unknown_role_returns_its_stable_code_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="stemforge.ui.formatting"):
        label = formatting.role_label("no_such_role")

    assert label == "no_such_role"
    assert any("no_such_role" in record.getMessage() for record in caplog.records)


def test_batch_confirm_and_remaining_are_singular_at_one() -> None:
    assert formatting.export_batch_confirm(5) == "Export 5 files"
    assert formatting.export_batch_confirm(1) == "Export 1 file"
    assert formatting.export_batch_remaining(4) == "Export remaining 4"
    assert formatting.export_batch_remaining(1) == "Export remaining 1"


def test_batch_headline_reads_the_per_item_counts() -> None:
    destination = Path("/music/exports")

    assert (
        formatting.export_batch_headline(
            written=5,
            failed=0,
            not_exported=0,
            cancelled=False,
            destination=destination,
        )
        == f"Exported 5 files to {destination}"
    )
    assert (
        formatting.export_batch_headline(
            written=1,
            failed=0,
            not_exported=0,
            cancelled=False,
            destination=destination,
        )
        == f"Exported 1 file to {destination}"
    )
    assert (
        formatting.export_batch_headline(
            written=3,
            failed=2,
            not_exported=0,
            cancelled=False,
            destination=destination,
        )
        == "3 of 5 files exported; 2 failed"
    )
    assert (
        formatting.export_batch_headline(
            written=1,
            failed=1,
            not_exported=3,
            cancelled=False,
            destination=destination,
        )
        == "1 of 5 files exported; 4 remaining"
    )
    assert (
        formatting.export_batch_headline(
            written=1,
            failed=0,
            not_exported=4,
            cancelled=True,
            destination=destination,
        )
        == "Export cancelled — 1 of 5 files written"
    )
