# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""Ground-distance scale bar drawn over the map.

Cartopy ships no scale-bar artist, so this builds one: measure the real
geodesic distance spanned by a candidate slice of the visible map, round it
down to a readable 1/2/5 × 10ⁿ figure, then draw a bar of the corresponding
length in axes-fraction coordinates.

Placement is bottom-*left* on purpose — the basemap attribution required by the
tile providers occupies the bottom-right (see
``GeoCanvas._update_basemap_attribution``).
"""

import logging
import math

from PyQt6.QtCore import QObject

import cartopy.crs as ccrs
from cartopy.geodesic import Geodesic

logger = logging.getLogger(__name__)


class ScaleBarManager(QObject):
    """Owns the scale-bar artists drawn over the map."""

    #: Fraction of the visible width the *candidate* bar spans before rounding.
    TARGET_FRACTION = 0.20

    #: Bar anchor in axes fraction. Clear of the attribution (bottom-right), of
    #: a 'bottom' colorbar (centred, x 0.26–0.75), and high enough that the
    #: backdrop does not cover the graticule's bottom labels, which sit just
    #: inside the map edge (roughly y 0.02–0.05 — see _install_graticule).
    ANCHOR_X = 0.028
    ANCHOR_Y = 0.088

    def __init__(self, canvas):
        super().__init__(canvas)
        self.canvas = canvas
        self._artists = []
        self._visible = False
        self._geod = Geodesic()
        #: Last rounded length, in metres — exposed for tests/diagnostics.
        self.length_m = 0.0
        self.label = ""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @property
    def visible(self):
        return self._visible

    def set_visible(self, visible):
        """Show or hide the scale bar."""
        self._visible = bool(visible)
        self.refresh()

    def refresh(self):
        """Recompute and redraw the bar for the current extent."""
        self.clear()

        if not self._visible or self.canvas.ax is None:
            self.canvas.draw_idle()
            return

        try:
            self._build()
        except Exception as exc:  # pragma: no cover - defensive, as elsewhere
            logger.warning("Could not draw scale bar: %s", exc)
            self.clear()

        self.canvas.draw_idle()

    def clear(self):
        """Remove every artist belonging to the scale bar."""
        for artist in self._artists:
            try:
                artist.remove()
            except Exception:
                pass
        self._artists = []

    # ------------------------------------------------------------------
    # Measurement
    # ------------------------------------------------------------------
    #: Passes taken to refine the drawn length onto the rounded distance. Each
    #: one re-measures and corrects; two is enough for every projection the
    #: canvas offers (see :meth:`measure`).
    REFINEMENTS = 2

    def measure(self):
        """Return ``(rounded_metres, axes_fraction, label)`` for the extent.

        The candidate span is measured along the parallel through the middle of
        the map, which is where a single-number scale is least misleading.

        Scaling the drawn bar to the rounded distance is exact only where
        ground distance along that parallel is proportional to the projected x
        span — that is, under PlateCarree. Under Mercator, a conic or a polar
        cap the scale varies across the bar, so the first guess is re-measured
        and corrected. It converges immediately because the correction is only
        ever the difference between two nearby points on the same parallel.
        """
        ax = self.canvas.ax
        x0, x1 = ax.get_xlim()
        y0, y1 = ax.get_ylim()
        span_x = x1 - x0
        if span_x <= 0:
            return 0.0, 0.0, ""

        mid_y = (y0 + y1) / 2.0
        start_x = x0 + 0.05 * span_x

        candidate_x = self.TARGET_FRACTION * span_x
        candidate_m = self._ground_distance(start_x, mid_y, candidate_x)
        if not candidate_m > 0:
            return 0.0, 0.0, ""

        nice_m = self._nice_length(candidate_m)

        # First guess: assume the scale is constant across the bar.
        bar_x = candidate_x * (nice_m / candidate_m)
        for _ in range(self.REFINEMENTS):
            measured = self._ground_distance(start_x, mid_y, bar_x)
            if not measured > 0:
                return 0.0, 0.0, ""
            bar_x *= nice_m / measured

        fraction = bar_x / span_x
        if not 0 < fraction < 1:
            return 0.0, 0.0, ""
        return nice_m, fraction, self._format_length(nice_m)

    def _ground_distance(self, start_x, y, width_x):
        """Metres on the ground spanned by ``width_x`` of axes width at ``y``.

        Projected coordinates → geographic, so the axes need not be PlateCarree.
        Zero where either end falls off the globe, which a wide projection's
        corners readily do.
        """
        ax = self.canvas.ax
        lon1, lat1 = ccrs.PlateCarree().transform_point(start_x, y, ax.projection)
        lon2, lat2 = ccrs.PlateCarree().transform_point(start_x + width_x, y, ax.projection)
        if any(v is None or math.isnan(v) for v in (lon1, lat1, lon2, lat2)):
            return 0.0

        # Returns an (n, 3) array of (distance, azimuth, back-azimuth).
        # Note: pyproj emits a NumPy "ndim > 0 to scalar" DeprecationWarning
        # from inside Geod.inv for every input shape with the installed
        # cartopy/pyproj pair — it is not avoidable from here.
        result = self._geod.inverse((lon1, lat1), (lon2, lat2))
        return float(result[0][0])

    @staticmethod
    def _nice_length(meters):
        """Largest 1/2/5 × 10ⁿ value not exceeding ``meters``."""
        exponent = math.floor(math.log10(meters))
        base = 10.0 ** exponent
        for multiple in (5, 2, 1):
            if base * multiple <= meters:
                return base * multiple
        return base

    @staticmethod
    def _format_length(meters):
        """Label the length, switching to metres below 1 km."""
        if meters >= 1000:
            km = meters / 1000.0
            return f"{km:g} km"
        return f"{meters:g} m"

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------
    def _theme_colors(self):
        """Foreground / backdrop colours for the current canvas theme."""
        if getattr(self.canvas, 'theme', 'light') == 'dark':
            return '#f0f0f0', '#1a1a1a'
        return '#1a1a1a', '#ffffff'

    def _build(self):
        """Draw bar, end caps, label and the backdrop box behind them."""
        self.length_m, fraction, self.label = self.measure()
        if not fraction > 0:
            return

        ax = self.canvas.ax
        fg, bg = self._theme_colors()
        x_start = self.ANCHOR_X
        x_end = x_start + fraction
        y = self.ANCHOR_Y
        cap = 0.014  # end-cap half-height, axes fraction

        # Translucent box so the bar reads over both dark satellite imagery and
        # pale data. Sized to enclose caps and label.
        box = ax.fill_between(
            [x_start - 0.014, x_end + 0.014], [y - cap - 0.008], [y + cap + 0.040],
            transform=ax.transAxes, facecolor=bg, edgecolor='none', alpha=0.68,
            zorder=7,
        )
        self._artists.append(box)

        bar, = ax.plot(
            [x_start, x_end], [y, y], transform=ax.transAxes,
            color=fg, linewidth=2.2, solid_capstyle='butt', zorder=7.1,
        )
        self._artists.append(bar)

        for x in (x_start, x_end):
            cap_line, = ax.plot(
                [x, x], [y - cap, y + cap], transform=ax.transAxes,
                color=fg, linewidth=1.4, solid_capstyle='butt', zorder=7.1,
            )
            self._artists.append(cap_line)

        text = ax.text(
            (x_start + x_end) / 2.0, y + cap + 0.006, self.label,
            transform=ax.transAxes, ha='center', va='bottom',
            fontsize=7.5, color=fg, zorder=7.2,
        )
        self._artists.append(text)

        # The bar is chrome, not geography: it is placed in axes fractions and
        # belongs in the corner of the widget wherever the map itself happens to
        # reach. Left clipped to the axes patch, a projection with a boundary of
        # its own — a polar cap's circle — cuts the bar away and leaves only its
        # label floating in the margin.
        for artist in self._artists:
            try:
                artist.set_clip_on(False)
            except Exception:
                pass
