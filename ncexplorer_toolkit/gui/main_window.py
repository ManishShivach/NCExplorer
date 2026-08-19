# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
from PyQt6.QtCore import Qt, QSettings, pyqtSlot
from PyQt6.QtGui import QAction, QFont
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLineEdit, QPushButton, QLabel, QTextEdit, QFileDialog,
    QFormLayout, QScrollArea, QMessageBox, QDockWidget,
    QListWidget, QListWidgetItem, QAbstractItemView, QSizePolicy,
    QComboBox, QCheckBox
)
import os
import shlex
from collections.abc import Callable

from ..core.nc_integration import OUTPUT_EXTENSIONS as _CDO_EXTENSIONS
from ..core.nc_integration import stream_notices
from ..core import filetypes as ft
from ..core.categories import input_file_kind, parameter_file_kind
from ..resources.branding import apply_window_icon

from .toolbar import NCExplorerToolbar
from .compare_dock import CompareDock
from .stats_dock import StatsDock
from ..geocanvas.canvas import GeoCanvas
from ..geocanvas import projections
from ..geocanvas.offline_basemap import discover_mbtiles
from ..geocanvas.properties import LayerPropertyEditor
from .widgets import MultiSelectEdit, QIntValidator, QDoubleValidator
from ..core.nc_integration import create_NCExplorer_integration
from ..core.project import (
    NETCDF_SELECTION_KEYS, PROJECT_SUFFIX, CanvasState, LayerState, ProjectError,
    ProjectState, ensure_suffix, load_project, pipeline_from_steps, resolve_layers,
    save_project, suggest_project_path
)
from ..core.session_log import (OK, OperatorRequest, SessionStep,
                                write_stdout_capture)
from ..utils.logging_setup import redact_text
from .batch_dialog import BatchDialog
from .command_palette import CommandPalette
from .execution_controller import ExecutionController
from .menubar import MenuBar
from .layer_manager import LayerManager
from .file_explorer import FileExplorer
from .log_dock import LogDock
from .nav_overlay import NavOverlay
from .plot_dock import PlotDock
from .recent_files import RecentFilesStore
from .model_builder import ModelBuilderWindow
from .session_dock import SessionDock
from .shortcuts import ShortcutCheatSheet, register_shortcuts
from .time_player import TimePlayerDock
from ..core.categories import (
    OPERATOR_CATEGORIES, NCExplorerCategory, expected_plot_files,
    missing_required_parameters, operator_options_hint, operator_syntax,
    writes_images, writes_output_prefix,
)

import logging
logger = logging.getLogger(__name__)


def _operator_arity(operator: str) -> tuple[int, int]:
    """``(nin, nout)`` for one operator, from the schema and nowhere else.

    One function because three places in this file need the answer and all
    three have to give the *same* answer: the form builder decides from it
    whether to render a ``MultiFileInputWidget``, ``parse_parameters`` decides
    how many "Input File" rows to emit, and ``execute_operation`` validates the
    collected file count against it. Disagreement between them is not a wrong
    number on screen, it is an operator that cannot be run.

    That is what happened. All three read the hand-maintained
    ``OPERATOR_SIGNATURES`` with a ``(1, 1)`` default, and it was 227 operators
    short of the catalog, so 38 two-input operators produced one input row, were
    validated against one file, and reached CDO as ``cdo timcor in.nc out.nc``
    — "cdo (Abort): Missing inputs". Among them all twelve ``ymon``/``yseas``
    comparison operators and all four of the Correlation section, none of which
    could be run from this panel at all. ``eq``/``ne``/``le``/``lt``/``ge``/
    ``gt`` were in the old table and worked, which is why it went unnoticed.

    ``OPERATOR_SCHEMA`` takes its signatures from ``cdo --operators``, and is
    what the model builder, the command palette and the batch surfaces have
    always read; ``audit_operator_surfaces.py`` now fails if any surface
    disagrees with it about arity.

    The ``(1, 1)`` fallback is kept for a genuinely unknown operator — a project
    saved against a CDO build that had one this one does not — because refusing
    to draw a form at all is a worse answer than drawing the commonest one.
    """
    try:
        from ..core.categories import get_operator_spec
    except ImportError:                                         # pragma: no cover
        from ncexplorer_toolkit.core.categories import get_operator_spec

    spec = get_operator_spec(operator)
    return (spec.nin, spec.nout) if spec is not None else (1, 1)


def _operator_outputs(operator: str, nout: int):
    """The output slots of ``operator``, always ``nout`` of them.

    Wraps ``categories.operator_outputs`` with the same unknown-operator
    tolerance :func:`_operator_arity` has: a project saved against a CDO build
    that had an operator this one does not must still draw a form. Falls back to
    unlabelled slots, which is what the form drew for every multi-output
    operator before the metadata existed.
    """
    try:
        from ..core.categories import OperatorOutput, operator_outputs
    except ImportError:                                         # pragma: no cover
        from ncexplorer_toolkit.core.categories import (
            OperatorOutput, operator_outputs)

    declared = operator_outputs(operator)
    if len(declared) == max(nout, 0):
        return declared
    return tuple(OperatorOutput(f"Output File {index + 1}", "")
                 for index in range(max(nout, 0)))


def _operator_environment(operator: str):
    """The environment variables ``operator`` reads, or none.

    Empty for all but the eight operators of the EOFs section, so every other
    form is drawn exactly as it was.
    """
    try:
        from ..core.categories import operator_env
    except ImportError:                                         # pragma: no cover
        from ncexplorer_toolkit.core.categories import operator_env

    return operator_env(operator)


def _reads_stdin(operator: str) -> bool:
    """True for the three operators whose field data arrives on standard input.

    Asked of the schema rather than of a list here, so the form and the
    execution layer answer it from the same place; see
    ``categories.reads_stdin``.
    """
    try:
        from ..core.categories import reads_stdin
    except ImportError:                                         # pragma: no cover
        from ncexplorer_toolkit.core.categories import reads_stdin

    return reads_stdin(operator)


#: The prefix ``parse_parameters`` puts on an environment-variable row's label.
#: Shared with ``_collect_environment`` so the two cannot disagree about which
#: rows of the form are arguments and which are environment.
ENV_LABEL_PREFIX = "Env: "

#: Captions for the three rows that are not operator arguments. Each is a
#: module-level constant for the reason ``_output_field_label`` is a function:
#: ``parse_parameters`` creates the row under this label and
#: ``execute_operation`` looks the widget up by it, and a disagreement between
#: the two is a silently ignored field rather than a visible error.
#:
#: None of the three may contain "Input File", "Output" or "prefix", which are
#: the substrings ``execute_operation`` scans the widget dictionary for when it
#: collects the operator's real files. Checked in
#: ``test_import_export_category.py::test_the_extra_rows_cannot_be_mistaken_for_files``
#: rather than left to the eye, since the failure is silent.
STDIN_FILE_LABEL = "Data File (read from stdin)"
STDOUT_FILE_LABEL = "Save reading to"
CDO_OPTIONS_LABEL = "CDO global options"

#: One caption plus its extensions, as Qt wants them. Kept here for the canvas
#: chooser below, which builds its list from the geocanvas format registry; the
#: operator form's filters come from ``core/filetypes.py`` instead.
def _filter(caption: str, extensions) -> str:
    return f"{caption} ({' '.join('*' + e for e in extensions)})"


#: What a printed reading is saved as, and what an ``nin == 0`` operator reads
#: on standard input. Plain text either way.
STDOUT_FILE_DIALOG_FILTER = ft.STDOUT_FILTER

#: Where a run may write.
OUTPUT_FILE_DIALOG_FILTER = ft.OUTPUT_FILTER

# There is deliberately no ``INPUT_FILE_DIALOG_FILTER`` beside these two. What
# stood here was one nine-entry filter handed to every browse button in the
# form, and seven of those entries were not CDO inputs at all — GeoTIFF,
# shapefile, GeoJSON, GeoPackage, GML, KML, and an "All Supported Files" entry
# that unioned them together. They were there because this constant served the
# *map canvas* as well, whose formats those are. CDO reads GRIB, NetCDF,
# SERVICE, EXTRA and IEG (manual §1.1) and nothing else, so every one of them
# was an offer the run could not honour.
#
# An input's chooser now comes from its slot — ``input_file_kind`` for a data
# input, ``parameter_file_kind`` for a file-valued parameter — because the two
# genuinely differ and one constant cannot say so: ``import_binary`` wants a
# GrADS ``.ctl`` where every other operator wants a dataset, and ``remap``
# wants a SCRIP NetCDF for its weights and a grid description beside it.


def _canvas_file_dialog_filter() -> str:
    """The Open-dialog filter for things the *canvas* can draw.

    Built from geocanvas/formats.py at call time rather than written out, so a
    format added to the registry reaches this dialog without a second edit —
    and, more importantly, so the dialog offers exactly what the loader
    accepts. Those two sets were maintained separately and had drifted: the
    dialog listed ``.shp`` and ``.geojson`` only, while the canvas also claimed
    KML and GPX.

    Only formats that are usable in *this* process are offered, since driver
    availability differs between a source checkout and the packaged build.
    """
    from ..geocanvas import formats as fmts

    usable = [f for f in fmts.FORMATS if fmts.availability(f)[0]]
    groups = [_filter("All Supported Files",
                      tuple(e for f in usable for e in f.extensions))]
    groups += [_filter(f.label, f.extensions) for f in usable]
    groups.append("All Files (*)")
    return ";;".join(groups)


def _output_field_label(operator: str, index: int, nout: int) -> str:
    """The form's caption for output slot ``index``.

    One function because two places need the answer and they must agree
    exactly: ``parse_parameters`` creates the row under this label, and
    ``execute_operation`` looks the widget up by it. A disagreement between them
    is not a cosmetic problem — it is a run refused for an empty output field
    the user can see they filled in.

    Numbered even when the slot is named, because the order is what CDO reads.
    Falls back to the caption the form used before the metadata existed, which
    is what every operator that declares nothing still gets.
    """
    slots = _operator_outputs(operator, nout)
    role = slots[index].role if index < len(slots) else ""
    if role and not role.startswith("Output"):
        return f"Output {index + 1}: {role}"
    return f"Output File {index + 1}"


class MultiFileInputWidget(QWidget):
    """
    File list widget with drag-and-drop reordering.

    nin == 2  → no folder button, max 2 files (exact pair selection)
    nin == 3  → folder button + add file, no cap
    nin == -1 → folder button + add file, no cap

    Both this widget and the single-input browse button ask the schema what the
    operator's inputs are, so which of the two a user meets no longer changes
    what they are offered. That mattered when the two filters were written out
    separately: this one had no ``.grb2`` and the browse button had no ``.ctl``,
    and the difference was decided by the operator's arity.
    """

    def __init__(self, nin: int = -1, parent=None, *, file_kind: str = ft.DATA):
        super().__init__(parent)
        self._nin = nin          # expected number of input files (-1 = unlimited)
        #: What CDO reads in these slots, as a ``core/filetypes.py`` key. Every
        #: slot of a multi-input operator holds the same kind of file — there is
        #: no operator in the catalog that takes a dataset in one input and a
        #: descriptor in another — so one kind for the whole list is enough.
        self._file_kind = file_kind
        self._build_ui()

    @property
    def FILE_FILTER(self) -> tuple:
        """Extensions the folder scan accepts."""
        return ft.extensions_for(self._file_kind)

    @property
    def FILE_DIALOG_F(self) -> str:
        """The chooser's filter string."""
        return ft.dialog_filter(self._file_kind)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        # ── button row ──────────────────────────────────────────────
        btn_row = QHBoxLayout()

        # Folder button — only for nin != 2
        if self._nin != 2:
            self.folder_btn = QPushButton("📂 Select Folder")
            self.folder_btn.setToolTip("Load all supported files from a folder")
            self.folder_btn.clicked.connect(self._pick_folder)
            btn_row.addWidget(self.folder_btn)

        # Add File button — always present
        cap_hint = f" (max {self._nin})" if self._nin == 2 else ""
        self.add_btn = QPushButton(f"➕ Add File{cap_hint}")
        self.add_btn.setToolTip(
            f"Add one of the {self._nin} required files"
            if self._nin == 2 else "Add a file to the list"
        )
        self.add_btn.clicked.connect(self._add_file)
        btn_row.addWidget(self.add_btn)

        self.remove_btn = QPushButton("✖ Remove")
        self.remove_btn.setToolTip("Remove selected file")
        self.remove_btn.clicked.connect(self._remove_selected)
        btn_row.addWidget(self.remove_btn)

        self.clear_btn = QPushButton("🗑 Clear")
        self.clear_btn.clicked.connect(self._clear)
        btn_row.addWidget(self.clear_btn)

        btn_row.addStretch()
        root.addLayout(btn_row)

        # ── hint label ──────────────────────────────────────────────
        if self._nin == 2:
            hint_text = "Add exactly 2 files — drag to set order"
        elif self._nin > 0:
            hint_text = f"Add exactly {self._nin} files — drag rows to reorder"
        else:
            hint_text = ("Add any number of files — drag rows to reorder "
                         "(order = argument order)")

        hint = QLabel(hint_text)
        hint.setStyleSheet("color: gray; font-size: 10px;")
        root.addWidget(hint)

        # ── list widget (drag-to-reorder) ────────────────────────────
        self.list_widget = QListWidget()
        self.list_widget.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_widget.setMinimumHeight(100)
        self.list_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        # Refresh numbers after any internal drag-drop reorder
        self.list_widget.model().rowsMoved.connect(self._refresh_numbers)
        root.addWidget(self.list_widget)

    # ── public API ───────────────────────────────────────────────────

    def get_files(self) -> list[str]:
        """Return file paths in current list order."""
        return [
            self.list_widget.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.list_widget.count())
        ]

    # ── private helpers ──────────────────────────────────────────────

    def _pick_folder(self):
        """Load all supported files from a chosen folder (nin != 2 only)."""
        folder = QFileDialog.getExistingDirectory(
            self, "Select Folder", "",
            QFileDialog.Option.DontUseNativeDialog
        )
        if not folder:
            return
        import pathlib
        files = sorted(
            str(p) for p in pathlib.Path(folder).iterdir()
            if p.suffix.lower() in self.FILE_FILTER
        )
        if not files:
            QMessageBox.information(
                self, "No files found",
                f"No supported files found in:\n{folder}"
            )
            return
        self.list_widget.clear()
        for f in files:
            self._add_item(f)

    def _add_file(self):
        """Add a single file, enforcing the cap for nin == 2."""
        if self._nin == 2 and self.list_widget.count() >= 2:
            QMessageBox.warning(
                self, "Limit reached",
                "This operator needs exactly 2 input files.\n"
                "Remove one before adding another."
            )
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Add File", "",
            self.FILE_DIALOG_F,
            options=QFileDialog.Option.DontUseNativeDialog
        )
        if path:
            self._add_item(path)

    def _add_item(self, path: str):
        item = QListWidgetItem(
            f"{self.list_widget.count() + 1}.  {os.path.basename(path)}"
        )
        item.setData(Qt.ItemDataRole.UserRole, path)
        item.setToolTip(path)
        self.list_widget.addItem(item)

    def _remove_selected(self):
        for item in self.list_widget.selectedItems():
            self.list_widget.takeItem(self.list_widget.row(item))
        self._refresh_numbers()

    def _clear(self):
        self.list_widget.clear()

    def _refresh_numbers(self, *_):
        """Keep the visible 1. 2. 3. prefix in sync after reordering/removal."""
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            path = item.data(Qt.ItemDataRole.UserRole)
            item.setText(f"{i + 1}.  {os.path.basename(path)}")

class NCExplorerOperatorGUI(QMainWindow):
    def __init__(self, progress: Callable[[float, str], None] | None = None):
        """Build the main window.

        ``progress`` is an optional reporter called with a fraction of *this
        constructor's* work (0.0-1.0) and a caption for what is happening.
        main.py passes the splash screen's setter through it, mapped onto the
        tail of the startup bar; everything else — tests, the operator lab —
        constructs the window without one and pays nothing.
        """
        super().__init__()

        # No logging configuration here: handlers are installed once by
        # utils/logging_setup.configure_logging(), called from main.py before this
        # window exists.
        self._startup_progress = progress or (lambda _fraction, _caption: None)

        # Initialize basic properties first
        self.current_layer = None
        self.current_output_file = None
        self.parameter_widgets = {}
        self.current_operator = None
        self.last_stdout = ""
        self.debug_mode = False
        self._output_line_edit = None   # QLineEdit for the current operator's output file
        self._command_palette = None    # built on first Ctrl+K, then reused

        # Built before anything that logs, so startup records — CDO discovery,
        # canvas construction — are already in the dock when it is first opened.
        self.log_dock = LogDock(self)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.log_dock)
        self.log_dock.visibilityChanged.connect(self._sync_log_dock_action)

        # The menu bar builds File > Open Recent from this, so it has to exist
        # before MenuBar is constructed.
        self.recent_files = RecentFilesStore()

        # Create NCExplorer integration
        self._startup_progress(0.05, "Locating CDO…")
        self.NCExplorer = create_NCExplorer_integration()

        # Basic window setup
        self.setWindowTitle("Geospatial Analysis Software")
        # main.py sets this on the QApplication, which is enough for a normal
        # launch; doing it here too covers the window being built directly —
        # tests, the operator lab, an embedding host.
        apply_window_icon(self)
        self.setGeometry(100, 100, 1200, 800)

        # Files can be dropped anywhere on the window. The canvas already accepts
        # drops over the map itself and, being the child under the cursor there,
        # keeps handling them; these handlers cover everything around it.
        self.setAcceptDrops(True)

        # Create a menu bar
        self._startup_progress(0.15, "Building menus and toolbars…")
        self.menu_bar = MenuBar(self)
        self.setMenuBar(self.menu_bar)

        # Create toolbar
        self.toolbar = NCExplorerToolbar(self)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.toolbar)

        # Create a central widget and main layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # Remove all margins from the main layout
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Create a GeoCanvas instance directly
        self._startup_progress(0.35, "Drawing the map canvas…")
        self.geo_canvas = GeoCanvas(self)
        main_layout.addWidget(self.geo_canvas)

        # Connect GeoCanvas signals
        self.geo_canvas.map_clicked.connect(self.handle_map_click)
        self.geo_canvas.layer_added.connect(self.handle_layer_added)
        self.geo_canvas.layer_removed.connect(self.handle_layer_removed)
        self.geo_canvas.file_loaded.connect(self.handle_file_loaded)
        self.geo_canvas.loading_error.connect(self.handle_loading_error)
        self.geo_canvas.progress_update.connect(self.handle_progress_update)
        self.geo_canvas.status_update.connect(self.handle_status_update)
        self.geo_canvas.layer_properties_requested.connect(self.handle_layer_properties)

        # Basemap and projection selectors, beside the operator classes. Both
        # insert at the head of the toolbar in the order they are built.
        self._setup_basemap_selector()
        self._setup_projection_selector()

        # Permanent lat/lon/value readout in the status bar
        self._setup_cursor_readout()

        # On-canvas navigation cluster and the keyboard bindings that mirror it
        self.nav_overlay = NavOverlay(self.geo_canvas, self)
        self.geo_canvas.attach_nav_overlay(self.nav_overlay)
        # current_layer is updated by the handlers connected above, so these
        # run after it and see the new value.
        self.geo_canvas.layer_added.connect(self.nav_overlay.refresh_state)
        self.geo_canvas.layer_removed.connect(self.nav_overlay.refresh_state)
        self.registered_shortcuts = register_shortcuts(self)

        # Dock widget for parameters
        self.param_dock = QDockWidget("Operator Parameters", self)
        self.param_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.param_dock)

        # Parameters container
        self.params_container = QWidget()
        self.params_layout = QFormLayout(self.params_container)
        self.params_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        # Scroll area for parameters
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(self.params_container)
        self.param_dock.setWidget(scroll_area)

        # Initially hide the dock until an operator is selected
        self.param_dock.hide()

        # Dock widget for layer properties
        self.property_dock = QDockWidget("Layer Properties", self)
        self.property_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.property_dock.setMinimumWidth(460)
        self.property_dock.setMinimumHeight(560)
        self.property_editor = LayerPropertyEditor(self.geo_canvas.property_manager, self)
        self.property_editor.property_changed.connect(self.on_property_changed)
        self.property_dock.setWidget(self.property_editor)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.property_dock)
        self.tabifyDockWidget(self.param_dock, self.property_dock)
        self.property_dock.hide()

        # Output console
        output_group = QGroupBox("Output")
        output_layout = QVBoxLayout(output_group)

        self.output_console = QTextEdit()
        self.output_console.setReadOnly(True)
        self.output_console.setPlaceholderText("NCExplorer output will appear here...")

        output_layout.addWidget(self.output_console)
        main_layout.addWidget(output_group)

        # Button layout
        button_layout = QHBoxLayout()

        # Execute button
        self.execute_btn = QPushButton("Execute NCExplorer Operation")
        self.execute_btn.clicked.connect(self.execute_operation)
        button_layout.addWidget(self.execute_btn)

        # Save button for info operators
        self.save_btn = QPushButton("Save Output")
        self.save_btn.clicked.connect(self.save_output)
        self.save_btn.setEnabled(False)
        button_layout.addWidget(self.save_btn)

        # Visualize button
        self.visualize_btn = QPushButton("Visualize Output")
        self.visualize_btn.clicked.connect(self.visualize_output)
        self.visualize_btn.setEnabled(False)
        button_layout.addWidget(self.visualize_btn)

        # Clear output button
        self.clear_output_btn = QPushButton("Clear Output")
        self.clear_output_btn.clicked.connect(self.clear_output_log)
        self.clear_output_btn.setToolTip("Clear the output console")
        button_layout.addWidget(self.clear_output_btn)

        # Operators run off the UI thread. The controller owns Cancel and the
        # progress bar, and is what locks the form for the duration of a run;
        # everything below only reacts to the outcome.
        self.execution = ExecutionController(self)
        button_layout.insertWidget(1, self.execution.cancel_button)
        self.statusBar().addPermanentWidget(self.execution.progress_bar)
        self.execution.finished.connect(self.handle_operation_finished)
        self.execution.failed.connect(self.handle_operation_failed)
        self.execution.cancelled.connect(self.handle_operation_cancelled)

        main_layout.addLayout(button_layout)

        # Create a layer manager dock
        self.layer_dock = QDockWidget("Layer Manager", self)
        self.layer_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.layer_dock)

        # Create a layer manager widget
        self.layer_manager = LayerManager(self)
        self.layer_dock.setWidget(self.layer_manager)

        # Connect layer manager signals
        self.layer_manager.layer_visibility_changed.connect(self.handle_layer_visibility_changed)
        self.layer_manager.layer_removed.connect(self.handle_layer_removed)
        self.layer_manager.layer_properties_requested.connect(self.handle_layer_properties)
        self.layer_manager.time_slider_requested.connect(self.geo_canvas.open_time_slider)
        self.layer_manager.layer_order_changed.connect(self.handle_layer_order_changed)

        # Create a file explorer dock
        self._startup_progress(0.75, "Adding the docks…")
        self.file_explorer_dock = QDockWidget("File Explorer", self)
        self.file_explorer_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)

        # Create file explorer
        self.file_explorer = FileExplorer(self)
        self.file_explorer_dock.setWidget(self.file_explorer)

        # Position file explorer above layer manager
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.file_explorer_dock)

        # Add layer manager dock below file explorer
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.layer_dock)

        # Create a tabified interface if desired, or stack vertically
        # For stacking: (file explorer will be above layer manager)
        self.resizeDocks([self.file_explorer_dock, self.layer_dock], [200, 300], Qt.Orientation.Vertical)

        # Connect file explorer signals
        self.file_explorer.file_double_clicked.connect(self.load_file_from_explorer)
        self.file_explorer.file_selected.connect(self.preview_file_info)

        # Animation and plot docks, built last: both need the canvas, and the
        # docks they tabify with have to exist before tabifyDockWidget is called.
        self._startup_progress(0.9, "Loading the analysis panels…")
        self._setup_analysis_docks()

        # Project tracking, last of all: it subscribes to the canvas and to the
        # session dock, so both have to exist by now.
        self._setup_project_tracking()

    def _setup_analysis_docks(self):
        """Install the animation player, the plot, statistics and compare panels.

        All start hidden — they are opt-in tools, and the plot and statistics
        docks explicitly do no work while they are not visible.
        """
        self.time_player_dock = TimePlayerDock(self)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.time_player_dock)
        self.tabifyDockWidget(self.log_dock, self.time_player_dock)
        self.time_player_dock.hide()
        self.time_player_dock.visibilityChanged.connect(self._sync_animation_dock_action)

        self.plot_dock = PlotDock(self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.plot_dock)
        self.tabifyDockWidget(self.property_dock, self.plot_dock)
        self.plot_dock.hide()
        self.plot_dock.visibilityChanged.connect(self._sync_plot_dock_action)

        self.stats_dock = StatsDock(self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.stats_dock)
        self.tabifyDockWidget(self.property_dock, self.stats_dock)
        self.stats_dock.hide()
        self.stats_dock.visibilityChanged.connect(self._sync_stats_dock_action)

        self.compare_dock = CompareDock(self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.compare_dock)
        self.tabifyDockWidget(self.property_dock, self.compare_dock)
        self.compare_dock.hide()
        self.compare_dock.visibilityChanged.connect(self._sync_compare_dock_action)

        # Records every run for export and replay. Built here because it
        # subscribes to the execution controller, which already exists by now.
        self.session_dock = SessionDock(self)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.session_dock)
        self.tabifyDockWidget(self.log_dock, self.session_dock)
        self.session_dock.hide()
        self.session_dock.visibilityChanged.connect(self._sync_session_dock_action)

        # Not a dock. The panels above annotate the map and belong beside it; a
        # model is drawn with the map irrelevant and wants the whole screen, so
        # it is a window of its own with minimise, maximise and full screen. It
        # subscribes to the execution controller through its runner, so it is
        # built here rather than earlier, and starts hidden like its siblings.
        self.model_builder = ModelBuilderWindow(self)
        self.model_builder.hide()
        self.model_builder.visibilityChanged.connect(
            self._sync_model_builder_action)

    # ------------------------------------------------------------------
    # Animation dock
    # ------------------------------------------------------------------
    def toggle_animation_dock(self, checked):
        """Show or hide the animation player."""
        self.time_player_dock.setVisible(checked)
        if checked:
            self.time_player_dock.raise_()
            # Layers loaded while the dock was hidden are not in its list yet.
            self.time_player_dock.refresh_layers()
        else:
            self.time_player_dock.pause()

    def _sync_animation_dock_action(self, visible):
        """Keep the View menu checkbox in step with the dock itself."""
        action = getattr(self.menu_bar, 'animation_dock_action', None)
        if action is not None and action.isChecked() != visible:
            action.setChecked(visible)
        if not visible:
            self.time_player_dock.pause()

    def toggle_animation_play(self):
        """Space: play/pause, opening the dock if it is not already up."""
        if not self.time_player_dock.isVisible():
            self.toggle_animation_dock(True)
        self.time_player_dock.toggle_play()

    def animation_step_back(self):
        if not self.time_player_dock.isVisible():
            self.toggle_animation_dock(True)
        self.time_player_dock.step_back()

    def animation_step_forward(self):
        if not self.time_player_dock.isVisible():
            self.toggle_animation_dock(True)
        self.time_player_dock.step_forward()

    # ------------------------------------------------------------------
    # Plot dock
    # ------------------------------------------------------------------
    def toggle_plot_dock(self, checked):
        """Show or hide the click-to-plot panel."""
        self.plot_dock.setVisible(checked)
        if checked:
            self.plot_dock.raise_()

    def _sync_plot_dock_action(self, visible):
        action = getattr(self.menu_bar, 'plot_dock_action', None)
        if action is not None and action.isChecked() != visible:
            action.setChecked(visible)

    # ------------------------------------------------------------------
    # Statistics dock
    # ------------------------------------------------------------------
    def toggle_stats_dock(self, checked):
        """Show or hide the region statistics panel."""
        self.stats_dock.setVisible(checked)
        if checked:
            self.stats_dock.raise_()
            # Layers loaded while the dock was hidden are not in its lists yet,
            # and it computes nothing at all while it is invisible.
            self.stats_dock.refresh_layers()

    def _sync_stats_dock_action(self, visible):
        action = getattr(self.menu_bar, 'stats_dock_action', None)
        if action is not None and action.isChecked() != visible:
            action.setChecked(visible)

    # ------------------------------------------------------------------
    # Compare dock
    # ------------------------------------------------------------------
    def toggle_compare_dock(self, checked):
        """Show or hide the two-layer comparison panel."""
        self.compare_dock.setVisible(checked)
        if checked:
            self.compare_dock.raise_()
            self.compare_dock.refresh_layers()

    def _sync_compare_dock_action(self, visible):
        action = getattr(self.menu_bar, 'compare_dock_action', None)
        if action is not None and action.isChecked() != visible:
            action.setChecked(visible)

    # ------------------------------------------------------------------
    # Model builder dock
    # ------------------------------------------------------------------
    def toggle_model_builder_window(self, checked):
        """Show or hide the model builder window."""
        if not checked:
            self.model_builder.hide()
            return
        # showNormal rather than show: a window minimised to the dock or the
        # taskbar is still "visible" as far as Qt is concerned, so a plain show()
        # on it would tick the menu entry and change nothing on screen.
        if self.model_builder.isMinimized():
            self.model_builder.showNormal()
        else:
            self.model_builder.show()
        self.model_builder.raise_()
        self.model_builder.activateWindow()

    def _sync_model_builder_action(self, visible):
        """Keep the Model menu checkbox in step with the window itself."""
        action = getattr(self.menu_bar, 'model_builder_action', None)
        if action is not None and action.isChecked() != visible:
            action.setChecked(visible)

    # ------------------------------------------------------------------
    # Session dock
    # ------------------------------------------------------------------
    def toggle_session_dock(self, checked):
        """Show or hide the recorded-session panel."""
        self.session_dock.setVisible(checked)
        if checked:
            self.session_dock.raise_()

    def _sync_session_dock_action(self, visible):
        action = getattr(self.menu_bar, 'session_dock_action', None)
        if action is not None and action.isChecked() != visible:
            action.setChecked(visible)

    # Basemap display names — must match the keys built in
    # GeoCanvas._get_basemap_providers(). "None" falls back to the built-in
    # Cartopy land/ocean/coastline backdrop.
    BASEMAP_CHOICES = [
        "None",
        GeoCanvas.OFFLINE_NATURAL_EARTH,
        "Carto Light",
        "Carto Dark",
        "Carto Voyager",
        "Satellite (Esri)",
        "Satellite (Sentinel-2)",
        "Topographic",
        "Topographic (Esri)",
        "Terrain (Esri)",
        "Shaded relief (Esri)",
        "National Geographic",
        "Ocean (Esri)",
        "NASA Blue Marble",
        "NASA Night Lights",
    ]

    #: Trailing selector entry that opens a file chooser rather than a basemap.
    LOAD_MBTILES_ITEM = "Load MBTiles…"

    #: Scanned at startup for ready-to-use offline archives.
    MBTILES_DIRECTORY = os.path.join(os.path.expanduser("~"), ".ncexplorer", "basemaps")

    #: QSettings key holding every MBTiles path the user has opened.
    MBTILES_SETTINGS_KEY = "basemap/mbtiles_paths"

    def _setup_basemap_selector(self):
        """Put a 'Basemap' dropdown at the head of the toolbar, ahead of Batch."""
        self.basemap_combo = QComboBox()
        self.basemap_combo.addItems(self.BASEMAP_CHOICES)
        self.basemap_combo.setToolTip(
            "Map backdrop drawn beneath your data layers.\n"
            "'Offline (Natural Earth)' and any MBTiles archive work with no network; "
            "the named tile providers need the 'contextily' package and a connection."
        )

        # Offline archives are appended before the chooser so the chooser always
        # stays last, where a trailing action belongs.
        self._register_known_mbtiles()
        self.basemap_combo.addItem(self.LOAD_MBTILES_ITEM)

        # Connect only after populating so the initial fill doesn't trigger a
        # spurious tile fetch; the canvas already starts on "None".
        self._last_basemap_choice = "None"
        self.basemap_combo.currentTextChanged.connect(self._on_basemap_selected)

        # Inserted rather than appended: this runs after the toolbar is built,
        # because the combo needs the canvas, and the basemap belongs first.
        self.toolbar.add_leading_widget(QLabel(" Basemap: "))
        self.toolbar.add_leading_widget(self.basemap_combo)
        self.toolbar.add_leading_separator()

    def _setup_projection_selector(self):
        """Put a 'Projection' dropdown next to the basemap one.

        The entries come from the projection registry rather than a list kept
        here: a projection the canvas cannot build must not be offered, and one
        it can must not be unreachable.
        """
        self.projection_combo = QComboBox()
        self.projection_combo.addItems(projections.PROJECTION_CHOICES)
        self.projection_combo.setToolTip(
            "Map projection. Each one's parameters — central longitude, standard "
            "parallels, the polar cut-off — are derived from the extent of the "
            "loaded data; there is nothing to fill in.\n"
            + "\n".join(f"{name}: {projections.spec(name).summary}"
                        for name in projections.PROJECTION_CHOICES)
        )

        # Connected after populating, for the same reason as the basemap combo:
        # the initial fill must not trigger a rebuild of a canvas that is
        # already in the projection being selected.
        self.projection_combo.setCurrentText(self.geo_canvas.projection_name)
        self.projection_combo.currentTextChanged.connect(self._on_projection_selected)

        self.toolbar.add_leading_widget(QLabel(" Projection: "))
        self.toolbar.add_leading_widget(self.projection_combo)
        self.toolbar.add_leading_separator()

    def _on_projection_selected(self, name):
        """Route a selector change to the canvas, following any fallback.

        The canvas reports what it actually drew, which is not always what was
        asked for — a CRS that will not construct degrades to PlateCarree — and
        a selector still showing the request would be lying about the map.
        """
        drawn = self.geo_canvas.set_projection(name)
        if drawn and drawn != name:
            self._show_projection(drawn)

    def _show_projection(self, name):
        """Point the selector at a projection without asking for it again."""
        index = self.projection_combo.findText(name)
        if index < 0:
            return
        self.projection_combo.blockSignals(True)
        self.projection_combo.setCurrentIndex(index)
        self.projection_combo.blockSignals(False)

    def _register_known_mbtiles(self):
        """Populate the combo with every archive we already know about.

        Two sources: whatever sits in ~/.ncexplorer/basemaps, and whatever the
        user has opened before. Paths that have since been deleted are dropped
        rather than offered as entries that would fail on selection.
        """
        paths = []
        try:
            paths.extend(discover_mbtiles(self.MBTILES_DIRECTORY))
        except Exception as exc:
            logger.warning("Could not scan %s: %s", self.MBTILES_DIRECTORY, exc)

        try:
            stored = QSettings().value(self.MBTILES_SETTINGS_KEY, [], type=list) or []
            paths.extend(str(path) for path in stored)
        except Exception as exc:
            logger.warning("Could not read remembered MBTiles paths: %s", exc)

        seen = set()
        for path in paths:
            if path in seen or not os.path.exists(path):
                continue
            seen.add(path)
            try:
                label = self.geo_canvas.register_mbtiles(path)
            except Exception as exc:
                logger.warning("Could not register MBTiles %s: %s", path, exc)
                continue
            if self.basemap_combo.findText(label) < 0:
                self.basemap_combo.addItem(label)

    def _on_basemap_selected(self, text):
        """Route a selector change to the canvas, or to the file chooser."""
        if text == self.LOAD_MBTILES_ITEM:
            self._choose_mbtiles_file()
            return

        self._last_basemap_choice = text
        self.geo_canvas.set_basemap(text)

    def _choose_mbtiles_file(self):
        """Ask for a .mbtiles file, register it, and select it."""
        # Restore the previous entry first: whether the dialog is accepted or
        # cancelled, "Load MBTiles…" must not stay showing as if it were a map.
        def restore(label):
            self.basemap_combo.blockSignals(True)
            index = self.basemap_combo.findText(label)
            self.basemap_combo.setCurrentIndex(max(0, index))
            self.basemap_combo.blockSignals(False)

        start_dir = self.MBTILES_DIRECTORY if os.path.isdir(self.MBTILES_DIRECTORY) else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Open MBTiles basemap", start_dir, "MBTiles archives (*.mbtiles);;All files (*)"
        )
        if not path:
            restore(self._last_basemap_choice)
            return

        try:
            label = self.geo_canvas.register_mbtiles(path)
        except Exception as exc:
            logger.error("Could not register MBTiles %s: %s", path, exc, exc_info=True)
            self.handle_status_update(f"Could not use that MBTiles file: {exc}")
            restore(self._last_basemap_choice)
            return

        self._remember_mbtiles_path(path)

        if self.basemap_combo.findText(label) < 0:
            # Inserted above the chooser so it stays the last entry.
            self.basemap_combo.blockSignals(True)
            self.basemap_combo.insertItem(self.basemap_combo.count() - 1, label)
            self.basemap_combo.blockSignals(False)

        restore(label)
        self._last_basemap_choice = label
        self.geo_canvas.set_basemap(label)

    def _remember_mbtiles_path(self, path):
        """Add ``path`` to the remembered list so it returns after a restart."""
        try:
            settings = QSettings()
            stored = settings.value(self.MBTILES_SETTINGS_KEY, [], type=list) or []
            stored = [str(entry) for entry in stored]
            if path in stored:
                return
            stored.append(path)
            settings.setValue(self.MBTILES_SETTINGS_KEY, stored)
        except Exception as exc:
            logger.warning("Could not remember MBTiles path %s: %s", path, exc)

    # ------------------------------------------------------------------
    # Cursor readout and map overlays
    # ------------------------------------------------------------------
    #: Shown in the readout where there is no value to report.
    NO_VALUE = "—"

    def _setup_cursor_readout(self):
        """Add the permanent lat/lon/value label to the status bar.

        Deliberately a permanent widget rather than showMessage(): that call is
        transient (it expires after a few seconds) and is shared with a dozen
        other call sites, so a hover readout pushed through it would both
        flicker and stomp on real status messages.
        """
        self.cursor_readout = QLabel("")
        # Fixed-pitch font plus a fixed-width format string, so the label does
        # not jitter as the digits under the cursor change. The families are
        # listed explicitly because Qt's generic "Monospace" alias does not
        # exist on macOS and resolving it costs a slow font-database sweep.
        readout_font = QFont()
        readout_font.setFamilies(
            ["Menlo", "SF Mono", "Consolas", "DejaVu Sans Mono", "Courier New"]
        )
        readout_font.setStyleHint(QFont.StyleHint.Monospace)
        self.cursor_readout.setFont(readout_font)
        self.cursor_readout.setToolTip("Cursor position and the value beneath it")
        self.cursor_readout.setMinimumWidth(
            self.cursor_readout.fontMetrics().horizontalAdvance("0" * 46)
        )
        self.statusBar().addPermanentWidget(self.cursor_readout)

        self.geo_canvas.cursor_position_changed.connect(self.handle_cursor_position)
        self.geo_canvas.cursor_left.connect(self.clear_cursor_readout)

    def handle_cursor_position(self, lat, lon, value):
        """Display the position and data value under the cursor."""
        if value is None:
            shown = self.NO_VALUE
        else:
            # 6 significant figures: enough to tell 273.15 K from 273.1 K, and
            # still short enough that the widest case fits the fixed field.
            shown = f"{value:.6g}"
        self.cursor_readout.setText(
            f"lat {lat:>8.3f}°  lon {lon:>9.3f}°  {shown:>12s}"
        )

    def clear_cursor_readout(self):
        """Blank the readout when the pointer leaves the map."""
        self.cursor_readout.setText("")

    def toggle_colorbar(self, checked):
        """Show or hide the on-map colorbar."""
        self.geo_canvas.colorbar_manager.set_visible(checked)
        self.menu_bar.colorbar_action.setChecked(checked)

    def set_colorbar_position(self, position):
        """Move the colorbar to one of right/left/bottom/top."""
        self.geo_canvas.colorbar_manager.set_position(position)
        action = self.menu_bar.colorbar_position_actions.get(position)
        if action is not None:
            action.setChecked(True)

    def toggle_graticule(self, checked):
        """Show or hide the lat/lon graticule."""
        self.geo_canvas.set_graticule(checked)
        self.menu_bar.graticule_action.setChecked(checked)

    def toggle_scalebar(self, checked):
        """Show or hide the scale bar."""
        self.geo_canvas.scalebar_manager.set_visible(checked)
        self.menu_bar.scalebar_action.setChecked(checked)

    def handle_map_click(self, lat, lon):
        values = {}

        try:
            for layer_name, layer in self.geo_canvas.layers.items():

                # Only process NetCDF layers
                if layer.get('type') != 'netcdf':
                    continue

                extent = layer.get('bounds')  # [lon_min, lon_max, lat_min, lat_max]

                if not extent:
                    continue

                lon_min, lon_max, lat_min, lat_max = extent

                # Check if click inside bounds
                if not (lon_min <= lon <= lon_max and lat_min <= lat <= lat_max):
                    continue

                data = layer.get('artist').get_array()

                if data is None:
                    continue

                ny, nx = data.shape

                # Convert lat/lon → pixel index
                x_idx = int((lon - lon_min) / (lon_max - lon_min) * (nx - 1))
                y_idx = int((lat - lat_min) / (lat_max - lat_min) * (ny - 1))

                # Clamp indices (important)
                x_idx = max(0, min(nx - 1, x_idx))
                y_idx = max(0, min(ny - 1, y_idx))

                value = data[y_idx, x_idx]

                # Handle NaN
                if hasattr(value, 'item'):
                    value = value.item()

                values[layer_name] = value

        except Exception as e:
            logger.error("Could not read layer value at click position: %s", e, exc_info=True)

        # Show output
        self.statusBar().showMessage(
            f"Clicked at: {lat:.4f}, {lon:.4f} | Values: {values}"
        )

    def handle_layer_added(self, layer_name):
        """Enhanced layer addition handler with auto-fit"""
        layer_prop = self.geo_canvas.property_manager.get_layer_property(layer_name)
        self.current_layer = layer_name

        # The colorbar follows the active layer (it falls back to the topmost
        # visible raster if this one turns out not to have a colour scale).
        self.geo_canvas.colorbar_manager.set_target_layer(layer_name)

        # Update the layer manager using the new add_layer_to_list method
        if hasattr(self, 'layer_manager'):
            self.layer_manager.add_layer_to_list(layer_name)

        # Enhanced status message with layer extent info
        if layer_prop and layer_prop.dimensions.extent:
            extent = layer_prop.dimensions.extent
            extent_str = f"[{extent[0]:.2f}, {extent[1]:.2f}, {extent[2]:.2f}, {extent[3]:.2f}]"
            self.statusBar().showMessage(
                f"Layer '{layer_name}' added and fitted to extent {extent_str}", 5000
            )
        else:
            self.statusBar().showMessage(f"Layer '{layer_name}' added successfully", 3000)

    def handle_layer_removed(self, layer_name):
        """Handle layer removal with proper cleanup"""
        try:
            # Remove from layer manager widget
            if hasattr(self, 'layer_manager'):
                self.layer_manager.remove_layer_from_list(layer_name)

            # Clear current layer if it was removed
            if self.current_layer == layer_name:
                self.current_layer = None
                if hasattr(self, 'property_editor'):
                    self.property_editor.clear_editor()
                if hasattr(self, 'property_dock'):
                    self.property_dock.hide()

            # Update status
            self.statusBar().showMessage(f"Layer '{layer_name}' removed", 2000)
            logger.info("Layer '%s' removed successfully", layer_name)

        except Exception as e:
            error_msg = f"Error removing layer: {str(e)}"
            logger.error(error_msg)
            self.statusBar().showMessage(error_msg, 5000)

    def handle_file_loaded(self, filepath, file_type):
        """Handle successful file loading"""
        filename = os.path.basename(filepath)
        self.statusBar().showMessage(f"Loaded {file_type} file: {filename}", 3000)
        # The one point every successful load passes through, whichever entry
        # point started it — the Open dialog, the file explorer, a drop, Open
        # Recent itself, or the visualisation of an operator's output.
        self.recent_files.add(filepath)

    def handle_loading_error(self, operation, error_message):
        """Handle loading errors"""
        self.statusBar().showMessage(f"Error in {operation}", 5000)
        QMessageBox.critical(self, f"{operation} Error", error_message)

    def handle_progress_update(self, progress):
        """Handle progress updates"""
        if progress > 0:
            self.statusBar().showMessage(f"Loading... {progress}%")
        else:
            self.statusBar().clearMessage()

    def handle_status_update(self, message):
        """Handle status updates"""
        self.statusBar().showMessage(message, 3000)

    def zoom_to_layer(self, layer_name):
        """Public method to zoom to a specific layer"""
        try:
            self.geo_canvas.zoom_to_layer(layer_name)
            self.statusBar().showMessage(f"Zoomed to layer: {layer_name}", 2000)
        except Exception as e:
            self.statusBar().showMessage(f"Failed to zoom to layer: {layer_name}", 3000)

    # ------------------------------------------------------------------
    # Navigation callbacks named by the shortcut registry (gui/shortcuts.py)
    # ------------------------------------------------------------------
    PAN_STEP = 0.15  # fraction of the visible width/height per key press

    def zoom_in_map(self):
        self.geo_canvas.zoom_in()

    def zoom_out_map(self):
        self.geo_canvas.zoom_out()

    def zoom_full_extent_map(self):
        self.geo_canvas.zoom_full_extent()

    def zoom_previous_extent(self):
        self.geo_canvas.zoom_previous()

    def zoom_to_active_layer(self):
        if not self.current_layer:
            self.statusBar().showMessage("No active layer to zoom to", 3000)
            return
        self.zoom_to_layer(self.current_layer)

    def pan_map_left(self):
        self.geo_canvas.pan_by(-self.PAN_STEP, 0)

    def pan_map_right(self):
        self.geo_canvas.pan_by(self.PAN_STEP, 0)

    def pan_map_up(self):
        self.geo_canvas.pan_by(0, self.PAN_STEP)

    def pan_map_down(self):
        self.geo_canvas.pan_by(0, -self.PAN_STEP)

    def show_shortcuts_dialog(self):
        """Show the keyboard-shortcut cheat sheet built from the registry."""
        ShortcutCheatSheet(self).exec()

    def show_command_palette(self):
        """Open the fuzzy operator finder (Ctrl+K).

        Built once and reused: indexing 943 operators is cheap but not free, and
        keeping the instance also keeps the query the user last typed.
        """
        if self._command_palette is None:
            self._command_palette = CommandPalette(self)
        self._command_palette.search.selectAll()
        self._command_palette.show()
        self._command_palette.raise_()
        self._command_palette.search.setFocus()

    def visualize_file(self, filepath):
        """Load a file onto the canvas. True once it is displayed, False if not.

        load_file's own @error_handler already turns a failure into a falsy
        return, so the guard here is only for something going wrong above it.
        """
        try:
            return bool(self.geo_canvas.load_file(filepath))
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to visualize file: {str(e)}")
            return False

    def visualize_output(self):
        """Visualize the output file"""
        if hasattr(self, 'current_output_file') and self.current_output_file:
            success = self.visualize_file(self.current_output_file)
            if success:
                self.property_dock.show()

    def clear_output_log(self):
        """Clear the output console with a confirmation and backup option"""
        # Check if there's content to clear
        if not self.output_console.toPlainText().strip():
            QMessageBox.information(self, "Clear Output", "Output console is already empty.")
            return

        # Create a custom message box with options
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Clear Output Console")
        msg_box.setText("Are you sure you want to clear the output console?")
        msg_box.setInformativeText("This action cannot be undone.")
        msg_box.setIcon(QMessageBox.Icon.Question)

        # Add custom buttons
        clear_btn = msg_box.addButton("Clear", QMessageBox.ButtonRole.AcceptRole)
        save_and_clear_btn = msg_box.addButton("Save & Clear", QMessageBox.ButtonRole.ActionRole)
        cancel_btn = msg_box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        msg_box.setDefaultButton(cancel_btn)
        msg_box.exec()

        clicked_button = msg_box.clickedButton()
        if clicked_button == clear_btn:
            # Simple clear
            self.output_console.clear()
            self.output_console.append("🗑️ Output console cleared.")
            self.statusBar().showMessage("Output console cleared", 2000)
        elif clicked_button == save_and_clear_btn:
            # Save to a file and then clear
            self.save_output_to_file()
            self.output_console.clear()
            self.output_console.append("💾 Output saved and console cleared.")
            self.statusBar().showMessage("Output saved and console cleared", 2000)

    def save_output_to_file(self):
        """Save the current output to a text file"""
        if not self.output_console.toPlainText().strip():
            return

        import datetime
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        default_filename = f"NCExplorer_output_{timestamp}.txt"

        options = QFileDialog.Option.DontUseNativeDialog
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Output Log",
            default_filename,
            "Text Files (*.txt);;Log Files (*.log);;All Files (*)",
            options=options
        )

        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(f"NCExplorer Toolkit Output Log\n")
                    f.write(f"Generated: {datetime.datetime.now().isoformat()}\n")
                    f.write("=" * 50 + "\n\n")
                    f.write(self.output_console.toPlainText())
                self.statusBar().showMessage(f"Output saved to {file_path}", 3000)
            except Exception as e:
                QMessageBox.critical(self, "Save Error", f"Failed to save output log:\n{str(e)}")

    def save_output(self):
        """Save the current NCExplorer operation output to a user-selected file"""
        # Check if there's an output file to save
        if not hasattr(self, 'current_output_file') or not self.current_output_file:
            QMessageBox.warning(self, "No Output", "No output file available to save.")
            return

        # Check if the output file actually exists
        if not os.path.exists(self.current_output_file):
            QMessageBox.warning(self, "File Not Found",
                                f"Output file not found: {self.current_output_file}")
            return

        # Determine appropriate file extension based on current output
        current_ext = os.path.splitext(self.current_output_file)[1]
        if not current_ext:
            current_ext = '.nc'  # Default to NetCDF

        # Get the base name for the suggested filename
        if self.current_operator:
            suggested_name = f"{self.current_operator}_output{current_ext}"
        else:
            suggested_name = f"NCExplorer_output{current_ext}"

        # Create a file dialogs with appropriate filters
        file_filters = [
            "NetCDF Files (*.nc)",
            "GRIB Files (*.grb *.grib *.grb2)",
            "Text Files (*.txt)",
            "All Files (*)"
        ]

        options = QFileDialog.Option.DontUseNativeDialog
        save_path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Save NCExplorer Output",
            suggested_name,
            ";;".join(file_filters),
            options=options
        )

        if not save_path:
            return  # User cancelled

        try:
            # Copy the temporary output file to the selected location
            import shutil
            shutil.copy2(self.current_output_file, save_path)

            # Update the console with a success message
            self.output_console.append(f"✓ Output saved to: {save_path}")

            # Update the current output file path
            self.current_output_file = save_path

            # Show a success message
            QMessageBox.information(self, "Save Successful",
                                    f"Output saved successfully to:\n{save_path}")

        except Exception as e:
            # Handle any errors during file copying
            error_msg = f"Failed to save output file: {str(e)}"
            self.output_console.append(f"✗ {error_msg}")
            QMessageBox.critical(self, "Save Error", error_msg)

    def on_property_changed(self, property_name, value):
        """Handle individual property changes.

        Most properties are a value the property manager holds and the canvas
        reads back, so writing it there is the whole change. The NetCDF
        variable is not: the choice lives in two places — the property and the
        canvas layer record every reader outside the canvas consults — and
        changing it has to redraw the layer and announce itself, which is what
        ``set_netcdf_variable`` exists to do. Writing only the property left
        the picker updating a field nobody was looking at: the map, the
        statistics panel and the plot all carried on showing the old variable.

        The edit goes to the layer the editor is *showing*, which is not always
        ``current_layer``: loading a file makes the new layer current while the
        panel still shows the old one, and every edit made after that was
        landing on the wrong layer.
        """
        layer_name = None
        if hasattr(self, 'property_editor'):
            layer_name = self.property_editor.current_layer_name
        layer_name = layer_name or self.current_layer
        if not layer_name:
            return

        if (property_name == 'netcdf.current_variable'
                and layer_name in self.geo_canvas.layers):
            self.geo_canvas.set_netcdf_variable(layer_name, value)
        else:
            # Update the layer property using the new property manager
            self.geo_canvas.property_manager.update_property(
                layer_name, property_name, value
            )

        # The colour scale the colorbar describes may have just changed.
        if property_name.startswith('style.'):
            self.geo_canvas.colorbar_manager.refresh()

        if hasattr(self, 'property_editor'):
            self.property_editor.refresh_current_layer()
        self._mark_project_dirty()

    def on_layer_updated(self):
        """Handle layer update completion"""
        self.geo_canvas.draw()

    def handle_layer_visibility_changed(self, layer_name, visible):
        """Handle layer visibility changes from the layer manager widget"""
        try:
            # Use the consolidated canvas structure to toggle layer visibility
            if hasattr(self, 'geo_canvas') and layer_name in self.geo_canvas.layers:
                self.geo_canvas.toggle_layer(layer_name, visible)

                # Update status bar
                visibility_status = "visible" if visible else "hidden"
                self.statusBar().showMessage(
                    f"Layer '{layer_name}' is now {visibility_status}",
                    2000
                )

                logger.debug("Layer visibility changed: %s -> %s", layer_name, visible)
            else:
                logger.warning("Layer '%s' not found in canvas", layer_name)

        except Exception as e:
            error_msg = f"Failed to change layer visibility: {str(e)}"
            logger.error(error_msg)
            self.statusBar().showMessage(error_msg, 5000)

    def handle_layer_order_changed(self, layer_names):
        """Restack the map to match the layer manager's list."""
        try:
            self.geo_canvas.set_layer_order(list(layer_names))
            # Which raster is topmost may have changed, and that is the one the
            # colorbar describes.
            self.geo_canvas.colorbar_manager.refresh()
            self.geo_canvas.draw_idle()
            self._mark_project_dirty()
            self.statusBar().showMessage("Layer order updated", 2000)
        except Exception as e:
            error_msg = f"Failed to restack the layers: {str(e)}"
            logger.error(error_msg, exc_info=True)
            self.statusBar().showMessage(error_msg, 5000)

    def handle_layer_properties(self, layer_name):
        """Handle explicit property request from layer manager"""
        self.current_layer = layer_name
        self.geo_canvas.colorbar_manager.set_target_layer(layer_name)

        # Load layer properties into the property editor
        if hasattr(self, 'property_editor'):
            self.property_editor.load_layer_properties(layer_name)

        # Show the property dock
        self.property_dock.show()
        self.property_dock.raise_()

        # Update status
        self.statusBar().showMessage(f"Showing properties for layer: {layer_name}")

    def load_file_from_explorer(self, file_path):
        """Load file when double-clicked in file explorer"""
        try:
            success = self.visualize_file(file_path)
            if success:
                self.statusBar().showMessage(f"Loaded: {os.path.basename(file_path)}", 3000)
            else:
                self.statusBar().showMessage(f"Failed to load: {os.path.basename(file_path)}", 5000)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load file: {str(e)}")

    def preview_file_info(self, file_path):
        """Show file information in the status bar when selected"""
        if os.path.isfile(file_path):
            try:
                size = os.path.getsize(file_path)
                size_str = self.file_explorer.format_file_size(size)
                self.statusBar().showMessage(f"Selected: {os.path.basename(file_path)} ({size_str})")
            except OSError:
                self.statusBar().showMessage(f"Selected: {os.path.basename(file_path)}")

    def show_operator_parameters(self, operator):
        """Show parameter input fields for the selected operator"""
        self.current_operator = operator
        self._output_line_edit = None   # reset for each new operator form
        self.param_dock.setWindowTitle(f"Parameters: {operator}")
        self.save_btn.setEnabled(False)

        # Clear existing parameter widgets
        while self.params_layout.rowCount() > 0:
            self.params_layout.removeRow(0)
        self.parameter_widgets.clear()

        # Get operator syntax from NCExplorer reference
        syntax = self.get_operator_syntax(operator)
        description = self.get_operator_description(operator)
        self.output_console.append(
            f"Selected operator: {operator}\nDescription: {description}\nSyntax: {syntax}\n"
        )

        summary_group = QGroupBox("Operation Summary")
        summary_layout = QVBoxLayout(summary_group)

        description_label = QLabel(description)
        description_label.setWordWrap(True)
        summary_layout.addWidget(description_label)

        syntax_label = QLabel(f"Syntax: {syntax}")
        syntax_label.setWordWrap(True)
        syntax_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        summary_layout.addWidget(syntax_label)

        self.params_layout.addRow(summary_group)

        # Extract parameters from syntax
        params = self.parse_parameters(operator, syntax)

        op_nin, op_nout = _operator_arity(operator)

        # Create input fields for each parameter
        multi_file_added = False  # guard so MultiFileInputWidget is only added once

        # ``parse_parameters`` returns widget shapes, not schema entries, so the
        # help text has to be looked up alongside. It is where the traps live:
        # the temperature indices read their field as Kelvin while the threshold
        # typed into this very box is in degrees Celsius, and the model builder
        # was the only surface saying so.
        help_for = self._parameter_help(operator)

        # Same arrangement, same reason, for the *format* each file-valued
        # parameter takes. Keyed by label because that is what the rows are
        # built from — see ``_parameter_file_kinds``.
        file_kind_for = self._parameter_file_kinds(operator)

        # What CDO reads in this operator's own input slots. A single answer
        # rather than one per slot: no operator in the catalog mixes formats
        # across its inputs, and the rows below are built without a slot index.
        input_kind = input_file_kind(operator)

        for param in params:
            if len(param) == 4:
                param_type, label, placeholder, choices = param
            else:
                param_type, label, placeholder = param
                choices = ()

            param_label = QLabel(label)

            # Before the "Input"/"Output" tests below, which key off substrings
            # of the caption: these two rows are files but neither is one of the
            # operator's arguments, and letting them fall through to the string
            # branch would have drawn a bare text box with no way to browse.
            if label in (STDIN_FILE_LABEL, STDOUT_FILE_LABEL):
                widget = QWidget()
                layout = QHBoxLayout(widget)
                layout.setContentsMargins(0, 0, 0, 0)
                line_edit = QLineEdit()
                line_edit.setPlaceholderText(placeholder)
                browse_btn = QPushButton("Browse")
                saving = label == STDOUT_FILE_LABEL
                browse_btn.clicked.connect(
                    lambda _, le=line_edit, save=saving:
                    self._browse_redirection(le, saving=save))
                layout.addWidget(line_edit)
                layout.addWidget(browse_btn)
                input_widget = widget

            elif param_type == "file" and "Input" in label:
                # Multi-input operators → one shared MultiFileInputWidget
                if op_nin > 1 or op_nin == -1:
                    if not multi_file_added:
                        input_widget = MultiFileInputWidget(
                            nin=op_nin, file_kind=input_kind)
                        self.params_layout.addRow(QLabel("Input Files"), input_widget)
                        self.parameter_widgets["multi_file_widget"] = input_widget
                        multi_file_added = True
                    continue  # skip ifile2, ifile3 … rows
                else:
                    # Single input file — original browse widget
                    widget = QWidget()
                    layout = QHBoxLayout(widget)
                    layout.setContentsMargins(0, 0, 0, 0)
                    line_edit = QLineEdit()
                    line_edit.setPlaceholderText(placeholder)
                    browse_btn = QPushButton("Browse")
                    browse_btn.setToolTip(ft.summary(input_kind))
                    browse_btn.clicked.connect(
                        lambda _, le=line_edit, k=input_kind:
                        self.browse_file(le, file_kind=k))
                    layout.addWidget(line_edit)
                    layout.addWidget(browse_btn)
                    input_widget = widget

            elif param_type == "file" and "Output" in label:
                # Output file — browse widget; capture reference for auto-fill
                widget = QWidget()
                layout = QHBoxLayout(widget)
                layout.setContentsMargins(0, 0, 0, 0)
                line_edit = QLineEdit()
                line_edit.setPlaceholderText(placeholder)
                browse_btn = QPushButton("Browse")
                browse_btn.clicked.connect(lambda _, le=line_edit: self.browse_file(le))
                layout.addWidget(line_edit)
                layout.addWidget(browse_btn)
                input_widget = widget
                self._output_line_edit = line_edit   # ← kept for auto-fill

            elif param_type == "checkbox":
                # A CDO BOOL. Rendered as a checkbox rather than a text box
                # because the value is not free text: CDO takes 1/0/true/false
                # and refuses anything else outright. ``parameter_tokens``
                # turns what this reports into the spelling the operator wants,
                # which for every one of them today is ``name=true``.
                input_widget = QCheckBox()
                input_widget.setToolTip(placeholder or label)

            elif param_type == "integer":
                input_widget = QLineEdit()
                input_widget.setPlaceholderText(placeholder)
                input_widget.setValidator(QIntValidator())

            elif param_type == "float":
                input_widget = QLineEdit()
                input_widget.setPlaceholderText(placeholder)
                input_widget.setValidator(QDoubleValidator())

            elif param_type == "paramfile":
                # Parameter that takes a file path (e.g. setpartab,table).
                #
                # The chooser is the parameter's own, not the operator's: two
                # file parameters of one operator can want unrelated formats.
                # ``remap`` is the clearest — a target grid beside a SCRIP
                # NetCDF weights file — and ``remapeta`` the most costly, since
                # its ASCII vct and its data-file oro sit next to each other and
                # a filter that offered NetCDF for both pointed at the wrong one.
                param_kind = file_kind_for.get(label, ft.ANY)
                widget = QWidget()
                layout = QHBoxLayout(widget)
                layout.setContentsMargins(0, 0, 0, 0)
                line_edit = QLineEdit()
                line_edit.setPlaceholderText(placeholder)
                browse_btn = QPushButton("Browse")
                browse_btn.setToolTip(ft.summary(param_kind))
                browse_btn.clicked.connect(
                    lambda _, le=line_edit, k=param_kind:
                    self.browse_file(le, file_kind=k))
                layout.addWidget(line_edit)
                layout.addWidget(browse_btn)
                input_widget = widget

            elif param_type == "paramgrid":
                # Grid descriptor: file path OR a preset grid name.
                #
                # Usually the grid chooser — a CDO description file, a SCRIP
                # grid in NetCDF, or a data file whose grid is copied, which is
                # the three ways manual §1.5.2 says a grid may be given. Read
                # from the parameter rather than assumed from the widget,
                # because ``setgridarea`` and ``setgridmask`` use this same
                # widget for something that is not a grid description at all:
                # "gridarea [STRING] Data file, the first field is used as grid
                # cell area". Their preset dropdown is beside the point; their
                # chooser has to offer datasets.
                param_kind = file_kind_for.get(label, ft.GRID)
                try:
                    from ..core.categories import GRID_PRESETS
                except ImportError:
                    GRID_PRESETS = (
                        "t63grid", "t106grid", "r180x90", "r360x180", "r720x360",
                    )
                widget = QWidget()
                layout = QHBoxLayout(widget)
                layout.setContentsMargins(0, 0, 0, 0)
                line_edit = QLineEdit()
                line_edit.setPlaceholderText(placeholder or "grid file or preset")
                preset_combo = QComboBox()
                preset_combo.addItem("")
                for preset in GRID_PRESETS:
                    preset_combo.addItem(preset)
                preset_combo.currentTextChanged.connect(
                    lambda text, le=line_edit: le.setText(text) if text else None
                )
                browse_btn = QPushButton("Browse")
                browse_btn.setToolTip(ft.summary(param_kind))
                browse_btn.clicked.connect(
                    lambda _, le=line_edit, k=param_kind:
                    self.browse_file(le, file_kind=k))
                layout.addWidget(line_edit)
                layout.addWidget(preset_combo)
                layout.addWidget(browse_btn)
                input_widget = widget

            elif param_type == "expression":
                # The Expr language: a line edit for the value the command
                # actually carries, plus the editor that is the only place the
                # language is discoverable. Kept as a line edit underneath so
                # somebody who already knows what to type still can.
                widget = QWidget()
                layout = QHBoxLayout(widget)
                layout.setContentsMargins(0, 0, 0, 0)
                line_edit = QLineEdit()
                line_edit.setPlaceholderText(placeholder)
                edit_btn = QPushButton("Edit…")
                edit_btn.setToolTip(
                    "Open the expression editor: the input file's variables, "
                    "the function reference, and a syntax check")
                edit_btn.clicked.connect(
                    lambda _checked=False, le=line_edit:
                    self.open_expression_editor(le))
                layout.addWidget(line_edit)
                layout.addWidget(edit_btn)
                input_widget = widget

            elif param_type == "select":
                input_widget = QComboBox()
                input_widget.addItem("")
                for choice in choices:
                    input_widget.addItem(str(choice))

            elif param_type == "multiselect":
                input_widget = MultiSelectEdit(choices, placeholder=placeholder)

            else:  # string
                input_widget = QLineEdit()
                input_widget.setPlaceholderText(placeholder)

            hint = help_for.get(label, "")
            if hint:
                param_label.setToolTip(hint)
                input_widget.setToolTip(hint)

            self.params_layout.addRow(param_label, input_widget)
            self.parameter_widgets[label] = input_widget

        # ── Output path auto-fill ─────────────────────────────────────────
        # Pre-fill from the currently active layer (if any) so the output
        # field is never blank when the form opens.
        if self._output_line_edit is not None:
            initial_input = ""
            if (self.current_layer
                    and hasattr(self, 'geo_canvas')
                    and self.current_layer in self.geo_canvas.layers):
                initial_input = self.geo_canvas.layers[self.current_layer].get('filepath', '')
            if initial_input and initial_input != 'N/A' and os.path.exists(initial_input):
                suggestion = self._suggest_output_path(initial_input, operator)
                if suggestion:
                    self._output_line_edit.setText(suggestion)
                    self._output_line_edit.setProperty('auto_filled', True)

        # Connect single-file input QLineEdits so typing/browsing auto-updates
        # the output suggestion as long as the field hasn't been manually edited.
        for _label, _widget in self.parameter_widgets.items():
            if "Input File" in _label and _label != "multi_file_widget":
                for _child in _widget.findChildren(QLineEdit):
                    _child.textChanged.connect(self._on_input_path_changed)
                    break

        # Connect MultiFileInputWidget row changes
        if "multi_file_widget" in self.parameter_widgets:
            _mfw = self.parameter_widgets["multi_file_widget"]
            _mfw.list_widget.model().rowsInserted.connect(self._on_multi_input_changed)
            _mfw.list_widget.model().rowsRemoved.connect(self._on_multi_input_changed)

        # Show the dock widget
        self.param_dock.show()

    @staticmethod
    def get_operator_description(operator: str) -> str:
        """Return a human-friendly description for a CDO operator.

        Priority:
          1. ``OperatorSpec.description`` — the catalog's line plus everything
             ``categories._describe`` appends to it.
          2. Category-level fallback sentence.
          3. Generic fallback.

        Read from the schema rather than straight from ``CDO_OPERATORS``, which
        is what this used to do. The catalog line is a title ("Multi-year
        monthly arithmetic") and the schema is where the facts that a title
        omits are attached — which infile2 a ``*arith`` operator actually wants,
        that Arith broadcasts a one-timestep file over the other, how an ECA
        index dates its output. The model builder had all of that and this form
        had none of it, for no better reason than that the two read different
        dictionaries.
        """
        # 1. The schema's description ----------------------------------------
        try:
            from ..core.categories import OPERATOR_SCHEMA
        except ImportError:
            from ncexplorer_toolkit.core.categories import OPERATOR_SCHEMA

        spec = OPERATOR_SCHEMA.get(operator)
        if spec is not None and len(spec.description) >= 5:
            return spec.description

        # 2. Category-level fallback -----------------------------------------
        _CATEGORY_FALLBACKS = {
            NCExplorerCategory.INFORMATION:
                "Reads the input data and prints information or diagnostics "
                "without creating a new output file.",
            NCExplorerCategory.FILE_OPERATIONS:
                "Copies, merges, splits, or reorganises files into a new output dataset.",
            NCExplorerCategory.SELECTION:
                "Selects a subset of variables, levels, dates, timesteps, or regions "
                "from the input data.",
            NCExplorerCategory.CONDITIONAL_SELECTION:
                "Filters or selects data conditionally using another dataset or a constant value.",
            NCExplorerCategory.COMPARISON:
                "Compares datasets or values and writes the comparison result.",
            NCExplorerCategory.MODIFICATION:
                "Changes metadata, coordinates, masks, or values while preserving "
                "the overall dataset structure.",
            NCExplorerCategory.ARITHMETIC:
                "Applies arithmetic or mathematical expressions to transform the data values.",
            NCExplorerCategory.STATISTICAL_VALUES:
                "Computes aggregated statistics across space, time, levels, or ensembles.",
            NCExplorerCategory.CORRELATION:
                "Measures how two fields vary together, over the map or over "
                "time. Takes two input files rather than one.",
            NCExplorerCategory.REGRESSION:
                "Computes regression-based relationships from the input data.",
            NCExplorerCategory.INTERPOLATION:
                "Interpolates the dataset onto a different grid, level set, or sampling geometry.",
            NCExplorerCategory.TRANSFORMATION:
                "Transforms the structure or spectral representation of the dataset.",
            NCExplorerCategory.IMPORT_EXPORT:
                "Reads a dataset in from outside CDO's own formats, or writes "
                "one out as text — to a file, a table or a GMT plot.",
            NCExplorerCategory.EOF:
                "Decomposes an anomaly field into empirical orthogonal functions, "
                "or projects it onto ones already computed.",
            NCExplorerCategory.MISCELLANEOUS:
                "Performs a utility operation that does not fit the main processing groups.",
            NCExplorerCategory.ECA_INDICES:
                "Computes a climate index or extreme-event indicator from the input data.",
        }
        for category, ops in OPERATOR_CATEGORIES.items():
            if operator in ops:
                fallback = _CATEGORY_FALLBACKS.get(category)
                if fallback:
                    return fallback

        # 3. Generic fallback ------------------------------------------------
        return "Runs the selected operator on the provided inputs and writes the requested output."

    @staticmethod
    def get_operator_syntax(operator):
        """The ``ifile ofile ...`` usage hint shown above the parameter form.

        Derived from the operator schema rather than a table kept here. The
        table this replaces listed 386 of the installed 943 operators, so every
        operator the menus gained showed the generic fallback, and it disagreed
        with the installed CDO on eight it did list.
        """
        return operator_syntax(operator)

    @staticmethod
    def get_extra_parameters_for_operator(operator: str):
        """The non-file parameters that go before ifile/ofile, from the schema.

        ``(name, ui_type, label, placeholder)``, plus a fifth ``choices`` entry
        for a ``select``. The only source is ``OPERATOR_SCHEMA``.

        There used to be a second, hand-written map here covering 129
        operators, consulted whenever the schema had nothing. By the time the
        schema covered every one of those 129 it was unreachable, and it had
        drifted while nobody could see it: it offered ``eca_rx1day`` a
        parameter called ``mode`` that CDO 2.6 answers with "Argument parse
        error!", and it was a parameter short on both ``setreftime`` and
        ``random``. A fallback that cannot be reached cannot be tested, and one
        that can be reached is a fourth place for the truth to live —
        ``audit_operator_surfaces.py`` now fails if the surfaces disagree about
        any operator's parameters, which is the check that keeps this honest.
        """
        try:
            from ..core.categories import OPERATOR_SCHEMA
        except ImportError:
            from ncexplorer_toolkit.core.categories import OPERATOR_SCHEMA

        spec = OPERATOR_SCHEMA.get(operator)
        if spec is None:
            return []

        kind_map = {
            "int": "integer",
            "float": "float",
            "string": "string",
            "bool": "checkbox",
            "file": "paramfile",
            "grid": "paramgrid",
            "select": "select",
            "multiselect": "multiselect",
            "expression": "expression",
        }
        out = []
        for p in spec.params:
            ui_type = kind_map.get(p.kind, "string")
            entry = (p.name, ui_type, p.label, p.placeholder)
            if p.kind in ("select", "multiselect"):
                entry = (p.name, ui_type, p.label, p.placeholder, tuple(p.choices))
            out.append(entry)
        return out

    @staticmethod
    def parse_parameters(operator, syntax):
        """The form's rows for one operator: its parameters, then its files.

        The file rows come from the schema's ``(nin, nout)``. This is the row
        that decides whether a two-input operator gets a two-file widget at all,
        so reading it from anywhere other than the schema is what made
        ``timcor`` unrunnable; see :func:`_operator_arity`.
        """
        nin, nout = _operator_arity(operator)
        params = []

        # Extra non‑file parameters that come before files in CDO
        extra_params = NCExplorerOperatorGUI.get_extra_parameters_for_operator(operator)
        for entry in extra_params:
            # entry is (name, ptype, label, placeholder) or
            # (name, ptype, label, placeholder, choices) for select.
            name = entry[0]
            ptype = entry[1]
            label = entry[2]
            placeholder = entry[3]
            choices = entry[4] if len(entry) > 4 else ()
            # Preserve richer widget kinds for the builder; legacy kinds
            # ("integer", "float", "string") keep their current behavior.
            # Anything not listed degrades to a plain text field rather than
            # reaching the form as a kind it has no branch for.
            passthrough = {"integer", "float", "string", "checkbox",
                           "paramfile", "paramgrid", "select", "multiselect",
                           "expression"}
            ui_type = ptype if ptype in passthrough else "string"
            if ui_type in ("select", "multiselect"):
                params.append((ui_type, label, placeholder, choices))
            else:
                params.append((ui_type, label, placeholder))

        # Add input file parameters based on nin
        if nin == -1:
            # Variable inputs - show 1 required + 2 optional
            params.append(("file", "Input File 1", "Select first input file"))
            params.append(("file", "Input File 2", "Select second input file (optional)"))
            params.append(("file", "Input File 3", "Select third input file (optional)"))
        elif nin == 0:
            # No input files *named on the command line* — which is not the
            # same as no input, and treating it as the same is why ``input``,
            # ``inputsrv`` and ``inputext`` could not be run from this form at
            # all. All three take their field data on standard input, so the
            # form drew no way to supply data and the run then blocked until the
            # timeout waiting for a terminal the user cannot type into.
            #
            # A file whose *contents* are piped in, rather than a path CDO is
            # told about: that is what the operators read, and it is what CDO's
            # own documentation shows (``cdo input,r4x2 out.nc < data.txt``).
            # Routed to ``OperatorRequest.stdin_file``, never to ``input_files``
            # — the engine validates that list against nin == 0 and would
            # rightly refuse a path in it.
            #
            # Keyed off the schema's module rather than off these three names;
            # see ``categories.reads_stdin``.
            if _reads_stdin(operator):
                params.append(
                    ("file", STDIN_FILE_LABEL,
                     "Text file of values to feed the operator"))
        else:
            # Fixed number of inputs
            for i in range(nin):
                params.append(("file", f"Input File {i + 1}", f"Select input file {i + 1}"))

        # The environment variables this operator reads, before the files. They
        # are declared on the schema and rendered with the widget kind they
        # name, so no new widget kind exists for them — a select is a select
        # whether it spells a CDO parameter or a CDO environment variable.
        #
        # Collected separately from ``extra_args`` in ``execute_operation``:
        # ``params`` is positional and becomes the ``op,a,b`` token, and an
        # environment variable is not an argument. The label carries the "Env: "
        # prefix so the two cannot be confused in the widget dictionary either,
        # which is what ``_collect_environment`` keys on.
        for variable in _operator_environment(operator):
            label = f"{ENV_LABEL_PREFIX}{variable.label or variable.name}"
            placeholder = f"default {variable.default}" if variable.default else ""
            if variable.kind == "select":
                # "" first, so the default is what an untouched form submits and
                # nothing is put in the environment unless it was chosen.
                params.append(("select", label, placeholder,
                               ("", *variable.choices)))
            else:
                kind = {"int": "integer", "float": "float"}.get(
                    variable.kind, "string")
                params.append((kind, label, placeholder))

        # Add output parameters based on nout
        if nout == 1:
            # ``nout == 1`` is not always one file. The Magics six land here and
            # what they take is an *obase*: CDO appends ``_<variable>.<device>``
            # or ``.<device>`` to it and creates nothing at the path as typed.
            # The placeholder says so, because the mistake it heads off — typing
            # ``plot.ps`` and getting ``plot.ps_tas.ps`` — is invisible until
            # the user goes looking in the folder.
            #
            # Still a ``file`` row rather than the ``string`` row ``nout == -1``
            # uses, deliberately: the browse button is what lets a user choose
            # *where* the plots go, and the row type is also how
            # ``operator_lab.surfaces`` recovers this form's output arity — a
            # string row here would make the surface audit read the operator as
            # writing nothing. The extension coercion that a file row normally
            # implies is suppressed for these in ``_browse_file`` and in
            # ``_ensure_output_extension``, which is where it actually happens.
            hint = expected_plot_files(operator, "plot")
            params.append(("file", "Output File", hint or "Select output file"))
        elif nout == -1:
            params.append(("string", "Output Prefix", "Enter output file prefix"))
        elif nout > 1:
            # The multi-output operators — eof and its seven relatives,
            # complextopol, complextorect, mrotuv, samplegridicon: eleven in the
            # catalog, all (1|2). There was no branch for them here at all, so
            # they drew *no* output row, and ``execute_operation`` then refused
            # them with "Unsupported operator signature: (1|2)". Unreachable
            # from this panel for the same reason the 38 two-input operators
            # were: the form could not express their shape.
            #
            # Found by the arity check in ``audit_operator_surfaces.py`` rather
            # than by hand, which is the argument for that check: the model
            # builder had these right all along and nothing compared the two.
            #
            # Captioned from the schema, not "Output File 1"/"Output File 2".
            # Those two rows are an eigenvalue spectrum on a 1x1 grid and a
            # stack of maps on the data grid, and a user who cannot tell which
            # is which from the form has to run the operator to find out. The
            # numbering stays in the caption because the *order* is what CDO
            # reads — "Output 1: Eigenvalues …" says both things at once.
            #
            # ``operator_outputs`` always returns ``nout`` entries, falling back
            # to the old generic captions for the operators nothing declares, so
            # this branch needs no test for whether metadata exists.
            for index, output in enumerate(_operator_outputs(operator, nout)):
                params.append(
                    ("file", _output_field_label(operator, index, nout),
                     output.field or f"Select output file {index + 1}"))
        elif nout == 0:
            # An operator that writes no file still produces something, and for
            # a third of this category that something *is* the deliverable:
            # ``cdo gmtxyz temp > data.gmt`` is how the manual writes gmtxyz,
            # and outputtab's whole purpose is a table somebody opens elsewhere.
            #
            # The model builder has had this since it was built — ``stdout_file``
            # on the node, written by ``model_runner.write_stdout_capture`` —
            # and this panel printed to a console with no way to keep it. Two
            # surfaces, one of which could do the documented thing.
            #
            # Optional, and blank is the old behaviour exactly: print to the
            # console and write nothing.
            params.append(
                ("savefile", STDOUT_FILE_LABEL,
                 "Optional: file to write the printed output to"))
        # nout == 0 also needs no output *argument*, which is why the row above
        # is a redirection and not one.

        # CDO's global options, last, because they are the least often needed
        # and belong to the run rather than to the operator.
        #
        # Offered on every operator rather than only the ones whose manual
        # examples show one: ``-f``, ``-b``, ``-z`` and ``-r`` are global by
        # definition and the manual uses them across every section. The reason
        # this section is what finally added the row is that here the default is
        # actively wrong — measured on 2.6.3, ``cdo import_binary demo.ctl
        # out.nc`` writes a *GRIB* file with 16-bit packing however the output
        # is named, so a user who asked for out.nc gets neither the format nor
        # the precision they had, and no message says so.
        #
        # The placeholder is per operator rather than the fixed "e.g. -f nc" it
        # used to be, because for ~200 operators the option that matters is not
        # a format switch. ``--timestat_date`` decides the timestamp every
        # temporal statistic stamps on its output periods, its default is
        # ``middle`` rather than the ``last`` most people assume, and a monthly
        # mean of January therefore comes out dated the 16th. A user who never
        # learns the option exists never learns that either — the file is not
        # wrong, it is dated somewhere they did not choose.
        #
        # A placeholder rather than a new widget: the options row is one
        # free-text field by design (CDO's global option set is large and
        # versioned, and this layer deliberately does not validate it — see
        # ``_resolve_operator_call``), so naming the relevant option in the
        # field's own hint is the surfacing that fits what is already there.
        # ``operator_options`` carries the full list and the measured defaults
        # for anything that wants to render more.
        params.append(
            ("string", CDO_OPTIONS_LABEL, operator_options_hint(operator)))

        # Special cases for operators with additional parameters
        if operator == "const":
            params.insert(0, ("string", "Constant Value", "Enter value,grid (e.g., 273.15,r360x180)"))

        return params

    # ── output-path helpers ───────────────────────────────────────────────

    def _suggest_output_path(self, input_path: str, operator: str = "") -> str:
        """Derive a suggested output path from *input_path* and the operator name.

        The extension is copied from the input file so CDO writes the same
        format.  If the input extension is not CDO-recognised, ``.nc`` is used.
        """
        if not input_path:
            return ""
        directory = os.path.dirname(os.path.abspath(input_path))
        base = os.path.basename(input_path)
        stem, ext = os.path.splitext(base)
        if ext.lower() not in _CDO_EXTENSIONS:
            ext = '.nc'
        suffix = f"_{operator}" if operator else "_output"
        return os.path.join(directory, f"{stem}{suffix}{ext}")

    def _get_first_input_path(self) -> str:
        """Return the first existing input file path currently set in the form."""
        if "multi_file_widget" in self.parameter_widgets:
            files = self.parameter_widgets["multi_file_widget"].get_files()
            return files[0] if files else ""
        for label in sorted(self.parameter_widgets):
            if "Input File" in label:
                val = self._extract_widget_value(self.parameter_widgets[label])
                if val and os.path.exists(val):
                    return val
        return ""

    def _ensure_output_extension(self, output_file: str, input_files: list) -> str:
        """Ensure *output_file* has a CDO-recognised extension.

        CDO infers the output format entirely from the file extension.  If the
        path has no extension or an unrecognised one, inherit the extension from
        the first input file so the output format matches the input.  Falls back
        to ``.nc`` when the input extension is also unrecognised.
        """
        stem, ext = os.path.splitext(output_file)
        if ext.lower() in _CDO_EXTENSIONS:
            return output_file           # already correct — nothing to do
        out_ext = '.nc'
        if input_files:
            inp_ext = os.path.splitext(input_files[0])[1].lower()
            if inp_ext in _CDO_EXTENSIONS:
                out_ext = inp_ext
        fixed = stem + out_ext
        self.output_console.append(
            f"ℹ️  Output extension adjusted: '{os.path.basename(output_file)}' "
            f"→ '{os.path.basename(fixed)}' (output format: {out_ext})"
        )
        return fixed

    def _on_input_path_changed(self, text: str) -> None:
        """Auto-fill the output path when a single-input file field changes."""
        if self._output_line_edit is None:
            return
        if not text or not os.path.exists(text):
            return
        # Only overwrite if still holding an auto-generated suggestion
        if (not self._output_line_edit.text()
                or self._output_line_edit.property('auto_filled')):
            suggestion = self._suggest_output_path(text, self.current_operator or "")
            if suggestion:
                self._output_line_edit.setText(suggestion)
                self._output_line_edit.setProperty('auto_filled', True)

    def _on_multi_input_changed(self, *_) -> None:
        """Auto-fill the output path when the MultiFileInputWidget list changes."""
        if self._output_line_edit is None:
            return
        if "multi_file_widget" not in self.parameter_widgets:
            return
        files = self.parameter_widgets["multi_file_widget"].get_files()
        if not files:
            return
        if (not self._output_line_edit.text()
                or self._output_line_edit.property('auto_filled')):
            suggestion = self._suggest_output_path(files[0], self.current_operator or "")
            if suggestion:
                self._output_line_edit.setText(suggestion)
                self._output_line_edit.setProperty('auto_filled', True)

    # ─────────────────────────────────────────────────────────────────────────

    def browse_file(self, line_edit, *, file_kind: str = ""):
        """Browse for a file and set the path in the line edit.

        ``file_kind`` is a key into ``core/filetypes.py`` naming what CDO reads
        in this particular slot, and the callers pass one because only they know
        which row they belong to — this method is reached from an operator's
        input, from any of its file-valued parameters and from its output, and
        those want three different choosers. Left empty it falls back to the
        All-Files chooser rather than to the nine-entry list that used to stand
        here, which offered shapefiles and GeoTIFFs to a CDO form.

        Ignored on the output branch: what may be *written* is decided by the
        engine's own extension set, not by what the slot reads.
        """
        # Determine if this is for input or output based on the label
        is_output = False
        for label, widget in self.parameter_widgets.items():
            if widget == line_edit.parent() and ("Output" in label or "output" in label.lower()):
                is_output = True
                break

        if is_output:
            # Derive a sensible initial path from the first input file so the
            # save dialog opens in the right directory with the right extension.
            input_path = self._get_first_input_path()
            suggested = self._suggest_output_path(
                input_path, self.current_operator or "output"
            ) if input_path else ""

            options = QFileDialog.Option.DontUseNativeDialog
            file_path, selected_filter = QFileDialog.getSaveFileName(
                self, "Select Output File",
                suggested,
                OUTPUT_FILE_DIALOG_FILTER,
                options=options
            )
            if file_path:
                # If the user chose "All Files" and omitted an extension, inherit
                # it from the input file (or fall back to .nc).
                #
                # Not for an operator whose output argument is a base: CDO picks
                # the extension there from the ``device`` parameter and appends
                # it itself, so forcing ``.nc`` on the way out of the dialog
                # produces ``plot.nc_tas.ps``. Same rule and same authority as
                # ``_ensure_output_extension``; both are the coercion, and both
                # have to know.
                stem, ext = os.path.splitext(file_path)
                if (ext.lower() not in _CDO_EXTENSIONS
                        and not writes_output_prefix(self.current_operator or "")):
                    inp_ext = os.path.splitext(input_path)[1].lower() if input_path else ''
                    file_path = stem + (inp_ext if inp_ext in _CDO_EXTENSIONS else '.nc')
                line_edit.setText(file_path)
                # Mark the field as manually set so auto-fill won't overwrite it
                if line_edit is self._output_line_edit:
                    self._output_line_edit.setProperty('auto_filled', False)
        else:
            # For input files, use open dialogs
            options = QFileDialog.Option.DontUseNativeDialog
            file_path, _ = QFileDialog.getOpenFileName(
                self, "Select Input File", "",
                ft.dialog_filter(file_kind),
                options=options
            )
            if file_path:
                line_edit.setText(file_path)

    def _browse_redirection(self, line_edit, *, saving: bool):
        """Pick the file on one side of a redirection — ``< data`` or ``> table``.

        Separate from :meth:`browse_file` because that method decides between an
        open and a save dialog by looking for "Output" in the row's caption, and
        neither of these rows is an output in the sense it means: one is read
        and one is written, and both are plain text rather than the NetCDF and
        GRIB it offers. Its extension-inheriting fallback is wrong for both too
        — a table saved as ``.tab`` must not be renamed to ``.nc`` because the
        input happened to be one.
        """
        options = QFileDialog.Option.DontUseNativeDialog
        if saving:
            path, _ = QFileDialog.getSaveFileName(
                self, "Save Printed Output As", "",
                STDOUT_FILE_DIALOG_FILTER, options=options)
        else:
            path, _ = QFileDialog.getOpenFileName(
                self, "Select Data File To Read", "",
                STDOUT_FILE_DIALOG_FILTER, options=options)
        if path:
            line_edit.setText(path)

    def open_expression_editor(self, line_edit):
        """Open the Expr editor on one field and write the result back.

        The input file comes from the form as it stands, so the editor can list
        the variables the expression will be written about and run its check
        against real data. An unwired form still opens the editor — the
        reference and the notes are worth having before a file is chosen.
        """
        from .expression_editor import edit_expression

        value = edit_expression(
            self,
            operator=self.current_operator,
            current=line_edit.text().strip(),
            input_path=self._first_input_path(),
            binary=getattr(self.NCExplorer, "NCExplorer_binary", "cdo"),
        )
        if value is not None:
            line_edit.setText(value)

    def _first_input_path(self):
        """The first input file the form currently names, or ""."""
        widget = self.parameter_widgets.get("multi_file_widget")
        if widget is not None:
            files = widget.get_files()
            if files:
                return files[0]

        for label in sorted(self.parameter_widgets):
            if "Input File" in label:
                path = self._extract_widget_value(self.parameter_widgets[label])
                if path:
                    return path

        # Nothing typed in yet: the layer the user is looking at is the file
        # they almost certainly mean.
        if (self.current_layer and hasattr(self, "geo_canvas")
                and self.current_layer in self.geo_canvas.layers):
            return self.geo_canvas.layers[self.current_layer].get("filepath", "")
        return ""

    def _collect_extra_parameters(self):
        """
        Collect extra non-file parameters in the order defined by
        get_extra_parameters_for_operator.  Skips the multi_file_widget entry.
        """
        extra_defs = self.get_extra_parameters_for_operator(self.current_operator)
        values = []
        for entry in extra_defs:
            name = entry[0]
            ptype = entry[1]
            label = entry[2]
            placeholder = entry[3]
            widget = self.parameter_widgets.get(label)
            if widget is None:
                self._debug(f"Extra parameter widget for '{label}' not found; skipping")
                values.append("")
                continue

            # Guard: never try to extract a value from MultiFileInputWidget
            if isinstance(widget, MultiFileInputWidget):
                self._debug(f"Skipping MultiFileInputWidget for label '{label}'")
                values.append("")
                continue

            val = self._extract_widget_value(widget)
            self._debug(f"Extra parameter '{label}' value: '{val}'")
            values.append(val)
        return values

    def _confirm_import_caveats(self, operator, input_files, options) -> bool:
        """Warn before an import that will quietly produce the wrong thing.

        False means the user backed out. Only the import operators reach a
        dialog, and only when the run is actually going to go wrong — a caveat
        shown on every run is one nobody reads.

        The case that earns a dialog is the format default, because it is the
        one mistake here with no symptom. Measured on 2.6.3, ``cdo import_binary
        demo.ctl out.nc`` exits 0, prints nothing unusual, and writes a **GRIB**
        file with 16-bit packing under the name ``out.nc``. The user loses the
        format they asked for and most of their precision, and finds out when
        something downstream refuses the file — or worse, does not. Every
        documented example of both import operators opens with ``cdo -f nc``,
        which is exactly why the options field now exists.

        The other two caveats are *not* dialogs, deliberately. The 32-bit-float
        limit and the HDF5 requirement are properties of the operator rather
        than of this run, so they belong on the operator's description where all
        three surfaces already show them — see ``_SURPRISING_DEFAULTS``. A modal
        that fires every time carries no information.
        """
        if operator not in ("import_binary", "import_grads", "import_cmsaf"):
            return True
        # ``-f`` in any spelling the user might have typed, including "-fnc".
        if any(option.startswith("-f") for option in options):
            return True

        answer = QMessageBox.warning(
            self, "Output format",
            f"'{operator}' has no -f option, so CDO will write its default "
            f"format — measured as GRIB with 16-bit packing — whatever the "
            f"output file is called.\n\n"
            f"Put -f nc in the “{CDO_OPTIONS_LABEL}” field to get NetCDF.\n\n"
            f"Run anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _redirection_value(self, label: str) -> str:
        """One of the three non-argument rows, or "" when the form has no such row.

        Returning "" for an absent row is what keeps every other operator's
        request byte-identical to what it was: the rows are only drawn for the
        operators that need them, and a missing widget is not an error.
        """
        widget = self.parameter_widgets.get(label)
        if widget is None:
            return ""
        return (self._extract_widget_value(widget) or "").strip()

    def _collect_environment(self):
        """The environment overrides the form is currently asking for.

        A tuple of ``(name, value)`` pairs, in the order the schema declares
        them, holding only the ones the user actually set — a blank row means
        "leave CDO's default alone" and must not become ``NAME=`` in the
        environment, which CDO reads as a value.

        Deliberately separate from :meth:`_collect_extra_parameters` rather than
        folded into it. That method walks ``spec.params``, which is positional
        and becomes the ``op,a,b`` token; an environment variable belongs to
        neither. Keeping the two collections apart is what stops
        ``CDO_WEIGHT_MODE=off`` being spelled at CDO as ``eof,3,off``.
        """
        pairs = []
        for variable in _operator_environment(self.current_operator):
            label = f"{ENV_LABEL_PREFIX}{variable.label or variable.name}"
            widget = self.parameter_widgets.get(label)
            if widget is None:
                continue
            value = (self._extract_widget_value(widget) or "").strip()
            if value:
                pairs.append((variable.name, value))
        return tuple(pairs)

    def execute_operation(self):
        """Execute the selected NCExplorer operation with comprehensive debugging"""
        if not self.current_operator:
            QMessageBox.warning(self, "Warning", "No operator selected")
            return

        # The same arity the form was built from, from the same source.
        nin, nout = _operator_arity(self.current_operator)

        self._debug(f"Starting execution of '{self.current_operator}'")
        self._debug(f"Operator signature: nin={nin}, nout={nout}")

        try:
            # Collect extra non-file parameters (c, nsets, nskip, etc.)
            extra_args = self._collect_extra_parameters()
            self._debug(f"Collected extra parameters: {extra_args}")

            # A required field left blank is checked here, before the file
            # count, because it is the one mistake on this form that does not
            # produce a failed run: CDO prompts for the missing value on stdin
            # and never stops, so the window freezes instead of reporting
            # anything. The model builder has refused this since it was built
            # (core/model.py::_validate_parameters); the toolbar and the command
            # palette share this form and so had no equivalent.
            missing = missing_required_parameters(self.current_operator, extra_args)
            if missing:
                error_msg = (
                    f"'{self.current_operator}' needs "
                    f"{'a value' if len(missing) == 1 else 'values'} for "
                    f"{', '.join(missing)}."
                )
                self.output_console.append(f"❌ DEBUG: {error_msg}")
                QMessageBox.warning(self, "Missing parameter", error_msg)
                return

            # Debug parameter collection
            self._debug(f"Collecting parameters from {len(self.parameter_widgets)} widgets")

            # Collect input files based on signature
            # Collect input files — use MultiFileInputWidget if present
            input_files = []
            if "multi_file_widget" in self.parameter_widgets:
                input_files = self.parameter_widgets["multi_file_widget"].get_files()
                self._debug(
                    f"Collected {len(input_files)} input files "
                    f"from MultiFileInputWidget: {input_files}"
                )
            else:
                for label in sorted(self.parameter_widgets):
                    self._debug(f"Processing parameter: '{label}'")
                    if "Input File" in label:
                        widget_container = self.parameter_widgets[label]
                        self._debug(f"Widget type: {type(widget_container)}")
                        file_value = self._extract_widget_value(widget_container)
                        self._debug(f"Extracted value: '{file_value}'")
                        if file_value:
                            input_files.append(file_value)
                            self._debug(f"Added to input_files: '{file_value}'")

            self._debug(f"Collected {len(input_files)} input files: {input_files}")

            # Validate count
            if nin > 0 and len(input_files) != nin:
                error_msg = (
                    f"Operator '{self.current_operator}' requires exactly {nin} input files, "
                    f"but {len(input_files)} were provided."
                )
                self.output_console.append(f"❌ DEBUG: {error_msg}")
                QMessageBox.warning(self, "Error", error_msg)
                return
            elif nin == -1 and len(input_files) == 0:
                error_msg = (
                    f"Operator '{self.current_operator}' requires at least one input file."
                )
                self.output_console.append(f"❌ DEBUG: {error_msg}")
                QMessageBox.warning(self, "Error", error_msg)
                return

            # Check if input files exist
            for i, file_path in enumerate(input_files):
                if not os.path.exists(file_path):
                    error_msg = f"Input file {i + 1} does not exist: '{file_path}'"
                    self.output_console.append(f"❌ DEBUG: {error_msg}")
                    QMessageBox.warning(self, "Error", error_msg)
                    return
                else:
                    self.output_console.append(
                        f"✅ DEBUG: Input file {i + 1} exists: '{file_path}'"
                    )

            self._warn_about_units(self.current_operator, input_files)

            # Whether the two files go together, which the units check cannot
            # ask: it looks at one file at a time. Returns False when the user
            # backed out of a blocking mismatch.
            if not self._confirm_input_pairing(self.current_operator, input_files):
                return

            # Build the invocation in CDO order:
            #   [extra_args..., input_files..., output/prefix (if any)]
            if nout == 0:
                # Info/display operators - no output file needed
                output_files = []
                self._debug(f"Executing info/display operator (nout=0)")

            elif nout == 1:
                # Standard operators - need one output file
                output_file = None
                for label in self.parameter_widgets:
                    if "Output File" in label or "output" in label.lower():
                        widget_container = self.parameter_widgets[label]
                        output_file = self._extract_widget_value(widget_container)
                        self._debug(f"Output file: '{output_file}'")
                        break

                if not output_file:
                    error_msg = "Output file is required for this operation"
                    self.output_console.append(f"❌ DEBUG: {error_msg}")
                    QMessageBox.warning(self, "Error", error_msg)
                    return

                # Ensure the output path has a CDO-recognised extension so CDO
                # writes the correct format.  If the user typed a bare name or
                # used an unrecognised extension, inherit from the input file.
                #
                # Skipped when the path is an *obase* rather than a file. The
                # six Magics plot operators are ``nout == 1`` and reach this
                # branch, and coercing their base name is actively harmful:
                # ``plot`` becomes ``plot.nc``, CDO appends its own suffix, and
                # the user gets ``plot.nc_tas.ps``. The extension is CDO's to
                # choose here — it comes from the ``device`` parameter — so
                # there is nothing for this to fix. See
                # ``categories.writes_output_prefix``.
                if not writes_output_prefix(self.current_operator, extra_args):
                    output_file = self._ensure_output_extension(
                        output_file, input_files)
                output_files = [output_file]
                self._debug(f"Executing standard operator (nout=1)")

            elif nout == -1:
                # Split / variable-output operators - need prefix/operfix
                prefix = None
                for label in self.parameter_widgets:
                    if "operfix" in label.lower() or "prefix" in label.lower() or "Output" in label:
                        widget_container = self.parameter_widgets[label]
                        prefix = self._extract_widget_value(widget_container)
                        self._debug(f"Using prefix/operfix '{prefix}' from label '{label}'")
                        break

                if not prefix:
                    error_msg = "Output prefix (operfix) is required for split operations"
                    self.output_console.append(f"❌ DEBUG: {error_msg}")
                    QMessageBox.warning(self, "Error", error_msg)
                    return

                output_files = [prefix]
                self._debug(f"Executing split operator (nout=-1)")

            elif nout > 1:
                # eof and its relatives write two files — eigenvalues to the
                # first, eigenvectors to the second — and the order is the
                # order of the rows, so they are collected by index rather than
                # by scanning the dict. Reaching this branch at all is new; it
                # used to fall through to "Unsupported operator signature".
                # Looked up by the same labels ``parse_parameters`` emitted,
                # rebuilt from the schema rather than hard-coded, so the two
                # cannot drift apart the moment an operator declares its
                # outputs. Position is what CDO reads, hence ``range(nout)``
                # rather than a scan of the widget dictionary.
                output_files = []
                for index in range(nout):
                    label = _output_field_label(
                        self.current_operator, index, nout)
                    widget = self.parameter_widgets.get(label)
                    value = self._extract_widget_value(widget) if widget else ""
                    if not value:
                        error_msg = (
                            f"'{self.current_operator}' writes {nout} output "
                            f"files; {label} is empty.")
                        self.output_console.append(f"❌ DEBUG: {error_msg}")
                        QMessageBox.warning(self, "Error", error_msg)
                        return
                    output_files.append(
                        self._ensure_output_extension(value, input_files))
                self._debug(f"Executing multi-output operator (nout={nout})")

            else:
                error_msg = f"Unsupported operator signature: ({nin}|{nout})"
                self.output_console.append(f"❌ DEBUG: {error_msg}")
                QMessageBox.warning(self, "Error", error_msg)
                return

            # The three rows that are not arguments. Each is looked up by the
            # exact label ``parse_parameters`` created it under, and each is
            # absent for every operator that does not draw it — so a form that
            # never showed these produces a request identical to the one it
            # produced before they existed.
            stdin_file = self._redirection_value(STDIN_FILE_LABEL)
            stdout_file = self._redirection_value(STDOUT_FILE_LABEL)

            # ``-f nc`` is two argv tokens, not one, so the field is split the
            # way a shell would split it. shlex rather than str.split so a path
            # in an option (``--chunkspec`` on a quoted value) survives.
            raw_options = self._redirection_value(CDO_OPTIONS_LABEL)
            try:
                options = tuple(shlex.split(raw_options)) if raw_options else ()
            except ValueError as exc:
                QMessageBox.warning(
                    self, "CDO options",
                    f"Could not read the options field: {exc}")
                return

            if stdin_file and not os.path.exists(stdin_file):
                QMessageBox.warning(
                    self, "Error",
                    f"The data file to read does not exist: '{stdin_file}'")
                return

            # The one caveat that has to arrive before the run rather than after
            # it, because afterwards there is nothing to see: a wrong answer
            # here is a file that opened fine and holds the wrong numbers.
            if not self._confirm_import_caveats(self.current_operator,
                                                input_files, options):
                return

            # The run itself happens off the UI thread; handle_operation_finished
            # and its siblings pick the outcome up from here.
            request = OperatorRequest(
                operator=self.current_operator,
                input_files=tuple(input_files),
                output_files=tuple(output_files),
                parameters=tuple(extra_args),
                nin=nin,
                nout=nout,
                stdin_file=stdin_file,
                stdout_file=stdout_file,
                options=options,
                env=self._collect_environment(),
            )
            # ``command_line`` renders the overrides as the ``NAME=value cdo …``
            # prefix, so what is echoed here is a line that can be pasted into a
            # shell and reproduce the run — which for the EOFs section is the
            # difference between two runs that compute different numbers.
            self.output_console.append(f"▶ {request.command_line()}")
            self.execution.start(request)

        except Exception as e:
            error_msg = f"Failed to execute operation: {str(e)}"
            self.output_console.append(f"✗ {error_msg}")
            self._debug(f"Exception type: {type(e)}")
            self._debug(f"Exception details: {str(e)}")
            import traceback
            self._debug(f"Traceback: {traceback.format_exc()}")
            QMessageBox.critical(self, "Execution Error", error_msg)
            self.save_btn.setEnabled(False)
            self.visualize_btn.setEnabled(False)

    @staticmethod
    def _parameter_help(operator):
        """``label -> help text`` for one operator's declared parameters.

        Keyed by label because that is what the form rows are built from, and
        it is the same label the schema supplied in the first place.
        """
        try:
            from ..core.categories import OPERATOR_SCHEMA
        except ImportError:
            from ncexplorer_toolkit.core.categories import OPERATOR_SCHEMA

        spec = OPERATOR_SCHEMA.get(operator)
        if spec is None:
            return {}
        return {p.label: p.help for p in spec.params if p.help}

    @staticmethod
    def _parameter_file_kinds(operator):
        """``label -> filetypes key`` for one operator's file-valued parameters.

        Keyed by label for the same reason :meth:`_parameter_help` is: that is
        what ``parse_parameters`` builds its rows from, and the label came from
        the schema in the first place. Only ``file`` and ``grid`` parameters
        appear, so a row that finds nothing here is a row with no chooser on it.
        """
        try:
            from ..core.categories import OPERATOR_SCHEMA
        except ImportError:
            from ncexplorer_toolkit.core.categories import OPERATOR_SCHEMA

        spec = OPERATOR_SCHEMA.get(operator)
        if spec is None:
            return {}
        kinds = {}
        for p in spec.params:
            resolved = parameter_file_kind(p)
            if resolved:
                kinds[p.label] = resolved
        return kinds

    def _warn_about_units(self, operator, input_files):
        """Say so when a file's units are not what the operator will assume.

        A warning and never a block. The climate indices are why: ``eca_su``
        reads its field as Kelvin while its threshold is in degrees Celsius,
        and a field already in °C does not fail — it counts every day of the
        year as a summer day and writes a perfectly well-formed file. But a
        ``units`` attribute is a claim about data rather than the data, plenty
        of valid model output carries none, and somebody who knows their file
        better than its metadata must still be able to press Run.
        """
        try:
            from ..core.units import check_inputs
        except ImportError:
            from ncexplorer_toolkit.core.units import check_inputs

        try:
            warnings = check_inputs(operator, list(input_files))
        except Exception:
            # Nothing here is worth failing a run over.
            logger.debug("Units check failed for %s", operator, exc_info=True)
            return

        for warning in warnings:
            self.output_console.append(f"⚠️ Units: {warning.message}")
            logger.info("Units warning for %s: %s", operator, warning.message)

    def _confirm_input_pairing(self, operator, input_files):
        """Report how two inputs fail to match. False when the user backs out.

        The units check beside this one warns and never blocks, and it is right
        to: a units attribute is a claim about data rather than the data. This
        one blocks, for one measured reason — ``fldcor`` and ``fldcovar`` handed
        series of different lengths do not fail. They warn on stdout, exit 0,
        and truncate the answer to the shorter series, so the run reaches
        ``handle_operation_finished`` as a success, the output opens, and the
        numbers are wrong. Nothing downstream can tell that from a real result,
        which is what makes this the one place it can be stopped.

        Still a confirmation rather than a refusal. A user who knows the two
        files line up in a way the metadata does not show must be able to
        proceed, and the dialog defaults to No so proceeding is deliberate.
        """
        try:
            from ..core.pairing import blocking, check_pairing
        except ImportError:                                     # pragma: no cover
            from ncexplorer_toolkit.core.pairing import blocking, check_pairing

        try:
            problems = check_pairing(operator, list(input_files))
        except Exception:
            # A pre-flight check that crashes must not be what stops a run.
            logger.debug("Pairing check failed for %s", operator, exc_info=True)
            return True

        for problem in problems:
            self.output_console.append(f"⚠️ Inputs: {problem.message}")
            logger.info("Pairing warning for %s: %s", operator, problem.message)

        stoppers = blocking(problems)
        if not stoppers:
            return True

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("These inputs do not match")
        box.setText(f"'{operator}' would not report this as a problem.")
        box.setInformativeText("\n\n".join(problem.message for problem in stoppers))
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        box.button(QMessageBox.StandardButton.Yes).setText("Run it anyway")
        box.button(QMessageBox.StandardButton.No).setText("Cancel")
        if box.exec() == QMessageBox.StandardButton.Yes:
            self.output_console.append(
                f"▶ {operator}: mismatched inputs accepted by the user")
            logger.warning("%s run with mismatched inputs after confirmation",
                           operator)
            return True
        self.output_console.append(f"■ {operator} cancelled: inputs do not match")
        return False

    def _announce_variables(self, output_file):
        """Name every variable in a result that holds more than one.

        Most of the climate indices write two: ``eca_cdd`` writes
        ``consecutive_dry_days_index_per_time_period`` *and*
        ``number_of_cdd_periods_with_more_than_5days_per_time_period``, and so
        do cwd, cfd, csu, rx5day and the spell indices. Everything downstream —
        the map, the statistics panel, the plot — follows the layer's variable,
        which is seeded from the first one in the file. Saying which they are is
        what turns a silent choice into a visible one.
        """
        try:
            from ..core.units import data_variables
        except ImportError:
            from ncexplorer_toolkit.core.units import data_variables

        names = data_variables(output_file)
        if len(names) < 2:
            return
        self.output_console.append(
            f"ℹ️ This result holds {len(names)} variables: "
            f"{', '.join(self._label_variable(name) for name in names)}. "
            f"The map and the statistics panel show '{names[0]}' unless you "
            "pick another in the layer's properties."
        )

    @staticmethod
    def _label_variable(name: str) -> str:
        """One variable name, plus what it is when the name does not say.

        ``timcor`` writes its correlation beside a variable literally called
        ``pvalue``, and listing it as ``pvalue`` next to ``tas`` presents it as
        a second unnamed field of the same kind. It is neither: it is a
        significance measure, and — measured against the t-distribution on 162
        gridpoints — it is not a p-value either, but the confidence level, high
        where the correlation is strong. A user who reads the name and filters
        ``< 0.05`` gets an empty map and no indication why, so the one place the
        name is shown is the place to say so.
        """
        if name == "pvalue":
            return "pvalue (significance: high = significant, not a p-value)"
        return name

    def _offer_result_as_series(self, output_file):
        """Send a one-gridpoint result to the plot panel instead of the map.

        A result with a single gridpoint is a time series, not a map, and the
        canvas cannot say so: ``layers.load_netcdf`` refuses anything that is
        not two-dimensional and a ``(1, 1)`` field is two-dimensional. It is
        drawn as one degenerate cell over a zero-width extent — a successful
        render of nothing.

        Decided by inspecting the file rather than by matching the operator's
        name, so it covers ``fldcor`` and ``fldcovar`` and equally ``fldmean``,
        ``fldsum``, ``fldpctl`` and every other reduction to a single point,
        including results that arrive from a model or a batch step whose
        operator this method never sees. See ``core.units.result_shape``.

        Additive, never a veto: the layer is still offered on the canvas and
        Visualise still works, because a user who wants to see the degenerate
        cell is entitled to. This adds the reading that is actually useful and
        says why.
        """
        try:
            from ..core.units import result_shape
        except ImportError:                                     # pragma: no cover
            from ncexplorer_toolkit.core.units import result_shape

        try:
            shape = result_shape(output_file)
            if not shape.is_single_point:
                return

            where = "the plot panel" if shape.is_series else "the statistics panel"
            steps = shape.steps or 1
            self.output_console.append(
                f"📈 This result has one gridpoint, not a map: "
                f"{steps} value(s) over time across "
                f"{len(shape.variables) or 1} variable(s). It has no spatial "
                f"extent to draw, so it has been opened in {where} rather than "
                f"added as a map layer."
            )

            if not shape.is_series:
                # A single point and a single timestep is one number. There is
                # no curve to draw, and the statistics panel already reports a
                # value; saying so is the whole of what is useful here.
                return

            plotted = self.plot_dock.show_file_series(output_file)
            if plotted:
                self.plot_dock.show()
                self.plot_dock.raise_()
        except Exception:
            # A convenience must never be what turns a successful run into a
            # failed one; the file is on disk and already reported either way.
            logger.debug("Could not offer %s as a series", output_file, exc_info=True)

    # ------------------------------------------------------------------
    # Outcomes of an asynchronous run (gui/execution_controller.py)
    # ------------------------------------------------------------------
    def handle_operation_finished(self, request, result):
        """Display the result of a run that reached CDO's own end."""
        self._debug(f"Result.success: {result.success}")
        self._debug(f"Result.stdout length: {len(result.stdout) if result.stdout else 0}")
        self._debug(f"Result.stderr length: {len(result.stderr) if result.stderr else 0}")

        if result.success:
            self.output_console.append(
                f"✓ Operation '{request.operator}' completed successfully"
            )
            self.output_console.append(
                f"Execution time: {result.execution_time:.2f} seconds"
            )

            # Before the bulk output, because this is the part of it that
            # changes what the result means. CDO prints it on stdout or stderr
            # and exits 0, so it otherwise scrolls past among the progress
            # lines — or, on stderr, is never shown at all.
            for notice in stream_notices(result.stdout, result.stderr):
                self.output_console.append(f"⚠️ {redact_text(notice)}")

            if result.stdout:
                if request.operator in ["diff", "diffv", "diffc", "diffn", "diffp"]:
                    if "records differ" in result.stdout or "differ" in result.stdout.lower():
                        self.output_console.append("Differences found between files:")
                    else:
                        self.output_console.append("Files are identical")
                else:
                    self.output_console.append("Output:")
                self.output_console.append(result.stdout)

            # The reading, kept. ``write_stdout_capture``'s own docstring says
            # "every driver that runs an OperatorRequest calls this" — the model
            # builder and the batch runner did, and this panel did not, so the
            # one surface most people run gmtxyz from was the one that could not
            # keep what it printed. The same function on all three, so an
            # exported script's ``> file`` and an in-process run put the same
            # bytes in the same place.
            if request.stdout_file:
                if write_stdout_capture(request, result.stdout):
                    self.output_console.append(
                        f"Reading saved to: {request.stdout_file}")
                else:
                    self.output_console.append(
                        f"⚠️ Could not write {request.stdout_file}")

            if result.stderr:
                for line in result.stderr.splitlines():
                    if line.strip():
                        self.output_console.append(f"⚠️ {redact_text(line)}")

            # Files the run wrote whose names it chose itself. Two shapes reach
            # here and both were previously silent:
            #
            #   * ``cmor``'s DRS tree, which no argument names at all;
            #   * a base path that fans out — every ``split*``, ``distgrid``,
            #     and the two Ensval operators, whose suffixes are not a
            #     numbered series and cannot be guessed. ``cdo enscrps ref
            #     ens… cbase`` writes cbase.crps.nc, cbase.crps_pot.nc and
            #     cbase.crps_reli.nc, and the module page names three
            #     *different* suffixes, so a user reading the manual and
            #     looking in the folder finds neither what they expected nor a
            #     message about it.
            #
            # Listed before the single-output branch because a run can be both
            # kinds at once in principle, and because for a fan-out run this is
            # the only report there will be: ``nout == -1`` leaves
            # ``output_file`` unset by design (there is no one file to name),
            # which is exactly why the else-branch below said nothing.
            discovered = getattr(result, "discovered_outputs", ()) or ()
            if discovered:
                # A plot run says so, because the next thing a user would try
                # is Visualise — and these are pictures, so the button is off
                # and would otherwise be off for no stated reason. The paths
                # themselves are the whole report for these: their names are
                # CDO's choice, not the one the user typed.
                noun = ("plot(s) written — open them in an image viewer, not "
                        "in this application"
                        if writes_images(request.operator) else "file(s) written")
                self.output_console.append(f"{len(discovered)} {noun}:")
                for path in discovered:
                    self.output_console.append(f"  • {path}")

            # Handle an output file for operators that create files
            if request.nout > 0 and getattr(result, "output_file", None):
                self.current_output_file = result.output_file
                self.save_btn.setEnabled(True)
                self.visualize_btn.setEnabled(True)
                self.output_console.append(f"Output saved to: {result.output_file}")
                self._announce_variables(result.output_file)
                self._offer_result_as_series(result.output_file)
            else:
                # For info operators, enable save for the console output
                self.save_btn.setEnabled(True)
                self.visualize_btn.setEnabled(False)

            self.statusBar().showMessage(f"{request.operator} finished", 3000)
        else:
            self.output_console.append(f"✗ Operation '{request.operator}' failed")
            if result.stderr:
                self.output_console.append(f"Error: {redact_text(result.stderr)}")
            self.save_btn.setEnabled(False)
            self.visualize_btn.setEnabled(False)
            self.statusBar().showMessage(f"{request.operator} failed", 5000)

    def handle_operation_failed(self, request, message):
        """Report a run that never produced a result at all."""
        self.output_console.append(f"✗ Operation '{request.operator}' failed: {message}")
        self.save_btn.setEnabled(False)
        self.visualize_btn.setEnabled(False)
        QMessageBox.critical(self, "Execution Error", message)

    def handle_operation_cancelled(self, request):
        """Note a run the user stopped. Any partial output is already gone."""
        self.output_console.append(f"■ Operation '{request.operator}' cancelled")
        self.statusBar().showMessage(f"{request.operator} cancelled", 3000)

    def _debug(self, message: str) -> None:
        """Route DEBUG messages: always to logger, to console only if enabled."""
        logger.debug(message)
        if getattr(self, "debug_mode", False):
            self.output_console.append(f"🔍 DEBUG: {message}")

    def _extract_widget_value(self, widget_container):
        """
        Extract the actual string value from a widget container with debug info.
        """
        self._debug(f"_extract_widget_value called with: {type(widget_container)}")

        if isinstance(widget_container, QCheckBox):
            # Before the QLineEdit branch: a QCheckBox is not one, but putting
            # this after the composite-layout branch would let a checkbox in a
            # row widget fall through to "". Reported as the literal CDO takes
            # so a saved project reads the same as a fresh form.
            value = "true" if widget_container.isChecked() else "false"
            self._debug(f"QCheckBox value: '{value}'")
            return value

        if isinstance(widget_container, QLineEdit):
            # Direct QLineEdit widget
            value = widget_container.text().strip()
            self._debug(f"Direct QLineEdit value: '{value}'")
            return value

        elif isinstance(widget_container, QComboBox):
            value = widget_container.currentText().strip()
            self._debug(f"QComboBox current value: '{value}'")
            return value

        elif hasattr(widget_container, 'layout') and widget_container.layout() is not None:
            # Composite widget with layout (like file browser widgets)
            layout = widget_container.layout()
            self._debug(f"Widget has layout with {layout.count()} items")

            # Find the QLineEdit in the layout
            for i in range(layout.count()):
                item = layout.itemAt(i)
                if item and item.widget():
                    widget = item.widget()
                    self._debug(f"Layout item {i}: {type(widget)}")
                    if isinstance(widget, QLineEdit):
                        value = widget.text().strip()
                        self._debug(f"Found QLineEdit with value: '{value}'")
                        return value

        elif hasattr(widget_container, 'text'):
            # Widget with text() method
            value = widget_container.text().strip()
            self._debug(f"Widget with text() method: '{value}'")
            return value

        # Fallback: try to get string representation, but avoid QWidget objects
        widget_str = str(widget_container)
        if "PyQt" in widget_str or "QWidget" in widget_str:
            # This is a widget object, return empty string instead
            self._debug(f"Widget object detected, returning empty string")
            return ""

        self._debug(f"Fallback string representation: '{widget_str.strip()}'")
        return widget_str.strip()

    # File menu actions
    def open_file(self):
        """Open a file dialogs and load the selected file"""
        options = QFileDialog.Option.DontUseNativeDialog
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Open File", "", _canvas_file_dialog_filter(),
            options=options
        )

        if file_path:
            self.visualize_file(file_path)

    def open_recent_file(self, file_path):
        """Load a file chosen from File > Open Recent.

        The one list holds data files and projects alike, so the extension is
        what decides which of the two ways of opening applies.
        """
        if not os.path.exists(file_path):
            # The menu filters missing files when it is built, but the file can
            # disappear between the menu opening and the click.
            logger.warning("Recent file no longer exists: %s", file_path)
            self.statusBar().showMessage(f"File no longer exists: {file_path}", 5000)
            return
        if file_path.lower().endswith(PROJECT_SUFFIX):
            if self._confirm_discard_project():
                self.load_project_file(file_path)
            return
        self.visualize_file(file_path)

    def clear_recent_files(self):
        """Empty the recent-files list and refresh the submenu."""
        self.recent_files.clear()
        self.menu_bar.rebuild_recent_menu()
        self.statusBar().showMessage("Recent files cleared", 3000)

    # ------------------------------------------------------------------
    # Drag and drop. The canvas accepts drops over its own area (see
    # GeoCanvas.dropEvent); these handlers cover the rest of the window.
    # ------------------------------------------------------------------

    #: GRIB on top of the registry: the canvas cannot draw it, but a dropped
    #: .grb is still a valid CDO input, and the loader refusing it with a
    #: reason beats the drop handler discarding it in silence.
    _EXTRA_DROPPABLE = (".grb", ".grib", ".grb2", ".grib2")

    @property
    def DROPPABLE_EXTENSIONS(self):
        """Extensions a drop will accept — the set the Open dialog offers.

        A property rather than a class attribute because the registry probes
        GDAL's driver tables to answer, and doing that while the class body is
        executing would pull rasterio and pyogrio into every import of this
        module. The probe result is cached, so repeated drops cost nothing.
        """
        from ..geocanvas import formats as fmts
        return self._EXTRA_DROPPABLE + fmts.supported_extensions()

    def _droppable_paths(self, mime_data):
        """Local paths in a drop that this application can actually load.

        Anything without a local path — dragged text, a web URL, a mail
        attachment — yields an empty string from toLocalFile() and is skipped,
        as is any file whose extension is not supported.
        """
        if not mime_data.hasUrls():
            return []

        paths = []
        for url in mime_data.urls():
            path = url.toLocalFile()
            if not path:
                continue
            if os.path.splitext(path)[1].lower() in self.DROPPABLE_EXTENSIONS:
                paths.append(path)
        return paths

    def dragEnterEvent(self, event):
        # Accepting only when something in the drag is loadable is what makes the
        # cursor honest; accepting everything would promise a load that dropEvent
        # then silently ignores.
        if self._droppable_paths(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._droppable_paths(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = self._droppable_paths(event.mimeData())
        if not paths:
            event.ignore()
            return

        event.acceptProposedAction()
        for path in paths:  # in the order they were dropped
            logger.info("Loading dropped file: %s", path)
            self.visualize_file(path)

    def save_file(self):
        """Save the current output to a file"""
        if not self.current_output_file:
            QMessageBox.warning(self, "Warning", "No output to save")
            return

        options = QFileDialog.Option.DontUseNativeDialog
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save File", self.current_output_file,
            "NetCDF Files (*.nc);;GRIB Files (*.grb *.grib);;All Files (*)",
            options=options
        )
        if file_path:
            try:
                # Implement actual file saving logic here
                self.output_console.append(f"Saved to: {file_path}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to save file: {str(e)}")

    # Edit menu actions
    def show_preferences(self):
        """Show preferences dialogs"""
        QMessageBox.information(self, "Preferences", "Preferences dialogs would appear here")

    # View menu actions
    def toggle_toolbar(self, checked):
        """Toggle toolbar visibility"""
        self.menu_bar.toolbar_action.setChecked(checked)
        # Implement actual toolbar toggle logic

    def toggle_statusbar(self, checked):
        """Toggle status bar visibility"""
        self.statusBar().setVisible(checked)

    def toggle_log_dock(self, checked):
        """Show or hide the log dock."""
        self.log_dock.setVisible(checked)
        if checked:
            self.log_dock.raise_()

    def _sync_log_dock_action(self, visible):
        """Keep the View menu check mark right when the dock is closed directly."""
        # Fires once while the window is still being built, before the menu bar
        # exists; there is nothing to sync yet in that case.
        menu_bar = getattr(self, 'menu_bar', None)
        if menu_bar is not None:
            menu_bar.log_dock_action.setChecked(visible)

    def toggle_fullscreen(self):
        """Toggle fullscreen mode"""
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def setup_view_menu(self):
        """Setup view menu with a fullscreen canvas option."""
        # Add to your existing menu setup
        fullscreen_canvas_action = QAction("Fullscreen Canvas", self)
        fullscreen_canvas_action.setCheckable(True)
        fullscreen_canvas_action.triggered.connect(self.toggle_fullscreen_canvas)

    def toggle_fullscreen_canvas(self, checked):
        """Toggle fullscreen canvas mode."""
        self.geo_canvas.set_fullscreen_canvas(checked)

    # Layer menu actions
    def add_layer(self):
        """Add a new layer (wrapper for open_file)"""
        self.open_file()

    def remove_layer(self):
        """Remove the currently selected layer"""
        if not self.current_layer:
            QMessageBox.warning(self, "Warning", "No layer selected")
            return

        try:
            self.geo_canvas.remove_layer(self.current_layer)
            self.output_console.append(f"Removed layer: {self.current_layer}")
            self.current_layer = None
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to remove layer: {str(e)}")

    def show_layer_properties(self):
        """Show properties for current layer"""
        if not self.current_layer:
            QMessageBox.warning(self, "Warning", "No layer selected")
            return

        # Implement actual properties dialogs
        self.param_dock.show()

    # Help menu actions
    def show_about(self):
        """Show about dialogs"""
        QMessageBox.about(self, "About NCExplorer Toolkit",
                          "Climate Data Operators GUI\nVersion 1.0\n\n"
                          "A graphical interface for NCExplorer operations")

    def show_documentation(self):
        """Show documentation"""
        QMessageBox.information(self, "Documentation",
                                "Documentation would open here")

    def update_visualization(self, properties):
        """Update visualization with new properties"""
        if self.current_layer:
            # Update the layer properties
            layer_props = self.geo_canvas.property_manager.get_property(self.current_layer, '')
            if layer_props:
                layer_props.style.color = properties.get('colormap', '#3388ff')
                layer_props.style.transparency = properties.get('transparency', 0.0)
                layer_props.style.line_width = properties.get('line_width', 1.0)
                layer_props.style.point_size = properties.get('point_size', 10.0)

                # Update the visualization
                self.geo_canvas.symbology_manager.update_layer_style(self.current_layer)
                self.geo_canvas.draw()

    # ==================================================================
    # Projects (.ncx). The format itself lives in core/project.py; this
    # section is only the part that knows about widgets.
    # ==================================================================

    #: Shown when no project is open, and used as the suffix of the title once
    #: one is.
    BASE_TITLE = "Geospatial Analysis Software"

    def _setup_project_tracking(self):
        """Start watching for the changes that make a project unsaved.

        Structural changes only — layers, their properties, and operations that
        ran. Panning and zooming are *saved* in a project but do not mark one
        dirty: the extent changes on every scroll-wheel notch, and a window that
        asks about unsaved work after an idle drag across the map trains people
        to dismiss the question without reading it.
        """
        self._project_path = None
        self._project_dirty = False

        canvas = self.geo_canvas
        canvas.layer_added.connect(lambda *_: self._mark_project_dirty())
        canvas.layer_removed.connect(lambda *_: self._mark_project_dirty())
        canvas.property_manager.property_changed.connect(lambda *_: self._mark_project_dirty())
        self.execution.finished.connect(lambda *_: self._mark_project_dirty())

        self._update_window_title()

    # ------------------------------------------------------------------
    # Dirty flag and title
    # ------------------------------------------------------------------
    def _mark_project_dirty(self, dirty=True):
        """Note that the working state has moved away from the saved file."""
        if self._project_dirty == dirty:
            return
        self._project_dirty = dirty
        self._update_window_title()

    def _update_window_title(self):
        """Show the project's name, and a marker while it has unsaved changes."""
        if not self._project_path:
            self.setWindowTitle(self.BASE_TITLE)
            return
        name = os.path.basename(self._project_path)
        marker = "*" if self._project_dirty else ""
        self.setWindowTitle(f"{marker}{name} — {self.BASE_TITLE}")

    def _confirm_discard_project(self):
        """Ask about unsaved work. False means the user chose to stay put.

        Only asked when a project file is actually open. "Unsaved changes" is a
        statement about divergence from a saved file, and with no file there is
        nothing to diverge from and nothing the answer could restore — the
        window then closes exactly as it did before projects existed. It is also
        why the title carries no dirty marker until a project is named: the two
        follow one rule rather than disagreeing about when work counts as at
        risk.
        """
        if not self._project_path or not self._project_dirty:
            return True

        answer = QMessageBox.question(
            self, "Unsaved changes",
            "This project has unsaved changes.\n\nSave them before continuing?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            # A failed or cancelled save must not be read as "discard": that is
            # exactly the path that loses the work the question was asked about.
            return self.save_project()
        return True

    # ------------------------------------------------------------------
    # Capturing and applying
    # ------------------------------------------------------------------
    def _capture_project_state(self):
        """Everything about the current session worth putting in a file."""
        canvas = self.geo_canvas
        layers = []

        for name, record in canvas.layers.items():
            prop = canvas.property_manager.get_layer_property(name)
            artist = record.get('artist')
            source = record.get('filepath') or ""
            if source in ("", "N/A") and prop is not None:
                source = prop.metadata.source_file or ""
            if not source:
                # Points, polygons and lines added programmatically have no file
                # to reload from, so there is nothing a project could restore.
                logger.debug("Layer '%s' has no source file; not saved", name)
                continue

            netcdf = {}
            if prop is not None and prop.netcdf is not None:
                stored = prop.netcdf.to_dict()
                netcdf = {key: stored.get(key) for key in NETCDF_SELECTION_KEYS}

            layers.append(LayerState(
                name=name,
                source_path=os.path.abspath(source),
                layer_type=record.get('type') or (prop.metadata.layer_type if prop else ""),
                visible=bool(record.get('visible', True)),
                zorder=float(artist.get_zorder()) if artist is not None else 0.0,
                style=prop.style.to_dict() if prop is not None else {},
                netcdf=netcdf,
            ))

        colorbar = canvas.colorbar_manager
        canvas_state = CanvasState(
            extent=tuple(canvas.extent),
            projection=canvas.projection_name,
            basemap=self.basemap_combo.currentText(),
            graticule=bool(canvas.graticule_visible),
            scalebar=bool(canvas.scalebar_manager.visible),
            colorbar=bool(colorbar.visible),
            colorbar_position=getattr(colorbar, '_position', 'right'),
            theme=canvas.theme,
        )

        return ProjectState(
            layers=tuple(layers),
            canvas=canvas_state,
            pipeline=self._project_pipeline(),
            # The graph goes in whole, so reopening the project restores
            # something editable. The pipeline above stays what it always was —
            # the batch dialog reads it out of the file and knows nothing about
            # graphs.
            model=self.model_builder.graph.to_dict(),
            ui={"docks": {
                "log": self.log_dock.isVisible(),
                "session": self.session_dock.isVisible(),
                "plot": self.plot_dock.isVisible(),
                "statistics": self.stats_dock.isVisible(),
                "compare": self.compare_dock.isVisible(),
                "animation": self.time_player_dock.isVisible(),
                "model": self.model_builder.isVisible(),
            }},
        )

    def _project_pipeline(self):
        """The steps a project stores, session first and the model as a fallback.

        The session's recorded steps stay the answer whenever there are any, so
        nothing about an existing project changes. A project whose work was drawn
        rather than run would otherwise store an empty pipeline, and the batch
        dialog's "load a pipeline from a project file" would find nothing in a
        file that plainly contains a chain.
        """
        recorded = pipeline_from_steps(self.session_dock.log.steps)
        if recorded:
            return recorded
        try:
            return tuple(self.model_builder.export_pipeline())
        except Exception:
            # A pipeline is a convenience inside a project; failing to derive one
            # is not a reason to refuse to save the user's work.
            logger.warning("Could not compile the model into a stored pipeline",
                           exc_info=True)
            return ()

    def _apply_project_state(self, state, path):
        """Rebuild the window from a loaded project. Returns the missing layers."""
        canvas = self.geo_canvas
        canvas.clear_layers()

        found, missing = resolve_layers(state, path)
        stacked = []
        for index, (layer, source) in enumerate(found):
            if not canvas.load_file(source):
                logger.warning("Project layer '%s' could not be loaded from %s",
                               layer.name, source)
                continue
            # load_file names the layer after the file, which is not necessarily
            # the name the project stored — the file may have been renamed since.
            name = os.path.splitext(os.path.basename(source))[0]
            self._restore_layer(name, layer)
            stacked.append((layer.zorder, index, name))

        # The stored z-orders are the saved stacking order, highest on top. A
        # project written before layers had an order of their own stored one
        # z-order per layer *type*, so ties are broken by load order with the
        # later layer on top — which is how those maps were drawn.
        stacked.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
        canvas.set_layer_order([name for _zorder, _index, name in stacked])

        self._restore_canvas(state.canvas)
        self._restore_pipeline(state.pipeline)
        self._restore_model(state.model)
        self._restore_docks(state.ui.get("docks") or {})

        canvas.draw_idle()
        self.layer_manager.sync_order_from_canvas()
        return missing

    def _restore_layer(self, name, layer):
        """Put one loaded layer back into the state the project recorded."""
        canvas = self.geo_canvas
        prop = canvas.property_manager.get_layer_property(name)
        if prop is None:
            return

        if layer.style:
            prop.style.from_dict(layer.style)
        if layer.netcdf and prop.netcdf is not None:
            variable = layer.netcdf.get("current_variable")
            if variable:
                canvas.set_netcdf_variable(name, variable)
            index = layer.netcdf.get("current_time_index")
            if isinstance(index, int):
                canvas.set_netcdf_time_index(name, index)
            # The canonical redraw path for a variable/time change, the one the
            # animation player and the compare panel both use.
            canvas.update_netcdf_layer(name)

        # The z-order is not written onto the artist here: stacking is applied
        # once for the whole project by _apply_project_state, from the same
        # stored z-orders, so that the order it puts in the layer manager and
        # the order on the map cannot disagree.
        canvas.toggle_layer(name, layer.visible)
        canvas.update_layer_display(name)

    def _restore_canvas(self, canvas_state):
        """Point the map where it was and put the overlays back.

        Extent before projection, and both before the basemap: the projection's
        parameters are derived from the extent it is given, and a basemap chosen
        first would be fetched once for the old projection and again for the new.
        """
        self.geo_canvas.set_extent(list(canvas_state.extent))

        # An unknown name — a project written by a build with more projections
        # than this one — falls back to PlateCarree inside the canvas, so this
        # follows what was drawn rather than what was stored.
        self._show_projection(self.geo_canvas.set_projection(canvas_state.projection))

        combo_index = self.basemap_combo.findText(canvas_state.basemap)
        if combo_index >= 0:
            # Setting the combo rather than the canvas keeps the selector honest;
            # its own handler is what calls set_basemap.
            self.basemap_combo.setCurrentIndex(combo_index)

        # Routed through the window's own toggles so the View menu check marks
        # end up agreeing with what is drawn.
        self.toggle_graticule(canvas_state.graticule)
        self.toggle_scalebar(canvas_state.scalebar)
        self.toggle_colorbar(canvas_state.colorbar)
        if canvas_state.colorbar:
            self.set_colorbar_position(canvas_state.colorbar_position)

    def _restore_pipeline(self, pipeline):
        """Refill the session panel from a project's recorded steps.

        Recorded as successful: a project only ever stores steps that worked,
        and the panel would otherwise offer to export them commented out.
        """
        log = self.session_dock.log
        log.clear()
        for request in pipeline:
            log.record(SessionStep(request=request, status=OK))
        self.session_dock.refresh()

    def _restore_model(self, model):
        """Put the project's processing graph back into the model builder.

        A project written before 1.1 has no ``model`` key at all, which restores
        an empty graph — the right answer, and the reason the key was added as a
        minor version rather than a major one.
        """
        from ..core.model import ModelGraph

        if not model:
            self.model_builder.set_graph(ModelGraph())
            return
        try:
            self.model_builder.set_graph(ModelGraph.from_dict(model))
        except Exception:
            # from_dict already drops what it cannot read; anything reaching here
            # is a stored shape it did not expect at all. One unusable graph is
            # not a reason to fail a whole project load.
            logger.warning("The project's model could not be restored", exc_info=True)
            self.model_builder.set_graph(ModelGraph())

    def _restore_docks(self, visibility):
        """Reopen the panels the project had open. Absent keys change nothing."""
        toggles = {
            "log": self.toggle_log_dock,
            "session": self.toggle_session_dock,
            "plot": self.toggle_plot_dock,
            "statistics": self.toggle_stats_dock,
            "compare": self.toggle_compare_dock,
            "animation": self.toggle_animation_dock,
            "model": self.toggle_model_builder_window,
        }
        for key, toggle in toggles.items():
            if key in visibility:
                toggle(bool(visibility[key]))

    def _relocate_missing(self, missing, path):
        """Offer to find the files a project could not resolve.

        One question for the whole set, then one file dialog per layer: a
        project whose data moved usually had *all* of it move together, and
        asking nine times in a row about nine files in the same folder would be
        its own kind of unhelpful.
        """
        if not missing:
            return

        names = "\n".join(f"  • {layer.name} ({layer.source_path})" for layer in missing[:10])
        if len(missing) > 10:
            names += f"\n  … and {len(missing) - 10} more"

        answer = QMessageBox.question(
            self, "Files not found",
            f"{len(missing)} layer(s) in this project could not be found:\n\n{names}\n\n"
            "The rest of the project has been loaded. Locate them now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.output_console.append(
                f"⚠ {len(missing)} layer(s) skipped — their files could not be found")
            return

        for layer in missing:
            located, _ = QFileDialog.getOpenFileName(
                self, f"Locate '{layer.name}'", os.path.dirname(path),
                _canvas_file_dialog_filter(),
                options=QFileDialog.Option.DontUseNativeDialog,
            )
            if not located:
                continue  # skipping this one is a legitimate answer
            if self.geo_canvas.load_file(located):
                self._restore_layer(
                    os.path.splitext(os.path.basename(located))[0], layer)

    # ------------------------------------------------------------------
    # File menu actions
    # ------------------------------------------------------------------
    def new_project(self):
        """Start again: no layers, no recorded steps, no project file."""
        if not self._confirm_discard_project():
            return

        self.geo_canvas.clear_layers()
        self.session_dock.log.clear()
        self.session_dock.refresh()
        self.geo_canvas.draw_idle()
        self.layer_manager.update_layer_list()

        self._project_path = None
        self._project_dirty = False
        self._update_window_title()
        self.statusBar().showMessage("New project", 3000)

    def open_project(self):
        """Open a .ncx project chosen from a dialog."""
        if not self._confirm_discard_project():
            return

        path, _ = QFileDialog.getOpenFileName(
            self, "Open Project", "",
            f"NCExplorer project (*{PROJECT_SUFFIX});;All Files (*)",
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if path:
            self.load_project_file(path)

    def load_project_file(self, path):
        """Load one project file. True when it opened."""
        try:
            state = load_project(path)
        except ProjectError as exc:
            logger.error("Could not open the project %s: %s", path, exc)
            QMessageBox.critical(self, "Open Project", str(exc))
            return False

        missing = self._apply_project_state(state, path)

        self._project_path = path
        self._project_dirty = False
        self._update_window_title()
        self.recent_files.add(path)
        self.menu_bar.rebuild_recent_menu()

        loaded = len(state.layers) - len(missing)
        self.output_console.append(
            f"✓ Opened {os.path.basename(path)} — {loaded} layer(s) restored"
            + (f", {len(missing)} missing" if missing else "")
        )
        self.statusBar().showMessage(f"Opened {path}", 5000)

        # After the counts are reported, so the message box does not interrupt
        # the summary of what did load.
        self._relocate_missing(missing, path)
        return True

    def save_project(self):
        """Save to the current file, asking for one the first time. True on success."""
        if not self._project_path:
            return self.save_project_as()
        return self._write_project(self._project_path)

    def save_project_as(self):
        """Choose a file and save to it. True on success."""
        suggestion = self._project_path or suggest_project_path(
            os.path.dirname(self._first_layer_path() or "") or os.path.expanduser("~")
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Project As", suggestion,
            f"NCExplorer project (*{PROJECT_SUFFIX});;All Files (*)",
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if not path:
            return False
        return self._write_project(ensure_suffix(path))

    def _write_project(self, path):
        try:
            state = save_project(path, self._capture_project_state())
        except ProjectError as exc:
            logger.error("Could not save the project %s: %s", path, exc)
            QMessageBox.critical(self, "Save Project", str(exc))
            return False

        self._project_path = path
        self._project_dirty = False
        self._update_window_title()
        self.recent_files.add(path)
        self.menu_bar.rebuild_recent_menu()
        self.output_console.append(
            f"✓ Saved {os.path.basename(path)} — {len(state.layers)} layer(s), "
            f"{len(state.pipeline)} step(s)"
        )
        self.statusBar().showMessage(f"Project saved to {path}", 5000)
        return True

    def _first_layer_path(self):
        """A loaded layer's folder, as the default place to put a new project."""
        for record in self.geo_canvas.layers.values():
            source = record.get('filepath')
            if source and source != 'N/A':
                return source
        return ""

    # ------------------------------------------------------------------
    # Batch processing (core/batch.py, gui/batch_dialog.py)
    # ------------------------------------------------------------------
    def open_batch_dialog(self, checked=False, *, pipeline=None, source=""):
        """Apply a recorded pipeline to a folder of files.

        ``pipeline`` lets a caller supply the steps instead of leaving the dialog
        to take them from this session — which is how the model builder hands
        over a chain that was drawn rather than recorded. ``checked`` is the
        QAction's own argument and is ignored.
        """
        if self.execution.is_running():
            QMessageBox.information(
                self, "Batch Process",
                "Wait for the running operation to finish first.")
            return
        BatchDialog(self, pipeline=pipeline, source=source).exec()

    def toggle_model_builder(self):
        """Open or close the model builder — the shortcut's no-argument entry point."""
        self.toggle_model_builder_window(not self.model_builder.isVisible())

    def open_model_file(self):
        """Model ▸ Open Model — brings the builder up around the loaded graph."""
        self.toggle_model_builder_window(True)
        self.model_builder.open_model()

    def save_model_file(self):
        """Model ▸ Save Model."""
        self.toggle_model_builder_window(True)
        self.model_builder.save_model_as()

    def model_from_session(self):
        """Model ▸ Build from Session."""
        self.toggle_model_builder_window(True)
        self.model_builder.build_from_session()

    def model_add_inputs_from_folder(self):
        """Model ▸ Add Inputs from Folder."""
        self.toggle_model_builder_window(True)
        self.model_builder.add_inputs_from_folder()

    def export_model(self, fmt):
        """Model ▸ Export Model As — one of shell / makefile / notebook."""
        self.toggle_model_builder_window(True)
        self.model_builder.export_as(fmt)

    def run_model_over_folder(self):
        """Model ▸ Run Model over a Folder."""
        self.toggle_model_builder_window(True)
        self.model_builder.send_to_batch()

    def closeEvent(self, event):
        """Handle window closing with proper cleanup"""
        if not self._confirm_discard_project():
            event.ignore()
            return

        try:
            # Stop any CDO run still going, before the objects watching it are
            # torn down underneath it.
            if hasattr(self, 'execution'):
                self.execution.shutdown()

            # Every intermediate a model run wrote, deleted with the window that
            # produced them; nothing outside that run ever refers to them.
            if hasattr(self, 'model_builder'):
                self.model_builder.runner.cleanup()

            # Detach the log handler first: records emitted by the cleanup below
            # would otherwise be delivered to a widget Qt is dismantling.
            if hasattr(self, 'log_dock'):
                self.log_dock.detach()

            # Cleanup canvas first
            if hasattr(self, 'geo_canvas'):
                self.geo_canvas.cleanup()

            # Cleanup layer manager
            if hasattr(self, 'layer_manager'):
                self.layer_manager.cleanup()

            # Accept the close event
            event.accept()
        except Exception as e:
            logger.error("Error during cleanup: %s", e, exc_info=True)
            event.accept()

if __name__ == "__main__":
    import  sys
    app = QApplication(sys.argv)
    window = NCExplorerOperatorGUI()
    window.show()
    sys.exit(app.exec())
