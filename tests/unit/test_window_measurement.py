"""The measurement harness: parsing, classification, and gate exclusion.

The harness itself needs a live X11 session, so these tests cover only the
helpers that decide what the numbers mean.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from scripts import measure_window_rendering as measure

from stemforge.integration.window import WEBKIT_SHM_ENV

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECK = REPO_ROOT / "scripts" / "check.sh"


def test_sizes_resolve_against_the_constants_and_the_screen() -> None:
    default = (1280, 820)
    screen = (3640, 1920)

    assert measure.parse_size("default", default=default, screen=screen) == default
    assert measure.parse_size("maximized", default=default, screen=screen) == screen
    assert measure.parse_size("1920x1080", default=default, screen=screen) == (1920, 1080)


@pytest.mark.parametrize("text", ["", "1920", "1920x", "x1080", "wide", "0x800", "800x0"])
def test_an_unusable_size_is_rejected(text: str) -> None:
    with pytest.raises(measure.MeasurementError):
        measure.parse_size(text, default=(1280, 820), screen=(3640, 1920))


def test_proc_stat_survives_a_command_name_with_spaces_and_parentheses() -> None:
    line = "123 (a (weird) comm) S 1 1 1 0 -1 4194560 100 0 0 0 42 7 0 0 20 0 1 0"

    assert measure.parse_proc_stat(line) == (42, 7)


def test_cpu_percent_is_percent_of_one_core() -> None:
    ticks = os.sysconf("SC_CLK_TCK")

    assert measure.cpu_percent((0, 0), (ticks, 0), 1.0) == pytest.approx(100.0)
    assert measure.cpu_percent((0, 0), (ticks, ticks), 2.0) == pytest.approx(100.0)


def test_a_restarted_process_does_not_produce_negative_cpu() -> None:
    ticks = os.sysconf("SC_CLK_TCK")

    assert measure.cpu_percent((ticks, ticks), (0, 0), 1.0) == 0.0


def test_geometry_reads_the_shell_form() -> None:
    output = "WINDOW=83886092\nX=2458\nY=548\nWIDTH=1280\nHEIGHT=820\nSCREEN=0\n"

    assert measure.parse_geometry(output) == {
        "WINDOW": 83886092,
        "X": 2458,
        "Y": 548,
        "WIDTH": 1280,
        "HEIGHT": 820,
        "SCREEN": 0,
    }


def test_geometry_requires_a_size() -> None:
    with pytest.raises(measure.MeasurementError):
        measure.parse_geometry("WINDOW=1\nX=0\nY=0\n")


def _ppm(width: int, height: int, rgb: bytes) -> bytes:
    return f"P6\n{width} {height}\n255\n".encode() + rgb


def test_ppm_roundtrip_accepts_a_comment() -> None:
    data = b"P6\n# written by import\n2 1\n255\n" + bytes([1, 2, 3, 4, 5, 6])

    width, height, rgb = measure.parse_ppm(data)

    assert (width, height) == (2, 1)
    assert rgb == bytes([1, 2, 3, 4, 5, 6])


def test_ppm_rejects_other_formats_and_depths() -> None:
    with pytest.raises(measure.MeasurementError):
        measure.parse_ppm(b"P3\n1 1\n255\n0 0 0\n")
    with pytest.raises(measure.MeasurementError):
        measure.parse_ppm(b"P6\n1 1\n65535\n" + bytes(6))


def test_white_fraction_counts_only_near_white_pixels() -> None:
    assert measure.white_fraction(bytes([255, 255, 255])) == 1.0
    assert measure.white_fraction(bytes([0, 0, 0])) == 0.0
    # One channel below the floor is not white.
    assert measure.white_fraction(bytes([255, 255, 239])) == 0.0


def test_a_blank_web_view_is_classified_white() -> None:
    pixels = bytes([255, 255, 255] * 100)

    capture = measure.classify_ppm(_ppm(10, 10, pixels))

    assert capture.white is True
    assert capture.white_fraction == 1.0


def test_the_dark_page_is_not_classified_white() -> None:
    pixels = bytes([18, 19, 24] * 100)

    capture = measure.classify_ppm(_ppm(10, 10, pixels))

    assert capture.white is False


def test_nvidia_smi_rows_become_a_mean() -> None:
    samples = measure.parse_nvidia_smi("47, 470\n53, 490\n")

    summary = measure.summarize_gpu(samples)

    assert summary.samples == 2
    assert summary.util_percent == pytest.approx(50.0)
    assert summary.used_mib == pytest.approx(480.0)


def test_missing_gpu_samples_are_not_reported_as_zero() -> None:
    summary = measure.summarize_gpu([])

    assert summary.samples == 0
    assert summary.util_percent is None
    assert summary.used_mib is None


def test_every_condition_sets_the_environment_it_claims() -> None:
    assert set(measure.CONDITIONS) == set(measure.CONDITION_ORDER)
    assert measure.CONDITIONS["default"] == {}
    assert measure.CONDITIONS["shm-0"] == {WEBKIT_SHM_ENV: "0"}
    assert measure.CONDITIONS["disable-dmabuf"] == {"WEBKIT_DISABLE_DMABUF_RENDERER": "1"}
    assert measure.CONDITIONS["disable-dmabuf-only"] == {
        WEBKIT_SHM_ENV: "0",
        "WEBKIT_DISABLE_DMABUF_RENDERER": "1",
    }
    assert measure.CONDITIONS["browser"]["STEMFORGE_BROWSER"] == "1"


def test_the_matrix_row_has_the_header_column_count() -> None:
    cell = measure.Cell(
        condition="default",
        size="default",
        requested=(1280, 820),
        achieved=(1280, 820),
        idle_cpu={"stemforge": 1.0, "webkit": 2.0},
        scroll_cpu={"stemforge": 3.0, "webkit": 4.0},
        resize_cpu={"stemforge": 5.0, "webkit": 6.0},
        gpu_idle=measure.Gpu(util_percent=47.0, used_mib=470.0, samples=4),
        gpu_scroll=measure.Gpu(util_percent=None, used_mib=None, samples=0),
        capture=measure.Capture(width=1280, height=820, white_fraction=0.0, white=False),
        screenshot=None,
        note="",
    )

    header = measure.matrix_header().splitlines()[0]
    row = measure.format_cell_row(cell)

    assert header.count("|") == row.count("|")
    assert "| default | default | 1280x820 | 1280x820 | 1.0 | 2.0 | 3.0 | 4.0 | 6.0 |" in row


def test_the_harness_stays_out_of_the_default_gate() -> None:
    """A live GPU and X11 session cannot be a push requirement."""
    gate = CHECK.read_text(encoding="utf-8")
    assert "measure_window_rendering" not in gate
    assert "measure_window_latency" not in gate
