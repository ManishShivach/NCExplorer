"""The application's visual identity: the app icon and the splash artwork.

Two PNGs are the whole of it, and they live in ``assest/`` at the repository
root (spelled as the directory is spelled) rather than under this package:

* ``NCE_icon.png``            — 1024², transparent margin, used as the window /
  dock / taskbar icon and as the source for the ``.ico`` / ``.icns`` that
  build.py embeds in the frozen executable.
* ``NCE_logo_with_name.png``  — the wordmark on a near-white field, used as the
  splash background.

Resolution has to work in three layouts, which is why :func:`asset_path`
searches rather than computes:

* a source checkout — ``<repo>/assest``;
* a PyInstaller bundle — ``sys._MEIPASS/assest``, which build.py's
  ``--add-data`` produces (on a macOS .app, ``_MEIPASS`` is
  ``Contents/Frameworks``, so this covers that too);
* an installed package with no repo around it, where the assets may have been
  copied in beside this module.

A missing asset is never fatal.  Every accessor degrades to a null QIcon or a
drawn-from-scratch card, because a branding file is not a reason to refuse to
start.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon, QPixmap

logger = logging.getLogger(__name__)

#: Directory name as it is spelled in the repository.
ASSET_DIR_NAME = "assest"

ICON_FILE = "NCE_icon.png"
LOGO_FILE = "NCE_logo_with_name.png"

# Sampled out of the artwork itself, so the splash's chrome cannot drift away
# from the logo it is drawn around.
BRAND_NAVY = "#0B294B"
BRAND_GREEN_DARK = "#2C9157"
BRAND_GREEN = "#4AC575"
BRAND_PAPER = "#FFFFFF"

#: Sizes baked into the window icon.  Qt will scale a single large pixmap on
#: demand, but a 1024² source scaled to 16px by the window manager is visibly
#: worse than one Qt has been handed at that size.
_ICON_SIZES = (512, 1024)

_icon_cache: dict[str, QIcon] = {}


def asset_dirs() -> list[Path]:
    """Every directory that might hold the branding PNGs, best guess first."""
    here = Path(__file__).resolve()
    candidates: list[Path] = []

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / ASSET_DIR_NAME)

    # <repo>/ncexplorer_toolkit/resources/branding.py -> <repo>/assest
    candidates.append(here.parents[2] / ASSET_DIR_NAME)
    # Beside the frozen executable (macOS .app: Contents/MacOS/../Resources).
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / ASSET_DIR_NAME)
        candidates.append(exe_dir.parent / "Resources" / ASSET_DIR_NAME)
    # An installed layout where the assets were copied into the package.
    candidates.append(here.parent / ASSET_DIR_NAME)

    seen: set[Path] = set()
    unique: list[Path] = []
    for path in candidates:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def asset_path(name: str) -> Path | None:
    """Absolute path of a branding asset, or None if it is not present."""
    for directory in asset_dirs():
        candidate = directory / name
        if candidate.is_file():
            return candidate
    logger.warning(
        "Branding asset %s not found; looked in %s",
        name, ", ".join(str(d) for d in asset_dirs()),
    )
    return None


def icon_source() -> Path | None:
    """Path of the square app-icon PNG, or None."""
    return asset_path(ICON_FILE)


def logo_source() -> Path | None:
    """Path of the wordmark PNG used behind the splash, or None."""
    return asset_path(LOGO_FILE)


def app_icon() -> QIcon:
    """The application icon, or a null QIcon when the asset is missing.

    Requires a live QGuiApplication: it rasterises.  Call it after the
    QApplication is constructed, which is also when it is first useful.
    """
    cached = _icon_cache.get("app")
    if cached is not None:
        return cached

    path = icon_source()
    if path is None:
        return QIcon()

    source = QPixmap(str(path))
    if source.isNull():
        logger.warning("Could not load app icon %s", path)
        return QIcon()

    icon = QIcon()
    for size in _ICON_SIZES:
        if size > source.width():
            icon.addPixmap(source)
            break
        icon.addPixmap(source.scaled(
            size, size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))
    _icon_cache["app"] = icon
    return icon


def logo_pixmap() -> QPixmap:
    """The wordmark at its native size; a null QPixmap when unavailable."""
    path = logo_source()
    if path is None:
        return QPixmap()
    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        logger.warning("Could not load splash logo %s", path)
    return pixmap


def apply_window_icon(widget) -> None:
    """Set the app icon on a widget, skipping quietly when there is none.

    Qt takes the window icon from the application when a widget has none, so
    this is belt-and-braces for windows built outside :mod:`main` (tests, the
    operator lab) — and on Windows it is what puts the icon in the taskbar
    button rather than the generic Python one.
    """
    icon = app_icon()
    if not icon.isNull():
        widget.setWindowIcon(icon)
