"""
Layer Manager for GeoCanvas
===========================

Handles layer management including loading, display, and geometry operations.
"""

import os
import time
import warnings
import numpy as np
import geopandas as gpd
import rasterio
import xarray as xr
import cartopy.crs as ccrs
from PyQt6.QtWidgets import QMessageBox, QListWidgetItem
from matplotlib.patches import Polygon as MPLPolygon
from matplotlib.collections import PatchCollection, LineCollection

from .properties import LayerProperty, NetCDFProperties, find_case_insensitive_key

import logging
import threading
logger = logging.getLogger(__name__)


class LayerCache:
    """LRU cache for layer-related data."""

    def __init__(self, max_size=100):
        self.max_size = max_size
        self._cache = {}
        self._access_order = []
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            if key in self._cache:
                self._access_order.remove(key)
                self._access_order.append(key)
                return self._cache[key]
            return None

    def put(self, key, value):
        with self._lock:
            if key in self._cache:
                self._access_order.remove(key)
            elif len(self._cache) >= self.max_size:
                oldest = self._access_order.pop(0)
                del self._cache[oldest]
            self._cache[key] = value
            self._access_order.append(key)

    def clear(self):
        with self._lock:
            self._cache.clear()
            self._access_order.clear()

    def remove(self, key):
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                self._access_order.remove(key)


def error_handler(func):
    """Error handling decorator."""

    def wrapper(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        except Exception as e:
            error_msg = f"Error in {func.__name__}: {str(e)}"
            print(f"[Layer Error] {error_msg}")
            if hasattr(self.canvas, 'loading_error'):
                self.canvas.loading_error.emit(func.__name__, error_msg)
            return None

    return wrapper

class NetCDFBandManager:
    """Manager for NetCDF band and time navigation."""

    def __init__(self, property_manager, canvas):
        self.property_manager = property_manager
        self.canvas = canvas

    def load_netcdf_file(self, filepath, layer_name):
        """Load NetCDF metadata."""
        try:
            ds = xr.open_dataset(filepath)

            layer_prop = self.property_manager.get_layer_property(layer_name)
            if not layer_prop:
                return False

            if not layer_prop.netcdf:
                layer_prop.netcdf = NetCDFProperties()

            # Extract metadata
            layer_prop.netcdf.variables = list(ds.data_vars.keys())
            layer_prop.netcdf.coordinate_variables = list(ds.coords.keys())
            layer_prop.netcdf.attributes = dict(ds.attrs)

            # Handle time dimension
            time_dim_name = find_case_insensitive_key(list(ds.dims.keys()), "time")
            if time_dim_name:
                layer_prop.netcdf.time_dimension = time_dim_name
                if time_dim_name in ds:
                    layer_prop.netcdf.time_values = ds[time_dim_name].values.tolist()
                else:
                    layer_prop.netcdf.time_values = list(range(int(ds.sizes.get(time_dim_name, 0))))

            # Handle other dimensions
            layer_prop.netcdf.dimensions_info = {dim: size for dim, size in ds.sizes.items()}

            ds.close()
            return True

        except Exception as e:
            print(f"Error loading NetCDF metadata: {e}")
            return False

class LayerManager:
    """Manager for layer operations with comprehensive functionality."""

    def __init__(self, property_manager, canvas):
        self.invisibletimestamps = None
        self.property_manager = property_manager
        self.canvas = canvas
        self.layers = {}  # matplotlib artists
        self.layer_order = []  # Draw order
        self.loaded_files = set()
        self._layer_cache = LayerCache(max_size=50)
        self.netcdf_manager = NetCDFBandManager(property_manager, canvas)
        # --- Add a z-order counter for layer stacking ---
        self._z_order_counter = 1

    def add_layer(self, layer_name, **layer_properties):
        """
        Generic method to add a new layer to the manager.
        It now manages the z-order for stacking.
        """
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

        if 'zorder' not in defaults and defaults.get('artist'):
            defaults['artist'].set_zorder(self._z_order_counter)
            self._z_order_counter += 1

        self.layers[layer_name] = defaults

        if defaults['filepath'] != 'N/A':
            self.add_loaded_file(defaults['filepath'])

        print(f"Layer '{layer_name}' registered with z-order: {defaults['artist'].get_zorder() if defaults.get('artist') else 'N/A'}.")

    @error_handler
    def load_netcdf(self, filepath, layer_name=None, variable=None, time_index=0, alpha=0.8, cmap='viridis'):
        """Enhanced NetCDF loading with comprehensive error handling and optimization."""
        start_time = time.perf_counter()

        try:
            if not os.path.exists(filepath):
                raise FileNotFoundError(f"File not found: {filepath}")

            if self.is_file_already_loaded(filepath):
                filename = os.path.basename(filepath)
                self.canvas.file_already_loaded.emit(filename)
                return None, None

            if layer_name is None:
                layer_name = os.path.splitext(os.path.basename(filepath))[0]

            self.canvas.progress_update.emit(10)

            # Create a layer property
            layer_prop = LayerProperty()
            layer_prop.metadata.name = layer_name
            layer_prop.metadata.layer_type = "netcdf"
            layer_prop.metadata.source_file = filepath
            layer_prop.metadata.file_size = os.path.getsize(filepath)
            layer_prop.netcdf = NetCDFProperties()

            self.canvas.progress_update.emit(20)

            # Load NetCDF with the band manager
            success = self.netcdf_manager.load_netcdf_file(filepath, layer_name)
            if not success:
                raise Exception("Failed to load NetCDF metadata")

            self.canvas.progress_update.emit(40)

            # Open dataset and load data
            ds = xr.open_dataset(filepath)
            if variable is None:
                data_vars = list(ds.data_vars.keys())
                if not data_vars:
                    raise ValueError("No data variables found")
                variable = data_vars[0]

            data_array = ds[variable]
            self.canvas.progress_update.emit(60)

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

            self.canvas.progress_update.emit(70)

            # Get coordinates with multiple fallbacks
            lons, lats = self._extract_coordinates(data_array)
            data = data_array.values

            if data.ndim != 2:
                raise ValueError(f"Data must be 2D for visualization, got shape {data.shape}")

            if np.all(np.isnan(data)):
                raise ValueError("All data values are NaN")

            self.canvas.progress_update.emit(80)

            # Create extent and display
            extent = [lons.min(), lons.max(), lats.min(), lats.max()]

            # Add to property manager
            self.property_manager.add_layer(layer_name, layer_prop)

            # Create image
            try:
                im = self.canvas.ax.imshow(data, extent=extent, transform=ccrs.PlateCarree(),
                                         alpha=alpha, cmap=cmap, origin='lower')
            except Exception:
                im = self.canvas.ax.imshow(data, extent=extent, transform=ccrs.PlateCarree(),
                                         alpha=alpha, cmap=cmap, origin='upper')

            self.canvas.progress_update.emit(90)

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
            self.canvas.set_extent(extent)
            self.canvas.draw()
            self.canvas.layer_added.emit(layer_name)
            self.canvas.file_loaded.emit(filepath, 'netcdf')

            load_time = time.perf_counter() - start_time
            self.canvas.performance_stats['load_times'].append(load_time)
            self.canvas.progress_update.emit(100)
            self.canvas.status_update.emit(f"NetCDF loaded: {os.path.basename(filepath)} ({load_time:.2f}s)")

            return data, extent

        except Exception as e:
            error_msg = f"Error loading NetCDF: {str(e)}"
            print(f"[NetCDF Error] {error_msg}")
            self.canvas.loading_error.emit("NetCDF Error", error_msg)
            self.canvas.progress_update.emit(0)
            return None, None

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
                self.canvas.file_already_loaded.emit(filename)
                return None, None

            ext = os.path.splitext(filepath)[1].lower()
            if ext in ['.nc', '.nc4', '.netcdf']:
                return self.load_netcdf(filepath, layer_name, alpha=alpha, cmap=cmap)

            if layer_name is None:
                layer_name = os.path.splitext(os.path.basename(filepath))[0]

            self.canvas.progress_update.emit(10)

            # Create layer property
            layer_prop = LayerProperty()
            layer_prop.metadata.name = layer_name
            layer_prop.metadata.layer_type = "raster"
            layer_prop.metadata.source_file = filepath
            layer_prop.metadata.file_size = os.path.getsize(filepath)

            self.canvas.progress_update.emit(30)

            with rasterio.open(filepath) as src:
                if src.count == 0:
                    raise ValueError("Raster has no bands")

                data = src.read(1)  # Read first band
                transform = src.transform
                bounds = src.bounds

                self.canvas.progress_update.emit(60)

                # Handle no-data values
                if src.nodata is not None:
                    data = np.ma.masked_equal(data, src.nodata)
                    layer_prop.metadata.no_data_value = src.nodata

                if data.size == 0:
                    raise ValueError("Raster contains no data")

                extent = [bounds.left, bounds.right, bounds.bottom, bounds.top]
                self.canvas.progress_update.emit(70)

                # Add to property manager
                self.property_manager.add_layer(layer_name, layer_prop)

                # Create image
                im = self.canvas.ax.imshow(data, extent=extent, transform=ccrs.PlateCarree(),
                                         alpha=alpha, cmap=cmap, origin='upper')

                self.canvas.progress_update.emit(85)

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

                self.canvas.progress_update.emit(95)

                # Track file and emit signals
                self.add_loaded_file(filepath)
                self.canvas.set_extent(extent)
                self.canvas.draw()
                self.canvas.layer_added.emit(layer_name)
                self.canvas.file_loaded.emit(filepath, 'raster')

                load_time = time.perf_counter() - start_time
                self.canvas.performance_stats['load_times'].append(load_time)
                self.canvas.progress_update.emit(100)
                self.canvas.status_update.emit(f"Raster loaded: {os.path.basename(filepath)} ({load_time:.2f}s)")

                return data, bounds

        except Exception as e:
            error_msg = f"Error loading raster: {str(e)}"
            print(f"[Raster Error] {error_msg}")
            self.canvas.loading_error.emit("Raster Error", error_msg)
            self.canvas.progress_update.emit(0)
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
                self.canvas.file_already_loaded.emit(filename)
                return None

            self.canvas.progress_update.emit(20)

            gdf = gpd.read_file(filepath)
            if len(gdf) == 0:
                raise ValueError("Shapefile contains no features")

            if layer_name is None:
                layer_name = os.path.splitext(os.path.basename(filepath))[0]

            self.canvas.progress_update.emit(40)

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

            self.canvas.progress_update.emit(60)

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

            self.canvas.progress_update.emit(80)

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
            self.canvas.set_extent([bounds[0], bounds[2], bounds[1], bounds[3]])
            self.canvas.file_loaded.emit(filepath, 'shapefile')

            load_time = time.perf_counter() - start_time
            self.canvas.performance_stats['load_times'].append(load_time)
            self.canvas.progress_update.emit(100)
            self.canvas.status_update.emit(f"Vector loaded: {os.path.basename(filepath)} ({load_time:.2f}s)")

            return gdf

        except Exception as e:
            error_msg = f"Error loading shapefile: {str(e)}"
            self.canvas.loading_error.emit("Shapefile Error", error_msg)
            self.canvas.progress_update.emit(0)
            return None

    @error_handler
    def add_points(self, coordinates, layer_name='points', **kwargs):
        """Enhanced point addition with property management."""
        try:
            # Get style from property manager
            layer_prop = self.property_manager.get_layer_property(layer_name)
            if layer_prop:
                from symbology import SymbologyManager
                mpl_style = self.canvas.symbology_manager.get_matplotlib_style(layer_name)
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

            scatter = self.canvas.ax.scatter(lons, lats, c=colors, s=sizes, alpha=alpha,
                                           marker=marker, transform=ccrs.PlateCarree(),
                                           label=layer_name, zorder=10)

            self.layers[layer_name] = {
                'type': 'points',
                'artist': scatter,
                'data': coordinates,
                'visible': True,
                'feature_count': len(valid_coords)
            }

            self.canvas.draw()
            self.canvas.layer_added.emit(layer_name)
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
                mpl_style = self.canvas.symbology_manager.get_matplotlib_style(layer_name)
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

            self.canvas.ax.add_collection(collection)

            self.layers[layer_name] = {
                'type': 'polygons',
                'artist': collection,
                'data': polygons,
                'visible': True,
                'feature_count': valid_count
            }

            self.canvas.draw()
            self.canvas.layer_added.emit(layer_name)
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
                mpl_style = self.canvas.symbology_manager.get_matplotlib_style(layer_name)
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

            self.canvas.ax.add_collection(collection)

            self.layers[layer_name] = {
                'type': 'lines',
                'artist': collection,
                'data': lines,
                'visible': True,
                'feature_count': valid_count
            }

            self.canvas.draw()
            self.canvas.layer_added.emit(layer_name)
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
            with self.canvas._render_lock:
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
                self.canvas.draw()

                # Update performance stats
                update_time = time.perf_counter() - start_time
                self.canvas.performance_stats['update_times'].append(update_time)

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

            self.canvas.draw()
            self.canvas.layer_removed.emit(layer_name)

    @error_handler
    def toggle_layer(self, layer_name, visible=None):
        """Toggle layer visibility."""
        if layer_name in self.layers:
            layer = self.layers[layer_name]
            if visible is None:
                visible = not layer['visible']

            layer['artist'].set_visible(visible)
            layer['visible'] = visible

            self.property_manager.update_property(layer_name, 'visible', visible)

            self.canvas.draw()

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

    def update_layer_list(self):
        """Update the list widget/UI to reflect all loaded layers."""
        if hasattr(self, "layerListWidget"):
            self.layerListWidget.clear()
            for layer_name in self.layers.keys():
                item = QListWidgetItem(layer_name)
                self.layerListWidget.addItem(item)
        # If no GUI widget, print current layers for debug
        else:
            print("Current Layers:", list(self.layers.keys()))

    def update_statistics(self):
        """Update the layer statistics summary in the GUI or console."""
        num_layers = len(self.layers)
        types = [prop.layertype if hasattr(prop, 'layertype') else 'Unknown'
                 for prop in self.layers.values()]
        type_counts = {t: types.count(t) for t in set(types)}
        if hasattr(self, "statsLabel"):
            stats_text = f"Layers: {num_layers}\n"
            for t, count in type_counts.items():
                stats_text += f"{t.capitalize()}: {count}\n"
            self.statsLabel.setText(stats_text)
        else:
            print(f"Layer Count: {num_layers}")
            print(f"Layer Types: {type_counts}")

    def clear_all_layers(self):
        """Clear all layers from the layer manager and update the GUI."""
        if not self.layers:
            return
        reply = QMessageBox.question(
            self,
            title="Clear All Layers",
            text="Are you sure you want to remove all layers?",
            buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.layers.clear()
            self.invisibletimestamps.clear()
            self.update_layer_list()
            self.update_statistics()
            logger.info("Cleared all layers")

    def cleanup(self):
        """Clean up layer manager resources"""
        if hasattr(self, 'cleanup_timer'):
            self.cleanup_timer.stop()
        self.clear_all_layers()
