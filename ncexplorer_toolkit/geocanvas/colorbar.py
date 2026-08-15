"""On-map colorbar for raster / NetCDF layers.

The canvas deliberately gives its GeoAxes the whole figure
(``ax.set_position((0, 0, 1, 1))``, see :meth:`GeoCanvas.setup_map`), so the
usual ``fig.colorbar(im, ax=ax)`` cannot be used: it steals space from the axes
it is attached to and would shrink the map, leaving a white gutter. Instead this
manager creates its own small axes with ``fig.add_axes`` *overlaying* the map and
positions it itself, in figure-fraction coordinates so it tracks resizes for
free.

The colorbar is rebuilt from scratch on every :meth:`refresh`. That keeps the
state simple, but it makes removal of the previous axes mandatory — a forgotten
``cax`` stays in ``fig.axes`` forever and every redraw gets slower. See
:meth:`clear`.
"""

import logging

from PyQt6.QtCore import QObject

from matplotlib.patches import Rectangle

logger = logging.getLogger(__name__)


class ColorbarManager(QObject):
    """Owns the single colorbar drawn over the map."""

    #: Supported placements, mapped to the figure-fraction rect of the bar
    #: itself and of the translucent backdrop drawn behind bar + labels.
    #:
    #: Three things already own parts of the map and must not be covered:
    #: the on-canvas navigation cluster (bottom-right, ~40x170 px — see
    #: gui/nav_overlay.reposition), the basemap attribution (bottom-right, see
    #: GeoCanvas._update_basemap_attribution, whose text can run well to the
    #: left) and the scale bar (bottom-left). Hence the vertical bars start
    #: above the nav cluster and the horizontal ones stay centred.
    GEOMETRY = {
        # position:  (bar rect,                         backdrop rect,                  orientation, tick side)
        'right':  ((0.893, 0.320, 0.016, 0.590), (0.875, 0.265, 0.120, 0.700), 'vertical', 'right'),
        'left':   ((0.030, 0.320, 0.016, 0.590), (0.008, 0.265, 0.120, 0.700), 'vertical', 'right'),
        'bottom': ((0.300, 0.115, 0.400, 0.016), (0.258, 0.055, 0.484, 0.105), 'horizontal', 'bottom'),
        'top':    ((0.300, 0.905, 0.400, 0.016), (0.258, 0.880, 0.484, 0.105), 'horizontal', 'top'),
    }

    POSITIONS = tuple(GEOMETRY)
    DEFAULT_POSITION = 'right'

    #: Layer types that carry a colour scale worth showing.
    SCALAR_TYPES = ('netcdf', 'raster')

    def __init__(self, canvas):
        super().__init__(canvas)
        self.canvas = canvas
        self._cax = None            # the colorbar's own axes
        self._colorbar = None       # matplotlib Colorbar
        self._backdrop = None       # translucent patch behind bar + labels
        self._requested_layer = None  # explicit target, or None for "topmost"
        self._visible = False
        self._position = self.DEFAULT_POSITION
        #: Text of the label currently drawn beside the bar ('' when hidden).
        self.label = ""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @property
    def visible(self):
        return self._visible

    @property
    def position(self):
        return self._position

    @property
    def layer_name(self):
        """The layer the colorbar is currently describing, or None."""
        return self._active_layer_name()

    def set_visible(self, visible):
        """Show or hide the colorbar."""
        self._visible = bool(visible)
        self.refresh()

    def set_position(self, position):
        """Move the colorbar. Unknown positions are ignored."""
        if position not in self.GEOMETRY:
            return
        self._position = position
        self.refresh()

    def set_target_layer(self, layer_name):
        """Pin the colorbar to a specific layer.

        ``None`` restores the default behaviour of following the topmost visible
        raster/NetCDF layer.
        """
        self._requested_layer = layer_name
        self.refresh()

    def refresh(self):
        """Rebuild the colorbar from the current layer state."""
        self.clear()

        if not self._visible:
            self.canvas.draw_idle()
            return

        layer_name = self._active_layer_name()
        if layer_name is None:
            # Nothing scalar on the map — stay hidden rather than showing an
            # empty bar.
            self.canvas.draw_idle()
            return

        try:
            self._build(layer_name)
        except Exception as exc:  # pragma: no cover - defensive, as elsewhere
            logger.warning("Could not build colorbar: %s", exc)
            self.clear()

        self.canvas.draw_idle()

    def clear(self):
        """Remove the colorbar and its axes.

        Every path that rebuilds must come through here first: ``fig.add_axes``
        appends unconditionally, so skipping removal leaks an axes per refresh.
        """
        if self._colorbar is not None:
            try:
                self._colorbar.remove()
            except Exception:
                pass
            self._colorbar = None

        # Colorbar.remove() normally takes its axes with it, but not on every
        # matplotlib version and not if construction failed half-way — drop the
        # axes explicitly if it is still attached.
        if self._cax is not None:
            try:
                if self._cax in self.canvas.fig.axes:
                    self._cax.remove()
            except Exception:
                pass
            self._cax = None

        if self._backdrop is not None:
            try:
                self._backdrop.remove()
            except Exception:
                pass
            self._backdrop = None

        self.label = ""

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _candidate_layers(self):
        """Visible raster/NetCDF layers, topmost first.

        The canvas owns this ordering (it also drives the hover readout) and it
        already skips artists orphaned by a theme change, which would otherwise
        raise when handed to fig.colorbar.
        """
        return self.canvas.scalar_layer_order()

    def _active_layer_name(self):
        """Resolve which layer the colorbar should describe."""
        candidates = self._candidate_layers()
        if not candidates:
            return None
        if self._requested_layer in candidates:
            return self._requested_layer
        return candidates[0]

    def _theme_colors(self):
        """Foreground / backdrop colours for the current canvas theme."""
        if getattr(self.canvas, 'theme', 'light') == 'dark':
            return '#e8e8e8', '#1a1a1a'
        return '#1a1a1a', '#ffffff'

    def _build(self, layer_name):
        """Create the axes, the colorbar and its backdrop for one layer."""
        record = self.canvas.layers[layer_name]
        artist = record['artist']
        fig = self.canvas.fig

        bar_rect, back_rect, orientation, tick_side = self.GEOMETRY[self._position]
        fg, bg = self._theme_colors()

        # Backdrop first so it sits under the bar and its labels. Drawn in
        # figure coordinates and added to the figure (not the map axes) so it
        # cannot be cleared by a map redraw.
        self._backdrop = Rectangle(
            (back_rect[0], back_rect[1]), back_rect[2], back_rect[3],
            transform=fig.transFigure, facecolor=bg, edgecolor='none',
            alpha=0.72, zorder=8,
        )
        fig.add_artist(self._backdrop)

        self._cax = fig.add_axes(bar_rect, zorder=9)
        self._colorbar = fig.colorbar(artist, cax=self._cax, orientation=orientation)

        self.label = self._build_label(record)
        if self.label:
            self._colorbar.set_label(self.label, color=fg, fontsize=8)

        # Theme the ticks, their labels and the outline; matplotlib's defaults
        # are black, which vanishes against the dark theme.
        self._cax.tick_params(
            labelsize=7, colors=fg, direction='out', length=2.5, width=0.6,
        )
        if orientation == 'vertical':
            self._cax.yaxis.set_ticks_position(tick_side)
            self._cax.yaxis.set_label_position(tick_side)
        else:
            self._cax.xaxis.set_ticks_position(tick_side)
            self._cax.xaxis.set_label_position(tick_side)

        try:
            self._colorbar.outline.set_edgecolor(fg)
            self._colorbar.outline.set_linewidth(0.6)
        except Exception:
            pass

    def _build_label(self, record):
        """Human-readable label from the layer's own NetCDF attributes.

        Prefers ``long_name``, then ``standard_name``, then the bare variable
        name, and appends ``units`` in parentheses when the file declares them.
        Files with no attributes at all are common, so nothing here may raise.
        """
        variable = record.get('variable')
        dataset = record.get('dataset')

        name = variable or record.get('type', 'value')
        units = ''

        if dataset is not None and variable:
            try:
                attrs = dataset[variable].attrs
                name = attrs.get('long_name') or attrs.get('standard_name') or variable
                units = attrs.get('units') or ''
            except Exception:
                pass

        name = str(name).strip()
        units = str(units).strip()
        return f"{name} ({units})" if units else name
