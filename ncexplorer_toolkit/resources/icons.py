# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""Loading of the bundled SVG glyphs for the CDO operator categories.

Every icon ships in the repository under ``resources/icons``; nothing is ever
fetched at runtime.

Two Qt-specific hazards shape this module:

* ``QIcon("x.svg")`` silently produces a *null* icon when Qt's SVG image-format
  plugin is missing from a PyInstaller bundle, so the buttons would go blank
  with no error. The SVGs are therefore rasterised explicitly through
  ``QSvgRenderer``, which also makes PyInstaller pull ``PyQt6.QtSvg`` in.
* Qt's SVG renderer resolves ``currentColor`` to black rather than to the
  widget palette, so the glyph colour is substituted into the markup before
  rendering. That keeps the icons legible under a dark palette without
  introducing a theme system.
"""

import sys
import logging
from pathlib import Path

from PyQt6.QtCore import QByteArray, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap, QPalette
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QApplication

from ..core.categories import NCExplorerCategory

logger = logging.getLogger(__name__)

ICON_DIR_NAME = "icons"
DEFAULT_ICON_SIZE = 24

# Explicit and exhaustive: a category missing here raises KeyError in
# category_icon() (and in tests/test_icons.py) instead of silently rendering an
# empty button.
CATEGORY_ICONS: dict[NCExplorerCategory, str] = {
    NCExplorerCategory.INFORMATION: "information.svg",
    NCExplorerCategory.FILE_OPERATIONS: "file_operations.svg",
    NCExplorerCategory.SELECTION: "selection.svg",
    NCExplorerCategory.CONDITIONAL_SELECTION: "conditional_selection.svg",
    NCExplorerCategory.COMPARISON: "comparison.svg",
    NCExplorerCategory.MODIFICATION: "modification.svg",
    NCExplorerCategory.ARITHMETIC: "arithmetic.svg",
    NCExplorerCategory.STATISTICAL_VALUES: "statistical_values.svg",
    # A scatter without the fitted line, next to regression.svg which is the
    # same axes *with* one: these four measure how two fields move together,
    # they do not fit anything to them.
    NCExplorerCategory.CORRELATION: "correlation.svg",
    # A scree plot: the eigenvalue spectrum decaying from the leading mode.
    # Deliberately a *falling* curve rather than a bar chart — the bars are
    # statistical_values.svg, and the rising straight line is regression.svg,
    # so the shape is what tells the three apart at 24 pixels. It is also the
    # one plot every EOF result gets looked at through.
    NCExplorerCategory.EOF: "eof.svg",
    NCExplorerCategory.REGRESSION: "regression.svg",
    NCExplorerCategory.INTERPOLATION: "interpolation.svg",
    NCExplorerCategory.TRANSFORMATION: "transformation.svg",
    # Renamed with the category, art unchanged: a document with one arrow
    # coming in and one going out was already an import/export glyph rather
    # than a "formatted I/O" one, which is a thing that has no picture.
    NCExplorerCategory.IMPORT_EXPORT: "import_export.svg",
    # A framed plot with contour bands inside it — the picture these six
    # operators actually produce, and what a Magplot page's own figure looks
    # like at thumbnail size.
    #
    # Deliberately the only icon in this set built on a *frame* rather than on
    # a pair of axes. correlation.svg, regression.svg, eof.svg and
    # statistical_values.svg are all L-shaped axes distinguished by what sits
    # inside them (scatter, rising line, falling curve, bars), and a fifth
    # member of that family would not be tellable from the other four at 24
    # pixels. A closed rectangle with curves in it is a different silhouette,
    # which is the property that has to survive the size.
    NCExplorerCategory.GRAPHICS: "graphics.svg",
    NCExplorerCategory.MISCELLANEOUS: "miscellaneous.svg",
    NCExplorerCategory.ECA_INDICES: "eca_indices.svg",
}

_icon_cache: dict[tuple[str, str], QIcon] = {}


def resources_root() -> Path:
    """Directory holding the bundled assets, in-source or PyInstaller-frozen."""
    if getattr(sys, "frozen", False):
        # --add-data ships the whole package tree, so the layout below
        # _MEIPASS mirrors the source layout.
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass) / "ncexplorer_toolkit" / "resources"
    return Path(__file__).resolve().parent


def icon_path(name: str) -> Path:
    """Absolute path of a bundled icon file."""
    return resources_root() / ICON_DIR_NAME / name


def glyph_color() -> QColor:
    """Colour to draw glyphs in: the palette's text colour when one exists."""
    app = QApplication.instance()
    if app is not None:
        return app.palette().color(QPalette.ColorRole.WindowText)
    return QColor("#202020")


def load_pixmap(name: str, size: int, color: QColor) -> QPixmap:
    """Rasterise a bundled SVG in one colour; null QPixmap on failure."""
    path = icon_path(name)
    try:
        markup = path.read_text(encoding="utf-8").replace("currentColor", color.name())
    except OSError as exc:
        logger.warning("Icon %s could not be read (%s); falling back to text only", name, exc)
        return QPixmap()

    renderer = QSvgRenderer(QByteArray(markup.encode("utf-8")))
    if not renderer.isValid():
        logger.warning("Icon %s is not valid SVG; falling back to text only", name)
        return QPixmap()

    # Render at 2x for crisp scaling on HiDPI displays.
    pixmap = QPixmap(QSize(size * 2, size * 2))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        renderer.render(painter, QRectF(0, 0, size * 2, size * 2))
    finally:
        painter.end()
    return pixmap


def load_icon(
    name: str,
    size: int = DEFAULT_ICON_SIZE,
    color: QColor | str | None = None,
    disabled_color: QColor | str | None = None,
) -> QIcon:
    """Rasterise a bundled SVG into a QIcon; returns a null QIcon on failure.

    ``color`` defaults to the palette's text colour, which is right for icons on
    a palette-coloured surface such as the toolbar. Callers drawing onto a
    surface with a fixed colour of its own (the map overlay) must pass an
    explicit colour, or the glyphs vanish whenever the palette matches that
    surface — white-on-white under a dark system theme.
    """
    fill = QColor(color) if color is not None else glyph_color()
    faded = QColor(disabled_color) if disabled_color is not None else None

    cache_key = (name, size, fill.name(), faded.name() if faded else "")
    cached = _icon_cache.get(cache_key)
    if cached is not None:
        return cached

    pixmap = load_pixmap(name, size, fill)
    if pixmap.isNull():
        return QIcon()

    icon = QIcon(pixmap)
    if faded is not None:
        # Qt's own fading of a disabled icon is barely distinguishable on a
        # light panel; an explicit muted glyph reads as unmistakably inactive.
        disabled_pixmap = load_pixmap(name, size, faded)
        if not disabled_pixmap.isNull():
            icon.addPixmap(disabled_pixmap, QIcon.Mode.Disabled, QIcon.State.Off)

    _icon_cache[cache_key] = icon
    return icon


def category_icon(category: NCExplorerCategory, size: int = DEFAULT_ICON_SIZE) -> QIcon:
    """Icon for one operator category. Raises KeyError for an unmapped member."""
    return load_icon(CATEGORY_ICONS[category], size)


def clear_icon_cache() -> None:
    """Drop cached icons (used by tests, and after a palette change)."""
    _icon_cache.clear()
