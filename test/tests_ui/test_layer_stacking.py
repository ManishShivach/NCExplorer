"""Layer stacking order.

The claim under test is that the map draws its layers in the order the layer
manager shows them — top of the list on top of the map — whatever kind of layer
they are and whatever has happened to the canvas since they loaded.

Opacity and the colour scale are symbology, and live on the layer's properties
panel; they are tested in test_symbology_tab.py.
"""

import pytest

from ncexplorer_toolkit.geocanvas.canvas import GeoCanvas


def zorder_of(canvas, name):
    return float(canvas.layers[name]['artist'].get_zorder())


@pytest.fixture
def two_layers(canvas, nc_standard, nc_lon360):
    """Two NetCDF layers, loaded oldest first."""
    canvas.load_netcdf(str(nc_standard), layer_name="bottom")
    canvas.load_netcdf(str(nc_lon360), layer_name="top")
    assert set(canvas.layers) == {"bottom", "top"}
    return canvas


# ----------------------------------------------------------------------
# The canvas
# ----------------------------------------------------------------------
def test_a_new_layer_stacks_on_top(two_layers):
    canvas = two_layers
    assert canvas.layer_stacking_order() == ["top", "bottom"]
    assert zorder_of(canvas, "top") > zorder_of(canvas, "bottom")


def test_the_stack_stays_under_the_reference_features(two_layers):
    """Coastlines and borders (zorder 1) draw over the data, as they always did."""
    canvas = two_layers
    for name in canvas.layers:
        assert GeoCanvas.LAYER_ZORDER_BASE < zorder_of(canvas, name)
        assert zorder_of(canvas, name) < GeoCanvas.LAYER_ZORDER_TOP


def test_set_layer_order_restacks_the_map(two_layers):
    canvas = two_layers
    canvas.set_layer_order(["bottom", "top"])

    assert canvas.layer_stacking_order() == ["bottom", "top"]
    assert zorder_of(canvas, "bottom") > zorder_of(canvas, "top")


def test_an_unknown_name_is_ignored_and_an_omitted_layer_is_kept(two_layers):
    """A stale list must not drop a layer out of the stack."""
    canvas = two_layers
    canvas.set_layer_order(["bottom", "gone"])

    assert canvas.layer_stacking_order() == ["bottom", "top"]
    assert zorder_of(canvas, "bottom") > zorder_of(canvas, "top")


def test_the_order_survives_a_projection_change(two_layers):
    """A projection change rebuilds every artist; the stack must outlive them."""
    canvas = two_layers
    canvas.set_layer_order(["bottom", "top"])
    canvas.set_projection("Robinson")

    assert canvas.layer_stacking_order() == ["bottom", "top"]
    assert zorder_of(canvas, "bottom") > zorder_of(canvas, "top")


def test_a_vector_layer_can_be_stacked_under_a_raster(two_layers):
    """The per-type z-orders used to pin every vector over every raster."""
    canvas = two_layers
    canvas.add_points([(10.0, 20.0), (-5.0, 30.0)], layer_name="pins")

    assert canvas.layer_stacking_order()[0] == "pins"
    assert zorder_of(canvas, "pins") > zorder_of(canvas, "top")

    canvas.set_layer_order(["top", "bottom", "pins"])
    assert zorder_of(canvas, "pins") < zorder_of(canvas, "bottom")


def test_removing_a_layer_leaves_the_rest_ordered(two_layers):
    canvas = two_layers
    canvas.set_layer_order(["bottom", "top"])
    canvas.remove_layer("bottom")

    assert canvas.layer_stacking_order() == ["top"]
    assert 0.0 < zorder_of(canvas, "top") < GeoCanvas.LAYER_ZORDER_TOP


# ----------------------------------------------------------------------
# The layer manager widget
# ----------------------------------------------------------------------
@pytest.fixture
def manager(qapp):
    """The dock widget on its own, with three layers and no canvas behind it."""
    from ncexplorer_toolkit.gui.layer_manager import LayerManager

    widget = LayerManager(None)
    for name in ("first", "second", "third"):
        widget.add_layer(name, "netcdf", f"/tmp/{name}.nc")
    yield widget
    widget.cleanup()
    widget.deleteLater()


def test_the_list_shows_the_newest_layer_at_the_top(manager):
    assert manager.layer_names_top_first() == ["third", "second", "first"]


def test_moving_a_layer_up_announces_the_new_order(manager):
    announced = []
    manager.layer_order_changed.connect(announced.append)

    manager.layer_list.setCurrentRow(2)  # "first", at the bottom
    manager.move_selected_up()

    assert announced == [["third", "first", "second"]]
    assert manager.layer_names_top_first() == ["third", "first", "second"]
    # The dict the list is rebuilt from has to agree, or the next refresh
    # would put the old order back.
    manager.update_layer_list()
    assert manager.layer_names_top_first() == ["third", "first", "second"]


def test_a_layer_cannot_be_moved_off_either_end(manager):
    announced = []
    manager.layer_order_changed.connect(announced.append)

    manager.layer_list.setCurrentRow(0)
    manager.move_selected_up()
    manager.layer_list.setCurrentRow(2)
    manager.move_selected_down()

    assert announced == []
    assert manager.layer_names_top_first() == ["third", "second", "first"]


def test_the_list_is_set_up_for_drag_reordering(manager):
    from PyQt6.QtWidgets import QAbstractItemView

    assert (manager.layer_list.dragDropMode()
            == QAbstractItemView.DragDropMode.InternalMove)
    # A drop must insert between layers rather than replace the one under it.
    assert manager.layer_list.dragDropOverwriteMode() is False


def test_a_drop_announces_the_new_order(manager):
    """The signal a real drag ends on has to reach the canvas.

    Qt's internal move is a nested drag loop that cannot be driven offscreen, so
    the rows are moved directly and the drop's own signal is emitted — which is
    the state the list is in by the time dropEvent returns.
    """
    announced = []
    manager.layer_order_changed.connect(announced.append)

    manager.layer_list.insertItem(0, manager.layer_list.takeItem(2))
    manager.layer_list.order_changed.emit()

    assert announced == [["first", "third", "second"]]


def test_bring_to_front_and_send_to_back(manager):
    manager.move_layer_to_row("first", 0)
    assert manager.layer_names_top_first() == ["first", "third", "second"]

    manager.move_layer_to_row("first", manager.layer_list.count() - 1)
    assert manager.layer_names_top_first() == ["third", "second", "first"]


def test_the_layer_manager_has_no_opacity_control(manager):
    """Opacity is symbology; it belongs to the layer's properties panel."""
    assert not hasattr(manager, "opacity_slider")
    assert not hasattr(manager, "opacity_spin")
    assert not hasattr(manager, "layer_opacity_changed")


def test_the_symbology_button_asks_for_the_selected_layer(manager):
    requested = []
    manager.layer_properties_requested.connect(requested.append)

    manager.layer_list.setCurrentRow(1)  # "second"
    manager.show_selected_symbology()

    assert requested == ["second"]


def test_the_selection_controls_are_disabled_with_no_selection(manager):
    manager.layer_list.setCurrentRow(-1)
    manager.refresh_selection_controls()

    assert not manager.symbology_btn.isEnabled()
    assert not manager.move_up_btn.isEnabled()
    assert not manager.move_down_btn.isEnabled()


# ----------------------------------------------------------------------
# The two halves, wired together
# ----------------------------------------------------------------------
@pytest.fixture(scope="module")
def window(qapp):
    from ncexplorer_toolkit.gui.main_window import NCExplorerOperatorGUI

    gui = NCExplorerOperatorGUI()
    yield gui
    gui.close()
    gui.deleteLater()


def test_reordering_the_list_restacks_the_real_map(window, nc_standard, nc_lon360):
    canvas = window.geo_canvas
    canvas.clear_layers()
    canvas.load_netcdf(str(nc_standard), layer_name="lower")
    canvas.load_netcdf(str(nc_lon360), layer_name="upper")

    manager = window.layer_manager
    assert manager.layer_names_top_first() == ["upper", "lower"]

    manager.layer_list.setCurrentRow(1)  # "lower"
    manager.move_selected_up()

    assert canvas.layer_stacking_order() == ["lower", "upper"]
    assert zorder_of(canvas, "lower") > zorder_of(canvas, "upper")


def test_a_project_restores_the_order_and_the_symbology(window, nc_standard, nc_lon360,
                                                        tmp_path):
    """Both are stored per layer, so reopening has to bring them back."""
    import os

    from ncexplorer_toolkit.core.project import load_project, save_project

    canvas = window.geo_canvas
    canvas.clear_layers()
    canvas.load_netcdf(str(nc_standard))
    canvas.load_netcdf(str(nc_lon360))

    # Deliberately not the order they loaded in: an order that happens to match
    # the load order would pass whether it was restored or not.
    older = os.path.splitext(os.path.basename(str(nc_standard)))[0]
    newer = os.path.splitext(os.path.basename(str(nc_lon360)))[0]
    canvas.set_layer_order([older, newer])
    canvas.property_manager.update_property(newer, "style.transparency", 0.7)
    canvas.property_manager.update_property(newer, "style.colormap", "plasma")

    path = str(tmp_path / "stacked.ncx")
    save_project(path, window._capture_project_state())

    canvas.clear_layers()
    window._apply_project_state(load_project(path), path)

    assert canvas.layer_stacking_order() == [older, newer]
    assert zorder_of(canvas, older) > zorder_of(canvas, newer)
    assert window.layer_manager.layer_names_top_first() == [older, newer]

    restored = canvas.property_manager.get_layer_property(newer)
    assert restored.style.transparency == pytest.approx(0.7)
    assert restored.style.colormap == "plasma"
    assert canvas.layers[newer]['artist'].get_alpha() == pytest.approx(0.3)
