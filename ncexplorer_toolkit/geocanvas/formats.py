# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""The one table of file formats the canvas can put on the map.

Before this module the answer was spread over five places that disagreed:
``GeoCanvas.load_file`` dispatched on ``.shp/.geojson/.tif/.tiff/.img/.nc``,
``GeoCanvas.supported_vector_formats`` also listed ``.kml``/``.gpx`` that the
dispatcher would have rejected, ``supported_raster_formats`` listed
``.png``/``.jpg`` that nothing drew, the Open dialog offered a sixth set and
``DROPPABLE_EXTENSIONS`` a seventh. A file could therefore be offered by the
chooser and refused by the loader, or accepted by drag-and-drop and refused by
the chooser. Every one of those lists now derives from :data:`FORMATS`.

Why capability is probed rather than declared
---------------------------------------------
The extension is only half the answer: the other half is whether the GDAL
build actually carries the driver, and *that varies inside one installation*.
geopandas 1.x picks its engine at call time — pyogrio first, then fiona — and
the two ship **different** GDAL builds in their own wheels. Measured on this
project's dependency set:

===============  =======================  =====================
driver            pyogrio (65 drivers)     fiona (17 drivers)
===============  =======================  =====================
ESRI Shapefile    yes                      yes
GeoJSON/GML/GPKG  yes                      yes
GPX               yes                      yes
KML / LIBKML      yes                      **no**
Idrisi (vector)   yes (read-only)          **no**
===============  =======================  =====================

So KML works in a source checkout, where pyogrio is usually installed, and
fails in the packaged app, where ``build.py`` excludes pyogrio on purpose. That
is exactly the failure mode this module exists to prevent: a format is declared
supported only if the *running* interpreter can open it, and a format that
cannot be opened says which package would fix it instead of surfacing GDAL's
"not recognized as being in a supported file format".

The probe result is cached — it costs a driver-table walk, and the answer
cannot change without restarting the process.
"""

from __future__ import annotations

import logging
import os
import zipfile
from dataclasses import dataclass, field
from functools import lru_cache

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Kinds
# --------------------------------------------------------------------------
#: Read through geopandas -> pyogrio/fiona, drawn as point/line/polygon artists.
VECTOR = "vector"
#: Read through rasterio, drawn with imshow.
RASTER = "raster"
#: Read through xarray; has its own variable/time UI, so it stays distinct
#: from RASTER even though rasterio could open some of these files.
NETCDF = "netcdf"
#: Known to us, and known not to work. Registered so the refusal can explain
#: itself rather than falling through to a generic parse error.
UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class Format:
    """One file format the canvas knows about.

    ``driver`` is the GDAL/OGR driver name to probe for, or None when the
    format needs no probe (rasterio's GDAL is the same wheel everywhere, and
    NetCDF goes through xarray). ``reason`` is filled in only for UNSUPPORTED
    and is shown to the user verbatim.
    """

    label: str
    kind: str
    extensions: tuple[str, ...]
    driver: str | None = None
    #: Companion files that must sit beside the main one. A shapefile without
    #: its .dbf/.shx and an ENVI .dat without its .hdr are both unreadable, and
    #: GDAL reports that as an unrecognised format rather than a missing file.
    sidecars: tuple[str, ...] = ()
    reason: str = ""
    notes: str = ""


#: Every format, in the order the Open dialog should offer them.
#:
#: Two entries deserve their reasons spelled out, because both look like
#: oversights and neither is:
#:
#: **SVG** — GDAL does have a driver called SVG, but it reads Cloudmade Vector
#: Stream Server documents, not drawings. A normal SVG is refused by it
#: (measured: "not recognized as being in a supported file format" with the
#: driver named explicitly). The deeper problem is that an SVG carries no CRS
#: and no ground control points, so even parsed there is nothing to say where
#: on Earth it goes.
#:
#: **HDF4** — the GDAL inside the rasterio wheel is built without the HDF4 and
#: HDF4Image drivers (measured: absent from ``rasterio.env.Env().drivers()``,
#: which lists 157). HDF5 is present, so ``.h5`` works and the classic HDF4
#: ``.hdf`` does not, despite the shared extension family.
FORMATS: tuple[Format, ...] = (
    # ---------------------------------------------------------- vector ---
    Format("Shapefile", VECTOR, (".shp",), driver="ESRI Shapefile",
           sidecars=(".shx", ".dbf"),
           notes="Also reads .prj/.cpg/.sbn/.sbx when present."),
    Format("GeoJSON", VECTOR, (".geojson", ".json"), driver="GeoJSON",
           notes=".json is only tried as GeoJSON; a plain JSON file is refused."),
    Format("KML", VECTOR, (".kml",), driver="KML"),
    Format("KMZ", VECTOR, (".kmz",), driver="KML",
           notes="A zipped KML; opened through GDAL's /vsizip/ virtual filesystem."),
    Format("GML", VECTOR, (".gml",), driver="GML"),
    Format("GeoPackage", VECTOR, (".gpkg",), driver="GPKG"),
    Format("GPX", VECTOR, (".gpx",), driver="GPX",
           notes="Multi-layer: waypoints, routes, tracks."),
    Format("Idrisi Vector", VECTOR, (".vct",), driver="Idrisi",
           sidecars=(".vdc",),
           notes="Read-only in GDAL; .vdc carries the metadata."),
    Format(
        "SVG", UNSUPPORTED, (".svg",),
        reason=(
            "SVG is a drawing format with no coordinate reference system, so "
            "there is nothing that says where on the map it belongs.\n\n"
            "GDAL's driver named 'SVG' reads Cloudmade Vector Stream Server "
            "documents, not ordinary SVG drawings, and refuses these files.\n\n"
            "To place artwork on the map, georeference it first — save it as a "
            "GeoTIFF with a CRS, which NCExplorer reads."
        ),
    ),

    # ---------------------------------------------------------- raster ---
    Format("GeoTIFF", RASTER, (".tif", ".tiff"), driver="GTiff"),
    Format("USGS DEM", RASTER, (".dem",), driver="USGSDEM"),
    Format("ENVI", RASTER, (".dat",), driver="ENVI", sidecars=(".hdr",),
           notes="The .hdr is mandatory — without it GDAL cannot identify the file."),
    Format("Idrisi Raster", RASTER, (".rst",), driver="RST",
           notes=".rdc holds the CRS and units; readable without it, but unreferenced."),
    Format("HDF5", RASTER, (".h5", ".hdf5"), driver="HDF5",
           notes="Container format: variables are exposed as GDAL subdatasets."),
    Format(
        "HDF4", UNSUPPORTED, (".hdf",),
        reason=(
            "This build cannot read HDF4.\n\n"
            "The GDAL bundled with rasterio is compiled without the HDF4 "
            "driver, and a classic .hdf file (MODIS and other older products) "
            "needs it.\n\n"
            "HDF5 files are supported — if this file is really HDF5, rename it "
            "to .h5 and it will open."
        ),
    ),

    # ---------------------------------------------------------- netcdf ---
    Format("NetCDF", NETCDF, (".nc", ".nc4", ".netcdf"),
           notes="Read through xarray, with variable and time selection."),
)


# --------------------------------------------------------------------------
# Lookup
# --------------------------------------------------------------------------
_BY_EXTENSION: dict[str, Format] = {
    ext: fmt for fmt in FORMATS for ext in fmt.extensions
}


def format_for(path) -> Format | None:
    """The :class:`Format` for ``path``'s extension, or None if unknown."""
    return _BY_EXTENSION.get(os.path.splitext(str(path))[1].lower())


def all_extensions(kind: str | None = None) -> tuple[str, ...]:
    """Every registered extension, optionally limited to one kind.

    UNSUPPORTED extensions are included when ``kind`` is None, on purpose: the
    Open dialog should let a user pick a ``.svg`` and be told why it will not
    work, rather than hide it and leave them wondering whether the dialog is
    broken.
    """
    return tuple(
        ext for fmt in FORMATS
        if kind is None or fmt.kind == kind
        for ext in fmt.extensions
    )


# --------------------------------------------------------------------------
# Capability probing
# --------------------------------------------------------------------------
@lru_cache(maxsize=1)
def vector_drivers() -> frozenset[str]:
    """OGR drivers the active geopandas engine can actually open.

    pyogrio is asked first because geopandas prefers it, and because it is the
    engine with the wider driver table — reporting fiona's 17 drivers while
    geopandas is dispatching to pyogrio's 65 would refuse KML files that would
    in fact have loaded.

    An empty set means no engine is importable at all, which
    :func:`~ncexplorer_toolkit.geocanvas.vector_io.read_vector_file` already
    reports; callers treat "empty" as "cannot answer", not "nothing supported".
    """
    try:
        import pyogrio
        return frozenset(pyogrio.list_drivers())
    except Exception:
        logger.debug("pyogrio unavailable; falling back to fiona's driver table")

    try:
        import fiona
        return frozenset(fiona.supported_drivers)
    except Exception:
        logger.warning("No vector I/O engine is importable")
        return frozenset()


@lru_cache(maxsize=1)
def raster_drivers() -> frozenset[str]:
    """GDAL drivers the rasterio wheel was built with."""
    try:
        import rasterio.env
        with rasterio.env.Env() as env:
            return frozenset(env.drivers())
    except Exception:
        logger.warning("rasterio driver table unavailable", exc_info=True)
        return frozenset()


#: What to tell a user whose engine lacks the driver. Only pyogrio-only
#: drivers get a specific remedy; anything else is a genuinely odd build.
_PYOGRIO_ONLY = {"KML", "LIBKML", "Idrisi"}

_PYOGRIO_REMEDY = (
    "This format needs the 'pyogrio' engine, which is not part of this "
    "installation.\n\n"
    "Running from source: install it with\n"
    "    pip install pyogrio\n\n"
    "Formats that work without it: Shapefile, GeoJSON, GML, GeoPackage and GPX."
)


def availability(fmt: Format) -> tuple[bool, str]:
    """Whether ``fmt`` can be opened here, and why not when it cannot.

    Returns ``(True, "")`` when the format is usable. The message is written
    for the user, not the log — it names the missing package and what to do.
    """
    if fmt.kind == UNSUPPORTED:
        return False, fmt.reason

    if fmt.driver is None:            # NetCDF: xarray, no driver to probe
        return True, ""

    if fmt.kind == VECTOR:
        drivers = vector_drivers()
        if not drivers:               # no engine at all — vector_io reports it
            return True, ""
        if fmt.driver in drivers:
            return True, ""
        if fmt.driver in _PYOGRIO_ONLY:
            return False, f"{fmt.label} files cannot be opened.\n\n{_PYOGRIO_REMEDY}"
        return False, (
            f"{fmt.label} files cannot be opened: this installation's vector "
            f"engine has no '{fmt.driver}' driver."
        )

    if fmt.kind == RASTER:
        drivers = raster_drivers()
        if not drivers or fmt.driver in drivers:
            return True, ""
        return False, (
            f"{fmt.label} files cannot be opened: the GDAL bundled with "
            f"rasterio has no '{fmt.driver}' driver."
        )

    return True, ""


def supported_extensions() -> tuple[str, ...]:
    """Extensions that will actually load in *this* process.

    This is what the file choosers and the drop handler filter on, so the set a
    user is offered matches the set the loader accepts.
    """
    return tuple(
        ext for fmt in FORMATS if availability(fmt)[0] for ext in fmt.extensions
    )


# --------------------------------------------------------------------------
# Sidecars
# --------------------------------------------------------------------------
def missing_sidecars(path) -> tuple[str, ...]:
    """Required companion files that are not next to ``path``.

    Matched case-insensitively: a shapefile set written on Windows often
    arrives as ``NAME.SHP`` + ``NAME.DBF``, and treating those as missing would
    refuse a perfectly good layer.
    """
    fmt = format_for(path)
    if not fmt or not fmt.sidecars:
        return ()

    stem, _ = os.path.splitext(str(path))
    directory = os.path.dirname(stem) or "."
    try:
        present = {name.lower() for name in os.listdir(directory)}
    except OSError:
        return ()

    base = os.path.basename(stem).lower()
    return tuple(
        ext for ext in fmt.sidecars if f"{base}{ext}" not in present
    )


def sidecar_message(path, missing: tuple[str, ...]) -> str:
    """Why a file with absent sidecars cannot be read."""
    name = os.path.basename(str(path))
    fmt = format_for(path)
    label = fmt.label if fmt else "This format"
    listed = ", ".join(missing)
    return (
        f"{name} is missing {'a companion file' if len(missing) == 1 else 'companion files'}: "
        f"{listed}\n\n"
        f"{label} layers are a set of files that must stay together. Copy "
        f"{'it' if len(missing) == 1 else 'them'} into the same folder and try again."
    )


# --------------------------------------------------------------------------
# KMZ
# --------------------------------------------------------------------------
def gdal_path(path) -> str:
    """The path to hand GDAL, which is not always the path on disk.

    A KMZ is a zip archive; GDAL reads it only through the ``/vsizip/`` virtual
    filesystem, and only when pointed at the KML *inside* it. The entry is
    located by reading the archive rather than assuming ``doc.kml``, because
    the name is a convention that exporters do not all follow.
    """
    text = str(path)
    if not text.lower().endswith(".kmz"):
        return text

    absolute = os.path.abspath(text)
    try:
        with zipfile.ZipFile(absolute) as archive:
            entries = [n for n in archive.namelist() if n.lower().endswith(".kml")]
    except (OSError, zipfile.BadZipFile):
        # Not a readable zip. Hand back the plain path so the loader's own
        # error reporting describes the real problem.
        return text

    if not entries:
        return text

    # doc.kml is the convention; otherwise the first KML in the archive.
    preferred = next((n for n in entries if os.path.basename(n).lower() == "doc.kml"),
                     entries[0])
    return f"/vsizip/{absolute}/{preferred}"
