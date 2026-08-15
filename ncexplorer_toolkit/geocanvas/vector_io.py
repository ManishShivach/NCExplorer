"""Reading vector files through geopandas, with a comprehensible failure.

geopandas 1.x chooses its I/O engine at call time: ``pyogrio`` first, then
``fiona``. The packaged build excludes pyogrio deliberately — its macOS arm64
wheels lack the Mach-O headerpad space PyInstaller needs to rewrite install
names, and pyinstaller-hooks-contrib ships no ``hook-pyogrio`` to work around
it — so the frozen app has exactly one engine: fiona.

That choice is not neutral, and this docstring used to say it was. The two
engines vendor **different GDAL builds**: pyogrio's carries 65 vector drivers
and fiona's 17, and KML, LIBKML and Idrisi are among the ones only pyogrio has.
KML therefore works in a source checkout and fails in the packaged app —
``fiona.open`` on a ``.kml`` raises ``DriverError: unsupported driver: 'KML'``.
:mod:`.formats` is what keeps that from reaching a user as a mystery: it probes
the running engine and refuses the format with a sentence naming pyogrio.

That also makes fiona a single point of failure. When neither engine is importable,
``gpd.read_file`` raises a bare ``ImportError`` naming both packages, which the
callers turn into "Failed to load shapefile: ..." — a message that tells a user
nothing they can act on. This module converts that one case into a sentence
that names the missing package and what to do about it, and leaves every other
failure (bad geometry, unreadable file, missing sidecar) exactly as it was.

Beyond that one message, this module owns the three things that stand between
"geopandas returned a GeoDataFrame" and "the features are in the right place on
the map". Each was a silent wrong answer before:

* **Reprojection.** The canvas draws with ``ccrs.PlateCarree()``, which means
  every coordinate handed to it is read as degrees. A layer in a projected CRS
  carries metres, so a UTM extent such as ``[-23539, 2219308, 1023539,
  3329330]`` was drawn as if those were longitudes and latitudes. The point
  path made it worse by filtering to ``-180..180``/``-90..90``, so a projected
  point layer lost every feature and reported "No valid coordinates found".
* **Layer choice.** GPX always has five layers (waypoints, routes, tracks,
  route_points, track_points) and GeoPackage and GML may have any number.
  ``read_file`` silently takes the first, so a GPX holding only tracks read as
  an empty waypoints layer — zero features, no error.
* **Mixed geometry.** KML and GeoJSON routinely mix points, lines and polygons
  in one file. Keying the draw off ``geometry.iloc[0].geom_type`` drew whichever
  kind happened to come first and dropped the rest without a word.
"""

from __future__ import annotations

import logging

from . import formats

logger = logging.getLogger(__name__)

#: Shown verbatim to the user, so it has to stand on its own.
ENGINE_MISSING_MESSAGE = (
    "Vector files cannot be read because no geopandas I/O engine is "
    "available.\n\n"
    "NCExplorer reads shapefiles, GeoJSON, GML, GeoPackage and GPX through "
    "'fiona'. It is missing from this installation.\n\n"
    "Running from source: install it with\n"
    "    pip install fiona\n\n"
    "Running a packaged build: the package is incomplete — please report "
    "this, as fiona should have been bundled."
)

#: The CRS the canvas draws in. Everything is converted to this before it
#: reaches an artist, because ``ccrs.PlateCarree()`` is what the transform says.
CANVAS_CRS = "EPSG:4326"


class VectorEngineMissing(RuntimeError):
    """geopandas has no usable I/O engine.

    A ``RuntimeError`` rather than an ``ImportError`` so that the broad
    ``except Exception`` handlers on the load paths surface ``str(exc)``
    unchanged instead of folding it into a generic import failure.
    """


class VectorFormatUnavailable(RuntimeError):
    """The format is known, but this installation cannot open it.

    Carries the user-facing explanation from :mod:`.formats` — which package is
    missing, or why the format cannot work at all.
    """


def read_vector_file(filepath, **kwargs):
    """``geopandas.read_file`` with a legible error when no engine exists."""
    import geopandas as gpd

    try:
        return gpd.read_file(filepath, **kwargs)
    except ImportError as exc:
        logger.error("geopandas has no usable I/O engine: %s", exc)
        raise VectorEngineMissing(ENGINE_MISSING_MESSAGE) from exc


def list_layers(filepath) -> list[str]:
    """Layer names inside ``filepath``, or ``[]`` when it has only one.

    Both engines can answer this, and neither is guaranteed present, so both
    are tried before giving up. Failure is not an error — a format with no
    layer concept simply has nothing to report, and the caller falls back to
    letting the engine pick.
    """
    target = formats.gdal_path(filepath)

    try:
        import pyogrio
        return [str(row[0]) for row in pyogrio.list_layers(target)]
    except Exception:
        pass

    try:
        import fiona
        return list(fiona.listlayers(target))
    except Exception:
        logger.debug("Could not enumerate layers in %s", filepath, exc_info=True)
        return []


def read_all_layers(filepath, **kwargs):
    """Every non-empty layer in ``filepath``, concatenated.

    Reading only the first layer is what made a tracks-only GPX open as zero
    features: ``waypoints`` exists in every GPX, is listed first, and is empty
    unless the file happens to carry waypoints. Concatenating instead means the
    canvas shows what the file actually contains.

    Layers are read independently and a failure in one is logged and skipped —
    one unreadable layer in a GeoPackage should not cost the user the others.
    """
    import geopandas as gpd
    import pandas as pd

    target = formats.gdal_path(filepath)
    names = list_layers(filepath)

    if len(names) <= 1:
        return read_vector_file(target, **kwargs)

    frames = []
    for name in names:
        try:
            frame = read_vector_file(target, layer=name, **kwargs)
        except VectorEngineMissing:
            raise
        except Exception:
            logger.debug("Skipping unreadable layer %r in %s", name, filepath,
                         exc_info=True)
            continue
        if len(frame):
            frames.append(frame)

    if not frames:
        # Nothing readable anywhere. Return the first layer so the caller's
        # "contains no features" message describes the file, not our slicing.
        return read_vector_file(target, layer=names[0], **kwargs)

    if len(frames) == 1:
        return frames[0]

    combined = pd.concat(frames, ignore_index=True)
    return gpd.GeoDataFrame(combined, geometry="geometry", crs=frames[0].crs)


def to_canvas_crs(gdf):
    """``gdf`` in :data:`CANVAS_CRS`, reprojecting when it has to.

    A layer with no CRS is *assumed* to already be lon/lat rather than
    reprojected, which is the only safe reading: there is no information to
    convert from. That assumption was already being made — this makes it
    explicit and leaves a warning behind.
    """
    if gdf.crs is None:
        logger.warning("Layer has no CRS; assuming %s", CANVAS_CRS)
        return gdf.set_crs(CANVAS_CRS, allow_override=True)

    try:
        if gdf.crs.to_epsg() == 4326:
            return gdf
    except Exception:
        pass

    try:
        return gdf.to_crs(CANVAS_CRS)
    except Exception:
        logger.warning("Could not reproject from %s; drawing as-is", gdf.crs,
                       exc_info=True)
        return gdf


#: Geometry type -> the draw path that handles it. ``GeometryCollection`` is
#: absent on purpose: it is exploded into its parts before this is consulted.
_GEOMETRY_GROUPS = {
    "Point": "points",
    "MultiPoint": "points",
    "LineString": "lines",
    "MultiLineString": "lines",
    "LinearRing": "lines",
    "Polygon": "polygons",
    "MultiPolygon": "polygons",
}


def split_by_geometry(gdf) -> dict[str, "object"]:
    """``gdf`` split into ``{"points"|"lines"|"polygons": sub-frame}``.

    Mixed-geometry files are the norm for KML and common for GeoJSON, and the
    canvas needs a different artist for each kind, so the split has to happen
    somewhere. Doing it here means every caller draws all of the file.

    ``GeometryCollection`` members are exploded first — they are containers,
    and their parts are what can be drawn.
    """
    empty = gdf[gdf.geometry.isna() | gdf.geometry.is_empty]
    usable = gdf.drop(empty.index)

    if len(usable) and (usable.geometry.geom_type == "GeometryCollection").any():
        usable = usable.explode(index_parts=False)
        usable = usable[~(usable.geometry.isna() | usable.geometry.is_empty)]

    groups = {}
    for group in ("points", "lines", "polygons"):
        mask = usable.geometry.geom_type.map(_GEOMETRY_GROUPS).eq(group)
        if mask.any():
            groups[group] = usable[mask]
    return groups


def open_vector(filepath):
    """Read ``filepath`` ready to draw: right CRS, all layers, all geometry.

    Raises :class:`VectorFormatUnavailable` when the format is registered but
    this installation cannot open it, so the caller can show the reason from
    :mod:`.formats` rather than GDAL's "not recognized as being in a supported
    file format".

    Returns ``(gdf, groups)`` where ``groups`` is the mapping from
    :func:`split_by_geometry`.
    """
    fmt = formats.format_for(filepath)
    if fmt is not None:
        available, reason = formats.availability(fmt)
        if not available:
            raise VectorFormatUnavailable(reason)

    missing = formats.missing_sidecars(filepath)
    if missing:
        raise VectorFormatUnavailable(formats.sidecar_message(filepath, missing))

    gdf = to_canvas_crs(read_all_layers(filepath))
    return gdf, split_by_geometry(gdf)
