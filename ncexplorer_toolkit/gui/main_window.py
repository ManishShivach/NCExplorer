from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLineEdit, QPushButton, QLabel, QTextEdit, QFileDialog,
    QFormLayout, QScrollArea, QMessageBox, QDockWidget,
    QListWidget, QListWidgetItem, QAbstractItemView, QSizePolicy,
    QComboBox
)
import os

from .toolbar import NCExplorerToolbar
from ..geocanvas.canvas import GeoCanvas
from ..geocanvas.properties import LayerPropertyEditor
from .widgets import QIntValidator, QDoubleValidator
from ..core.nc_integration import create_NCExplorer_integration
from .menubar import MenuBar
from .layer_manager import LayerManager
from .file_explorer import FileExplorer
from ..core.categories import OPERATOR_CATEGORIES, NCExplorerCategory

import logging
logger = logging.getLogger(__name__)

class MultiFileInputWidget(QWidget):
    """
    File list widget with drag-and-drop reordering.

    nin == 2  → no folder button, max 2 files (exact pair selection)
    nin == 3  → folder button + add file, no cap
    nin == -1 → folder button + add file, no cap
    """

    FILE_FILTER   = (".nc", ".grb", ".grib", ".tif", ".tiff")
    FILE_DIALOG_F = "Supported Files (*.nc *.grb *.grib *.tif *.tiff);;All Files (*)"

    def __init__(self, nin: int = -1, parent=None):
        super().__init__(parent)
        self._nin = nin          # expected number of input files (-1 = unlimited)
        self._build_ui()

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
            hint_text = "Add any number of files — drag rows to reorder (order = CDO argument order)"

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
    def __init__(self):
        super().__init__()

        # Enable debug logging
        import logging
        logging.basicConfig(level=logging.DEBUG)
        logger = logging.getLogger('NCExplorerIntegration')
        logger.setLevel(logging.DEBUG)

        # Initialize basic properties first
        self.current_layer = None
        self.current_output_file = None
        self.parameter_widgets = {}
        self.current_operator = None
        self.last_stdout = ""
        self.debug_mode = False

        # Create NCExplorer integration
        self.NCExplorer = create_NCExplorer_integration()

        # Basic window setup
        self.setWindowTitle("Geospatial Analysis Software")
        self.setGeometry(100, 100, 1200, 800)

        # Create a menu bar
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

        # Create a file explorer dock
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
            print(f"[Click Value Error] {e}")

        # Show output
        self.statusBar().showMessage(
            f"Clicked at: {lat:.4f}, {lon:.4f} | Values: {values}"
        )

    def handle_layer_added(self, layer_name):
        """Enhanced layer addition handler with auto-fit"""
        layer_prop = self.geo_canvas.property_manager.get_layer_property(layer_name)
        self.current_layer = layer_name

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
            logger.info(f"Layer '{layer_name}' removed successfully")

        except Exception as e:
            error_msg = f"Error removing layer: {str(e)}"
            logger.error(error_msg)
            self.statusBar().showMessage(error_msg, 5000)

    def handle_file_loaded(self, filepath, file_type):
        """Handle successful file loading"""
        filename = os.path.basename(filepath)
        self.statusBar().showMessage(f"Loaded {file_type} file: {filename}", 3000)

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

    def visualize_file(self, filepath):
        """Enhanced file visualization with new structure"""
        try:
            success = self.geo_canvas.load_file(filepath)
            if success is not None:
                return True
            else:
                return False
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
        """Handle individual property changes"""
        if self.current_layer:
            # Update the layer property using the new property manager
            self.geo_canvas.property_manager.update_property(
                self.current_layer, property_name, value
            )
            if hasattr(self, 'property_editor'):
                self.property_editor.refresh_current_layer()

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

                logger.debug(f"Layer visibility changed: {layer_name} -> {visible}")
            else:
                logger.warning(f"Layer '{layer_name}' not found in canvas")

        except Exception as e:
            error_msg = f"Failed to change layer visibility: {str(e)}"
            logger.error(error_msg)
            self.statusBar().showMessage(error_msg, 5000)

    def handle_layer_properties(self, layer_name):
        """Handle explicit property request from layer manager"""
        self.current_layer = layer_name

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

        try:
            from .core.categories import OPERATOR_SIGNATURES
        except ImportError:
            from ncexplorer_toolkit.core.categories import OPERATOR_SIGNATURES
        op_nin, op_nout = OPERATOR_SIGNATURES.get(operator, (1, 1))

        # Create input fields for each parameter
        multi_file_added = False  # guard so MultiFileInputWidget is only added once

        for param in params:
            if len(param) == 4:
                param_type, label, placeholder, choices = param
            else:
                param_type, label, placeholder = param
                choices = ()

            param_label = QLabel(label)

            if param_type == "file" and "Input" in label:
                # Multi-input operators → one shared MultiFileInputWidget
                if op_nin > 1 or op_nin == -1:
                    if not multi_file_added:
                        input_widget = MultiFileInputWidget(nin=op_nin)
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
                    browse_btn.clicked.connect(lambda _, le=line_edit: self.browse_file(le))
                    layout.addWidget(line_edit)
                    layout.addWidget(browse_btn)
                    input_widget = widget

            elif param_type == "file" and "Output" in label:
                # Output file — original browse widget
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

            elif param_type == "paramgrid":
                # Grid descriptor: file path OR a preset grid name.
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
                browse_btn.clicked.connect(lambda _, le=line_edit: self.browse_file(le))
                layout.addWidget(line_edit)
                layout.addWidget(preset_combo)
                layout.addWidget(browse_btn)
                input_widget = widget

            elif param_type == "select":
                input_widget = QComboBox()
                input_widget.addItem("")
                for choice in choices:
                    input_widget.addItem(str(choice))

            else:  # string
                input_widget = QLineEdit()
                input_widget.setPlaceholderText(placeholder)

            self.params_layout.addRow(param_label, input_widget)
            self.parameter_widgets[label] = input_widget

        # Show the dock widget
        self.param_dock.show()

    @staticmethod
    def get_operator_description(operator):
        """Return a short human-friendly description for an operator."""
        explicit_descriptions = {
            "info": "Shows a compact summary of the variables, levels, and timesteps in the input file.",
            "infov": "Shows detailed variable-oriented information from the input file.",
            "sinfo": "Shows a short dataset summary without writing a new output file.",
            "map": "Prints a map-style textual view of the data values in the input file.",
            "diff": "Compares two input files and reports the differences between them.",
            "diffv": "Compares variables in two files and reports detailed differences.",
            "copy": "Copies the input dataset to a new output file without changing the content.",
            "cat": "Concatenates multiple input files in the given order into one output file.",
            "merge": "Merges multiple files so their fields or variables are combined into one output dataset.",
            "mergetime": "Merges multiple files along the time axis into one chronological output file.",
            "selname": "Keeps only the selected variable names in the output file.",
            "seldate": "Keeps only the timesteps that fall within the selected date range.",
            "seltimestep": "Keeps only the selected timestep numbers in the output file.",
            "setname": "Renames variables or metadata fields in the output file.",
            "setlevel": "Changes the level metadata of the selected data.",
            "expr": "Applies an expression to compute new values or variables from the input data.",
            "exprf": "Runs expressions from a file to transform the input dataset.",
            "add": "Adds values from two input files cell-by-cell and writes the result.",
            "sub": "Subtracts the second input file from the first input file.",
            "mul": "Multiplies values from two input files cell-by-cell.",
            "div": "Divides values from the first input file by the second input file.",
            "fldmean": "Calculates the spatial mean over all grid cells for each timestep.",
            "fldsum": "Calculates the spatial sum over all grid cells for each timestep.",
        }
        if operator in explicit_descriptions:
            return explicit_descriptions[operator]

        for category, operators in OPERATOR_CATEGORIES.items():
            if operator in operators:
                if category == NCExplorerCategory.INFORMATION:
                    return "Reads the input data and prints information or diagnostics without creating a new output file."
                if category == NCExplorerCategory.FILE_OPERATIONS:
                    return "Copies, merges, splits, or reorganizes files into a new output dataset."
                if category == NCExplorerCategory.SELECTION:
                    return "Selects a subset of variables, levels, dates, timesteps, or regions from the input data."
                if category == NCExplorerCategory.CONDITIONAL_SELECTION:
                    return "Filters or selects data conditionally using another dataset or constant value."
                if category == NCExplorerCategory.COMPARISON:
                    return "Compares datasets or values and writes the comparison result."
                if category == NCExplorerCategory.MODIFICATION:
                    return "Changes metadata, coordinates, masks, or values while preserving the overall dataset structure."
                if category == NCExplorerCategory.ARITHMETIC:
                    return "Applies arithmetic or mathematical expressions to transform the data values."
                if category == NCExplorerCategory.STATISTICAL_VALUES:
                    return "Computes aggregated statistics across space, time, levels, or ensembles."
                if category == NCExplorerCategory.REGRESSION:
                    return "Computes regression-based relationships from the input data."
                if category == NCExplorerCategory.INTERPOLATION:
                    return "Interpolates the dataset onto a different grid, level set, or sampling geometry."
                if category == NCExplorerCategory.TRANSFORMATION:
                    return "Transforms the structure or representation of the dataset."
                if category == NCExplorerCategory.FORMATTED_IO:
                    return "Imports or exports the dataset in a specific textual or formatted representation."
                if category == NCExplorerCategory.MISCELLANEOUS:
                    return "Performs a utility operation that does not fit the main processing groups."
                if category == NCExplorerCategory.ECA_INDICES:
                    return "Computes a climate index or extreme-event indicator from the input data."

        return "Runs the selected operator on the provided inputs and writes the requested output."

    @staticmethod
    def get_operator_syntax(operator):
        """Get operator syntax from NCExplorer reference"""
        syntax_map = {
            # Information operators
            "info": "ifile",
            "infov": "ifile",
            "map": "ifile",
            "sinfo": "ifile",
            "sinfov": "ifile",
            "diff": "ifile1 ifile2",
            "diffv": "ifile1 ifile2",
            "npar": "ifile",
            "nlevel": "ifile",
            "nyear": "ifile",
            "nmon": "ifile",
            "ndate": "ifile",
            "ntime": "ifile",
            "showformat": "ifile",
            "showcode": "ifile",
            "showname": "ifile",
            "showstdname": "ifile",
            "showlevel": "ifile",
            "showltype": "ifile",
            "showyear": "ifile",
            "showmon": "ifile",
            "showdate": "ifile",
            "showtime": "ifile",
            "pardes": "ifile",
            "griddes": "ifile",
            "vct": "ifile",

            # File operations
            "copy": "ifile ofile",
            "cat": "ifiles ofile",
            "replace": "ifile1 ifile2 ofile",
            "merge": "ifiles ofile",
            "mergetime": "ifiles ofile",
            "splitcode": "ifile oprefix",
            "splitname": "ifile oprefix",
            "splitlevel": "ifile oprefix",
            "splitgrid": "ifile oprefix",
            "splitzaxis": "ifile oprefix",
            "splithour": "ifile oprefix",
            "splitday": "ifile oprefix",
            "splitmon": "ifile oprefix",
            "splitseas": "ifile oprefix",
            "splityear": "ifile oprefix",
            "splitsel": "ifile oprefix",

            # Selection operators
            "selcode": "ifile ofile codes",
            "delcode": "ifile ofile codes",
            "selname": "ifile ofile names",
            "delname": "ifile ofile names",
            "selstdname": "ifile ofile stdnames",
            "sellevel": "ifile ofile levels",
            "selgrid": "ifile ofile grids",
            "selgridname": "ifile ofile gridnames",
            "selzaxis": "ifile ofile zaxes",
            "selzaxisname": "ifile ofile zaxisnames",
            "selltype": "ifile ofile ltypes",
            "seltabnum": "ifile ofile tabnums",
            "seltimestep": "ifile ofile timesteps",
            "seltime": "ifile ofile times",
            "selhour": "ifile ofile hours",
            "selday": "ifile ofile days",
            "selmon": "ifile ofile months",
            "selyear": "ifile ofile years",
            "selseas": "ifile ofile seasons",
            "seldate": "ifile ofile date1[,date2]",
            "selsmon": "ifile ofile month[,nts1[,nts2]]",
            "sellonlatbox": "ifile ofile lon1,lon2,lat1,lat2",
            "selindexbox": "ifile ofile idx1,idx2,idy1,idy2",

            # Conditional selection
            "ifthen": "ifile1 ifile2 ofile",
            "ifnotthen": "ifile1 ifile2 ofile",
            "ifthenelse": "ifile1 ifile2 ifile3 ofile",
            "ifthenc": "ifile ofile constant",
            "ifnotthenc": "ifile ofile constant",

            # Comparison
            "eq": "ifile1 ifile2 ofile",
            "ne": "ifile1 ifile2 ofile",
            "le": "ifile1 ifile2 ofile",
            "lt": "ifile1 ifile2 ofile",
            "ge": "ifile1 ifile2 ofile",
            "gt": "ifile1 ifile2 ofile",
            "eqc": "ifile ofile constant",
            "nec": "ifile ofile constant",
            "lec": "ifile ofile constant",
            "ltc": "ifile ofile constant",
            "gec": "ifile ofile constant",
            "gtc": "ifile ofile constant",

            # Modification
            "setpartab": "ifile ofile table",
            "setcode": "ifile ofile code",
            "setname": "ifile ofile name",
            "setlevel": "ifile ofile level",
            "setltype": "ifile ofile ltype",
            "setdate": "ifile ofile date",
            "settime": "ifile ofile time",
            "setday": "ifile ofile day",
            "setmon": "ifile ofile month",
            "setyear": "ifile ofile year",
            "settunits": "ifile ofile units",
            "settaxis": "ifile ofile date,time[,inc]",
            "setreftime": "ifile ofile date,time",
            "setcalendar": "ifile ofile calendar",
            "shifttime": "ifile ofile sval",
            "chcode": "ifile ofile oldcode,newcode[,...]",
            "chname": "ifile ofile ovar,nvar,...",
            "chlevel": "ifile ofile oldlev,newlev,...",
            "chlevelc": "ifile ofile code,oldlev,newlev",
            "chlevelv": "ifile ofile var,oldlev,newlev",
            "setgrid": "ifile ofile grid",
            "setgridtype": "ifile ofile gridtype",
            "setzaxis": "ifile ofile zaxis",
            "setgatt": "ifile ofile attname,attstring",
            "setgatts": "ifile ofile attfile",
            "invertlat": "ifile ofile",
            "invertlon": "ifile ofile",
            "invertlatdes": "ifile ofile",
            "invertlondes": "ifile ofile",
            "invertlatdata": "ifile ofile",
            "invertlondata": "ifile ofile",
            "maskregion": "ifile ofile regions",
            "masklonlatbox": "ifile ofile lon1,lon2,lat1,lat2",
            "maskindexbox": "ifile ofile idx1,idx2,idy1,idy2",
            "setclonlatbox": "ifile ofile c,lon1,lon2,lat1,lat2",
            "setcindexbox": "ifile ofile c,idx1,idx2,idy1,idy2",
            "enlarge": "ifile ofile grid",
            "setmissval": "ifile ofile miss",
            "setctomiss": "ifile ofile c",
            "setmisstoc": "ifile ofile c",
            "setrtomiss": "ifile ofile rmin,rmax",

            # Arithmetic
            "expr": "ifile ofile instr",
            "exprf": "ifile ofile filename",
            "abs": "ifile ofile",
            "int": "ifile ofile",
            "nint": "ifile ofile",
            "sqr": "ifile ofile",
            "sqrt": "ifile ofile",
            "exp": "ifile ofile",
            "ln": "ifile ofile",
            "log10": "ifile ofile",
            "sin": "ifile ofile",
            "cos": "ifile ofile",
            "tan": "ifile ofile",
            "asin": "ifile ofile",
            "acos": "ifile ofile",
            "atan": "ifile ofile",
            "addc": "ifile ofile c",
            "subc": "ifile ofile c",
            "mulc": "ifile ofile c",
            "divc": "ifile ofile c",
            "add": "ifile1 ifile2 ofile",
            "sub": "ifile1 ifile2 ofile",
            "mul": "ifile1 ifile2 ofile",
            "div": "ifile1 ifile2 ofile",
            "min": "ifile1 ifile2 ofile",
            "max": "ifile1 ifile2 ofile",
            "atan2": "ifile1 ifile2 ofile",
            "ymonadd": "ifile1 ifile2 ofile",
            "ymonsub": "ifile1 ifile2 ofile",
            "ymonmul": "ifile1 ifile2 ofile",
            "ymondiv": "ifile1 ifile2 ofile",
            "muldpm": "ifile ofile",
            "divdpm": "ifile ofile",
            "muldpy": "ifile ofile",
            "divdpy": "ifile ofile",

            # Statistical values
            "ensmin": "ifiles ofile",
            "ensmax": "ifiles ofile",
            "enssum": "ifiles ofile",
            "ensmean": "ifiles ofile",
            "ensavg": "ifiles ofile",
            "ensvar": "ifiles ofile",
            "ensstd": "ifiles ofile",
            "enspctl": "ifiles ofile p",
            "fldmin": "ifile ofile",
            "fldmax": "ifile ofile",
            "fldsum": "ifile ofile",
            "fldmean": "ifile ofile",
            "fldavg": "ifile ofile",
            "fldvar": "ifile ofile",
            "fldstd": "ifile ofile",
            "fldpctl": "ifile ofile p",
            "zonmin": "ifile ofile",
            "zonmax": "ifile ofile",
            "zonsum": "ifile ofile",
            "zonmean": "ifile ofile",
            "zonavg": "ifile ofile",
            "zonvar": "ifile ofile",
            "zonstd": "ifile ofile",
            "zonpctl": "ifile ofile p",
            "mermin": "ifile ofile",
            "mermax": "ifile ofile",
            "mersum": "ifile ofile",
            "mermean": "ifile ofile",
            "meravg": "ifile ofile",
            "mervar": "ifile ofile",
            "merstd": "ifile ofile",
            "merpctl": "ifile ofile p",
            "vertmin": "ifile ofile",
            "vertmax": "ifile ofile",
            "vertsum": "ifile ofile",
            "vertmean": "ifile ofile",
            "vertavg": "ifile ofile",
            "vertvar": "ifile ofile",
            "vertstd": "ifile ofile",
            "timselmin": "ifile ofile nsets[,noffset[,nskip]]",
            "timselmax": "ifile ofile nsets[,noffset[,nskip]]",
            "timselsum": "ifile ofile nsets[,noffset[,nskip]]",
            "timselmean": "ifile ofile nsets[,noffset[,nskip]]",
            "timselavg": "ifile ofile nsets[,noffset[,nskip]]",
            "timselvar": "ifile ofile nsets[,noffset[,nskip]]",
            "timselstd": "ifile ofile nsets[,noffset[,nskip]]",
            "timselpctl": "ifile1 ifile2 ifile3 ofile p,nsets[,noffset[,nskip]]",
            "runmin": "ifile ofile nts",
            "runmax": "ifile ofile nts",
            "runsum": "ifile ofile nts",
            "runmean": "ifile ofile nts",
            "runavg": "ifile ofile nts",
            "runvar": "ifile ofile nts",
            "runstd": "ifile ofile nts",
            "runpctl": "ifile1 ofile p,nts",
            "timmin": "ifile ofile",
            "timmax": "ifile ofile",
            "timsum": "ifile ofile",
            "timmean": "ifile ofile",
            "timavg": "ifile ofile",
            "timvar": "ifile ofile",
            "timstd": "ifile ofile",
            "timpctl": "ifile1 ifile2 ifile3 ofile p",
            "hourmin": "ifile ofile",
            "hourmax": "ifile ofile",
            "hoursum": "ifile ofile",
            "hourmean": "ifile ofile",
            "houravg": "ifile ofile",
            "hourvar": "ifile ofile",
            "hourstd": "ifile ofile",
            "hourpctl": "ifile1 ifile2 ifile3 ofile p",
            "daymin": "ifile ofile",
            "daymax": "ifile ofile",
            "daysum": "ifile ofile",
            "daymean": "ifile ofile",
            "dayavg": "ifile ofile",
            "dayvar": "ifile ofile",
            "daystd": "ifile ofile",
            "daypctl": "ifile1 ifile2 ifile3 ofile p",
            "monmin": "ifile ofile",
            "monmax": "ifile ofile",
            "monsum": "ifile ofile",
            "monmean": "ifile ofile",
            "monavg": "ifile ofile",
            "monvar": "ifile ofile",
            "monstd": "ifile ofile",
            "monpctl": "ifile1 ifile2 ifile3 ofile p",
            "yearmin": "ifile ofile",
            "yearmax": "ifile ofile",
            "yearsum": "ifile ofile",
            "yearmean": "ifile ofile",
            "yearavg": "ifile ofile",
            "yearvar": "ifile ofile",
            "yearstd": "ifile ofile",
            "yearpctl": "ifile1 ifile2 ifile3 ofile p",
            "seasmin": "ifile ofile",
            "seasmax": "ifile ofile",
            "seassum": "ifile ofile",
            "seasmean": "ifile ofile",
            "seasavg": "ifile ofile",
            "seasvar": "ifile ofile",
            "seasstd": "ifile ofile",
            "seaspctl": "ifile1 ifile2 ifile3 ofile p",
            "ydaymin": "ifile ofile",
            "ydaymax": "ifile ofile",
            "ydaysum": "ifile ofile",
            "ydaymean": "ifile ofile",
            "ydayavg": "ifile ofile",
            "ydayvar": "ifile ofile",
            "ydaystd": "ifile ofile",
            "ydaypctl": "ifile1 ifile2 ifile3 ofile p",
            "ymonmin": "ifile ofile",
            "ymonmax": "ifile ofile",
            "ymonsum": "ifile ofile",
            "ymonmean": "ifile ofile",
            "ymonavg": "ifile ofile",
            "ymonvar": "ifile ofile",
            "ymonstd": "ifile ofile",
            "ymonpctl": "ifile1 ifile2 ifile3 ofile p",
            "yseasmin": "ifile ofile",
            "yseasmax": "ifile ofile",
            "yseassum": "ifile ofile",
            "yseasmean": "ifile ofile",
            "yseasavg": "ifile ofile",
            "yseasvar": "ifile ofile",
            "yseasstd": "ifile ofile",
            "yseaspctl": "ifile1 ifile2 ifile3 ofile p",
            "ydrunmin": "ifile ofile nts",
            "ydrunmax": "ifile ofile nts",
            "ydrunsum": "ifile ofile nts",
            "ydrunmean": "ifile ofile nts",
            "ydrunavg": "ifile ofile nts",
            "ydrunvar": "ifile ofile nts",
            "ydrunstd": "ifile ofile nts",
            "ydrunpctl": "ifile1 ifile2 ifile3 ofile p,nts",

            # Regression
            "detrend": "ifile ofile",
            "trend": "ifile ofile1 ofile2",
            "subtrend": "ifile1 ifile2 ifile3 ofile",

            # Interpolation
            "remapbil": "ifile ofile grid",
            "remapbic": "ifile ofile grid",
            "remapcon": "ifile ofile grid",
            "remapdis": "ifile ofile grid",
            "genbil": "ifile ofile grid",
            "genbic": "ifile ofile grid",
            "gencon": "ifile ofile grid",
            "gendis": "ifile ofile grid",
            "remap": "ifile ofile grid,weights",
            "interpolate": "ifile ofile grid",
            "intgridbil": "ifile ofile grid",
            "remapeta": "ifile ofile vct[,oro]",
            "ml2pl": "ifile ofile plevels",
            "ml2hl": "ifile ofile hlevels",
            "inttime": "ifile ofile date,time[,inc]",
            "intntime": "ifile ofile n",
            "intyear": "ifile1 ifile2 oprefix years",

            # Transformation
            "sp2gp": "ifile ofile",
            "sp2gpl": "ifile ofile",
            "gp2sp": "ifile ofile",
            "gp2spl": "ifile ofile",
            "sp2sp": "ifile ofile trunc",
            "spcut": "ifile ofile wnums",
            "dv2uv": "ifile ofile",
            "dv2uvl": "ifile ofile",
            "uv2dv": "ifile ofile",
            "uv2dvl": "ifile ofile",

            # Formatted I/O
            "input": "ofile grid",
            "inputsrv": "ofile",
            "inputext": "ofile",
            "output": "ifiles",
            "outputf": "ifiles format,nelem",
            "outputint": "ifiles",
            "outputsrv": "ifiles",
            "outputext": "ifiles",

            # Miscellaneous
            "gradsdes1": "ifile",
            "gradsdes2": "ifile",
            "smooth9": "ifile ofile",
            "setrtoc": "ifile ofile rmin,rmax,c",
            "setrtoc2": "ifile ofile rmin,rmax,c,c2",
            "timsort": "ifile ofile",
            "const": "ofile constant grid",
            "random": "ofile grid",
            "rotuvb": "ifile ofile u,v,...",
            "mastrfu": "ifile ofile",
            "histcount": "ifile ofile bins",
            "histsum": "ifile ofile bins",
            "histmean": "ifile ofile bins",
            "histfreq": "ifile ofile bins",
            "wct": "ifile1 ifile2 ofile",
            "fdns": "ifile1 ifile2 ofile",
            "strwin": "ifile ofile [,v]",
            "strbre": "ifile ofile",
            "strgal": "ifile ofile",
            "hurr": "ifile ofile",

            # ECA indices
            "eca_cdd": "ifile ofile",
            "eca_cfd": "ifile ofile",
            "eca_csu": "ifile ofile [,T]",
            "eca_cwd": "ifile ofile",
            "eca_cwdi": "ifile1 ifile2 ofile [,nday[,T]]",
            "eca_cwfi": "ifile1 ifile2 ofile [,nday]",
            "eca_etr": "ifile1 ifile2 ofile",
            "eca_fd": "ifile ofile",
            "eca_gsl": "ifile ofile [,nday[,T]]",
            "eca_hd": "ifile ofile [,T1[,T2]]",
            "eca_hwdi": "ifile1 ifile2 ofile [,nday[,T]]",
            "eca_hwfi": "ifile1 ifile2 ofile [,nday]",
            "eca_id": "ifile ofile",
            "eca_r10mm": "ifile ofile",
            "eca_r20mm": "ifile ofile",
            "eca_r75p": "ifile1 ifile2 ofile",
            "eca_r75ptot": "ifile1 ifile2 ofile",
            "eca_r90p": "ifile1 ifile2 ofile",
            "eca_r90ptot": "ifile1 ifile2 ofile",
            "eca_r95p": "ifile1 ifile2 ofile",
            "eca_r95ptot": "ifile1 ifile2 ofile",
            "eca_r99p": "ifile1 ifile2 ofile",
            "eca_r99ptot": "ifile1 ifile2 ofile",
            "eca_rr1": "ifile ofile",
            "eca_rx1day": "ifile ofile [,mode]",
            "eca_rx5day": "ifile ofile [,x]",
            "eca_sdii": "ifile ofile",
            "eca_su": "ifile ofile [,T]",
            "eca_tg10p": "ifile1 ifile2 ofile",
            "eca_tg90p": "ifile1 ifile2 ofile",
            "eca_tn10p": "ifile1 ifile2 ofile",
            "eca_tn90p": "ifile1 ifile2 ofile",
            "eca_tr": "ifile ofile [,T]",
            "eca_tx10p": "ifile1 ifile2 ofile",
            "eca_tx90p": "ifile1 ifile2 ofile",
        }

        return syntax_map.get(operator, "ifile ofile")

    @staticmethod
    def get_extra_parameters_for_operator(operator: str):
        """
        Return a list of (name, type, label, placeholder) for extra
        non-file parameters that go before ifile/ofile in CDO.

        Prefers the canonical OPERATOR_SCHEMA when the operator has an
        entry there, falling back to the legacy hardcoded map for
        operators not yet migrated.
        """
        try:
            from ..core.categories import OPERATOR_SCHEMA
        except ImportError:
            OPERATOR_SCHEMA = {}
        spec = OPERATOR_SCHEMA.get(operator) if OPERATOR_SCHEMA else None
        if spec is not None and spec.params:
            kind_map = {
                "int": "integer",
                "float": "float",
                "string": "string",
                "file": "paramfile",
                "grid": "paramgrid",
                "select": "select",
            }
            out = []
            for p in spec.params:
                ui_type = kind_map.get(p.kind, "string")
                entry = (p.name, ui_type, p.label, p.placeholder)
                if p.kind == "select":
                    entry = (p.name, ui_type, p.label, p.placeholder, tuple(p.choices))
                out.append(entry)
            return out

        extra_map = {
            # =========================
            # SELECTION (all of them)
            # =========================

            # selcode,codes ifile ofile
            "selcode": [
                ("codes", "string", "codes", "Enter codes (e.g. 130,131)"),
            ],
            # delcode,codes ifile ofile
            "delcode": [
                ("codes", "string", "codes", "Enter codes (e.g. 130,131)"),
            ],
            # selname,vars ifile ofile
            "selname": [
                ("vars", "string", "variables", "Enter variable names (comma-separated)"),
            ],
            # delname,vars ifile ofile
            "delname": [
                ("vars", "string", "variables", "Enter variable names (comma-separated)"),
            ],
            # selstdname,stdnames ifile ofile
            "selstdname": [
                ("stdnames", "string", "stdnames", "Enter standard names"),
            ],
            # sellevel,levels ifile ofile
            "sellevel": [
                ("levels", "string", "levels", "Enter levels (e.g. 1000,850)"),
            ],
            # selgrid,grids ifile ofile
            "selgrid": [
                ("grids", "string", "grids", "Enter grid IDs or names"),
            ],
            # selgridname,gridnames ifile ofile
            "selgridname": [
                ("gridnames", "string", "gridnames", "Enter grid names"),
            ],
            # selzaxis,zaxes ifile ofile
            "selzaxis": [
                ("zaxes", "string", "zaxes", "Enter z-axis IDs"),
            ],
            # selzaxisname,zaxisnames ifile ofile
            "selzaxisname": [
                ("zaxisnames", "string", "zaxisnames", "Enter z-axis names"),
            ],
            # selltype,ltypes ifile ofile
            "selltype": [
                ("ltypes", "string", "ltypes", "Enter level types"),
            ],
            # seltabnum,tabnums ifile ofile
            "seltabnum": [
                ("tabnums", "string", "tabnums", "Enter table numbers"),
            ],
            # seltimestep,timesteps ifile ofile
            "seltimestep": [
                ("timesteps", "string", "timesteps", "Enter time steps"),
            ],
            # seltime,times ifile ofile
            "seltime": [
                ("times", "string", "times", "Enter times selector (e.g. 12:00,18:00)"),
            ],
            # selhour,hours ifile ofile
            "selhour": [
                ("hours", "string", "hours", "Enter hours selector (e.g. 0,6,12,18)"),
            ],
            # selday,days ifile ofile
            "selday": [
                ("days", "string", "days", "Enter days selector"),
            ],
            # selmon,months ifile ofile
            "selmon": [
                ("months", "string", "months", "Enter months selector (e.g. 1,2,12)"),
            ],
            # selyear,years ifile ofile
            "selyear": [
                ("years", "string", "years", "Enter years (e.g. 1981/2010)"),
            ],
            # selseas,seasons ifile ofile
            "selseas": [
                ("seasons", "string", "seasons", "Enter seasons (e.g. DJF,MAM)"),
            ],
            # seldate,date1[,date2] ifile ofile
            "seldate": [
                ("date1", "string", "date1", "Enter first date (YYYY-MM-DD)"),
                ("date2", "string", "date2 (optional)", "Enter second date (optional)"),
            ],
            # selsmon,month[,nts1[,nts2]] ifile ofile
            "selsmon": [
                ("month", "integer", "month", "Enter month (1–12)"),
                ("nts1", "integer", "nts1 (optional)", "Enter nts1 (optional)"),
                ("nts2", "integer", "nts2 (optional)", "Enter nts2 (optional)"),
            ],
            # sellonlatbox,lon1,lon2,lat1,lat2 ifile ofile
            "sellonlatbox": [
                ("lon1", "float", "lon1", "Enter lon1"),
                ("lon2", "float", "lon2", "Enter lon2"),
                ("lat1", "float", "lat1", "Enter lat1"),
                ("lat2", "float", "lat2", "Enter lat2"),
            ],
            # selindexbox,idx1,idx2,idy1,idy2 ifile ofile
            "selindexbox": [
                ("idx1", "integer", "idx1", "Enter idx1"),
                ("idx2", "integer", "idx2", "Enter idx2"),
                ("idy1", "integer", "idy1", "Enter idy1"),
                ("idy2", "integer", "idy2", "Enter idy2"),
            ],

            # =========================
            # MODIFICATION (all of them)
            # =========================

            # setpartab,table ifile ofile
            "setpartab": [
                ("table", "string", "table", "Enter parameter table name or file"),
            ],
            # setcode,code ifile ofile
            "setcode": [
                ("code", "integer", "code", "Enter code number"),
            ],
            # setname,name ifile ofile
            "setname": [
                ("name", "string", "name", "Enter variable name"),
            ],
            # setlevel,level ifile ofile
            "setlevel": [
                ("level", "string", "level", "Enter level"),
            ],
            # setltype,ltype ifile ofile
            "setltype": [
                ("ltype", "string", "ltype", "Enter GRIB level type"),
            ],
            # setdate,date ifile ofile
            "setdate": [
                ("date", "string", "date", "Enter date (YYYY-MM-DD)"),
            ],
            # settime,time ifile ofile
            "settime": [
                ("time", "string", "time", "Enter time (HH:MM)"),
            ],
            # setday,day ifile ofile
            "setday": [
                ("day", "integer", "day", "Enter day (1–31)"),
            ],
            # setmon,month ifile ofile
            "setmon": [
                ("month", "integer", "month", "Enter month (1–12)"),
            ],
            # setyear,year ifile ofile
            "setyear": [
                ("year", "integer", "year", "Enter year"),
            ],
            # settunits,units ifile ofile
            "settunits": [
                ("units", "string", "units", "Enter time units"),
            ],
            # settaxis,date,time[,inc] ifile ofile
            "settaxis": [
                ("date", "string", "date", "Enter start date (YYYY-MM-DD)"),
                ("time", "string", "time", "Enter time (HH:MM)"),
                ("inc", "string", "inc (optional)", "Enter increment (e.g. 6hour)"),
            ],
            # setreftime,date,time ifile ofile
            "setreftime": [
                ("date", "string", "date", "Enter reference date (YYYY-MM-DD)"),
                ("time", "string", "time", "Enter reference time (HH:MM)"),
            ],
            # setcalendar,calendar ifile ofile
            "setcalendar": [
                ("calendar", "string", "calendar", "Enter calendar type (e.g. standard)"),
            ],
            # shifttime,sval ifile ofile
            "shifttime": [
                ("sval", "string", "sval", "Enter shift value (e.g. 1day)"),
            ],
            # chcode,oldcode,newcode[,...] ifile ofile
            "chcode": [
                ("oldcode_newcode", "string", "oldcode,newcode[,...]",
                 "Enter oldcode,newcode pairs"),
            ],
            # chname,ovar,nvar,... ifile ofile
            "chname": [
                ("ovar_nvar", "string", "ovar,nvar,...",
                 "Enter old/new variable name pairs"),
            ],
            # chlevel,oldlev,newlev,... ifile ofile
            "chlevel": [
                ("oldlev_newlev", "string", "oldlev,newlev,...",
                 "Enter old/new level pairs"),
            ],
            # chlevelc,code,oldlev,newlev ifile ofile
            "chlevelc": [
                ("code", "integer", "code", "Enter code"),
                ("oldlev", "string", "oldlev", "Enter old level"),
                ("newlev", "string", "newlev", "Enter new level"),
            ],
            # chlevelv,var,oldlev,newlev ifile ofile
            "chlevelv": [
                ("var", "string", "var", "Enter variable name"),
                ("oldlev", "string", "oldlev", "Enter old level"),
                ("newlev", "string", "newlev", "Enter new level"),
            ],
            # setgrid,grid ifile ofile
            "setgrid": [
                ("grid", "string", "grid", "Enter grid name or file"),
            ],
            # setgridtype,gridtype ifile ofile
            "setgridtype": [
                ("gridtype", "string", "gridtype", "Enter grid type"),
            ],
            # setzaxis,zaxis ifile ofile
            "setzaxis": [
                ("zaxis", "string", "zaxis", "Enter zaxis name or ID"),
            ],
            # setgatt,attname,attstring ifile ofile
            "setgatt": [
                ("attname", "string", "attname", "Enter attribute name"),
                ("attstring", "string", "attstring", "Enter attribute value"),
            ],
            # setgatts,attfile ifile ofile
            "setgatts": [
                ("attfile", "string", "attfile", "Enter attribute file path"),
            ],
            # maskregion,regions ifile ofile
            "maskregion": [
                ("regions", "string", "regions", "Enter region IDs"),
            ],
            # masklonlatbox,lon1,lon2,lat1,lat2 ifile ofile
            "masklonlatbox": [
                ("lon1", "float", "lon1", "Enter lon1"),
                ("lon2", "float", "lon2", "Enter lon2"),
                ("lat1", "float", "lat1", "Enter lat1"),
                ("lat2", "float", "lat2", "Enter lat2"),
            ],
            # maskindexbox,idx1,idx2,idy1,idy2 ifile ofile
            "maskindexbox": [
                ("idx1", "integer", "idx1", "Enter idx1"),
                ("idx2", "integer", "idx2", "Enter idx2"),
                ("idy1", "integer", "idy1", "Enter idy1"),
                ("idy2", "integer", "idy2", "Enter idy2"),
            ],
            # setclonlatbox,c,lon1,lon2,lat1,lat2 ifile ofile
            "setclonlatbox": [
                ("c", "float", "c", "Enter constant c"),
                ("lon1", "float", "lon1", "Enter lon1"),
                ("lon2", "float", "lon2", "Enter lon2"),
                ("lat1", "float", "lat1", "Enter lat1"),
                ("lat2", "float", "lat2", "Enter lat2"),
            ],
            # setcindexbox,c,idx1,idx2,idy1,idy2 ifile ofile
            "setcindexbox": [
                ("c", "float", "c", "Enter constant c"),
                ("idx1", "integer", "idx1", "Enter idx1"),
                ("idx2", "integer", "idx2", "Enter idx2"),
                ("idy1", "integer", "idy1", "Enter idy1"),
                ("idy2", "integer", "idy2", "Enter idy2"),
            ],
            # setmissval,miss ifile ofile
            "setmissval": [
                ("miss", "float", "miss", "Enter missing value"),
            ],
            # setctomiss,c ifile ofile ; setmisstoc,c ifile ofile
            "setctomiss": [
                ("c", "float", "c", "Enter constant c"),
            ],
            "setmisstoc": [
                ("c", "float", "c", "Enter constant c"),
            ],
            # setrtomiss,rmin,rmax ifile ofile
            "setrtomiss": [
                ("rmin", "float", "rmin", "Enter minimum value (rmin)"),
                ("rmax", "float", "rmax", "Enter maximum value (rmax)"),
            ],
            # enlarge,grid ifile ofile
            "enlarge": [
                ("grid", "string", "grid", "Enter target grid"),
            ],

            # =========================
            # ARITHMETIC
            # =========================

            # expr,instr ifile ofile
            "expr": [
                ("instr", "string", "instr", "Enter expression (e.g. var*2)"),
            ],
            # exprf,filename ifile ofile
            "exprf": [
                ("filename", "string", "filename", "Enter expression script file"),
            ],
            # addc/subc/mulc/divc handled earlier: <operator>,c ifile ofile
            "addc": [("c", "float", "c (constant)", "Enter constant c")],
            "subc": [("c", "float", "c (constant)", "Enter constant c")],
            "mulc": [("c", "float", "c (constant)", "Enter constant c")],
            "divc": [("c", "float", "c (constant)", "Enter constant c")],

            # =========================
            # STATISTICAL (extra ones)
            # =========================

            # timpctl,p ifile1 ifile2 ifile3 ofile
            "timpctl": [
                ("p", "float", "p (percentile)", "Enter percentile p"),
            ],
            # monpctl,p ifile1 ifile2 ifile3 ofile
            "monpctl": [
                ("p", "float", "p (percentile)", "Enter percentile p"),
            ],
            # yearpctl,p ifile1 ifile2 ifile3 ofile
            "yearpctl": [
                ("p", "float", "p (percentile)", "Enter percentile p"),
            ],
            # seaspctl,p ifile1 ifile2 ifile3 ofile
            "seaspctl": [
                ("p", "float", "p (percentile)", "Enter percentile p"),
            ],
            # ydaypctl,p ifile1 ifile2 ifile3 ofile
            "ydaypctl": [
                ("p", "float", "p (percentile)", "Enter percentile p"),
            ],
            # ymonpctl,p ifile1 ifile2 ifile3 ofile
            "ymonpctl": [
                ("p", "float", "p (percentile)", "Enter percentile p"),
            ],
            # yseaspctl,p ifile1 ifile2 ifile3 ofile
            "yseaspctl": [
                ("p", "float", "p (percentile)", "Enter percentile p"),
            ],
            # ydrunpctl,p,nts ifile1 ifile2 ifile3 ofile
            "ydrunpctl": [
                ("p", "float", "p (percentile)", "Enter percentile p"),
                ("nts", "integer", "nts", "Enter window length (nts)"),
            ],

            # existing time-range / running windows you already had:
            "timselmin": [
                ("nsets", "integer", "nsets", "Enter number of sets (nsets)"),
                ("noffset", "integer", "noffset (optional)", "Enter offset (optional)"),
                ("nskip", "integer", "nskip (optional)", "Enter skip (optional)"),
            ],
            "timselmax": [
                ("nsets", "integer", "nsets", "Enter number of sets (nsets)"),
                ("noffset", "integer", "noffset (optional)", "Enter offset (optional)"),
                ("nskip", "integer", "nskip (optional)", "Enter skip (optional)"),
            ],
            "timselsum": [
                ("nsets", "integer", "nsets", "Enter number of sets (nsets)"),
                ("noffset", "integer", "noffset (optional)", "Enter offset (optional)"),
                ("nskip", "integer", "nskip (optional)", "Enter skip (optional)"),
            ],
            "timselmean": [
                ("nsets", "integer", "nsets", "Enter number of sets (nsets)"),
                ("noffset", "integer", "noffset (optional)", "Enter offset (optional)"),
                ("nskip", "integer", "nskip (optional)", "Enter skip (optional)"),
            ],
            "timselavg": [
                ("nsets", "integer", "nsets", "Enter number of sets (nsets)"),
                ("noffset", "integer", "noffset (optional)", "Enter offset (optional)"),
                ("nskip", "integer", "nskip (optional)", "Enter skip (optional)"),
            ],
            "timselvar": [
                ("nsets", "integer", "nsets", "Enter number of sets (nsets)"),
                ("noffset", "integer", "noffset (optional)", "Enter offset (optional)"),
                ("nskip", "integer", "nskip (optional)", "Enter skip (optional)"),
            ],
            "timselstd": [
                ("nsets", "integer", "nsets", "Enter number of sets (nsets)"),
                ("noffset", "integer", "noffset (optional)", "Enter offset (optional)"),
                ("nskip", "integer", "nskip (optional)", "Enter skip (optional)"),
            ],
            "timselpctl": [
                ("p", "float", "p (percentile)", "Enter percentile p"),
                ("nsets", "integer", "nsets", "Enter number of sets (nsets)"),
                ("noffset", "integer", "noffset (optional)", "Enter offset (optional)"),
                ("nskip", "integer", "nskip (optional)", "Enter skip (optional)"),
            ],
            "runmin": [("nts", "integer", "nts", "Enter window length (nts)")],
            "runmax": [("nts", "integer", "nts", "Enter window length (nts)")],
            "runsum": [("nts", "integer", "nts", "Enter window length (nts)")],
            "runmean": [("nts", "integer", "nts", "Enter window length (nts)")],
            "runavg": [("nts", "integer", "nts", "Enter window length (nts)")],
            "runvar": [("nts", "integer", "nts", "Enter window length (nts)")],
            "runstd": [("nts", "integer", "nts", "Enter window length (nts)")],
            "runpctl": [
                ("p", "float", "p (percentile)", "Enter percentile p"),
                ("nts", "integer", "nts", "Enter window length (nts)"),
            ],
            "ydrunmin": [("nts", "integer", "nts", "Enter window length (nts)")],
            "ydrunmax": [("nts", "integer", "nts", "Enter window length (nts)")],
            "ydrunsum": [("nts", "integer", "nts", "Enter window length (nts)")],
            "ydrunmean": [("nts", "integer", "nts", "Enter window length (nts)")],
            "ydrunavg": [("nts", "integer", "nts", "Enter window length (nts)")],
            "ydrunvar": [("nts", "integer", "nts", "Enter window length (nts)")],
            "ydrunstd": [("nts", "integer", "nts", "Enter window length (nts)")],

            # =========================
            # INTERPOLATION
            # =========================

            # remapbil,grid ifile ofile etc.
            "remapbil": [("grid", "string", "grid", "Enter target grid")],
            "remapbic": [("grid", "string", "grid", "Enter target grid")],
            "remapcon": [("grid", "string", "grid", "Enter target grid")],
            "remapdis": [("grid", "string", "grid", "Enter target grid")],
            "genbil": [("grid", "string", "grid", "Enter target grid")],
            "genbic": [("grid", "string", "grid", "Enter target grid")],
            "gencon": [("grid", "string", "grid", "Enter target grid")],
            "gendis": [("grid", "string", "grid", "Enter target grid")],
            # remap,grid,weights ifile ofile
            "remap": [
                ("grid", "string", "grid", "Enter grid name or file"),
                ("weights", "string", "weights", "Enter weights file"),
            ],
            # interpolate,grid ifile ofile
            "interpolate": [
                ("grid", "string", "grid", "Enter target grid"),
            ],
            "intgridbil": [
                ("grid", "string", "grid", "Enter target grid"),
            ],
            # remapeta,vct[,oro] ifile ofile
            "remapeta": [
                ("vct", "string", "vct", "Enter vertical coordinate table"),
                ("oro", "string", "oro (optional)", "Enter orography file (optional)"),
            ],
            # ml2pl,plevels ifile ofile
            "ml2pl": [
                ("plevels", "string", "plevels", "Enter pressure levels"),
            ],
            # ml2hl,hlevels ifile ofile
            "ml2hl": [
                ("hlevels", "string", "hlevels", "Enter height levels"),
            ],
            # inttime,date,time[,inc] ifile ofile
            "inttime": [
                ("date", "string", "date", "Enter start date (YYYY-MM-DD)"),
                ("time", "string", "time", "Enter time (HH:MM)"),
                ("inc", "string", "inc (optional)", "Enter time increment (e.g. 6hour)"),
            ],
            # intntime,n ifile ofile
            "intntime": [
                ("n", "integer", "n", "Enter number of time steps"),
            ],
            # intyear,years ifile1 ifile2 oprefix
            "intyear": [
                ("years", "string", "years", "Enter years (e.g. 1981/2010)"),
            ],

            # =========================
            # FORMATTED I/O
            # =========================

            # input,grid ofile
            "input": [
                ("grid", "string", "grid", "Enter grid name or file"),
            ],
            # outputf,format,nelem ifiles
            "outputf": [
                ("format", "string", "format", "Enter output format string"),
                ("nelem", "integer", "nelem", "Enter number of elements per record"),
            ],

            # =========================
            # MISC (requested subset)
            # =========================

            # setrtoc,rmin,rmax,c ifile ofile
            "setrtoc": [
                ("rmin", "float", "rmin", "Enter minimum value (rmin)"),
                ("rmax", "float", "rmax", "Enter maximum value (rmax)"),
                ("c", "float", "c", "Enter constant c"),
            ],
            # setrtoc2,rmin,rmax,c,c2 ifile ofile
            "setrtoc2": [
                ("rmin", "float", "rmin", "Enter minimum value (rmin)"),
                ("rmax", "float", "rmax", "Enter maximum value (rmax)"),
                ("c", "float", "c", "Enter first constant c"),
                ("c2", "float", "c2", "Enter second constant c2"),
            ],
            # const,const,grid ofile  (no input file)
            "const": [
                ("const", "string", "constant", "Enter constant value"),
                ("grid", "string", "grid", "Enter grid name or spec"),
            ],
            # random,grid ofile
            "random": [
                ("grid", "string", "grid", "Enter grid name or spec"),
            ],
            # rotuvb,u,v,... ifile ofile  (variable names list)
            "rotuvb": [
                ("u_v", "string", "u,v,...", "Enter U,V variable names (comma-separated)"),
            ],
            # strwin[,v] ifile ofile
            "strwin": [
                ("v", "string", "v (optional)", "Enter wind threshold variable or value (optional)"),
            ],

            # =========================
            # ECA INDICES (requested subset)
            # =========================

            # eca_csu[,T] ifile ofile
            "eca_csu": [
                ("T", "float", "T (optional)", "Enter temperature threshold (optional)"),
            ],
            # eca_cwdi[,nday[,T]] ifile1 ifile2 ofile
            "eca_cwdi": [
                ("nday", "integer", "nday (optional)", "Enter minimal duration (optional)"),
                ("T", "float", "T (optional)", "Enter threshold (optional)"),
            ],
            # eca_cwfi[,nday] ifile1 ifile2 ofile
            "eca_cwfi": [
                ("nday", "integer", "nday (optional)", "Enter minimal duration (optional)"),
            ],
            # eca_gsl[,nday[,T]] ifile ofile
            "eca_gsl": [
                ("nday", "integer", "nday (optional)", "Enter minimal duration (optional)"),
                ("T", "float", "T (optional)", "Enter threshold (optional)"),
            ],
            # eca_hd[,T1[,T2]] ifile ofile
            "eca_hd": [
                ("T1", "float", "T1 (optional)", "Enter lower threshold (optional)"),
                ("T2", "float", "T2 (optional)", "Enter upper threshold (optional)"),
            ],
            # eca_hwdi[,nday[,T]] ifile1 ifile2 ofile
            "eca_hwdi": [
                ("nday", "integer", "nday (optional)", "Enter minimal duration (optional)"),
                ("T", "float", "T (optional)", "Enter threshold (optional)"),
            ],
            # eca_hwfi[,nday] ifile1 ifile2 ofile
            "eca_hwfi": [
                ("nday", "integer", "nday (optional)", "Enter minimal duration (optional)"),
            ],
            # eca_rx1day[,mode] ifile ofile
            "eca_rx1day": [
                ("mode", "string", "mode (optional)", "Enter mode (optional)"),
            ],
            # eca_su[,T] ifile ofile
            "eca_su": [
                ("T", "float", "T (optional)", "Enter temperature threshold (optional)"),
            ],
        }

        return extra_map.get(operator, [])

    @staticmethod
    def parse_parameters(operator, syntax):
        """Parse operator syntax using OPERATOR_SIGNATURES"""
        try:
            from .core.categories import OPERATOR_SIGNATURES
        except ImportError:
            from ncexplorer_toolkit.core.categories import OPERATOR_SIGNATURES

        nin, nout = OPERATOR_SIGNATURES.get(operator, (1, 1))
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
            passthrough = {"integer", "float", "string", "paramfile", "paramgrid", "select"}
            ui_type = ptype if ptype in passthrough else "string"
            if ui_type == "select":
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
            # No input files
            pass
        else:
            # Fixed number of inputs
            for i in range(nin):
                params.append(("file", f"Input File {i + 1}", f"Select input file {i + 1}"))

        # Add output parameters based on nout
        if nout == 1:
            params.append(("file", "Output File", "Select output file"))
        elif nout == -1:
            params.append(("string", "Output Prefix", "Enter output file prefix"))
        # nout == 0: no output parameters needed

        # Special cases for operators with additional parameters
        if operator == "const":
            params.insert(0, ("string", "Constant Value", "Enter value,grid (e.g., 273.15,r360x180)"))

        return params

    def browse_file(self, line_edit):
        """Browse for a file and set the path in the line edit"""
        # Determine if this is for input or output based on the label
        is_output = False
        for label, widget in self.parameter_widgets.items():
            if widget == line_edit.parent() and ("Output" in label or "output" in label.lower()):
                is_output = True
                break

        if is_output:
            # For output files, use save dialogs
            options = QFileDialog.Option.DontUseNativeDialog
            file_path, _ = QFileDialog.getSaveFileName(
                self, "Select Output File", "",
                "NetCDF Files (*.nc);;GRIB Files (*.grb *.grib);;All Files (*)",
                options=options
            )
        else:
            # For input files, use open dialogs
            options = QFileDialog.Option.DontUseNativeDialog
            file_path, _ = QFileDialog.getOpenFileName(
                self, "Select Input File", "",
                "All Supported Files (*.nc *.grb *.grib *.tif *.tiff *.shp *.geojson);;"
                "NetCDF Files (*.nc);;GRIB Files (*.grb *.grib);;"
                "GeoTIFF Files (*.tif *.tiff);;Vector Files (*.shp *.geojson);;"
                "All Files (*)",
                options=options
            )

        if file_path:
            line_edit.setText(file_path)

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

    def execute_operation(self):
        """Execute the selected NCExplorer operation with comprehensive debugging"""
        if not self.current_operator:
            QMessageBox.warning(self, "Warning", "No operator selected")
            return

        # Import OPERATOR_SIGNATURES to get operator info
        try:
            from .core.categories import OPERATOR_SIGNATURES
        except ImportError:
            from ncexplorer_toolkit.core.categories import OPERATOR_SIGNATURES

        # Get operator signature
        nin, nout = OPERATOR_SIGNATURES.get(self.current_operator, (1, 1))

        self._debug(f"Starting execution of '{self.current_operator}'")
        self._debug(f"Operator signature: nin={nin}, nout={nout}")

        try:
            # Collect extra non-file parameters (c, nsets, nskip, etc.)
            extra_args = self._collect_extra_parameters()
            self._debug(f"Collected extra parameters: {extra_args}")

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

            # Build call arguments in CDO order:
            #   [extra_args..., input_files..., output/prefix (if any)]
            if nout == 0:
                # Info/display operators - no output file needed
                call_args = [*extra_args, *input_files]
                self._debug(f"Executing info/display operator (nout=0)")
                self._debug(
                    f"Calling: self.NCExplorer.{self.current_operator}"
                    f"({', '.join(repr(a) for a in call_args)})"
                )

                try:
                    result = getattr(self.NCExplorer, self.current_operator)(*call_args)
                    self._debug(f"NCExplorer method call completed")
                except AttributeError as e:
                    self.output_console.append(f"❌ DEBUG: Method not found: {e}")
                    QMessageBox.critical(
                        self,
                        "Error",
                        f"NCExplorer method '{self.current_operator}' not found: {e}",
                    )
                    return
                except Exception as e:
                    self.output_console.append(f"❌ DEBUG: NCExplorer method call failed: {e}")
                    QMessageBox.critical(
                        self, "Error", f"NCExplorer method call failed: {e}"
                    )
                    return

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

                # CDO order: extra params, then input files, then ofile
                call_args = [*extra_args, *input_files, output_file]
                self._debug(f"Executing standard operator (nout=1)")
                self._debug(
                    f"Calling: self.NCExplorer.{self.current_operator}"
                    f"({', '.join(repr(a) for a in call_args)})"
                )

                result = getattr(self.NCExplorer, self.current_operator)(*call_args)

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

                # CDO order: extra params, then input files, then prefix
                call_args = [*extra_args, *input_files, prefix]
                self._debug(f"Executing split operator (nout=-1)")
                self._debug(
                    f"Calling: self.NCExplorer.{self.current_operator}"
                    f"({', '.join(repr(a) for a in call_args)})"
                )

                result = getattr(self.NCExplorer, self.current_operator)(*call_args)

            else:
                error_msg = f"Unsupported operator signature: ({nin}|{nout})"
                self.output_console.append(f"❌ DEBUG: {error_msg}")
                QMessageBox.warning(self, "Error", error_msg)
                return

            # Debug result analysis
            self._debug(f"Result object: {type(result)}")
            self._debug(f"Result.success: {result.success}")
            self._debug(f"Result.stdout length: {len(result.stdout) if result.stdout else 0}")
            self._debug(f"Result.stderr length: {len(result.stderr) if result.stderr else 0}")

            # Display results
            if result.success:
                self.output_console.append(
                    f"✓ Operation '{self.current_operator}' completed successfully"
                )
                self.output_console.append(
                    f"Execution time: {result.execution_time:.2f} seconds"
                )

                if result.stdout:
                    if self.current_operator in ["diff", "diffv", "diffc", "diffn", "diffp"]:
                        if "records differ" in result.stdout or "differ" in result.stdout.lower():
                            self.output_console.append(
                                "Differences found between files:"
                            )
                        else:
                            self.output_console.append("Files are identical")
                    else:
                        self.output_console.append("Output:")
                    self.output_console.append(result.stdout)

                if result.stderr:
                    for line in result.stderr.splitlines():
                        if line.strip():
                            self.output_console.append(f"⚠️ {line}")

                # Handle an output file for operators that create files
                if nout > 0 and hasattr(result, "output_file") and result.output_file:
                    self.current_output_file = result.output_file
                    self.save_btn.setEnabled(True)
                    self.visualize_btn.setEnabled(True)
                    self.output_console.append(
                        f"Output saved to: {result.output_file}"
                    )
                else:
                    # For info operators, enable save for the console output
                    self.save_btn.setEnabled(True)
                    self.visualize_btn.setEnabled(False)

            else:
                self.output_console.append(
                    f"✗ Operation '{self.current_operator}' failed"
                )
                if result.stderr:
                    self.output_console.append(f"Error: {result.stderr}")
                self.save_btn.setEnabled(False)
                self.visualize_btn.setEnabled(False)

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
            self, "Open File", "",
            "All Supported Files (*.nc *.grb *.grib *.tif *.tiff *.shp *.geojson);;"
            "Shapefiles (*.shp);;"  # Add a specific shapefile filter
            "NetCDF Files (*.nc);;GRIB Files (*.grb *.grib);;"
            "GeoTIFF Files (*.tif *.tiff);;Vector Files (*.shp *.geojson);;"
            "All Files (*)",
            options=options
        )

        if file_path:
            self.visualize_file(file_path)

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

    def closeEvent(self, event):
        """Handle window closing with proper cleanup"""
        try:
            # Cleanup canvas first
            if hasattr(self, 'geo_canvas'):
                self.geo_canvas.cleanup()

            # Cleanup layer manager
            if hasattr(self, 'layer_manager'):
                self.layer_manager.cleanup()

            # Accept the close event
            event.accept()
        except Exception as e:
            print(f"Error during cleanup: {e}")
            event.accept()

if __name__ == "__main__":
    import  sys
    app = QApplication(sys.argv)
    window = NCExplorerOperatorGUI()
    window.show()
    sys.exit(app.exec())
