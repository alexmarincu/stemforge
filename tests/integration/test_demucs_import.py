"""Integration (slow): the CPU torch + Demucs API import gate.

Ticket 11 §4: the released ``demucs==4.1.0`` omits numpy on Linux and Windows, so
``from demucs.api import Separator`` fails on a clean install. This test catches
that release blocker in seconds, without weights and without network. Imports
are inside the test function on purpose: importing torch at module scope would
run on collection and would make a lightweight environment fail the whole file.
"""

from __future__ import annotations

import importlib.util

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.slow]


def test_demucs_api_imports_on_the_pinned_cpu_stack() -> None:
    import demucs
    import numpy
    import torch
    from demucs.api import Separator

    assert torch.__version__.endswith("+cpu"), torch.__version__
    assert demucs.__version__ == "4.1.0"
    assert numpy.__version__.startswith("2.")
    assert callable(Separator)
    assert importlib.util.find_spec("torchaudio") is None, (
        "torchaudio is not part of the v1 bundle; do not add it as a dependency"
    )


def test_no_weights_are_committed() -> None:
    """ADR 0009: weights are downloaded, never committed or bundled."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for directory in ("src", "packaging", "tests"):
        assert not list((root / directory).rglob("*.safetensors")), directory
