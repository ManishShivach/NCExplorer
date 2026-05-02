"""
NetCDF-specific functionality for GeoCanvas
"""

import xarray as xr
import netCDF4
import scipy
import h5netcdf
import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from .properties import LayerProperty, find_case_insensitive_key


class NetCDFManager(QObject):
    """Manages NetCDF-specific functionality"""

    variable_changed = pyqtSignal(str, str)  # layer_name, variable_name
    time_index_changed = pyqtSignal(str, int)  # layer_name, time_index

    def __init__(self, canvas):
        super().__init__()
        self.canvas = canvas

    def load_netcdf_file(self, filepath, layer_name):
        try:
            ds = xr.open_dataset(filepath, decode_times=False) # Keep time as numerical
            variables = list(ds.data_vars.keys())

            # Improved coordinate detection
            lon_vars = ['lon', 'longitude', 'X', 'x', 'LONGITUDE']
            lat_vars = ['lat', 'latitude', 'Y', 'y', 'LATITUDE']
            lon, lat = None, None

            for var in lon_vars:
                if var in ds.coords:
                    lon = ds[var]
                    break
                if var in ds.data_vars:
                    lon = ds[var]
                    break
                if var in ds.dims:
                    lon = ds[var]
                    break
            for var in lat_vars:
                if var in ds.coords:
                    lat = ds[var]
                    break
                if var in ds.data_vars:
                    lat = ds[var]
                    break
                if var in ds.dims:
                    lat = ds[var]
                    break

            if lon is None or lat is None:
                raise ValueError("Could not find standard longitude/latitude coordinate variables.")

            extent = [
                np.min(lon.values), np.max(lon.values),
                np.min(lat.values), np.max(lat.values)
            ]

            # Update properties
            props = self.canvas.property_manager.get_property(layer_name, '')
            if not props:
                props = LayerProperty()
                self.canvas.property_manager.add_layer(layer_name, props)
            if not hasattr(props, "netcdf") or props.netcdf is None:
                props.netcdf = type('NetCDFMeta', (), {})()
            props.netcdf.available_variables = variables
            props.netcdf.current_variable = variables[0] if variables else None

            time_dim_name = find_case_insensitive_key(list(ds.dims.keys()), "time")
            if time_dim_name:
                props.netcdf.time_dimension = time_dim_name
                if time_dim_name in ds:
                    props.netcdf.time_values = ds[time_dim_name].values.tolist()
                else:
                    props.netcdf.time_values = list(range(int(ds.sizes.get(time_dim_name, 0))))

            props.metadata.source_file = filepath
            props.dimensions.extent = extent

            # Store dimension information
            if hasattr(ds[variables[0]], 'dims'):
                if len(ds[variables[0]].shape) >= 2:
                    props.dimensions.width = ds[variables[0]].shape[-1]
                    props.dimensions.height = ds[variables[0]].shape[-2]
                else:
                    props.dimensions.width = 1
                    props.dimensions.height = 1


            self.canvas.layer_manager.add_layer(
                filepath=filepath,
                layer_name=layer_name,
                layer_type='netcdf',
                dataset=ds,
                extent=extent,
            )

            return True
        except Exception as e:
            error_msg = f"Error loading NetCDF file '{filepath}': {e}"
            print(error_msg)
            self.canvas.loading_error.emit("load_netcdf_file", error_msg)
            return False

    def visualize_netcdf_layer(self, layer_name):
        """Visualize loaded NetCDF layer on canvas"""
        try:
            props = self.canvas.property_manager.get_property(layer_name)
            if not props or not hasattr(props, 'netcdf'):
                return False

            # Get the NetCDF dataset
            ds = props.netcdf.dataset if hasattr(props.netcdf, 'dataset') else None
            if ds is None:
                return False

            # Get current variable
            var_name = props.netcdf.current_variable
            if var_name not in ds.data_vars:
                return False

            # Plot the data
            data = ds[var_name]

            # Handle time dimension if present
            if 'time' in data.dims and len(data.dims) > 2:
                data = data.isel(time=0)  # Select first time step

            # Plot using pcolormesh or similar
            lon = props.netcdf.longitude
            lat = props.netcdf.latitude

            im = self.canvas.ax.pcolormesh(
                lon, lat, data,
                transform=ccrs.PlateCarree(),
                cmap='viridis',
                alpha=0.8
            )

            # Zoom to layer extent
            extent = props.dimensions.extent
            self.canvas.ax.set_extent(extent, crs=ccrs.PlateCarree())

            # Force redraw
            self.canvas.draw()

            return True

        except Exception as e:
            print(f"Error visualizing NetCDF layer {layer_name}: {e}")
            return False

    def update_netcdf_layer(self, layer_name):
        """Update the NetCDF layer visualization based on current properties"""
        if layer_name not in self.canvas.layer_manager.layers:
            return

        layer = self.canvas.layer_manager.layers[layer_name]
        if layer['type'] != 'netcdf':
            return

        # Get current properties
        props = self.canvas.property_manager.get_property(layer_name, '')
        if not props:
            return

        # Get dataset and current settings
        ds = layer['dataset']
        variable = props.netcdf.current_variable
        time_index = props.netcdf.current_time_index

        # Validate variable exists
        if variable not in ds.data_vars:
            print(f"Variable '{variable}' not found in dataset")
            return

        # Extract data based on time dimension
        if 'time' in ds[variable].dims and props.netcdf.time_dimension:
            if time_index < len(ds['time']):
                data = ds[variable].isel(time=time_index).values
            else:
                print(f"Time index {time_index} out of range")
                data = ds[variable].values[0]  # Fallback to first time step
        else:
            data = ds[variable].values

        # Update the artist with new data
        layer['artist'].set_data(data)

        # Update colormap if needed (handled by symbology manager)

        # Update extent if changed (get from properties)
        extent = props.dimensions.extent
        if extent and len(extent) == 4:
            layer['artist'].set_extent(extent)

        # Notify canvas to redraw
        self.canvas.draw()

        # Update metadata in properties
        props.metadata.name = layer_name
        props.metadata.layer_type = 'netcdf'
        props.metadata.source_file = layer['filepath']

        # Store dimension information
        if hasattr(ds[variable], 'dims'):
            props.dimensions.width = ds[variable].shape[-1]
            props.dimensions.height = ds[variable].shape[-2]

    def set_variable(self, layer_name, variable):
        """Set the current variable for a NetCDF layer"""
        if layer_name not in self.canvas.layer_manager.layers:
            return

        self.canvas.property_manager.update_property(
            layer_name, 'netcdf.current_variable', variable
        )
        self.variable_changed.emit(layer_name, variable)

    def set_time_index(self, layer_name, index):
        """Set the current time index for a NetCDF layer"""
        if layer_name not in self.canvas.layer_manager.layers:
            return

        self.canvas.property_manager.update_property(
            layer_name, 'netcdf.current_time_index', index
        )
        self.time_index_changed.emit(layer_name, index)

    def set_extent(self, layer_name, extent):
        """Set the extent for a NetCDF layer"""
        if layer_name not in self.canvas.layer_manager.layers:
            return

        layer = self.canvas.layer_manager.layers[layer_name]
        if 'artist' in layer:
            layer['artist'].set_extent(extent)
            self.canvas.draw()

        # Update properties
        self.canvas.property_manager.update_property(
            layer_name,
            'dimensions.extent',
            extent
        )

    @staticmethod
    def extract_netcdf_metadata(filepath):
        ds = xr.open_dataset(filepath, decode_times=False)
        # Returns dict of metadata for a netCDF xarray Dataset
        info = {
            "data_vars": list(ds.data_vars.keys()),
            "coords": list(ds.coords.keys()),
            "dims": dict(ds.dims)
        }
        return info
