"""Comparison dock: two layers, one difference, three ways of looking at it.

Comparing two climate files is mostly a question about grids. Two files that
describe the same region can disagree about resolution, about where the first
cell centre sits, and about whether longitude runs 0…360 or −180…180 — and
subtracting them regardless is the classic way to produce a plausible-looking
field that means nothing. So the dock states the grid relationship first, and
only offers arithmetic once it can say the two sides line up:

* **Identical grids** are differenced in memory with numpy, straight from the
  datasets — no temporary files, no subprocess, instant.
* **Different grids** need CDO. B is interpolated onto A's grid with
  ``remapbil`` against A's own ``griddes`` output, which is the honest way to
  make the shapes agree; the regrid runs off the UI thread because it can take
  seconds on a large file.

The difference is registered as an ordinary layer, so it inherits the layer
list, the visibility toggle, the property editor and the colorbar for free. It
is styled with a diverging colormap centred on zero because that is what an
anomaly field needs: with a sequential map the eye reads "more" and "less", not
"one side is warmer and the other is cooler".

The swipe is the third view: it clips A to the left of a draggable divider and B
to the right, using axes-coordinate clip rectangles so the split stays put while
the map pans underneath. Every artist it touches is tracked by the canvas and
released when swipe mode ends — a layer left invisibly cropped after the dock
closes would be a genuinely baffling bug.
"""

from __future__ import annotations

import logging
import os

import cartopy.crs as ccrs
import numpy as np
import xarray as xr
from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDockWidget, QFileDialog, QGridLayout,
    QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from ..geocanvas import colormaps
from ..geocanvas.properties import LayerProperty, NetCDFProperties, find_case_insensitive_key
from ..utils import regionmask
from ..utils.logging_setup import redact_text
from ..utils.tempfile_store import TempFileStore

logger = logging.getLogger(__name__)

NO_LAYER = "— no NetCDF layer —"

#: Diverging colormaps to style a difference with, best first. Names that this
#: matplotlib (or an absent optional package) cannot resolve are skipped.
DIVERGING_PREFERENCE = ("cmo.balance", "cmc.vik", "RdBu_r", "coolwarm", "bwr", "seismic")

#: Operators the CDO-backed paths need. Membership in ``operator_signatures`` is
#: checked before either path is offered, because a missing operator has to be
#: reported as a disabled button rather than as a failure halfway through a run.
REGRID_OPERATORS = ("griddes", "remapbil")
SAVE_OPERATOR = "sub"

#: How the two grids relate; drives which buttons are usable.
GRID_SAME = "same"
GRID_DIFFERENT = "different"
GRID_UNKNOWN = "unknown"


class CompareError(Exception):
    """A comparison cannot proceed, in a way the user should just be told."""


class JobResult:
    """What a background CDO run produced, handed back to the UI thread."""

    def __init__(self, kind, success, message="", path=None, command=""):
        self.kind = kind          # 'regrid' or 'save'
        self.success = success
        self.message = message
        self.path = path
        self.command = command


# ----------------------------------------------------------------------
# Swipe divider
# ----------------------------------------------------------------------
class SwipeOverlay(QWidget):
    """A draggable vertical handle floating over the map.

    Frameless child of the canvas, in the style of the navigation cluster: it
    must sit *on* the map without taking space from it, and it stays narrow so
    matplotlib keeps receiving pan/zoom events everywhere else. The line itself
    is drawn on the figure by the canvas; this widget only provides something to
    grab.
    """

    moved = pyqtSignal(float)

    WIDTH = 18
    GRIP_HEIGHT = 46

    def __init__(self, canvas):
        super().__init__(canvas)
        self.canvas = canvas
        self._fraction = 0.5
        self._dragging = False

        self.setCursor(Qt.CursorShape.SplitHCursor)
        self.setToolTip("Drag to move the divider between layer A and layer B")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # Repositioning follows the canvas rather than a layout, and an event
        # filter keeps that knowledge here instead of in the canvas.
        canvas.installEventFilter(self)

    # -- geometry ------------------------------------------------------
    def fraction(self) -> float:
        return self._fraction

    def set_fraction(self, value):
        self._fraction = max(0.0, min(1.0, float(value)))
        self.reposition()

    def reposition(self):
        parent = self.parentWidget()
        if parent is None:
            return
        x = int(round(self._fraction * parent.width())) - self.WIDTH // 2
        self.setGeometry(x, 0, self.WIDTH, parent.height())
        self.raise_()

    def eventFilter(self, watched, event):
        if watched is self.canvas and event.type() == QEvent.Type.Resize:
            self.reposition()
        return super().eventFilter(watched, event)

    def showEvent(self, event):
        super().showEvent(event)
        self.reposition()

    # -- interaction ---------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            event.accept()

    def mouseMoveEvent(self, event):
        if not self._dragging:
            return
        parent = self.parentWidget()
        if parent is None or parent.width() <= 0:
            return
        # The event is in the handle's own coordinates; map it back to the map.
        x = self.mapToParent(event.position().toPoint()).x()
        self.set_fraction(x / parent.width())
        self.moved.emit(self._fraction)
        event.accept()

    def mouseReleaseEvent(self, event):
        self._dragging = False
        event.accept()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # A short grip at mid-height, dark-edged so it stays visible over both a
        # pale ocean and a dark basemap.
        top = max(0, (self.height() - self.GRIP_HEIGHT) // 2)
        rect = self.rect().adjusted(3, top, -3, -(self.height() - top - self.GRIP_HEIGHT))
        painter.setPen(QPen(QColor("#0f172a"), 1))
        painter.setBrush(QColor(248, 250, 252, 235))
        painter.drawRoundedRect(rect, 5, 5)

        painter.setPen(QPen(QColor("#0f172a"), 1))
        centre_x = self.width() // 2
        centre_y = self.height() // 2
        for offset in (-4, 0, 4):
            painter.drawLine(centre_x + offset, centre_y - 8, centre_x + offset, centre_y + 8)
        painter.end()


# ----------------------------------------------------------------------
# Extraction helpers
# ----------------------------------------------------------------------
def grid_of(dataset, variable):
    """The lat/lon axes a variable lives on, without reading any data.

    Both are returned ascending, which is the order :func:`slice_2d` puts its
    rows and columns in — comparing grids and comparing arrays then agree even
    for the many files that store latitude north-to-south.
    """
    if variable not in dataset.variables:
        raise CompareError(f"'{variable}' is not in that dataset")

    array = dataset[variable]
    lat_name, lon_name = regionmask.find_lat_lon(array.dims)
    if not lat_name or not lon_name:
        raise CompareError(f"'{variable}' has no recognisable lat/lon dimensions")

    lats = _coordinate(dataset, lat_name, int(dataset.sizes.get(lat_name, 0)))
    lons = _coordinate(dataset, lon_name, int(dataset.sizes.get(lon_name, 0)))
    return np.sort(lats), np.sort(lons)


def slice_2d(dataset, variable, time_dim=None, time_index=0):
    """One 2-D ``[lat, lon]`` slice of a variable, with its coordinates.

    Rows are ordered south-to-north whatever the file does, so the array can be
    handed to ``imshow(origin='lower')`` without the picture coming out upside
    down.
    """
    if variable not in dataset.variables:
        raise CompareError(f"'{variable}' is not in that dataset")

    array = dataset[variable]
    lat_name, lon_name = regionmask.find_lat_lon(array.dims)
    if not lat_name or not lon_name:
        raise CompareError(f"'{variable}' has no recognisable lat/lon dimensions")

    if time_dim and time_dim in array.dims:
        size = int(dataset.sizes.get(time_dim, 1))
        array = array.isel({time_dim: max(0, min(size - 1, int(time_index)))})

    for dim in list(array.dims):
        if dim not in (lat_name, lon_name):
            array = array.isel({dim: 0})

    array = array.transpose(lat_name, lon_name)
    values = np.asarray(array.values, dtype=float)

    lats = _coordinate(dataset, lat_name, values.shape[0])
    lons = _coordinate(dataset, lon_name, values.shape[1])

    if lats.size > 1 and lats[0] > lats[-1]:
        values = values[::-1, :]
        lats = lats[::-1]
    if lons.size > 1 and lons[0] > lons[-1]:
        values = values[:, ::-1]
        lons = lons[::-1]

    return values, lats, lons


def _coordinate(dataset, name, length):
    """Coordinate values for a dimension, falling back to plain indices."""
    if name in dataset.variables or name in dataset.coords:
        try:
            values = np.asarray(dataset[name].values, dtype=float).ravel()
            if values.size == length:
                return values
        except (TypeError, ValueError):
            pass
    return np.arange(length, dtype=float)


def variable_units(dataset, variable) -> str:
    """The declared units of a variable, or an empty string."""
    try:
        return str((dataset[variable].attrs or {}).get('units') or '').strip()
    except Exception:
        return ""


def grids_match(lats_a, lons_a, lats_b, lons_b) -> bool:
    """True when two grids are the same to within floating-point noise."""
    if lats_a.shape != lats_b.shape or lons_a.shape != lons_b.shape:
        return False
    return bool(np.allclose(lats_a, lats_b, atol=1e-6)
                and np.allclose(lons_a, lons_b, atol=1e-6))


def diverging_colormap() -> str:
    """The best diverging colormap this installation can actually resolve."""
    for name in DIVERGING_PREFERENCE:
        resolved, ok = colormaps.resolve_colormap(name)
        if ok and colormaps.is_diverging(resolved):
            return resolved
    for name in colormaps.flat_colormaps():
        if colormaps.is_diverging(name):
            return name
    return colormaps.DEFAULT_COLORMAP


# ----------------------------------------------------------------------
# Dock
# ----------------------------------------------------------------------
class CompareDock(QDockWidget):
    """Difference two NetCDF layers, and swipe between them on the map."""

    #: Emitted by a worker thread when a CDO run finishes. Qt marshals it back
    #: to the UI thread, which is the only place widgets may be touched.
    job_finished = pyqtSignal(object)

    def __init__(self, main_window):
        super().__init__("Compare", main_window)
        self.setAllowedAreas(
            Qt.DockWidgetArea.RightDockWidgetArea | Qt.DockWidgetArea.LeftDockWidgetArea
        )
        self.setMinimumWidth(360)

        self.main_window = main_window
        self.canvas = main_window.geo_canvas

        self._temp = TempFileStore(tag="ncexplorer_compare")
        self._swipe: SwipeOverlay | None = None
        self._difference_layer: str | None = None
        # Whether the user has picked a layer for each side themselves. Until
        # they have, the side keeps following its positional default — without
        # this, B stays on the first layer loaded and the dock opens offering to
        # difference a layer with itself.
        self._chosen = {"A": False, "B": False}
        # Set while a spin box is being moved programmatically, so the "lock
        # together" checkbox cannot start an infinite ping-pong between them.
        self._syncing = False
        self._busy = False
        # Last regrid produced by this dock, reused by "Save difference…" so a
        # second interpolation of the same file is not paid for twice.
        self._regridded: tuple[str, str] | None = None   # (source file, regridded file)
        # Read by a worker, which cannot ask the widgets itself; refreshed by
        # _start_job just before the job is submitted.
        self._grid_state_snapshot = (GRID_UNKNOWN, "")

        self._build_ui()

        self.job_finished.connect(self._on_job_finished)
        self.canvas.layer_added.connect(lambda *_: self.refresh_layers())
        self.canvas.layer_removed.connect(self._on_layer_removed)
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

        grid = QGridLayout()
        grid.setSpacing(4)
        grid.addWidget(QLabel("Layer"), 0, 1)
        grid.addWidget(QLabel("Variable"), 0, 2)
        grid.addWidget(QLabel("Time"), 0, 3)

        self.layer_a, self.variable_a, self.time_a, self.label_a = self._add_side(grid, 1, "A")
        self.layer_b, self.variable_b, self.time_b, self.label_b = self._add_side(grid, 3, "B")
        grid.setColumnStretch(1, 2)
        grid.setColumnStretch(2, 2)
        root.addLayout(grid)

        options = QHBoxLayout()
        options.setSpacing(6)
        self.lock_check = QCheckBox("Lock timesteps together")
        self.lock_check.setToolTip("Step both layers to the same timestep index")
        self.lock_check.toggled.connect(self._on_lock_toggled)
        options.addWidget(self.lock_check)
        options.addStretch(1)
        root.addLayout(options)

        self.grid_label = QLabel("")
        self.grid_label.setWordWrap(True)
        root.addWidget(self.grid_label)

        buttons = QHBoxLayout()
        buttons.setSpacing(4)
        self.difference_button = QPushButton("Difference layer")
        self.difference_button.setToolTip(
            "Add A − B to the map as a new layer, regridding B onto A if needed"
        )
        self.difference_button.clicked.connect(self.build_difference)
        buttons.addWidget(self.difference_button)

        self.save_button = QPushButton("Save difference…")
        self.save_button.setToolTip(
            "Write A − B to a NetCDF file with the sub operator "
            "(every timestep, not just this one)"
        )
        self.save_button.clicked.connect(self.save_difference)
        buttons.addWidget(self.save_button)

        self.swipe_button = QPushButton("Swipe")
        self.swipe_button.setCheckable(True)
        self.swipe_button.setToolTip("Split the map: layer A on the left, layer B on the right")
        self.swipe_button.toggled.connect(self._on_swipe_toggled)
        buttons.addWidget(self.swipe_button)
        root.addLayout(buttons)

        self.message = QLabel("Load two NetCDF layers to compare them.")
        self.message.setWordWrap(True)
        self.message.setStyleSheet("color: palette(mid);")
        root.addWidget(self.message)

        root.addStretch(1)
        self.setWidget(container)

    def _add_side(self, grid, row, title):
        """One A/B row: layer, variable, timestep, and the decoded date under it."""
        grid.addWidget(QLabel(f"<b>{title}</b>"), row, 0)

        layer = QComboBox()
        layer.currentTextChanged.connect(lambda _name, side=title: self._on_layer_changed(side))
        grid.addWidget(layer, row, 1)

        variable = QComboBox()
        variable.currentTextChanged.connect(lambda _name: self._describe_grids())
        grid.addWidget(variable, row, 2)

        time = QSpinBox()
        time.setRange(0, 0)
        time.setToolTip("Timestep index")
        time.valueChanged.connect(lambda value, side=title: self._on_time_changed(side, value))
        grid.addWidget(time, row, 3)

        label = QLabel("")
        label.setStyleSheet("color: palette(mid);")
        grid.addWidget(label, row + 1, 1, 1, 3)
        return layer, variable, time, label

    # ------------------------------------------------------------------
    # Layer discovery
    # ------------------------------------------------------------------
    def netcdf_layers(self) -> list[str]:
        try:
            layers = dict(self.canvas.layers)
        except Exception:
            return []
        return [name for name, layer in layers.items()
                if layer.get('type') == 'netcdf' and layer.get('dataset') is not None]

    def refresh_layers(self):
        """Rebuild both layer combos, keeping selections that still exist."""
        names = self.netcdf_layers()
        for side, combo, fallback_index in (("A", self.layer_a, 0), ("B", self.layer_b, 1)):
            previous = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(names or [NO_LAYER])
            if self._chosen[side] and previous in names:
                combo.setCurrentText(previous)
            elif len(names) > fallback_index:
                # Two layers loaded back to back land on A and B rather than
                # both on the first one.
                combo.setCurrentIndex(fallback_index)
            elif previous in names:
                combo.setCurrentText(previous)
            combo.blockSignals(False)

        for side in ("A", "B"):
            self._reload_side(side)
        self._describe_grids()

    def _reload_side(self, side):
        """Refill one side's variable list and time range from its layer."""
        layer_combo, variable_combo, time_spin, label = self._widgets(side)
        record = self.canvas.layers.get(layer_combo.currentText())

        variables = []
        dataset = None
        if record is not None:
            dataset = record.get('dataset')
        if dataset is not None:
            variables = list(dataset.data_vars)

        previous = variable_combo.currentText()
        variable_combo.blockSignals(True)
        variable_combo.clear()
        variable_combo.addItems(variables or ["—"])
        if previous in variables:
            variable_combo.setCurrentText(previous)
        else:
            current = self._recorded_variable(layer_combo.currentText(), record)
            if current in variables:
                variable_combo.setCurrentText(current)
        variable_combo.blockSignals(False)

        count = 0
        if dataset is not None:
            dim = self._time_dim(dataset, layer_combo.currentText())
            count = int(dataset.sizes.get(dim, 0)) if dim else 0

        time_spin.blockSignals(True)
        time_spin.setRange(0, max(0, count - 1))
        time_spin.setEnabled(count > 1)
        # Adopt the timestep the layer is already showing rather than snapping
        # it back to zero just because the dock opened.
        recorded = self._recorded_index(layer_combo.currentText())
        time_spin.setValue(max(0, min(count - 1, recorded)) if count else 0)
        time_spin.blockSignals(False)
        self._update_time_label(side)

    def _widgets(self, side):
        if side == "A":
            return self.layer_a, self.variable_a, self.time_a, self.label_a
        return self.layer_b, self.variable_b, self.time_b, self.label_b

    def _recorded_variable(self, layer_name, record):
        if record is not None and record.get('variable'):
            return record['variable']
        try:
            props = self.canvas.property_manager.get_layer_property(layer_name)
            return getattr(props.netcdf, 'current_variable', None)
        except Exception:
            return None

    def _recorded_index(self, layer_name) -> int:
        try:
            props = self.canvas.property_manager.get_layer_property(layer_name)
            return int(getattr(props.netcdf, 'current_time_index', 0) or 0)
        except Exception:
            return 0

    def _time_dim(self, dataset, layer_name):
        try:
            props = self.canvas.property_manager.get_layer_property(layer_name)
            recorded = getattr(props.netcdf, 'time_dimension', None)
            if recorded and recorded in dataset.dims:
                return recorded
        except Exception:
            pass
        return find_case_insensitive_key(list(dataset.dims), "time", "t")

    def _update_time_label(self, side):
        layer_combo, _variable, time_spin, label = self._widgets(side)
        name = layer_combo.currentText()
        try:
            props = self.canvas.property_manager.get_layer_property(name)
            labels = getattr(props.netcdf, 'time_labels', None) or []
        except Exception:
            labels = []
        index = time_spin.value()
        label.setText(labels[index] if 0 <= index < len(labels) else "")

    # ------------------------------------------------------------------
    # Selection changes
    # ------------------------------------------------------------------
    def _on_layer_changed(self, side):
        # Reached only from an unblocked signal, i.e. a real choice: the combo
        # rebuild in refresh_layers blocks signals precisely so it cannot lie
        # about the user having picked something.
        self._chosen[side] = True
        self._reload_side(side)
        self._describe_grids()
        if self.swipe_button.isChecked():
            self._apply_swipe()

    def _on_time_changed(self, side, value):
        """Move one layer's timestep, and the other's when they are locked."""
        if self._syncing:
            return
        self._syncing = True
        try:
            self._render_time(side, value)
            if self.lock_check.isChecked():
                other = "B" if side == "A" else "A"
                _layer, _variable, spin, _label = self._widgets(other)
                mirrored = max(spin.minimum(), min(spin.maximum(), value))
                if mirrored != spin.value():
                    spin.setValue(mirrored)
                self._render_time(other, mirrored)
                self._update_time_label(other)
        finally:
            self._syncing = False
        self._update_time_label(side)

    def _render_time(self, side, index):
        """Move a layer to a timestep through the canvas' own time path.

        The same two calls the animation dock makes, so a step here is
        indistinguishable from one made anywhere else and every listener — the
        colorbar, the property panel, the time slider — updates as it always has.
        """
        layer_combo, _variable, _spin, _label = self._widgets(side)
        name = layer_combo.currentText()
        record = self.canvas.layers.get(name)
        if record is None or record.get('dataset') is None:
            return
        try:
            self.canvas.set_netcdf_time_index(name, int(index))
            self.canvas.update_netcdf_layer(name)
        except Exception as exc:
            logger.error("Could not move '%s' to timestep %s: %s", name, index, exc,
                         exc_info=True)
            self._set_message(f"Could not show timestep {index} of '{name}': {exc}")

    def _on_lock_toggled(self, checked):
        if checked:
            # Adopt A's timestep immediately rather than waiting for the next
            # step, so "locked" is true the moment the box is ticked.
            self._on_time_changed("A", self.time_a.value())

    def _on_layer_removed(self, layer_name):
        if layer_name == self._difference_layer:
            self._difference_layer = None
        if self.swipe_button.isChecked() and layer_name in (
            self.layer_a.currentText(), self.layer_b.currentText()
        ):
            # One side of the swipe just vanished; leave the mode rather than
            # keeping the survivor clipped to half the map.
            self.swipe_button.setChecked(False)
        self.refresh_layers()

    def _on_visibility_changed(self, visible):
        if visible:
            self.refresh_layers()
        elif self.swipe_button.isChecked():
            self.swipe_button.setChecked(False)

    # ------------------------------------------------------------------
    # Grid compatibility
    # ------------------------------------------------------------------
    def _selection(self, side):
        """``(name, record, dataset, variable, time_dim, index)`` for one side."""
        layer_combo, variable_combo, time_spin, _label = self._widgets(side)
        name = layer_combo.currentText()
        record = self.canvas.layers.get(name)
        if record is None or record.get('dataset') is None:
            raise CompareError(f"Choose a NetCDF layer for {side}")
        variable = variable_combo.currentText()
        dataset = record['dataset']
        if variable not in dataset.data_vars:
            raise CompareError(f"Choose a variable for {side}")
        return (name, record, dataset, variable,
                self._time_dim(dataset, name), int(time_spin.value()))

    def grid_state(self) -> tuple[str, str]:
        """How the two grids relate, and a sentence describing it."""
        try:
            _name_a, _record_a, dataset_a, variable_a, _dim_a, _index_a = self._selection("A")
            _name_b, _record_b, dataset_b, variable_b, _dim_b, _index_b = self._selection("B")
        except CompareError as exc:
            return GRID_UNKNOWN, str(exc)

        # Only the axes are needed to answer this, and reading them costs
        # nothing — pulling both data slices would re-read the files on every
        # combo-box change.
        try:
            lats_a, lons_a = grid_of(dataset_a, variable_a)
            lats_b, lons_b = grid_of(dataset_b, variable_b)
        except CompareError as exc:
            return GRID_UNKNOWN, str(exc)
        except Exception as exc:
            logger.error("Could not read the two grids: %s", exc, exc_info=True)
            return GRID_UNKNOWN, f"Could not read those layers: {exc}"

        if grids_match(lats_a, lons_a, lats_b, lons_b):
            return GRID_SAME, (f"Same grid: {lats_a.size} × {lons_a.size} cells — "
                               "differenced directly.")
        return GRID_DIFFERENT, (
            f"Different grids: A is {lats_a.size} × {lons_a.size}, "
            f"B is {lats_b.size} × {lons_b.size}. B will be regridded onto A "
            "with remapbil."
        )

    def _missing_operators(self, names) -> list[str]:
        """Which of ``names`` the installed CDO does not have."""
        try:
            signatures = self.main_window.NCExplorer.operator_signatures
        except Exception:
            return list(names)
        return [name for name in names if name not in signatures]

    def _describe_grids(self):
        """Update the grid line and enable only the paths that can actually run."""
        state, description = self.grid_state()
        self.grid_label.setText(description)

        can_difference = state == GRID_SAME
        if state == GRID_DIFFERENT:
            missing = self._missing_operators(REGRID_OPERATORS)
            if missing:
                self.grid_label.setText(
                    f"{description}\nThat needs {', '.join(missing)}, which this "
                    "build does not have — regridding is unavailable."
                )
            else:
                can_difference = True

        self.difference_button.setEnabled(can_difference and not self._busy)

        save_missing = self._missing_operators([SAVE_OPERATOR])
        self.save_button.setEnabled(
            state != GRID_UNKNOWN and not save_missing and not self._busy
        )
        if save_missing:
            self.save_button.setToolTip(
                f"This build has no '{SAVE_OPERATOR}' operator, so the difference "
                "cannot be written"
            )

    # ------------------------------------------------------------------
    # Difference layer
    # ------------------------------------------------------------------
    def build_difference(self):
        """Compute A − B and put it on the map."""
        try:
            self._build_difference()
        except CompareError as exc:
            self._set_message(str(exc))
        except Exception as exc:
            logger.error("Could not build the difference layer: %s", exc, exc_info=True)
            self._set_message(f"Could not build the difference: {exc}")

    def _build_difference(self):
        name_a, record_a, dataset_a, variable_a, dim_a, index_a = self._selection("A")
        name_b, record_b, dataset_b, variable_b, dim_b, index_b = self._selection("B")

        values_a, lats_a, lons_a = slice_2d(dataset_a, variable_a, dim_a, index_a)
        values_b, lats_b, lons_b = slice_2d(dataset_b, variable_b, dim_b, index_b)

        if grids_match(lats_a, lons_a, lats_b, lons_b):
            self._register_difference(name_a, name_b, values_a - values_b, lats_a, lons_a,
                                      variable_a, variable_b, "same grid",
                                      units=variable_units(dataset_a, variable_a))
            return

        missing = self._missing_operators(REGRID_OPERATORS)
        if missing:
            raise CompareError(
                f"These grids differ and this build has no {', '.join(missing)}, "
                "so B cannot be regridded onto A."
            )

        file_a = self._layer_file(record_a, name_a)
        file_b = self._layer_file(record_b, name_b)
        self._start_job(
            "regrid",
            lambda: self._run_regrid(file_a, file_b),
            f"Regridding '{name_b}' onto '{name_a}' with remapbil…",
        )

    def _layer_file(self, record, name) -> str:
        """The file behind a layer, which every CDO path needs."""
        path = record.get('data')
        if not isinstance(path, str) or not os.path.exists(path):
            raise CompareError(f"'{name}' has no file on disk to read")
        return path

    def _run_regrid(self, file_a, file_b) -> JobResult:
        """Interpolate B onto A's grid. Runs on a worker thread."""
        integration = self.main_window.NCExplorer

        if self._regridded and self._regridded[0] == file_b:
            return JobResult("regrid", True, "Reusing the previous regrid",
                             self._regridded[1])

        description = integration.execute_operator("griddes", input_files=file_a)
        command = integration.last_command
        if not description.success or not description.stdout.strip():
            return JobResult("regrid", False,
                             redact_text(description.stderr.strip()) or "griddes produced nothing",
                             command=command)

        grid_path = self._temp.new(suffix=".txt")
        with open(grid_path, "w", encoding="utf-8") as handle:
            handle.write(description.stdout)

        target = self._temp.new(suffix=".nc")
        result = integration.execute_operator(
            "remapbil", input_files=file_b, output_files=target,
            extra_parameters=[grid_path],
        )
        command = integration.last_command
        if not result.success:
            return JobResult("regrid", False,
                             redact_text(result.stderr.strip()) or "remapbil failed",
                             command=command)

        self._regridded = (file_b, target)
        return JobResult("regrid", True, "", target, command)

    def _finish_regrid(self, result: JobResult):
        """Difference A against the regridded B, back on the UI thread."""
        name_a, _record_a, dataset_a, variable_a, dim_a, index_a = self._selection("A")
        name_b, _record_b, _dataset_b, variable_b, _dim_b, index_b = self._selection("B")

        values_a, lats_a, lons_a = slice_2d(dataset_a, variable_a, dim_a, index_a)

        with xr.open_dataset(result.path, decode_times=False) as regridded:
            if variable_b not in regridded.data_vars:
                raise CompareError(
                    f"The regridded file has no '{variable_b}' — it was renamed or dropped"
                )
            dim_b = find_case_insensitive_key(list(regridded.dims), "time", "t")
            values_b, lats_b, lons_b = slice_2d(regridded, variable_b, dim_b, index_b)

        if not grids_match(lats_a, lons_a, lats_b, lons_b):
            raise CompareError("The regridded file still does not match A's grid")

        self._register_difference(name_a, name_b, values_a - values_b, lats_a, lons_a,
                                  variable_a, variable_b, "B regridded onto A",
                                  units=variable_units(dataset_a, variable_a))

    def _register_difference(self, name_a, name_b, values, lats, lons,
                             variable_a, variable_b, how, units=""):
        """Put the difference on the map as an ordinary, stylable layer."""
        if not np.isfinite(values).any():
            raise CompareError("Every cell of that difference is missing (NaN)")

        layer_name = f"{name_a} − {name_b}"
        # Recomputing replaces the previous difference rather than stacking a
        # second artist under the same name.
        if layer_name in self.canvas.layers:
            self.canvas.remove_layer(layer_name)

        extent = [float(lons.min()), float(lons.max()),
                  float(lats.min()), float(lats.max())]
        finite = values[np.isfinite(values)]
        statistics = {
            'min': float(np.min(finite)),
            'max': float(np.max(finite)),
            'mean': float(np.mean(finite)),
            'std': float(np.std(finite)),
            'valid_pixels': int(finite.size),
            'total_pixels': int(values.size),
        }

        colormap = diverging_colormap()
        limits = colormaps.symmetric_limits(statistics)

        # A real in-memory dataset rather than None: every other tool in the app
        # reads a layer's data through record['dataset'], so with one attached
        # the difference can be clicked, summarised and exported exactly like a
        # layer that came from a file.
        variable = (variable_a if variable_a == variable_b
                    else f"{variable_a}_minus_{variable_b}")
        dataset = xr.Dataset(
            {variable: (("lat", "lon"), values)},
            coords={"lat": lats, "lon": lons},
        )
        dataset[variable].attrs = {
            'long_name': f"{name_a} − {name_b} ({variable_a} − {variable_b})",
            'units': units,
        }
        dataset.lat.attrs = {'units': 'degrees_north', 'standard_name': 'latitude'}
        dataset.lon.attrs = {'units': 'degrees_east', 'standard_name': 'longitude'}

        properties = LayerProperty()
        properties.metadata.name = layer_name
        # 'netcdf' rather than 'raster' so the raster styling path, the colorbar
        # and the property editor all treat it like any other data layer.
        properties.metadata.layer_type = "netcdf"
        properties.metadata.source_file = "N/A"
        properties.metadata.statistics = statistics
        properties.dimensions.extent = extent
        properties.dimensions.width = int(values.shape[1])
        properties.dimensions.height = int(values.shape[0])
        properties.style.colormap = colormap
        # vmin/vmax are deliberately left unset: raster_clim() derives the
        # symmetric limits from the statistics above, so a later restyle keeps
        # zero at the centre instead of freezing today's numbers.
        properties.style.diverging_center_zero = True
        properties.netcdf = NetCDFProperties()
        properties.netcdf.variables = [variable]
        properties.netcdf.current_variable = variable
        properties.netcdf.units = {variable: units}
        self.canvas.property_manager.add_layer(layer_name, properties)

        artist = self.canvas.ax.imshow(
            values, extent=extent, transform=ccrs.PlateCarree(),
            cmap=colormap, origin='lower', alpha=1.0,
        )
        if limits is not None:
            artist.set_clim(*limits)

        self.canvas.add_layer(
            layer_name, type='netcdf', artist=artist, data='',
            variable=variable, bounds=extent, dataset=dataset, visible=True,
        )
        self._difference_layer = layer_name
        self.canvas.draw_idle()
        self.canvas.layer_added.emit(layer_name)

        span = "" if limits is None else f", scaled ±{abs(limits[1]):.4g}"
        self._set_message(
            f"Added '{layer_name}' ({how}): {variable_a} − {variable_b}, "
            f"{statistics['min']:.4g} to {statistics['max']:.4g}{span}."
        )
        logger.info("Difference layer '%s' added (%s)", layer_name, how)

    # ------------------------------------------------------------------
    # Saving
    # ------------------------------------------------------------------
    def save_difference(self):
        """Write A − B to a file with CDO ``sub``."""
        try:
            name_a, record_a, _dataset_a, _variable_a, _dim_a, _index_a = self._selection("A")
            name_b, record_b, _dataset_b, _variable_b, _dim_b, _index_b = self._selection("B")
            if self._missing_operators([SAVE_OPERATOR]):
                raise CompareError(f"This build has no '{SAVE_OPERATOR}' operator")

            file_a = self._layer_file(record_a, name_a)
            file_b = self._layer_file(record_b, name_b)
        except CompareError as exc:
            self._set_message(str(exc))
            return

        suggestion = os.path.join(
            os.path.dirname(file_a) or os.path.expanduser("~"),
            f"{os.path.splitext(os.path.basename(file_a))[0]}_minus_"
            f"{os.path.splitext(os.path.basename(file_b))[0]}.nc",
        )
        path, _ = QFileDialog.getSaveFileName(
            self.main_window, "Save difference", suggestion,
            "NetCDF files (*.nc);;All files (*)",
        )
        if not path:
            return
        # CDO picks its output format from the extension alone, so a bare name
        # would silently become something other than NetCDF.
        if os.path.splitext(path)[1].lower() not in ('.nc', '.nc2', '.nc4', '.nc5'):
            path += ".nc"

        self._start_job(
            "save",
            lambda: self._run_save(file_a, file_b, path),
            f"Writing {os.path.basename(path)} with the sub operator…",
        )

    def _run_save(self, file_a, file_b, path) -> JobResult:
        """Regrid if needed, then subtract. Runs on a worker thread."""
        integration = self.main_window.NCExplorer
        source_b = file_b

        state, _description = self._grid_state_snapshot
        if state == GRID_DIFFERENT:
            regrid = self._run_regrid(file_a, file_b)
            if not regrid.success:
                return JobResult("save", False, regrid.message, command=regrid.command)
            source_b = regrid.path

        result = integration.execute_operator(
            "sub", input_files=[file_a, source_b], output_files=path,
        )
        command = integration.last_command
        if not result.success:
            return JobResult("save", False,
                             redact_text(result.stderr.strip()) or "sub failed", command=command)
        return JobResult("save", True, "", result.output_file or path, command)

    # ------------------------------------------------------------------
    # Background jobs
    # ------------------------------------------------------------------
    def _start_job(self, kind, work, message):
        """Run ``work`` on the canvas' thread pool without freezing the window."""
        if self._busy:
            self._set_message("A run is already in progress…")
            return

        # The worker reads it and cannot ask the widgets itself.
        self._grid_state_snapshot = self.grid_state()

        self._busy = True
        self.difference_button.setEnabled(False)
        self.save_button.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self._set_message(message)
        self.canvas.status_update.emit(message)

        def run():
            try:
                result = work()
            except Exception as exc:
                logger.error("Background job failed: %s", exc, exc_info=True)
                result = JobResult(kind, False, str(exc))
            self.job_finished.emit(result)

        try:
            self.canvas.thread_pool.submit(run)
        except Exception as exc:
            # A pool that will not accept work is better than a dead button:
            # fall back to running inline so the user still gets their result.
            logger.warning("Thread pool rejected the job, running inline: %s", exc)
            run()

    def _on_job_finished(self, result: JobResult):
        """Back on the UI thread: report, and use whatever the run produced."""
        self._busy = False
        QApplication.restoreOverrideCursor()

        if result.command:
            logger.info("Command: %s", result.command)
            console = getattr(self.main_window, 'output_console', None)
            if console is not None:
                console.append(f"$ {result.command}")

        if not result.success:
            self._set_message(result.message or "The run failed")
            self.canvas.status_update.emit("Run failed")
            self._describe_grids()
            return

        try:
            if result.kind == "regrid":
                self._finish_regrid(result)
            else:
                self.main_window.current_output_file = result.path
                for button in ('save_btn', 'visualize_btn'):
                    widget = getattr(self.main_window, button, None)
                    if widget is not None:
                        widget.setEnabled(True)
                self._set_message(f"Wrote {os.path.basename(result.path)}. "
                                  "“Save Output” and “Visualize Output” now use it.")
                self.canvas.status_update.emit(f"Difference written to {result.path}")
        except CompareError as exc:
            self._set_message(str(exc))
        except Exception as exc:
            logger.error("Could not finish the job: %s", exc, exc_info=True)
            self._set_message(f"Could not finish that run: {exc}")
        finally:
            self._describe_grids()

    # ------------------------------------------------------------------
    # Swipe
    # ------------------------------------------------------------------
    def _on_swipe_toggled(self, checked):
        if checked:
            try:
                self._selection("A")
                self._selection("B")
            except CompareError as exc:
                self._set_message(str(exc))
                self.swipe_button.setChecked(False)
                return
            if self._swipe is None:
                self._swipe = SwipeOverlay(self.canvas)
                self._swipe.moved.connect(lambda _f: self._apply_swipe())
            self._swipe.show()
            self._swipe.reposition()
            self._apply_swipe()
        else:
            if self._swipe is not None:
                self._swipe.hide()
            self.canvas.clear_swipe_clips()
            self._set_message("Swipe off — every layer is showing in full again.")

    def _apply_swipe(self):
        """Clip A to the left of the divider and B to the right."""
        if self._swipe is None or not self.swipe_button.isChecked():
            return
        record_a = self.canvas.layers.get(self.layer_a.currentText())
        record_b = self.canvas.layers.get(self.layer_b.currentText())
        artist_a = record_a.get('artist') if record_a else None
        artist_b = record_b.get('artist') if record_b else None
        if artist_a is None or artist_b is None:
            self._set_message("Both layers need to be drawn on the map to swipe between them.")
            return

        self.canvas.apply_swipe_clip(self._swipe.fraction(), [artist_a], [artist_b])

        hidden = [name for name, record in (
            (self.layer_a.currentText(), record_a), (self.layer_b.currentText(), record_b)
        ) if not record.get('visible', True)]
        if hidden:
            self._set_message(f"Swipe is on, but {', '.join(hidden)} is hidden — "
                              "tick it in the layer list to see that half.")
        else:
            self._set_message("Swipe on: A on the left, B on the right. Drag the handle.")

    # ------------------------------------------------------------------
    def _set_message(self, text):
        self.message.setText(text)
        logger.debug("Compare dock: %s", text)

    def closeEvent(self, event):
        """Leave nothing clipped and no temporary file behind."""
        if self.swipe_button.isChecked():
            self.swipe_button.setChecked(False)
        self._temp.cleanup()
        super().closeEvent(event)
