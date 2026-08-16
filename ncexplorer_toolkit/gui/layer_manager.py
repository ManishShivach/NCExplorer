"""
Layer Manager Widget for NCExplorer Toolkit
Provides a user-friendly interface for managing NCExplorer layers
"""

import os
import logging
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QCheckBox, QLabel, QGroupBox, QMenu, QMessageBox,
    QFileDialog, QInputDialog, QTreeView, QComboBox, QApplication,
    QDialog, QDialogButtonBox, QFormLayout, QTextEdit, QAbstractItemView
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QDateTime
from PyQt6.QtGui import QAction, QIcon

from ..utils import regionmask
from .mask_dialog import MaskDialog

logger = logging.getLogger(__name__)


class LayerListWidget(QListWidget):
    """The layer list, which the user can drag into a different order.

    Qt's internal move is a remove-and-insert rather than a move, so there is no
    rowsMoved to listen for; the drop itself is the event that says the order
    changed. Signals are blocked across it because the re-inserted item is a new
    QListWidgetItem built from the drag's serialised roles, and setting its check
    state would otherwise arrive as a visibility change nobody asked for.
    """

    order_changed = pyqtSignal()

    def dropEvent(self, event):
        was_blocked = self.signalsBlocked()
        self.blockSignals(True)
        try:
            super().dropEvent(event)
        finally:
            self.blockSignals(was_blocked)
        self.order_changed.emit()


class LayerManager(QWidget):
    """Enhanced layer management widget with proper memory management"""

    # Signals
    layer_visibility_changed = pyqtSignal(str, bool)
    layer_removed = pyqtSignal(str)
    layer_properties_requested = pyqtSignal(str)
    layer_added = pyqtSignal(str)
    time_slider_requested = pyqtSignal(str)
    # The whole stack, topmost first, whenever the user reorders it.
    layer_order_changed = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.layers = {}

        # Memory management
        self._cleanup_timer = QTimer()
        self._cleanup_timer.timeout.connect(self.cleanup_unused_layers)
        self._cleanup_timer.start(30000)  # Cleanup every 30 seconds

        # Track layer visibility timestamps
        self._invisible_timestamps = {}
        self._max_invisible_time = 300000  # 5 minutes in milliseconds

        # Supported file formats
        self.supported_formats = {
            'Vector': ['.shp', '.geojson', '.kml', '.gpx'],
            'Raster': ['.tif', '.tiff', '.jpg', '.png', '.gif'],
            'NetCDF': ['.nc', '.nc4', '.netcdf'],
            'GRIB': ['.grb', '.grib', '.grb2']
        }

        self.setup_ui()
        self.connect_signals()

    def setup_ui(self):
        """Setup the user interface"""
        layout = QVBoxLayout(self)

        # Title
        title_label = QLabel("Layer Manager")
        title_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(title_label)

        # File type filter
        filter_group = QGroupBox("File Type Filter")
        filter_layout = QHBoxLayout(filter_group)
        self.format_combo = QComboBox()
        self.format_combo.addItems(['All Files'] + list(self.supported_formats.keys()))
        filter_layout.addWidget(QLabel("Type:"))
        filter_layout.addWidget(self.format_combo)
        layout.addWidget(filter_group)

        # Layer list. Top of the list is the top of the map: the order here is
        # the order the canvas draws in, and dragging an entry restacks the map.
        self.layer_list = LayerListWidget()
        self.layer_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.layer_list.customContextMenuRequested.connect(self.show_context_menu)
        self.layer_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.layer_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.layer_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        # A drop lands between two layers; it never replaces the one under the
        # pointer. Stated here rather than by clearing ItemIsDropEnabled on each
        # item, because item flags are not carried across an internal move —
        # they would hold for a layer until the first time it was dragged.
        self.layer_list.setDragDropOverwriteMode(False)
        self.layer_list.setToolTip("Drag a layer to restack it — the top of the "
                                   "list draws on top of the map.")
        layout.addWidget(self.layer_list)

        # Stacking controls
        order_layout = QHBoxLayout()
        self.move_up_btn = QPushButton("▲ Up")
        self.move_up_btn.setToolTip("Draw the selected layer one place higher")
        self.move_up_btn.clicked.connect(self.move_selected_up)
        self.move_down_btn = QPushButton("▼ Down")
        self.move_down_btn.setToolTip("Draw the selected layer one place lower")
        self.move_down_btn.clicked.connect(self.move_selected_down)
        order_layout.addWidget(self.move_up_btn)
        order_layout.addWidget(self.move_down_btn)
        layout.addLayout(order_layout)

        # Enhanced layer controls
        controls_layout = QVBoxLayout()

        # Primary controls row
        primary_controls = QHBoxLayout()
        self.add_layer_btn = QPushButton("Add Layer")
        self.add_layer_btn.clicked.connect(self.add_layer_dialog)
        self.remove_layer_btn = QPushButton("Remove Layer")
        self.remove_layer_btn.clicked.connect(self.remove_selected_layer)

        primary_controls.addWidget(self.add_layer_btn)
        primary_controls.addWidget(self.remove_layer_btn)

        # Secondary controls row
        secondary_controls = QHBoxLayout()
        self.info_btn = QPushButton("Info")
        self.info_btn.setToolTip("Show layer information")
        self.info_btn.clicked.connect(self.show_selected_info)

        self.zoom_btn = QPushButton("Zoom To")
        self.zoom_btn.setToolTip("Zoom to layer extent")
        self.zoom_btn.clicked.connect(self.zoom_to_selected)

        # Opacity and the colour scale are symbology, and symbology lives on the
        # layer's own properties panel; this is the way there for the layer that
        # is selected here.
        self.symbology_btn = QPushButton("Symbology")
        self.symbology_btn.setToolTip("Edit this layer's opacity, colormap and symbols")
        self.symbology_btn.clicked.connect(self.show_selected_symbology)

        secondary_controls.addWidget(self.info_btn)
        secondary_controls.addWidget(self.zoom_btn)
        secondary_controls.addWidget(self.symbology_btn)

        controls_layout.addLayout(primary_controls)
        controls_layout.addLayout(secondary_controls)
        layout.addLayout(controls_layout)

        # Layer statistics
        stats_group = QGroupBox("Layer Statistics")
        stats_layout = QVBoxLayout(stats_group)
        self.stats_label = QLabel("No layers loaded")
        stats_layout.addWidget(self.stats_label)
        layout.addWidget(stats_group)

        # Memory usage indicator
        self.memory_label = QLabel("Memory: 0 MB")
        layout.addWidget(self.memory_label)

    def connect_signals(self):
        """Connect internal signals"""
        self.layer_list.itemChanged.connect(self.on_layer_item_changed)
        self.layer_list.itemDoubleClicked.connect(self.on_layer_item_double_clicked)
        self.layer_list.order_changed.connect(self.commit_layer_order)
        self.layer_list.currentItemChanged.connect(self.on_current_layer_changed)
        self.format_combo.currentTextChanged.connect(self.filter_layers)
        self.refresh_selection_controls()

    # ------------------------------------------------------------------
    # Stacking order
    # ------------------------------------------------------------------
    def layer_names_top_first(self):
        """The layer names as the list shows them: topmost layer first."""
        names = []
        for row in range(self.layer_list.count()):
            name = self.layer_list.item(row).data(Qt.ItemDataRole.UserRole)
            if name:
                names.append(name)
        return names

    def commit_layer_order(self):
        """Announce the list's order after the user has changed it.

        ``self.layers`` is re-keyed to match, bottom layer first, because that
        dict is what update_layer_list rebuilds the list from — leaving it alone
        would undo the reorder the next time anything refreshed the list.
        """
        names = self.layer_names_top_first()
        reordered = {name: self.layers[name] for name in reversed(names)
                     if name in self.layers}
        # Anything the list did not show (filtered out, or mid-load) keeps its
        # entry rather than being dropped from the manager altogether.
        for name, info in self.layers.items():
            if name not in reordered:
                reordered[name] = info
        self.layers = reordered

        # A layer dragged to either end can no longer move that way.
        self.refresh_selection_controls()
        self.layer_order_changed.emit(names)
        logger.debug("Layer order is now (top first): %s", names)
        return names

    def move_selected_up(self):
        """Move the selected layer one place up the stack."""
        self.move_selected(-1)

    def move_selected_down(self):
        """Move the selected layer one place down the stack."""
        self.move_selected(+1)

    def move_selected(self, offset):
        """Move the selected layer by ``offset`` rows, if there is room.

        Silent rather than complaining when there is nothing to move: the two
        buttons are disabled without a selection, and at the end of the stack
        they are disabled in the direction that has nowhere to go.
        """
        row = self.layer_list.currentRow()
        if row < 0:
            return

        target = row + offset
        if not 0 <= target < self.layer_list.count():
            return

        # takeItem/insertItem moves the item object itself, so its check state
        # and stored layer name travel with it and no itemChanged is emitted.
        item = self.layer_list.takeItem(row)
        self.layer_list.insertItem(target, item)
        self.layer_list.setCurrentItem(item)
        self.commit_layer_order()

    def move_layer_to_row(self, layer_name, target_row):
        """Move one named layer to a given row of the list, 0 being the top."""
        for row in range(self.layer_list.count()):
            if self.layer_list.item(row).data(Qt.ItemDataRole.UserRole) != layer_name:
                continue
            target_row = max(0, min(target_row, self.layer_list.count() - 1))
            if target_row == row:
                return
            item = self.layer_list.takeItem(row)
            self.layer_list.insertItem(target_row, item)
            self.layer_list.setCurrentItem(item)
            self.commit_layer_order()
            return

    def sync_order_from_canvas(self):
        """Adopt the canvas' stacking order, topmost first.

        Used after a project is restored, where the layers come back in the
        order their files happened to load rather than the order they were
        stacked in when the project was saved.
        """
        canvas = getattr(self.parent_window, 'geo_canvas', None)
        if canvas is None or not hasattr(canvas, 'layer_stacking_order'):
            self.update_layer_list()
            return

        ordered = [name for name in canvas.layer_stacking_order() if name in self.layers]
        ordered += [name for name in self.layers if name not in ordered]
        self.layers = {name: self.layers[name] for name in reversed(ordered)}
        self.update_layer_list()

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------
    def on_current_layer_changed(self, *_args):
        """Follow the selection with the controls that act on one layer."""
        self.refresh_selection_controls()

    def selected_layer_name(self):
        """The selected layer's name, or None when nothing is selected."""
        item = self.layer_list.currentItem()
        if item is None:
            return None
        name = item.data(Qt.ItemDataRole.UserRole)
        return name if name in self.layers else None

    def refresh_selection_controls(self):
        """Enable the controls that need a selected layer to act on."""
        layer_name = self.selected_layer_name()
        has_selection = layer_name is not None

        self.move_up_btn.setEnabled(has_selection and self.layer_list.currentRow() > 0)
        self.move_down_btn.setEnabled(
            has_selection and self.layer_list.currentRow() < self.layer_list.count() - 1
        )
        self.symbology_btn.setEnabled(has_selection)

    def show_selected_symbology(self):
        """Open the selected layer's properties, on its symbology."""
        layer_name = self.selected_layer_name()
        if layer_name is None:
            QMessageBox.information(self, "No Selection", "Please select a layer first.")
            return
        self.layer_properties_requested.emit(layer_name)

    def add_layer_dialog(self):
        """Open the file dialog to add a new layer"""
        options = QFileDialog.Option.DontUseNativeDialog

        # Build file filter based on supported formats
        filters = []
        for format_name, extensions in self.supported_formats.items():
            ext_filter = f"{format_name} Files ({' '.join(['*' + ext for ext in extensions])})"
            filters.append(ext_filter)
        filters.append("All Files (*)")

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Add Layer",
            "",
            ";;".join(filters),
            options=options
        )

        if file_path:
            self.add_layer_from_file(file_path)

    def add_layer_from_file(self, filepath):
        """Add a layer from a file path"""
        try:
            # Validate file
            if not self.validate_file(filepath):
                return

            # Get layer name
            layer_name = os.path.splitext(os.path.basename(filepath))[0]

            # Check for duplicates
            if layer_name in self.layers:
                layer_name = self.get_unique_layer_name(layer_name)

            # Add to parent's canvas if available
            if hasattr(self.parent_window, 'geo_canvas'):
                # Use the new consolidated canvas structure
                self.parent_window.geo_canvas.load_file(filepath)
                # Layer will be added via signal
            else:
                # Add directly to the layer manager
                self.add_layer(layer_name, self.get_file_type(filepath), filepath)

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to add layer: {str(e)}")
            logger.error(f"Failed to add layer from {filepath}: {str(e)}")

    def validate_file(self, filepath):
        """Validate file format and accessibility"""
        # Check if file exists
        if not os.path.exists(filepath):
            QMessageBox.warning(self, "File Not Found", f"File does not exist: {filepath}")
            return False

        # Check if file is readable
        if not os.access(filepath, os.R_OK):
            QMessageBox.warning(self, "Permission Error", f"Cannot read file: {filepath}")
            return False

        # Check file format
        ext = os.path.splitext(filepath)[1].lower()
        supported_exts = []
        for exts in self.supported_formats.values():
            supported_exts.extend(exts)

        if ext not in supported_exts:
            QMessageBox.warning(self, "Unsupported Format", f"Unsupported file format: {ext}")
            return False

        return True

    def get_file_type(self, filepath):
        """Determine file type from extension"""
        ext = os.path.splitext(filepath)[1].lower()

        for format_name, extensions in self.supported_formats.items():
            if ext in extensions:
                return format_name.lower()

        return 'unknown'

    def get_unique_layer_name(self, base_name):
        """Generate a unique layer name"""
        counter = 1
        while f"{base_name}_{counter}" in self.layers:
            counter += 1
        return f"{base_name}_{counter}"

    def add_layer(self, layer_name, layer_type, filepath):
        """Add a new layer to the manager"""
        layer_info = {
            'name': layer_name,
            'type': layer_type,
            'filepath': filepath,
            'visible': True,
            'style': self.get_default_style(layer_type),
            'created': QDateTime.currentDateTime()
        }

        self.layers[layer_name] = layer_info
        self.update_layer_list()
        self.update_statistics()

        # Remove from invisible tracking if present
        if layer_name in self._invisible_timestamps:
            del self._invisible_timestamps[layer_name]

        self.layer_added.emit(layer_name)
        logger.info(f"Added layer: {layer_name}")

    def get_default_style(self, layer_type):
        """Get default styling for a layer type"""
        styles = {
            'vector': {'color': '#3388ff', 'linewidth': 1.0, 'alpha': 0.8},
            'raster': {'cmap': 'viridis', 'alpha': 0.8},
            'netcdf': {'cmap': 'RdYlBu', 'alpha': 0.8},
            'grib': {'cmap': 'RdYlBu', 'alpha': 0.8}
        }
        return styles.get(layer_type, {'color': '#3388ff', 'alpha': 0.8})

    def update_layer_list(self):
        """Refresh the entire layer list widget based on the current self.layers dict.

        ``self.layers`` runs bottom of the stack first, so reversing it puts the
        topmost layer at the top of the list — where both the drag-and-drop
        order and the canvas' z-orders expect it.
        """
        self.layer_list.clear()
        for layer_name in reversed(list(self.layers.keys())):
            item = QListWidgetItem(layer_name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setData(Qt.ItemDataRole.UserRole, layer_name)  # Store layer name in UserRole
            is_visible = self.layers[layer_name].get('visible', True)
            item.setCheckState(Qt.CheckState.Checked if is_visible else Qt.CheckState.Unchecked)
            self.layer_list.addItem(item)
        self.refresh_selection_controls()

    def filter_layers(self):
        """Filter layers based on selected type"""
        self.update_layer_list()

    def add_layer_to_list(self, layer_name):
        """Slot to add a single layer to the GUI list. Inserts at the top."""
        if layer_name in self.layers:
            return

        # Get layer info from canvas
        if hasattr(self.parent_window, 'geo_canvas'):
            canvas_layers = self.parent_window.geo_canvas.layers
            layer_info = canvas_layers.get(layer_name)

            if not layer_info:
                return

            # Get file type from layer info
            layer_type = layer_info.get('type', 'unknown')
            filepath = layer_info.get('data', '')

            self.layers[layer_name] = {
                "name": layer_name,
                "type": layer_type,
                "visible": layer_info.get('visible', True),
                "filepath": filepath,
                "style": self.get_default_style(layer_type),
                "created": QDateTime.currentDateTime()
            }

            # Insert new layer at the top of the list — which is also where the
            # canvas stacks it, so the two agree without being told to.
            item = QListWidgetItem(layer_name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setData(Qt.ItemDataRole.UserRole, layer_name)
            item.setCheckState(Qt.CheckState.Checked)
            self.layer_list.insertItem(0, item)

            self.update_statistics()
            self.refresh_selection_controls()
            logger.info(f"Layer '{layer_name}' added to GUI list.")

    def remove_layer_from_list(self, layer_name):
        """Slot to remove a single layer from the GUI list."""
        if layer_name in self.layers:
            del self.layers[layer_name]
            self.update_layer_list()
            self.update_statistics()
            logger.info(f"Layer '{layer_name}' removed from GUI list.")

    def on_layer_item_changed(self, item):
        """Handle layer item check state changes"""
        layer_name = item.data(Qt.ItemDataRole.UserRole)
        if layer_name and layer_name in self.layers:
            visible = item.checkState() == Qt.CheckState.Checked
            self.set_layer_visibility(layer_name, visible)

    def set_layer_visibility(self, layer_name, visible):
        """Set layer visibility and track for cleanup"""
        if layer_name not in self.layers:
            return

        self.layers[layer_name]['visible'] = visible

        if visible:
            # Remove from invisible tracking
            if layer_name in self._invisible_timestamps:
                del self._invisible_timestamps[layer_name]
        else:
            # Track when layer became invisible
            self._invisible_timestamps[layer_name] = QDateTime.currentMSecsSinceEpoch()

        self.layer_visibility_changed.emit(layer_name, visible)

    def remove_selected_layer(self):
        """Remove the currently selected layer"""
        current_item = self.layer_list.currentItem()
        if not current_item:
            QMessageBox.information(self, "No Selection", "Please select a layer first.")
            return

        layer_name = current_item.data(Qt.ItemDataRole.UserRole)
        if layer_name:
            self.remove_layer(layer_name)

    def remove_layer(self, layer_name):
        """Remove layer with proper cleanup"""
        if layer_name not in self.layers:
            return

        # Confirm removal
        reply = QMessageBox.question(
            self,
            'Remove Layer',
            f'Are you sure you want to remove layer "{layer_name}"?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            del self.layers[layer_name]

            # Clean up tracking
            if layer_name in self._invisible_timestamps:
                del self._invisible_timestamps[layer_name]

            self.update_layer_list()
            self.update_statistics()
            self.layer_removed.emit(layer_name)

            logger.info(f"Removed layer: {layer_name}")

    def cleanup_unused_layers(self):
        """Clean up unused layer resources"""
        current_time = QDateTime.currentMSecsSinceEpoch()
        layers_to_remove = []

        for layer_name, invisible_time in list(self._invisible_timestamps.items()):
            if current_time - invisible_time > self._max_invisible_time:
                layers_to_remove.append(layer_name)

        for layer_name in layers_to_remove:
            logger.info(f"Auto-removing invisible layer: {layer_name}")
            if layer_name in self.layers:
                del self.layers[layer_name]
                self.layer_removed.emit(layer_name)

        if layers_to_remove:
            self.update_layer_list()
            self.update_statistics()

    def show_context_menu(self, position):
        """Show enhanced context menu for layer operations"""
        item = self.layer_list.itemAt(position)
        if not item:
            return

        layer_name = item.data(Qt.ItemDataRole.UserRole)
        if not layer_name:
            return

        menu = QMenu(self)

        # Layer Info action
        info_action = QAction("Layer Info", self)
        info_action.setToolTip("Show basic layer information")
        info_action.triggered.connect(lambda: self.show_layer_info(layer_name))
        menu.addAction(info_action)

        # Timestamp
        timestamp_action = QAction("Timestamps", self)
        timestamp_action.triggered.connect(lambda: self.time_slider_requested.emit(layer_name))
        menu.addAction(timestamp_action)

        # Property action
        properties_action = QAction("Properties", self)
        properties_action.setToolTip("Open layer properties editor")
        properties_action.triggered.connect(lambda: self.layer_properties_requested.emit(layer_name))
        menu.addAction(properties_action)

        menu.addSeparator()

        # Zoom to Layer action
        zoom_action = QAction("Zoom to Layer", self)
        zoom_action.triggered.connect(lambda: self.zoom_to_layer(layer_name))
        menu.addAction(zoom_action)

        # Toggle Visibility
        layer_info = self.layers.get(layer_name)
        if layer_info:
            visibility_text = "Hide Layer" if layer_info['visible'] else "Show Layer"
            visibility_action = QAction(visibility_text, self)
            visibility_action.triggered.connect(
                lambda: self.set_layer_visibility(layer_name, not layer_info['visible'])
            )
            menu.addAction(visibility_action)

        menu.addSeparator()

        # Stacking. The single-step moves are on the buttons under the list;
        # these two are the jumps that a drag would otherwise have to make.
        row = self.layer_list.row(item)
        front_action = QAction("Bring to Front", self)
        front_action.setToolTip("Draw this layer above every other layer")
        front_action.setEnabled(row > 0)
        front_action.triggered.connect(lambda: self.move_layer_to_row(layer_name, 0))
        menu.addAction(front_action)

        back_action = QAction("Send to Back", self)
        back_action.setToolTip("Draw this layer below every other layer")
        back_action.setEnabled(row < self.layer_list.count() - 1)
        back_action.triggered.connect(
            lambda: self.move_layer_to_row(layer_name, self.layer_list.count() - 1)
        )
        menu.addAction(back_action)

        # Polygon layers can drive a mask over any loaded NetCDF file, so the
        # entry only appears where it means something.
        if self.is_polygon_layer(layer_name):
            menu.addSeparator()
            mask_action = QAction("Mask NetCDF by this polygon…", self)
            mask_action.setToolTip("Clip or mask a NetCDF file to this polygon")
            mask_action.triggered.connect(lambda: self.mask_by_polygon(layer_name))
            menu.addAction(mask_action)

        menu.addSeparator()

        # Rename action
        rename_action = QAction("Rename Layer", self)
        rename_action.triggered.connect(lambda: self.rename_layer(layer_name))
        menu.addAction(rename_action)

        # Remove action
        remove_action = QAction("Remove Layer", self)
        remove_action.triggered.connect(lambda: self.remove_layer(layer_name))
        menu.addAction(remove_action)

        menu.exec(self.layer_list.mapToGlobal(position))

    def canvas_record(self, layer_name):
        """The canvas' own record for a layer, which holds the real geometry.

        This widget keeps a lightweight copy of each layer (name, type, path);
        the artists, datasets and GeoDataFrames live on the canvas.
        """
        canvas = getattr(self.parent_window, 'geo_canvas', None)
        if canvas is None:
            return None
        try:
            return canvas.layers.get(layer_name)
        except Exception:
            return None

    def is_polygon_layer(self, layer_name) -> bool:
        """True when a layer carries polygons that could mask a NetCDF file."""
        record = self.canvas_record(layer_name)
        if not record or record.get('type') not in ('polygons', 'shapefile'):
            return False
        try:
            return bool(regionmask.layer_features(self.parent_window.geo_canvas, layer_name))
        except Exception as exc:
            logger.debug("Could not read polygons from '%s': %s", layer_name, exc)
            return False

    def mask_by_polygon(self, layer_name):
        """Open the masking dialog for one polygon layer."""
        if self.parent_window is None or not hasattr(self.parent_window, 'NCExplorer'):
            QMessageBox.warning(self, "Not Available",
                                "The processing engine is not available in the main window.")
            return
        if not self.is_polygon_layer(layer_name):
            QMessageBox.warning(self, "No Polygons",
                                f"'{layer_name}' has no polygon features to mask with.")
            return

        try:
            MaskDialog(self.parent_window, layer_name, self).exec()
        except Exception as exc:
            logger.error("Could not open the mask dialog: %s", exc, exc_info=True)
            QMessageBox.critical(self, "Error", f"Could not open the mask dialog:\n{exc}")

    def show_layer_info(self, layer_name):
        """Show detailed layer information, using CDO for dataset-backed layers."""
        if layer_name not in self.layers:
            QMessageBox.warning(self, "Layer Not Found", f"Layer '{layer_name}' not found in manager.")
            return

        layer_info = self.layers[layer_name]

        # Safely get values with defaults
        name = layer_info.get('name', layer_name)
        layer_type = layer_info.get('type', 'unknown').title()
        filepath = layer_info.get('filepath', 'N/A')
        visible = layer_info.get('visible', True)
        created = layer_info.get('created', QDateTime.currentDateTime())

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Layer Info - {layer_name}")
        dialog.resize(560, 560)

        layout = QVBoxLayout(dialog)

        summary_group = QGroupBox("Layer Summary")
        summary_layout = QFormLayout(summary_group)
        summary_layout.addRow("Name:", QLabel(name))
        summary_layout.addRow("Type:", QLabel(layer_type))
        summary_layout.addRow("File:", QLabel(filepath))
        summary_layout.addRow("Status:", QLabel("Visible" if visible else "Hidden"))
        summary_layout.addRow(
            "Created:",
            QLabel(created.toString() if hasattr(created, 'toString') else str(created))
        )
        layout.addWidget(summary_group)

        info_payload, error_message = self._get_cdo_info_payload(filepath)

        if info_payload:
            grid_group = QGroupBox("Grid Description")
            grid_layout = QFormLayout(grid_group)
            for key in ["gridsize", "xsize", "ysize", "xname", "xfirst", "xinc", "yfirst", "yinc"]:
                grid_layout.addRow(f"{key}:", QLabel(info_payload["grid"].get(key, "N/A")))
            layout.addWidget(grid_group)

            metrics_group = QGroupBox("Dataset Metrics")
            metrics_layout = QFormLayout(metrics_group)
            for key, label in [
                ("ndate", "Dates"),
                ("ngridpoints", "Grid Points"),
                ("ngrids", "Grids"),
                ("nlevel", "Levels"),
                ("nmon", "Months"),
                ("nyear", "Years"),
                ("npar", "Parameters"),
                ("ntime", "Timesteps"),
            ]:
                metrics_layout.addRow(f"{label}:", QLabel(info_payload["counts"].get(key, "N/A")))
            layout.addWidget(metrics_group)

            raw_group = QGroupBox("Raw `operator- griddes` Output")
            raw_layout = QVBoxLayout(raw_group)
            raw_text = QTextEdit()
            raw_text.setReadOnly(True)
            raw_text.setMaximumHeight(170)
            raw_text.setPlainText(info_payload["raw_griddes"])
            raw_layout.addWidget(raw_text)
            layout.addWidget(raw_group)
        elif error_message:
            error_label = QLabel(error_message)
            error_label.setWordWrap(True)
            layout.addWidget(error_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(dialog.accept)
        layout.addWidget(buttons)

        dialog.exec()

    def _get_cdo_info_payload(self, filepath):
        """Run a small set of CDO info operators and parse their output."""
        if not filepath or not os.path.isfile(filepath):
            return None, "No dataset file is available for this layer."

        if not self.parent_window or not hasattr(self.parent_window, "NCExplorer"):
            return None, "The processing engine is not available in the main window."

        integration = self.parent_window.NCExplorer
        commands = ["griddes", "ndate", "ngridpoints", "ngrids", "nlevel", "nmon", "nyear", "npar", "ntime"]
        results = {}

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for command in commands:
                operator = getattr(integration, command, None)
                if operator is None:
                    return None, f"Operator '{command}' is not available."

                result = operator(filepath)
                if not result.success:
                    stderr = result.stderr.strip() or "Unknown error."
                    return None, f"Failed to run `{command}` on:\n{filepath}\n\n{stderr}"

                results[command] = result.stdout.strip()
        finally:
            QApplication.restoreOverrideCursor()

        grid_data = self._parse_griddes_output(results.get("griddes", ""))
        count_data = {
            command: " ".join(results.get(command, "").split()) or "N/A"
            for command in commands
            if command != "griddes"
        }

        return {
            "grid": grid_data,
            "counts": count_data,
            "raw_griddes": results.get("griddes", "").strip() or "N/A",
        }, None

    @staticmethod
    def _parse_griddes_output(output):
        """Parse selected fields from `cdo griddes` output."""
        wanted_keys = {"gridsize", "xsize", "ysize", "xfirst", "xinc", "yfirst", "yinc", "xname"}
        parsed = {key: "N/A" for key in wanted_keys}

        for line in output.splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key in wanted_keys:
                parsed[key] = value.strip().strip('"')

        return parsed

    def on_layer_item_double_clicked(self, item):
        """Open detailed info when a layer is double-clicked."""
        layer_name = item.data(Qt.ItemDataRole.UserRole)
        if layer_name:
            self.show_layer_info(layer_name)

    def zoom_to_layer(self, layer_name):
        """Zoom canvas to layer extent"""
        # Emit signal for parent to handle
        if hasattr(self.parent_window, 'zoom_to_layer'):
            self.parent_window.zoom_to_layer(layer_name)

    def rename_layer(self, layer_name):
        """Rename a layer"""
        if layer_name not in self.layers:
            return

        new_name, ok = QInputDialog.getText(
            self,
            'Rename Layer',
            'Enter new layer name:',
            text=layer_name
        )

        if ok and new_name and new_name != layer_name:
            if new_name in self.layers:
                QMessageBox.warning(self, "Name Conflict", f"Layer name '{new_name}' already exists.")
                return

            # Update layer info
            layer_info = self.layers[layer_name]
            layer_info['name'] = new_name
            self.layers[new_name] = layer_info
            del self.layers[layer_name]

            # Update visibility tracking
            if layer_name in self._invisible_timestamps:
                self._invisible_timestamps[new_name] = self._invisible_timestamps[layer_name]
                del self._invisible_timestamps[layer_name]

            self.update_layer_list()
            logger.info(f"Renamed layer: {layer_name} -> {new_name}")

    def show_selected_info(self):
        """Show info for the selected layer"""
        current_item = self.layer_list.currentItem()
        if not current_item:
            QMessageBox.information(self, "No Selection", "Please select a layer first.")
            return

        layer_name = current_item.data(Qt.ItemDataRole.UserRole)
        if layer_name:
            self.show_layer_info(layer_name)

    def zoom_to_selected(self):
        """Zoom to the selected layer"""
        current_item = self.layer_list.currentItem()
        if not current_item:
            QMessageBox.information(self, "No Selection", "Please select a layer first.")
            return

        layer_name = current_item.data(Qt.ItemDataRole.UserRole)
        if layer_name:
            self.zoom_to_layer(layer_name)

    def update_statistics(self):
        """Update layer statistics display"""
        total_layers = len(self.layers)
        visible_layers = sum(1 for layer in self.layers.values() if layer['visible'])

        stats_text = f"Total: {total_layers} | Visible: {visible_layers}"
        self.stats_label.setText(stats_text)

        # Update memory usage (simplified)
        memory_mb = total_layers * 2  # Estimate
        self.memory_label.setText(f"Memory: ~{memory_mb} MB")

    def get_layer_info(self, layer_name):
        """Get information about a layer"""
        if layer_name not in self.layers:
            return None

        layer = self.layers[layer_name]
        return {
            'name': layer['name'],
            'type': layer['type'],
            'filepath': layer.get('filepath', ''),
            'visible': layer['visible'],
            'style': layer['style'],
            'created': layer['created'].toString()
        }

    def clear_all_layers(self):
        """Clear all layers"""
        if not self.layers:
            return

        reply = QMessageBox.question(
            self,
            'Clear All Layers',
            'Are you sure you want to remove all layers?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.layers.clear()
            self._invisible_timestamps.clear()
            self.update_layer_list()
            self.update_statistics()
            logger.info("Cleared all layers")

    def cleanup(self):
        """Cleanup method for proper resource management"""
        timer = getattr(self, '_cleanup_timer', None)
        if timer is None:
            return
        try:
            timer.stop()
        except RuntimeError:
            # __del__ can run after Qt has already destroyed the timer along
            # with the widget, and touching it then raises rather than being
            # the no-op the caller wants.
            pass

    def __del__(self):
        """Cleanup on destruction"""
        self.cleanup()
