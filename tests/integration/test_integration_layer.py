"""Integration-layer placeholder until real-binary tests arrive in ticket 2."""

from __future__ import annotations

import importlib

import pytest

pytestmark = pytest.mark.integration


def test_integration_layer_is_importable() -> None:
    package = importlib.import_module("stemforge.integration")
    assert package.__doc__
