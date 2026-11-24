"""The visual root: theme palette, shared CSS tokens, compact controls, and brand assets."""

from __future__ import annotations

import re

import pytest

from stemforge.ui import page, theme

pytestmark = pytest.mark.unit


def _rule(css: str, selector: str) -> str:
    match = re.search(rf"^{re.escape(selector)}\s*\{{([^}}]*)\}}", css, re.MULTILINE)
    assert match is not None, f"missing rule for {selector}"
    return match.group(1)


def _srgb_channel(value: int) -> float:
    channel = value / 255
    return channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4


def _luminance(colour: str) -> float:
    """WCAG relative luminance for a six-digit hex token."""
    match = re.fullmatch(r"#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})", colour)
    assert match is not None, f"{colour} is not a six-digit hex colour"
    red, green, blue = (int(part, 16) for part in match.groups())
    return (
        0.2126 * _srgb_channel(red) + 0.7152 * _srgb_channel(green) + 0.0722 * _srgb_channel(blue)
    )


def _contrast_ratio(first: str, second: str) -> float:
    lighter, darker = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


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


def test_dim_reaches_wcag_aa_on_every_shell_surface() -> None:
    """``--dim`` is normal-size text, so each surface it sits on must clear AA.

    The token carries metadata and fineprint at 9-11.5 px on the canvas, the
    rail, cards, selected rows, and wells; a single failing pair reintroduces
    the accessibility defect the palette is meant to avoid.
    """
    for surface in ("BG", "RAIL", "PANEL", "PANEL2", "WELL"):
        ratio = _contrast_ratio(theme.DIM, getattr(theme, surface))
        assert ratio >= 4.5, f"--dim on {surface}: {ratio:.2f}:1"


def test_dim_stays_the_third_text_level() -> None:
    """Text outranks muted, which outranks dim, so the hierarchy reads in order."""
    assert _luminance(theme.TEXT) > _luminance(theme.MUTED) > _luminance(theme.DIM)


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


def test_dialog_cards_reset_quasar_elevation() -> None:
    """``q-card`` carries a Quasar shadow; an ``sf-card`` dialog must stay flat.

    The reset is scoped to the dialog card so the shell's ``section.sf-card``
    surfaces keep their border-only rule.
    """
    dialog = _rule(page.SHELL_CSS, ".q-card.sf-card")
    assert "box-shadow: none" in dialog
    assert "box-shadow" not in _rule(page.SHELL_CSS, ".sf-card")


def test_output_row_css_has_no_dormant_active_state() -> None:
    """``is-playing`` is the only row highlight; the version tab owns the rest."""
    assert ".sf-output-row.is-playing" in page.SHELL_CSS
    assert ".sf-output-row.is-unavailable" in page.SHELL_CSS
    assert ".sf-output-row.is-active" not in page.SHELL_CSS


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
