# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""End-to-end smoke test: the real main window, driven through every new control.

Constructing NCExplorerOperatorGUI is slow (it builds the whole CDO operator
UI), so the window is built once for the module and the checks share it.
"""

import sys

import pytest


@pytest.fixture(scope="module")
def window(qapp):
    from ncexplorer_toolkit.gui.main_window import NCExplorerOperatorGUI

    gui = NCExplorerOperatorGUI()
    yield gui
    gui.close()
    gui.deleteLater()


def test_window_constructs_with_the_readout(window):
    assert window.cursor_readout is not None
    # A permanent widget, not a transient showMessage() — that call expires and
    # is shared with a dozen other status messages.
    assert window.cursor_readout.parent() is not None


def test_new_view_menu_actions_exist(window):
    menu_bar = window.menu_bar

    assert menu_bar.colorbar_action.isCheckable()
    assert menu_bar.graticule_action.isCheckable()
    assert menu_bar.scalebar_action.isCheckable()
    assert menu_bar.colorbar_action.shortcut().toString() == "Ctrl+B"
    assert menu_bar.graticule_action.shortcut().toString() == "Ctrl+G"
    assert set(menu_bar.colorbar_position_actions) == {"right", "left", "bottom", "top"}


def test_shortcuts_are_registered_once(window):
    """Registry entries must exist, and must not be double-bound.

    A QAction that owns its sequence plus a QShortcut for the same keys makes
    the binding ambiguous and then neither fires.
    """
    from ncexplorer_toolkit.gui.shortcuts import spec_by_id

    for shortcut_id in ("view.colorbar", "view.graticule"):
        spec = spec_by_id(shortcut_id)
        assert spec.owner == "action"
        installed = window.registered_shortcuts[shortcut_id]
        assert len(installed) == 1
        assert installed[0] is window.menu_bar.registry_actions[shortcut_id]


def test_readout_formatting(window):
    window.handle_cursor_position(12.3456, -98.7654, 273.15)
    with_value = window.cursor_readout.text()

    window.handle_cursor_position(12.3456, -98.7654, None)
    without_value = window.cursor_readout.text()

    assert "273.15" in with_value
    assert window.NO_VALUE in without_value
    # Fixed-width format: the label must not resize as digits change.
    assert len(with_value) == len(without_value)

    window.clear_cursor_readout()
    assert window.cursor_readout.text() == ""


def test_toggle_every_control_on_and_off(window, nc_standard, capsys):
    """The core smoke check: drive all four overlays with real data loaded."""
    window.geo_canvas.load_netcdf(nc_standard)
    assert window.geo_canvas.layers

    for checked in (True, False, True):
        window.toggle_colorbar(checked)
        assert window.geo_canvas.colorbar_manager.visible is checked
        assert window.menu_bar.colorbar_action.isChecked() is checked

        window.toggle_graticule(checked)
        assert window.geo_canvas.graticule_visible is checked

        window.toggle_scalebar(checked)
        assert window.geo_canvas.scalebar_manager.visible is checked

    for position in ("left", "bottom", "top", "right"):
        window.set_colorbar_position(position)
        assert window.geo_canvas.colorbar_manager.position == position
        assert window.menu_bar.colorbar_position_actions[position].isChecked()

    window.geo_canvas.draw()

    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out


def test_hovering_updates_the_readout(window, nc_standard):
    """The signal must actually reach the label."""
    from matplotlib.backend_bases import MouseEvent

    canvas = window.geo_canvas
    if not canvas.layers:
        canvas.load_netcdf(nc_standard)

    window.clear_cursor_readout()
    x, y = canvas.ax.transData.transform((10.0, 20.0))
    canvas._hover_last_emit = 0.0
    canvas._on_mouse_move(MouseEvent("motion_notify_event", canvas, x, y))

    assert "lat" in window.cursor_readout.text()


def test_no_stderr_traceback_during_toggling(window, capsys):
    window.toggle_colorbar(True)
    window.toggle_graticule(True)
    window.toggle_scalebar(True)
    window.geo_canvas.resize(900, 500)
    window.geo_canvas.set_extent([-30, 30, -15, 15])
    window.geo_canvas.draw()

    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert "Exception" not in captured.err
    assert sys.stderr is not None
