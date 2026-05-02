"""
Enhanced Shapefile Display Manager
"""

import os
import geopandas as gpd
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection, PatchCollection
from matplotlib.patches import Polygon
import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal, QThread


class ShapefileDisplayManager(QObject):
    """Professional shapefile display with QGIS/ArcGIS-like capabilities"""

    rendering_started = pyqtSignal(str)
    rendering_finished = pyqtSignal(str)
    rendering_progress = pyqtSignal(int)
    rendering_error = pyqtSignal(str, str)  # layer_name, error_message

    def __init__(self, canvas):
        super().__init__()
        self.canvas = canvas
        self.display_cache = {}
        self.performance_settings = {
            'max_features_full_render': 10000,
            'simplification_tolerance': 0.001,
            'use_spatial_index': True,
            'cache_rendered_features': True
        }

    def add_shapefile_layer(self, filepath, layer_name=None, styling_options=None):
        """Add a shapefile with professional rendering capabilities"""
        try:
            # Validate shapefile components
            if not self._validate_shapefile_components(filepath):
                raise ValueError("Incomplete shapefile - missing required components")

            # Load shapefile with geopandas
            gdf = gpd.read_file(filepath)

            if layer_name is None:
                layer_name = os.path.splitext(os.path.basename(filepath))[0]

            # Apply default styling if not provided
            if styling_options is None:
                styling_options = self._get_default_styling(gdf.geometry.type.iloc[0])

            # Determine the rendering strategy based on feature count
            feature_count = len(gdf)
            if feature_count > self.performance_settings['max_features_full_render']:
                return self._add_large_shapefile(gdf, layer_name, styling_options, filepath)
            else:
                return self._add_standard_shapefile(gdf, layer_name, styling_options, filepath)

        except Exception as e:
            error_msg = f"Failed to load shapefile: {str(e)}"
            self.rendering_error.emit(layer_name or "Unknown Layer", error_msg)
            raise ValueError(error_msg)

    @staticmethod
    def _validate_shapefile_components(shp_path):
        """Validate that all required shapefile components exist"""
        base_path = os.path.splitext(shp_path)[0]
        required_extensions = ['.shp', '.dbf', '.shx']

        for ext in required_extensions:
            if not os.path.exists(base_path + ext):
                print(f"Warning: Missing required file: {base_path + ext}")
                return False

        # Check for a projection file (optional but recommended)
        if not os.path.exists(base_path + '.prj'):
            print(f"Warning: Missing projection file: {base_path}.prj")

        return True

    @staticmethod
    def _get_default_styling(geometry_type):
        """Get default styling based on a geometry type (similar to QGIS)"""
        # Generate random colors similar to QGIS
        colors = ['#FF6B6B', '#4ECDC4', '#45B7D1', '#FFA07A', '#98D8C8', '#F7DC6F', '#BB8FCE']
        random_color = np.random.choice(colors)

        if geometry_type in ['Point', 'MultiPoint']:
            return {
                'marker_color': random_color,
                'marker_size': 8,
                'marker_style': 'o',
                'edge_color': 'black',
                'edge_width': 0.5,
                'alpha': 0.8
            }
        elif geometry_type in ['LineString', 'MultiLineString']:
            return {
                'line_color': random_color,
                'line_width': 1.5,
                'line_style': '-',
                'alpha': 0.8
            }
        else:  # Polygons
            return {
                'face_color': random_color,
                'edge_color': 'black',
                'edge_width': 0.5,
                'alpha': 0.7
            }

    def _add_standard_shapefile(self, gdf, layer_name, styling, filepath):
        """Add a shapefile with standard rendering (similar to the QGIS approach)"""
        # Ensure proper CRS
        if gdf.crs is None:
            gdf = gdf.set_crs("EPSG:4326")
        elif gdf.crs != "EPSG:4326":
            gdf = gdf.to_crs("EPSG:4326")

        geometry_type = gdf.geometry.type.iloc[0]

        # Render based on a geometry type
        if geometry_type in ['Point', 'MultiPoint']:
            artist = self._render_points(gdf, styling)
        elif geometry_type in ['LineString', 'MultiLineString']:
            artist = self._render_lines(gdf, styling)
        else:  # Polygons
            artist = self._render_polygons(gdf, styling)

        # Store layer information
        layer_info = {
            'type': 'shapefile',
            'geometry_type': geometry_type,
            'data': gdf,
            'artist': artist,
            'filepath': filepath,
            'feature_count': len(gdf),
            'styling': styling,
            'visible': True
        }

        return layer_info

    def _add_large_shapefile(self, gdf, layer_name, styling, filepath):
        """Add a large shapefile with performance optimizations (ArcGIS approach)"""
        self.rendering_started.emit(layer_name)

        # Simplify geometry for better performance
        if self.performance_settings['simplification_tolerance'] > 0:
            gdf_simplified = gdf.copy()
            gdf_simplified['geometry'] = gdf_simplified['geometry'].simplify(
                self.performance_settings['simplification_tolerance']
            )
        else:
            gdf_simplified = gdf

        # Create a spatial index if enabled
        if self.performance_settings['use_spatial_index']:
            spatial_index = gdf_simplified.sindex

        # Render with progress reporting
        layer_info = self._add_standard_shapefile(gdf_simplified, layer_name, styling, filepath)
        layer_info['is_simplified'] = True
        layer_info['original_data'] = gdf  # Keep original for detailed operations

        self.rendering_finished.emit(layer_name)
        return layer_info

    def _render_points(self, gdf, styling):
        """Render point geometries"""
        # Extract coordinates
        coords = [(point.x, point.y) for point in gdf.geometry]
        x_coords, y_coords = zip(*coords) if coords else ([], [])

        # Create scatter plot
        scatter = self.canvas.ax.scatter(
            x_coords, y_coords,
            c=styling['marker_color'],
            s=styling['marker_size'] ** 2,  # matplotlib uses area
            marker=styling['marker_style'],
            edgecolors=styling['edge_color'],
            linewidths=styling['edge_width'],
            alpha=styling['alpha'],
            transform=self.canvas.projection,
            zorder=5
        )

        return scatter

    def _render_lines(self, gdf, styling):
        """Render line geometries"""
        line_segments = []

        for geom in gdf.geometry:
            if geom.geom_type == 'LineString':
                line_segments.append(list(geom.coords))
            elif geom.geom_type == 'MultiLineString':
                for line in geom:
                    line_segments.append(list(line.coords))

        # Create a line collection for better performance
        line_collection = LineCollection(
            line_segments,
            colors=styling['line_color'],
            linewidths=styling['line_width'],
            linestyles=styling['line_style'],
            alpha=styling['alpha'],
            transform=self.canvas.projection,
            zorder=3
        )

        self.canvas.ax.add_collection(line_collection)
        return line_collection

    def _render_polygons(self, gdf, styling):
        """Render polygon geometries"""
        patches = []

        for geom in gdf.geometry:
            if geom.geom_type == 'Polygon':
                # Extract exterior coordinates
                exterior_coords = list(geom.exterior.coords)
                polygon = Polygon(exterior_coords, closed=True)
                patches.append(polygon)
            elif geom.geom_type == 'MultiPolygon':
                for poly in geom:
                    exterior_coords = list(poly.exterior.coords)
                    polygon = Polygon(exterior_coords, closed=True)
                    patches.append(polygon)

        # Create a patch collection for better performance
        patch_collection = PatchCollection(
            patches,
            facecolors=styling['face_color'],
            edgecolors=styling['edge_color'],
            linewidths=styling['edge_width'],
            alpha=styling['alpha'],
            transform=self.canvas.projection,
            zorder=2
        )

        self.canvas.ax.add_collection(patch_collection)
        return patch_collection

    def update_layer_styling(self, layer_info, new_styling):
        """Update layer styling (similar to the QGIS properties dialogs)"""
        artist = layer_info['artist']
        geometry_type = layer_info['geometry_type']

        if geometry_type in ['Point', 'MultiPoint']:
            if hasattr(artist, 'set_color'):
                artist.set_color(new_styling['marker_color'])
            if hasattr(artist, 'set_sizes'):
                artist.set_sizes([new_styling['marker_size'] ** 2] * len(layer_info['data']))

        elif geometry_type in ['LineString', 'MultiLineString']:
            if hasattr(artist, 'set_color'):
                artist.set_color(new_styling['line_color'])
            if hasattr(artist, 'set_linewidth'):
                artist.set_linewidth(new_styling['line_width'])

        else:  # Polygons
            if hasattr(artist, 'set_facecolor'):
                artist.set_facecolor(new_styling['face_color'])
            if hasattr(artist, 'set_edgecolor'):
                artist.set_edgecolor(new_styling['edge_color'])

        # Update stored styling
        layer_info['styling'] = new_styling

        # Redraw canvas
        self.canvas.draw()
