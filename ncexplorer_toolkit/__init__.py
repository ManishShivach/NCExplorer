"""NCExplorer toolkit — a cross-platform GUI for the Climate Data Operators.

Public surface:

    Eager (always available)
        __version__, APP_NAME, APP_AUTHOR, APP_AUTHOR_EMAIL,
        APP_DESCRIPTION, APP_URL
        NCExplorerCategory
        NCExplorerIntegration, NCExplorerResult, NCExplorerError,
        create_NCExplorer_integration,
        create_native_NCExplorer, create_wsl_NCExplorer

    Lazy (loaded on first attribute access — keeps Qt / Cartopy / Rasterio
    out of the import graph until something actually needs them)
        BasemapManager, GeoCanvas, LayerManager, NetCDFManager,
        LayerPropertyManager, LayerStyleProperties, LayerMetadata,
        LayerDimensions, LayerProperty, NetCDFProperties,
        SymbologyManager, TempFileStore,
        MenuBar, NCExplorerToolbar, NCExplorerOperatorGUI,
        QIntValidator, QDoubleValidator, QDoubleAutoValidator
"""

from __future__ import annotations

from importlib import import_module

from .__version__ import (
    APP_AUTHOR,
    APP_AUTHOR_EMAIL,
    APP_DESCRIPTION,
    APP_NAME,
    APP_URL,
    __version__,
)
from .core.categories import NCExplorerCategory
from .core.nc_integration import (
    NCExplorerError,
    NCExplorerIntegration,
    NCExplorerResult,
    create_NCExplorer_integration,
    create_native_NCExplorer,
    create_wsl_NCExplorer,
)

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    # geocanvas
    "BasemapManager":       (".geocanvas.basemap",    "BasemapManager"),
    "GeoCanvas":            (".geocanvas.canvas",     "GeoCanvas"),
    "LayerManager":         (".geocanvas.layers",     "LayerManager"),
    "NetCDFManager":        (".geocanvas.netcdf",     "NetCDFManager"),
    "LayerPropertyManager": (".geocanvas.properties", "LayerPropertyManager"),
    "LayerStyleProperties": (".geocanvas.properties", "LayerStyleProperties"),
    "LayerMetadata":        (".geocanvas.properties", "LayerMetadata"),
    "LayerDimensions":      (".geocanvas.properties", "LayerDimensions"),
    "LayerProperty":        (".geocanvas.properties", "LayerProperty"),
    "NetCDFProperties":     (".geocanvas.properties", "NetCDFProperties"),
    "SymbologyManager":     (".geocanvas.symbology",  "SymbologyManager"),
    # utils
    "TempFileStore":        (".utils.tempfile_store", "TempFileStore"),
    # gui
    "MenuBar":               (".gui.menubar",      "MenuBar"),
    "NCExplorerToolbar":     (".gui.toolbar",      "NCExplorerToolbar"),
    "QIntValidator":         (".gui.widgets",      "QIntValidator"),
    "QDoubleValidator":      (".gui.widgets",      "QDoubleValidator"),
    "QDoubleAutoValidator":  (".gui.widgets",      "QDoubleAutoValidator"),
    "NCExplorerOperatorGUI": (".gui.main_window",  "NCExplorerOperatorGUI"),
}

__all__ = [
    # version metadata
    "__version__",
    "APP_NAME",
    "APP_AUTHOR",
    "APP_AUTHOR_EMAIL",
    "APP_DESCRIPTION",
    "APP_URL",
    # core (eager)
    "NCExplorerCategory",
    "NCExplorerError",
    "NCExplorerIntegration",
    "NCExplorerResult",
    "create_NCExplorer_integration",
    "create_native_NCExplorer",
    "create_wsl_NCExplorer",
    # lazy
    *sorted(_LAZY_IMPORTS),
]


def __getattr__(name: str):
    if name in _LAZY_IMPORTS:
        module_name, attribute_name = _LAZY_IMPORTS[name]
        value = getattr(import_module(module_name, __name__), attribute_name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_IMPORTS))
