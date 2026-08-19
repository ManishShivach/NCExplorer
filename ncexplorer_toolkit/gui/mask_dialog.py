# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""Mask a NetCDF file to a shapefile polygon.

Three routes to the same idea, offered because they cost very different amounts:

1. **Bounding box** — ``sellonlatbox`` on the polygon's extent. Not a real mask;
   it keeps the rectangle around the region. Always available, always fast, and
   often all that is wanted before a further calculation.
2. **True polygon mask** — ``maskregion`` against an ASCII region file written
   from the polygon outline. Cells outside the polygon become missing values.
3. **Crop, then mask** — the box first, the polygon second. On a global file
   this is materially faster than masking outright, because ``maskregion`` then
   tests a few thousand cells instead of a few million, and it is the default
   whenever the polygon is small next to the file's domain.

The longitude convention is the trap. A shapefile is −180…180 after geopandas
has reprojected it; a climate file is as often 0…360. CDO's own operators
normalise longitudes internally, so the region file would in fact survive the
mismatch — but the in-memory fallback below has no CDO to lean on, and the
cropping box would span the whole globe instead of one country. So the polygon
is moved into the dataset's convention first, splitting it at the antimeridian
where that cuts it in two (see :mod:`ncexplorer_toolkit.utils.regionmask`).

``maskregion`` is not in every CDO build. When it is missing the dialog says so
and masks in memory with xarray instead, writing the result with ``to_netcdf``;
the status bar reports which of the two paths actually ran, because "it worked"
and "it worked without CDO" are different facts about a result.
"""

from __future__ import annotations

import logging
import os

import numpy as np
import xarray as xr
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QRadioButton,
    QVBoxLayout, QWidget,
)

from ..utils import regionmask
from ..utils.logging_setup import redact_text
from ..utils.tempfile_store import TempFileStore

logger = logging.getLogger(__name__)

MODE_BOX = "Bounding box only (fast)"
MODE_MASK = "True polygon mask"
MODE_CROP_MASK = "Crop to the box, then mask (recommended)"

BOX_OPERATOR = "sellonlatbox"
MASK_OPERATOR = "maskregion"

#: Below this share of the file's domain a polygon counts as "small", and
#: cropping first is worth it.
SMALL_POLYGON_SHARE = 0.25

ALL_FEATURES = "All features (dissolved)"
BROWSE_ITEM = "Choose a file…"

#: Extensions CDO recognises for NetCDF output; anything else and CDO would
#: silently write a different format.
NETCDF_EXTENSIONS = ('.nc', '.nc2', '.nc4', '.nc5')


class MaskError(Exception):
    """The mask cannot be built, in a way the user should just be told."""


class MaskPlan:
    """Everything the worker needs, resolved on the UI thread."""

    def __init__(self, source, output, mode, geometry, box, use_cdo_box, use_cdo_mask):
        self.source = source
        self.output = output
        self.mode = mode
        self.geometry = geometry      # already in the dataset's convention
        self.box = box                # (lon1, lon2, lat1, lat2) or None
        self.use_cdo_box = use_cdo_box
        self.use_cdo_mask = use_cdo_mask


class MaskResult:
    """What the run produced, handed back to the UI thread."""

    def __init__(self, success, message, path=None, commands=None, method=""):
        self.success = success
        self.message = message
        self.path = path
        self.commands = commands or []
        self.method = method


# ----------------------------------------------------------------------
# The work itself
# ----------------------------------------------------------------------
def dataset_longitudes(path):
    """The longitude coordinate of a file, for deciding its convention."""
    try:
        with xr.open_dataset(path, decode_times=False) as dataset:
            _lat, lon = regionmask.find_lat_lon(list(dataset.coords) + list(dataset.dims))
            if lon and lon in dataset.variables:
                return np.asarray(dataset[lon].values, dtype=float).ravel()
    except Exception as exc:
        logger.debug("Could not read the longitude axis of %s: %s", path, exc)
    return None


def domain_share(geometry, path) -> float:
    """How much of a file's lon/lat domain the polygon's box covers, 0…1.

    Used only to pick a sensible default mode, so anything unreadable returns 1
    (i.e. "not obviously small") rather than raising.
    """
    try:
        with xr.open_dataset(path, decode_times=False) as dataset:
            lat_name, lon_name = regionmask.find_lat_lon(
                list(dataset.coords) + list(dataset.dims)
            )
            lats = np.asarray(dataset[lat_name].values, dtype=float)
            lons = np.asarray(dataset[lon_name].values, dtype=float)
        span = (float(lons.max() - lons.min()) or 360.0) * (float(lats.max() - lats.min()) or 180.0)
        min_x, min_y, max_x, max_y = geometry.bounds
        return float(((max_x - min_x) * (max_y - min_y)) / span) if span else 1.0
    except Exception as exc:
        logger.debug("Could not size the polygon against %s: %s", path, exc)
        return 1.0


def mask_in_memory(plan: MaskPlan) -> str:
    """Mask with xarray and shapely, for a CDO that has no ``maskregion``.

    Every variable carrying both horizontal dimensions is masked; anything else
    (a time bounds array, a scalar) is copied through untouched.
    """
    with xr.open_dataset(plan.source, decode_times=False) as dataset:
        lat_name, lon_name = regionmask.find_lat_lon(
            list(dataset.coords) + list(dataset.dims)
        )
        if not lat_name or not lon_name:
            raise MaskError("That file has no recognisable lat/lon coordinates to mask")

        lats = np.asarray(dataset[lat_name].values, dtype=float)
        lons = np.asarray(dataset[lon_name].values, dtype=float)

        result = dataset
        # Only the two cropping modes narrow the domain; a plain polygon mask
        # keeps the file's own extent, exactly as CDO's maskregion would.
        if plan.box is not None and plan.mode in (MODE_BOX, MODE_CROP_MASK):
            lon1, lon2, lat1, lat2 = plan.box
            lat_ok = (lats >= lat1) & (lats <= lat2)
            lon_ok = ((lons >= lon1) & (lons <= lon2) if lon1 <= lon2
                      else (lons >= lon1) | (lons <= lon2))
            if not lat_ok.any() or not lon_ok.any():
                raise MaskError("That polygon does not overlap the file's grid")
            result = result.isel({lat_name: np.flatnonzero(lat_ok),
                                  lon_name: np.flatnonzero(lon_ok)})
            lats = lats[lat_ok]
            lons = lons[lon_ok]

        if plan.mode != MODE_BOX:
            inside = regionmask.grid_mask(plan.geometry, lats, lons)
            if not inside.any():
                raise MaskError(
                    "That polygon covers no grid-cell centre — it is finer than "
                    "the file's resolution"
                )
            mask = xr.DataArray(inside, dims=(lat_name, lon_name),
                                coords={lat_name: lats, lon_name: lons})
            masked = {}
            for name, variable in result.data_vars.items():
                if lat_name in variable.dims and lon_name in variable.dims:
                    masked[name] = variable.where(mask)
                    masked[name].attrs = dict(variable.attrs)
            result = result.assign(masked)

        # load() first: to_netcdf on a dataset still backed by the file it is
        # about to overwrite is a good way to lose both.
        result.load().to_netcdf(plan.output)

    return plan.output


def output_has_data(path) -> bool:
    """True when the written file still has at least one non-missing value."""
    try:
        with xr.open_dataset(path, decode_times=False) as dataset:
            for variable in dataset.data_vars.values():
                if variable.size == 0:
                    continue
                sample = variable
                for dim in variable.dims[:-2]:
                    sample = sample.isel({dim: 0})
                values = np.asarray(sample.values, dtype=float)
                if np.isfinite(values).any():
                    return True
            return False
    except Exception as exc:
        logger.debug("Could not check %s for data: %s", path, exc)
        return True


def run_mask(plan: MaskPlan, integration, store: TempFileStore) -> MaskResult:
    """Execute a plan. Called on a worker thread; touches no widgets."""
    commands = []

    try:
        if plan.mode == MODE_BOX and not plan.use_cdo_box:
            path = mask_in_memory(plan)
            return MaskResult(True, "", path, commands, "xarray (no sellonlatbox)")

        if plan.mode != MODE_BOX and not plan.use_cdo_mask:
            path = mask_in_memory(plan)
            return MaskResult(True, "", path, commands,
                              f"xarray + shapely (no {MASK_OPERATOR})")

        source = plan.source

        if plan.mode in (MODE_BOX, MODE_CROP_MASK) and plan.box is not None:
            target = plan.output if plan.mode == MODE_BOX else store.new(suffix=".nc")
            result = integration.execute_operator(
                BOX_OPERATOR, input_files=source, output_files=target,
                extra_parameters=[f"{value:.6f}" for value in plan.box],
            )
            commands.append(integration.last_command)
            if not result.success:
                return MaskResult(False, redact_text(result.stderr.strip()) or "sellonlatbox failed",
                                  commands=commands)
            source = target

        if plan.mode == MODE_BOX:
            return MaskResult(True, "", source, commands, BOX_OPERATOR)

        region_file = store.new(suffix=".txt")
        regionmask.write_region_file(plan.geometry, region_file)

        result = integration.execute_operator(
            MASK_OPERATOR, input_files=source, output_files=plan.output,
            extra_parameters=[region_file],
        )
        commands.append(integration.last_command)
        if not result.success:
            return MaskResult(False, redact_text(result.stderr.strip()) or "maskregion failed",
                              commands=commands)

        method = (f"{BOX_OPERATOR} + {MASK_OPERATOR}"
                  if plan.mode == MODE_CROP_MASK else MASK_OPERATOR)
        return MaskResult(True, "", plan.output, commands, method)

    except MaskError as exc:
        return MaskResult(False, str(exc), commands=commands)
    except Exception as exc:
        logger.error("Masking failed: %s", exc, exc_info=True)
        return MaskResult(False, str(exc), commands=commands)


# ----------------------------------------------------------------------
# Dialog
# ----------------------------------------------------------------------
class MaskDialog(QDialog):
    """Clip or mask a NetCDF layer to one polygon of a shapefile layer."""

    #: Emitted by the worker thread; Qt marshals it back to the UI thread.
    finished_run = pyqtSignal(object)

    def __init__(self, main_window, polygon_layer, parent=None):
        super().__init__(parent or main_window)
        self.setWindowTitle(f"Mask by polygon — {polygon_layer}")
        self.setMinimumWidth(520)

        self.main_window = main_window
        self.canvas = main_window.geo_canvas
        self.polygon_layer = polygon_layer
        self._temp = TempFileStore(tag="ncexplorer_mask")
        self._busy = False

        self._features = regionmask.layer_features(self.canvas, polygon_layer)

        self._build_ui()
        self.finished_run.connect(self._on_finished)

        if not self._features:
            self._set_message(f"'{polygon_layer}' has no polygon features to mask with.")
            self.run_button.setEnabled(False)
        elif self.target_combo.currentText() != BROWSE_ITEM:
            # A loaded NetCDF layer is preselected, so fill in its path and the
            # suggested output straight away.
            self._on_target_selected(self.target_combo.currentText())
        else:
            self._on_target_changed()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        root = QVBoxLayout(self)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.feature_combo = QComboBox()
        labels = [ALL_FEATURES] + [f.name for f in self._features] if self._features else ["—"]
        self.feature_combo.addItems(labels)
        self.feature_combo.currentTextChanged.connect(lambda _name: self._on_target_changed())
        form.addRow("Polygon features:", self.feature_combo)

        target_row = QHBoxLayout()
        self.target_combo = QComboBox()
        self.target_combo.addItems(self._netcdf_choices())
        # activated, not currentTextChanged: picking "Choose a file…" again after
        # cancelling the file dialog has to reopen it, and an unchanged
        # selection emits no currentTextChanged.
        self.target_combo.activated.connect(
            lambda _index: self._on_target_selected(self.target_combo.currentText())
        )
        target_row.addWidget(self.target_combo, 1)
        self.target_path = QLineEdit()
        self.target_path.setPlaceholderText("NetCDF file to mask")
        self.target_path.setReadOnly(True)
        target_row.addWidget(self.target_path, 2)
        form.addRow("NetCDF to mask:", self._wrap(target_row))

        output_row = QHBoxLayout()
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Where to write the result")
        output_row.addWidget(self.output_edit, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._pick_output)
        output_row.addWidget(browse)
        form.addRow("Output file:", self._wrap(output_row))
        root.addLayout(form)

        modes = QGroupBox("Mask mode")
        mode_layout = QVBoxLayout(modes)
        self.mode_buttons = {}
        for mode, explanation in (
            (MODE_CROP_MASK, "Crop to the polygon's box first, then mask — fastest on a global file"),
            (MODE_MASK, "Mask the whole file: cells outside the polygon become missing"),
            (MODE_BOX, "Keep the rectangle around the polygon; no masking"),
        ):
            button = QRadioButton(mode)
            button.setToolTip(explanation)
            mode_layout.addWidget(button)
            self.mode_buttons[mode] = button
        self.mode_buttons[MODE_CROP_MASK].setChecked(True)
        root.addWidget(modes)

        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.message.setStyleSheet("color: palette(mid);")
        root.addWidget(self.message)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.run_button = buttons.addButton("Run", QDialogButtonBox.ButtonRole.AcceptRole)
        self.run_button.clicked.connect(self.run)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    @staticmethod
    def _wrap(layout):
        holder = QWidget()
        layout.setContentsMargins(0, 0, 0, 0)
        holder.setLayout(layout)
        return holder

    def _netcdf_choices(self) -> list[str]:
        """Loaded NetCDF layers, plus the option of any file on disk."""
        try:
            layers = dict(self.canvas.layers)
        except Exception:
            layers = {}
        names = [name for name, record in layers.items()
                 if record.get('type') == 'netcdf'
                 and isinstance(record.get('data'), str)
                 and os.path.exists(record['data'])]
        return names + [BROWSE_ITEM]

    # ------------------------------------------------------------------
    # Inputs
    # ------------------------------------------------------------------
    def _on_target_selected(self, choice):
        if choice == BROWSE_ITEM:
            path, _ = QFileDialog.getOpenFileName(
                self, "Choose a NetCDF file to mask", os.path.expanduser("~"),
                "NetCDF files (*.nc *.nc4 *.netcdf);;All files (*)",
            )
            self.target_path.setText(path or "")
        else:
            record = self.canvas.layers.get(choice) or {}
            self.target_path.setText(record.get('data') or "")
        self._on_target_changed()

    def _on_target_changed(self):
        """Refresh the suggested output, the default mode and the warnings."""
        source = self.target_path.text().strip()
        if not source:
            self._set_message("Choose a NetCDF file to mask.")
            self.run_button.setEnabled(bool(self._features))
            return

        stem = os.path.splitext(os.path.basename(source))[0]
        suggestion = os.path.join(os.path.dirname(source), f"{stem}_masked.nc")
        if not self.output_edit.text().strip() or self.output_edit.property("auto"):
            self.output_edit.setText(suggestion)
            self.output_edit.setProperty("auto", True)

        notes = []
        geometry = self._geometry(quiet=True)
        if geometry is not None:
            share = domain_share(geometry, source)
            if share < SMALL_POLYGON_SHARE:
                self.mode_buttons[MODE_CROP_MASK].setChecked(True)
                notes.append(
                    f"The polygon covers about {share:.1%} of this file's domain, "
                    "so cropping first is much faster."
                )
            else:
                self.mode_buttons[MODE_MASK].setChecked(True)

        missing = self._missing_operators()
        if MASK_OPERATOR in missing:
            notes.append(
                f"This build has no '{MASK_OPERATOR}', so the polygon mask will be "
                "applied in memory with xarray instead."
            )
        if BOX_OPERATOR in missing:
            notes.append(f"This build has no '{BOX_OPERATOR}'; cropping will also be "
                         "done in memory.")

        self._set_message(" ".join(notes))
        self.run_button.setEnabled(True)

    def _pick_output(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save masked file", self.output_edit.text() or os.path.expanduser("~"),
            "NetCDF files (*.nc);;All files (*)",
        )
        if path:
            self.output_edit.setText(path)
            self.output_edit.setProperty("auto", False)

    def _missing_operators(self) -> set[str]:
        try:
            signatures = self.main_window.NCExplorer.operator_signatures
        except Exception:
            return {BOX_OPERATOR, MASK_OPERATOR}
        return {name for name in (BOX_OPERATOR, MASK_OPERATOR) if name not in signatures}

    def _geometry(self, quiet=False):
        """The selected polygon, in −180…180, or None."""
        if not self._features:
            return None
        choice = self.feature_combo.currentText()
        if choice in (ALL_FEATURES, "—", ""):
            geometry = regionmask.dissolve(self._features)
        else:
            match = next((f for f in self._features if f.name == choice), None)
            geometry = match.geometry if match else None
        if geometry is None or geometry.is_empty:
            if not quiet:
                self._set_message("That polygon selection is empty.")
            return None
        return geometry

    # ------------------------------------------------------------------
    # Running
    # ------------------------------------------------------------------
    def run(self):
        """Validate everything, then start the run in the background."""
        if self._busy:
            return
        try:
            plan = self._plan()
        except MaskError as exc:
            self._set_message(str(exc))
            return
        except Exception as exc:
            logger.error("Could not prepare the mask: %s", exc, exc_info=True)
            self._set_message(f"Could not prepare the mask: {exc}")
            return

        self._busy = True
        self.run_button.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self._set_message(f"Running… writing {os.path.basename(plan.output)}")
        self.canvas.status_update.emit("Masking…")

        integration = self.main_window.NCExplorer
        store = self._temp

        def work():
            result = run_mask(plan, integration, store)
            if result.success and result.path and not output_has_data(result.path):
                result = MaskResult(
                    False,
                    "The result is empty — that polygon does not overlap the data.",
                    commands=result.commands,
                )
            self.finished_run.emit(result)

        try:
            self.canvas.thread_pool.submit(work)
        except Exception as exc:
            logger.warning("Thread pool rejected the mask job, running inline: %s", exc)
            work()

    def _plan(self) -> MaskPlan:
        source = self.target_path.text().strip()
        if not source or not os.path.exists(source):
            raise MaskError("Choose a NetCDF file to mask.")

        output = self.output_edit.text().strip()
        if not output:
            raise MaskError("Choose where to write the result.")
        if os.path.splitext(output)[1].lower() not in NETCDF_EXTENSIONS:
            # CDO reads the format off the extension alone, so a bare name would
            # quietly produce something that is not NetCDF.
            output += ".nc"
            self.output_edit.setText(output)
        if os.path.abspath(output) == os.path.abspath(source):
            raise MaskError("The output would overwrite the input — choose another name.")

        geometry = self._geometry()
        if geometry is None:
            raise MaskError("Choose which polygon features to mask with.")

        longitudes = dataset_longitudes(source)
        want_360 = regionmask.uses_360(longitudes) if longitudes is not None else False
        try:
            geometry = regionmask.to_longitude_convention(geometry, want_360)
            lon1, lon2, lat1, lat2 = regionmask.box_arguments(geometry, want_360)
        except regionmask.RegionError as exc:
            raise MaskError(str(exc)) from exc

        mode = next((name for name, button in self.mode_buttons.items()
                     if button.isChecked()), MODE_CROP_MASK)
        missing = self._missing_operators()

        logger.info("Masking %s with %s (%s), box %.3f,%.3f,%.3f,%.3f, 0-360=%s",
                    os.path.basename(source), self.feature_combo.currentText(), mode,
                    lon1, lon2, lat1, lat2, want_360)

        return MaskPlan(
            source=source, output=output, mode=mode, geometry=geometry,
            box=(lon1, lon2, lat1, lat2),
            use_cdo_box=BOX_OPERATOR not in missing,
            use_cdo_mask=MASK_OPERATOR not in missing,
        )

    def _on_finished(self, result: MaskResult):
        """Report the run and, on success, put the output on the map."""
        self._busy = False
        self.run_button.setEnabled(True)
        QApplication.restoreOverrideCursor()

        console = getattr(self.main_window, 'output_console', None)
        for command in result.commands:
            logger.info("Command: %s", command)
            if console is not None:
                console.append(f"$ {command}")

        if not result.success:
            self._set_message(result.message or "The mask failed.")
            self.canvas.status_update.emit("Masking failed")
            return

        self.main_window.current_output_file = result.path
        for name in ('save_btn', 'visualize_btn'):
            widget = getattr(self.main_window, name, None)
            if widget is not None:
                widget.setEnabled(True)

        loaded = False
        try:
            loaded = bool(self.main_window.visualize_file(result.path))
        except Exception as exc:
            logger.error("Could not display the masked file: %s", exc, exc_info=True)

        summary = f"Wrote {os.path.basename(result.path)} using {result.method}."
        self._set_message(summary + ("" if loaded else " It is not on the map — open it manually."))
        self.canvas.status_update.emit(summary)
        if console is not None:
            console.append(f"✓ {summary}")

    def _set_message(self, text):
        self.message.setText(text)
        if text:
            logger.debug("Mask dialog: %s", text)

    def closeEvent(self, event):
        self._temp.cleanup()
        super().closeEvent(event)
