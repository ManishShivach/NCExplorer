"""The shortcut registry: no duplicates, no dangling callbacks, no ambiguity."""

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QLineEdit

from ncexplorer_toolkit.gui import shortcuts as reg

LEGACY_KEYS = ("Ctrl+O", "Ctrl+S", "Ctrl+Q", "F11")


@pytest.fixture(scope="module")
def main_window(qapp):
    from ncexplorer_toolkit.gui.main_window import NCExplorerOperatorGUI

    window = NCExplorerOperatorGUI()
    window.resize(1000, 700)
    window.show()
    window.activateWindow()
    qapp.processEvents()
    yield window
    window.close()


def _all_declared_keys():
    return [keys for spec in reg.SHORTCUTS for keys in spec.keys]


def test_no_duplicate_key_sequences():
    declared = [QKeySequence(k).toString() for k in _all_declared_keys()]
    duplicates = {k for k in declared if declared.count(k) > 1}
    assert not duplicates, f"duplicate sequences: {duplicates}"


def test_ids_are_unique():
    ids = [spec.id for spec in reg.SHORTCUTS]
    assert len(set(ids)) == len(ids)


@pytest.mark.parametrize("keys", _all_declared_keys())
def test_key_sequence_parses(keys):
    sequence = QKeySequence(keys)
    assert not sequence.isEmpty()
    assert sequence[0].key() != Qt.Key.Key_unknown


def test_every_callback_exists(main_window):
    missing = [
        spec.id for spec in reg.SHORTCUTS if not hasattr(main_window, spec.callback)
    ]
    assert not missing, f"shortcuts naming missing callbacks: {missing}"


def test_scopes_are_known():
    assert {spec.scope for spec in reg.SHORTCUTS} <= {reg.WINDOW, reg.CANVAS}


def test_canvas_scoped_shortcuts_use_widget_context(main_window):
    """Bare keys must not be window-scoped or they break text editing."""
    installed = main_window.registered_shortcuts
    canvas_specs = [s for s in reg.SHORTCUTS if s.scope == reg.CANVAS]
    assert canvas_specs

    for spec in canvas_specs:
        for shortcut in installed[spec.id]:
            assert isinstance(shortcut, QShortcut)
            assert shortcut.context() == Qt.ShortcutContext.WidgetWithChildrenShortcut
            assert shortcut.parent() is main_window.geo_canvas


def test_bare_keys_are_canvas_scoped():
    bare = {"Backspace", "Left", "Right", "Up", "Down", "?"}
    for spec in reg.SHORTCUTS:
        if bare & set(spec.keys):
            assert spec.scope == reg.CANVAS, f"{spec.id} must be canvas-scoped"


def test_legacy_bindings_still_present(main_window):
    installed = main_window.registered_shortcuts
    declared = {
        QKeySequence(k).toString(): spec
        for spec in reg.SHORTCUTS
        for k in spec.keys
    }
    for keys in LEGACY_KEYS:
        name = QKeySequence(keys).toString()
        assert name in declared, f"{keys} disappeared from the registry"
        assert installed[declared[name].id], f"{keys} was not installed"


def test_legacy_bindings_owned_by_exactly_one_object(main_window):
    """A QAction and a QShortcut on one sequence make it ambiguous: neither fires."""
    installed = main_window.registered_shortcuts
    for keys in LEGACY_KEYS:
        target = QKeySequence(keys).toString()
        owners = []
        for objects in installed.values():
            for obj in objects:
                if isinstance(obj, QShortcut):
                    if obj.key().toString() == target:
                        owners.append(obj)
                elif obj.shortcut().toString() == target:
                    owners.append(obj)
        assert len(owners) == 1, f"{keys} is owned by {len(owners)} objects"


def test_menu_actions_read_sequences_from_registry(main_window):
    menu_bar = main_window.menu_bar
    assert menu_bar.open_action.shortcut() == reg.key_sequence("file.open")
    assert menu_bar.save_action.shortcut() == reg.key_sequence("file.save")
    assert menu_bar.exit_action.shortcut() == reg.key_sequence("file.quit")
    assert menu_bar.fullscreen_action.shortcut() == reg.key_sequence("view.fullscreen")
    assert menu_bar.shortcuts_action.shortcut() == reg.key_sequence("help.shortcuts")


def test_zoom_in_binds_every_spelling():
    """Ctrl+= and Ctrl++ arrive differently per keyboard; all must be bound."""
    spec = reg.spec_by_id("nav.zoom_in")
    assert len(spec.keys) >= 3


def test_cheat_sheet_lists_every_binding(qapp):
    grouped = reg.grouped_shortcuts()
    listed = [spec.id for _, specs in grouped for spec in specs]
    assert sorted(listed) == sorted(spec.id for spec in reg.SHORTCUTS)

    dialog = reg.ShortcutCheatSheet()
    try:
        labels = dialog.findChildren(type(dialog.children()[0]))  # smoke: builds
        assert dialog.windowTitle() == "Keyboard Shortcuts"
        assert labels is not None
    finally:
        dialog.deleteLater()


def test_display_keys_is_human_readable():
    spec = reg.spec_by_id("nav.zoom_out")
    assert reg.display_keys(spec)


def test_bare_keys_drive_the_map_when_the_canvas_has_focus(main_window, qapp):
    canvas = main_window.geo_canvas
    canvas.setFocus()
    qapp.processEvents()
    assert canvas.hasFocus()

    canvas.zoom_history.clear()
    canvas.set_extent([-100, -20, 0, 40])
    # Panning deliberately does not record history (neither does wheel zoom), so
    # Backspace goes back to the last extent that set_extent recorded.
    recorded = list(canvas.zoom_history[-1])
    before_pan = list(canvas.extent)

    QTest.keyClick(canvas, Qt.Key.Key_Left)
    qapp.processEvents()
    assert canvas.extent != before_pan, "arrow keys must pan the focused canvas"

    QTest.keyClick(canvas, Qt.Key.Key_Backspace)
    qapp.processEvents()
    assert canvas.extent == pytest.approx(recorded), "Backspace must walk the history"


def _parameter_fields(main_window, operator):
    main_window.show_operator_parameters(operator)
    return main_window.params_container.findChildren(QLineEdit)


def test_text_editing_survives_the_navigation_shortcuts(main_window, qapp):
    """Trap: window-scoped Backspace/arrows would break the parameter forms."""
    fields = _parameter_fields(main_window, "sellonlatbox")
    assert fields

    canvas = main_window.geo_canvas
    canvas.set_extent([-100, -20, 0, 40])
    extent_before = list(canvas.extent)

    field = fields[0]
    field.clear()
    field.setFocus()
    qapp.processEvents()
    assert field.hasFocus()

    QTest.keyClicks(field, "12.5")
    assert field.text() == "12.5"

    QTest.keyClick(field, Qt.Key.Key_Backspace)
    assert field.text() == "12.", "Backspace no longer deletes in a parameter field"

    QTest.keyClick(field, Qt.Key.Key_Left)
    QTest.keyClick(field, Qt.Key.Key_Left)
    assert field.cursorPosition() == 1, "arrow keys no longer move the cursor"

    QTest.keyClick(field, Qt.Key.Key_Home)
    QTest.keyClicks(field, "-")
    assert field.text() == "-12."

    for key in (
        Qt.Key.Key_Left,
        Qt.Key.Key_Right,
        Qt.Key.Key_Up,
        Qt.Key.Key_Down,
        Qt.Key.Key_Backspace,
    ):
        QTest.keyClick(field, key)
    qapp.processEvents()
    assert canvas.extent == pytest.approx(extent_before), "editing text moved the map"


def test_question_mark_types_literally_in_a_text_field(main_window, qapp):
    fields = [f for f in _parameter_fields(main_window, "expr") if f.validator() is None]
    assert fields

    field = fields[0]
    field.clear()
    field.setFocus()
    qapp.processEvents()

    QTest.keyClicks(field, "why?")
    qapp.processEvents()

    assert field.text() == "why?"
    dialogs = [
        w
        for w in qapp.topLevelWidgets()
        if w.isVisible() and isinstance(w, reg.ShortcutCheatSheet)
    ]
    assert not dialogs, "'?' opened the cheat sheet while typing"
