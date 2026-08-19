# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""Reading raster files for the canvas: subdatasets, CRS, and honest refusals.

``GeoCanvas.load_raster`` opened the path, checked ``src.count``, read band 1
and drew it at ``src.bounds`` under ``ccrs.PlateCarree()``. That is right for a
lon/lat GeoTIFF and wrong for most of what this module now accepts:

* **Container formats have no bands.** HDF5 reports ``count == 0`` and exposes
  its variables as *subdatasets* — measured on a two-variable file, ``count``
  is 0 and ``subdatasets`` holds ``HDF5:file://temp`` and ``HDF5:file://precip``.
  The old ``if src.count == 0: raise ValueError("Raster has no bands")`` meant
  every HDF5 file was rejected as empty when the data was one level down.
* **Projected rasters were drawn as degrees.** ``bounds`` in a projected CRS is
  metres, and ``transform=ccrs.PlateCarree()`` reads it as lon/lat, so the
  image landed far off the map — and, for a UTM raster, mostly outside it.
* **A missing sidecar reads as a corrupt file.** ENVI's ``.hdr`` carries the
  dimensions and data type, so ``.dat`` alone is not merely unreferenced, it is
  unidentifiable; GDAL says "not recognized as being in a supported file
  format", which sends the user looking for the wrong problem.

Everything here is display-oriented. Reprojection is done for the *picture*,
through a :class:`~rasterio.vrt.WarpedVRT`; the file on disk is untouched and
CDO still receives the original path.
"""

from __future__ import annotations

import logging

from . import formats

logger = logging.getLogger(__name__)

#: The CRS the canvas draws in; see vector_io.CANVAS_CRS.
CANVAS_CRS = "EPSG:4326"

#: Above this, a warped read is downsampled rather than pulled in at full size.
#: A warp allocates the destination up front, so a large scene reprojected at
#: native resolution can cost far more memory than the file suggests.
MAX_DISPLAY_PIXELS = 4096 * 4096


class RasterFormatUnavailable(RuntimeError):
    """The format is registered but this installation cannot open it."""


class Subdataset:
    """One variable inside a container file, named for a human.

    ``path`` is GDAL's connection string (``HDF5:"file.h5"://temp``), which is
    what :func:`open_raster` must be given; ``name`` is the trailing component,
    which is what a user recognises.
    """

    __slots__ = ("path", "name", "description")

    def __init__(self, path: str, description: str = ""):
        self.path = path
        self.description = description or path
        # GDAL's form is DRIVER:location://path/to/var — the variable is the
        # last component, and rsplit is safe even when the pattern differs.
        self.name = path.rsplit("/", 1)[-1] or path.rsplit(":", 1)[-1]

    def __repr__(self):
        return f"<Subdataset {self.name!r}>"


def list_subdatasets(filepath) -> list[Subdataset]:
    """Variables inside a container file; ``[]`` for an ordinary raster.

    A file with real bands is never treated as a container even if it also
    advertises subdatasets, because the bands are what the user opened it for.
    """
    import rasterio

    try:
        with rasterio.open(str(filepath)) as src:
            if src.count:
                return []
            return [Subdataset(p) for p in (src.subdatasets or [])]
    except Exception:
        logger.debug("Could not enumerate subdatasets in %s", filepath,
                     exc_info=True)
        return []


def _needs_warp(src) -> bool:
    """Whether ``src`` must be reprojected before it can be drawn."""
    if src.crs is None:
        # No CRS: assume the bounds are already lon/lat. There is nothing to
        # convert from, and this is what the canvas did before.
        return False
    try:
        return src.crs.to_epsg() != 4326
    except Exception:
        return True


def _decimation(width: int, height: int) -> int:
    """Integer stride keeping a read under :data:`MAX_DISPLAY_PIXELS`."""
    step = 1
    while (width // step) * (height // step) > MAX_DISPLAY_PIXELS:
        step += 1
    return step


class RasterRead:
    """The result of :func:`open_raster` — data plus where to draw it.

    ``extent`` is ``[west, east, south, north]`` in :data:`CANVAS_CRS`, which is
    the order ``imshow`` wants and the order the canvas already stores.
    """

    __slots__ = ("data", "extent", "crs", "source_crs", "width", "height",
                 "count", "nodata", "pixel_size_x", "pixel_size_y",
                 "warped", "subdatasets")

    def __init__(self, **kwargs):
        for slot in self.__slots__:
            setattr(self, slot, kwargs.get(slot))


def open_raster(filepath, band: int = 1, subdataset: str | None = None) -> RasterRead:
    """Read ``filepath`` ready to draw, reprojecting to lon/lat if needed.

    ``subdataset`` selects a variable inside a container file; when omitted and
    the file turns out to be a container, the first variable is used and the
    rest are reported on the result so the caller can offer a choice.
    """
    import numpy as np
    import rasterio
    from rasterio.vrt import WarpedVRT

    path = str(filepath)

    fmt = formats.format_for(path)
    if fmt is not None:
        available, reason = formats.availability(fmt)
        if not available:
            raise RasterFormatUnavailable(reason)

    missing = formats.missing_sidecars(path)
    if missing:
        raise RasterFormatUnavailable(formats.sidecar_message(path, missing))

    available_subdatasets: list[Subdataset] = []
    if subdataset:
        target = subdataset
    else:
        available_subdatasets = list_subdatasets(path)
        if available_subdatasets:
            target = available_subdatasets[0].path
            logger.info("%s is a container; showing %r of %d variables",
                        path, available_subdatasets[0].name,
                        len(available_subdatasets))
        else:
            target = path

    with rasterio.open(target) as src:
        if src.count == 0:
            raise ValueError(
                "This file contains no readable raster bands or variables."
            )

        source_crs = str(src.crs) if src.crs else None
        warped = _needs_warp(src)

        # A WarpedVRT presents the reprojected raster as if it were a normal
        # dataset, so the read path below is identical either way.
        context = WarpedVRT(src, crs=CANVAS_CRS, resampling=0) if warped else src
        try:
            reader = context
            step = _decimation(reader.width, reader.height)
            if step > 1:
                logger.info("Decimating %dx%d raster by %d for display",
                            reader.width, reader.height, step)
                shape = (reader.height // step, reader.width // step)
                data = reader.read(band, out_shape=shape, masked=True)
            else:
                data = reader.read(band, masked=True)

            bounds = reader.bounds
            transform = reader.transform
            width, height = reader.width, reader.height
            nodata = reader.nodata
        finally:
            if warped:
                context.close()

    if data.size == 0:
        raise ValueError("Raster contains no data.")

    # imshow wants a plain array; a fully-unmasked read is cheaper to draw as
    # one, and a masked read must keep its mask so nodata stays transparent.
    if np.ma.is_masked(data):
        data = np.ma.masked_invalid(data)
    else:
        data = np.asarray(data)

    # A file with no geotransform gets GDAL's identity matrix, whose bounds run
    # top=0 down to bottom=height. Passed on unchanged that is south > north,
    # which set_extent and imshow both read as an inverted axis. Ordering the
    # pair costs nothing when the transform is real, since north > south there
    # already.
    west, east = sorted((bounds.left, bounds.right))
    south, north = sorted((bounds.bottom, bounds.top))
    if source_crs is None:
        logger.warning(
            "%s has no CRS or geotransform; placing it at pixel coordinates "
            "%s. It will not line up with georeferenced layers.",
            path, [west, east, south, north])

    return RasterRead(
        data=data,
        extent=[west, east, south, north],
        crs=CANVAS_CRS,
        source_crs=source_crs,
        width=width,
        height=height,
        count=1,
        nodata=nodata,
        pixel_size_x=abs(transform.a),
        pixel_size_y=abs(transform.e),
        warped=warped,
        subdatasets=available_subdatasets,
    )
