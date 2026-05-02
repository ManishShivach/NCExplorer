import os
from PyQt6.QtCore import pyqtSignal, QDir, QModelIndex
from PyQt6.QtGui import QFileSystemModel
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTreeView, QComboBox, QLineEdit,
    QPushButton, QLabel, QGroupBox, QCheckBox, QProgressBar, QMessageBox
)


class FileExplorer(QWidget):
    """Enhanced file explorer with filtering, search, and navigation"""

    file_selected = pyqtSignal(str)
    file_double_clicked = pyqtSignal(str)
    directory_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_path = QDir.homePath()
        self.supported_formats = {
            'All NCExplorer Files': ['.nc', '.nc4', '.netcdf', '.grb', '.grib', '.grb2', '.hdf', '.h5'],
            'NetCDF': ['.nc', '.nc4', '.netcdf'],
            'GRIB': ['.grb', '.grib', '.grb2'],
            'HDF': ['.hdf', '.h5'],
            'Vector': ['.shp', '.geojson', '.kml', '.gpx'],
            'Raster': ['.tif', '.tiff']
        }
        self.setup_ui()
        self.setup_model()
        self.connect_signals()

    def setup_ui(self):
        """Set up the enhanced user interface"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        # Header with title
        header_layout = QHBoxLayout()
        title_label = QLabel("File Explorer")
        title_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #2E3440;")
        header_layout.addWidget(title_label)
        header_layout.addStretch()

        # Navigation controls
        nav_layout = QHBoxLayout()

        self.up_btn = QPushButton("↑")
        self.up_btn.setFixedSize(30, 25)
        self.up_btn.setToolTip("Go up one level")

        # Home button
        self.home_btn = QPushButton("🏠")
        self.home_btn.setFixedSize(30, 25)
        self.home_btn.setToolTip("Go to home directory")

        nav_layout.addWidget(self.up_btn)
        nav_layout.addWidget(self.home_btn)
        nav_layout.addStretch()

        # Path display and edit
        path_layout = QHBoxLayout()
        self.path_label = QLabel("Path:")
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Enter path or search...")
        self.browse_btn = QPushButton("Browse")
        self.browse_btn.setFixedWidth(60)

        path_layout.addWidget(self.path_label)
        path_layout.addWidget(self.path_edit, 1)
        path_layout.addWidget(self.browse_btn)

        # Filter controls
        filter_group = QGroupBox("Filters")
        filter_layout = QVBoxLayout(filter_group)

        # File type filter
        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel("Type:"))
        self.format_combo = QComboBox()
        self.format_combo.addItems(list(self.supported_formats.keys()))
        self.format_combo.setCurrentText('All NCExplorer Files')
        type_layout.addWidget(self.format_combo)
        type_layout.addStretch()

        # Search box
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("Search:"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search files...")
        self.search_btn = QPushButton("🔍")
        self.search_btn.setFixedSize(25, 25)
        search_layout.addWidget(self.search_edit)
        search_layout.addWidget(self.search_btn)

        # Options
        options_layout = QHBoxLayout()
        self.show_hidden_cb = QCheckBox("Show hidden files")
        self.dirs_only_cb = QCheckBox("Directories only")
        options_layout.addWidget(self.show_hidden_cb)
        options_layout.addWidget(self.dirs_only_cb)
        options_layout.addStretch()

        filter_layout.addLayout(type_layout)
        filter_layout.addLayout(search_layout)
        filter_layout.addLayout(options_layout)

        # File tree view
        self.tree_view = QTreeView()
        self.tree_view.setSortingEnabled(True)
        self.tree_view.setAlternatingRowColors(True)
        self.tree_view.setUniformRowHeights(True)

        # Status bar
        status_layout = QHBoxLayout()
        self.status_label = QLabel("Ready")
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        status_layout.addWidget(self.status_label)
        status_layout.addWidget(self.progress_bar)

        # Add all components to the main layout
        layout.addLayout(header_layout)
        layout.addLayout(nav_layout)
        layout.addLayout(path_layout)
        layout.addWidget(filter_group)
        layout.addWidget(self.tree_view, 1)  # The main tree takes most space
        layout.addLayout(status_layout)

    def setup_model(self):
        """Set up the file system model with filters"""
        self.file_model = QFileSystemModel()
        self.file_model.setRootPath('')

        # Set initial filters
        self.update_file_filters()

        # Set model to tree view
        self.tree_view.setModel(self.file_model)
        self.set_root_path(self.current_path)

        # Configure tree view columns
        self.tree_view.setColumnWidth(0, 250)  # Name column
        self.tree_view.hideColumn(1)  # Size column (initially hidden)
        self.tree_view.hideColumn(2)  # Type column (initially hidden)
        self.tree_view.hideColumn(3)  # Date modified column (initially hidden)

    def connect_signals(self):
        """Connect all widget signals"""
        # Navigation
        self.up_btn.clicked.connect(self.go_up)
        self.home_btn.clicked.connect(self.go_home)
        self.browse_btn.clicked.connect(self.browse_directory)

        # Path editing
        self.path_edit.returnPressed.connect(self.navigate_to_path)

        # Filter changes
        self.format_combo.currentTextChanged.connect(self.update_file_filters)
        self.show_hidden_cb.toggled.connect(self.update_file_filters)
        self.dirs_only_cb.toggled.connect(self.update_file_filters)

        # Search
        self.search_edit.textChanged.connect(self.filter_files)
        self.search_btn.clicked.connect(self.perform_search)

        # Tree view interactions
        self.tree_view.clicked.connect(self.on_item_clicked)
        self.tree_view.doubleClicked.connect(self.on_item_double_clicked)

    def update_file_filters(self):
        """Update file system model filters"""
        filters = QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot

        if not self.dirs_only_cb.isChecked():
            filters |= QDir.Filter.Files

        if self.show_hidden_cb.isChecked():
            filters |= QDir.Filter.Hidden

        self.file_model.setFilter(filters)

        # Set name filters for file types
        current_filter = self.format_combo.currentText()
        if current_filter in self.supported_formats:
            extensions = self.supported_formats[current_filter]
            name_filters = [f"*{ext}" for ext in extensions]
            if not self.dirs_only_cb.isChecked():
                self.file_model.setNameFilters(name_filters)
            else:
                self.file_model.setNameFilters([])  # Clear filters for dirs only

    def set_root_path(self, path):
        """Set the root path for the file explorer"""
        if os.path.exists(path):
            self.current_path = path
            self.file_model.setRootPath(path)
            root_index = self.file_model.index(path)
            self.tree_view.setRootIndex(root_index)
            self.path_edit.setText(path)
            self.directory_changed.emit(path)
            self.update_status(f"Current directory: {path}")

    def navigate_to_path(self):
        """Navigate to the path entered in the path edit"""
        path = self.path_edit.text().strip()
        if os.path.exists(path) and os.path.isdir(path):
            self.set_root_path(path)
        else:
            QMessageBox.warning(self, "Invalid Path", f"Path does not exist: {path}")
            self.path_edit.setText(self.current_path)

    def go_up(self):
        """Go up one directory level"""
        parent_path = os.path.dirname(self.current_path)
        if parent_path != self.current_path:  # Avoid root directory issues
            self.set_root_path(parent_path)

    def go_home(self):
        """Navigate to home directory"""
        self.set_root_path(QDir.homePath())

    def browse_directory(self):
        """Open directory browser dialogs"""
        from PyQt6.QtWidgets import QFileDialog
        directory = QFileDialog.getExistingDirectory(
            self, "Select Directory", self.current_path
        )
        if directory:
            self.set_root_path(directory)

    def filter_files(self):
        """Apply search filter to files"""
        search_text = self.search_edit.text().strip()
        if search_text:
            # Implement search functionality
            self.update_status(f"Searching for: {search_text}")
        else:
            self.update_status("Ready")

    def perform_search(self):
        """Perform detailed search"""
        search_text = self.search_edit.text().strip()
        if search_text:
            self.update_status(f"Searching for '{search_text}'...")
            # Implement comprehensive search

    def on_item_clicked(self, index: QModelIndex):
        """Handle item click in tree view"""
        if index.isValid():
            file_path = self.file_model.filePath(index)
            self.file_selected.emit(file_path)

            if self.file_model.isDir(index):
                self.update_status(f"Directory: {file_path}")
            else:
                file_info = self.file_model.fileInfo(index)
                size = file_info.size()
                self.update_status(f"File: {file_path} ({self.format_file_size(size)})")

    def on_item_double_clicked(self, index: QModelIndex):
        """Handle item double-click in tree view"""
        if index.isValid():
            file_path = self.file_model.filePath(index)

            if self.file_model.isDir(index):
                # Navigate into directory
                self.set_root_path(file_path)
            else:
                # Open file
                self.file_double_clicked.emit(file_path)

    def validate_file_for_NCExplorer(self, filepath):
        """Validate if a file is compatible with NCExplorer operations"""
        if not os.path.exists(filepath):
            return False, "File does not exist"

        if not os.access(filepath, os.R_OK):
            return False, "No read permission"

        ext = os.path.splitext(filepath)[1].lower()
        NCExplorer_extensions = self.supported_formats['All NCExplorer Files']

        if ext not in NCExplorer_extensions:
            return False, f"File format {ext} not supported by NCExplorer"

        return True, "File is compatible with NCExplorer"

    def update_status(self, message):
        """Update status label"""
        self.status_label.setText(message)

    def format_file_size(self, size_bytes):
        """Format file size in human-readable format"""
        if size_bytes == 0:
            return "0 B"

        size_names = ["B", "KB", "MB", "GB", "TB"]
        import math
        i = int(math.floor(math.log(size_bytes, 1024)))
        p = math.pow(1024, i)
        s = round(size_bytes / p, 2)
        return f"{s} {size_names[i]}"