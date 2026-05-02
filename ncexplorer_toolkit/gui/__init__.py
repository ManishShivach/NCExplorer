"""GUI components: main window, menus, toolbars, validators, file explorer.

Members are loaded lazily via ``__getattr__``. Importing this package by
itself does not import PyQt6 — Qt is only loaded when a specific class is
first accessed.

    from ncexplorer_toolkit.gui import NCExplorerOperatorGUI  # imports PyQt6
    from ncexplorer_toolkit.gui import ThemeManager           # also imports PyQt6
"""

from __future__ import annotations

from importlib import import_module

# NOTE: gui.LayerManager is the *Qt-side* layer dock widget — distinct from
# geocanvas.LayerManager (the data-layer registry). Both names are kept for
# backward compatibility; reach for the explicit module path when both are
# needed in the same file.
_LAZY: dict[str, tuple[str, str]] = {
    "NCExplorerOperatorGUI": (".main_window",   "NCExplorerOperatorGUI"),
    "MenuBar":               (".menubar",       "MenuBar"),
    "NCExplorerToolbar":     (".toolbar",       "NCExplorerToolbar"),
    "QIntValidator":         (".widgets",       "QIntValidator"),
    "QDoubleValidator":      (".widgets",       "QDoubleValidator"),
    "QDoubleAutoValidator":  (".widgets",       "QDoubleAutoValidator"),
    "LayerManager":          (".layer_manager", "LayerManager"),
    "ThemeManager":          (".theme_manager", "ThemeManager"),
    "FileExplorer":          (".file_explorer", "FileExplorer"),
}

__all__ = sorted(_LAZY)


def __getattr__(name: str):
    if name in _LAZY:
        module_name, attribute_name = _LAZY[name]
        value = getattr(import_module(module_name, __name__), attribute_name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY))
