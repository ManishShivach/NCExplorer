"""Central keyboard-shortcut registry and the cheat-sheet dialog.

Every binding in the application is declared once in ``SHORTCUTS`` and installed
by :func:`register_shortcuts`, so the Help > Keyboard Shortcuts sheet can never
drift from what is actually bound.

Two constraints drive the design:

* Bindings that a menu ``QAction`` already owns (``owner="action"``) must not
  also get a ``QShortcut``. Qt reports the sequence as ambiguous and then
  *neither* copy fires — the menu bar reads its sequences from here instead.
* Bare keys (Backspace, the arrows, ``?``) are declared ``scope="canvas"`` and
  installed on the canvas with ``WidgetWithChildrenShortcut``. Window-scoped
  they would swallow ordinary text editing in the operator parameter fields.
"""

import logging
from dataclasses import dataclass

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QGridLayout, QLabel, QScrollArea, QVBoxLayout, QWidget
)

logger = logging.getLogger(__name__)

WINDOW = "window"
CANVAS = "canvas"


@dataclass(frozen=True)
class ShortcutSpec:
    """One declared binding.

    ``keys`` holds every accepted sequence for the action; ``callback`` is the
    name of a no-argument method on the main window. ``owner="action"`` marks
    sequences owned by a menu QAction (see the module docstring).
    """

    id: str
    keys: tuple[str, ...]
    label: str
    category: str
    scope: str = WINDOW
    callback: str = ""
    owner: str = "shortcut"


SHORTCUTS: tuple[ShortcutSpec, ...] = (
    # --- File -------------------------------------------------------------
    ShortcutSpec("file.open", ("Ctrl+O",), "Open file…", "File",
                 WINDOW, "open_file", owner="action"),
    ShortcutSpec("file.save", ("Ctrl+S",), "Save output", "File",
                 WINDOW, "save_file", owner="action"),
    ShortcutSpec("file.quit", ("Ctrl+Q",), "Quit", "File",
                 WINDOW, "close", owner="action"),

    # --- Project ----------------------------------------------------------
    # Ctrl+Shift+S belongs to Save Project, which is why the statistics panel
    # below moved to Ctrl+Shift+I: two actions declaring one sequence makes it
    # ambiguous, and Qt then fires neither of them.
    ShortcutSpec("project.new", ("Ctrl+Shift+N",), "New project", "Project",
                 WINDOW, "new_project", owner="action"),
    ShortcutSpec("project.open", ("Ctrl+Shift+O",), "Open project…", "Project",
                 WINDOW, "open_project", owner="action"),
    ShortcutSpec("project.save", ("Ctrl+Shift+S",), "Save project", "Project",
                 WINDOW, "save_project", owner="action"),
    ShortcutSpec("project.save_as", ("Ctrl+Alt+S",), "Save project as…", "Project",
                 WINDOW, "save_project_as", owner="action"),

    # --- View -------------------------------------------------------------
    ShortcutSpec("view.fullscreen", ("F11",), "Toggle full screen", "View",
                 WINDOW, "toggle_fullscreen", owner="action"),
    ShortcutSpec("view.colorbar", ("Ctrl+B",), "Toggle colorbar", "View",
                 WINDOW, "toggle_colorbar", owner="action"),
    ShortcutSpec("view.graticule", ("Ctrl+G",), "Toggle graticule", "View",
                 WINDOW, "toggle_graticule", owner="action"),
    ShortcutSpec("view.log", ("Ctrl+L",), "Toggle log panel", "View",
                 WINDOW, "toggle_log_dock", owner="action"),
    ShortcutSpec("view.animation", ("Ctrl+Shift+A",), "Toggle animation player", "View",
                 WINDOW, "toggle_animation_dock", owner="action"),
    ShortcutSpec("view.plot", ("Ctrl+Shift+P",), "Toggle plot panel", "View",
                 WINDOW, "toggle_plot_dock", owner="action"),
    # Ctrl+Shift+I, not Ctrl+Shift+S: Save Project owns that one now. The 'i'
    # matches the menu entry's own mnemonic, "Stat&istics".
    ShortcutSpec("view.statistics", ("Ctrl+Shift+I",), "Toggle statistics panel", "View",
                 WINDOW, "toggle_stats_dock", owner="action"),
    ShortcutSpec("view.compare", ("Ctrl+Shift+C",), "Toggle comparison panel", "View",
                 WINDOW, "toggle_compare_dock", owner="action"),
    ShortcutSpec("view.session", ("Ctrl+Shift+R",), "Toggle session panel", "View",
                 WINDOW, "toggle_session_dock", owner="action"),

    # --- Model -------------------------------------------------------------
    # Ctrl+Shift+M was the only obvious candidate left: A, B, C, I, L, N, O, P,
    # R and S are all taken above, as are Ctrl+Alt+S, Ctrl+K, Ctrl+B, Ctrl+G,
    # Ctrl+L and Ctrl+0. Owned by the Model menu action, so no QShortcut is made
    # for it — see the module docstring.
    #
    # The builder's own F11 (full screen) is deliberately not declared here: it
    # is installed on that window with WidgetWithChildrenShortcut, so it does not
    # compete with the main window's own F11, and a registry that installs on the
    # main window could not express that.
    ShortcutSpec("model.builder", ("Ctrl+Shift+M",), "Open the model builder",
                 "Model", WINDOW, "toggle_model_builder", owner="action"),

    # --- Animation --------------------------------------------------------
    # Canvas-scoped for the same reason as the bare navigation keys: Space and
    # the comma/period pair are ordinary characters, and window-scoped bindings
    # would swallow them while the user is typing an operator parameter.
    ShortcutSpec("anim.play", ("Space",), "Play / pause animation", "Animation",
                 CANVAS, "toggle_animation_play"),
    ShortcutSpec("anim.step_back", (",",), "Previous frame", "Animation",
                 CANVAS, "animation_step_back"),
    ShortcutSpec("anim.step_forward", (".",), "Next frame", "Animation",
                 CANVAS, "animation_step_forward"),

    # --- Navigation -------------------------------------------------------
    # Ctrl+= and Ctrl++ arrive as different sequences depending on the
    # keyboard, so all the usual spellings are bound.
    ShortcutSpec("nav.zoom_in", ("Ctrl+=", "Ctrl++", "Ctrl+Shift+="), "Zoom in",
                 "Navigation", WINDOW, "zoom_in_map"),
    ShortcutSpec("nav.zoom_out", ("Ctrl+-",), "Zoom out",
                 "Navigation", WINDOW, "zoom_out_map"),
    ShortcutSpec("nav.full_extent", ("Ctrl+0",), "Zoom to full extent",
                 "Navigation", WINDOW, "zoom_full_extent_map"),
    ShortcutSpec("nav.previous", ("Backspace",), "Previous extent",
                 "Navigation", CANVAS, "zoom_previous_extent"),
    ShortcutSpec("nav.pan_left", ("Left",), "Pan west",
                 "Navigation", CANVAS, "pan_map_left"),
    ShortcutSpec("nav.pan_right", ("Right",), "Pan east",
                 "Navigation", CANVAS, "pan_map_right"),
    ShortcutSpec("nav.pan_up", ("Up",), "Pan north",
                 "Navigation", CANVAS, "pan_map_up"),
    ShortcutSpec("nav.pan_down", ("Down",), "Pan south",
                 "Navigation", CANVAS, "pan_map_down"),

    # --- Layer ------------------------------------------------------------
    ShortcutSpec("layer.zoom_to", ("Ctrl+Shift+L",), "Zoom to active layer",
                 "Layer", WINDOW, "zoom_to_active_layer"),

    # --- Tools ------------------------------------------------------------
    # No menu QAction owns this one, so it stays owner="shortcut"; adding an
    # action with the same sequence later would make it ambiguous and silence
    # both copies (see the module docstring).
    ShortcutSpec("tools.palette", ("Ctrl+K",), "Find an operator (command palette)",
                 "Tools", WINDOW, "show_command_palette"),
    ShortcutSpec("tools.batch", ("Ctrl+Shift+B",), "Batch process a folder",
                 "Tools", WINDOW, "open_batch_dialog", owner="action"),

    # --- Help -------------------------------------------------------------
    ShortcutSpec("help.shortcuts", ("F1",), "Keyboard shortcuts", "Help",
                 WINDOW, "show_shortcuts_dialog", owner="action"),
    # '?' is canvas-scoped so typing a literal question mark into a parameter
    # field still works.
    ShortcutSpec("help.shortcuts_alt", ("?",), "Keyboard shortcuts", "Help",
                 CANVAS, "show_shortcuts_dialog"),
)

CATEGORY_ORDER = ("File", "Project", "View", "Navigation", "Animation", "Layer",
                  "Model", "Tools", "Help")


def spec_by_id(shortcut_id: str) -> ShortcutSpec:
    """Look up one declared binding. Raises KeyError if it does not exist."""
    for spec in SHORTCUTS:
        if spec.id == shortcut_id:
            return spec
    raise KeyError(shortcut_id)


def key_sequence(shortcut_id: str) -> QKeySequence:
    """Primary key sequence of a binding, for a QAction's setShortcut()."""
    return QKeySequence(spec_by_id(shortcut_id).keys[0])


def display_keys(spec: ShortcutSpec) -> str:
    """Platform-native rendering of every sequence bound to one action."""
    return " / ".join(
        QKeySequence(k).toString(QKeySequence.SequenceFormat.NativeText) for k in spec.keys
    )


def register_shortcuts(main_window) -> dict[str, list]:
    """Install every declared binding on ``main_window``.

    Returns the objects that own each binding, keyed by shortcut id — created
    QShortcuts, plus the menu QActions that already own their sequence.
    """
    canvas = getattr(main_window, "geo_canvas", None)
    menu_actions = getattr(getattr(main_window, "menu_bar", None), "registry_actions", {})
    installed: dict[str, list] = {}

    for spec in SHORTCUTS:
        if spec.owner == "action":
            # Already bound by the menu bar; a second QShortcut here would make
            # the sequence ambiguous and kill both copies.
            action = menu_actions.get(spec.id)
            if action is None:
                logger.warning("No menu action found for shortcut %s", spec.id)
                continue
            installed[spec.id] = [action]
            continue

        callback = getattr(main_window, spec.callback, None)
        if callback is None:
            logger.warning("Shortcut %s names missing callback %s", spec.id, spec.callback)
            continue

        parent = canvas if spec.scope == CANVAS else main_window
        if parent is None:
            logger.warning("Shortcut %s needs a canvas that does not exist yet", spec.id)
            continue

        objects = []
        for keys in spec.keys:
            shortcut = QShortcut(QKeySequence(keys), parent)
            if spec.scope == CANVAS:
                shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback)
            objects.append(shortcut)
        installed[spec.id] = objects

    return installed


def grouped_shortcuts() -> list[tuple[str, list[ShortcutSpec]]]:
    """The registry grouped for display, in CATEGORY_ORDER."""
    groups: dict[str, list[ShortcutSpec]] = {}
    for spec in SHORTCUTS:
        groups.setdefault(spec.category, []).append(spec)

    ordered = [(name, groups[name]) for name in CATEGORY_ORDER if name in groups]
    ordered += [(name, specs) for name, specs in groups.items() if name not in CATEGORY_ORDER]
    return ordered


class ShortcutCheatSheet(QDialog):
    """Modal list of every binding, built by iterating the registry."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Keyboard Shortcuts")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)

        content = QWidget()
        grid = QGridLayout(content)
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(18)

        row = 0
        for category, specs in grouped_shortcuts():
            heading = QLabel(f"<b>{category}</b>")
            grid.addWidget(heading, row, 0, 1, 2)
            row += 1
            for spec in specs:
                keys = QLabel(display_keys(spec))
                keys.setStyleSheet("font-family: monospace;")
                grid.addWidget(keys, row, 0)
                grid.addWidget(QLabel(spec.label), row, 1)
                row += 1
            grid.addWidget(QLabel(""), row, 0)
            row += 1

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        layout.addWidget(scroll)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
