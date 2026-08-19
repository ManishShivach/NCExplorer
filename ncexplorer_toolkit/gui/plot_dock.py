# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""Plot dock: time series and vertical profiles for a clicked map point.

Every series is pulled from the **xarray dataset**, never from the rendered
image. The artist holds one 2-D slice at the display resolution with the colour
scaling already applied; the dataset still has the full time and level axes and
the real units, which is the whole point of plotting a point rather than reading
one off the colorbar.

Point selection is ``sel(..., method='nearest')`` on the coordinate variables,
which means the two coordinate conventions in circulation have to be reconciled
first: a click always arrives as −180…180 from the PlateCarree axes, while files
are just as often stored 0…360. :func:`align_longitude` moves the click into
whichever convention the file uses instead of quietly returning the wrong
column at the antimeridian.
"""

from __future__ import annotations

import csv
import logging
import os

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDockWidget, QFileDialog, QHBoxLayout, QLabel,
    QPushButton, QVBoxLayout, QWidget,
)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from ..geocanvas.properties import find_case_insensitive_key
from ..utils.timeaxis import read_time_axis

logger = logging.getLogger(__name__)

#: Dimension names that mean "vertical", in the order we would rather find them.
LEVEL_DIMS = ("lev", "level", "plev", "depth", "height", "z", "altitude", "pressure")

#: Vertical coordinates that read top-down and therefore want an inverted axis.
DOWNWARD_LEVELS = ("plev", "depth", "pressure", "lev")

#: Units that mean pressure, which also reads top-down.
PRESSURE_UNITS = ("pa", "hpa", "mbar", "millibar", "millibars", "bar", "atm")

#: Above this many points a line plot is a solid block of ink and the redraw is
#: slow, so the display series is thinned. Exports still use the full data.
MAX_PLOT_POINTS = 50_000

MODE_TIME = "Time series"
MODE_PROFILE = "Vertical profile"

#: Colours for pinned points; cycled, so pinning more than six wraps.
PIN_COLORS = ("#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#8c564b")


class SeriesError(Exception):
    """Extraction failed in a way the user should simply be told about."""


class Series:
    """One extracted curve, with everything needed to draw and export it."""

    def __init__(self, layer, variable, lat, lon, mode, x, y, x_labels,
                 x_name, y_name, used_lat=None, used_lon=None,
                 x_dim="", x_units=""):
        self.layer = layer
        self.variable = variable
        self.lat = lat
        self.lon = lon
        self.mode = mode
        self.x = x                 # numeric x (time index, or level value)
        self.y = y                 # the data
        self.x_labels = x_labels   # decoded dates, or None for a profile
        self.x_name = x_name       # display label, e.g. "pressure (hPa)"
        self.y_name = y_name
        # The raw dimension name and units are kept alongside the display label
        # because the "does this axis read downward?" test matches on those, and
        # the prettified label never matches anything.
        self.x_dim = x_dim
        self.x_units = x_units
        # Assigned once when the series is pinned, so its curve and its map
        # marker keep the same colour no matter how the list is later filtered.
        self.colour = None
        # Where the nearest-neighbour lookup actually landed, which is not the
        # click on a coarse grid and is worth showing.
        self.used_lat = used_lat
        self.used_lon = used_lon

    @property
    def label(self) -> str:
        return f"{self.variable} @ {self.lat:.2f}°, {self.lon:.2f}°"

    @property
    def is_empty(self) -> bool:
        return self.y is None or self.y.size == 0

    @property
    def all_nan(self) -> bool:
        return bool(self.y is not None and self.y.size and np.all(np.isnan(self.y)))


# ----------------------------------------------------------------------
# Extraction helpers
# ----------------------------------------------------------------------
def align_longitude(lon: float, coordinate) -> float:
    """Move a −180…180 click into the convention ``coordinate`` uses.

    Returns ``lon`` unchanged when the file already matches, so a dataset
    spanning −180…180 is never shifted.
    """
    try:
        values = np.asarray(coordinate)
        low = float(np.nanmin(values))
        high = float(np.nanmax(values))
    except Exception:
        return lon

    if high > 180.0 and lon < 0.0:
        return lon + 360.0
    if low < 0.0 and lon > 180.0:
        return lon - 360.0
    return lon


def find_level_dim(dims) -> str | None:
    """The vertical dimension among ``dims``, or None."""
    return find_case_insensitive_key(list(dims), *LEVEL_DIMS)


def axis_is_downward(name: str, units: str) -> bool:
    """True when a vertical axis should be drawn with its maximum at the bottom."""
    if (units or "").strip().lower() in PRESSURE_UNITS:
        return True
    return (name or "").strip().lower() in DOWNWARD_LEVELS


def describe(variable, fallback: str) -> str:
    """An axis label from a variable's ``long_name`` and ``units``."""
    attrs = getattr(variable, "attrs", None) or {}
    name = str(attrs.get("long_name") or attrs.get("standard_name") or fallback)
    units = str(attrs.get("units") or "").strip()
    return f"{name} ({units})" if units else name


def decimate(x, y, limit: int = MAX_PLOT_POINTS):
    """Thin a series for display only, preserving its shape and endpoints."""
    if x is None or len(x) <= limit:
        return x, y
    step = int(np.ceil(len(x) / limit))
    return x[::step], y[::step]


def extract_series(dataset, variable, lat, lon, mode, time_dim=None,
                   level_dim=None, time_index=0, layer_name="") -> Series:
    """Pull one time series or vertical profile out of ``dataset``.

    Raises :class:`SeriesError` with a sentence fit for the status bar whenever
    the point cannot be sampled.
    """
    if variable not in dataset.variables:
        raise SeriesError(f"'{variable}' is not in this dataset")

    array = dataset[variable]
    dims = list(array.dims)

    lat_name = find_case_insensitive_key(dims, "lat", "latitude", "y", "nlat")
    lon_name = find_case_insensitive_key(dims, "lon", "longitude", "x", "nlon")
    if not lat_name or not lon_name:
        raise SeriesError(f"'{variable}' has no recognisable lat/lon dimensions")

    selection = {}
    used_lat = used_lon = None

    if lat_name in dataset.coords or lat_name in dataset.variables:
        selection[lat_name] = lat
    if lon_name in dataset.coords or lon_name in dataset.variables:
        selection[lon_name] = align_longitude(lon, dataset[lon_name].values)

    if not selection:
        raise SeriesError(
            f"'{variable}' has no coordinate values for {lat_name}/{lon_name}"
        )

    try:
        point = array.sel(selection, method="nearest")
    except Exception as exc:
        raise SeriesError(f"Could not sample that point: {exc}") from exc

    for name in (lat_name, lon_name):
        try:
            value = float(np.asarray(point[name].values).reshape(-1)[0])
        except Exception:
            continue
        if name == lat_name:
            used_lat = value
        else:
            used_lon = value

    level_name = level_dim or find_level_dim(point.dims)

    if mode == MODE_PROFILE:
        if not level_name or level_name not in point.dims:
            raise SeriesError(f"'{variable}' has no vertical dimension")
        if time_dim and time_dim in point.dims:
            index = max(0, min(int(dataset.sizes.get(time_dim, 1)) - 1, int(time_index)))
            point = point.isel({time_dim: index})
        x_source, x_name = level_name, level_name
    else:
        if not time_dim or time_dim not in point.dims:
            raise SeriesError(f"'{variable}' has no time dimension to plot")
        if level_name and level_name in point.dims:
            # A time series needs one level; the first is the convention the
            # rest of the app already uses when it flattens extra dimensions.
            point = point.isel({level_name: 0})
        x_source, x_name = time_dim, time_dim

    # Anything still left over (ensembles, bounds) collapses to its first entry
    # so the result is the 1-D curve the caller asked for.
    for dim in list(point.dims):
        if dim != x_source:
            point = point.isel({dim: 0})

    values = np.atleast_1d(np.asarray(point.values, dtype=float))

    if x_source in dataset.variables or x_source in point.coords:
        try:
            axis_values = np.atleast_1d(np.asarray(point[x_source].values, dtype=float))
        except Exception:
            axis_values = np.arange(values.size, dtype=float)
    else:
        axis_values = np.arange(values.size, dtype=float)

    if axis_values.size != values.size:
        axis_values = np.arange(values.size, dtype=float)

    x_labels = None
    if mode == MODE_TIME:
        try:
            x_labels = read_time_axis(dataset, x_source).labels
        except Exception as exc:
            logger.debug("Could not decode the time axis %s: %s", x_source, exc)
        if x_labels is not None and len(x_labels) != values.size:
            x_labels = None
        # A time series is indexed, not measured: plotting against the raw
        # numeric axis would put uneven gaps between evenly spaced steps.
        axis_values = np.arange(values.size, dtype=float)

    axis_label = x_name
    axis_units = ""
    if x_source in dataset.variables:
        axis_label = describe(dataset[x_source], x_name)
        axis_units = str((getattr(dataset[x_source], "attrs", None) or {}).get("units") or "")

    return Series(
        layer=layer_name,
        variable=variable,
        lat=lat,
        lon=lon,
        mode=mode,
        x=axis_values,
        y=values,
        x_labels=x_labels,
        x_name=axis_label,
        y_name=describe(array, variable),
        used_lat=used_lat,
        used_lon=used_lon,
        x_dim=x_source,
        x_units=axis_units,
    )


# ----------------------------------------------------------------------
# Dock
# ----------------------------------------------------------------------
class PlotDock(QDockWidget):
    """A small matplotlib panel that plots whatever point was last clicked."""

    def __init__(self, main_window):
        super().__init__("Plot", main_window)
        self.setAllowedAreas(
            Qt.DockWidgetArea.RightDockWidgetArea | Qt.DockWidgetArea.LeftDockWidgetArea
        )
        self.setMinimumWidth(360)

        self.main_window = main_window
        self.canvas = main_window.geo_canvas

        self._live: Series | None = None
        self._pinned: list[Series] = []
        self._markers: list = []
        self._colour_index = 0

        self._build_ui()
        self.canvas.map_clicked.connect(self.handle_map_click)
        self._render()

    def _build_ui(self):
        container = QWidget(self)
        root = QVBoxLayout(container)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        controls = QHBoxLayout()
        controls.setSpacing(4)

        controls.addWidget(QLabel("Mode:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems([MODE_TIME, MODE_PROFILE])
        self.mode_combo.setToolTip(
            "Time series plots the variable over its time axis;\n"
            "vertical profile plots it against level at the current timestep."
        )
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
        controls.addWidget(self.mode_combo, 1)

        self.auto_pin = QCheckBox("Pin on click")
        self.auto_pin.setToolTip("Keep every clicked point on the plot instead of replacing it")
        controls.addWidget(self.auto_pin)

        root.addLayout(controls)

        buttons = QHBoxLayout()
        buttons.setSpacing(4)
        self.pin_button = QPushButton("Pin")
        self.pin_button.setToolTip("Keep the current curve and mark its point on the map")
        self.pin_button.clicked.connect(self.pin_current)
        buttons.addWidget(self.pin_button)

        self.clear_button = QPushButton("Clear")
        self.clear_button.setToolTip("Remove every curve and its map marker")
        self.clear_button.clicked.connect(self.clear)
        buttons.addWidget(self.clear_button)

        self.csv_button = QPushButton("CSV…")
        self.csv_button.setToolTip("Export the plotted values")
        self.csv_button.clicked.connect(self.export_csv)
        buttons.addWidget(self.csv_button)

        self.png_button = QPushButton("PNG…")
        self.png_button.setToolTip("Export the plot as an image")
        self.png_button.clicked.connect(self.export_png)
        buttons.addWidget(self.png_button)
        root.addLayout(buttons)

        self.figure = Figure(figsize=(4, 3), tight_layout=True)
        self.plot_canvas = FigureCanvas(self.figure)
        self.ax = self.figure.add_subplot(111)
        root.addWidget(self.plot_canvas, 1)

        self.message = QLabel("Click the map to plot a point.")
        self.message.setWordWrap(True)
        self.message.setStyleSheet("color: palette(mid);")
        root.addWidget(self.message)

        self.setWidget(container)

    # ------------------------------------------------------------------
    # Click handling
    # ------------------------------------------------------------------
    def handle_map_click(self, lat, lon):
        """Extract and plot the clicked point, if this dock is on screen."""
        # Deliberately inert while hidden: clicking the map is also how the user
        # reads a value off the status bar, and that must not pay for an
        # extraction nobody can see.
        if not self.isVisible():
            return

        try:
            self._plot_point(lat, lon)
        except SeriesError as exc:
            self._set_message(str(exc))
        except Exception as exc:
            logger.error("Could not plot point %.4f, %.4f: %s", lat, lon, exc, exc_info=True)
            self._set_message(f"Could not plot that point: {exc}")

    def _plot_point(self, lat, lon):
        target = self._layer_at(lat, lon)
        if target is None:
            self._set_message("No NetCDF layer covers that point.")
            return

        layer_name, layer = target
        dataset = layer.get('dataset')
        variable = self._variable_for(layer_name, layer, dataset)
        if variable is None:
            raise SeriesError(f"'{layer_name}' has no plottable variable")

        mode = self.mode_combo.currentText()
        time_dim = self._time_dim(dataset, layer_name)
        level_dim = find_level_dim(dataset[variable].dims)

        if mode == MODE_PROFILE and not level_dim:
            raise SeriesError(f"'{variable}' has no vertical dimension to profile")
        if mode == MODE_TIME and not time_dim:
            raise SeriesError(f"'{variable}' has no time dimension to plot")

        series = extract_series(
            dataset, variable, lat, lon, mode,
            time_dim=time_dim, level_dim=level_dim,
            time_index=self._time_index(layer_name), layer_name=layer_name,
        )

        if series.is_empty:
            raise SeriesError("That point has no data")
        if series.all_nan:
            # Still shown, so the user can see the point was sampled and is
            # genuinely masked rather than wonder whether the click registered.
            self._live = series
            self._render()
            self._set_message(f"{series.label}: every value is missing (NaN)")
            return

        self._live = series
        if self.auto_pin.isChecked():
            self.pin_current()
        else:
            self._render()
            self._describe(series)

    def _layer_at(self, lat, lon):
        """The topmost NetCDF layer whose bounds contain the click."""
        try:
            layers = list(self.canvas.layers.items())
        except Exception:
            return None

        candidates = []
        for name, layer in layers:
            if layer.get('type') != 'netcdf' or layer.get('dataset') is None:
                continue
            bounds = layer.get('bounds')
            if not bounds or len(bounds) != 4:
                continue
            lon_min, lon_max, lat_min, lat_max = bounds
            # A 0-360 file has bounds to match, so the click is mapped into the
            # file's convention before the containment test, not after.
            probe = lon
            if lon_max > 180.0 and lon < 0.0:
                probe = lon + 360.0
            elif lon_min < 0.0 and lon > 180.0:
                probe = lon - 360.0
            if lon_min <= probe <= lon_max and lat_min <= lat <= lat_max:
                candidates.append((name, layer))

        if not candidates:
            return None

        # Prefer whatever the rest of the app considers active, then the one
        # drawn on top.
        active = getattr(self.main_window, 'current_layer', None)
        for name, layer in candidates:
            if name == active:
                return name, layer

        def zorder(item):
            artist = item[1].get('artist')
            try:
                return artist.get_zorder()
            except Exception:
                return -1

        return max(candidates, key=zorder)

    def _variable_for(self, layer_name, layer, dataset):
        """The variable to plot: the layer's own, then the property, then the first."""
        candidate = layer.get('variable')
        if candidate and candidate in dataset.data_vars:
            return candidate
        try:
            props = self.canvas.property_manager.get_layer_property(layer_name)
            candidate = getattr(props.netcdf, 'current_variable', None)
        except Exception:
            candidate = None
        if candidate and candidate in dataset.data_vars:
            return candidate
        names = list(dataset.data_vars)
        return names[0] if names else None

    def _time_dim(self, dataset, layer_name):
        try:
            props = self.canvas.property_manager.get_layer_property(layer_name)
            recorded = getattr(props.netcdf, 'time_dimension', None)
            if recorded and recorded in dataset.dims:
                return recorded
        except Exception:
            pass
        return find_case_insensitive_key(list(dataset.dims), "time", "t")

    def _time_index(self, layer_name):
        try:
            props = self.canvas.property_manager.get_layer_property(layer_name)
            return int(getattr(props.netcdf, 'current_time_index', 0) or 0)
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # Pinning
    # ------------------------------------------------------------------
    def show_file_series(self, path, *, label="") -> int:
        """Plot every variable of a single-gridpoint file. Returns how many.

        The other way into this dock is a map click, which cannot reach these
        results at all: a reduction to one gridpoint has no extent to click on.
        ``fldcor`` writes a 1x1 grid with one value per timestep — a scalar time
        series wearing a map's clothes — and so do ``fldmean`` and the rest of
        the field statistics. Loaded onto the canvas they draw a single
        degenerate cell; there is nothing wrong with the file, only with
        treating it as a map.

        So the series is read straight from the file and pinned, with no layer
        and no marker: there is no point on the map to mark. Every data variable
        becomes its own curve, which is what makes ``timcor``'s correlation and
        its ``pvalue`` both visible rather than only the first.
        """
        import xarray as xr

        name = label or os.path.splitext(os.path.basename(str(path)))[0]
        added = 0
        with xr.open_dataset(path, decode_times=False) as dataset:
            time_name = next((n for n in ("time", "t", "Time", "TIME")
                              if n in dataset.sizes), "")
            labels = None
            if time_name:
                try:
                    labels = read_time_axis(dataset, time_name).labels
                except Exception as exc:
                    logger.debug("Could not decode the time axis of %s: %s",
                                 path, exc)

            for variable in dataset.data_vars:
                values = np.asarray(dataset[variable].values).reshape(-1)
                if values.size == 0:
                    continue
                series = Series(
                    layer=name,
                    variable=str(variable),
                    lat=float("nan"), lon=float("nan"),
                    mode=MODE_TIME,
                    x=np.arange(values.size),
                    y=values,
                    x_labels=labels if labels and len(labels) == values.size else None,
                    x_name="Time",
                    y_name=describe(dataset[variable], str(variable)),
                    x_dim=time_name,
                    x_units=str(dataset[variable].attrs.get("units", "")),
                )
                series.colour = PIN_COLORS[self._colour_index % len(PIN_COLORS)]
                self._colour_index += 1
                self._pinned.append(series)
                added += 1

        if added:
            # The mode has to match or _all_series filters every one of them
            # straight back out again.
            if self.mode_combo.currentText() != MODE_TIME:
                self.mode_combo.setCurrentText(MODE_TIME)
            self._live = None
            self._render()
        return added

    def pin_current(self):
        """Freeze the live curve into the pinned set and mark it on the map."""
        if self._live is None:
            self._set_message("Nothing to pin — click the map first.")
            return

        pinned = self._live
        pinned.colour = PIN_COLORS[self._colour_index % len(PIN_COLORS)]
        self._colour_index += 1
        self._pinned.append(pinned)
        self._add_marker(pinned)
        self._live = None
        self._render()
        self._set_message(f"{len(self._pinned)} point(s) pinned.")

    def _add_marker(self, series):
        """Draw a small marker on the map for a pinned point."""
        colour = series.colour or PIN_COLORS[0]
        try:
            import cartopy.crs as ccrs
            handles = self.canvas.ax.plot(
                series.lon, series.lat, marker='o', markersize=6,
                markerfacecolor=colour, markeredgecolor='white', markeredgewidth=1.0,
                linestyle='none', transform=ccrs.PlateCarree(), zorder=12,
            )
            self._markers.extend(handles)
            self.canvas.draw_idle()
        except Exception as exc:
            # The curve is still worth having without its marker.
            logger.warning("Could not mark the pinned point on the map: %s", exc)

    def clear(self):
        """Drop every curve and remove the markers from the map."""
        self._live = None
        self._pinned = []
        self._colour_index = 0
        for marker in self._markers:
            try:
                marker.remove()
            except Exception:
                pass
        self._markers = []
        try:
            self.canvas.draw_idle()
        except Exception:
            pass
        self._render()
        self._set_message("Cleared. Click the map to plot a point.")

    def _on_mode_changed(self, _mode):
        # The live curve was extracted for the other mode, so it no longer
        # answers the question on screen; pinned curves keep their own mode.
        self._live = None
        self._render()
        self._set_message("Mode changed — click the map to plot.")

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------
    def _all_series(self) -> list[Series]:
        """Every series the current mode can draw.

        Filtered by mode because the two modes do not share axes: a pressure
        profile plotted against a time axis would be meaningless. Pinned series
        of the other mode are kept, not discarded, and come back when the mode
        is switched again.
        """
        mode = self.mode_combo.currentText()
        series = [s for s in self._pinned if s.mode == mode]
        if self._live is not None and self._live.mode == mode:
            series.append(self._live)
        return series

    def _render(self):
        """Redraw the panel from the current live and pinned series."""
        try:
            self.ax.clear()
            series_list = self._all_series()

            if not series_list:
                self.ax.set_xticks([])
                self.ax.set_yticks([])
                self.ax.text(0.5, 0.5, "Click the map to plot a point",
                             ha='center', va='center', fontsize=9, color='#888888',
                             transform=self.ax.transAxes)
                self.plot_canvas.draw_idle()
                return

            downward = False
            for index, series in enumerate(series_list):
                colour = series.colour or PIN_COLORS[index % len(PIN_COLORS)]
                x, y = decimate(series.x, series.y)
                if x is None or len(x) == 0:
                    continue

                if series.mode == MODE_PROFILE:
                    # Level on the y axis is what makes it read as a profile.
                    self.ax.plot(y, x, marker='o' if len(x) < 40 else None,
                                 markersize=3, linewidth=1.2, color=colour,
                                 label=series.label)
                    self.ax.set_xlabel(series.y_name, fontsize=8)
                    self.ax.set_ylabel(series.x_name, fontsize=8)
                    downward = downward or axis_is_downward(series.x_dim, series.x_units)
                else:
                    # A single timestep has no line to draw, only a point.
                    style = 'o' if len(x) == 1 else '-'
                    self.ax.plot(x, y, style, linewidth=1.2, markersize=5,
                                 color=colour, label=series.label)
                    self.ax.set_xlabel("Time", fontsize=8)
                    self.ax.set_ylabel(series.y_name, fontsize=8)

            self._apply_time_ticks(series_list)

            if downward and not self.ax.yaxis_inverted():
                self.ax.invert_yaxis()

            if len(series_list) > 1:
                self.ax.legend(fontsize=7, loc='best', framealpha=0.85)

            self.ax.tick_params(labelsize=7)
            self.ax.grid(True, alpha=0.25, linewidth=0.5)
            self.plot_canvas.draw_idle()
        except Exception as exc:
            logger.error("Could not draw the plot: %s", exc, exc_info=True)
            self._set_message(f"Could not draw the plot: {exc}")

    def _apply_time_ticks(self, series_list):
        """Label the x axis with decoded dates instead of bare indices."""
        labelled = [s for s in series_list if s.mode == MODE_TIME and s.x_labels]
        if not labelled:
            return

        # The longest axis carries the ticks; shorter series share its scale.
        series = max(labelled, key=lambda s: len(s.x_labels))
        count = len(series.x_labels)
        if count == 0:
            return

        step = max(1, count // 6)
        positions = list(range(0, count, step))
        try:
            self.ax.set_xticks(positions)
            self.ax.set_xticklabels([series.x_labels[i] for i in positions],
                                    rotation=30, ha='right', fontsize=7)
        except Exception as exc:
            logger.debug("Could not apply decoded time ticks: %s", exc)

    def _describe(self, series):
        """Report where the sample actually came from."""
        parts = [series.label, f"{series.y.size} points"]
        if series.used_lat is not None and series.used_lon is not None:
            parts.append(f"nearest cell {series.used_lat:.3f}°, {series.used_lon:.3f}°")
        self._set_message(" — ".join(parts))

    def _set_message(self, text):
        self.message.setText(text)
        logger.debug("Plot dock: %s", text)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def export_csv(self):
        """Write every plotted series to one long-format CSV."""
        series_list = self._all_series()
        if not series_list:
            self._set_message("Nothing to export — click the map first.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self.main_window, "Export plotted data",
            os.path.join(os.path.expanduser("~"), "ncexplorer_plot.csv"),
            "CSV files (*.csv);;All files (*)",
        )
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path += ".csv"

        try:
            # Long format: one row per point, so several series of different
            # lengths share a file without padding anything with blanks.
            with open(path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow([
                    "layer", "variable", "mode", "click_lat", "click_lon",
                    "sample_lat", "sample_lon", "x", "x_label", "value",
                ])
                for series in series_list:
                    labels = series.x_labels or []
                    for index, (x_value, y_value) in enumerate(zip(series.x, series.y)):
                        writer.writerow([
                            series.layer, series.variable, series.mode,
                            f"{series.lat:.6f}", f"{series.lon:.6f}",
                            "" if series.used_lat is None else f"{series.used_lat:.6f}",
                            "" if series.used_lon is None else f"{series.used_lon:.6f}",
                            x_value,
                            labels[index] if index < len(labels) else "",
                            "" if np.isnan(y_value) else y_value,
                        ])
        except Exception as exc:
            logger.error("CSV export failed: %s", exc, exc_info=True)
            self._set_message(f"CSV export failed: {exc}")
            return

        self._set_message(f"Exported {len(series_list)} series to {os.path.basename(path)}")

    def export_png(self):
        """Save the plot itself as an image."""
        if not self._all_series():
            self._set_message("Nothing to export — click the map first.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self.main_window, "Export plot image",
            os.path.join(os.path.expanduser("~"), "ncexplorer_plot.png"),
            "PNG image (*.png);;PDF document (*.pdf);;SVG image (*.svg);;All files (*)",
        )
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path += ".png"

        try:
            self.figure.savefig(path, dpi=200, bbox_inches='tight')
        except Exception as exc:
            logger.error("Plot image export failed: %s", exc, exc_info=True)
            self._set_message(f"Image export failed: {exc}")
            return

        self._set_message(f"Saved {os.path.basename(path)}")
