# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""The on-canvas navigation cluster: parenting, placement, and wiring."""

from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QSize
from PyQt6.QtGui import QColor, QIcon, QKeySequence, QPalette, QResizeEvent

from ncexplorer_toolkit.gui.nav_overlay import ICON_SIZE, MARGIN, NavOverlay
from ncexplorer_toolkit.resources.icons import clear_icon_cache


@pytest.fixture
def overlay(canvas):
    window = SimpleNamespace(current_layer=None)
    widget = NavOverlay(canvas, window)
    canvas.attach_nav_overlay(widget)
    return widget


def test_overlay_is_a_child_of_the_canvas(overlay, canvas):
    assert overlay.parent() is canvas


def test_overlay_is_smaller_than_the_canvas(overlay, canvas):
    # It must not blanket the map, or matplotlib stops seeing mouse events.
    assert overlay.width() < canvas.width() / 2
    assert overlay.height() < canvas.height()


@pytest.mark.parametrize("size", [(900, 600), (500, 700)])
def test_overlay_sticks_to_the_bottom_right_on_resize(overlay, canvas, size):
    old = canvas.size()
    canvas.resize(*size)
    canvas.resizeEvent(QResizeEvent(QSize(*size), old))

    rect = overlay.geometry()
    # right()/bottom() are the last pixel inside the rect, hence MARGIN + 1.
    assert canvas.width() - rect.right() == pytest.approx(MARGIN + 1, abs=2)
    assert canvas.height() - rect.bottom() == pytest.approx(MARGIN + 1, abs=2)


def test_zoom_to_layer_button_disabled_without_active_layer(overlay):
    assert overlay.main_window.current_layer is None
    assert not overlay.zoom_layer_btn.isEnabled()


def test_zoom_to_layer_button_enabled_with_active_layer(overlay):
    overlay.main_window.current_layer = "some_layer"
    overlay.refresh_state()
    assert overlay.zoom_layer_btn.isEnabled()


def test_zoom_buttons_change_the_extent(overlay, canvas):
    before = list(canvas.extent)
    overlay.zoom_in_btn.click()
    zoomed = list(canvas.extent)
    assert zoomed[1] - zoomed[0] < before[1] - before[0]

    overlay.zoom_out_btn.click()
    assert canvas.extent[1] - canvas.extent[0] > zoomed[1] - zoomed[0]


def test_full_extent_button_restores_the_globe(overlay, canvas):
    canvas.zoom_in(0.4)
    overlay.full_extent_btn.click()
    assert canvas.extent == pytest.approx([-180, 180, -90, 90])


def test_previous_button_restores_the_previous_extent(overlay, canvas):
    canvas.set_extent([-100, -20, 0, 40])
    zoomed = list(canvas.extent)

    overlay.previous_btn.click()

    assert canvas.extent != pytest.approx(zoomed)


def test_previous_button_disabled_with_empty_history(overlay, canvas):
    canvas.zoom_history.clear()
    overlay.refresh_state()
    assert not overlay.previous_btn.isEnabled()


def test_zoom_out_button_disabled_at_full_extent(overlay, canvas):
    canvas.zoom_full_extent()
    overlay.refresh_state()
    assert not overlay.zoom_out_btn.isEnabled()


def test_every_button_has_a_rendered_icon(overlay):
    for button in (
        overlay.zoom_in_btn,
        overlay.zoom_out_btn,
        overlay.full_extent_btn,
        overlay.zoom_layer_btn,
        overlay.previous_btn,
    ):
        assert not button.icon().isNull()
        assert not button.icon().pixmap(ICON_SIZE, ICON_SIZE).isNull()


def _painted_colors(icon):
    image = icon.pixmap(ICON_SIZE, ICON_SIZE).toImage()
    return [
        image.pixelColor(x, y)
        for y in range(image.height())
        for x in range(image.width())
        if image.pixelColor(x, y).alpha() > 40
    ]


def test_glyphs_stay_dark_under_a_dark_palette(qapp, canvas):
    """Regression: palette-coloured glyphs went white-on-white in dark mode.

    The panel keeps its own light background, so the glyphs must not follow the
    system palette the way the toolbar icons do.
    """
    original = qapp.palette()
    dark = QPalette(original)
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        dark.setColor(role, QColor("#f5f5f5"))

    try:
        qapp.setPalette(dark)
        clear_icon_cache()
        widget = NavOverlay(canvas, SimpleNamespace(current_layer=None))

        painted = _painted_colors(widget.zoom_in_btn.icon())
        assert painted, "the zoom-in glyph rendered nothing"
        assert min(c.lightness() for c in painted) < 110, (
            "glyphs are light-on-light under a dark palette"
        )
    finally:
        qapp.setPalette(original)
        clear_icon_cache()


def test_disabled_glyph_is_muted_but_present(overlay):
    """A disabled control should read as inactive, not as an empty button."""
    assert not overlay.zoom_layer_btn.isEnabled()

    icon = overlay.zoom_layer_btn.icon()
    enabled = _painted_colors(icon)
    disabled_pixmap = icon.pixmap(ICON_SIZE, ICON_SIZE, QIcon.Mode.Disabled)
    image = disabled_pixmap.toImage()
    disabled = [
        image.pixelColor(x, y)
        for y in range(image.height())
        for x in range(image.width())
        if image.pixelColor(x, y).alpha() > 40
    ]

    assert disabled, "the disabled glyph vanished entirely"
    assert min(c.lightness() for c in disabled) > min(c.lightness() for c in enabled)


def test_buttons_do_not_steal_keyboard_focus(overlay):
    from PyQt6.QtCore import Qt

    for button in (
        overlay.zoom_in_btn,
        overlay.zoom_out_btn,
        overlay.full_extent_btn,
        overlay.zoom_layer_btn,
        overlay.previous_btn,
    ):
        assert button.focusPolicy() == Qt.FocusPolicy.NoFocus


def test_tooltips_mention_the_shortcut(overlay):
    # Rendered in the platform's own notation (⌘ on macOS, Ctrl elsewhere).
    zoom_in_keys = QKeySequence("Ctrl+=").toString(
        QKeySequence.SequenceFormat.NativeText
    )
    assert zoom_in_keys in overlay.zoom_in_btn.toolTip()
    assert "Zoom in" in overlay.zoom_in_btn.toolTip()

    backspace = QKeySequence("Backspace").toString(
        QKeySequence.SequenceFormat.NativeText
    )
    assert backspace in overlay.previous_btn.toolTip()
