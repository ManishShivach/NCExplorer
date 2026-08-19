# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""The startup splash: the NCExplorer wordmark with a real progress bar.

Startup is slow for a reason that is not going away — Qt, then matplotlib,
Cartopy and the netCDF stack, then a main window that builds a map canvas — and
several seconds of nothing on screen reads as a failed launch.  So the splash
appears before the expensive imports and is stepped forward by
:mod:`main`, which knows what it is about to do.

Two details that are easy to get wrong:

* the progress it reports is the caller's real stage, not a timer, so
  :meth:`NCExplorerSplash.set_progress` repaints *and* pumps the event loop —
  nothing else will, because the thread is about to block inside an import;
* the background is composed at the screen's device-pixel ratio, otherwise the
  wordmark is visibly soft on every HiDPI display, which is most of them.

With the artwork missing (see :mod:`ncexplorer_toolkit.resources.branding`) the
same card is drawn from the brand colours and the application name, so this
module never fails to produce a splash.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, QRect, QRectF
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import QApplication, QSplashScreen

from ..__version__ import APP_NAME, __version__
from ..resources.branding import (
    BRAND_GREEN,
    BRAND_GREEN_DARK,
    BRAND_NAVY,
    BRAND_PAPER,
    logo_pixmap,
)

logger = logging.getLogger(__name__)

#: Logical size of the card.  The width is chosen so the wordmark reads at a
#: glance without the splash dominating a laptop screen; the artwork's own
#: aspect ratio (11:6) fixes the height of the image area above the footer.
SPLASH_WIDTH = 620
LOGO_HEIGHT = 338
FOOTER_HEIGHT = 86
SPLASH_HEIGHT = LOGO_HEIGHT + FOOTER_HEIGHT

#: Horizontal inset shared by the separator, the text rows and the bar.
MARGIN = 32
BAR_HEIGHT = 6


def _footer_top() -> int:
    return LOGO_HEIGHT


def _compose_background(dpr: float) -> QPixmap:
    """Draw everything about the splash that does not change while it is up."""
    pixmap = QPixmap(int(SPLASH_WIDTH * dpr), int(SPLASH_HEIGHT * dpr))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(QColor(BRAND_PAPER))

    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        logo = logo_pixmap()
        if logo.isNull():
            _draw_fallback_wordmark(painter)
        else:
            scaled = logo.scaled(
                int(SPLASH_WIDTH * dpr), int(LOGO_HEIGHT * dpr),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            scaled.setDevicePixelRatio(dpr)
            # Centred, in case the artwork is ever re-cut to another ratio.
            x = (SPLASH_WIDTH - scaled.width() / dpr) / 2
            y = (LOGO_HEIGHT - scaled.height() / dpr) / 2
            painter.drawPixmap(QRectF(
                x, y, scaled.width() / dpr, scaled.height() / dpr,
            ), scaled, QRectF(scaled.rect()))

        footer_top = _footer_top()

        hairline = QColor(BRAND_NAVY)
        hairline.setAlpha(38)
        painter.setPen(QPen(hairline, 1))
        painter.drawLine(MARGIN, footer_top, SPLASH_WIDTH - MARGIN, footer_top)

        version = QColor(BRAND_NAVY)
        version.setAlpha(120)
        painter.setPen(version)
        painter.setFont(QFont(_ui_family(), 9))
        painter.drawText(
            QRect(MARGIN, footer_top + 58, SPLASH_WIDTH - 2 * MARGIN, 18),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            f"version {__version__}",
        )

        border = QColor(BRAND_NAVY)
        border.setAlpha(60)
        painter.setPen(QPen(border, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # Inset by half a pixel so the 1px stroke lands *on* the edge instead of
        # being clipped in half by it.
        painter.drawRect(QRectF(0.5, 0.5, SPLASH_WIDTH - 1, SPLASH_HEIGHT - 1))
    finally:
        painter.end()

    return pixmap


def _ui_family() -> str:
    """A UI font that exists on the platform Qt is running on."""
    return QApplication.font().family()


def _draw_fallback_wordmark(painter: QPainter) -> None:
    """The card when the logo PNG is absent: name and tagline, brand colours."""
    painter.fillRect(0, 0, SPLASH_WIDTH, LOGO_HEIGHT, QColor(BRAND_PAPER))

    painter.setPen(QColor(BRAND_NAVY))
    painter.setFont(QFont(_ui_family(), 34, QFont.Weight.Bold))
    painter.drawText(
        QRect(MARGIN, LOGO_HEIGHT // 2 - 44, SPLASH_WIDTH - 2 * MARGIN, 60),
        int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
        APP_NAME,
    )

    subtitle = QColor(BRAND_GREEN_DARK)
    painter.setPen(subtitle)
    painter.setFont(QFont(_ui_family(), 12))
    painter.drawText(
        QRect(MARGIN, LOGO_HEIGHT // 2 + 16, SPLASH_WIDTH - 2 * MARGIN, 24),
        int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
        "Climate Data Operators, with a map",
    )


class NCExplorerSplash(QSplashScreen):
    """Branded splash screen with a caller-driven progress bar."""

    def __init__(self) -> None:
        screen = QApplication.primaryScreen()
        dpr = screen.devicePixelRatio() if screen is not None else 1.0
        super().__init__(_compose_background(dpr))

        # QSplashScreen already sets SplashScreen | FramelessWindowHint; the
        # stays-on-top hint is what keeps it visible while the main window is
        # being built and raised behind it.
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)

        self._message = ""
        self._progress = 0

    # -- public API ---------------------------------------------------------
    @property
    def progress(self) -> int:
        """The bar's current value, 0-100."""
        return self._progress

    @property
    def message(self) -> str:
        """The caption currently under the wordmark."""
        return self._message

    def set_progress(self, value: int, message: str | None = None) -> None:
        """Move the bar to ``value`` (0-100) and optionally change the caption.

        Repaints synchronously and pumps the event loop: the caller is a
        straight-line startup sequence that is about to block, so nothing else
        would deliver the paint event.
        """
        self._progress = max(0, min(100, int(value)))
        if message is not None:
            self._message = message
        logger.debug("Splash: %d%% — %s", self._progress, self._message)

        self.repaint()
        app = QApplication.instance()
        if app is not None:
            app.processEvents()

    # -- painting -----------------------------------------------------------
    def drawContents(self, painter: QPainter | None) -> None:   # noqa: N802
        """Draw the caption and the bar over the composed background.

        Overrides rather than extends: the base implementation draws the
        ``showMessage`` text, which this class does not use.
        """
        if painter is None:
            return

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        footer_top = _footer_top()

        if self._message:
            painter.setPen(QColor(BRAND_NAVY))
            painter.setFont(QFont(_ui_family(), 10))
            painter.drawText(
                QRect(MARGIN, footer_top + 12, SPLASH_WIDTH - 2 * MARGIN, 20),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                self._message,
            )

        bar_x = MARGIN
        bar_y = footer_top + 40
        bar_width = SPLASH_WIDTH - 2 * MARGIN
        radius = BAR_HEIGHT / 2

        track = QColor(BRAND_NAVY)
        track.setAlpha(28)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(track))
        painter.drawRoundedRect(
            QRectF(bar_x, bar_y, bar_width, BAR_HEIGHT), radius, radius,
        )

        if self._progress > 0:
            fill_width = bar_width * self._progress / 100
            # Never narrower than the cap radius, or the rounded rect collapses
            # into a sliver that reads as an empty bar at low percentages.
            fill_width = max(fill_width, BAR_HEIGHT)
            gradient = QLinearGradient(bar_x, 0, bar_x + bar_width, 0)
            gradient.setColorAt(0.0, QColor(BRAND_GREEN_DARK))
            gradient.setColorAt(1.0, QColor(BRAND_GREEN))
            painter.setBrush(QBrush(gradient))
            painter.drawRoundedRect(
                QRectF(bar_x, bar_y, fill_width, BAR_HEIGHT), radius, radius,
            )


def show_splash() -> NCExplorerSplash:
    """Construct, show and paint the splash in one call."""
    splash = NCExplorerSplash()
    splash.show()
    splash.set_progress(0, f"Starting {APP_NAME}…")
    return splash
