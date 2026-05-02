"""
Symbology Manager for GeoCanvas
===============================

Handles symbol styling and visual representation of layers.
"""

import matplotlib.pyplot as plt
from matplotlib.colors import to_hex
from PyQt6.QtCore import QObject, pyqtSignal

class SymbologyManager(QObject):
    """Manager for layer symbology and styling."""

    symbology_changed = pyqtSignal(str)  # layer_name

    def __init__(self, property_manager, canvas):
        """Initialize symbology manager.

        Args:
            property_manager: LayerPropertyManager instance
            canvas: GeoCanvas instance
        """
        super().__init__()
        self.property_manager = property_manager
        self.canvas = canvas

        # Default style mappings
        self.default_styles = {
            'vector': {
                'point': {
                    'color': '#FF0000',
                    'marker': 'o',
                    's': 50,
                    'alpha': 0.7
                },
                'line': {
                    'color': '#00FF00',
                    'linewidth': 2,
                    'alpha': 0.8,
                    'linestyle': '-'
                },
                'polygon': {
                    'facecolor': '#0000FF',
                    'edgecolor': '#000000',
                    'alpha': 0.5,
                    'linewidth': 1
                }
            },
            'raster': {
                'cmap': 'viridis',
                'alpha': 0.8,
                'interpolation': 'nearest'
            }
        }

    def get_matplotlib_style(self, layer_name: str) -> dict:
        """Get matplotlib style parameters for a layer.

        Args:
            layer_name: Name of the layer

        Returns:
            Dictionary of matplotlib style parameters
        """
        layer_prop = self.property_manager.get_layer_property(layer_name)
        if not layer_prop:
            return {}

        style = layer_prop.style
        layer_type = layer_prop.metadata.layer_type
        geometry_type = layer_prop.metadata.geometry_type.lower() if layer_prop.metadata.geometry_type else ""

        mpl_style = {}

        if layer_type == 'vector':
            # Vector styling
            if 'point' in geometry_type:
                mpl_style.update({
                    'color': style.color,
                    'marker': self._get_marker_symbol(style.marker_style),
                    's': style.marker_size ** 2,  # matplotlib expects area
                    'alpha': 1.0 - style.transparency
                })
            elif 'line' in geometry_type:
                mpl_style.update({
                    'color': style.color,
                    'linewidth': style.line_width,
                    'alpha': 1.0 - style.transparency,
                    'linestyle': self._get_line_style(style.line_style)
                })
            elif 'polygon' in geometry_type:
                mpl_style.update({
                    'facecolor': style.fill_color,
                    'edgecolor': style.edge_color,
                    'alpha': 1.0 - style.transparency,
                    'linewidth': style.line_width
                })

        elif layer_type in ['raster', 'netcdf']:
            # Raster styling
            cmap = style.colormap
            if style.reverse_colormap:
                cmap = f"{cmap}_r"

            mpl_style.update({
                'cmap': cmap,
                'alpha': 1.0 - style.transparency,
                'interpolation': style.interpolation
            })

            if style.vmin is not None:
                mpl_style['vmin'] = style.vmin
            if style.vmax is not None:
                mpl_style['vmax'] = style.vmax

        return mpl_style

    def _get_marker_symbol(self, marker_style: str) -> str:
        """Convert marker style name to matplotlib marker symbol."""
        marker_map = {
            'circle': 'o',
            'square': 's',
            'triangle': '^',
            'diamond': 'D',
            'star': '*',
            'plus': '+',
            'cross': 'x'
        }
        return marker_map.get(marker_style, 'o')

    def _get_line_style(self, line_style: str) -> str:
        """Convert line style name to matplotlib line style."""
        line_map = {
            'solid': '-',
            'dashed': '--',
            'dotted': ':',
            'dashdot': '-.'
        }
        return line_map.get(line_style, '-')

    def apply_style_to_artist(self, layer_name: str, artist):
        """Apply style properties to a matplotlib artist.

        Args:
            layer_name: Name of the layer
            artist: Matplotlib artist object
        """
        style_dict = self.get_matplotlib_style(layer_name)

        try:
            # Apply common properties
            if 'alpha' in style_dict and hasattr(artist, 'set_alpha'):
                artist.set_alpha(style_dict['alpha'])

            # Apply color properties
            if 'color' in style_dict and hasattr(artist, 'set_color'):
                artist.set_color(style_dict['color'])
            if 'facecolor' in style_dict and hasattr(artist, 'set_facecolor'):
                artist.set_facecolor(style_dict['facecolor'])
            if 'edgecolor' in style_dict and hasattr(artist, 'set_edgecolor'):
                artist.set_edgecolor(style_dict['edgecolor'])

            # Apply line properties
            if 'linewidth' in style_dict and hasattr(artist, 'set_linewidth'):
                artist.set_linewidth(style_dict['linewidth'])
            if 'linestyle' in style_dict and hasattr(artist, 'set_linestyle'):
                artist.set_linestyle(style_dict['linestyle'])

            # Apply marker properties for scatter plots
            if 'marker' in style_dict and hasattr(artist, 'set_marker'):
                artist.set_marker(style_dict['marker'])
            if 's' in style_dict and hasattr(artist, 'set_sizes'):
                n_points = len(artist.get_offsets()) if hasattr(artist, 'get_offsets') else 1
                artist.set_sizes([style_dict['s']] * n_points)

            # Apply raster properties
            if 'cmap' in style_dict and hasattr(artist, 'set_cmap'):
                artist.set_cmap(style_dict['cmap'])
            if 'vmin' in style_dict or 'vmax' in style_dict:
                if hasattr(artist, 'set_clim'):
                    vmin = style_dict.get('vmin')
                    vmax = style_dict.get('vmax')
                    artist.set_clim(vmin=vmin, vmax=vmax)

        except Exception as e:
            print(f"Warning: Could not apply some style properties to artist: {e}")

    def get_color_palette(self, name: str = 'default', n_colors: int = 10):
        """Get a color palette for styling multiple layers.

        Args:
            name: Name of the color palette
            n_colors: Number of colors to generate

        Returns:
            List of hex color strings
        """
        if name == 'default':
            # Use matplotlib's default color cycle
            prop_cycle = plt.rcParams['axes.prop_cycle']
            colors = prop_cycle.by_key()['color']

            # Extend if needed
            while len(colors) < n_colors:
                colors.extend(colors)

            return colors[:n_colors]

        elif name == 'categorical':
            # Use a categorical color map
            try:
                cmap = plt.cm.get_cmap('tab10')
                colors = [to_hex(cmap(i / n_colors)) for i in range(n_colors)]
                return colors
            except:
                # Fallback to default
                return self.get_color_palette('default', n_colors)

        else:
            # Try to use as matplotlib colormap
            try:
                cmap = plt.cm.get_cmap(name)
                colors = [to_hex(cmap(i / n_colors)) for i in range(n_colors)]
                return colors
            except:
                # Fallback to default
                return self.get_color_palette('default', n_colors)

    def auto_style_layer(self, layer_name: str, style_type: str = 'auto'):
        """Automatically apply styling to a layer.

        Args:
            layer_name: Name of the layer to style
            style_type: Type of styling ('auto', 'qualitative', 'quantitative')
        """
        layer_prop = self.property_manager.get_layer_property(layer_name)
        if not layer_prop:
            return

        layer_type = layer_prop.metadata.layer_type
        geometry_type = layer_prop.metadata.geometry_type

        # Apply default styling based on a geometry type
        if layer_type == 'vector' and geometry_type:
            if 'point' in geometry_type.lower():
                defaults = self.default_styles['vector']['point']
                layer_prop.style.color = defaults['color']
                layer_prop.style.marker_size = defaults['s'] ** 0.5  # Convert back from area
                layer_prop.style.transparency = 1.0 - defaults['alpha']

            elif 'line' in geometry_type.lower():
                defaults = self.default_styles['vector']['line']
                layer_prop.style.color = defaults['color']
                layer_prop.style.line_width = defaults['linewidth']
                layer_prop.style.transparency = 1.0 - defaults['alpha']

            elif 'polygon' in geometry_type.lower():
                defaults = self.default_styles['vector']['polygon']
                layer_prop.style.fill_color = defaults['facecolor']
                layer_prop.style.edge_color = defaults['edgecolor']
                layer_prop.style.line_width = defaults['linewidth']
                layer_prop.style.transparency = 1.0 - defaults['alpha']

        elif layer_type in ['raster', 'netcdf']:
            defaults = self.default_styles['raster']
            layer_prop.style.colormap = defaults['cmap']
            layer_prop.style.transparency = 1.0 - defaults['alpha']
            layer_prop.style.interpolation = defaults['interpolation']

        # Emit signal that symbology changed
        self.symbology_changed.emit(layer_name)

    @staticmethod
    def get_available_colormaps():
        """Get list of available matplotlib colormaps."""
        return [
            'viridis', 'plasma', 'inferno', 'magma', 'cividis',
            'gray', 'hot', 'cool', 'spring', 'summer', 'autumn', 'winter',
            'bone', 'copper', 'terrain', 'rainbow', 'jet', 'hsv',
            'Spectral', 'coolwarm', 'seismic', 'RdYlBu', 'RdYlGn',
            'tab10', 'tab20', 'Set1', 'Set2', 'Set3', 'Pastel1', 'Pastel2'
        ]

    @staticmethod
    def get_available_markers():
        """Get list of available marker styles."""
        return ['circle', 'square', 'triangle', 'diamond', 'star', 'plus', 'cross']

    @staticmethod
    def get_available_line_styles():
        """Get list of available line styles."""
        return ['solid', 'dashed', 'dotted', 'dashdot']

    def update_layer_symbology(self, layer_name: str):
        """Update layer visualization after symbology changes.

        Args:
            layer_name: Name of the layer to update
        """
        # This will trigger the layer manager to update the display
        self.symbology_changed.emit(layer_name)
