"""Basemaps that work with no internet at all.

Two sources live here, and neither one may ever touch the network or import
:mod:`contextily` — that is the whole point of the module:

* :class:`NaturalEarthBackdrop` draws a real cartographic backdrop (ocean, land,
  lakes, rivers, coastline, borders) from the Natural Earth shapefiles sitting in
  Cartopy's data directory.
* :class:`MBTilesSource` reads a local ``.mbtiles`` archive and stitches the
  raster tiles covering an extent into a single image, in the exact
  ``(image, web-mercator extent)`` shape the contextily path already produces,
  so the canvas draw code needs no special case for it.

**Why the shapefiles are probed by hand.** Cartopy's ``NaturalEarthFeature``
silently downloads whatever it is missing on first draw. That is exactly the
behaviour an offline basemap must not have — on a machine with no route it
stalls the render thread until the request times out. Every feature here is
therefore added only after :func:`natural_earth_path` confirms the ``.shp`` is
already on disk; a missing one is dropped from the backdrop and reported, never
fetched.
"""

from __future__ import annotations

import io
import logging
import math
import os
import sqlite3
import sys

import numpy as np

logger = logging.getLogger(__name__)

# Web Mercator half-circumference: the edge of the projected world in metres.
MERCATOR_LIMIT = 20037508.342789244

# Natural Earth scales we are willing to use. 10m is deliberately absent: it is
# far heavier to draw, and several of its layers are not part of the set Cartopy
# caches on ordinary use, so asking for it is the most likely way to trip a
# download attempt.
SCALE_WIDE = "110m"
SCALE_CLOSE = "50m"

# Longitude span (degrees) below which the finer scale is worth its draw cost.
CLOSE_SCALE_SPAN = 90.0

# Refuse absurd stitches rather than allocating gigabytes for a typo'd zoom.
MAX_TILES = 400

# MBTiles ``format`` values that mean vector tiles. Rendering those needs a
# style sheet and a vector renderer, neither of which NCExplorer has.
VECTOR_FORMATS = frozenset({"pbf", "mvt", "pbf.gz", "vector"})


class OfflineBasemapError(Exception):
    """Any failure that should reach the user as a plain sentence."""


class VectorTilesUnsupported(OfflineBasemapError):
    """The archive holds vector tiles, which we cannot draw."""


# ----------------------------------------------------------------------
# Natural Earth backdrop
# ----------------------------------------------------------------------
#: ``(category, name, kind)`` per feature, drawn in this order — fills first,
#: then lines on top. ``kind`` picks the styling branch below.
NE_LAYERS = (
    ("physical", "ocean", "ocean"),
    ("physical", "land", "land"),
    ("physical", "lakes", "lake"),
    ("physical", "rivers_lake_centerlines", "river"),
    ("physical", "coastline", "coastline"),
    ("cultural", "admin_0_boundary_lines_land", "border"),
)

#: Fill and stroke per theme. Deliberately warmer and more differentiated than
#: the flat two-tone starting backdrop, so the offline map reads as a real
#: cartographic base rather than a placeholder.
NE_STYLE = {
    "light": {
        "ocean": dict(facecolor="#c8dcef", edgecolor="none"),
        "land": dict(facecolor="#f4eddc", edgecolor="none"),
        "lake": dict(facecolor="#c8dcef", edgecolor="#a9c4da", linewidth=0.2),
        "river": dict(facecolor="none", edgecolor="#8fb6d4", linewidth=0.4),
        "coastline": dict(facecolor="none", edgecolor="#6b7d8c", linewidth=0.5),
        "border": dict(facecolor="none", edgecolor="#b3a894", linewidth=0.4,
                       linestyle=(0, (4, 2))),
    },
    "dark": {
        "ocean": dict(facecolor="#0d1a24", edgecolor="none"),
        "land": dict(facecolor="#24292e", edgecolor="none"),
        "lake": dict(facecolor="#142b39", edgecolor="#1d3b4c", linewidth=0.2),
        "river": dict(facecolor="none", edgecolor="#2b5468", linewidth=0.4),
        "coastline": dict(facecolor="none", edgecolor="#5f727d", linewidth=0.5),
        "border": dict(facecolor="none", edgecolor="#4c545b", linewidth=0.4,
                       linestyle=(0, (4, 2))),
    },
}


#: Directory inside a PyInstaller bundle holding the staged Natural Earth
#: shapefiles. build.py::gather_natural_earth_data writes this layout; the two
#: names must agree.
BUNDLED_CARTOPY_DIR = "cartopy_data"


def _frozen_cartopy_dir() -> str | None:
    """The bundled Cartopy data directory, when running from a frozen build.

    ``cartopy.config['data_dir']`` points at a per-user *download* cache that
    only exists on a machine where Cartopy has already fetched something. A
    packaged app cannot rely on it and must not fetch, so build.py stages the
    shapefiles it needs into the bundle and this puts them on the search path.
    Mirrors the ``sys._MEIPASS`` pattern in resources/icons.py::resources_root.
    """
    if not getattr(sys, "frozen", False):
        return None
    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        return None
    return os.path.join(meipass, BUNDLED_CARTOPY_DIR)


def _cartopy_data_dirs() -> list[str]:
    """Directories Cartopy would read Natural Earth shapefiles from.

    The bundled copy is searched first: it is the only one guaranteed to exist
    on an end user's machine, and preferring it keeps a frozen app's backdrop
    identical everywhere. ``pre_existing_data_dir`` comes next — that is the
    documented way to point Cartopy at a read-only bundled copy, so a user who
    has set it still wins over the cache — with the ordinary per-user cache as
    the final fallback.
    """
    dirs = []

    frozen_dir = _frozen_cartopy_dir()
    if frozen_dir:
        dirs.append(frozen_dir)

    try:
        import cartopy
    except Exception:
        return dirs

    for key in ("pre_existing_data_dir", "data_dir"):
        try:
            value = cartopy.config.get(key)
        except Exception:
            value = None
        if value:
            dirs.append(str(value))
    return dirs


def natural_earth_path(category: str, name: str, scale: str) -> str | None:
    """Path to an already-downloaded Natural Earth shapefile, or None.

    Returning None rather than fetching is the contract that keeps this module
    offline; callers drop the layer instead.
    """
    filename = f"ne_{scale}_{name}.shp"
    for root in _cartopy_data_dirs():
        candidate = os.path.join(root, "shapefiles", "natural_earth", category, filename)
        if os.path.exists(candidate):
            return candidate
    return None


def natural_earth_available(scale: str = SCALE_WIDE) -> bool:
    """True when at least land and ocean are on disk for ``scale``.

    Those two carry the backdrop; without them there is nothing to show and the
    caller should say so rather than draw an empty map.
    """
    return all(
        natural_earth_path(category, name, scale) is not None
        for category, name in (("physical", "land"), ("physical", "ocean"))
    )


def missing_natural_earth_layers(scale: str = SCALE_WIDE) -> list[str]:
    """Names of the backdrop layers that are not cached at ``scale``."""
    return [
        name for category, name, _kind in NE_LAYERS
        if natural_earth_path(category, name, scale) is None
    ]


class NaturalEarthBackdrop:
    """The offline vector backdrop, and the artists it owns.

    One instance is held by the canvas. It is re-``draw``\\ n from scratch after
    a theme change, because that swaps the axes out from under every artist.
    """

    def __init__(self):
        self._artists: list = []
        self._scale: str | None = None

    @property
    def active(self) -> bool:
        return bool(self._artists)

    @property
    def scale(self) -> str | None:
        """Natural Earth scale currently drawn, or None when inactive."""
        return self._scale

    @staticmethod
    def scale_for_span(lon_span: float) -> str:
        """Pick a Natural Earth scale from how much longitude is on screen."""
        if lon_span <= CLOSE_SCALE_SPAN and natural_earth_available(SCALE_CLOSE):
            return SCALE_CLOSE
        return SCALE_WIDE

    def draw(self, ax, theme: str, zorder_base: int, scale: str = SCALE_WIDE) -> list[str]:
        """Draw the backdrop on ``ax``; return the names actually drawn.

        Every layer is independent: one unreadable shapefile costs that layer
        alone, not the whole backdrop.
        """
        self.clear()

        palette = NE_STYLE.get(theme) or NE_STYLE["light"]
        drawn: list[str] = []

        for offset, (category, name, kind) in enumerate(NE_LAYERS):
            path = natural_earth_path(category, name, scale)
            if path is None:
                logger.debug("Natural Earth layer %s (%s) not cached; skipping", name, scale)
                continue

            style = palette.get(kind)
            if style is None:
                continue

            try:
                artist = self._add_shapefile(ax, path, style, zorder_base + offset)
            except Exception as exc:
                logger.warning("Could not draw Natural Earth layer %s: %s", name, exc)
                continue

            if artist is not None:
                self._artists.append(artist)
                drawn.append(name)

        self._scale = scale if drawn else None
        return drawn

    @staticmethod
    def _add_shapefile(ax, path: str, style: dict, zorder: int):
        """Add one shapefile to the axes as a Cartopy feature."""
        import cartopy.crs as ccrs
        from cartopy.feature import ShapelyFeature
        from cartopy.io.shapereader import Reader

        # Natural Earth ships the occasional record with no geometry at all
        # (ne_50m_rivers_lake_centerlines has one). Cartopy only skips those on
        # its extent-filtered draw path; on the unfiltered one it tries to take
        # a weak reference to the None and raises mid-render, which matplotlib
        # then reports as a traceback from inside the event loop. Dropping them
        # here is the only place that reliably catches both paths.
        geometries = [
            geom for geom in Reader(path).geometries()
            if geom is not None and not geom.is_empty
        ]
        if not geometries:
            return None

        feature = ShapelyFeature(geometries, ccrs.PlateCarree(), **style)
        return ax.add_feature(feature, zorder=zorder)

    def set_visible(self, visible: bool) -> None:
        for artist in self._artists:
            try:
                artist.set_visible(visible)
            except Exception:
                pass

    def clear(self) -> None:
        """Remove every artist. Safe to call when the axes are already gone."""
        for artist in self._artists:
            try:
                artist.remove()
            except Exception:
                # Expected after a theme change: the old axes took its children
                # with it, so the artist is already detached.
                pass
        self._artists = []
        self._scale = None


# ----------------------------------------------------------------------
# Local MBTiles
# ----------------------------------------------------------------------
def _tile_x(lon: float, zoom: int) -> int:
    return int(math.floor((lon + 180.0) / 360.0 * (2 ** zoom)))


def _tile_y(lat: float, zoom: int) -> int:
    # Clamped to the Web Mercator latitude limit, past which the projection
    # diverges and the tile index runs off the grid.
    lat = max(-85.05112878, min(85.05112878, lat))
    lat_rad = math.radians(lat)
    return int(math.floor(
        (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * (2 ** zoom)
    ))


def _tile_bounds_mercator(x: int, y: int, zoom: int) -> tuple[float, float, float, float]:
    """``(west, east, south, north)`` of one XYZ tile, in Web Mercator metres."""
    span = 2.0 * MERCATOR_LIMIT / (2 ** zoom)
    west = -MERCATOR_LIMIT + x * span
    north = MERCATOR_LIMIT - y * span
    return west, west + span, north - span, north


def discover_mbtiles(directory: str) -> list[str]:
    """Every ``*.mbtiles`` in ``directory``, sorted. Never raises."""
    try:
        if not os.path.isdir(directory):
            return []
        return sorted(
            os.path.join(directory, entry)
            for entry in os.listdir(directory)
            if entry.lower().endswith(".mbtiles")
        )
    except Exception as exc:
        logger.warning("Could not list MBTiles in %s: %s", directory, exc)
        return []


class MBTilesSource:
    """A local MBTiles archive, opened read-only.

    Connections are opened per call rather than held, because stitching runs on
    the canvas thread pool and a :mod:`sqlite3` connection may not cross threads.
    """

    def __init__(self, path: str):
        self.path = path
        self.metadata: dict[str, str] = {}
        self.tile_format = ""
        self.attribution = ""
        self.name = os.path.splitext(os.path.basename(path))[0]
        self.minzoom: int | None = None
        self.maxzoom: int | None = None
        self._load_metadata()

    # -- opening ------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        """Open the archive read-only so a stray write can never corrupt it."""
        if not os.path.exists(self.path):
            raise OfflineBasemapError(f"MBTiles file not found: {self.path}")
        uri = "file:" + self.path.replace("?", "%3f").replace("#", "%23") + "?mode=ro"
        return sqlite3.connect(uri, uri=True, timeout=5.0)

    def _load_metadata(self) -> None:
        """Read the ``metadata`` table and validate that we can draw the tiles.

        Raises :class:`VectorTilesUnsupported` for a vector archive and
        :class:`OfflineBasemapError` for anything structurally wrong, so the
        failure is reported once at selection time instead of on every pan.
        """
        try:
            connection = self._connect()
        except OfflineBasemapError:
            raise
        except Exception as exc:
            raise OfflineBasemapError(f"Could not open MBTiles: {exc}") from exc

        try:
            with connection:
                try:
                    rows = connection.execute("SELECT name, value FROM metadata").fetchall()
                    self.metadata = {
                        str(key): str(value) for key, value in rows if key is not None
                    }
                except sqlite3.Error as exc:
                    # metadata is optional in practice; the tiles table is not.
                    logger.debug("MBTiles %s has no readable metadata: %s", self.path, exc)
                    self.metadata = {}

                try:
                    connection.execute("SELECT zoom_level FROM tiles LIMIT 1").fetchone()
                except sqlite3.Error as exc:
                    raise OfflineBasemapError(
                        f"'{os.path.basename(self.path)}' has no readable tiles table"
                    ) from exc
        finally:
            connection.close()

        self.tile_format = (self.metadata.get("format") or "").strip().lower()
        if self.tile_format in VECTOR_FORMATS:
            raise VectorTilesUnsupported(
                f"'{os.path.basename(self.path)}' holds vector tiles "
                f"(format={self.tile_format}); only raster MBTiles can be drawn"
            )

        self.attribution = (self.metadata.get("attribution") or "").strip()
        self.name = (self.metadata.get("name") or self.name).strip() or self.name
        self.minzoom = self._int_metadata("minzoom")
        self.maxzoom = self._int_metadata("maxzoom")

    def _int_metadata(self, key: str) -> int | None:
        raw = self.metadata.get(key)
        if raw is None:
            return None
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            return None

    # -- reading ------------------------------------------------------
    def clamp_zoom(self, zoom: int) -> int:
        """Pull ``zoom`` inside the range the archive actually contains."""
        if self.minzoom is not None:
            zoom = max(self.minzoom, zoom)
        if self.maxzoom is not None:
            zoom = min(self.maxzoom, zoom)
        return max(0, zoom)

    def fetch_extent(self, west: float, south: float, east: float, north: float,
                     zoom: int) -> tuple[np.ndarray, list[float]]:
        """Stitch the tiles covering a lon/lat box.

        Returns ``(rgba_image, [west, east, south, north])`` with the extent in
        Web Mercator metres — the same payload ``contextily.bounds2img``
        produces, so the caller draws both the same way.
        """
        from PIL import Image

        zoom = self.clamp_zoom(zoom)
        n_tiles = 2 ** zoom

        x_min = max(0, min(n_tiles - 1, _tile_x(west, zoom)))
        x_max = max(0, min(n_tiles - 1, _tile_x(east, zoom)))
        # Screen north is the *smaller* tile row, so the ordering flips here.
        y_min = max(0, min(n_tiles - 1, _tile_y(north, zoom)))
        y_max = max(0, min(n_tiles - 1, _tile_y(south, zoom)))

        if x_max < x_min:
            x_min, x_max = x_max, x_min
        if y_max < y_min:
            y_min, y_max = y_max, y_min

        columns = x_max - x_min + 1
        rows = y_max - y_min + 1
        if columns * rows > MAX_TILES:
            raise OfflineBasemapError(
                f"Extent needs {columns * rows} tiles at zoom {zoom}; refusing to stitch"
            )

        tiles = self._read_tiles(zoom, x_min, x_max, y_min, y_max, n_tiles)
        if not tiles:
            raise OfflineBasemapError(
                f"'{self.name}' has no tiles covering this area at zoom {zoom}"
            )

        # Tile size comes from the data: 512px archives are common and assuming
        # 256 would stitch them into a garbled mosaic.
        sample = next(iter(tiles.values()))
        tile_w, tile_h = sample.size

        mosaic = Image.new("RGBA", (columns * tile_w, rows * tile_h), (0, 0, 0, 0))
        for (x, y), image in tiles.items():
            if image.size != (tile_w, tile_h):
                image = image.resize((tile_w, tile_h), Image.BILINEAR)
            mosaic.paste(image, ((x - x_min) * tile_w, (y - y_min) * tile_h))

        left, _, _, top = _tile_bounds_mercator(x_min, y_min, zoom)
        _, right, bottom, _ = _tile_bounds_mercator(x_max, y_max, zoom)
        return np.asarray(mosaic), [left, right, bottom, top]

    def _read_tiles(self, zoom: int, x_min: int, x_max: int, y_min: int, y_max: int,
                    n_tiles: int) -> dict[tuple[int, int], "object"]:
        """Decode every stored tile in the window, keyed by XYZ ``(x, y)``.

        MBTiles rows are TMS-numbered — row 0 is the *south* edge — so the query
        window is flipped going in and each row is flipped back coming out.
        """
        from PIL import Image

        row_min = n_tiles - 1 - y_max
        row_max = n_tiles - 1 - y_min

        connection = self._connect()
        try:
            with connection:
                cursor = connection.execute(
                    "SELECT tile_column, tile_row, tile_data FROM tiles "
                    "WHERE zoom_level = ? AND tile_column BETWEEN ? AND ? "
                    "AND tile_row BETWEEN ? AND ?",
                    (zoom, x_min, x_max, row_min, row_max),
                )
                raw = cursor.fetchall()
        except sqlite3.Error as exc:
            raise OfflineBasemapError(f"Could not read tiles: {exc}") from exc
        finally:
            connection.close()

        tiles: dict[tuple[int, int], object] = {}
        for column, row, blob in raw:
            if not blob:
                continue
            try:
                image = Image.open(io.BytesIO(bytes(blob)))
                image.load()
                tiles[(int(column), n_tiles - 1 - int(row))] = image.convert("RGBA")
            except Exception as exc:
                # A single corrupt tile leaves a transparent square; the rest of
                # the mosaic is still worth showing.
                logger.debug("Skipping undecodable tile z%s x%s row%s: %s",
                             zoom, column, row, exc)
        return tiles
