"""
Main GeoCanvas implementation for NCExplorer visualization
"""

import os
import time
import logging
import warnings
import functools
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import geopandas as gpd
import rasterio
import xarray as xr
import cartopy.crs as ccrs
import cartopy.feature as cfeature

from PyQt6.QtCore import pyqtSignal, Qt, QPoint, QTimer, QMutex, QObject
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QCursor, QAction
from PyQt6.QtWidgets import QFileDialog, QMenu, QMessageBox
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.patches import Polygon as MPLPolygon
from matplotlib.collections import PatchCollection, LineCollection

from .properties import LayerPropertyManager, LayerProperty, NetCDFProperties, find_case_insensitive_key
from .symbology import SymbologyManager
from .layers import LayerCache

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

        print(f"[GeoCanvas] {func.__name__} executed in {end_time - start_time:.4f}s")
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
            print(f"[GeoCanvas Error] {error_msg}")
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

        # Map properties
        self.projection_name = 'PlateCarree'
        self.projection = ccrs.PlateCarree()
        self.ax = None
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

        # File format support
        self.supported_vector_formats = ['.shp', '.geojson', '.kml', '.gpx']
        self.supported_raster_formats = ['.tif', '.tiff', '.jpg', '.jpeg', '.png', '.nc', '.nc4', '.netcdf']
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

        except Exception as e:
            print(f"Error connecting property signals: {e}")

    def on_property_changed(self, layer_name: str, property_path: str, value):
        """Handle property changes with optimized updates."""
        print(f"[Property] {layer_name}.{property_path} = {value}")

        # Update layer display if it's a visual property
        visual_properties = ['style.', 'visible', 'transparency']
        if any(prop in property_path for prop in visual_properties):
            self.update_layer_display(layer_name)

    def on_layer_property_added(self, layer_name: str):
        """Handle new layer property addition."""
        print(f"[Property] Layer property added: {layer_name}")
        self.layer_added.emit(layer_name)

    def on_layer_property_removed(self, layer_name: str):
        """Handle layer property removal."""
        print(f"[Property] Layer property removed: {layer_name}")
        self.layer_removed.emit(layer_name)

    @monitor_performance
    def apply_theme(self, theme=None):
        """Apply a visual theme to the map with enhanced styling and no padding."""
        if theme:
            self.theme = theme

        self.ax.clear()
        self.ax = self.fig.add_subplot(111, projection=self.projection)

        # Ensure no padding
        self.ax.set_position((0, 0, 1, 1))
        self.fig.subplots_adjust(left=0, bottom=0, right=1, top=1, wspace=0, hspace=0)

        # Remove all borders and axis elements
        self.ax.spines['top'].set_visible(False)
        self.ax.spines['right'].set_visible(False)
        self.ax.spines['bottom'].set_visible(False)
        self.ax.spines['left'].set_visible(False)
        self.ax.set_xticks([])
        self.ax.set_yticks([])

        self.ax.set_global()

        # Enhanced theme styling
        if self.theme == 'light':
            self.ax.add_feature(cfeature.LAND, color='#f5f5f5', alpha=0.8, zorder=0)
            self.ax.add_feature(cfeature.OCEAN, color='#e6f3ff', alpha=0.8, zorder=0)
            self.ax.add_feature(cfeature.COASTLINE, color='#666666', linewidth=0.5, zorder=1)
            self.ax.add_feature(cfeature.BORDERS, color='#999999', linewidth=0.3, zorder=1)
            self.fig.patch.set_facecolor('white')
        elif self.theme == 'dark':
            self.ax.add_feature(cfeature.LAND, color='#2d2d2d', alpha=0.9, zorder=0)
            self.ax.add_feature(cfeature.OCEAN, color='#1a1a1a', alpha=0.9, zorder=0)
            self.ax.add_feature(cfeature.COASTLINE, color='#cccccc', linewidth=0.5, zorder=1)
            self.ax.add_feature(cfeature.BORDERS, color='#888888', linewidth=0.3, zorder=1)
            self.fig.patch.set_facecolor('#1a1a1a')
            self.ax.set_facecolor('#1a1a1a')

        self.draw()

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

    def setup_map(self):
        """Initialize the map with enhanced setup and no padding."""
        self.fig.clear()

        # Create subplot with no padding
        self.ax = self.fig.add_subplot(111, projection=self.projection)

        # Remove all whitespace around the plot
        self.fig.subplots_adjust(left=0, bottom=0, right=1, top=1, wspace=0, hspace=0)

        # Set the axis to fill the entire figure
        self.ax.set_position((0, 0, 1, 1))

        # Remove axis spines (borders)
        self.ax.spines['top'].set_visible(False)
        self.ax.spines['right'].set_visible(False)
        self.ax.spines['bottom'].set_visible(False)
        self.ax.spines['left'].set_visible(False)

        # Remove tick marks and labels
        self.ax.set_xticks([])
        self.ax.set_yticks([])

        self._set_constrained_extent(self.extent)
        self.apply_theme()

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
        """Set the extent with constraints applied."""
        constrained_extent = self._constrain_extent(extent)
        self.ax.set_extent(constrained_extent, crs=ccrs.PlateCarree())
        self.extent = constrained_extent
        return constrained_extent

    def connect_events(self):
        """Connect matplotlib events to PyQt6 signals."""
        self.mpl_connect('button_press_event', self._on_click)

    def setup_mouse_controls(self):
        """Setup enhanced mouse controls for pan and zoom."""
        self.mpl_connect('button_press_event', self._on_mouse_press)
        self.mpl_connect('button_release_event', self._on_mouse_release)
        self.mpl_connect('motion_notify_event', self._on_mouse_move)
        self.mpl_connect('scroll_event', self._on_scroll)

    def _on_click(self, event):
        """Handle map click events with coordinate validation."""
        if event.inaxes == self.ax and event.xdata and event.ydata:
            lon, lat = event.xdata, event.ydata
            if -180 <= lon <= 180 and -90 <= lat <= 90:
                self.map_clicked.emit(lat, lon)

    def _on_mouse_press(self, event):
        """Handle mouse press for pan/zoom with drag threshold."""
        if event.inaxes != self.ax:
            return
        if event.button == 1 and self.pan_enabled:
            self.press_event = event
            self.last_extent = self.ax.get_extent()

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
        if self.press_event is None or event.inaxes != self.ax:
            return
        if not self.pan_enabled:
            return

        if event.xdata is None or event.ydata is None:
            return
        if self.press_event.xdata is None or self.press_event.ydata is None:
            return

        dx = event.xdata - self.press_event.xdata
        dy = event.ydata - self.press_event.ydata

        if dx is None or dy is None:
            return

        current_extent = self.ax.get_extent()
        new_extent = [
            current_extent[0] - dx, current_extent[1] - dx,
            current_extent[2] - dy, current_extent[3] - dy
        ]

        constrained_extent = self._constrain_extent(new_extent)
        self.ax.set_extent(constrained_extent, crs=self.projection)
        self.extent = constrained_extent
        self.draw()

    def _on_scroll(self, event):
        """Enhanced mouse scroll for zooming - mouse-centered with smooth operation."""
        if event.inaxes != self.ax or not self.zoom_enabled:
            return

        current_extent = self.ax.get_extent()
        current_width = current_extent[1] - current_extent[0]

        # Adaptive zoom factor
        base_zoom = 0.9 if event.step > 0 else 1.1
        zoom_factor = base_zoom

        if current_width < 10:
            zoom_factor = 0.95 if event.step > 0 else 1.05
        elif current_width < 50:
            zoom_factor = 0.92 if event.step > 0 else 1.08

        mouse_x, mouse_y = event.xdata, event.ydata
        if mouse_x is None or mouse_y is None:
            return

        current_height = current_extent[3] - current_extent[2]
        new_width = current_width * zoom_factor
        new_height = current_height * zoom_factor

        mouse_frac_x = (mouse_x - current_extent[0]) / current_width
        mouse_frac_y = (mouse_y - current_extent[2]) / current_height

        new_extent = [
            mouse_x - new_width * mouse_frac_x,
            mouse_x + new_width * (1 - mouse_frac_x),
            mouse_y - new_height * mouse_frac_y,
            mouse_y + new_height * (1 - mouse_frac_y)
        ]

        constrained_extent = self._constrain_extent(new_extent)
        self.ax.set_extent(constrained_extent, crs=self.projection)
        self.extent = constrained_extent
        self.draw()
        self.extent_changed.emit(constrained_extent)

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
            self.zoom_history.append(self.extent)
            if len(self.zoom_history) > self.max_zoom_history:
                self.zoom_history.pop(0)
            self.set_extent(extent)

    def zoom_previous(self):
        """Zoom to previous extent."""
        if self.zoom_history:
            extent = self.zoom_history.pop()
            self.set_extent(extent)

    def set_extent(self, extent, crs=None):
        """Enhanced extent setting with validation and history."""
        if crs is None:
            crs = ccrs.PlateCarree()

        if len(extent) != 4:
            return

        min_lon, max_lon, min_lat, max_lat = extent
        if min_lon >= max_lon or min_lat >= max_lat:
            return

        if self.extent != extent:
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
            print(f"Warning: Layer '{layer_name}' already exists. Overwriting.")

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

        print(f"Layer '{layer_name}' reNCExplorertered with z-order: {defaults['artist'].get_zorder() if defaults.get('artist') else 'N/A'}.")

    @error_handler
    def load_file(self, filepath):
        """Load a file (shapefile, raster, NetCDF) onto the canvas."""
        if not os.path.exists(filepath):
            self.loading_error.emit("load_file", f"File not found: {filepath}")
            return

        layer_name = os.path.splitext(os.path.basename(filepath))[0]
        file_ext = os.path.splitext(filepath)[1].lower()

        self.status_update.emit(f"Loading {filepath}...")
        self.progress_update.emit(10)

        try:
            if file_ext in ['.shp', '.geojson']:
                self.load_shapefile(filepath, layer_name)
            elif file_ext in ['.tif', '.tiff', '.img']:
                self.load_raster(filepath, layer_name)
            elif file_ext in ['.nc', '.nc4', '.netcdf']:
                self.load_netcdf(filepath, layer_name)
            else:
                self.loading_error.emit("load_file", f"Unsupported file type: {file_ext}")
                return

            self.file_loaded.emit(filepath, layer_name)
            self.layer_added.emit(layer_name)
            self.status_update.emit(f"Loaded {layer_name} successfully.")
            self.progress_update.emit(100)
            self.draw()

        except Exception as e:
            error_msg = f"Failed to load {filepath}: {str(e)}"
            print(f"[GeoCanvas Error] {error_msg}")
            self.loading_error.emit("load_file", error_msg)
            self.status_update.emit("Loading failed.")
            self.progress_update.emit(0)

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

            # Open dataset and load data
            ds = xr.open_dataset(filepath)
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

            # Store layer info
            self.layers[layer_name] = {
                'type': 'netcdf',
                'artist': im,
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
            print(f"[NetCDF Error] {error_msg}")
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

            # Handle time dimension
            time_dim_name = find_case_insensitive_key(list(ds.dims.keys()), "time")
            if time_dim_name:
                layer_prop.netcdf.time_dimension = time_dim_name
                if time_dim_name in ds:
                    layer_prop.netcdf.time_values = ds[time_dim_name].values.tolist()
                else:
                    layer_prop.netcdf.time_values = list(range(int(ds.sizes.get(time_dim_name, 0))))
            else:
                time_coord_name = find_case_insensitive_key(list(ds.coords.keys()), "time")
                if time_coord_name:
                    layer_prop.netcdf.time_dimension = time_coord_name
                    layer_prop.netcdf.time_values = ds[time_coord_name].values.tolist()

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
            print(f"Error loading NetCDF metadata: {e}")
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
    def load_raster(self, filepath, layer_name=None, alpha=0.8, cmap='viridis'):
        """Enhanced raster loading with comprehensive validation and optimization."""
        start_time = time.perf_counter()

        try:
            if not os.path.exists(filepath):
                raise FileNotFoundError(f"File not found: {filepath}")

            if self.is_file_already_loaded(filepath):
                filename = os.path.basename(filepath)
                self.file_already_loaded.emit(filename)
                return None, None

            ext = os.path.splitext(filepath)[1].lower()
            if ext in ['.nc', '.nc4', '.netcdf']:
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

            with rasterio.open(filepath) as src:
                if src.count == 0:
                    raise ValueError("Raster has no bands")

                data = src.read(1)  # Read first band
                transform = src.transform
                bounds = src.bounds

                self.progress_update.emit(60)

                # Handle no-data values
                if src.nodata is not None:
                    data = np.ma.masked_equal(data, src.nodata)
                    layer_prop.metadata.no_data_value = src.nodata

                if data.size == 0:
                    raise ValueError("Raster contains no data")

                extent = [bounds.left, bounds.right, bounds.bottom, bounds.top]
                self.progress_update.emit(70)

                # Add to property manager
                self.property_manager.add_layer(layer_name, layer_prop)

                # Create image
                im = self.ax.imshow(data, extent=extent, transform=ccrs.PlateCarree(),
                                         alpha=alpha, cmap=cmap, origin='upper')

                self.progress_update.emit(85)

                # Store layer
                self.layers[layer_name] = {
                    'type': 'raster',
                    'artist': im,
                    'data': filepath,
                    'bounds': bounds,
                    'visible': True,
                    'load_time': time.perf_counter() - start_time,
                    'crs': str(src.crs) if src.crs else None
                }

                # Update properties
                layer_prop.dimensions.width = src.width
                layer_prop.dimensions.height = src.height
                layer_prop.dimensions.extent = extent
                layer_prop.dimensions.crs = str(src.crs) if src.crs else None
                layer_prop.dimensions.pixel_size_x = abs(transform.a)
                layer_prop.dimensions.pixel_size_y = abs(transform.e)
                layer_prop.style.transparency = 1.0 - alpha

                # Calculate statistics
                if not np.ma.is_masked(data):
                    valid_data = data[~np.isnan(data)] if np.any(np.isnan(data)) else data
                    if len(valid_data) > 0:
                        layer_prop.metadata.statistics = {
                            'min': float(np.min(valid_data)),
                            'max': float(np.max(valid_data)),
                            'mean': float(np.mean(valid_data)),
                            'std': float(np.std(valid_data)),
                            'data_type': str(data.dtype)
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
                self.status_update.emit(f"Raster loaded: {os.path.basename(filepath)} ({load_time:.2f}s)")

                return data, bounds

        except Exception as e:
            error_msg = f"Error loading raster: {str(e)}"
            print(f"[Raster Error] {error_msg}")
            self.loading_error.emit("Raster Error", error_msg)
            self.progress_update.emit(0)
            return None, None

    @error_handler
    def load_shapefile(self, filepath, layer_name=None):
        """Enhanced shapefile loading with comprehensive validation."""
        start_time = time.perf_counter()

        try:
            if not os.path.exists(filepath):
                raise FileNotFoundError(f"File not found: {filepath}")

            if self.is_file_already_loaded(filepath):
                filename = os.path.basename(filepath)
                self.file_already_loaded.emit(filename)
                return None

            self.progress_update.emit(20)

            gdf = gpd.read_file(filepath)
            if len(gdf) == 0:
                raise ValueError("Shapefile contains no features")

            if layer_name is None:
                layer_name = os.path.splitext(os.path.basename(filepath))[0]

            self.progress_update.emit(40)

            # Create layer property
            layer_prop = LayerProperty()
            layer_prop.metadata.name = layer_name
            layer_prop.metadata.layer_type = "vector"
            layer_prop.metadata.source_file = filepath
            layer_prop.metadata.file_size = os.path.getsize(filepath)

            # Handle CRS
            if gdf.crs is None:
                gdf.crs = 'EPSG:4326'
                warnings.warn("No CRS found, assuming WGS84")

            self.progress_update.emit(60)

            # Add to property manager first
            self.property_manager.add_layer(layer_name, layer_prop)

            # Determine geometry type and add to map
            geom_type = gdf.geometry.iloc[0].geom_type
            layer_prop.metadata.geometry_type = geom_type

            if geom_type in ['Point', 'MultiPoint']:
                coordinates = [(geom.y, geom.x) for geom in gdf.geometry if not geom.is_empty]
                artist = self.add_points(coordinates, layer_name=layer_name)
            elif geom_type in ['Polygon', 'MultiPolygon']:
                polygons = [geom for geom in gdf.geometry if not geom.is_empty]
                artist = self.add_polygons(polygons, layer_name=layer_name)
            elif geom_type in ['LineString', 'MultiLineString']:
                lines = [geom for geom in gdf.geometry if not geom.is_empty]
                artist = self.add_lines(lines, layer_name=layer_name)

            self.progress_update.emit(80)

            # Update properties
            bounds = gdf.total_bounds
            layer_prop.dimensions.extent = [bounds[0], bounds[2], bounds[1], bounds[3]]
            layer_prop.dimensions.crs = str(gdf.crs)

            # Store attribute information
            if not gdf.empty:
                layer_prop.metadata.attributes = {
                    'columns': list(gdf.columns),
                    'feature_count': len(gdf),
                    'sample_attributes': gdf.iloc[0].to_dict() if len(gdf) > 0 else {}
                }

            # Track file and signals
            self.add_loaded_file(filepath)
            self.set_extent([bounds[0], bounds[2], bounds[1], bounds[3]])
            self.file_loaded.emit(filepath, 'shapefile')

            load_time = time.perf_counter() - start_time
            self.performance_stats['load_times'].append(load_time)
            self.progress_update.emit(100)
            self.status_update.emit(f"Vector loaded: {os.path.basename(filepath)} ({load_time:.2f}s)")

            return gdf

        except Exception as e:
            error_msg = f"Error loading shapefile: {str(e)}"
            self.loading_error.emit("Shapefile Error", error_msg)
            self.progress_update.emit(0)
            return None

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
            with self._render_lock:
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
            print(f"Error updating layer display: {e}")

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
            print(f"Error updating vector display: {e}")

    @staticmethod
    def _update_raster_display(artist, layer_prop):
        """Update raster layer display properties with enhanced options."""
        style = layer_prop.style
        try:
            # For raster data, update the colormap
            if hasattr(artist, 'set_cmap'):
                cmap = style.colormap
                if style.reverse_colormap:
                    cmap = f"{cmap}_r"
                artist.set_cmap(cmap)

            # Update value range
            if hasattr(artist, 'set_clim'):
                if style.vmin is not None or style.vmax is not None:
                    artist.set_clim(vmin=style.vmin, vmax=style.vmax)

        except Exception as e:
            print(f"Error updating raster display: {e}")

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
                print(f"Warning: Could not remove artist for layer {layer_name}: {e}")

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
                    logging.warning("Failed to close NetCDF dataset for layer '%s': %s", layer_name, e)

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
        """Set the current variable for a NetCDF layer"""
        if layer_name not in self.layers:
            return

        self.property_manager.update_property(
            layer_name, 'netcdf.current_variable', variable
        )
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
            print(f"Variable '{variable}' not found in dataset")
            return

        # Extract data based on time dimension
        time_dim_name = find_case_insensitive_key(list(ds[variable].dims), "time")
        if time_dim_name and props.netcdf.time_dimension:
            if time_index < ds.sizes.get(time_dim_name, 0):
                data = ds[variable].isel({time_dim_name: time_index}).values
            else:
                print(f"Time index {time_index} out of range")
                data = ds[variable].values[0]  # Fallback to first time step
        else:
            data = ds[variable].values

        # Update the artist with new data
        layer['artist'].set_data(data)

        # Update extent if changed
        extent = props.dimensions.extent
        if extent and len(extent) == 4:
            layer['artist'].set_extent(extent)

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

    def add_gridlines(self, draw_labels=True, alpha=0.5):
        """Enhanced gridlines with better styling and label positioning."""
        try:
            gl = self.ax.gridlines(draw_labels=draw_labels, alpha=alpha,
                                   linestyle='--', linewidth=0.5, color='gray')
            if draw_labels:
                gl.top_labels = False
                gl.right_labels = False
                gl.xlabel_style = {'size': 8, 'color': 'black'}
                gl.ylabel_style = {'size': 8, 'color': 'black'}
                gl.xpadding = 5
                gl.ypadding = 5

            self.draw()
        except Exception as e:
            print(f"Error adding gridlines: {e}")

    def add_legend(self, loc='upper right', fontsize=10):
        """Enhanced legend with better formatting."""
        try:
            if self.layers:
                legend = self.ax.legend(loc=loc, fontsize=fontsize, framealpha=0.9)
                legend.set_title("Layers")
                self.draw()
        except Exception as e:
            print(f"Error adding legend: {e}")

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
        """Enhanced zoom information with detailed metrics."""
        current_extent = self.ax.get_extent()
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
                    print(f"[DragDrop] Folder detected: {path}")
                    self.handle_dropped_folder(path)

                elif os.path.isfile(path):
                    print(f"[DragDrop] File detected: {path}")
                    self.handle_dropped_file(path)

            event.acceptProposedAction()

    def handle_dropped_file(self, file_path):
        try:
            if not file_path:
                return

            print(f"[DragDrop] Loading file: {file_path}")

            # Use existing system
            self.load_file(file_path)

        except Exception as e:
            print(f"[DragDrop Error] {e}")
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

            loaded_count = 0

            for root, _, files in os.walk(folder_path):
                for file in files:
                    if file.lower().endswith(supported_ext):
                        full_path = os.path.join(root, file)
                        print(f"[FolderDrop] Loading: {full_path}")
                        self.load_file(full_path)
                        loaded_count += 1

            if loaded_count == 0:
                print("[FolderDrop] No supported files found.")

            else:
                print(f"[FolderDrop] Loaded {loaded_count} files.")

        except Exception as e:
            print(f"[FolderDrop Error] {e}")
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
            print("[TimeSlider] Not a NetCDF layer")
            return

        ds = layer.get('dataset')

        if ds is None:
            print("[TimeSlider] Dataset missing")
            return

        # Detect time dimension (robust)
        time_dim = None
        for dim in ds.dims:
            if dim.lower() not in ['lat', 'latitude', 'lon', 'longitude', 'x', 'y']:
                time_dim = dim
                break

        if not time_dim:
            print("[TimeSlider] No time dimension found")
            return

        time_values = ds[time_dim].values

        if len(time_values) <= 1:
            print("[TimeSlider] Only one timestep — slider not needed")
            return

        # Store state
        self.current_time_layer = layer_name
        self.current_time_dim = time_dim
        self.time_values = time_values

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
        self.time_slider.setMaximum(len(time_values) - 1)
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

        layer['artist'].set_data(data)

        # Format time
        time_val = self.time_values[index]

        try:
            import pandas as pd
            time_str = pd.to_datetime(time_val).strftime('%Y-%m-%d')
        except:
            time_str = str(time_val)

        self.time_label.setText(f"Time: {time_str}")

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
                        logging.warning("Failed to close NetCDF dataset for layer '%s' during cleanup: %s", layer_name, e)

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

        except Exception as e:
            print(f"Error during cleanup: {e}")

    def __del__(self):
        """Destructor for proper cleanup."""
        self.cleanup()
