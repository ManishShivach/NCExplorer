import os

from PyQt6.QtWidgets import QMenuBar, QMenu
from PyQt6.QtGui import QAction, QActionGroup
from PyQt6.QtCore import Qt

from .shortcuts import key_sequence


class MenuBar(QMenuBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_window = parent
        self.setup_menus()

    def setup_menus(self):
        # Key sequences come from gui/shortcuts.py so the menus and the
        # cheat sheet cannot disagree; these actions own their sequence (the
        # registry deliberately creates no QShortcut for them).
        self.registry_actions = {}

        # File Menu
        file_menu = self.addMenu("&File")

        self.new_project_action = QAction("&New Project", self)
        self.new_project_action.setShortcut(key_sequence("project.new"))
        self.new_project_action.triggered.connect(self.main_window.new_project)
        file_menu.addAction(self.new_project_action)
        self.registry_actions["project.new"] = self.new_project_action

        self.open_project_action = QAction("Open &Project...", self)
        self.open_project_action.setShortcut(key_sequence("project.open"))
        self.open_project_action.triggered.connect(self.main_window.open_project)
        file_menu.addAction(self.open_project_action)
        self.registry_actions["project.open"] = self.open_project_action

        self.save_project_action = QAction("Save Pro&ject", self)
        self.save_project_action.setShortcut(key_sequence("project.save"))
        self.save_project_action.triggered.connect(self.main_window.save_project)
        file_menu.addAction(self.save_project_action)
        self.registry_actions["project.save"] = self.save_project_action

        self.save_project_as_action = QAction("Save Project &As...", self)
        self.save_project_as_action.setShortcut(key_sequence("project.save_as"))
        self.save_project_as_action.triggered.connect(self.main_window.save_project_as)
        file_menu.addAction(self.save_project_as_action)
        self.registry_actions["project.save_as"] = self.save_project_as_action

        file_menu.addSeparator()

        self.open_action = QAction("&Open...", self)
        self.open_action.setShortcut(key_sequence("file.open"))
        self.open_action.triggered.connect(self.main_window.open_file)
        file_menu.addAction(self.open_action)
        self.registry_actions["file.open"] = self.open_action

        self.recent_menu = QMenu("Open &Recent", self)
        file_menu.addMenu(self.recent_menu)
        # Rebuilt every time it is opened rather than kept in sync from the load
        # path: a file opened a moment ago cannot be missing from it, and one
        # deleted since cannot linger in it.
        self.recent_menu.aboutToShow.connect(self.rebuild_recent_menu)
        self.rebuild_recent_menu()

        self.save_action = QAction("&Save", self)
        self.save_action.setShortcut(key_sequence("file.save"))
        self.save_action.triggered.connect(self.main_window.save_file)
        file_menu.addAction(self.save_action)
        self.registry_actions["file.save"] = self.save_action

        file_menu.addSeparator()

        self.batch_action = QAction("&Batch Process...", self)
        self.batch_action.setShortcut(key_sequence("tools.batch"))
        self.batch_action.setToolTip("Apply a recorded pipeline to a folder of files")
        self.batch_action.triggered.connect(self.main_window.open_batch_dialog)
        file_menu.addAction(self.batch_action)
        self.registry_actions["tools.batch"] = self.batch_action

        file_menu.addSeparator()

        self.exit_action = QAction("E&xit", self)
        self.exit_action.setShortcut(key_sequence("file.quit"))
        self.exit_action.triggered.connect(self.main_window.close)
        file_menu.addAction(self.exit_action)
        self.registry_actions["file.quit"] = self.exit_action

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

        # Map overlays. All start off; the canvas managers own the state.
        self.colorbar_action = QAction("&Colorbar", self)
        self.colorbar_action.setCheckable(True)
        self.colorbar_action.setShortcut(key_sequence("view.colorbar"))
        self.colorbar_action.triggered.connect(self.main_window.toggle_colorbar)
        view_menu.addAction(self.colorbar_action)
        self.registry_actions["view.colorbar"] = self.colorbar_action

        self.colorbar_position_menu = QMenu("Colorbar &Position", self)
        self.colorbar_position_group = QActionGroup(self)
        self.colorbar_position_group.setExclusive(True)
        self.colorbar_position_actions = {}
        for position in ("right", "left", "bottom", "top"):
            action = QAction(position.capitalize(), self)
            action.setCheckable(True)
            action.setChecked(position == "right")
            action.setActionGroup(self.colorbar_position_group)
            # default=position binds the loop variable per iteration.
            action.triggered.connect(
                lambda _checked, name=position: self.main_window.set_colorbar_position(name)
            )
            self.colorbar_position_menu.addAction(action)
            self.colorbar_position_actions[position] = action
        view_menu.addMenu(self.colorbar_position_menu)

        self.graticule_action = QAction("&Graticule", self)
        self.graticule_action.setCheckable(True)
        self.graticule_action.setShortcut(key_sequence("view.graticule"))
        self.graticule_action.triggered.connect(self.main_window.toggle_graticule)
        view_menu.addAction(self.graticule_action)
        self.registry_actions["view.graticule"] = self.graticule_action

        self.scalebar_action = QAction("Scale &Bar", self)
        self.scalebar_action.setCheckable(True)
        self.scalebar_action.triggered.connect(self.main_window.toggle_scalebar)
        view_menu.addAction(self.scalebar_action)

        view_menu.addSeparator()

        # Starts unchecked: the log dock is hidden until asked for.
        self.log_dock_action = QAction("&Log", self)
        self.log_dock_action.setCheckable(True)
        self.log_dock_action.setShortcut(key_sequence("view.log"))
        self.log_dock_action.triggered.connect(self.main_window.toggle_log_dock)
        view_menu.addAction(self.log_dock_action)
        self.registry_actions["view.log"] = self.log_dock_action

        # Both analysis docks start hidden too, so their entries start unchecked
        # and are kept in step by the docks' own visibilityChanged handlers.
        self.animation_dock_action = QAction("&Animation", self)
        self.animation_dock_action.setCheckable(True)
        self.animation_dock_action.setShortcut(key_sequence("view.animation"))
        self.animation_dock_action.triggered.connect(self.main_window.toggle_animation_dock)
        view_menu.addAction(self.animation_dock_action)
        self.registry_actions["view.animation"] = self.animation_dock_action

        self.plot_dock_action = QAction("&Plot", self)
        self.plot_dock_action.setCheckable(True)
        self.plot_dock_action.setShortcut(key_sequence("view.plot"))
        self.plot_dock_action.triggered.connect(self.main_window.toggle_plot_dock)
        view_menu.addAction(self.plot_dock_action)
        self.registry_actions["view.plot"] = self.plot_dock_action

        self.stats_dock_action = QAction("Stat&istics", self)
        self.stats_dock_action.setCheckable(True)
        self.stats_dock_action.setShortcut(key_sequence("view.statistics"))
        self.stats_dock_action.triggered.connect(self.main_window.toggle_stats_dock)
        view_menu.addAction(self.stats_dock_action)
        self.registry_actions["view.statistics"] = self.stats_dock_action

        self.compare_dock_action = QAction("Co&mpare", self)
        self.compare_dock_action.setCheckable(True)
        self.compare_dock_action.setShortcut(key_sequence("view.compare"))
        self.compare_dock_action.triggered.connect(self.main_window.toggle_compare_dock)
        view_menu.addAction(self.compare_dock_action)
        self.registry_actions["view.compare"] = self.compare_dock_action

        self.session_dock_action = QAction("Sessio&n", self)
        self.session_dock_action.setCheckable(True)
        self.session_dock_action.setShortcut(key_sequence("view.session"))
        self.session_dock_action.triggered.connect(self.main_window.toggle_session_dock)
        view_menu.addAction(self.session_dock_action)
        self.registry_actions["view.session"] = self.session_dock_action

        view_menu.addSeparator()

        self.fullscreen_action = QAction("&Full Screen", self)
        self.fullscreen_action.setShortcut(key_sequence("view.fullscreen"))
        self.fullscreen_action.triggered.connect(self.main_window.toggle_fullscreen)
        view_menu.addAction(self.fullscreen_action)
        self.registry_actions["view.fullscreen"] = self.fullscreen_action

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

        # Model Menu
        #
        # A menu of its own rather than an entry under View. View toggles panels
        # that annotate the map; the model builder is a separate window with its
        # own documents (.ncmodel), its own run, and its own exports — the same
        # weight as Layer, and filed the same way.
        model_menu = self.addMenu("&Model")

        self.model_builder_action = QAction("Model &Builder", self)
        self.model_builder_action.setCheckable(True)
        self.model_builder_action.setShortcut(key_sequence("model.builder"))
        self.model_builder_action.setToolTip(
            "Wire operators together into a processing chain")
        self.model_builder_action.triggered.connect(
            self.main_window.toggle_model_builder_window)
        model_menu.addAction(self.model_builder_action)
        self.registry_actions["model.builder"] = self.model_builder_action

        model_menu.addSeparator()

        self.open_model_action = QAction("&Open Model...", self)
        self.open_model_action.setToolTip("Open a saved processing graph")
        self.open_model_action.triggered.connect(self.main_window.open_model_file)
        model_menu.addAction(self.open_model_action)

        self.save_model_action = QAction("&Save Model...", self)
        self.save_model_action.setToolTip("Save the current processing graph")
        self.save_model_action.triggered.connect(self.main_window.save_model_file)
        model_menu.addAction(self.save_model_action)

        model_menu.addSeparator()

        self.model_from_session_action = QAction("Build from &Session", self)
        self.model_from_session_action.setToolTip(
            "Turn this session's recorded steps into a model you can branch")
        self.model_from_session_action.triggered.connect(
            self.main_window.model_from_session)
        model_menu.addAction(self.model_from_session_action)

        self.model_folder_action = QAction("Add Inputs from &Folder...", self)
        self.model_folder_action.setToolTip(
            "Pick a folder and choose which of its files to read")
        self.model_folder_action.triggered.connect(
            self.main_window.model_add_inputs_from_folder)
        model_menu.addAction(self.model_folder_action)

        model_menu.addSeparator()

        self.model_export_menu = QMenu("&Export Model As", self)
        for fmt, label in (("shell", "Shell Script..."),
                           ("makefile", "Makefile..."),
                           ("notebook", "Jupyter Notebook...")):
            action = self.model_export_menu.addAction(label)
            # default=fmt binds the loop variable per iteration.
            action.triggered.connect(
                lambda _checked, f=fmt: self.main_window.export_model(f))
        model_menu.addMenu(self.model_export_menu)

        self.model_batch_action = QAction("&Run Model over a Folder...", self)
        self.model_batch_action.setToolTip(
            "Apply the model to every file in a folder")
        self.model_batch_action.triggered.connect(self.main_window.run_model_over_folder)
        model_menu.addAction(self.model_batch_action)

        # Help Menu
        help_menu = self.addMenu("&Help")

        self.about_action = QAction("&About", self)
        self.about_action.triggered.connect(self.main_window.show_about)
        help_menu.addAction(self.about_action)

        self.docs_action = QAction("&Documentation", self)
        self.docs_action.triggered.connect(self.main_window.show_documentation)
        help_menu.addAction(self.docs_action)

        self.shortcuts_action = QAction("&Keyboard Shortcuts", self)
        self.shortcuts_action.setShortcut(key_sequence("help.shortcuts"))
        self.shortcuts_action.triggered.connect(self.main_window.show_shortcuts_dialog)
        help_menu.addAction(self.shortcuts_action)
        self.registry_actions["help.shortcuts"] = self.shortcuts_action

    def rebuild_recent_menu(self):
        """Fill File > Open Recent from the store, dropping vanished files.

        Reads ``main_window.recent_files``, so the main window has to create the
        store before it constructs the menu bar.
        """
        self.recent_menu.clear()

        paths = self.main_window.recent_files.paths()
        if not paths:
            placeholder = self.recent_menu.addAction("No recent files")
            placeholder.setEnabled(False)
            return

        for path in paths:
            # '&' in a file name would otherwise be eaten as a mnemonic marker.
            action = self.recent_menu.addAction(os.path.basename(path).replace("&", "&&"))
            action.setToolTip(path)
            action.setStatusTip(path)
            # default=path binds the loop variable per iteration.
            action.triggered.connect(
                lambda _checked, target=path: self.main_window.open_recent_file(target)
            )

        self.recent_menu.addSeparator()
        clear_action = self.recent_menu.addAction("Clear Recent Files")
        clear_action.triggered.connect(self.main_window.clear_recent_files)