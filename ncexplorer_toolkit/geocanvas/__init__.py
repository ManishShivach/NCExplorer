"""Geospatial canvas: Cartopy-on-Qt rendering, layers, symbology.

Members are loaded lazily via ``__getattr__``. Importing this package by
itself is cheap; Cartopy, Matplotlib, rasterio, geopandas and shapely are
only loaded when a specific class is accessed.

    from ncexplorer_toolkit.geocanvas import GeoCanvas    # imports cartopy
    from ncexplorer_toolkit.geocanvas import LayerManager # cheap
"""

from __future__ import annotations

from importlib import import_module

_LAZY: dict[str, tuple[str, str]] = {
    "GeoCanvas":               (".canvas",     "GeoCanvas"),
    "LayerManager":            (".layers",     "LayerManager"),
    "LayerPropertyManager":    (".properties", "LayerPropertyManager"),
    "LayerProperty":           (".properties", "LayerProperty"),
    "LayerStyleProperties":    (".properties", "LayerStyleProperties"),
    "LayerMetadata":           (".properties", "LayerMetadata"),
    "LayerDimensions":         (".properties", "LayerDimensions"),
    "NetCDFProperties":        (".properties", "NetCDFProperties"),
    "SymbologyManager":        (".symbology",  "SymbologyManager"),
    "ShapefileDisplayManager": (".shapefile",  "ShapefileDisplayManager"),
    # Reading and format capability. open_vector/open_raster pull in geopandas
    # and rasterio respectively; ``formats`` itself is cheap, and its driver
    # probes only run when something asks what is supported.
    "open_vector":             (".vector_io",  "open_vector"),
    "open_raster":             (".raster_io",  "open_raster"),
}

# ``NetCDFManager`` (geocanvas/netcdf.py) was exported here until it was found
# to be unrunnable: every method that touched a layer dereferenced
# ``canvas.layer_manager``, which ``GeoCanvas`` does not have, and
# ``visualize_netcdf_layer`` referenced a ``ccrs`` the module never imported.
# ``GeoCanvas`` carries its own copies of those methods — see the "NetCDF-specific
# Methods (from NetCDFManager)" section in canvas.py — and they are what the app
# has always used.

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
