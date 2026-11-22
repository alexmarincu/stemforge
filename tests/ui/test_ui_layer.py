"""UI-layer placeholder until page tests arrive with the load flow."""

from __future__ import annotations

import importlib

import pytest

pytestmark = pytest.mark.ui


def test_ui_layer_is_importable() -> None:
    for name in ("stemforge.ui", "stemforge.ui.page", "stemforge.ui.formatting"):
        assert importlib.import_module(name) is not None


def test_component_package_is_importable() -> None:
    assert importlib.import_module("stemforge.ui.components") is not None
