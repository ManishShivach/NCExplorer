# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""Turning a polygon into something a grid — or CDO — can be masked with.

The statistics dock and the mask dialog both have to answer the same awkward
question: which cells of a lon/lat grid fall inside a shapefile polygon? They
answer it in different places (one in memory for a subset, one by handing CDO a
region file) but the geometry work in front of that is identical, so it lives
here once.

Three things make this harder than a point-in-polygon test:

* **Two longitude conventions.** A shapefile that has been through geopandas is
  −180…180; a climate file is just as often 0…360. Nothing warns you when they
  disagree — the mask simply comes back empty, or worse, selects the wrong side
  of the globe. :func:`to_longitude_convention` moves the polygon into whichever
  convention the *dataset* uses before anything else happens.
* **The seam.** Moving a polygon between conventions can cut it in half: a shape
  spanning −20…20 becomes 340…360 *and* 0…20 in a 0…360 file. It is split at the
  seam and returned as a multipart geometry rather than smeared into a box that
  wraps the wrong way round the world.
* **Optional accelerators.** Point-in-polygon over a global grid is a million
  tests. Shapely 2's vectorized ``contains_xy`` does it in one call; older
  shapely offers ``shapely.vectorized``; rasterio can rasterise the geometry
  instead. All three are tried in that order and a plain prepared-geometry loop
  is the floor, so masking always works even on a minimal install.

CDO's own operators are more forgiving than they look — ``maskregion`` and
``sellonlatbox`` both normalise longitudes themselves — but the conversion still
earns its place: it keeps the bounding box tight instead of global, and the
in-memory fallback path has no CDO to lean on.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Iterable

import numpy as np
from shapely.affinity import translate
from shapely.geometry import MultiPolygon, Polygon, box
from shapely.ops import unary_union

logger = logging.getLogger(__name__)

#: Coordinate names, in the order we would rather find them.
LAT_NAMES = ("lat", "latitude", "y", "nlat", "Latitude", "LATITUDE")
LON_NAMES = ("lon", "longitude", "x", "nlon", "long", "Longitude", "LONGITUDE")

#: Attribute columns that plausibly hold a human-readable feature name. Checked
#: case-insensitively and by substring, so ``NAME_EN`` and ``admin_name`` match.
NAME_COLUMN_HINTS = (
    "name", "nom", "nombre", "label", "title", "admin", "region", "state",
    "province", "district", "country", "city", "zone", "basin", "id",
)

#: Tolerance, in degrees, for "this piece touches the seam".
SEAM_TOLERANCE = 1e-6


class RegionError(Exception):
    """A region cannot be built, in a way the user should simply be told about."""


@dataclass(frozen=True)
class Feature:
    """One selectable polygon: a label for the combo box and its geometry."""

    name: str
    geometry: object


# ----------------------------------------------------------------------
# Coordinates
# ----------------------------------------------------------------------
def find_lat_lon(names: Iterable[str]) -> tuple[str | None, str | None]:
    """The latitude and longitude names among ``names``, case-insensitively."""
    lowered = {str(name).lower(): str(name) for name in names}
    lat = next((lowered[c.lower()] for c in LAT_NAMES if c.lower() in lowered), None)
    lon = next((lowered[c.lower()] for c in LON_NAMES if c.lower() in lowered), None)
    return lat, lon


def uses_360(values) -> bool:
    """True when a longitude coordinate runs 0…360 rather than −180…180.

    Decided on the maximum alone: a 0…360 file is the only one that carries
    longitudes above 180, and a regional file that happens to sit entirely east
    of Greenwich is correctly reported either way because both conventions
    describe it identically.
    """
    try:
        array = np.asarray(values, dtype=float)
        return bool(np.nanmax(array) > 180.0)
    except (TypeError, ValueError):
        return False


def align_longitude_bounds(west: float, east: float, values) -> tuple[float, float]:
    """Move a −180…180 longitude span into the convention ``values`` uses.

    Returns the pair unchanged when the file already matches. The returned span
    may come back "inverted" (``west > east``) for a box that crosses the seam
    of the target convention; callers must treat that as a wrap, not an error.
    """
    if not uses_360(values):
        return west, east
    return west % 360.0, east % 360.0


# ----------------------------------------------------------------------
# Features
# ----------------------------------------------------------------------
def _polygonal(geometry) -> bool:
    return getattr(geometry, "geom_type", "") in ("Polygon", "MultiPolygon")


def _name_column(frame) -> str | None:
    """The attribute column that most plausibly names each feature."""
    columns = [c for c in getattr(frame, "columns", []) if c != "geometry"]
    for hint in NAME_COLUMN_HINTS:
        for column in columns:
            if hint in str(column).lower():
                return column
    # No hint matched: any text column still reads better than "Feature 3".
    for column in columns:
        if getattr(frame[column], "dtype", None) == object:
            return column
    return None


def polygon_features(record, source: str | None = None) -> list[Feature]:
    """Every polygon in a canvas layer record, labelled for display.

    Handles both shapes the loaders produce: a GeoDataFrame (from the shapefile
    display manager) and a plain list of shapely geometries (from the canvas'
    own ``load_shapefile``). Non-polygonal features are skipped rather than
    raising — a mixed-geometry file still offers its polygons.

    The list form has already lost the attribute table, so its features can only
    be numbered. When ``source`` names the file they came from it is re-read for
    the names, which is the difference between choosing "Kenya" and choosing
    "Feature 27".
    """
    data = record.get("data") if isinstance(record, dict) else record

    if source and not (hasattr(data, "geometry") and hasattr(data, "columns")):
        frame = _read_frame(source)
        if frame is not None:
            data = frame

    if data is None:
        return []

    # A GeoDataFrame: use its attribute table for the labels, and reproject if
    # it somehow reached us in something other than degrees.
    if hasattr(data, "geometry") and hasattr(data, "columns"):
        frame = data
        try:
            if frame.crs is not None and frame.crs.to_epsg() != 4326:
                frame = frame.to_crs("EPSG:4326")
        except Exception as exc:
            logger.debug("Could not reproject a polygon layer to EPSG:4326: %s", exc)

        column = _name_column(frame)
        features = []
        for position, (_index, row) in enumerate(frame.iterrows()):
            geometry = row.geometry
            if geometry is None or geometry.is_empty or not _polygonal(geometry):
                continue
            label = str(row[column]) if column is not None else ""
            features.append(Feature(label.strip() or f"Feature {position + 1}", geometry))
        return features

    if isinstance(data, (list, tuple)):
        return [
            Feature(f"Feature {position + 1}", geometry)
            for position, geometry in enumerate(data)
            if geometry is not None and not geometry.is_empty and _polygonal(geometry)
        ]

    if _polygonal(data):
        return [Feature("Feature 1", data)]
    return []


def _read_frame(path: str):
    """Re-read a vector file for its attribute table, or None if that fails."""
    if not isinstance(path, str) or not os.path.exists(path):
        return None
    try:
        import geopandas as gpd

        return gpd.read_file(path)
    except Exception as exc:
        logger.debug("Could not re-read %s for its attributes: %s", path, exc)
        return None


def source_file(layer_property) -> str | None:
    """The file a layer was loaded from, as the property manager recorded it."""
    path = getattr(getattr(layer_property, "metadata", None), "source_file", None)
    return path if isinstance(path, str) and os.path.exists(path) else None


def layer_features(canvas, layer_name) -> list[Feature]:
    """Named polygons of one loaded layer.

    Takes the canvas duck-typed — anything with ``layers`` and a
    ``property_manager`` — so this module stays independent of the geocanvas
    package while every caller gets the same naming behaviour.
    """
    try:
        record = canvas.layers.get(layer_name)
    except Exception:
        return []
    if not record:
        return []

    try:
        properties = canvas.property_manager.get_layer_property(layer_name)
    except Exception:
        properties = None

    return polygon_features(record, source=source_file(properties))


def dissolve(features: Iterable[Feature]):
    """One geometry covering every feature, or None when there are none."""
    geometries = [f.geometry for f in features if f.geometry is not None]
    if not geometries:
        return None
    if len(geometries) == 1:
        return geometries[0]
    return unary_union(geometries)


# ----------------------------------------------------------------------
# Longitude convention
# ----------------------------------------------------------------------
def _polygonal_parts(geometry) -> list:
    """Only the areal parts of a geometry; slivers and stray lines dropped."""
    if geometry is None or geometry.is_empty:
        return []
    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, MultiPolygon):
        return [g for g in geometry.geoms if not g.is_empty]
    parts = []
    for part in getattr(geometry, "geoms", []):
        parts.extend(_polygonal_parts(part))
    return parts


def to_longitude_convention(geometry, want_360: bool):
    """Move ``geometry`` into the 0…360 or −180…180 window, splitting at the seam.

    Anything already inside the target window is returned untouched, so the
    common case costs one bounds check. Everything else is intersected with each
    360°-wide window the geometry can reach and translated into place, which
    both shifts a wholly out-of-range polygon and cuts a straddling one in two.
    """
    if geometry is None or geometry.is_empty:
        return geometry

    low = 0.0 if want_360 else -180.0
    high = low + 360.0

    min_x, _min_y, max_x, _max_y = geometry.bounds
    if low - SEAM_TOLERANCE <= min_x and max_x <= high + SEAM_TOLERANCE:
        return geometry

    pieces = []
    for start in (low - 360.0, low, low + 360.0):
        window = box(start, -91.0, start + 360.0, 91.0)
        clipped = geometry.intersection(window)
        parts = _polygonal_parts(clipped)
        if not parts:
            continue
        offset = low - start
        if offset:
            parts = [translate(part, xoff=offset) for part in parts]
        pieces.extend(parts)

    if not pieces:
        raise RegionError("The polygon has no area once its longitudes are normalised")
    return pieces[0] if len(pieces) == 1 else MultiPolygon(pieces)


def box_arguments(geometry, want_360: bool) -> tuple[float, float, float, float]:
    """``(lon1, lon2, lat1, lat2)`` for CDO's ``sellonlatbox``.

    A polygon split across the seam gets the wrapped form CDO understands
    (``lon1 > lon2``) rather than the full-globe span its raw bounds would
    imply, which is the difference between cropping a country and cropping
    nothing at all.
    """
    parts = _polygonal_parts(geometry)
    if not parts:
        raise RegionError("The polygon is empty")

    bounds = [part.bounds for part in parts]
    low = 0.0 if want_360 else -180.0
    high = low + 360.0

    south = min(b[1] for b in bounds)
    north = max(b[3] for b in bounds)

    touches_high = [b for b in bounds if b[2] >= high - 1e-3]
    touches_low = [b for b in bounds if b[0] <= low + 1e-3]
    if len(parts) > 1 and touches_high and touches_low:
        return (min(b[0] for b in touches_high),
                max(b[2] for b in touches_low),
                south, north)

    return (min(b[0] for b in bounds), max(b[2] for b in bounds), south, north)


# ----------------------------------------------------------------------
# Masking
# ----------------------------------------------------------------------
def _mask_shapely2(geometry, mesh_lon, mesh_lat):
    """Shapely 2's vectorized containment test — one call for the whole grid."""
    import shapely

    if not hasattr(shapely, "contains_xy"):
        return None
    try:
        shapely.prepare(geometry)
    except Exception:  # a geometry that refuses preparation still tests fine
        pass
    return np.asarray(shapely.contains_xy(geometry, mesh_lon, mesh_lat), dtype=bool)


def _mask_vectorized(geometry, mesh_lon, mesh_lat):
    """The pre-2.0 equivalent, kept for older shapely installs."""
    from shapely import vectorized

    return np.asarray(vectorized.contains(geometry, mesh_lon, mesh_lat), dtype=bool)


def _mask_rasterio(geometry, lats, lons):
    """Rasterise the geometry against a regular grid.

    Only usable on an evenly spaced grid, because an affine transform cannot
    describe anything else; irregular axes fall through to the point tests.
    """
    from rasterio.features import geometry_mask
    from rasterio.transform import from_origin

    if lats.ndim != 1 or lons.ndim != 1 or lats.size < 2 or lons.size < 2:
        return None

    dx = np.diff(lons)
    dy = np.diff(lats)
    if not (np.allclose(dx, dx[0]) and np.allclose(dy, dy[0])):
        return None

    x_step, y_step = float(dx[0]), float(dy[0])
    west = float(lons[0]) - x_step / 2.0
    north = float(lats[0]) - y_step / 2.0
    # from_origin wants the top-left corner and positive step sizes; a
    # south-to-north axis is rasterised flipped and turned back afterwards.
    flipped = y_step > 0
    if flipped:
        north = float(lats[-1]) + y_step / 2.0

    transform = from_origin(west, north, abs(x_step), abs(y_step))
    inside = ~geometry_mask([geometry], out_shape=(lats.size, lons.size),
                            transform=transform, invert=False)
    return inside[::-1] if flipped else inside


def _mask_loop(geometry, mesh_lon, mesh_lat):
    """The floor: a prepared-geometry point loop. Slow but always available."""
    from shapely.geometry import Point
    from shapely.prepared import prep

    prepared = prep(geometry)
    flat_lon = mesh_lon.ravel()
    flat_lat = mesh_lat.ravel()
    inside = np.fromiter(
        (prepared.contains(Point(x, y)) for x, y in zip(flat_lon, flat_lat)),
        dtype=bool, count=flat_lon.size,
    )
    return inside.reshape(mesh_lon.shape)


def grid_mask(geometry, lats, lons) -> np.ndarray:
    """A ``(len(lats), len(lons))`` boolean mask: True where a cell centre is inside.

    Cell *centres* are tested, which is the same rule CDO's ``maskregion``
    applies, so the in-memory and the CDO paths pick out the same cells. They
    can still disagree by one row or column when centres land exactly on the
    polygon's edge — the test here treats the boundary as outside — but that
    only happens on synthetic grids whose spacing lines up with the polygon's
    own coordinates.

    A polygon smaller than one grid cell can select nothing at all, so callers
    must check for an empty mask and say so rather than reporting statistics
    over zero cells.
    """
    lats = np.asarray(lats, dtype=float)
    lons = np.asarray(lons, dtype=float)

    if lats.ndim == 2 and lons.ndim == 2:
        mesh_lat, mesh_lon = lats, lons
    else:
        mesh_lon, mesh_lat = np.meshgrid(lons.ravel(), lats.ravel())

    for attempt in (_mask_shapely2, _mask_vectorized):
        try:
            mask = attempt(geometry, mesh_lon, mesh_lat)
        except Exception as exc:
            logger.debug("%s could not mask this grid: %s", attempt.__name__, exc)
            continue
        if mask is not None:
            return mask.reshape(mesh_lon.shape)

    if lats.ndim == 1 and lons.ndim == 1:
        try:
            mask = _mask_rasterio(geometry, lats, lons)
            if mask is not None:
                return mask
        except Exception as exc:
            logger.debug("rasterio could not mask this grid: %s", exc)

    logger.info("Falling back to the point-by-point mask over %d cells", mesh_lon.size)
    return _mask_loop(geometry, mesh_lon, mesh_lat)


# ----------------------------------------------------------------------
# CDO region files
# ----------------------------------------------------------------------
def exterior_rings(geometry) -> list[list[tuple[float, float]]]:
    """Closed exterior rings of a geometry, as ``(lon, lat)`` lists.

    Holes are deliberately dropped: CDO's region format has no way to express
    one, so an inner ring would be read as another region to *keep* — exactly
    inverting its meaning.
    """
    rings = []
    for part in _polygonal_parts(geometry):
        coordinates = [(float(x), float(y)) for x, y in part.exterior.coords]
        if len(coordinates) < 3:
            continue
        if coordinates[0] != coordinates[-1]:
            coordinates.append(coordinates[0])
        rings.append(coordinates)
        if part.interiors:
            logger.info("Dropping %d hole(s) from a region: region files cannot "
                        "express them", len(part.interiors))
    return rings


def write_region_file(geometry, path: str) -> str:
    """Write a geometry as a CDO ASCII region file and return the path.

    One ``lon lat`` pair per line, a blank line between polygons — the format
    ``maskregion`` reads. A MultiPolygon becomes consecutive blocks, which is
    also how a polygon split at the antimeridian arrives here.
    """
    rings = exterior_rings(geometry)
    if not rings:
        raise RegionError("The polygon has no usable outline to mask with")

    with open(path, "w", encoding="utf-8") as handle:
        for index, ring in enumerate(rings):
            if index:
                handle.write("\n")
            for lon, lat in ring:
                handle.write(f"{lon:.6f} {lat:.6f}\n")

    logger.debug("Wrote a %d-polygon region file to %s", len(rings), path)
    return path
