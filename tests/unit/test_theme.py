"""The visual root: theme palette, shared CSS tokens, compact controls, and brand assets."""

from __future__ import annotations

import re

import pytest

from stemforge.ui import page, theme

pytestmark = pytest.mark.unit


def _rule(css: str, selector: str) -> str:
    match = re.search(rf"{re.escape(selector)}\s*\{{([^}}]*)\}}", css)
    assert match is not None, f"missing rule for {selector}"
    return match.group(1)


def test_quasar_palette_replaces_the_blue_defaults() -> None:
    assert theme.ACCENT == "#f2a65a"
    assert theme.INK == "#1d1204"
    assert theme.GOOD == "#5ec99a"
    assert theme.BAD == "#ef7070"
    assert theme.BG == "#0e1014"
    assert theme.QUASAR_COLORS["primary"] == theme.ACCENT
    assert theme.QUASAR_COLORS["positive"] == theme.GOOD
    assert theme.QUASAR_COLORS["negative"] == theme.BAD
    assert theme.QUASAR_COLORS["ink"] == theme.INK


def test_shell_colours_are_tokens_not_scattered_literals() -> None:
    # The tokens belong to theme; the shell rules must consume them, not restate them.
    shell_rules = page.SHELL_CSS.removeprefix(theme.TOKENS_CSS)
    assert shell_rules != page.SHELL_CSS
    assert "#" not in shell_rules
    assert "#f0a83a" not in page.SHELL_CSS
    for token in (
        "--bg",
        "--rail",
        "--panel",
        "--accent",
        "--text",
        "--muted",
        "--dim",
        "--bad-text",
        "--bad-action",
        "--bad-dim",
    ):
        assert f"{token}:" in theme.TOKENS_CSS


def test_compact_controls_share_one_box() -> None:
    tokens = theme.TOKENS_CSS
    for declaration in (
        "--control-h: 32px;",
        "--control-min-w: 44px;",
        "--control-pad-x: 12px;",
        "--control-icon: 32px;",
        "--control-primary-h: 36px;",
        "--control-primary-min-w: 120px;",
        "--control-primary-pad-x: 16px;",
        "--control-font: 11px;",
        "--control-primary-font: 12.5px;",
    ):
        assert declaration in tokens

    css = page.SHELL_CSS
    base = _rule(css, ".q-btn.sf-control")
    assert "height: var(--control-h)" in base
    assert "min-height: var(--control-h)" in base
    assert "min-width: var(--control-min-w)" in base
    assert "padding: 0 var(--control-pad-x)" in base
    assert "font-size: var(--control-font)" in base
    assert "px" not in base

    icon = _rule(css, ".q-btn.sf-control--icon")
    assert "width: var(--control-icon)" in icon
    assert "min-width: var(--control-icon)" in icon
    assert "height: var(--control-icon)" in icon
    assert "min-height: var(--control-icon)" in icon
    assert "px" not in icon

    primary = _rule(css, ".q-btn.sf-control--primary")
    assert "height: var(--control-primary-h)" in primary
    assert "min-height: var(--control-primary-h)" in primary
    assert "min-width: var(--control-primary-min-w)" in primary
    assert "padding: 0 var(--control-primary-pad-x)" in primary
    assert "font-size: var(--control-primary-font)" in primary
    assert "font-weight: 600" in primary
    assert "px" not in primary


def test_brand_assets_are_available_as_data_urls() -> None:
    logo = theme.asset_data_url(theme.LOGO_SVG)
    about = theme.asset_data_url(theme.ABOUT_PNG)
    assert logo is not None
    assert logo.startswith("data:image/svg+xml;base64,")
    assert about is not None
    assert about.startswith("data:image/png;base64,")
    html = theme.image_html(theme.LOGO_SVG, size=28, css_class="sf-brand-mark")
    assert html is not None
    assert html.startswith('<img src="data:image/svg+xml;base64,')
    assert 'class="sf-brand-mark"' in html
