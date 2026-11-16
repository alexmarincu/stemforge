"""UI presentation helpers: durations, audio facts, cache sizes, and recency."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from stemforge import errors
from stemforge.ui import formatting

pytestmark = pytest.mark.unit


def test_duration_is_m_ss_and_handles_hours() -> None:
    assert formatting.format_duration(187.4) == "3:07"
    assert formatting.format_duration(3.0) == "0:03"
    assert formatting.format_duration(3_725) == "1:02:05"
    assert formatting.format_duration(None) == "—"
    assert formatting.format_duration(-1) == "—"


def test_audio_facts_read_like_the_prototype() -> None:
    assert formatting.format_sample_rate(44_100) == "44.1 kHz"
    assert formatting.format_sample_rate(48_000) == "48 kHz"
    assert formatting.format_sample_rate(None) is None
    assert formatting.format_channels(1) == "mono"
    assert formatting.format_channels(2) == "stereo"
    assert formatting.format_channels(6) == "6 ch"
    assert formatting.format_channels(None) is None


def test_cache_sizes_use_one_decimal_and_the_ticket_units() -> None:
    assert formatting.format_bytes(3_650_000_000) == "3.4 GB"
    assert formatting.format_bytes(120_000_000) == "114.4 MB"
    assert formatting.format_bytes(0) == "0.0 MB"


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
