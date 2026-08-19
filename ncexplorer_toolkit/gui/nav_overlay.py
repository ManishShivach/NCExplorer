# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""Floating navigation cluster drawn over the bottom-right of the map.

The overlay is a plain child widget of the GeoCanvas rather than something in a
layout: it must sit *on* the map without taking space from it, and it must stay
small so matplotlib keeps receiving pan/zoom/click events everywhere else.

Every button calls straight into the canvas API — no geometry is reimplemented
here — so the buttons and the keyboard shortcuts always agree.
"""

import logging

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import QFrame, QLabel, QToolButton, QVBoxLayout, QWidget

from ..resources.icons import load_icon
from .shortcuts import display_keys, spec_by_id

logger = logging.getLogger(__name__)

MARGIN = 12
BUTTON_SIZE = 30
ICON_SIZE = 19

# The panel keeps its own light colours because it floats over the map, which is
# light whatever the system theme is. The glyphs must therefore be fixed too:
# following the palette turns them white-on-white under a dark theme.
GLYPH_COLOR = "#16222e"
GLYPH_COLOR_DISABLED = "#a7b2bd"

STYLESHEET = """
NavOverlay {
    background-color: rgba(253, 253, 254, 240);
    border: 1px solid rgba(22, 34, 46, 75);
    border-radius: 7px;
}
NavOverlay QToolButton {
    border: none;
    border-radius: 5px;
    background: transparent;
    color: #16222e;
    font-size: 13px;
}
NavOverlay QToolButton:hover:enabled {
    background-color: rgba(22, 34, 46, 34);
}
NavOverlay QToolButton:pressed:enabled {
    background-color: rgba(22, 34, 46, 64);
}
NavOverlay QToolButton:disabled {
    color: #a7b2bd;
}
NavOverlay QLabel#northLabel {
    color: #16222e;
    font-size: 11px;
    font-weight: bold;
}
NavOverlay QFrame#navSeparator {
    background-color: rgba(22, 34, 46, 80);
    border: none;
}
"""


class NavOverlay(QWidget):
    """Zoom / extent controls plus a north indicator, floating over the canvas."""

    def __init__(self, canvas, main_window=None):
        super().__init__(canvas)
        self.canvas = canvas
        self.main_window = main_window

        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        # Without WA_StyledBackground a QWidget subclass ignores the background
        # and border in its own stylesheet, leaving the buttons floating loose
        # over the map.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(STYLESHEET)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        # The map is always PlateCarree, so north is a static label, not a control.
        north = QLabel("N▲")
        north.setObjectName("northLabel")
        north.setAlignment(Qt.AlignmentFlag.AlignCenter)
        north.setToolTip("North is up (PlateCarree projection)")
        layout.addWidget(north)

        # Separates the indicator from the controls. A QFrame carries a
        # stylesheet background reliably; a border-bottom on the label above
        # does not paint.
        separator = QFrame(self)
        separator.setObjectName("navSeparator")
        separator.setFixedHeight(1)
        layout.addWidget(separator)

        self.zoom_in_btn = self._add_button(
            layout, "nav_zoom_in.svg", "nav.zoom_in", canvas.zoom_in
        )
        self.zoom_out_btn = self._add_button(
            layout, "nav_zoom_out.svg", "nav.zoom_out", canvas.zoom_out
        )
        self.full_extent_btn = self._add_button(
            layout, "nav_full_extent.svg", "nav.full_extent", canvas.zoom_full_extent
        )
        self.zoom_layer_btn = self._add_button(
            layout, "nav_zoom_layer.svg", "layer.zoom_to", self._zoom_to_active_layer
        )
        self.previous_btn = self._add_button(
            layout, "nav_previous.svg", "nav.previous", canvas.zoom_previous
        )

        self.adjustSize()
        canvas.extent_changed.connect(self.refresh_state)
        self.refresh_state()

    def _add_button(self, layout, icon_name, shortcut_id, callback):
        button = QToolButton(self)
        icon = load_icon(
            icon_name, ICON_SIZE, color=GLYPH_COLOR, disabled_color=GLYPH_COLOR_DISABLED
        )
        if icon.isNull():
            # Never leave an unlabelled button behind if an asset is missing.
            logger.warning("Nav icon %s failed to load; using a text label", icon_name)
            button.setText(icon_name[4:5].upper())
        else:
            button.setIcon(icon)
            button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        button.setFixedSize(BUTTON_SIZE, BUTTON_SIZE)
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # keep canvas keyboard focus

        spec = spec_by_id(shortcut_id)
        button.setToolTip(f"{spec.label} ({display_keys(spec)})")
        # clicked() carries a 'checked' bool; passing it straight through would
        # land in zoom_in()'s factor argument.
        button.clicked.connect(lambda _checked=False, run=callback: run())
        layout.addWidget(button)
        return button

    def _zoom_to_active_layer(self):
        layer = getattr(self.main_window, "current_layer", None)
        if layer:
            self.canvas.zoom_to_layer(layer)

    def refresh_state(self, *_):
        """Follow the canvas' zoom limits and whether a layer is active."""
        info = self.canvas.get_zoom_info() or {}
        self.zoom_in_btn.setEnabled(bool(info.get("can_zoom_in", True)))
        self.zoom_out_btn.setEnabled(bool(info.get("can_zoom_out", True)))
        self.previous_btn.setEnabled(bool(self.canvas.zoom_history))
        self.zoom_layer_btn.setEnabled(
            bool(getattr(self.main_window, "current_layer", None))
        )

    def reposition(self):
        """Pin the cluster to the bottom-right corner of the canvas."""
        parent = self.parentWidget()
        if parent is None:
            return
        size = self.sizeHint()
        self.setGeometry(
            parent.width() - size.width() - MARGIN,
            parent.height() - size.height() - MARGIN,
            size.width(),
            size.height(),
        )
        self.raise_()

    def showEvent(self, event):
        # resizeEvent alone is not enough: without this the cluster would be
        # mispositioned until the first resize after the window appears.
        super().showEvent(event)
        self.reposition()
