# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""Statistics dock: summarise a NetCDF layer over a region of the map.

Like the plot dock, every number here is computed from the **xarray dataset**,
never from the rendered image. The artist holds one 2-D slice that cartopy may
have reprojected and resampled to fit the axes, with the colour scaling already
applied; averaging that would answer a question about the picture rather than
about the data.

The area-weighted mean is the reason this matters most. On a regular lon/lat
grid the cells near a pole cover a fraction of the ground that the equatorial
ones do, so a plain ``.mean()`` over a region silently over-weights the high
latitudes. Weighting by ``cos(latitude)`` is the standard correction, and it can
only be applied where the latitude of every cell is still known — which is in
the dataset, not in the image.

Three region sources are offered, and all three end up as the same thing: a set
of grid cells. The visible extent and a drawn rectangle become a lon/lat box;
a shapefile polygon becomes a boolean mask over the cells inside its bounding
box (see :mod:`ncexplorer_toolkit.utils.regionmask`, which also reconciles the
−180…180 / 0…360 split between a shapefile and a climate file).
"""

from __future__ import annotations

import csv
import logging
import os

import cartopy.crs as ccrs
import numpy as np
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDockWidget, QFileDialog,
    QHBoxLayout, QHeaderView, QLabel, QPushButton, QSpinBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from ..geocanvas.properties import find_case_insensitive_key
from ..utils import regionmask

logger = logging.getLogger(__name__)

SOURCE_EXTENT = "Current visible extent"
SOURCE_RECTANGLE = "Drawn rectangle"
SOURCE_POLYGON = "Shapefile polygon"

#: Panning emits extent_changed for every mouse move; without a debounce the
#: dock would recompute dozens of times per drag and none but the last matter.
EXTENT_DEBOUNCE_MS = 300

#: Above this many values the histogram is drawn from a thinned sample — the
#: bar heights are indistinguishable and the redraw stops being instant. The
#: statistics and the export always use every value.
MAX_HISTOGRAM_POINTS = 200_000

#: Placeholders, so an empty combo box still reads as a sentence.
NO_LAYER = "— no NetCDF layer —"
NO_POLYGON = "— no polygon layer —"
ALL_FEATURES = "All features (dissolved)"

DEFAULT_BINS = 30

#: Percentiles reported, in the order they are shown.
PERCENTILES = (5, 25, 50, 75, 95)


class StatsError(Exception):
    """Statistics cannot be produced, in a way the user should just be told."""


class Region:
    """Where to measure: a lon/lat box, a polygon, or both."""

    def __init__(self, kind, description, bounds=None, geometry=None):
        self.kind = kind
        self.description = description
        # (west, south, east, north) in whatever convention it was given in;
        # reconciled against the dataset at extraction time.
        self.bounds = bounds
        self.geometry = geometry


class RegionSample:
    """The cells of one region, flattened, with their area weights."""

    def __init__(self, values, weights, bounds, lat_span, lon_span):
        self.values = values        # every cell in the region, NaNs included
        self.weights = weights      # cos(latitude) for the same cells
        self.bounds = bounds        # (west, south, east, north) actually covered
        self.lat_span = lat_span    # number of rows the region spans
        self.lon_span = lon_span    # number of columns


# ----------------------------------------------------------------------
# Extraction
# ----------------------------------------------------------------------
def _coordinate_values(dataset, name):
    """The 1-D values of a coordinate, or None when the file has none."""
    if name not in dataset.variables and name not in dataset.coords:
        return None
    try:
        values = np.asarray(dataset[name].values, dtype=float)
    except (TypeError, ValueError):
        return None
    return values if values.ndim == 1 and values.size else None


def _window(lats, lons, west, south, east, north):
    """Indices of the cells inside a lon/lat box.

    ``west > east`` means the box wraps the seam of the dataset's own longitude
    convention, which is the normal shape of a Pacific-centred selection and
    must not be read as an empty range.
    """
    lat_ok = (lats >= south) & (lats <= north)
    if west <= east:
        lon_ok = (lons >= west) & (lons <= east)
    else:
        lon_ok = (lons >= west) | (lons <= east)
    return np.flatnonzero(lat_ok), np.flatnonzero(lon_ok)


def extract_region(dataset, variable, region, *, time_dim=None, time_index=0) -> RegionSample:
    """Pull one region's cells out of ``dataset``.

    Raises :class:`StatsError` with a sentence fit for the panel whenever the
    region cannot be measured — an unrecognised grid, a box off the edge of the
    data, a polygon finer than the grid.
    """
    if variable not in dataset.variables:
        raise StatsError(f"'{variable}' is not in this dataset")

    array = dataset[variable]
    lat_name, lon_name = regionmask.find_lat_lon(array.dims)
    if not lat_name or not lon_name:
        raise StatsError(f"'{variable}' has no recognisable lat/lon dimensions")

    if time_dim and time_dim in array.dims:
        size = int(dataset.sizes.get(time_dim, 1))
        array = array.isel({time_dim: max(0, min(size - 1, int(time_index)))})

    # Anything still left over (levels, ensembles) collapses to its first entry,
    # which is the convention the rest of the app already uses when it flattens
    # a variable down to something two-dimensional.
    for dim in list(array.dims):
        if dim not in (lat_name, lon_name):
            array = array.isel({dim: 0})

    lats = _coordinate_values(dataset, lat_name)
    lons = _coordinate_values(dataset, lon_name)
    if lats is None or lons is None:
        raise StatsError(f"'{variable}' has no lat/lon coordinate values to select on")

    geometry = region.geometry
    if geometry is not None:
        geometry = regionmask.to_longitude_convention(geometry, regionmask.uses_360(lons))
        west, east, south, north = regionmask.box_arguments(
            geometry, regionmask.uses_360(lons)
        )
    else:
        if not region.bounds:
            raise StatsError("No region has been chosen yet")
        west, south, east, north = region.bounds
        west, east = regionmask.align_longitude_bounds(west, east, lons)

    lat_index, lon_index = _window(lats, lons, west, south, east, north)
    if lat_index.size == 0 or lon_index.size == 0:
        raise StatsError(
            "That polygon covers no grid-cell centre — it is finer than this "
            "layer's resolution" if geometry is not None
            else "No grid cells fall inside that region"
        )

    # Transposed first so the array is always [lat, lon]; a file that stores its
    # dimensions the other way round would otherwise mask along the wrong axis.
    try:
        values = np.asarray(
            array.transpose(lat_name, lon_name)
            .isel({lat_name: lat_index, lon_name: lon_index}).values,
            dtype=float,
        )
    except Exception as exc:
        raise StatsError(f"Could not read that region: {exc}") from exc

    sub_lats = lats[lat_index]
    sub_lons = lons[lon_index]
    weights = np.clip(np.cos(np.deg2rad(sub_lats)), 0.0, None)
    weights = np.repeat(weights[:, None], sub_lons.size, axis=1)

    if geometry is not None:
        mask = regionmask.grid_mask(geometry, sub_lats, sub_lons)
        if mask.shape != values.shape or not mask.any():
            raise StatsError(
                "That polygon does not cover the centre of any grid cell — "
                "it is finer than this layer's resolution"
            )
        values = values[mask]
        weights = weights[mask]
        covered = (float(sub_lons.min()), float(sub_lats.min()),
                   float(sub_lons.max()), float(sub_lats.max()))
        return RegionSample(values.ravel(), weights.ravel(), covered,
                            int(mask.any(axis=1).sum()), int(mask.any(axis=0).sum()))

    return RegionSample(
        values.ravel(), weights.ravel(),
        (float(sub_lons.min()), float(sub_lats.min()),
         float(sub_lons.max()), float(sub_lats.max())),
        sub_lats.size, sub_lons.size,
    )


def summarise(sample: RegionSample) -> list[tuple[str, float | int | None]]:
    """Every reported statistic, in display order, as ``(label, value)`` pairs.

    A value of None means "not defined for this sample" — an all-NaN region
    still reports its cell counts rather than collapsing to a bare error, which
    is how the user learns the region is masked rather than mis-selected.
    """
    values = np.asarray(sample.values, dtype=float)
    weights = np.asarray(sample.weights, dtype=float)
    finite = np.isfinite(values)
    good = values[finite]

    rows: list[tuple[str, float | int | None]] = [
        ("Grid cells", int(values.size)),
        ("Valid cells", int(good.size)),
        ("Missing (NaN)", int(values.size - good.size)),
    ]

    if good.size == 0:
        rows += [(label, None) for label in (
            "Minimum", "Maximum", "Range", "Mean", "Area-weighted mean",
            "Median", "Std. deviation", "Sum",
        )]
        rows += [(f"{p}th percentile", None) for p in PERCENTILES]
        return rows

    good_weights = weights[finite]
    weight_total = float(np.sum(good_weights))
    weighted_mean = (
        float(np.sum(good * good_weights) / weight_total) if weight_total > 0 else None
    )

    rows += [
        ("Minimum", float(np.min(good))),
        ("Maximum", float(np.max(good))),
        ("Range", float(np.max(good) - np.min(good))),
        ("Mean", float(np.mean(good))),
        ("Area-weighted mean", weighted_mean),
        ("Median", float(np.median(good))),
        ("Std. deviation", float(np.std(good))),
        ("Sum", float(np.sum(good))),
    ]
    percentiles = np.percentile(good, PERCENTILES)
    rows += [(f"{p}th percentile", float(v)) for p, v in zip(PERCENTILES, percentiles)]
    return rows


def thin(values, limit: int = MAX_HISTOGRAM_POINTS):
    """Thin a value array for display only, preserving its distribution."""
    values = np.asarray(values)
    if values.size <= limit:
        return values
    step = int(np.ceil(values.size / limit))
    return values[::step]


def format_value(value, units: str = "") -> str:
    """Render one statistic for the table.

    Counts arrive as ints and are shown bare: the variable's units belong to a
    measurement, not to a number of cells.
    """
    if value is None:
        return "—"
    if isinstance(value, str):
        return value
    if isinstance(value, (int, np.integer)) and not isinstance(value, bool):
        return f"{int(value):,}"
    if not np.isfinite(value):
        return "—"
    magnitude = abs(value)
    if magnitude and (magnitude < 1e-3 or magnitude >= 1e6):
        text = f"{value:.4e}"
    else:
        text = f"{value:,.4f}".rstrip("0").rstrip(".")
        text = text or "0"
    return f"{text} {units}".strip()


# ----------------------------------------------------------------------
# Dock
# ----------------------------------------------------------------------
class StatsDock(QDockWidget):
    """Statistics and a histogram for one region of one NetCDF layer."""

    def __init__(self, main_window):
        super().__init__("Statistics", main_window)
        self.setAllowedAreas(
            Qt.DockWidgetArea.RightDockWidgetArea | Qt.DockWidgetArea.LeftDockWidgetArea
        )
        self.setMinimumWidth(360)

        self.main_window = main_window
        self.canvas = main_window.geo_canvas

        self._rectangle = None      # the last box drawn, as (w, s, e, n)
        self._rows: list[tuple[str, object]] = []
        self._sample: RegionSample | None = None
        self._context = ""          # layer/variable/timestep line, for exports
        # What the current sample is of, kept so redrawing the histogram for a
        # new bin count keeps its axis label instead of falling back to "value".
        self._variable = ""
        self._units = ""

        # Coalesces extent changes, and doubles as the "something moved"
        # entry point for variable and timestep changes.
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(EXTENT_DEBOUNCE_MS)
        self._debounce.timeout.connect(self.refresh)

        self._build_ui()

        self.canvas.extent_changed.connect(self._on_extent_changed)
        self.canvas.region_selected.connect(self._on_region_selected)
        self.canvas.layer_added.connect(lambda *_: self.refresh_layers())
        self.canvas.layer_removed.connect(lambda *_: self.refresh_layers())
        self.canvas.variable_changed.connect(lambda *_: self._debounce.start())
        self.canvas.time_index_changed.connect(lambda *_: self._debounce.start())
        self.visibilityChanged.connect(self._on_visibility_changed)

        self.refresh_layers()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        container = QWidget(self)
        root = QVBoxLayout(container)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        layer_row = QHBoxLayout()
        layer_row.setSpacing(4)
        layer_row.addWidget(QLabel("Layer:"))
        self.layer_combo = QComboBox()
        self.layer_combo.setToolTip(
            "The NetCDF layer to summarise, at its current variable and timestep"
        )
        self.layer_combo.currentTextChanged.connect(lambda _name: self.refresh())
        layer_row.addWidget(self.layer_combo, 1)
        root.addLayout(layer_row)

        source_row = QHBoxLayout()
        source_row.setSpacing(4)
        source_row.addWidget(QLabel("Region:"))
        self.source_combo = QComboBox()
        self.source_combo.addItems([SOURCE_EXTENT, SOURCE_RECTANGLE, SOURCE_POLYGON])
        self.source_combo.setToolTip(
            "Where to measure: whatever is on screen, a box you draw, "
            "or a polygon from a loaded shapefile"
        )
        self.source_combo.currentTextChanged.connect(self._on_source_changed)
        source_row.addWidget(self.source_combo, 1)

        self.draw_button = QPushButton("Draw region")
        self.draw_button.setCheckable(True)
        self.draw_button.setToolTip("Drag a box on the map; panning resumes afterwards")
        self.draw_button.toggled.connect(self._on_draw_toggled)
        # Both source-specific controls start hidden, matching the initial
        # "visible extent" source; _on_source_changed owns them from then on.
        self.draw_button.setVisible(False)
        source_row.addWidget(self.draw_button)
        root.addLayout(source_row)

        # Polygon pickers live in their own container so the whole row can be
        # hidden for the other two sources rather than sitting there disabled.
        self.polygon_row = QWidget()
        polygon_layout = QHBoxLayout(self.polygon_row)
        polygon_layout.setContentsMargins(0, 0, 0, 0)
        polygon_layout.setSpacing(4)
        polygon_layout.addWidget(QLabel("Polygon:"))
        self.polygon_combo = QComboBox()
        self.polygon_combo.currentTextChanged.connect(self._on_polygon_layer_changed)
        polygon_layout.addWidget(self.polygon_combo, 1)
        self.feature_combo = QComboBox()
        self.feature_combo.setToolTip("Which feature of the layer to measure inside")
        self.feature_combo.currentTextChanged.connect(lambda _name: self.refresh())
        polygon_layout.addWidget(self.feature_combo, 1)
        self.polygon_row.setVisible(False)
        root.addWidget(self.polygon_row)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Statistic", "Value"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.table, 3)

        hist_row = QHBoxLayout()
        hist_row.setSpacing(4)
        hist_row.addWidget(QLabel("Bins:"))
        self.bins_spin = QSpinBox()
        self.bins_spin.setRange(5, 200)
        self.bins_spin.setValue(DEFAULT_BINS)
        self.bins_spin.setToolTip("Histogram bin count")
        self.bins_spin.valueChanged.connect(lambda _value: self._draw_histogram())
        hist_row.addWidget(self.bins_spin)
        hist_row.addStretch(1)

        self.copy_button = QPushButton("Copy")
        self.copy_button.setToolTip("Copy the table to the clipboard, tab-separated")
        self.copy_button.clicked.connect(self.copy_to_clipboard)
        hist_row.addWidget(self.copy_button)

        self.csv_button = QPushButton("Export CSV")
        self.csv_button.setToolTip("Write the statistics to a CSV file")
        self.csv_button.clicked.connect(self.export_csv)
        hist_row.addWidget(self.csv_button)
        root.addLayout(hist_row)

        self.figure = Figure(figsize=(4, 2), tight_layout=True)
        self.hist_canvas = FigureCanvas(self.figure)
        self.hist_canvas.setMinimumHeight(140)
        self.ax = self.figure.add_subplot(111)
        root.addWidget(self.hist_canvas, 2)

        self.message = QLabel("Open a NetCDF layer to see its statistics.")
        self.message.setWordWrap(True)
        self.message.setStyleSheet("color: palette(mid);")
        root.addWidget(self.message)

        self.setWidget(container)

    # ------------------------------------------------------------------
    # Layer discovery
    # ------------------------------------------------------------------
    def netcdf_layers(self) -> list[str]:
        """Loaded NetCDF layers that still have their dataset open."""
        try:
            layers = dict(self.canvas.layers)
        except Exception:
            return []
        return [name for name, layer in layers.items()
                if layer.get('type') == 'netcdf' and layer.get('dataset') is not None]

    def polygon_layers(self) -> list[str]:
        """Loaded layers that carry polygon geometry."""
        try:
            layers = dict(self.canvas.layers)
        except Exception:
            return []
        found = []
        for name, layer in layers.items():
            if layer.get('type') not in ('polygons', 'shapefile'):
                continue
            if self._features_of(name):
                found.append(name)
        return found

    def refresh_layers(self):
        """Rebuild both layer lists, keeping the selections that survive."""
        self._reload_combo(self.layer_combo, self.netcdf_layers(), NO_LAYER)
        self._reload_combo(self.polygon_combo, self.polygon_layers(), NO_POLYGON)
        # The feature list hangs off the polygon layer, and reloading the combo
        # above was signal-blocked, so it has to be rebuilt explicitly.
        if self.source_combo.currentText() == SOURCE_POLYGON:
            self._on_polygon_layer_changed(self.polygon_combo.currentText())
        else:
            self.refresh()

    @staticmethod
    def _reload_combo(combo, names, placeholder):
        previous = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(names or [placeholder])
        if previous in names:
            combo.setCurrentText(previous)
        combo.blockSignals(False)

    def _on_polygon_layer_changed(self, name):
        """Refill the feature list for the newly chosen polygon layer."""
        features = self._features_of(name)
        labels = [ALL_FEATURES] + [f.name for f in features] if features else []
        previous = self.feature_combo.currentText()
        self.feature_combo.blockSignals(True)
        self.feature_combo.clear()
        self.feature_combo.addItems(labels or ["—"])
        if previous in labels:
            self.feature_combo.setCurrentText(previous)
        self.feature_combo.blockSignals(False)
        # Per-feature selection is pointless for a single-feature file.
        self.feature_combo.setVisible(len(labels) > 2)
        self.refresh()

    def _features_of(self, layer_name) -> list[regionmask.Feature]:
        if not layer_name:
            return []
        try:
            return regionmask.layer_features(self.canvas, layer_name)
        except Exception as exc:
            logger.warning("Could not read polygons from '%s': %s", layer_name, exc)
            return []

    # ------------------------------------------------------------------
    # Region sources
    # ------------------------------------------------------------------
    def _on_source_changed(self, source):
        self.polygon_row.setVisible(source == SOURCE_POLYGON)
        self.draw_button.setVisible(source == SOURCE_RECTANGLE)
        if source != SOURCE_RECTANGLE and self.draw_button.isChecked():
            # Leaving the rectangle source must not leave the map in drawing
            # mode with no button on screen to turn it off.
            self.draw_button.setChecked(False)
        if source == SOURCE_POLYGON:
            self._on_polygon_layer_changed(self.polygon_combo.currentText())
        else:
            self.refresh()

    def _on_draw_toggled(self, checked):
        if checked:
            if not self.canvas.begin_region_selection():
                self.draw_button.setChecked(False)
        elif self.canvas.region_selection_active:
            self.canvas.end_region_selection()

    def _on_region_selected(self, west, south, east, north):
        """Adopt a box drawn on the map."""
        # The canvas announces every region it is asked for, including ones
        # armed by something else; only take it while this dock asked.
        if not self.draw_button.isChecked():
            return
        self.draw_button.setChecked(False)
        self._rectangle = (west, south, east, north)
        self.source_combo.setCurrentText(SOURCE_RECTANGLE)
        self.refresh()

    def _on_extent_changed(self, _extent):
        if self.isVisible() and self.source_combo.currentText() == SOURCE_EXTENT:
            self._debounce.start()

    def _on_visibility_changed(self, visible):
        if visible:
            self.refresh_layers()
        elif self.draw_button.isChecked():
            self.draw_button.setChecked(False)

    def current_region(self) -> Region:
        """The region the current source describes. Raises StatsError if it has none."""
        source = self.source_combo.currentText()

        if source == SOURCE_EXTENT:
            try:
                # Explicitly in lon/lat: the axes' own coordinates are metres in
                # every projection but PlateCarree, and a box of metres read as
                # a box of degrees would not report an error — it would report
                # statistics for somewhere else.
                west, east, south, north = self.canvas.ax.get_extent(
                    crs=ccrs.PlateCarree()
                )
            except Exception as exc:
                raise StatsError(f"Could not read the map extent: {exc}") from exc
            return Region(source, "visible extent", bounds=(west, south, east, north))

        if source == SOURCE_RECTANGLE:
            if not self._rectangle:
                raise StatsError("Press “Draw region”, then drag a box on the map.")
            return Region(source, "drawn rectangle", bounds=self._rectangle)

        layer_name = self.polygon_combo.currentText()
        features = self._features_of(layer_name)
        if not features:
            raise StatsError("Load a polygon shapefile to measure inside it.")

        choice = self.feature_combo.currentText()
        if choice == ALL_FEATURES or not choice or choice == "—":
            geometry = regionmask.dissolve(features)
            description = f"{layer_name} (all {len(features)} features)"
        else:
            match = next((f for f in features if f.name == choice), None)
            if match is None:
                raise StatsError(f"'{choice}' is no longer in that layer")
            geometry = match.geometry
            description = f"{layer_name}: {choice}"

        if geometry is None or geometry.is_empty:
            raise StatsError("That polygon selection is empty")
        return Region(source, description, geometry=geometry)

    # ------------------------------------------------------------------
    # Computation
    # ------------------------------------------------------------------
    def refresh(self):
        """Recompute and redraw, or explain why that is not possible."""
        # Deliberately inert while hidden, for the same reason as the plot dock:
        # panning the map must not pay for statistics nobody can see.
        if not self.isVisible():
            return

        try:
            self._compute()
        except StatsError as exc:
            self._clear(str(exc))
        except Exception as exc:
            logger.error("Could not compute region statistics: %s", exc, exc_info=True)
            self._clear(f"Could not compute statistics: {exc}")

    def _compute(self):
        layer_name = self.layer_combo.currentText()
        record = self.canvas.layers.get(layer_name) if layer_name else None
        if not record or record.get('dataset') is None:
            raise StatsError("Open a NetCDF layer to see its statistics.")

        dataset = record['dataset']
        variable = self._variable_for(layer_name, record, dataset)
        if variable is None:
            raise StatsError(f"'{layer_name}' has no variable to summarise")

        region = self.current_region()
        time_dim = self._time_dim(dataset, layer_name)
        time_index = self._time_index(layer_name)

        sample = extract_region(
            dataset, variable, region, time_dim=time_dim, time_index=time_index,
        )

        units = self._variable_units(dataset, layer_name, variable)
        rows = summarise(sample)

        west, south, east, north = sample.bounds
        header = [
            ("Layer", layer_name),
            ("Variable", variable),
        ]
        if time_dim:
            header.append(("Timestep", self._time_label(layer_name, time_index)))
        header += [
            ("Region", region.description),
            ("Bounds", f"{west:.3f}…{east:.3f}°E, {south:.3f}…{north:.3f}°N"),
        ]

        self._rows = header + rows
        self._sample = sample
        self._context = f"{layer_name} · {variable} · {region.description}"
        self._variable = variable
        self._units = units
        self._populate(units)
        self._draw_histogram()

        valid = int(np.isfinite(sample.values).sum())
        if valid == 0:
            self._set_message(
                f"{sample.values.size:,} cells in the region, every one missing (NaN)."
            )
        else:
            self._set_message(
                f"{valid:,} of {sample.values.size:,} cells carry data "
                f"({sample.lat_span} × {sample.lon_span} grid window)."
            )

    def _variable_for(self, layer_name, record, dataset):
        """The variable to summarise: the layer's own, then the property, then the first."""
        candidate = record.get('variable')
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

    def _time_label(self, layer_name, index):
        try:
            props = self.canvas.property_manager.get_layer_property(layer_name)
            labels = getattr(props.netcdf, 'time_labels', None) or []
            if 0 <= index < len(labels):
                return f"{labels[index]} (#{index})"
        except Exception:
            pass
        return f"#{index}"

    def _variable_units(self, dataset, layer_name, variable) -> str:
        """Units of the variable, from the layer properties or the file itself."""
        try:
            props = self.canvas.property_manager.get_layer_property(layer_name)
            recorded = (getattr(props.netcdf, 'units', None) or {}).get(variable)
            if recorded:
                return str(recorded).strip()
        except Exception:
            pass
        try:
            return str((dataset[variable].attrs or {}).get('units') or '').strip()
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------
    def _populate(self, units):
        """Fill the table from the rows computed by :meth:`_compute`."""
        self.table.setRowCount(len(self._rows))
        for row, (label, value) in enumerate(self._rows):
            self.table.setItem(row, 0, QTableWidgetItem(str(label)))
            self.table.setItem(row, 1, QTableWidgetItem(format_value(value, units)))

    def _draw_histogram(self):
        """Redraw the distribution of the current sample."""
        units, variable = self._units, self._variable
        try:
            self.ax.clear()
            values = None if self._sample is None else self._sample.values
            good = np.asarray([]) if values is None else values[np.isfinite(values)]

            if good.size == 0:
                self.ax.set_xticks([])
                self.ax.set_yticks([])
                self.ax.text(0.5, 0.5, "No data in this region", ha='center',
                             va='center', fontsize=9, color='#888888',
                             transform=self.ax.transAxes)
            else:
                shown = thin(good)
                self.ax.hist(shown, bins=int(self.bins_spin.value()),
                             color='#3b82f6', edgecolor='#1e3a8a', linewidth=0.4)
                label = f"{variable} ({units})" if units else (variable or "value")
                self.ax.set_xlabel(label, fontsize=8)
                self.ax.set_ylabel("cells", fontsize=8)
                self.ax.tick_params(labelsize=7)
                self.ax.grid(True, alpha=0.25, linewidth=0.5)
                if shown.size < good.size:
                    self.ax.set_title(
                        f"sampled {shown.size:,} of {good.size:,} cells", fontsize=7,
                    )
            self.hist_canvas.draw_idle()
        except Exception as exc:
            logger.error("Could not draw the histogram: %s", exc, exc_info=True)
            self._set_message(f"Could not draw the histogram: {exc}")

    def _clear(self, message):
        """Empty the panel and say why."""
        self._rows = []
        self._sample = None
        self._context = ""
        self._variable = ""
        self._units = ""
        self.table.setRowCount(0)
        self._draw_histogram()
        self._set_message(message)

    def _set_message(self, text):
        self.message.setText(text)
        logger.debug("Statistics dock: %s", text)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def _export_rows(self) -> list[tuple[str, str]]:
        """The table as plain strings, ready for the clipboard or a file."""
        units = ""
        layer = self.layer_combo.currentText()
        record = self.canvas.layers.get(layer) if layer else None
        if record is not None and record.get('dataset') is not None:
            variable = self._variable_for(layer, record, record['dataset'])
            if variable:
                units = self._variable_units(record['dataset'], layer, variable)

        return [(str(label), format_value(value, units)) for label, value in self._rows]

    def copy_to_clipboard(self):
        """Put the table on the clipboard, one ``label<TAB>value`` per line."""
        if not self._rows:
            self._set_message("Nothing to copy yet.")
            return
        text = "\n".join(f"{label}\t{value}" for label, value in self._export_rows())
        QApplication.clipboard().setText(text)
        self._set_message("Statistics copied to the clipboard.")

    def export_csv(self):
        """Write the statistics to a CSV file."""
        if not self._rows:
            self._set_message("Nothing to export yet.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self.main_window, "Export region statistics",
            os.path.join(os.path.expanduser("~"), "ncexplorer_statistics.csv"),
            "CSV files (*.csv);;All files (*)",
        )
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path += ".csv"

        try:
            with open(path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["statistic", "value"])
                writer.writerows(self._export_rows())
        except Exception as exc:
            logger.error("Statistics CSV export failed: %s", exc, exc_info=True)
            self._set_message(f"CSV export failed: {exc}")
            return

        self._set_message(f"Exported to {os.path.basename(path)}")
