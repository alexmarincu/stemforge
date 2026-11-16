"""Pure ``Range`` header parsing for the single ``/media`` route (ticket 09).

The accepted set is deliberately the ticket-01 measurement: one open-ended or
bounded byte range. Suffix, multi-range, malformed, and unsatisfiable requests
are all rejected with ``416`` by the route, so the parser raises one exception.
"""

from __future__ import annotations

import pytest

from stemforge.integration.media import ByteRange, RangeNotSatisfiable, parse_range_header

pytestmark = pytest.mark.unit


def test_no_range_header_asks_for_the_whole_file() -> None:
    assert parse_range_header(None, 10) is None


def test_open_ended_range_runs_to_the_last_byte() -> None:
    assert parse_range_header("bytes=4-", 10) == ByteRange(4, 9)


def test_bounded_range_returns_the_exact_byte_interval() -> None:
    assert parse_range_header("bytes=2-5", 10) == ByteRange(2, 5)


def test_a_single_byte_range_is_valid() -> None:
    assert parse_range_header("bytes=0-0", 10) == ByteRange(0, 0)


def test_an_end_past_eof_is_clamped_to_the_last_byte() -> None:
    assert parse_range_header("bytes=8-999999", 10) == ByteRange(8, 9)


def test_surrounding_and_internal_whitespace_is_tolerated() -> None:
    assert parse_range_header("  bytes = 2 - 5  ", 10) == ByteRange(2, 5)


def test_the_unit_is_case_insensitive() -> None:
    assert parse_range_header("Bytes=2-5", 10) == ByteRange(2, 5)


@pytest.mark.parametrize(
    "header",
    [
        "bytes=-1",
        "bytes=-500",
        "bytes=0-1,3-4",
        "bytes=10-",
        "bytes=11-12",
        "bytes=5-2",
        "items=0-1",
        "bytes=abc-def",
        "bytes=",
        "bytes=-",
        "",
        "   ",
    ],
)
def test_unsupported_or_unsatisfiable_ranges_are_rejected(header: str) -> None:
    with pytest.raises(RangeNotSatisfiable):
        parse_range_header(header, 10)


def test_any_range_on_an_empty_file_is_unsatisfiable() -> None:
    with pytest.raises(RangeNotSatisfiable):
        parse_range_header("bytes=0-", 0)


def test_an_absurdly_long_number_is_rejected_without_an_integer_error() -> None:
    with pytest.raises(RangeNotSatisfiable):
        parse_range_header("bytes=" + "9" * 5000 + "-", 10)
