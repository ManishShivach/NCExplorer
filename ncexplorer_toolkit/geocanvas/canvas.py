"""
Main GeoCanvas implementation for NCExplorer visualization
"""

import os
import copy
import math
import time
import logging
import warnings
import functools
from typing import cast
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import geopandas as gpd
import xarray as xr
import cartopy.crs as ccrs
import cartopy.feature as cfeature
from cartopy.mpl.geoaxes import GeoAxes

from PyQt6.QtCore import pyqtSignal, Qt, QPoint, QTimer, QMutex, QMutexLocker, QObject
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QCursor, QAction
from PyQt6.QtWidgets import QFileDialog, QMenu, QMessageBox
import matplotlib.patheffects as path_effects
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.patches import Polygon as MPLPolygon, Rectangle
from matplotlib.collections import PatchCollection, LineCollection
from matplotlib.widgets import RectangleSelector

from .properties import LayerPropertyManager, LayerProperty, NetCDFProperties, find_case_insensitive_key
from .symbology import SymbologyManager
from .layers import LayerCache
from .colorbar import ColorbarManager
from .scalebar import ScaleBarManager
from . import colormaps as colormap_registry
from .offline_basemap import (
    MBTilesSource, NaturalEarthBackdrop, VectorTilesUnsupported, natural_earth_available,
)
from . import formats
from . import projections
from .vector_io import VectorFormatUnavailable, open_vector
from .raster_io import RasterFormatUnavailable, open_raster
from .basemap_sources import (
    S2_CLOUDLESS_LABEL, STATIC_SOURCES, resolve, sentinel2_cloudless,
)
from ..utils.timeaxis import read_time_axis

logger = logging.getLogger(__name__)

cache_key: str = ""

def monitor_performance(func):
    """Performance monitoring decorator with caching."""
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        global cache_key
        start_time = time.perf_counter()

        # Create a cache key for methods that can be cached
        if hasattr(self, '_method_cache') and func.__name__ in ['get_layer_info', 'get_zoom_info']:
            cache_key = f"{func.__name__}_{hash(str(args))}"
            if cache_key in self._method_cache:
                return self._method_cache[cache_key]

        result = func(self, *args, **kwargs)
        end_time = time.perf_counter()

        # Cache result if applicable
        if hasattr(self, '_method_cache') and func.__name__ in ['get_layer_info', 'get_zoom_info']:
            self._method_cache[cache_key] = result

        logger.debug("%s executed in %.4fs", func.__name__, end_time - start_time)
        return result
    return wrapper

def error_handler(func):
    """Error handling decorator for robust operation."""
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        except Exception as e:
            error_msg = f"Error in {func.__name__}: {str(e)}"
            logger.error("Error in %s: %s", func.__name__, e, exc_info=True)
            if hasattr(self, 'loading_error'):
                self.loading_error.emit(func.__name__, error_msg)
            return None
    return wrapper

class GeoCanvas(FigureCanvas):
    """Main NCExplorer canvas widget."""

    # Signals
    map_clicked = pyqtSignal(float, float)
    layer_added = pyqtSignal(str)
    layer_removed = pyqtSignal(str)
    extent_changed = pyqtSignal(list)
    file_loaded = pyqtSignal(str, str)
    layer_properties_requested = pyqtSignal(str)
    zoom_limit_reached = pyqtSignal(str)
    file_already_loaded = pyqtSignal(str)
    loading_error = pyqtSignal(str, str)
    progress_update = pyqtSignal(int)
    status_update = pyqtSignal(str)
    variable_changed = pyqtSignal(str, str)  # layer_name, variable_name
    time_index_changed = pyqtSignal(str, int)  # layer_name, time_index
    basemap_ready = pyqtSignal(object, object, int)  # tile image, mercator extent, request id
    basemap_failed = pyqtSignal(str, int)  # error message, request id
    # lat, lon, value-under-cursor (None where there is no sampleable data).
    # The canvas only reports the reading; main_window owns how it is displayed.
    cursor_position_changed = pyqtSignal(float, float, object)
    cursor_left = pyqtSignal()  # pointer left the map; clear any readout
    # A rubber-band box drawn on the map, as west, south, east, north. The canvas
    # reports the box and forgets it; whoever armed the selection owns what it
    # means (see begin_region_selection).
    region_selected = pyqtSignal(float, float, float, float)

    # Background layering (kept below data: rasters draw at zorder 0, vectors at
    # 5/7/10). The land/ocean fill is the lowest backdrop; basemap tiles sit just
    # above it but still beneath every data layer.
    BACKDROP_ZORDER = -20
    BASEMAP_ZORDER = -10
    # The offline Natural Earth backdrop stacks its six layers upward from here,
    # so the whole set stays under BASEMAP_ZORDER and far under the data.
    NATURAL_EARTH_ZORDER = -19

    # Selector entries the offline sources answer to. main_window builds the
    # combo from these, so they are declared here with the code that reads them.
    OFFLINE_NATURAL_EARTH = "Offline (Natural Earth)"
    MBTILES_PREFIX = "MBTiles: "

    # Minimum gap between cursor_position_changed emissions (~30 Hz).
    HOVER_MIN_INTERVAL = 1.0 / 30.0

    # Layer types that carry a sampleable value / colour scale.
    SCALAR_LAYER_TYPES = ('netcdf', 'raster')

    def __init__(self, parent=None, width=12, height=8, dpi=100):
        """Initialize Enhanced GeoCanvas with comprehensive property management."""
        # Create a matplotlib figure
        self.fig = Figure(figsize=(width, height), dpi=dpi, facecolor='white')
        # Remove all margins and padding from the figure
        self.fig.subplots_adjust(left=0, bottom=0, right=1, top=1, wspace=0, hspace=0)

        super().__init__(self.fig)
        self.setParent(parent)

        # Remove any widget margins
        self.setContentsMargins(0, 0, 0, 0)

        # Enable drag and drop
        self.setAcceptDrops(True)

        # Initialize managers
        self.property_manager = LayerPropertyManager(self)
        self.symbology_manager = SymbologyManager(self.property_manager, canvas=self)

        # Layer management (moved from LayerManager)
        self.layers = {}  # matplotlib artists
        self.layer_order = []  # Draw order
        self.loaded_files = set()
        self._layer_cache = LayerCache(max_size=50)
        self._z_order_counter = 1

        # Map properties. The projection is a registry entry (see
        # geocanvas/projections.py), not a free-form CRS: the name is what a
        # project file stores and what the selector shows, and the parameters
        # are derived from the extent rather than asked for. They are kept
        # beside the CRS because the axes box a conic can be given follows the
        # cutoff baked into the *live* CRS, not whatever the current view would
        # imply — see _apply_axes_extent.
        self.projection_name = projections.DEFAULT_PROJECTION
        self.projection = ccrs.PlateCarree()
        self._projection_params = {}
        # add_subplot(projection=...) returns a cartopy GeoAxes at runtime, but
        # matplotlib types it as Axes/Axes3D; annotate so type checkers know the
        # cartopy-specific API (set_extent/add_feature/gridlines/…) is available.
        self.ax: GeoAxes = cast(GeoAxes, None)
        self.extent = [-180, 180, -90, 90]
        self.theme = 'light'

        # Enhanced zoom and extent constraints
        self.max_extent = [-180, 180, -90, 90]
        self.min_zoom_extent = [360, 180]
        self.max_zoom_extent = [0.001, 0.001]
        self.aspect_ratio = width / height
        self.maintain_aspect_ratio = True
        self.zoom_history = []
        self.max_zoom_history = 20

        # Enhanced mouse control
        self.mouse_mode = 'pan'
        self.pan_enabled = True
        self.zoom_enabled = True
        self.press_event = None
        self.last_extent = None
        self.drag_threshold = 5

        # Keyboard navigation. The canvas-scoped shortcuts (arrows, Backspace)
        # only reach us while the canvas holds focus, so it has to accept it.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Set while walking back through zoom_history, so set_extent() knows not
        # to record the extent it is restoring; see zoom_previous().
        self._restoring_extent = False
        self._nav_overlay = None

        # File format support, from the one registry rather than restated here.
        # These lists used to be written by hand and were wrong in both
        # directions: they claimed .kml and .gpx, which load_file would have
        # refused, and .png/.jpg, which nothing ever drew.
        self.supported_vector_formats = list(formats.all_extensions(formats.VECTOR))
        self.supported_raster_formats = list(
            formats.all_extensions(formats.RASTER) + formats.all_extensions(formats.NETCDF)
        )
        self.max_file_size = 500 * 1024 * 1024

        # Performance monitoring
        self.performance_stats = {
            'render_times': [],
            'load_times': [],
            'update_times': []
        }

        # Thread management
        self.thread_pool = ThreadPoolExecutor(max_workers=4)
        self._render_lock = QMutex()
        self._method_cache = {}

        # Open-source basemap (XYZ tiles) state and wiring
        self._init_basemap()

        # Map overlays. Created before setup_map() so the theme pass it triggers
        # finds them; both start hidden and their refresh() is a no-op until the
        # user turns them on.
        self.colorbar_manager = ColorbarManager(self)
        self.scalebar_manager = ScaleBarManager(self)
        self._gridliner = None
        self._graticule_visible = False

        # Hover readout throttling — motion_notify_event fires far faster than
        # a Qt label can usefully be updated (see _update_hover_readout).
        self._hover_last_emit = 0.0

        # Rubber-band region selection and swipe comparison. Both temporarily
        # take over part of the canvas and must hand it back untouched, so each
        # remembers exactly what it changed: the pan flag it suppressed, and
        # every artist it clipped.
        self._region_selector = None
        self._region_pan_enabled = True
        self._swipe_clipped = []
        self._swipe_divider = None

        # Initialize the map
        self.setup_map()
        self.connect_events()
        self.setup_mouse_controls()
        self.connect_property_signals()
        self.remove_axis_borders()

        # Context menu support
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)

        # Setup update timers
        self.setup_update_timers()

    def setup_update_timers(self):
        """Setup timers for various update operations."""
        # Cache cleanup timer
        self.cache_cleanup_timer = QTimer()
        self.cache_cleanup_timer.timeout.connect(self._cleanup_caches)
        self.cache_cleanup_timer.start(30000)

        # Performance monitoring timer
        self.performance_timer = QTimer()
        self.performance_timer.timeout.connect(self._update_performance_stats)
        self.performance_timer.start(5000)

    def _cleanup_caches(self):
        """Cleanup old cache entries."""
        if len(self._method_cache) > 100:
            self._method_cache.clear()

    def _update_performance_stats(self):
        """Update performance statistics."""
        max_entries = 100
        for key in self.performance_stats:
            if len(self.performance_stats[key]) > max_entries:
                self.performance_stats[key] = self.performance_stats[key][-max_entries:]

    def connect_property_signals(self):
        """Connect property management signals with enhanced error handling."""
        try:
            # Property manager signals
            self.property_manager.property_changed.connect(self.on_property_changed)
            self.property_manager.layer_added.connect(self.on_layer_property_added)
            self.property_manager.layer_removed.connect(self.on_layer_property_removed)

            # Symbology manager signals
            self.symbology_manager.symbology_changed.connect(self.update_layer_display)

            self._connect_overlay_signals()

        except Exception as e:
            logger.error("Error connecting property signals: %s", e, exc_info=True)

    def _connect_overlay_signals(self):
        """Keep the colorbar and scale bar in step with the layer state.

        The colorbar describes one layer's colour scale, so anything that can
        change which layer that is, or what its scale looks like, has to trigger
        a rebuild. The scale bar only depends on the visible extent.
        """
        self.layer_added.connect(lambda _name: self.colorbar_manager.refresh())
        self.layer_removed.connect(lambda _name: self.colorbar_manager.refresh())
        self.variable_changed.connect(lambda _name, _var: self.colorbar_manager.refresh())
        self.time_index_changed.connect(lambda _name, _idx: self.colorbar_manager.refresh())
        self.symbology_manager.symbology_changed.connect(
            lambda _name: self.colorbar_manager.refresh()
        )
        self.extent_changed.connect(lambda _extent: self.scalebar_manager.refresh())

    def on_property_changed(self, layer_name: str, property_path: str, value):
        """Handle property changes with optimized updates."""
        logger.debug("Property changed: %s.%s = %s", layer_name, property_path, value)

        # Update layer display if it's a visual property
        visual_properties = ['style.', 'visible', 'transparency']
        if any(prop in property_path for prop in visual_properties):
            self.update_layer_display(layer_name)

    def on_layer_property_added(self, layer_name: str):
        """Trace a new property entry. Deliberately does not emit layer_added.

        Every loader creates the property entry *before* building the artist, so
        this fires on a half-built layer: it is not in ``self.layers`` yet, its
        extent is unset, and the layer-list slot bails out on it. The loaders emit
        ``layer_added`` themselves once the layer is complete, which is the copy
        worth acting on — re-emitting here only duplicated it, prematurely.
        """
        logger.debug("Layer property added: %s", layer_name)

    def on_layer_property_removed(self, layer_name: str):
        """Handle layer property removal."""
        logger.debug("Layer property removed: %s", layer_name)
        self.layer_removed.emit(layer_name)

    def set_projection(self, name):
        """Draw the map in a different projection. Returns the name really used.

        Not always the one asked for: a CRS that will not construct with the
        parameters this extent implies degrades to PlateCarree rather than
        taking the window down, and so does a name from a build with more
        projections than this one. The caller is expected to follow the return
        value — a selector still showing the request would be lying about the
        map.

        The lon/lat extent is untouched by the switch. It is the canonical
        record of what is being looked at; only the axes' own coordinates
        change, and they are metres in every projection but the default.
        """
        crs, used = projections.build(name, self.extent)

        self.projection = crs
        self.projection_name = used
        self._projection_params = projections.derive(used, self.extent)

        self._rebuild_axes()
        self._warn_if_tiles_are_stretched()

        if used != name:
            self.status_update.emit(f"'{name}' is not available — showing {used}")
        else:
            self.status_update.emit(f"Projection: {used}")
        return used

    def _warn_if_tiles_are_stretched(self):
        """Say so when XYZ tiles are being warped into an unfriendly projection.

        A hint, not a restriction: the tiles are Web Mercator and Cartopy will
        reproject them into anything, but the cost and the blurring both climb
        steeply away from a cylindrical map. The provider stays selectable.
        """
        if getattr(self, '_basemap_kind', 'none') != 'xyz':
            return
        if self.projection_name in ('PlateCarree', 'Mercator'):
            return
        self.status_update.emit(
            f"Basemap tiles are reprojected into {self.projection_name} — "
            "slower to draw and softer than in a cylindrical projection"
        )

    @monitor_performance
    def _rebuild_axes(self):
        """Build the map again on a brand new axes, keeping everything on it.

        The one path a projection change and a theme change both take, because
        both throw the axes away: the projection is fixed at construction and a
        theme is half a dozen artists that were added to the old one. Two paths
        would drift, and the symptom of drift is a layer that quietly stops
        being drawn.

        The order is not arbitrary. The extent goes on before the layers,
        because Cartopy warps an image the moment imshow receives it, into
        whatever extent the axes has *then* — redrawing first and setting the
        extent afterwards resamples every layer across the whole world and then
        crops, losing exactly the detail being looked at. The overlays go on
        last, because the colorbar reads the layer artists that have to exist
        by then.
        """
        # fig.clear() rather than ax.clear(): add_subplot has not reused an
        # existing subplot since matplotlib 3.6, so clearing only the axes
        # stacks one more per rebuild and every redraw afterwards is slower
        # than the last.
        self.fig.clear()
        self.ax = cast(GeoAxes, self.fig.add_subplot(111, projection=self.projection))

        self._style_axes()
        self._apply_axes_extent()
        self._draw_theme_features()
        self._restore_basemap()
        self._redraw_layers()
        # After the layers, not only before: imshow sets the axes aspect to
        # 'equal' whether or not it was asked to, so drawing a raster silently
        # undoes the choice _style_axes made.
        self._apply_aspect()
        self._rebuild_overlays()

        self.draw()

    def _apply_aspect(self):
        """Fill the widget, or keep the projection's own shape — as it needs.

        A globe and a polar cap have an outline of their own, and stretching
        that to the widget would simply draw a false map, so they keep an equal
        aspect and whatever margin the widget's shape leaves them.

        Everything else stretches to fill the axes box rather than preserving a
        fixed geographic aspect. A GeoAxes defaults to aspect='equal', which
        shrinks the box (leaving white margins) whenever the zoomed extent's
        ratio differs from the canvas' pixel ratio. 'auto' keeps the box full;
        the extent constraint (see _constrain_extent / resizeEvent) tracks the
        real widget ratio, so proportions stay correct instead of stretching.
        """
        if self.ax is None:
            return
        spec = projections.spec(self.projection_name)
        self.ax.set_aspect(1.0 if (spec is not None and spec.fixed_shape) else 'auto')

    def _style_axes(self):
        """Padding, borders and outline — everything not the map itself."""
        # Ensure no padding
        self.ax.set_position((0, 0, 1, 1))
        self.fig.subplots_adjust(left=0, bottom=0, right=1, top=1, wspace=0, hspace=0)

        spec = projections.spec(self.projection_name)
        self._apply_aspect()

        # A polar stereographic is a circle cut at one latitude, not a box.
        if spec is not None and spec.polar:
            try:
                self.ax.set_boundary(projections.CIRCULAR_BOUNDARY,
                                     transform=self.ax.transAxes)
            except Exception as exc:
                logger.warning("Could not set the circular map boundary: %s", exc)

        # Remove all borders and axis elements
        self.ax.spines['top'].set_visible(False)
        self.ax.spines['right'].set_visible(False)
        self.ax.spines['bottom'].set_visible(False)
        self.ax.spines['left'].set_visible(False)
        self.ax.set_xticks([])
        self.ax.set_yticks([])

    def _apply_axes_extent(self):
        """Show ``self.extent`` in whatever projection is current.

        The registry works out what the axes can actually be given — the globe
        for a whole-world Robinson, the full circle for a polar cap, a box
        pulled inside Mercator's latitude limits — from the parameters the live
        CRS was built with. Anything it will not accept falls back to the whole
        globe rather than leaving the axes in a state Cartopy cannot draw.
        """
        if self.ax is None:
            return  # too early in construction; setup_map() will come back

        box = projections.axes_extent(
            self.projection_name, self.extent, self._projection_params
        )
        try:
            if box is None:
                self.ax.set_global()
            else:
                self.ax.set_extent(box, crs=ccrs.PlateCarree())
        except Exception as exc:
            logger.warning("Could not show extent %s in %s: %s",
                           self.extent, self.projection_name, exc)
            try:
                self.ax.set_global()
            except Exception:
                pass

    def apply_theme(self, theme=None):
        """Apply a visual theme to the map with enhanced styling and no padding."""
        if theme:
            self.theme = theme

        self._rebuild_axes()

    def _draw_theme_features(self):
        """Add the theme's own artists to the axes that was just built."""
        # Enhanced theme styling. The land/ocean fill is the *starting backdrop*:
        # it fills the map before any basemap is chosen. When a real basemap is
        # active its tiles replace this fill entirely (the fill is hidden — see
        # _set_backdrop_visible), so the two never show at once. Coastlines/
        # borders stay just above the data as reference in either mode.
        #
        # LAND and OCEAN are fills and take ``color``; COASTLINE and BORDERS are
        # strokes and take ``edgecolor``. The distinction is not cosmetic:
        # Cartopy defines those two features with ``facecolor='never'``, and
        # ``color`` sets face *and* edge, so asking for a face colour they are
        # built to refuse warned "facecolor will have no effect as it has been
        # defined as 'never'" on every single draw. The lines were always drawn
        # in the edge colour regardless, so this changes the log and nothing on
        # the map.
        if self.theme == 'light':
            land = self.ax.add_feature(cfeature.LAND, color='#f5f5f5', alpha=0.8, zorder=self.BACKDROP_ZORDER)
            ocean = self.ax.add_feature(cfeature.OCEAN, color='#e6f3ff', alpha=0.8, zorder=self.BACKDROP_ZORDER)
            self.ax.add_feature(cfeature.COASTLINE, edgecolor='#666666', linewidth=0.5, zorder=1)
            self.ax.add_feature(cfeature.BORDERS, edgecolor='#999999', linewidth=0.3, zorder=1)
            self.fig.patch.set_facecolor('white')
        elif self.theme == 'dark':
            land = self.ax.add_feature(cfeature.LAND, color='#2d2d2d', alpha=0.9, zorder=self.BACKDROP_ZORDER)
            ocean = self.ax.add_feature(cfeature.OCEAN, color='#1a1a1a', alpha=0.9, zorder=self.BACKDROP_ZORDER)
            self.ax.add_feature(cfeature.COASTLINE, edgecolor='#cccccc', linewidth=0.5, zorder=1)
            self.ax.add_feature(cfeature.BORDERS, edgecolor='#888888', linewidth=0.3, zorder=1)
            self.fig.patch.set_facecolor('#1a1a1a')
            self.ax.set_facecolor('#1a1a1a')
        else:
            land = ocean = None

        # Track the land/ocean fill so it can be hidden while tiles are shown.
        self._backdrop_artists = [a for a in (land, ocean) if a is not None]

    def _restore_basemap(self):
        """Put the selected basemap back onto the axes that was just built.

        The axes was recreated, so any prior tile/attribution artists are gone —
        drop the stale handles. If a basemap is active, keep the backdrop hidden
        and re-fetch the tiles for the new axes.
        """
        self._basemap_artist = None
        self._basemap_attr_text = None
        kind = getattr(self, '_basemap_kind', 'none')
        if kind == 'natural_earth':
            # The vector artists belonged to the discarded axes, so this is a
            # full redraw rather than a refresh — and it re-reads the palette,
            # which is the whole reason a theme change comes through here.
            self._ne_backdrop.clear()
            self._activate_natural_earth()
        elif kind != 'none':
            self._set_backdrop_visible(False)
            self._request_basemap_refresh()

    def _rebuild_overlays(self):
        """Re-create colorbar, scale bar and graticule after the axes changes.

        _rebuild_axes() swaps ``self.ax`` for a fresh GeoAxes, which orphans
        every overlay artist; clearing first keeps the stale handles from
        piling up.
        """
        colorbar = getattr(self, 'colorbar_manager', None)
        scalebar = getattr(self, 'scalebar_manager', None)
        if colorbar is None or scalebar is None:
            return  # called during construction, before the managers exist

        colorbar.clear()
        scalebar.clear()

        # The new axes carries no gridliner, so drop the stale handle before
        # re-installing (otherwise set_graticule would try to remove it).
        self._gridliner = None
        if self._graticule_visible:
            self._install_graticule()

        colorbar.refresh()
        scalebar.refresh()

    # ------------------------------------------------------------------
    # Putting the layers back after an axes rebuild
    # ------------------------------------------------------------------
    def _redraw_layers(self):
        """Draw every layer again on the axes that was just built.

        Signals are blocked for the duration. Nothing here is a new layer —
        each one is the same layer on a different axes — and layer_added would
        otherwise announce the whole map afresh on every projection change,
        leaving the layer list with a duplicate of everything.
        """
        if not self.layers:
            return

        was_blocked = self.signalsBlocked()
        self.blockSignals(True)
        try:
            for name, record in list(self.layers.items()):
                try:
                    self._redraw_layer(name, record)
                except Exception as exc:
                    logger.warning("Could not redraw layer '%s': %s", name, exc,
                                   exc_info=True)
        finally:
            self.blockSignals(was_blocked)

    def _redraw_layer(self, name, record):
        """Re-create one layer's artist, keeping the record it already has."""
        kind = record.get('type')
        previous = record.get('artist')

        if kind in self.SCALAR_LAYER_TYPES:
            artist = self._draw_scalar_layer(record)
        elif kind in ('points', 'lines', 'polygons'):
            drawer = {'points': self.add_points,
                      'lines': self.add_lines,
                      'polygons': self.add_polygons}[kind]
            # Vectors go back through the loader that drew them the first time,
            # which is also what re-applies their symbology. It replaces the
            # whole record with a fresh one, though, and that would drop
            # everything the layer has accumulated since it loaded — visibility
            # above all — so only the artist it made is taken from it.
            drawer(record.get('data'), layer_name=name)
            artist = (self.layers.get(name) or {}).get('artist')
        else:
            return

        record['artist'] = artist
        self.layers[name] = record
        if artist is None:
            return

        artist.set_visible(bool(record.get('visible', True)))
        try:
            if previous is not None:
                artist.set_zorder(previous.get_zorder())
        except Exception as exc:
            logger.debug("Could not carry a layer's z-order across: %s", exc)

    def _set_layer_array(self, layer_name, record, array):
        """Show a different 2-D array — another time step — on an existing layer.

        Under the default projection the artist holds the file's own array and
        can simply be handed the new one. Under any other, Cartopy warped the
        array at imshow time and the artist's grid no longer matches the file's,
        so set_data would draw a mangled image: the layer is drawn again
        instead. Either way the record keeps the source array, because that is
        what the next projection change redraws from.
        """
        record['array'] = array

        artist = record.get('artist')
        if artist is not None and self.projection_name == projections.DEFAULT_PROJECTION:
            artist.set_data(array)
            return

        self._redraw_layer(layer_name, record)
        self._rebuild_overlays()

    def _draw_scalar_layer(self, record):
        """imshow a raster/NetCDF layer again, from the array the file gave us.

        Not from the old artist's array: that holds whatever the last projection
        warped, and re-warping a warped array compounds the resampling until the
        data on screen is no longer the data in the file.
        """
        array = record.get('array')
        bounds = self.layer_lonlat_bounds(record)
        if array is None or bounds is None:
            return None

        previous = record.get('artist')
        # Still on the live axes when this is a redraw in place (a new time
        # step) rather than a rebuild; an axes swap has already orphaned it.
        if previous is not None and getattr(previous, 'axes', None) is self.ax:
            try:
                previous.remove()
            except Exception as exc:
                logger.debug("Could not remove the superseded layer artist: %s", exc)

        style = {'alpha': 0.8, 'cmap': 'viridis', 'zorder': None, 'clim': None}
        if previous is not None:
            try:
                style = {
                    'alpha': previous.get_alpha(),
                    'cmap': previous.get_cmap(),
                    'zorder': previous.get_zorder(),
                    'clim': previous.get_clim(),
                }
            except Exception as exc:
                logger.debug("Could not read back a layer's style: %s", exc)

        image = self.ax.imshow(
            array, extent=list(bounds), transform=ccrs.PlateCarree(),
            origin=record.get('origin', 'lower'),
            alpha=style['alpha'], cmap=style['cmap'],
        )
        if style['zorder'] is not None:
            image.set_zorder(style['zorder'])
        if style['clim'] is not None:
            image.set_clim(*style['clim'])
        return image

    # ------------------------------------------------------------------
    # Open-source basemaps (XYZ tiles via contextily)
    # ------------------------------------------------------------------
    def _init_basemap(self):
        """Initialize open-source basemap state, caching, and async wiring."""
        self._basemap_name = "None"
        self._basemap_provider = None       # xyzservices TileProvider, or None
        self._basemap_provider_map = None   # lazily-built {name: provider}
        self._basemap_artist = None         # matplotlib AxesImage for tiles
        self._basemap_attr_text = None      # attribution text artist
        self._basemap_request_id = 0        # guards against stale async fetches
        self._contextily_available = None   # tri-state availability cache
        self._backdrop_artists = []         # Cartopy land/ocean fill, hidden when tiles show

        # Which family of source is active. Everything downstream branches on
        # this rather than on _basemap_provider, so the offline paths never
        # reach the contextily code and vice versa.
        self._basemap_kind = "none"         # none | xyz | natural_earth | mbtiles
        self._ne_backdrop = NaturalEarthBackdrop()
        self._mbtiles_source = None         # MBTilesSource while kind == 'mbtiles'
        self._mbtiles_paths = {}            # {selector label: file path}

        # Debounce tile refetch so we fetch once when panning/zooming settles,
        # not on every intermediate extent change.
        self._basemap_timer = QTimer(self)
        self._basemap_timer.setSingleShot(True)
        self._basemap_timer.setInterval(220)
        self._basemap_timer.timeout.connect(self._request_basemap_refresh)

        # Tiles are fetched off the UI thread; the worker emits a signal that Qt
        # delivers back on the main thread (queued connection) for drawing.
        self.basemap_ready.connect(self._on_basemap_ready)
        self.basemap_failed.connect(self._on_basemap_failed)
        self.extent_changed.connect(self._on_extent_changed_basemap)

        # Persistent on-disk tile cache: faster repeat runs and offline reuse.
        try:
            import contextily as ctx
            cache_dir = os.path.join(os.path.expanduser("~"), ".ncexplorer", "basemap_cache")
            os.makedirs(cache_dir, exist_ok=True)
            ctx.set_cache_dir(cache_dir)
        except Exception:
            pass

    def _contextily_ok(self):
        """Return True if contextily is importable (cached after first check)."""
        if self._contextily_available is None:
            try:
                import contextily  # noqa: F401
                self._contextily_available = True
            except Exception:
                self._contextily_available = False
        return self._contextily_available

    def _get_basemap_providers(self):
        """Lazily build the {display name: xyzservices provider} mapping.

        All sources are open / free and need no API key. Keys here must match
        the items offered by the toolbar's basemap selector.
        """
        if self._basemap_provider_map is not None:
            return self._basemap_provider_map

        providers: dict = {"None": None}
        if self._contextily_ok():
            import contextily as ctx
            p = ctx.providers
            # Resolve each provider independently: xyzservices renames/removes
            # providers between versions, and one missing entry must not wipe out
            # the rest (that would make every basemap fall back to "none").
            wanted = {
                "Carto Light": ("CartoDB", "Positron"),
                "Carto Dark": ("CartoDB", "DarkMatter"),
                "Satellite (Esri)": ("Esri", "WorldImagery"),
                "Topographic": ("OpenTopoMap",),
                "Ocean (Esri)": ("Esri", "OceanBasemap"),
            }
            for label, path in wanted.items():
                try:
                    node = p
                    for part in path:
                        node = getattr(node, part)  # Bunch supports attribute access
                    providers[label] = node
                except Exception as exc:
                    logger.warning("Basemap provider '%s' unavailable: %s", label, exc)

            # Keyless additions. Each resolves independently for the same reason
            # as above, and a missing one simply never reaches the selector.
            s2 = sentinel2_cloudless()
            if s2 is not None:
                providers[S2_CLOUDLESS_LABEL] = s2

            for label, path in STATIC_SOURCES.items():
                node = resolve(path)
                if node is not None:
                    providers[label] = node

        self._basemap_provider_map = providers
        return providers

    def set_basemap(self, name):
        """Select a basemap by display name ('None' to disable).

        Handles three families: the online XYZ providers, the offline Natural
        Earth backdrop, and a local MBTiles archive. Only the first ever imports
        contextily or reaches the network.
        """
        self._basemap_name = name

        # Explicit "None": revert to the built-in Cartopy land/ocean backdrop.
        if name == "None":
            self._basemap_kind = "none"
            self._basemap_provider = None
            self._invalidate_pending_basemap()
            self._clear_offline_sources()
            self._clear_basemap_artist()
            self._update_basemap_attribution()  # clears existing attribution
            self._set_backdrop_visible(True)     # bring the starting fill back
            self.draw_idle()
            self.status_update.emit("Basemap: none")
            return

        if name == self.OFFLINE_NATURAL_EARTH:
            self._activate_natural_earth()
            return

        if name.startswith(self.MBTILES_PREFIX):
            self._activate_mbtiles(name)
            return

        # From here on it is an online provider; drop any offline source first
        # so the two can never draw on top of each other.
        self._clear_offline_sources()

        # A real basemap was requested but the tile library is missing — say so
        # plainly instead of silently falling back. This is the usual cause of a
        # surprising "none": the app was launched with a Python lacking contextily.
        if not self._contextily_ok():
            self._basemap_kind = "none"
            self._basemap_provider = None
            self.status_update.emit(
                "Basemap needs the 'contextily' package — pip install contextily"
            )
            return

        provider = self._get_basemap_providers().get(name)
        if provider is None:
            self._basemap_kind = "none"
            self._basemap_provider = None
            self.status_update.emit(f"Basemap '{name}' is unavailable")
            return

        self._basemap_kind = "xyz"
        self._basemap_provider = provider
        self.status_update.emit(f"Loading basemap: {name}…")
        self._request_basemap_refresh()

    # ------------------------------------------------------------------
    # Offline basemaps (no network, no contextily)
    # ------------------------------------------------------------------
    def register_mbtiles(self, path):
        """Make a local ``.mbtiles`` selectable; returns its selector label.

        Registration is deliberately cheap — the archive is only opened when the
        user actually selects it, so a broken file in ~/.ncexplorer/basemaps
        cannot slow down or break startup.
        """
        label = self.MBTILES_PREFIX + os.path.splitext(os.path.basename(path))[0]
        self._mbtiles_paths[label] = path
        return label

    def _invalidate_pending_basemap(self):
        """Make any tile fetch still in flight land as stale and be discarded.

        Without this, switching from tiles to the vector backdrop can let a
        fetch that was already running finish afterwards — and tiles draw above
        the Natural Earth layers, so they would cover the backdrop that just
        replaced them.
        """
        self._basemap_request_id += 1

    def _activate_natural_earth(self):
        """Draw the bundled-vector backdrop for the current extent."""
        self._basemap_kind = "natural_earth"
        self._basemap_provider = None
        self._mbtiles_source = None
        self._invalidate_pending_basemap()
        self._clear_basemap_artist()
        self._update_basemap_attribution()

        if not natural_earth_available():
            self._basemap_kind = "none"
            self._ne_backdrop.clear()
            self._set_backdrop_visible(True)
            self.draw_idle()
            self.status_update.emit(
                "Offline basemap needs Cartopy's Natural Earth data, which is not "
                "on this machine — run once with a network connection to cache it"
            )
            return

        try:
            span = 360.0
            try:
                w, e, _s, _n = self.ax.get_extent(crs=ccrs.PlateCarree())
                span = abs(e - w)
            except Exception:
                pass  # not laid out yet; the wide scale is the safe default

            scale = self._ne_backdrop.scale_for_span(span)
            drawn = self._ne_backdrop.draw(
                self.ax, self.theme, self.NATURAL_EARTH_ZORDER, scale=scale
            )
        except Exception as exc:
            logger.error("Natural Earth backdrop failed: %s", exc, exc_info=True)
            self._basemap_kind = "none"
            self._set_backdrop_visible(True)
            self.draw_idle()
            self.status_update.emit(f"Offline basemap failed: {exc}")
            return

        if not drawn:
            self._basemap_kind = "none"
            self._set_backdrop_visible(True)
            self.draw_idle()
            self.status_update.emit("Offline basemap has no drawable layers cached")
            return

        # The vector backdrop replaces the plain fill outright, exactly as tiles do.
        self._set_backdrop_visible(False)
        self.draw_idle()
        self.status_update.emit(
            f"Basemap: Natural Earth ({scale}, offline) — {len(drawn)} layers"
        )

    def _activate_mbtiles(self, label):
        """Open a local MBTiles archive and stitch its tiles for the extent."""
        path = self._mbtiles_paths.get(label)
        if not path:
            self._basemap_kind = "none"
            self.status_update.emit(f"Basemap '{label}' is no longer registered")
            return

        try:
            source = MBTilesSource(path)
        except VectorTilesUnsupported as exc:
            # Explicitly separated from the generic failure: this one is a
            # property of the file, not a fault the user can retry away.
            self._basemap_kind = "none"
            self._mbtiles_source = None
            self._invalidate_pending_basemap()
            self._set_backdrop_visible(True)
            self.draw_idle()
            logger.info("Rejected vector MBTiles %s: %s", path, exc)
            self.status_update.emit(str(exc))
            return
        except Exception as exc:
            self._basemap_kind = "none"
            self._mbtiles_source = None
            self._invalidate_pending_basemap()
            self._set_backdrop_visible(True)
            self.draw_idle()
            logger.warning("Could not open MBTiles %s: %s", path, exc)
            self.status_update.emit(f"Could not open MBTiles: {exc}")
            return

        self._ne_backdrop.clear()
        self._basemap_kind = "mbtiles"
        self._basemap_provider = None
        self._mbtiles_source = source
        self.status_update.emit(f"Loading basemap: {source.name} (offline)…")
        self._request_basemap_refresh()

    def _clear_offline_sources(self):
        """Drop both offline sources; used whenever another family takes over."""
        self._ne_backdrop.clear()
        self._mbtiles_source = None

    def _fetch_mbtiles_worker(self, w, s, e, n, zoom, source, req):
        """Worker thread: stitch local tiles and signal the result back."""
        try:
            img, ext = source.fetch_extent(w, s, e, n, zoom)
            self.basemap_ready.emit(img, list(ext), req)
        except Exception as exc:
            self.basemap_failed.emit(str(exc), req)

    def _on_extent_changed_basemap(self, *_):
        """Debounce a basemap refresh whenever the visible extent changes."""
        if getattr(self, '_basemap_kind', 'none') != 'none':
            self._basemap_timer.start()

    def _pick_zoom(self, w, s, e, n):
        """Choose an XYZ zoom level from the longitude span, capped to keep the
        tile count bounded at wide/global extents."""
        lon_span = max(1e-6, e - w)
        zoom = int(math.floor(math.log2(360.0 / lon_span))) + 1
        return max(1, min(12, zoom))

    def _request_basemap_refresh(self):
        """Compute the current bounds and dispatch an async tile fetch."""
        kind = getattr(self, '_basemap_kind', 'none')
        if kind == 'none':
            return

        # Vector features are already drawn for the whole world; panning only
        # needs a re-scale when the extent crosses into the finer resolution.
        if kind == 'natural_earth':
            self._refresh_natural_earth_scale()
            return

        if kind == 'xyz' and (not getattr(self, '_basemap_provider', None)
                              or not self._contextily_ok()):
            return
        if kind == 'mbtiles' and getattr(self, '_mbtiles_source', None) is None:
            return

        try:
            w, e, s, n = self.ax.get_extent(crs=ccrs.PlateCarree())
        except Exception:
            return

        # Clamp to valid lon/lat and the Web Mercator latitude limit (~±85°).
        w = max(-179.9999, min(179.9999, w))
        e = max(-179.9999, min(179.9999, e))
        s = max(-85.0, min(85.0, s))
        n = max(-85.0, min(85.0, n))
        if e <= w or n <= s:
            return

        zoom = self._pick_zoom(w, s, e, n)
        self._basemap_request_id += 1
        req = self._basemap_request_id

        # Both fetches run on the existing pool; neither blocks the UI thread.
        # Stitching local tiles is disk- and CPU-bound rather than network-bound,
        # but it is just as capable of freezing the window on a large extent.
        if kind == 'mbtiles':
            self.thread_pool.submit(
                self._fetch_mbtiles_worker, w, s, e, n, zoom, self._mbtiles_source, req
            )
            return

        provider = self._basemap_provider
        self.thread_pool.submit(self._fetch_basemap_worker, w, s, e, n, zoom, provider, req)

    def _refresh_natural_earth_scale(self):
        """Redraw the vector backdrop if the extent now warrants another scale."""
        try:
            w, e, _s, _n = self.ax.get_extent(crs=ccrs.PlateCarree())
        except Exception:
            return

        scale = self._ne_backdrop.scale_for_span(abs(e - w))
        if scale == self._ne_backdrop.scale:
            return  # already at the right resolution; redrawing would only cost time

        try:
            drawn = self._ne_backdrop.draw(
                self.ax, self.theme, self.NATURAL_EARTH_ZORDER, scale=scale
            )
        except Exception as exc:
            logger.warning("Could not re-scale Natural Earth backdrop: %s", exc)
            return

        if drawn:
            self._set_backdrop_visible(False)
            self.draw_idle()

    def _fetch_basemap_worker(self, w, s, e, n, zoom, provider, req):
        """Worker thread: download tiles and signal the result back to the UI."""
        try:
            import contextily as ctx
            img, ext = ctx.bounds2img(w, s, e, n, zoom=zoom, source=provider, ll=True)
            self.basemap_ready.emit(img, list(ext), req)
        except Exception as exc:
            self.basemap_failed.emit(str(exc), req)

    def _on_basemap_ready(self, img, ext, req_id):
        """Main thread: draw fetched tiles beneath the data, ignoring stale ones."""
        if req_id != self._basemap_request_id:
            return  # a newer request superseded this one
        try:
            self._clear_basemap_artist()
            # Tiles arrive in Web Mercator; Cartopy reprojects them onto the
            # PlateCarree axis. They sit above the land/ocean backdrop but below
            # every data layer (rasters at zorder 0, vectors at 5/7/10) so the
            # user's climate data always draws on top of the basemap.
            self._basemap_artist = self.ax.imshow(
                img, extent=ext, transform=ccrs.GOOGLE_MERCATOR,
                origin='upper', zorder=self.BASEMAP_ZORDER, interpolation='bilinear',
            )
            # Tiles now cover the map — hide the starting land/ocean fill so the
            # basemap is shown on its own, not blended with the Cartopy backdrop.
            self._set_backdrop_visible(False)
            self._update_basemap_attribution()
            self.draw_idle()
            self.status_update.emit(f"Basemap: {self._basemap_name}")
        except Exception as exc:
            logger.error("Basemap draw error: %s", exc, exc_info=True)

    def _on_basemap_failed(self, msg, req_id):
        """Main thread: report a fetch failure and restore the Cartopy backdrop."""
        if req_id != self._basemap_request_id:
            return
        logger.warning("Basemap fetch failed: %s", msg)
        # No tiles arrived — bring the land/ocean fill back so the map isn't blank.
        self._set_backdrop_visible(True)
        self.draw_idle()
        if getattr(self, '_basemap_kind', 'none') == 'mbtiles':
            # A local archive that yields nothing is a coverage or format
            # problem, so report what actually went wrong rather than blaming
            # the network the way the online path does.
            self.status_update.emit(f"MBTiles basemap unavailable — {msg}")
        else:
            self.status_update.emit("Basemap unavailable (offline?) — showing base features")

    def _set_backdrop_visible(self, visible):
        """Show or hide the Cartopy land/ocean fill (the starting backdrop).

        Hidden while real basemap tiles are displayed so the two never overlap;
        shown again for 'None' or when a tile fetch fails.
        """
        for artist in self._backdrop_artists:
            try:
                artist.set_visible(visible)
            except Exception:
                pass

    def _clear_basemap_artist(self):
        """Remove the current tile artist, if any."""
        if self._basemap_artist is not None:
            try:
                self._basemap_artist.remove()
            except Exception:
                pass
            self._basemap_artist = None

    def _update_basemap_attribution(self):
        """Refresh the small attribution overlay required by tile providers."""
        if self._basemap_attr_text is not None:
            try:
                self._basemap_attr_text.remove()
            except Exception:
                pass
            self._basemap_attr_text = None

        # MBTiles carry their credit in the archive's own metadata table; online
        # providers carry it on the xyzservices object.
        source = getattr(self, '_mbtiles_source', None)
        if getattr(self, '_basemap_kind', 'none') == 'mbtiles' and source is not None:
            attribution = source.attribution
        else:
            provider = getattr(self, '_basemap_provider', None)
            if not provider:
                return
            try:
                attribution = provider.get('attribution', '') or ''
            except Exception:
                attribution = ''
        if not attribution:
            return

        self._basemap_attr_text = self.ax.text(
            0.995, 0.008, attribution, transform=self.ax.transAxes,
            ha='right', va='bottom', fontsize=6, color='#222222', zorder=6,
            bbox=dict(boxstyle='round,pad=0.25', fc='white', ec='none', alpha=0.6),
        )

    def set_fullscreen_canvas(self, fullscreen=True):
        """Toggle fullscreen canvas mode with no borders."""
        if fullscreen:
            # Remove all padding and margins
            self.fig.subplots_adjust(left=0, bottom=0, right=1, top=1, wspace=0, hspace=0)
            self.ax.set_position([0, 0, 1, 1])
            self.setContentsMargins(0, 0, 0, 0)
            self.remove_axis_borders()
        else:
            # Restore some padding for UI elements
            self.fig.subplots_adjust(left=0, bottom=0, right=0, top=0)
            self.ax.set_position([0, 0, 0, 0])
            self.setContentsMargins(0, 0, 0, 0)
            self.remove_axis_borders()

        self.draw()

    def resizeEvent(self, event):
        """Keep the map filling the canvas with correct proportions on resize.

        The axes uses 'auto' aspect so it always fills the box (no white
        margins), but the extent-constraint aspect must follow the widget's
        real pixel ratio; otherwise the data stretches. On every resize we
        refresh that ratio and re-fit the current extent to it.
        """
        super().resizeEvent(event)

        overlay = getattr(self, '_nav_overlay', None)
        if overlay is not None:
            overlay.reposition()

        ax = getattr(self, 'ax', None)
        extent = getattr(self, 'extent', None)
        if ax is None or not extent:
            return  # too early in construction, or no extent yet

        try:
            w, h = self.width(), self.height()
            if w <= 0 or h <= 0:
                return
            self.aspect_ratio = w / h
            self.extent = self._constrain_extent(extent)
            self._apply_axes_extent()
            ax.set_position((0, 0, 1, 1))
            # A resize changes the visible area — refetch tiles if a basemap is on.
            if getattr(self, '_basemap_provider', None) is not None:
                self._basemap_timer.start()

            # The overlays are placed in fraction coordinates, but the extent
            # they describe has just been re-fitted, so both need recomputing.
            colorbar = getattr(self, 'colorbar_manager', None)
            scalebar = getattr(self, 'scalebar_manager', None)
            if colorbar is not None:
                colorbar.refresh()
            if scalebar is not None:
                scalebar.refresh()

            self.draw_idle()
        except Exception as exc:
            logger.error("Resize handling error: %s", exc, exc_info=True)

    def setup_map(self):
        """Initialize the map: the same rebuild every later change goes through."""
        self.extent = self._constrain_extent(self.extent)
        self._rebuild_axes()

    def remove_axis_borders(self):
        """Remove all axis borders and decorations."""
        if self.ax:
            # Hide all spines
            for spine in self.ax.spines.values():
                spine.set_visible(False)

            # Remove tick marks and labels
            self.ax.set_xticks([])
            self.ax.set_yticks([])

            self.draw()

    def _constrain_extent(self, extent):
        """Constrain the extent with enhanced validation."""
        min_lon, max_lon, min_lat, max_lat = extent
        width = max_lon - min_lon
        height = max_lat - min_lat

        # Validate extent
        if width <= 0 or height <= 0:
            return self.extent

        # Apply zoom constraints
        if width > self.min_zoom_extent[0]:
            width = self.min_zoom_extent[0]
        elif width < self.max_zoom_extent[0]:
            width = self.max_zoom_extent[0]
            self.zoom_limit_reached.emit("Maximum zoom in reached")

        if height > self.min_zoom_extent[1]:
            height = self.min_zoom_extent[1]
        elif height < self.max_zoom_extent[1]:
            height = self.max_zoom_extent[1]
            self.zoom_limit_reached.emit("Maximum zoom in reached")

        # Maintain aspect ratio
        if self.maintain_aspect_ratio:
            target_aspect = self.aspect_ratio
            current_aspect = width / height
            if current_aspect > target_aspect:
                width = height * target_aspect
            else:
                height = width / target_aspect

        # Calculate center and create new extent
        center_lon = (min_lon + max_lon) / 2
        center_lat = (min_lat + max_lat) / 2
        new_extent = [
            center_lon - width / 2, center_lon + width / 2,
            center_lat - height / 2, center_lat + height / 2
        ]

        # Constrain to global bounds
        new_extent[0] = max(new_extent[0], self.max_extent[0])
        new_extent[1] = min(new_extent[1], self.max_extent[1])
        new_extent[2] = max(new_extent[2], self.max_extent[2])
        new_extent[3] = min(new_extent[3], self.max_extent[3])

        return new_extent

    def _set_constrained_extent(self, extent):
        """Set the extent with constraints applied.

        ``self.extent`` is always the lon/lat box, whatever the axes is drawn
        in — it is what a project file stores, what the statistics dock reads
        and what survives a projection change. What the axes is actually given
        is worked out from it (see _apply_axes_extent).
        """
        constrained_extent = self._constrain_extent(extent)
        self.extent = constrained_extent
        self._apply_axes_extent()
        # Every loader sets the extent right after its imshow, and imshow has
        # just reset the aspect to 'equal' behind our back — so this is also
        # where a freshly loaded layer stops letterboxing the map.
        self._apply_aspect()
        self._invalidate_zoom_info_cache()
        return constrained_extent

    def _effective_extent(self):
        """The lon/lat box actually on screen, which zoom and pan work from.

        Not the same as ``self.extent`` in every projection: a polar cap asked
        for the whole world shows one hemisphere, and a conic shows the half of
        the world its cone covers. Zooming from the request rather than from
        what is drawn would do nothing at all for the first few clicks — the
        request would shrink while the drawn map stayed pinned at its limit.
        """
        box = projections.axes_extent(
            self.projection_name, self.extent, self._projection_params
        )
        return list(box) if box is not None else list(self.extent)

    def _invalidate_zoom_info_cache(self):
        """Drop the memoised get_zoom_info() result after an extent change.

        @monitor_performance caches get_zoom_info() under a key built only from
        its arguments (there are none), so without this every caller after the
        first would keep seeing the zoom state from startup.
        """
        cache = getattr(self, '_method_cache', None)
        if not cache:
            return
        for key in [k for k in cache if k.startswith('get_zoom_info')]:
            del cache[key]

    def connect_events(self):
        """Connect matplotlib events to PyQt6 signals."""
        self.mpl_connect('button_press_event', self._on_click)

    def setup_mouse_controls(self):
        """Setup enhanced mouse controls for pan and zoom."""
        self.mpl_connect('button_press_event', self._on_mouse_press)
        self.mpl_connect('button_release_event', self._on_mouse_release)
        self.mpl_connect('motion_notify_event', self._on_mouse_move)
        self.mpl_connect('scroll_event', self._on_scroll)
        self.mpl_connect('axes_leave_event', self._on_axes_leave)

    def _axes_to_lonlat(self, x, y):
        """``(lon, lat)`` for a point in axes coordinates, or ``(None, None)``.

        The axes' own coordinates are metres in every projection but the
        default, so nothing may read xdata/ydata as a position. None comes back
        for a point that is not anywhere: the space in the corners beside a
        Mollweide ellipse, or outside a polar cap's circle, is off the globe.
        Such a point transforms to infinity rather than raising — and infinity
        passes every range check a caller might make on its own.
        """
        if x is None or y is None or self.ax is None:
            return None, None

        try:
            lon, lat = ccrs.PlateCarree().transform_point(x, y, self.ax.projection)
        except Exception:
            return None, None

        if lon is None or lat is None:
            return None, None
        if not (math.isfinite(lon) and math.isfinite(lat)):
            return None, None
        return lon, lat

    def _on_click(self, event):
        """Handle map click events with coordinate validation."""
        # While a region is being drawn the press belongs to the selector: a
        # click emitted here would also plot a point under the rubber band.
        if self._region_selector is not None:
            return
        if event.inaxes == self.ax:
            lon, lat = self._axes_to_lonlat(event.xdata, event.ydata)
            if lon is None:
                return
            if -180 <= lon <= 180 and -90 <= lat <= 90:
                self.map_clicked.emit(lat, lon)

    def _on_mouse_press(self, event):
        """Handle mouse press for pan/zoom with drag threshold."""
        if event.inaxes != self.ax:
            return
        if event.button == 1 and self.pan_enabled:
            self.press_event = event
            self.last_extent = list(self.extent)

    def _on_mouse_release(self, event):
        """Handle mouse release with drag detection."""
        if self.press_event:
            if hasattr(event, 'x') and hasattr(self.press_event, 'x'):
                drag_distance = ((event.x - self.press_event.x) ** 2 +
                                 (event.y - self.press_event.y) ** 2) ** 0.5
                if drag_distance < self.drag_threshold:
                    self._on_click(event)
                else:
                    self.extent_changed.emit(self.extent)
            self.press_event = None
            self.last_extent = None

    def _on_mouse_move(self, event):
        """Handle mouse movement for panning with smooth updates."""
        # Runs first: everything below only applies while a drag is in progress,
        # but the hover readout has to track the cursor whether or not a button
        # is held.
        self._update_hover_readout(event)

        if self.press_event is None or event.inaxes != self.ax:
            return
        if not self.pan_enabled:
            return

        # In degrees, not in axes coordinates: a drag of so many metres means
        # nothing to a lon/lat extent, and the two are the same number only
        # under the default projection.
        lon, lat = self._axes_to_lonlat(event.xdata, event.ydata)
        from_lon, from_lat = self._axes_to_lonlat(self.press_event.xdata,
                                                  self.press_event.ydata)
        if lon is None or from_lon is None:
            return  # the drag left the globe; the next move picks it up again

        dx = lon - from_lon
        dy = lat - from_lat

        west, east, south, north = self.extent
        self._set_constrained_extent([west - dx, east - dx, south - dy, north - dy])
        self.draw()

    # ------------------------------------------------------------------
    # Cursor readout (lat / lon / data value under the pointer)
    # ------------------------------------------------------------------
    def _on_axes_leave(self, _event):
        """Clear the readout when the pointer leaves the map."""
        self.cursor_left.emit()

    def _update_hover_readout(self, event):
        """Emit the geographic position and data value under the cursor.

        Throttled to ~30 Hz: motion_notify_event fires on every pixel of
        movement and an unthrottled Qt label update is visibly slower to pan.
        """
        if event.inaxes != self.ax or event.xdata is None or event.ydata is None:
            return

        now = time.perf_counter()
        if now - self._hover_last_emit < self.HOVER_MIN_INTERVAL:
            return
        self._hover_last_emit = now

        # The axes projection is not necessarily PlateCarree, so convert rather
        # than reading xdata/ydata as lon/lat.
        lon, lat = self._axes_to_lonlat(event.xdata, event.ydata)
        if lon is None:
            return

        self.cursor_position_changed.emit(lat, lon, self.sample_value_at(lon, lat))

    def scalar_layer_order(self):
        """Visible raster/NetCDF layer names, topmost first.

        Ordered by artist zorder then by insertion order; the loaders all use
        imshow's default zorder, so in practice the most recently added layer
        counts as topmost. Layers whose artist was orphaned by a theme change
        are skipped.
        """
        entries = []
        for index, (name, record) in enumerate(self.layers.items()):
            if record.get('type') not in self.SCALAR_LAYER_TYPES:
                continue
            if not record.get('visible', True):
                continue
            artist = record.get('artist')
            if artist is None or not hasattr(artist, 'get_array'):
                continue
            try:
                if artist.axes is not self.ax or artist.get_array() is None:
                    continue
                zorder = float(artist.get_zorder())
            except Exception:
                continue
            entries.append((zorder, index, name))

        entries.sort(reverse=True)
        return [name for _zorder, _index, name in entries]

    @staticmethod
    def layer_lonlat_bounds(record):
        """Geographic bounds of a layer as ``(west, east, south, north)``.

        Every loader now stores the imshow extent ``[w, e, s, n]`` — load_raster
        used to store a rasterio BoundingBox, whose field order is
        ``(left, bottom, right, top)``, and which is also in whatever CRS the
        file happened to use rather than lon/lat.

        The BoundingBox branch is kept as a defensive path: it costs one
        ``hasattr`` and it is the difference between a stale record reading
        correctly and reading with its latitudes and longitudes transposed.
        """
        bounds = record.get('bounds')
        if bounds is None:
            return None
        try:
            if hasattr(bounds, 'left'):
                return (float(bounds.left), float(bounds.right),
                        float(bounds.bottom), float(bounds.top))
            if len(bounds) == 4:
                return tuple(float(v) for v in bounds)
        except (TypeError, ValueError):
            return None
        return None

    def sample_value_at(self, lon, lat):
        """Data value of the topmost visible raster/NetCDF layer at lon/lat.

        Returns None where there is no such layer, the point falls outside it,
        or the cell is masked / NaN.
        """
        order = self.scalar_layer_order()
        if not order:
            return None
        return self._sample_layer(self.layers[order[0]], lon, lat)

    @staticmethod
    def sample_extent(artist, record):
        """The geographic box the artist's *current* array actually covers.

        Not necessarily the layer's stored bounds. Cartopy reprojects an image
        whose data does not line up with the axes' native domain — a 0…360
        longitude file, for instance, comes back from imshow as a much larger
        array remapped onto −180…180 with the outside masked. get_array() then
        returns that reprojected array, so only the artist's own extent still
        agrees with it. The record's bounds are the fallback for artists that
        cannot report an extent.
        """
        try:
            extent = artist.get_extent()
        except Exception:
            extent = None

        if extent is not None and len(extent) == 4:
            try:
                west, east, south, north = (float(v) for v in extent)
            except (TypeError, ValueError):
                west = east = south = north = 0.0
            if east > west and north > south:
                return west, east, south, north

        return GeoCanvas.layer_lonlat_bounds(record)

    @staticmethod
    def _sample_layer(record, lon, lat):
        """Read one layer's array at a geographic position."""
        artist = record.get('artist')
        if artist is None:
            return None
        bounds = GeoCanvas.sample_extent(artist, record)
        if bounds is None:
            return None

        west, east, south, north = bounds
        if east <= west or north <= south:
            return None

        # Longitude convention: many climate files run 0…360 instead of
        # −180…180. Shift the cursor longitude into whatever range the layer's
        # own bounds describe rather than assuming either convention.
        x = lon
        if not west <= x <= east:
            for candidate in (lon + 360.0, lon - 360.0):
                if west <= candidate <= east:
                    x = candidate
                    break
        if not (west <= x <= east and south <= lat <= north):
            return None

        try:
            array = artist.get_array()
        except Exception:
            return None
        if array is None or getattr(array, 'ndim', 0) < 2:
            return None

        rows, cols = array.shape[0], array.shape[1]
        if rows < 1 or cols < 1:
            return None

        # imshow spreads the array over the extent box, so the extent maps to
        # the outer pixel edges: cols equal columns across [west, east].
        col = int((x - west) / (east - west) * cols)
        row_from_south = int((lat - south) / (north - south) * rows)
        col = max(0, min(cols - 1, col))
        row_from_south = max(0, min(rows - 1, row_from_south))

        # The NetCDF loader tries origin='lower' and silently falls back to
        # 'upper' (see load_netcdf), so read back what was actually used —
        # assuming either one mirrors the values vertically.
        if getattr(artist, 'origin', 'lower') == 'upper':
            row = rows - 1 - row_from_south
        else:
            row = row_from_south

        try:
            raw = array[row, col]
        except (IndexError, TypeError):
            return None

        if raw is np.ma.masked or np.ma.is_masked(raw):
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        if math.isnan(value):
            return None
        return value

    def _zoom_about(self, factor, focus_x=None, focus_y=None):
        """Scale the current extent by ``factor`` about a focus point.

        The wheel handler and the navigation buttons both zoom through here so
        the two can never drift apart. ``focus_x``/``focus_y`` are a longitude
        and a latitude, and default to the centre of the current view (button
        zoom); the wheel passes the cursor position so zooming follows the
        mouse.
        """
        if not factor or factor <= 0:
            return None

        # Zooming *in* narrows what is on screen, so it starts from what is
        # drawn (see _effective_extent) — otherwise the first few clicks on a
        # polar cap or a conic would shrink a request that is already wider
        # than the map can show, and nothing would visibly happen.
        #
        # Zooming *out* widens the request instead. Starting from the drawn box
        # there would throw away the part the projection clips on every click,
        # and the view would stall short of full extent however many times it
        # was pressed. It does mean getting back out takes more clicks than
        # getting in took; "full extent" is the shortcut for anyone who does
        # not want to press them.
        current_extent = self._effective_extent() if factor < 1 else list(self.extent)
        current_width = current_extent[1] - current_extent[0]
        current_height = current_extent[3] - current_extent[2]
        if current_width <= 0 or current_height <= 0:
            return None

        if focus_x is None:
            focus_x = (current_extent[0] + current_extent[1]) / 2
        if focus_y is None:
            focus_y = (current_extent[2] + current_extent[3]) / 2

        new_width = current_width * factor
        new_height = current_height * factor

        focus_frac_x = (focus_x - current_extent[0]) / current_width
        focus_frac_y = (focus_y - current_extent[2]) / current_height

        new_extent = [
            focus_x - new_width * focus_frac_x,
            focus_x + new_width * (1 - focus_frac_x),
            focus_y - new_height * focus_frac_y,
            focus_y + new_height * (1 - focus_frac_y)
        ]

        constrained_extent = self._set_constrained_extent(new_extent)
        self.draw()
        self.extent_changed.emit(constrained_extent)
        return constrained_extent

    def _on_scroll(self, event):
        """Enhanced mouse scroll for zooming - mouse-centered with smooth operation."""
        if event.inaxes != self.ax or not self.zoom_enabled:
            return

        current_extent = self._effective_extent()
        current_width = current_extent[1] - current_extent[0]

        # Adaptive zoom factor
        base_zoom = 0.9 if event.step > 0 else 1.1
        zoom_factor = base_zoom

        if current_width < 10:
            zoom_factor = 0.95 if event.step > 0 else 1.05
        elif current_width < 50:
            zoom_factor = 0.92 if event.step > 0 else 1.08

        # A wheel turn over the space beside a globe is not over anywhere, so
        # there is no point to follow: zoom about the centre instead of doing
        # nothing.
        mouse_lon, mouse_lat = self._axes_to_lonlat(event.xdata, event.ydata)
        self._zoom_about(zoom_factor, mouse_lon, mouse_lat)

    # ------------------------------------------------------------------
    # Rubber-band region selection
    # ------------------------------------------------------------------
    def begin_region_selection(self):
        """Arm a drag-to-draw rectangle over the map. True once it is armed.

        Drag panning is suppressed for the duration — matplotlib's selector and
        the canvas' own drag handler would otherwise both act on the same
        gesture, moving the map out from under the box being drawn. The flag is
        restored by :meth:`end_region_selection`, which every exit path calls.
        """
        if self._region_selector is not None:
            return True

        try:
            self._region_pan_enabled = self.pan_enabled
            self.pan_enabled = False
            # Any drag already in flight would otherwise finish as a pan the
            # moment the button comes up.
            self.press_event = None
            self.last_extent = None

            self._region_selector = RectangleSelector(
                self.ax,
                self._on_region_drawn,
                useblit=False,
                button=[1],
                minspanx=5,
                minspany=5,
                spancoords='pixels',
                interactive=False,
                props=dict(facecolor='#3b82f6', edgecolor='#1d4ed8',
                           alpha=0.25, fill=True, linewidth=1.2),
            )
        except Exception as exc:
            logger.error("Could not start region selection: %s", exc, exc_info=True)
            self.pan_enabled = self._region_pan_enabled
            self._region_selector = None
            self.status_update.emit(f"Could not start region selection: {exc}")
            return False

        self.setCursor(Qt.CursorShape.CrossCursor)
        self.status_update.emit("Drag on the map to draw a region")
        return True

    def end_region_selection(self):
        """Disarm the rectangle and give the canvas back exactly as it was."""
        selector = self._region_selector
        self._region_selector = None

        if selector is not None:
            try:
                selector.set_active(False)
                selector.set_visible(False)
                selector.disconnect_events()
                for artist in getattr(selector, 'artists', ()):
                    try:
                        artist.remove()
                    except (ValueError, NotImplementedError):
                        # Already detached with the axes; nothing left to undo.
                        pass
            except Exception as exc:
                logger.warning("Could not fully tear down the region selector: %s", exc)

        self.pan_enabled = self._region_pan_enabled
        self.unsetCursor()
        self.draw_idle()

    @property
    def region_selection_active(self) -> bool:
        return self._region_selector is not None

    def _on_region_drawn(self, press, release):
        """Convert the drawn box to lon/lat and announce it."""
        # One box per arming: the selection is a deliberate act, not a mode the
        # user has to remember to leave.
        self.end_region_selection()

        corners = []
        for event in (press, release):
            if event is None:
                self.status_update.emit("That region was drawn outside the map")
                return
            lon, lat = self._axes_to_lonlat(event.xdata, event.ydata)
            if lon is None:
                self.status_update.emit("That region was drawn outside the map")
                return
            corners.append((lon, lat))

        (west, south), (east, north) = corners
        west, east = sorted((west, east))
        south, north = sorted((south, north))

        if east <= west or north <= south:
            self.status_update.emit("That region has no area — drag a larger box")
            return

        logger.debug("Region drawn: %.3f, %.3f → %.3f, %.3f", west, south, east, north)
        self.region_selected.emit(west, south, east, north)

    # ------------------------------------------------------------------
    # Swipe comparison
    # ------------------------------------------------------------------
    def apply_swipe_clip(self, fraction, left_artists=(), right_artists=()):
        """Show ``left_artists`` left of the divider and ``right_artists`` right of it.

        The clip rectangles are in axes coordinates, so the split stays put
        while the map is panned or zoomed underneath it. Every artist touched is
        remembered, and one that was clipped by an earlier call but is not named
        now is released here — otherwise swapping layer B would leave the old
        layer B permanently cropped.
        """
        fraction = max(0.0, min(1.0, float(fraction)))

        left_box = Rectangle((0, 0), fraction, 1, transform=self.ax.transAxes)
        right_box = Rectangle((fraction, 0), 1 - fraction, 1, transform=self.ax.transAxes)

        touched = []
        for artists, clip in ((left_artists, left_box), (right_artists, right_box)):
            for artist in artists or ():
                if artist is None:
                    continue
                try:
                    artist.set_clip_path(clip)
                    artist.set_clip_on(True)
                except Exception as exc:
                    logger.debug("Could not clip an artist for the swipe: %s", exc)
                    continue
                touched.append(artist)

        for artist in self._swipe_clipped:
            if artist not in touched:
                self._release_clip(artist)
        self._swipe_clipped = touched

        self._draw_swipe_divider(fraction)
        self.draw_idle()

    def clear_swipe_clips(self):
        """Release every clip the swipe set and remove the divider line."""
        for artist in self._swipe_clipped:
            self._release_clip(artist)
        self._swipe_clipped = []

        if self._swipe_divider is not None:
            try:
                self._swipe_divider.remove()
            except (ValueError, NotImplementedError):
                pass
            self._swipe_divider = None

        self.draw_idle()

    def _release_clip(self, artist):
        """Put one artist back on the axes' own clip path."""
        try:
            artist.set_clip_path(self.ax.patch)
        except Exception as exc:
            logger.debug("Could not restore an artist's clip path: %s", exc)

    def _draw_swipe_divider(self, fraction):
        """Draw (or move) the vertical line marking the split."""
        try:
            if self._swipe_divider is None or self._swipe_divider.axes is not self.ax:
                self._swipe_divider = self.ax.plot(
                    [fraction, fraction], [0, 1], transform=self.ax.transAxes,
                    color='#f8fafc', linewidth=2.0, zorder=25,
                    solid_capstyle='butt',
                )[0]
                self._swipe_divider.set_path_effects([
                    path_effects.Stroke(linewidth=3.6, foreground='#0f172a'),
                    path_effects.Normal(),
                ])
            else:
                self._swipe_divider.set_xdata([fraction, fraction])
        except Exception as exc:
            logger.debug("Could not draw the swipe divider: %s", exc)

    @error_handler
    def zoom_in(self, factor=0.8):
        """Zoom in about the centre of the view."""
        if not self.zoom_enabled:
            return None
        return self._zoom_about(factor)

    @error_handler
    def zoom_out(self, factor=1.25):
        """Zoom out about the centre of the view."""
        if not self.zoom_enabled:
            return None
        return self._zoom_about(factor)

    @error_handler
    def zoom_full_extent(self):
        """Restore the whole-globe view."""
        self.set_extent(list(self.max_extent))
        self.status_update.emit("Zoomed to full extent")
        return self.extent

    @error_handler
    def pan_by(self, dx_fraction, dy_fraction):
        """Shift the view by a fraction of the current width/height.

        The shift is clamped to the global bounds: set_extent() silently
        ignores an out-of-order extent, so an unclamped pan at a pole or the
        antimeridian would read as a dead key rather than simply stopping.
        """
        current_extent = self._effective_extent()
        width = current_extent[1] - current_extent[0]
        height = current_extent[3] - current_extent[2]

        dx = width * dx_fraction
        dy = height * dy_fraction
        dx = max(self.max_extent[0] - current_extent[0],
                 min(dx, self.max_extent[1] - current_extent[1]))
        dy = max(self.max_extent[2] - current_extent[2],
                 min(dy, self.max_extent[3] - current_extent[3]))
        if dx == 0 and dy == 0:
            return self.extent

        constrained_extent = self._set_constrained_extent([
            current_extent[0] + dx, current_extent[1] + dx,
            current_extent[2] + dy, current_extent[3] + dy
        ])
        self.draw()
        self.extent_changed.emit(constrained_extent)
        return constrained_extent

    def attach_nav_overlay(self, overlay):
        """Register the floating navigation cluster drawn over the map.

        Kept duck-typed (the overlay only needs a reposition()) so the canvas
        does not import from the gui package, which imports the canvas.
        """
        self._nav_overlay = overlay
        overlay.reposition()
        overlay.raise_()

    def mousePressEvent(self, event):
        """Give the canvas focus on click so its keyboard shortcuts fire.

        matplotlib's Qt canvas forwards presses to the figure but never takes
        focus, and the arrow/Backspace shortcuts are widget-scoped.
        """
        super().mousePressEvent(event)
        self.setFocus(Qt.FocusReason.MouseFocusReason)

    @error_handler
    def show_context_menu(self, position: QPoint):
        """Show an enhanced context menu for layer operations."""
        context_menu = QMenu(self)

        if self.layers:
            layer_names = list(self.layers.keys())
            if layer_names:
                top_layer = layer_names[-1]

                # Zoom actions
                zoom_action = QAction(f"Zoom to {top_layer}", self)
                zoom_action.triggered.connect(lambda: self.zoom_to_layer(top_layer))
                context_menu.addAction(zoom_action)

                # Remove layer
                remove_action = QAction(f"Remove {top_layer}", self)
                remove_action.triggered.connect(lambda: self.remove_layer(top_layer))
                context_menu.addAction(remove_action)

        # Global actions
        if context_menu.actions():
            context_menu.addSeparator()

        reset_action = QAction("Reset View", self)
        reset_action.triggered.connect(lambda: self.set_extent([-180, 180, -90, 90]))
        context_menu.addAction(reset_action)

        save_action = QAction("Save Map", self)
        save_action.triggered.connect(self._show_save_dialog)
        context_menu.addAction(save_action)

        if not context_menu.isEmpty():
            context_menu.exec(self.mapToGlobal(position))

    def _show_save_dialog(self):
        """Show save dialogs for map export."""
        try:
            filename, _ = QFileDialog.getSaveFileName(
                self, "Save Map", "geocanvas_map.png",
                "PNG Files (*.png);;PDF Files (*.pdf);;SVG Files (*.svg)"
            )
            if filename:
                self.save_map(filename, dpi=300)
                self.status_update.emit(f"Map saved: {os.path.basename(filename)}")
        except Exception as e:
            self.loading_error.emit("Save Error", f"Error saving map: {str(e)}")

    @error_handler
    def zoom_to_layer(self, layer_name: str):
        """Zoom to the extent of a specific layer with animation."""
        layer_prop = self.property_manager.get_layer_property(layer_name)
        if layer_prop and layer_prop.dimensions.extent:
            extent = layer_prop.dimensions.extent
            # set_extent() records the outgoing extent itself; pushing it here
            # too would put the same view on the stack twice.
            self.set_extent(extent)

    def zoom_previous(self):
        """Zoom to previous extent."""
        if not self.zoom_history:
            return
        extent = self.zoom_history.pop()
        # Suppress set_extent()'s own history bookkeeping while restoring —
        # otherwise it pushes the current extent straight back and repeated
        # "back" presses oscillate between two views instead of walking the
        # stack.
        self._restoring_extent = True
        try:
            self.set_extent(extent)
        finally:
            self._restoring_extent = False

    def set_extent(self, extent, crs=None):
        """Enhanced extent setting with validation and history."""
        if crs is None:
            crs = ccrs.PlateCarree()

        if len(extent) != 4:
            return

        min_lon, max_lon, min_lat, max_lat = extent
        if min_lon >= max_lon or min_lat >= max_lat:
            return

        if self.extent != extent and not self._restoring_extent:
            self.zoom_history.append(self.extent)
            if len(self.zoom_history) > self.max_zoom_history:
                self.zoom_history.pop(0)

        constrained_extent = self._set_constrained_extent(extent)
        self.draw()
        self.extent_changed.emit(constrained_extent)

    # ============================================================================
    # Layer Management Methods (from LayerManager)
    # ============================================================================

    def add_layer(self, layer_name, **layer_properties):
        """Generic method to add a new layer to the manager."""
        if layer_name in self.layers:
            logger.warning("Layer '%s' already exists; overwriting", layer_name)

        defaults = {
            'name': layer_name,
            'filepath': 'N/A',
            'type': 'unknown',
            'dataset': None,
            'artist': None,
            'visible': True,
        }
        defaults.update(layer_properties)

        # Assign z-order for stacking
        if 'zorder' not in defaults and defaults.get('artist'):
            defaults['artist'].set_zorder(self._z_order_counter)
            self._z_order_counter += 1

        self.layers[layer_name] = defaults

        if defaults['filepath'] != 'N/A':
            self.add_loaded_file(defaults['filepath'])

        logger.debug("Layer '%s' registered with z-order: %s", layer_name,
                     defaults['artist'].get_zorder() if defaults.get('artist') else 'N/A')

    @error_handler
    def load_file(self, filepath):
        """Load a file (shapefile, raster, NetCDF) onto the canvas.

        Returns True once the layer is on the canvas — including when the file was
        already loaded — and False when it could not be loaded. ``@error_handler``
        returns None for an exception that escapes, so None has to keep meaning
        failure: callers must test truthiness and never ``is not None``.

        Success is read back from ``self.layers`` rather than from the sub-loaders'
        return values. Those come in three shapes — a ``(data, extent)`` tuple, a
        GeoDataFrame, or None — and two of them cannot be truth-tested at all
        without raising "truth value is ambiguous".
        """
        if not os.path.exists(filepath):
            self.loading_error.emit("load_file", f"File not found: {filepath}")
            return False

        layer_name = os.path.splitext(os.path.basename(filepath))[0]
        file_ext = os.path.splitext(filepath)[1].lower()

        # The sub-loaders make this a silent no-op, so it is caught here instead:
        # the file is already displayed, which is success as far as every caller is
        # concerned, but nothing changed and re-announcing the layer would be a lie.
        if self.is_file_already_loaded(filepath):
            self.file_already_loaded.emit(os.path.basename(filepath))
            return True

        # The registry decides, so the set the choosers offer and the set the
        # loader accepts cannot drift apart; see geocanvas/formats.py.
        fmt = formats.format_for(filepath)
        if fmt is None:
            self.loading_error.emit(
                "load_file",
                f"Unsupported file type: {file_ext or os.path.basename(filepath)}",
            )
            return False

        # A format we know cannot work here explains itself, rather than
        # letting GDAL report it as an unrecognised file.
        available, reason = formats.availability(fmt)
        if not available:
            self.loading_error.emit(f"{fmt.label} not supported", reason)
            return False

        self.status_update.emit(f"Loading {filepath}...")
        self.progress_update.emit(10)

        # Success is "a layer appeared", not "a layer called layer_name
        # appeared". A mixed-geometry file is drawn as one layer per geometry
        # kind — "roads (lines)", "roads (points)" — so the bare name is never
        # registered and testing for it reported a correct load as a failure.
        before = set(self.layers)

        try:
            if fmt.kind == formats.VECTOR:
                self.load_shapefile(filepath, layer_name)
            elif fmt.kind == formats.RASTER:
                self.load_raster(filepath, layer_name)
            elif fmt.kind == formats.NETCDF:
                self.load_netcdf(filepath, layer_name)
            else:
                self.loading_error.emit("load_file", f"Unsupported file type: {file_ext}")
                return False

            # file_loaded and layer_added belong to the sub-loaders: they know the
            # real file type ('netcdf' / 'raster' / 'shapefile') and they fire once
            # the artist exists. Emitting them again here handed every consumer a
            # second copy, with the layer name sitting in the file-type argument.
            added = set(self.layers) - before
            # A reload that replaced a same-named layer in place adds nothing to
            # the set, so fall back to the name for that case.
            loaded = bool(added) or layer_name in self.layers
            if loaded:
                names = ", ".join(sorted(added)) or layer_name
                self.status_update.emit(f"Loaded {names} successfully.")
                self.progress_update.emit(100)
                self.draw()
            else:
                # A sub-loader that failed has already reported why through
                # loading_error; don't overwrite that with a generic message.
                self.progress_update.emit(0)
            return loaded

        except Exception as e:
            error_msg = f"Failed to load {filepath}: {str(e)}"
            logger.error("Failed to load %s: %s", filepath, e, exc_info=True)
            self.loading_error.emit("load_file", error_msg)
            self.status_update.emit("Loading failed.")
            self.progress_update.emit(0)
            return False

    @error_handler
    def load_netcdf(self, filepath, layer_name=None, variable=None, time_index=0, alpha=0.8, cmap='viridis'):
        """Enhanced NetCDF loading with comprehensive error handling and optimization."""
        start_time = time.perf_counter()

        try:
            if not os.path.exists(filepath):
                raise FileNotFoundError(f"File not found: {filepath}")

            if self.is_file_already_loaded(filepath):
                filename = os.path.basename(filepath)
                self.file_already_loaded.emit(filename)
                return None, None

            if layer_name is None:
                layer_name = os.path.splitext(os.path.basename(filepath))[0]

            self.progress_update.emit(10)

            # Create a layer property
            layer_prop = LayerProperty()
            layer_prop.metadata.name = layer_name
            layer_prop.metadata.layer_type = "netcdf"
            layer_prop.metadata.source_file = filepath
            layer_prop.metadata.file_size = os.path.getsize(filepath)
            layer_prop.netcdf = NetCDFProperties()

            self.progress_update.emit(20)

            # Load NetCDF metadata
            success = self._load_netcdf_metadata(filepath, layer_prop)
            if not success:
                raise Exception("Failed to load NetCDF metadata")

            self.progress_update.emit(40)

            # Open dataset and load data. decode_times=False for the same reason
            # as everywhere else (timesteps are selected by integer index below),
            # and because xarray *raises* on an unrecognised calendar — a file
            # CDO has left with a damaged time axis would otherwise fail to load
            # at all rather than falling back to raw time labels.
            ds = xr.open_dataset(filepath, decode_times=False)
            if variable is None:
                data_vars = list(ds.data_vars.keys())
                if not data_vars:
                    raise ValueError("No data variables found")
                variable = data_vars[0]

            data_array = ds[variable]
            self.progress_update.emit(60)

            # Handle time dimension with validation
            if 'time' in data_array.dims and len(data_array.dims) > 2:
                if time_index >= len(data_array.time):
                    time_index = 0
                data_array = data_array.isel(time=time_index)

            # Handle additional dimensions
            if len(data_array.dims) > 2:
                spatial_dims = ['lat', 'latitude', 'lon', 'longitude', 'x', 'y']
                for dim_name in data_array.dims:
                    if dim_name.lower() not in spatial_dims:
                        data_array = data_array.isel({dim_name: 0})
                        break

            self.progress_update.emit(70)

            # Get coordinates with multiple fallbacks
            lons, lats = self._extract_coordinates(data_array)
            data = data_array.values

            if data.ndim != 2:
                raise ValueError(f"Data must be 2D for visualization, got shape {data.shape}")

            if np.all(np.isnan(data)):
                raise ValueError("All data values are NaN")

            self.progress_update.emit(80)

            # Create extent and display
            extent = [lons.min(), lons.max(), lats.min(), lats.max()]

            # Add to property manager
            self.property_manager.add_layer(layer_name, layer_prop)

            # Create image
            try:
                im = self.ax.imshow(data, extent=extent, transform=ccrs.PlateCarree(),
                                         alpha=alpha, cmap=cmap, origin='lower')
            except Exception:
                im = self.ax.imshow(data, extent=extent, transform=ccrs.PlateCarree(),
                                         alpha=alpha, cmap=cmap, origin='upper')

            self.progress_update.emit(90)

            # Store layer info. 'array' and 'origin' are what the layer is
            # redrawn from when the axes is rebuilt: the artist's own array is
            # whatever the last projection warped, and warping that again
            # compounds the resampling — see _draw_scalar_layer.
            self.layers[layer_name] = {
                'type': 'netcdf',
                'artist': im,
                'array': data,
                'origin': im.origin,
                'data': filepath,
                'variable': variable,
                'bounds': extent,
                'dataset': ds,
                'visible': True,
                'load_time': time.perf_counter() - start_time
            }

            # Update layer properties
            layer_prop.dimensions.width = data.shape[1]
            layer_prop.dimensions.height = data.shape[0]
            layer_prop.dimensions.extent = extent
            layer_prop.style.transparency = 1.0 - alpha

            # Calculate statistics
            valid_data = data[~np.isnan(data)]
            if len(valid_data) > 0:
                layer_prop.metadata.statistics = {
                    'min': float(np.min(valid_data)),
                    'max': float(np.max(valid_data)),
                    'mean': float(np.mean(valid_data)),
                    'std': float(np.std(valid_data)),
                    'valid_pixels': len(valid_data),
                    'total_pixels': data.size
                }

            # Track file and emit signals
            self.add_loaded_file(filepath)
            self.set_extent(extent)
            self.draw()
            self.layer_added.emit(layer_name)
            self.file_loaded.emit(filepath, 'netcdf')

            load_time = time.perf_counter() - start_time
            self.performance_stats['load_times'].append(load_time)
            self.progress_update.emit(100)
            self.status_update.emit(f"NetCDF loaded: {os.path.basename(filepath)} ({load_time:.2f}s)")

            return data, extent

        except Exception as e:
            error_msg = f"Error loading NetCDF: {str(e)}"
            logger.error("Error loading NetCDF: %s", e, exc_info=True)
            self.loading_error.emit("NetCDF Error", error_msg)
            self.progress_update.emit(0)
            return None, None

    @staticmethod
    def _load_netcdf_metadata(filepath, layer_prop):
        """Load NetCDF metadata into layer properties."""
        try:
            ds = xr.open_dataset(filepath, decode_times=False)

            if not layer_prop.netcdf:
                layer_prop.netcdf = NetCDFProperties()

            # Extract metadata
            layer_prop.netcdf.variables = list(ds.data_vars.keys())
            layer_prop.netcdf.coordinate_variables = list(ds.coords.keys())
            layer_prop.netcdf.attributes = dict(ds.attrs)
            layer_prop.netcdf.current_variable = (
                layer_prop.netcdf.current_variable or
                (layer_prop.netcdf.variables[0] if layer_prop.netcdf.variables else None)
            )

            # Handle time dimension. read_time_axis fills in the raw numeric
            # values *and* the decoded display labels (see utils/timeaxis.py);
            # selection stays on the integer index into time_values.
            #
            # ds.sizes, not ds.dims: xarray is turning ds.dims into a set of
            # names, so .keys() on it is already a FutureWarning on every load
            # and will be an AttributeError. ds.sizes is the name→length mapping
            # this was always after, and only the names are wanted here.
            time_dim_name = find_case_insensitive_key(list(ds.sizes), "time")
            if time_dim_name:
                read_time_axis(ds, time_dim_name).apply_to(layer_prop.netcdf)
            else:
                time_coord_name = find_case_insensitive_key(list(ds.coords.keys()), "time")
                if time_coord_name:
                    read_time_axis(ds, time_coord_name).apply_to(layer_prop.netcdf)

            # Handle other dimensions
            layer_prop.netcdf.dimensions_info = {dim: size for dim, size in ds.sizes.items()}

            # Improved coordinate detection
            lon_vars = ['lon', 'longitude', 'X', 'x', 'LONGITUDE']
            lat_vars = ['lat', 'latitude', 'Y', 'y', 'LATITUDE']
            lon, lat = None, None

            for var in lon_vars:
                if var in ds.coords or var in ds.data_vars or var in ds.dims:
                    lon = ds[var] if var in ds else None
                    if lon is not None:
                        break

            for var in lat_vars:
                if var in ds.coords or var in ds.data_vars or var in ds.dims:
                    lat = ds[var] if var in ds else None
                    if lat is not None:
                        break

            if lon is not None and lat is not None:
                extent = [
                    float(np.min(lon.values)), float(np.max(lon.values)),
                    float(np.min(lat.values)), float(np.max(lat.values))
                ]
                layer_prop.dimensions.extent = extent

            ds.close()
            return True

        except Exception as e:
            logger.error("Error loading NetCDF metadata: %s", e, exc_info=True)
            return False

    @staticmethod
    def _extract_coordinates(data_array):
        """Extract coordinates from a data array with multiple fallbacks."""
        coord_pairs = [
            ('lon', 'lat'),
            ('longitude', 'latitude'),
            ('x', 'y'),
            ('X', 'Y')
        ]

        for lon_name, lat_name in coord_pairs:
            if lon_name in data_array.coords and lat_name in data_array.coords:
                lons = data_array.coords[lon_name].values
                lats = data_array.coords[lat_name].values
                return lons, lats

        # Fallback to dimension coordinates
        if len(data_array.dims) >= 2:
            dim1, dim2 = data_array.dims[-2:]
            if dim1 in data_array.coords and dim2 in data_array.coords:
                lats = data_array.coords[dim1].values
                lons = data_array.coords[dim2].values
                return lons, lats

        raise ValueError("Could not identify coordinate variables")

    @error_handler
    def load_raster(self, filepath, layer_name=None, alpha=0.8, cmap='viridis',
                    subdataset=None):
        """Load any raster format the registry accepts onto the canvas.

        Covers GeoTIFF, USGS DEM, ENVI, Idrisi raster and HDF5. ``subdataset``
        picks one variable out of a container format; left None, the first is
        shown and the rest are listed on the layer for the UI to offer.
        """
        start_time = time.perf_counter()

        try:
            if not os.path.exists(filepath):
                raise FileNotFoundError(f"File not found: {filepath}")

            if self.is_file_already_loaded(filepath):
                filename = os.path.basename(filepath)
                self.file_already_loaded.emit(filename)
                return None, None

            # NetCDF has its own variable/time UI, so it is never drawn as a
            # plain raster even though rasterio could open it.
            fmt = formats.format_for(filepath)
            if fmt is not None and fmt.kind == formats.NETCDF:
                return self.load_netcdf(filepath, layer_name, alpha=alpha, cmap=cmap)

            if layer_name is None:
                layer_name = os.path.splitext(os.path.basename(filepath))[0]

            self.progress_update.emit(10)

            # Create layer property
            layer_prop = LayerProperty()
            layer_prop.metadata.name = layer_name
            layer_prop.metadata.layer_type = "raster"
            layer_prop.metadata.source_file = filepath
            layer_prop.metadata.file_size = os.path.getsize(filepath)

            self.progress_update.emit(30)

            # Reprojected to EPSG:4326 when needed, container variables
            # resolved, oversized scenes decimated; see raster_io.open_raster.
            read = open_raster(filepath, subdataset=subdataset)
            data, extent = read.data, read.extent

            self.progress_update.emit(70)

            if read.nodata is not None:
                layer_prop.metadata.no_data_value = read.nodata

            # Add to property manager
            self.property_manager.add_layer(layer_name, layer_prop)

            # Create image
            im = self.ax.imshow(data, extent=extent, transform=ccrs.PlateCarree(),
                                alpha=alpha, cmap=cmap, origin='upper')

            self.progress_update.emit(85)

            # Store layer. 'array'/'origin' are the source the layer is redrawn
            # from on an axes rebuild; see _draw_scalar_layer.
            self.layers[layer_name] = {
                'type': 'raster',
                'artist': im,
                'array': data,
                'origin': im.origin,
                'data': filepath,
                'bounds': extent,
                'visible': True,
                'load_time': time.perf_counter() - start_time,
                # The CRS the file is in, not the one it is drawn in — the
                # properties panel should report the source, and CDO still
                # receives the original path.
                'crs': read.source_crs,
                'subdatasets': read.subdatasets,
            }

            # Update properties
            layer_prop.dimensions.width = read.width
            layer_prop.dimensions.height = read.height
            layer_prop.dimensions.extent = extent
            layer_prop.dimensions.crs = read.source_crs
            layer_prop.dimensions.pixel_size_x = read.pixel_size_x
            layer_prop.dimensions.pixel_size_y = read.pixel_size_y
            layer_prop.style.transparency = 1.0 - alpha

            # Calculate statistics over the valid pixels only. compressed()
            # drops the masked ones; for an unmasked array it is a plain ravel.
            valid_data = (data.compressed() if np.ma.is_masked(data)
                          else np.asarray(data).ravel())
            valid_data = valid_data[np.isfinite(valid_data)]
            if valid_data.size:
                layer_prop.metadata.statistics = {
                    'min': float(np.min(valid_data)),
                    'max': float(np.max(valid_data)),
                    'mean': float(np.mean(valid_data)),
                    'std': float(np.std(valid_data)),
                    'data_type': str(data.dtype),
                }

            self.progress_update.emit(95)

            # Track file and emit signals
            self.add_loaded_file(filepath)
            self.set_extent(extent)
            self.draw()
            self.layer_added.emit(layer_name)
            self.file_loaded.emit(filepath, 'raster')

            load_time = time.perf_counter() - start_time
            self.performance_stats['load_times'].append(load_time)
            self.progress_update.emit(100)

            note = ""
            if read.warped:
                note = f", reprojected from {read.source_crs}"
            elif read.subdatasets:
                note = f", variable '{read.subdatasets[0].name}' of {len(read.subdatasets)}"
            self.status_update.emit(
                f"Raster loaded: {os.path.basename(filepath)} ({load_time:.2f}s{note})"
            )

            return data, extent

        except RasterFormatUnavailable as e:
            # A finished explanation from the registry; pass it through whole.
            fmt = formats.format_for(filepath)
            self.loading_error.emit(
                f"{fmt.label if fmt else 'Raster'} not supported", str(e)
            )
            self.progress_update.emit(0)
            return None, None

        except Exception as e:
            error_msg = f"Error loading raster: {str(e)}"
            logger.error("Error loading raster: %s", e, exc_info=True)
            self.loading_error.emit("Raster Error", error_msg)
            self.progress_update.emit(0)
            return None, None

    @error_handler
    def load_shapefile(self, filepath, layer_name=None):
        """Load any vector format the registry accepts onto the canvas.

        Named for the shapefile it started as, but it now serves every
        VECTOR entry in geocanvas/formats.py — GeoJSON, KML, KMZ, GML,
        GeoPackage, GPX and Idrisi vector. The reading, reprojection, layer
        enumeration and geometry split all live in vector_io.open_vector; what
        is left here is turning the result into artists and properties.
        """
        start_time = time.perf_counter()

        try:
            if not os.path.exists(filepath):
                raise FileNotFoundError(f"File not found: {filepath}")

            if self.is_file_already_loaded(filepath):
                filename = os.path.basename(filepath)
                self.file_already_loaded.emit(filename)
                return None

            self.progress_update.emit(20)

            # Already in EPSG:4326, every layer merged, split by geometry kind.
            gdf, groups = open_vector(filepath)
            if len(gdf) == 0:
                raise ValueError("This file contains no features")

            if layer_name is None:
                layer_name = os.path.splitext(os.path.basename(filepath))[0]

            self.progress_update.emit(40)

            # The property record is filled in *before* anything is drawn.
            # add_points/add_lines/add_polygons each look their style up by
            # layer name while drawing, and _draw_geometry_groups copies this
            # record for the sub-layers of a mixed file — so a field set after
            # the draw would be missing from every copy.
            bounds = gdf.total_bounds
            extent = [bounds[0], bounds[2], bounds[1], bounds[3]]

            layer_prop = LayerProperty()
            layer_prop.metadata.name = layer_name
            layer_prop.metadata.layer_type = "vector"
            layer_prop.metadata.source_file = filepath
            layer_prop.metadata.file_size = os.path.getsize(filepath)
            layer_prop.metadata.geometry_type = "/".join(
                sorted(gdf.geometry.geom_type.unique())
            )
            layer_prop.dimensions.extent = extent
            layer_prop.dimensions.crs = str(gdf.crs)
            layer_prop.metadata.attributes = {
                'columns': list(gdf.columns),
                'feature_count': len(gdf),
                'sample_attributes': gdf.iloc[0].to_dict(),
            }

            self.progress_update.emit(60)

            # Draw every geometry kind present. A file holding points, lines
            # and polygons gets one artist per kind under a suffixed layer
            # name; keying off the first feature's type, as this did before,
            # drew one kind and dropped the rest without saying so.
            drawn = self._draw_geometry_groups(groups, layer_name, layer_prop)
            if not drawn:
                raise ValueError(
                    "This file contains no drawable geometry "
                    "(no points, lines or polygons)."
                )

            self.progress_update.emit(80)

            # Track file and signals
            self.add_loaded_file(filepath)
            self.set_extent(extent)
            self.file_loaded.emit(filepath, 'shapefile')

            load_time = time.perf_counter() - start_time
            self.performance_stats['load_times'].append(load_time)
            self.progress_update.emit(100)
            self.status_update.emit(f"Vector loaded: {os.path.basename(filepath)} ({load_time:.2f}s)")

            return gdf

        except VectorFormatUnavailable as e:
            # Already a finished explanation — which package is missing, or why
            # the format cannot work. Wrapping it in "Error loading shapefile:"
            # would bury the part the user can act on.
            fmt = formats.format_for(filepath)
            self.loading_error.emit(
                f"{fmt.label if fmt else 'Vector'} not supported", str(e)
            )
            self.progress_update.emit(0)
            return None

        except Exception as e:
            error_msg = f"Error loading vector file: {str(e)}"
            logger.error("Error loading vector file %s: %s", filepath, e, exc_info=True)
            self.loading_error.emit("Vector Error", error_msg)
            self.progress_update.emit(0)
            return None

    def _draw_geometry_groups(self, groups, layer_name, layer_prop):
        """Draw each geometry kind in ``groups``; return the layer names made.

        A single-kind file keeps the plain layer name, so nothing about the
        common case changes. Only a genuinely mixed file gets the suffixed
        names, and then it gets one per kind rather than losing all but one.

        Every name that ends up in ``self.layers`` is registered with the
        property manager here, and registered *before* it is drawn. Both halves
        matter: the ``add_*`` methods read their style back by layer name while
        drawing, and ``update_layer_display`` returns early for a layer with no
        record — so a sub-layer registered late, or not at all, would draw
        unstyled and then refuse to be restyled.
        """
        drawn = []
        mixed = len(groups) > 1

        for group, frame in groups.items():
            name = f"{layer_name} ({group})" if mixed else layer_name

            record = layer_prop
            if mixed:
                record = copy.deepcopy(layer_prop)
                record.metadata.name = name
                record.metadata.geometry_type = "/".join(
                    sorted(frame.geometry.geom_type.unique()))
                record.metadata.attributes = dict(
                    layer_prop.metadata.attributes or {},
                    feature_count=len(frame))
            self.property_manager.add_layer(name, record)

            if group == "points":
                # add_points wants (lat, lon) pairs, and neither of its branches
                # handles MultiPoint — the GeoDataFrame one calls .geometry.x,
                # which raises on a multi-part geometry — so the parts are
                # flattened here.
                artist = self.add_points(
                    [(geom.y, geom.x) for geom in frame.geometry
                     if geom.geom_type == "Point"]
                    + [(part.y, part.x) for geom in frame.geometry
                       if geom.geom_type == "MultiPoint" for part in geom.geoms],
                    layer_name=name,
                )
            else:
                # The frame, not a list of geometries. add_lines and
                # add_polygons each have two branches, and only the
                # GeoDataFrame one walks MultiLineString/MultiPolygon parts;
                # the list branch reaches for .coords / .exterior, which
                # multi-part geometries do not have. Passing a list silently
                # dropped every multi-part feature — a GPX track, which is
                # always a MultiLineString, drew nothing at all.
                drawer = self.add_lines if group == "lines" else self.add_polygons
                artist = drawer(frame, layer_name=name)

            if artist is not None:
                drawn.append(name)

        return drawn

    @error_handler
    def add_points(self, coordinates, layer_name='points', **kwargs):
        """Enhanced point addition with property management."""
        try:
            # Get style from property manager
            layer_prop = self.property_manager.get_layer_property(layer_name)
            if layer_prop:
                mpl_style = self.symbology_manager.get_matplotlib_style(layer_name)
                colors = mpl_style.get('color', kwargs.get('colors', 'red'))
                sizes = mpl_style.get('s', kwargs.get('sizes', 50))
                alpha = mpl_style.get('alpha', kwargs.get('alpha', 0.7))
                marker = mpl_style.get('marker', kwargs.get('marker', 'o'))
            else:
                colors = kwargs.get('colors', 'red')
                sizes = kwargs.get('sizes', 50)
                alpha = kwargs.get('alpha', 0.7)
                marker = kwargs.get('marker', 'o')

            # Process coordinates
            if isinstance(coordinates, gpd.GeoDataFrame):
                lons = coordinates.geometry.x.tolist()
                lats = coordinates.geometry.y.tolist()
            else:
                lons = [coord[1] if isinstance(coord, (list, tuple)) else coord.x for coord in coordinates]
                lats = [coord[0] if isinstance(coord, (list, tuple)) else coord.y for coord in coordinates]

            # Validate coordinates
            valid_coords = [(lon, lat) for lon, lat in zip(lons, lats)
                           if -180 <= lon <= 180 and -90 <= lat <= 90]
            if not valid_coords:
                raise ValueError("No valid coordinates found")

            lons, lats = zip(*valid_coords)

            scatter = self.ax.scatter(lons, lats, c=colors, s=sizes, alpha=alpha,
                                           marker=marker, transform=ccrs.PlateCarree(),
                                           label=layer_name, zorder=10)

            self.layers[layer_name] = {
                'type': 'points',
                'artist': scatter,
                'data': coordinates,
                'visible': True,
                'feature_count': len(valid_coords)
            }

            self.draw()
            self.layer_added.emit(layer_name)
            return scatter

        except Exception as e:
            warnings.warn(f"Error adding points: {e}")
            return None

    @error_handler
    def add_polygons(self, polygons, layer_name='polygons', **kwargs):
        """Enhanced polygon addition with property management."""
        try:
            # Get style from property manager
            layer_prop = self.property_manager.get_layer_property(layer_name)
            if layer_prop:
                mpl_style = self.symbology_manager.get_matplotlib_style(layer_name)
                facecolors = mpl_style.get('facecolor', kwargs.get('facecolors', 'blue'))
                edgecolors = mpl_style.get('edgecolor', kwargs.get('edgecolors', 'black'))
                alpha = mpl_style.get('alpha', kwargs.get('alpha', 0.5))
                linewidth = mpl_style.get('linewidth', kwargs.get('linewidth', 1))
            else:
                facecolors = kwargs.get('facecolors', 'blue')
                edgecolors = kwargs.get('edgecolors', 'black')
                alpha = kwargs.get('alpha', 0.5)
                linewidth = kwargs.get('linewidth', 1)

            patches = []
            valid_count = 0

            if isinstance(polygons, gpd.GeoDataFrame):
                for geom in polygons.geometry:
                    if geom.geom_type == 'Polygon' and not geom.is_empty:
                        coords = list(geom.exterior.coords)
                        if len(coords) >= 3:
                            patch = MPLPolygon(coords, closed=True)
                            patches.append(patch)
                            valid_count += 1
                    elif geom.geom_type == 'MultiPolygon':
                        for poly in geom.geoms:
                            if not poly.is_empty:
                                coords = list(poly.exterior.coords)
                                if len(coords) >= 3:
                                    patch = MPLPolygon(coords, closed=True)
                                    patches.append(patch)
                                    valid_count += 1
            else:
                for poly in polygons:
                    if hasattr(poly, 'exterior') and not poly.is_empty:
                        coords = list(poly.exterior.coords)
                        if len(coords) >= 3:
                            patch = MPLPolygon(coords, closed=True)
                            patches.append(patch)
                            valid_count += 1
                    elif isinstance(poly, (list, tuple)) and len(poly) >= 3:
                        patch = MPLPolygon(poly, closed=True)
                        patches.append(patch)
                        valid_count += 1

            if not patches:
                raise ValueError("No valid polygons found")

            collection = PatchCollection(patches, facecolors=facecolors,
                                       edgecolors=edgecolors, alpha=alpha,
                                       linewidths=linewidth, transform=ccrs.PlateCarree(),
                                       label=layer_name, zorder=5)

            self.ax.add_collection(collection)

            self.layers[layer_name] = {
                'type': 'polygons',
                'artist': collection,
                'data': polygons,
                'visible': True,
                'feature_count': valid_count
            }

            self.draw()
            self.layer_added.emit(layer_name)
            return collection

        except Exception as e:
            warnings.warn(f"Error adding polygons: {e}")
            return None

    @error_handler
    def add_lines(self, lines, layer_name='lines', **kwargs):
        """Enhanced line addition with property management."""
        try:
            # Get style from property manager
            layer_prop = self.property_manager.get_layer_property(layer_name)
            if layer_prop:
                mpl_style = self.symbology_manager.get_matplotlib_style(layer_name)
                colors = mpl_style.get('color', kwargs.get('colors', 'green'))
                linewidth = mpl_style.get('linewidth', kwargs.get('linewidth', 2))
                alpha = mpl_style.get('alpha', kwargs.get('alpha', 0.8))
                linestyle = mpl_style.get('linestyle', kwargs.get('linestyle', '-'))
            else:
                colors = kwargs.get('colors', 'green')
                linewidth = kwargs.get('linewidth', 2)
                alpha = kwargs.get('alpha', 0.8)
                linestyle = kwargs.get('linestyle', '-')

            line_segments = []
            valid_count = 0

            if isinstance(lines, gpd.GeoDataFrame):
                for geom in lines.geometry:
                    if geom.geom_type == 'LineString' and not geom.is_empty:
                        coords = list(geom.coords)
                        if len(coords) >= 2:
                            line_segments.append(coords)
                            valid_count += 1
                    elif geom.geom_type == 'MultiLineString':
                        for line in geom.geoms:
                            if not line.is_empty:
                                coords = list(line.coords)
                                if len(coords) >= 2:
                                    line_segments.append(coords)
                                    valid_count += 1
            else:
                for line in lines:
                    if hasattr(line, 'coords') and not line.is_empty:
                        coords = list(line.coords)
                        if len(coords) >= 2:
                            line_segments.append(coords)
                            valid_count += 1
                    elif isinstance(line, (list, tuple)) and len(line) >= 2:
                        line_segments.append(line)
                        valid_count += 1

            if not line_segments:
                raise ValueError("No valid line segments found")

            collection = LineCollection(line_segments, colors=colors,
                                      linewidths=linewidth, alpha=alpha,
                                      linestyles=linestyle, transform=ccrs.PlateCarree(),
                                      label=layer_name, zorder=7)

            self.ax.add_collection(collection)

            self.layers[layer_name] = {
                'type': 'lines',
                'artist': collection,
                'data': lines,
                'visible': True,
                'feature_count': valid_count
            }

            self.draw()
            self.layer_added.emit(layer_name)
            return collection

        except Exception as e:
            warnings.warn(f"Error adding lines: {e}")
            return None

    @error_handler
    def update_layer_display(self, layer_name: str):
        """Update the visual display of a layer with performance optimization."""
        if layer_name not in self.layers:
            return

        layer_prop = self.property_manager.get_layer_property(layer_name)
        if not layer_prop:
            return

        artist = self.layers[layer_name].get('artist')
        if not artist:
            return

        start_time = time.perf_counter()

        try:
            with QMutexLocker(self._render_lock):
                # Update visibility
                artist.set_visible(layer_prop.visible)

                # Update transparency (alpha)
                alpha = 1.0 - layer_prop.style.transparency
                artist.set_alpha(alpha)

                # Handle different layer types
                layer_type = layer_prop.metadata.layer_type

                if layer_type == 'vector':
                    self._update_vector_display(artist, layer_prop)
                elif layer_type in ['raster', 'netcdf']:
                    self._update_raster_display(artist, layer_prop)

                # Redraw the canvas
                self.draw()

                # Update performance stats
                update_time = time.perf_counter() - start_time
                self.performance_stats['update_times'].append(update_time)

        except Exception as e:
            logger.error("Error updating layer display: %s", e, exc_info=True)

    @staticmethod
    def _update_vector_display(artist, layer_prop):
        """Update vector layer display properties with enhanced styling."""
        style = layer_prop.style
        try:
            # Update color properties
            if hasattr(artist, 'set_color'):
                artist.set_color(style.color)
            if hasattr(artist, 'set_facecolor'):
                artist.set_facecolor(style.fill_color)
            if hasattr(artist, 'set_edgecolor'):
                artist.set_edgecolor(style.edge_color)

            # Update line properties
            if hasattr(artist, 'set_linewidth'):
                artist.set_linewidth(style.line_width)
            if hasattr(artist, 'set_linestyle'):
                linestyle_map = {'solid': '-', 'dashed': '--', 'dotted': ':', 'dashdot': '-.'}
                artist.set_linestyle(linestyle_map.get(style.line_style, '-'))

            # Update marker properties for scatter plots
            if hasattr(artist, 'set_sizes'):
                n_points = len(artist.get_offsets())
                artist.set_sizes([style.marker_size ** 2] * n_points)

        except Exception as e:
            logger.error("Error updating vector display: %s", e, exc_info=True)

    def _update_raster_display(self, artist, layer_prop):
        """Update raster layer display properties with enhanced options."""
        style = layer_prop.style
        try:
            # For raster data, update the colormap. The style combo is editable,
            # so the name may be anything the user typed — resolve it rather
            # than letting set_cmap raise.
            if hasattr(artist, 'set_cmap'):
                cmap, recognised = colormap_registry.resolve_colormap(style.colormap)
                if not recognised:
                    self.status_update.emit(
                        f"Unknown colormap '{style.colormap}' — using {cmap}"
                    )
                artist.set_cmap(colormap_registry.apply_reverse(cmap, style.reverse_colormap))

            # Update value range. An explicit vmin/vmax wins; otherwise a
            # diverging scale may be auto-centred on zero.
            if hasattr(artist, 'set_clim'):
                clim = colormap_registry.raster_clim(style, layer_prop.metadata.statistics)
                if clim is not None:
                    artist.set_clim(vmin=clim[0], vmax=clim[1])

        except Exception as e:
            logger.error("Error updating raster display: %s", e, exc_info=True)

    @error_handler
    def remove_layer(self, layer_name):
        """Enhanced layer removal with complete cleanup."""
        if layer_name in self.layers:
            layer = self.layers[layer_name]
            try:
                if hasattr(layer['artist'], 'remove'):
                    layer['artist'].remove()
                elif hasattr(layer['artist'], 'set_visible'):
                    layer['artist'].set_visible(False)
            except Exception as e:
                logger.warning("Could not remove artist for layer %s: %s", layer_name, e)

            # Remove from property manager
            self.property_manager.remove_layer(layer_name)

            # Remove from caches
            self._layer_cache.remove(layer_name)

            # Remove file tracking
            if 'data' in layer and isinstance(layer['data'], str) and os.path.exists(layer['data']):
                self.remove_loaded_file(layer['data'])

            # Close dataset if it's NetCDF
            if layer.get('type') == 'netcdf' and 'dataset' in layer:
                try:
                    layer['dataset'].close()
                except Exception as e:
                    logger.warning("Failed to close NetCDF dataset for layer '%s': %s", layer_name, e)

            del self.layers[layer_name]

            # Remove from layer order
            if layer_name in self.layer_order:
                self.layer_order.remove(layer_name)

            self.draw()
            self.layer_removed.emit(layer_name)

    @error_handler
    def toggle_layer(self, layer_name, visible=None):
        """Toggle layer visibility."""
        if layer_name in self.layers:
            layer = self.layers[layer_name]
            if visible is None:
                visible = not layer['visible']

            layer['artist'].set_visible(visible)
            layer['visible'] = visible

            # Update property
            self.property_manager.update_property(layer_name, 'visible', visible)

            # Hiding the described layer must hide (or re-target) the colorbar.
            self.colorbar_manager.refresh()

            self.draw()

    def clear_layers(self):
        """Enhanced layer clearing with complete cleanup."""
        for layer_name in list(self.layers.keys()):
            self.remove_layer(layer_name)
        self.loaded_files.clear()
        self._layer_cache.clear()

    def is_file_already_loaded(self, filepath):
        """Check if the file is already loaded with validation."""
        if not os.path.exists(filepath):
            return False
        abs_path = os.path.abspath(filepath)
        return abs_path in self.loaded_files

    def add_loaded_file(self, filepath):
        """Add a file to loaded files tracking."""
        abs_path = os.path.abspath(filepath)
        self.loaded_files.add(abs_path)

    def remove_loaded_file(self, filepath):
        """Remove a file from loaded files tracking."""
        abs_path = os.path.abspath(filepath)
        self.loaded_files.discard(abs_path)

    def get_loaded_files_count(self):
        """Get count of loaded files."""
        return len(self.loaded_files)

    # ============================================================================
    # NetCDF-specific Methods (from NetCDFManager)
    # ============================================================================

    def set_netcdf_variable(self, layer_name, variable):
        """Draw a NetCDF layer's other variable, and record that it does.

        Two places hold the choice and both have to move. The property
        manager's copy is what ``update_netcdf_layer`` renders from; the layer
        record's is what every reader outside the canvas takes the layer's
        variable to be — the statistics panel and the plot dock read it first
        and only fall back to the property. Updating one and not the other left
        the map on the new variable and the panels summarising the old one.

        The render happens before the signal so that a slot reacting to
        ``variable_changed`` — the colorbar, the statistics panel — sees the
        layer already showing what it is being told about.
        """
        if layer_name not in self.layers:
            return

        self.property_manager.update_property(
            layer_name, 'netcdf.current_variable', variable
        )
        self.layers[layer_name]['variable'] = variable
        self.update_netcdf_layer(layer_name)
        self.variable_changed.emit(layer_name, variable)

    def set_netcdf_time_index(self, layer_name, index):
        """Set the current time index for a NetCDF layer"""
        if layer_name not in self.layers:
            return

        self.property_manager.update_property(
            layer_name, 'netcdf.current_time_index', index
        )
        self.time_index_changed.emit(layer_name, index)

    def set_netcdf_extent(self, layer_name, extent):
        """Set the extent for a NetCDF layer"""
        if layer_name not in self.layers:
            return

        layer = self.layers[layer_name]
        if 'artist' in layer:
            layer['artist'].set_extent(extent)
            self.draw()

        # Update properties
        self.property_manager.update_property(
            layer_name,
            'dimensions.extent',
            extent
        )

    def update_netcdf_layer(self, layer_name):
        """Update the NetCDF layer visualization based on current properties"""
        if layer_name not in self.layers:
            return

        layer = self.layers[layer_name]
        if layer['type'] != 'netcdf':
            return

        # Get current properties
        props = self.property_manager.get_layer_property(layer_name)
        if not props:
            return

        # Get dataset and current settings
        ds = layer['dataset']
        variable = props.netcdf.current_variable if hasattr(props.netcdf, 'current_variable') else None
        time_index = props.netcdf.current_time_index if hasattr(props.netcdf, 'current_time_index') else 0

        if not variable or variable not in ds.data_vars:
            logger.warning("Variable '%s' not found in dataset", variable)
            return

        # Extract data based on time dimension
        time_dim_name = find_case_insensitive_key(list(ds[variable].dims), "time")
        if time_dim_name and props.netcdf.time_dimension:
            if time_index < ds.sizes.get(time_dim_name, 0):
                data = ds[variable].isel({time_dim_name: time_index}).values
            else:
                logger.warning("Time index %s out of range", time_index)
                data = ds[variable].values[0]  # Fallback to first time step
        else:
            data = ds[variable].values

        # Update extent if changed. Before the data, because the redrawn branch
        # of _set_layer_array draws into whatever bounds the record carries.
        extent = props.dimensions.extent
        if extent and len(extent) == 4:
            layer['bounds'] = list(extent)
            if self.projection_name == projections.DEFAULT_PROJECTION:
                layer['artist'].set_extent(extent)

        self._set_layer_array(layer_name, layer, data)

        # Notify canvas to redraw
        self.draw()

    @staticmethod
    def extract_netcdf_metadata(filepath):
        """Extract metadata from a NetCDF file"""
        ds = xr.open_dataset(filepath, decode_times=False)
        info = {
            "data_vars": list(ds.data_vars.keys()),
            "coords": list(ds.coords.keys()),
            "dims": dict(ds.dims)
        }
        ds.close()
        return info

    # ============================================================================
    # Additional Helper Methods
    # ============================================================================

    def save_map(self, filename, dpi=300, bbox_inches='tight', **kwargs):
        """Enhanced map saving with format validation."""
        try:
            supported_formats = ['.png', '.pdf', '.svg', '.eps', '.ps', '.tiff']
            ext = os.path.splitext(filename)[1].lower()
            if ext not in supported_formats:
                filename += '.png'

            self.fig.savefig(filename, dpi=dpi, bbox_inches=bbox_inches, **kwargs)

            render_time = time.perf_counter()
            self.performance_stats['render_times'].append(render_time)
        except Exception as e:
            raise Exception(f"Error saving map: {str(e)}")

    def set_graticule(self, visible, draw_labels=True, alpha=0.5):
        """Show or hide the lat/lon graticule.

        Only ever one Gridliner exists: turning the graticule on replaces any
        previous one rather than adding a second, so repeated toggling cannot
        stack them.
        """
        self._graticule_visible = bool(visible)
        if self._graticule_visible:
            self._install_graticule(draw_labels=draw_labels, alpha=alpha)
        else:
            self._remove_graticule()
        self.draw_idle()

    @property
    def graticule_visible(self):
        return self._graticule_visible

    def _install_graticule(self, draw_labels=True, alpha=0.5):
        """Create the Gridliner, discarding any existing one first."""
        self._remove_graticule()

        if self.theme == 'dark':
            line_color, label_color, box_color = '#7d7d7d', '#e2e2e2', '#1a1a1a'
        else:
            line_color, label_color, box_color = '#9a9a9a', '#333333', '#ffffff'

        try:
            gl = self.ax.gridlines(draw_labels=draw_labels, alpha=alpha,
                                   linestyle='--', linewidth=0.5, color=line_color)
            if draw_labels:
                gl.top_labels = False
                gl.right_labels = False
                # The axes fills the whole figure (see setup_map), so there is no
                # margin outside it to put labels in — with cartopy's default
                # positive padding every label lands off-canvas and is clipped.
                # Negative padding draws them just inside the map instead, with a
                # translucent box so they stay readable over the data.
                label_style = {
                    'size': 8,
                    'color': label_color,
                    'bbox': dict(boxstyle='round,pad=0.18', facecolor=box_color,
                                 edgecolor='none', alpha=0.65),
                }
                gl.xlabel_style = dict(label_style)
                gl.ylabel_style = dict(label_style)
                gl.xpadding = -14
                gl.ypadding = -14
            self._gridliner = gl
        except Exception as e:
            logger.error("Error adding gridlines: %s", e, exc_info=True)
            self._gridliner = None

    def _remove_graticule(self):
        """Detach the current Gridliner, including its labels."""
        gridliner = self._gridliner
        self._gridliner = None
        if gridliner is None:
            return

        try:
            # Cartopy >= 0.23 makes Gridliner a normal removable Artist.
            gridliner.remove()
            return
        except Exception:
            pass

        # Older Cartopy: the Gridliner is not removable, so hide everything it
        # owns (leaving the labels visible is the obvious failure here) and drop
        # it from the axes' private registry if that is where it lives.
        for attr in ('xline_artists', 'yline_artists',
                     'xlabel_artists', 'ylabel_artists'):
            for artist in getattr(gridliner, attr, None) or []:
                try:
                    artist.set_visible(False)
                except Exception:
                    pass
        try:
            registry = getattr(self.ax, '_gridliners', None)
            if registry is not None and gridliner in registry:
                registry.remove(gridliner)
        except Exception:
            pass

    def add_gridlines(self, draw_labels=True, alpha=0.5):
        """Turn the graticule on. Kept for backwards compatibility."""
        self.set_graticule(True, draw_labels=draw_labels, alpha=alpha)

    def add_legend(self, loc='upper right', fontsize=10):
        """Enhanced legend with better formatting."""
        try:
            if self.layers:
                legend = self.ax.legend(loc=loc, fontsize=fontsize, framealpha=0.9)
                legend.set_title("Layers")
                self.draw()
        except Exception as e:
            logger.error("Error adding legend: %s", e, exc_info=True)

    def set_mouse_mode(self, mode):
        """Enhanced mouse mode setting with validation."""
        valid_modes = ['pan', 'zoom', 'info']
        if mode not in valid_modes:
            mode = 'pan'

        self.mouse_mode = mode

        if mode == 'pan':
            self.pan_enabled = True
            self.zoom_enabled = True
        elif mode == 'zoom':
            self.pan_enabled = False
            self.zoom_enabled = True
        elif mode == 'info':
            self.pan_enabled = False
            self.zoom_enabled = False

    @monitor_performance
    def get_layer_info(self):
        """Enhanced layer information with comprehensive metadata."""
        info = {}
        for name, layer in self.layers.items():
            layer_prop = self.property_manager.get_layer_property(name)
            summary = self.property_manager.get_layer_info_summary(name) if layer_prop else {}

            load_time = layer.get('load_time', 0)
            feature_count = layer.get('feature_count', 0)

            info[name] = {
                'type': layer['type'],
                'visible': layer['visible'],
                'summary': summary,
                'performance': {
                    'load_time': f"{load_time:.2f}s" if load_time else "N/A",
                    'feature_count': feature_count
                }
            }
        return info

    @monitor_performance
    def get_zoom_info(self):
        """Enhanced zoom information with detailed metrics.

        In degrees, from the canonical lon/lat extent. The axes' own limits are
        metres outside the default projection, and a "zoom level" worked out
        from those would jump by six orders of magnitude on a projection change.
        """
        current_extent = list(self.extent)
        width = current_extent[1] - current_extent[0]
        height = current_extent[3] - current_extent[2]

        return {
            'current_extent': current_extent,
            'width': width,
            'height': height,
            'zoom_level': self.min_zoom_extent[0] / width if width > 0 else 1,
            'can_zoom_in': width > self.max_zoom_extent[0],
            'can_zoom_out': width < self.min_zoom_extent[0],
            'center': [(current_extent[0] + current_extent[1]) / 2,
                       (current_extent[2] + current_extent[3]) / 2],
            'area': width * height,
            'aspect_ratio': width / height if height > 0 else 1
        }

    def get_performance_stats(self):
        """Get performance statistics."""
        stats = {}
        for key, times in self.performance_stats.items():
            if times:
                stats[key] = {
                    'average': sum(times) / len(times),
                    'min': min(times),
                    'max': max(times),
                    'count': len(times)
                }
            else:
                stats[key] = {'average': 0, 'min': 0, 'max': 0, 'count': 0}
        return stats

    # Function for Drag and Drop
    @staticmethod
    def dragEnterEvent(event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    @staticmethod
    def dragMoveEvent(event):
        event.acceptProposedAction()

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            self.setStyleSheet("")  # reset border

            for url in event.mimeData().urls():
                path = url.toLocalFile()

                if os.path.isdir(path):
                    logger.debug("Drag-drop: folder detected: %s", path)
                    self.handle_dropped_folder(path)

                elif os.path.isfile(path):
                    logger.debug("Drag-drop: file detected: %s", path)
                    self.handle_dropped_file(path)

            event.acceptProposedAction()

    def handle_dropped_file(self, file_path):
        try:
            if not file_path:
                return

            logger.info("Drag-drop: loading file %s", file_path)

            # Use existing system
            self.load_file(file_path)

        except Exception as e:
            logger.error("Drag-drop failed: %s", e, exc_info=True)
            if hasattr(self, 'loading_error'):
                self.loading_error.emit("DragDrop Error", str(e))

    def handle_dropped_folder(self, folder_path):
        try:
            supported_ext = (
                '.nc', '.nc4', '.netcdf',
                '.tif', '.tiff',
                '.shp', '.geojson', '.kml',
                '.grb', '.grib', '.grb2'
            )

            candidates = 0
            loaded_count = 0

            for root, _, files in os.walk(folder_path):
                for file in files:
                    if file.lower().endswith(supported_ext):
                        full_path = os.path.join(root, file)
                        logger.info("Folder drop: loading %s", full_path)
                        candidates += 1
                        # Counting candidates rather than successes made this
                        # report a load for every file it merely tried.
                        if self.load_file(full_path):
                            loaded_count += 1

            if candidates == 0:
                logger.info("Folder drop: no supported files found in %s", folder_path)

            else:
                logger.info("Folder drop: loaded %s of %s files", loaded_count, candidates)

        except Exception as e:
            logger.error("Folder drop failed: %s", e, exc_info=True)
            if hasattr(self, 'loading_error'):
                self.loading_error.emit("Folder Drop Error", str(e))

    def open_time_slider(self, layer_name):
        from PyQt6.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout,
            QLabel, QSlider, QPushButton
        )
        from PyQt6.QtCore import Qt

        # Get layer
        layer = self.layers.get(layer_name)

        if not layer or layer.get('type') != 'netcdf':
            logger.info("Time slider requested for '%s', which is not a NetCDF layer", layer_name)
            return

        ds = layer.get('dataset')

        if ds is None:
            logger.warning("Time slider requested for '%s' but its dataset is missing", layer_name)
            return

        # Detect time dimension (robust)
        time_dim = None
        for dim in ds.dims:
            if dim.lower() not in ['lat', 'latitude', 'lon', 'longitude', 'x', 'y']:
                time_dim = dim
                break

        if not time_dim:
            logger.info("Time slider not shown for '%s': no time dimension found", layer_name)
            return

        # Decoded once for the whole axis so every step is labelled in the same
        # format; falls back to the raw numbers when the axis cannot be decoded.
        axis = read_time_axis(ds, time_dim)

        if len(axis.values) <= 1:
            logger.info("Time slider not shown for '%s': only one timestep", layer_name)
            return

        # Store state
        self.current_time_layer = layer_name
        self.current_time_dim = time_dim
        self.time_values = axis.values
        self.time_labels = axis.labels

        # Prevent multiple popups
        if hasattr(self, 'time_dialog') and self.time_dialog.isVisible():
            self.time_dialog.raise_()
            self.time_dialog.activateWindow()
            return

        # Create popup dialog
        self.time_dialog = QDialog(self)
        self.time_dialog.setWindowTitle(f"Time Slider — {layer_name}")
        self.time_dialog.setMinimumWidth(420)

        # Optional: always on top
        self.time_dialog.setWindowFlags(
            self.time_dialog.windowFlags() | Qt.WindowType.WindowStaysOnTopHint
        )

        layout = QVBoxLayout(self.time_dialog)

        # Top row: label + close button
        top_layout = QHBoxLayout()

        self.time_label = QLabel("Time:")
        top_layout.addWidget(self.time_label)

        layout.addLayout(top_layout)

        # Slider
        self.time_slider = QSlider(Qt.Orientation.Horizontal)
        self.time_slider.setMinimum(0)
        self.time_slider.setMaximum(len(self.time_values) - 1)
        self.time_slider.setValue(0)
        self.time_slider.setTracking(True)
        layout.addWidget(self.time_slider)

        # Connections
        self.time_slider.valueChanged.connect(self.update_time_step)

        # Initialize first frame
        self.update_time_step(0)

        # Show popup
        self.time_dialog.show()

    def update_time_step(self, index):
        layer = self.layers.get(self.current_time_layer)
        if not layer:
            return

        ds = layer.get('dataset')
        variable = layer.get('variable')

        if ds is None or variable not in ds:
            return

        data = ds[variable]

        if self.current_time_dim in data.dims:
            data = data.isel({self.current_time_dim: index})

        data = data.values

        self._set_layer_array(self.current_time_layer, layer, data)

        # Labels were decoded when the slider was built; index into them rather
        # than reformatting per step so the format cannot vary between frames.
        labels = getattr(self, 'time_labels', None) or []
        if 0 <= index < len(labels):
            time_str = labels[index]
        else:
            time_str = str(self.time_values[index])

        self.time_label.setText(f"Time: {time_str}")

        # Keep the property manager and every other time-aware widget — the
        # animation dock, the property editor, the colorbar — in step with the
        # standalone slider, which until now moved the data without telling
        # anyone. Listeners guard their own re-entry.
        try:
            self.property_manager.update_property(
                self.current_time_layer, 'netcdf.current_time_index', index
            )
        except Exception as exc:
            logger.debug("Could not record time index for '%s': %s",
                         self.current_time_layer, exc)
        self.time_index_changed.emit(self.current_time_layer, index)

        self.draw()

    def cleanup(self):
        """Enhanced cleanup for proper resource management."""
        try:
            # Close all NetCDF datasets
            for layer_name, layer in self.layers.items():
                if layer.get('type') == 'netcdf' and 'dataset' in layer:
                    try:
                        layer['dataset'].close()
                    except Exception as e:
                        logger.warning("Failed to close NetCDF dataset for layer '%s' during cleanup: %s", layer_name, e)

            # Clear all caches
            self._layer_cache.clear()
            self._method_cache.clear()

            # Shutdown thread pool
            self.thread_pool.shutdown(wait=True)

            # Stop timers
            if hasattr(self, 'cache_cleanup_timer'):
                self.cache_cleanup_timer.stop()
            if hasattr(self, 'performance_timer'):
                self.performance_timer.stop()
            if hasattr(self, '_basemap_timer'):
                self._basemap_timer.stop()

        except Exception as e:
            logger.error("Error during canvas cleanup: %s", e, exc_info=True)

    def __del__(self):
        """Destructor for proper cleanup."""
        self.cleanup()
