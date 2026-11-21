"""Pure pitch math: range validation, frequency helpers, render keys, gain.

No external binaries: this module pins the arithmetic and the identity rules that
the engine and the Job compose (ticket 05 §4-§5, ticket 10).
"""

from __future__ import annotations

import hashlib
import math

import pytest

from stemforge.domain.models import RenderScope
from stemforge.engines.pitch import (
    MAX_SEMITONES,
    MIN_SEMITONES,
    render_key_for,
    semitone_ratio,
    shifted_frequency,
    validate_semitones,
)
from stemforge.pipeline.gain import PEAK_TARGET_DBFS, max_peak_dbfs, presentation_gain

# --- Range validation ------------------------------------------------------


@pytest.mark.unit
def test_semitone_range_includes_the_ticket_bounds() -> None:
    assert MIN_SEMITONES == -12
    assert MAX_SEMITONES == 12
    for semitones in range(MIN_SEMITONES, MAX_SEMITONES + 1):
        if semitones == 0:
            continue
        assert validate_semitones(semitones) == semitones


@pytest.mark.unit
@pytest.mark.parametrize("semitones", [-13, 13, -100, 100])
def test_semitone_range_rejects_out_of_range_values(semitones: int) -> None:
    with pytest.raises(ValueError, match="between -12 and 12"):
        validate_semitones(semitones)


@pytest.mark.unit
def test_zero_semitones_is_not_a_render() -> None:
    # Ticket 08 §5: the UI disables Render at 0; the Original is the 0 view.
    with pytest.raises(ValueError, match="0"):
        validate_semitones(0)


@pytest.mark.unit
@pytest.mark.parametrize("value", [1.5, "3", None, True, False])
def test_semitone_range_rejects_non_integers(value: object) -> None:
    with pytest.raises(ValueError, match="integer"):
        validate_semitones(value)  # type: ignore[arg-type]


# --- Frequency helpers ------------------------------------------------------


@pytest.mark.unit
def test_semitone_ratio_matches_equal_temperament() -> None:
    assert semitone_ratio(0) == pytest.approx(1.0)
    assert semitone_ratio(12) == pytest.approx(2.0)
    assert semitone_ratio(-12) == pytest.approx(0.5)
    assert semitone_ratio(7) == pytest.approx(1.4983070768766815)


@pytest.mark.unit
def test_shifted_frequency_scales_by_the_semitone_ratio() -> None:
    assert shifted_frequency(440.0, 0) == pytest.approx(440.0)
    assert shifted_frequency(440.0, 12) == pytest.approx(880.0)
    assert shifted_frequency(440.0, -12) == pytest.approx(220.0)
    assert shifted_frequency(440.0, 3) == pytest.approx(523.2511306011972)


@pytest.mark.unit
@pytest.mark.parametrize("frequency", [0.0, -1.0, math.inf, math.nan])
def test_shifted_frequency_rejects_non_positive_or_non_finite_input(frequency: float) -> None:
    with pytest.raises(ValueError, match="positive and finite"):
        shifted_frequency(frequency, 3)


@pytest.mark.unit
def test_measurement_round_trips_through_the_inverse() -> None:
    measured = shifted_frequency(440.0, -7)

    semitones = 12 * math.log2(measured / 440.0)

    assert semitones == pytest.approx(-7.0)


# --- Render key ------------------------------------------------------------

HASH_A = hashlib.sha256(b"a").hexdigest()
HASH_B = hashlib.sha256(b"b").hexdigest()


def _key(**overrides: object) -> str:
    arguments: dict[str, object] = {
        "source_id": "abc123",
        "scope": RenderScope.ORIGINAL,
        "semitones": 3,
        "input_hashes": [HASH_A],
        "engine_version": "4.0.0",
    }
    arguments.update(overrides)
    return render_key_for(**arguments)  # type: ignore[arg-type]


@pytest.mark.unit
def test_render_key_is_a_stable_64_hex_digest() -> None:
    first = _key()
    second = _key()

    assert first == second
    assert len(first) == 64
    int(first, 16)


@pytest.mark.unit
def test_render_key_separates_scope() -> None:
    assert _key(scope=RenderScope.ORIGINAL) != _key(scope=RenderScope.STEMS)


@pytest.mark.unit
def test_render_key_separates_semitones() -> None:
    assert _key(semitones=3) != _key(semitones=-3)


@pytest.mark.unit
def test_render_key_separates_engine_version_and_preset() -> None:
    assert _key(engine_version="4.0.0") != _key(engine_version="4.1.0")
    assert _key(formant=True) != _key(formant=False)
    assert _key(window="default") != _key(window="short")


@pytest.mark.unit
def test_render_key_separates_inputs_and_ignores_their_order() -> None:
    single = _key(input_hashes=[HASH_A])
    pair = _key(input_hashes=[HASH_A, HASH_B])
    reversed_pair = _key(input_hashes=[HASH_B, HASH_A])

    assert single != pair
    assert pair == reversed_pair


@pytest.mark.unit
def test_render_key_requires_a_semitone_and_at_least_one_input() -> None:
    with pytest.raises(ValueError, match="0"):
        _key(semitones=0)
    with pytest.raises(ValueError, match="hash"):
        _key(input_hashes=[])


# --- Presentation gain -----------------------------------------------------


@pytest.mark.unit
def test_a_set_below_full_scale_gets_no_gain() -> None:
    assert presentation_gain([]) == 0.0
    assert presentation_gain([float("-inf")]) == 0.0
    assert presentation_gain([-12.0, -0.25]) == 0.0
    assert presentation_gain([0.0]) == 0.0


@pytest.mark.unit
def test_a_peak_above_full_scale_brings_the_whole_set_to_minus_one_dbfs() -> None:
    assert presentation_gain([3.0]) == PEAK_TARGET_DBFS - 3.0
    assert presentation_gain([1.5, 3.0]) == PEAK_TARGET_DBFS - 3.0
    assert presentation_gain([3.0, 1.5]) == PEAK_TARGET_DBFS - 3.0
    assert PEAK_TARGET_DBFS == -1.0


@pytest.mark.unit
def test_gain_never_leaves_a_peak_above_full_scale() -> None:
    for peak in (0.1, 1.0, 2.5, 12.0):
        gain = presentation_gain([peak])
        assert peak + gain <= 0.0 + 1e-9
        if peak > 0.0:
            assert peak + gain == pytest.approx(PEAK_TARGET_DBFS)


@pytest.mark.unit
def test_gain_rejects_a_nan_peak() -> None:
    with pytest.raises(ValueError, match="NaN"):
        presentation_gain([math.nan])


@pytest.mark.unit
def test_max_peak_returns_none_for_missing_or_silent_sets() -> None:
    assert max_peak_dbfs([]) is None
    assert max_peak_dbfs([float("-inf")]) is None
    assert max_peak_dbfs([float("-inf"), -12.0, -0.5]) == -0.5


@pytest.mark.unit
def test_max_peak_rejects_a_nan_instead_of_skipping_it() -> None:
    with pytest.raises(ValueError, match="NaN"):
        max_peak_dbfs([-3.0, math.nan])
