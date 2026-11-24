"""The latency probe's geometry: it must measure the row, not a row control.

The probe itself needs a live X11 session, so these tests cover only the
coordinates and patches that decide what the reported number means.
"""

from __future__ import annotations

import pytest
from scripts import measure_window_latency as latency

pytestmark = pytest.mark.unit


def test_the_hover_point_sits_in_the_row_body() -> None:
    # The 292 px rail row is 275 px wide: the icon tile starts at 16, the title
    # column at 51, and the play and remove controls at 218 and 251. A probe on
    # a control measures a Quasar button hover, not the row.
    assert latency.ROW_HOVER_X > 51
    assert latency.ROW_HOVER_X < 218


def test_the_background_patch_sits_in_the_row_padding() -> None:
    centre = latency.ROW_CENTRES[0]
    area = latency.probe_areas(centre)["background"]

    assert (area.left, area.width) == (latency.ROW_BACKGROUND_LEFT, latency.ROW_BACKGROUND_WIDTH)
    assert area.left >= 8
    assert area.left + area.width <= 15
    assert area.height == latency.ROW_BACKGROUND_HEIGHT
    assert area.top == centre - area.height // 2


def test_the_icon_patch_covers_the_tile_and_stays_clear_of_the_controls() -> None:
    area = latency.probe_areas(latency.ROW_CENTRES[0])["icon"]

    assert area.left >= 16
    assert area.left + area.width <= 42
    assert area.width == latency.ROW_ICON_SIZE
    assert area.height == latency.ROW_ICON_SIZE


def test_the_detail_title_patch_sits_on_the_title_line() -> None:
    # The detail pane starts at x=320, y=67.8; its title is 306x28.5.
    area = latency.DETAIL_TITLE

    assert area.left >= 320
    assert area.left + area.width <= 626
    assert area.top >= 68
    assert area.top + area.height <= 96


def test_the_resting_point_is_the_rail_padding() -> None:
    # The rows start at x=8; a resting point in the rail's left padding cannot
    # hover a row or leave a button's fade running during the next trial.
    assert latency.AWAY_X < 8
    assert latency.away_point((1280, 820)) == (latency.AWAY_X, 410)


def test_the_import_probe_samples_the_button_not_the_rail() -> None:
    area = latency.IMPORT_BACKGROUND

    assert area.left >= 12
    assert area.left + area.width <= 279
    assert area.top >= 68
    assert area.top + area.height <= 104
