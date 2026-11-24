"""The Source stereo-layout vocabulary and its imported-file classification."""

from __future__ import annotations

import pytest

from stemforge.domain.models import (
    OriginKind,
    Source,
    SourceMetadata,
    StereoLayout,
    parse_stereo_layout,
)
from stemforge.engines.media import DUAL_MONO_SIDE_PEAK_DBFS, classify_stereo_layout

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("original_channels", "side_peak_dbfs", "expected"),
    [
        (1, float("-inf"), StereoLayout.MONO),
        (1, -3.0, StereoLayout.MONO),
        (2, float("-inf"), StereoLayout.DUAL_MONO),
        (2, DUAL_MONO_SIDE_PEAK_DBFS, StereoLayout.DUAL_MONO),
        (2, DUAL_MONO_SIDE_PEAK_DBFS + 0.1, StereoLayout.STEREO),
        (2, -6.0, StereoLayout.STEREO),
        (6, float("-inf"), StereoLayout.DUAL_MONO),
        (6, -20.0, StereoLayout.STEREO),
    ],
)
def test_classify_stereo_layout_uses_channels_then_the_side_signal(
    original_channels: int,
    side_peak_dbfs: float,
    expected: StereoLayout,
) -> None:
    assert (
        classify_stereo_layout(original_channels=original_channels, side_peak_dbfs=side_peak_dbfs)
        is expected
    )


def test_a_legacy_source_reads_back_with_an_unknown_layout() -> None:
    source = Source(
        source_id="src-legacy",
        origin="/music/old.wav",
        origin_kind=OriginKind.FILE,
        metadata=SourceMetadata(title="Old"),
    )

    assert source.original_channels is None
    assert source.stereo_layout is None


@pytest.mark.parametrize("value", [None, "", "surround", 2, True])
def test_parse_stereo_layout_treats_a_missing_or_unknown_value_as_unknown(
    value: object,
) -> None:
    assert parse_stereo_layout(value) is None


@pytest.mark.parametrize("layout", list(StereoLayout))
def test_parse_stereo_layout_round_trips_the_recorded_value(layout: StereoLayout) -> None:
    assert parse_stereo_layout(layout.value) is layout
    assert parse_stereo_layout(layout) is layout
