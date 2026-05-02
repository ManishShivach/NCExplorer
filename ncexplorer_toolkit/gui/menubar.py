from PyQt6.QtWidgets import QMenuBar, QMenu
from PyQt6.QtGui import QKeySequence, QAction
from PyQt6.QtCore import Qt


class MenuBar(QMenuBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_window = parent
        self.setup_menus()

    def setup_menus(self):
        # File Menu
        file_menu = self.addMenu("&File")

        self.open_action = QAction("&Open...", self)
        self.open_action.setShortcut(QKeySequence("Ctrl+O"))
        self.open_action.triggered.connect(self.main_window.open_file)
        file_menu.addAction(self.open_action)

        self.save_action = QAction("&Save", self)
        self.save_action.setShortcut(QKeySequence("Ctrl+S"))
        self.save_action.triggered.connect(self.main_window.save_file)
        file_menu.addAction(self.save_action)

        file_menu.addSeparator()

        self.exit_action = QAction("E&xit", self)
        self.exit_action.setShortcut(QKeySequence("Ctrl+Q"))
        self.exit_action.triggered.connect(self.main_window.close)
        file_menu.addAction(self.exit_action)

        # View Menu
        view_menu = self.addMenu("&View")

        self.toolbar_action = QAction("&Toolbar", self)
        self.toolbar_action.setCheckable(True)
        self.toolbar_action.setChecked(True)
        self.toolbar_action.triggered.connect(self.main_window.toggle_toolbar)
        view_menu.addAction(self.toolbar_action)

        self.statusbar_action = QAction("&Status Bar", self)
        self.statusbar_action.setCheckable(True)
        self.statusbar_action.setChecked(True)
        self.statusbar_action.triggered.connect(self.main_window.toggle_statusbar)
        view_menu.addAction(self.statusbar_action)

        view_menu.addSeparator()

        self.fullscreen_action = QAction("&Full Screen", self)
        self.fullscreen_action.setShortcut(QKeySequence("F11"))
        self.fullscreen_action.triggered.connect(self.main_window.toggle_fullscreen)
        view_menu.addAction(self.fullscreen_action)

        # Layer Menu
        layer_menu = self.addMenu("&Layer")

        self.add_layer_action = QAction("&Add Layer...", self)
        self.add_layer_action.triggered.connect(self.main_window.add_layer)
        layer_menu.addAction(self.add_layer_action)

        self.remove_layer_action = QAction("&Remove Layer", self)
        self.remove_layer_action.triggered.connect(self.main_window.remove_layer)
        layer_menu.addAction(self.remove_layer_action)

        layer_menu.addSeparator()

        self.layer_properties_action = QAction("Layer &Properties...", self)
        self.layer_properties_action.triggered.connect(self.main_window.show_layer_properties)
        layer_menu.addAction(self.layer_properties_action)

        # Help Menu
        help_menu = self.addMenu("&Help")

        self.about_action = QAction("&About", self)
        self.about_action.triggered.connect(self.main_window.show_about)
        help_menu.addAction(self.about_action)

        self.docs_action = QAction("&Documentation", self)
        self.docs_action.triggered.connect(self.main_window.show_documentation)
        help_menu.addAction(self.docs_action)