"""A loaded NetCDF layer knows every variable its file holds.

``NetCDFProperties.variables`` is what the variable picker on the layer's
NetCDF tab is built from, and it only offers a choice when the list has more
than one entry. A loader that leaves it empty therefore does not fail visibly —
it silently removes the ability to see the second variable of a two-variable
result, which is what most of the CDO climate indices write.

There were three loaders writing that list, and one of them wrote it under a
different name (``available_variables``). These tests pin the live path, and
pin the dead one as dead so it cannot come back without the mismatch being
noticed again.
"""

import numpy as np
import pytest
import xarray as xr


@pytest.fixture
def two_variable_nc(tmp_path):
    """The shape a CDO climate index writes: one index plus a period count.

    Named after ``eca_cdd``'s real output, because that is the case the picker
    exists for.
    """
    lats = np.linspace(-87.5, 87.5, 18)
    lons = np.linspace(-177.5, 177.5, 36)
    shape = (2, len(lats), len(lons))
    values = np.arange(np.prod(shape), dtype=float).reshape(shape)

    dataset = xr.Dataset(
        {
            "consecutive_dry_days_index_per_time_period": (
                ("time", "lat", "lon"), values, {"units": "days"}),
            "number_of_cdd_periods_with_more_than_5days_per_time_period": (
                ("time", "lat", "lon"), values / 2, {"units": "1"}),
        },
        coords={"time": np.arange(shape[0]), "lat": lats, "lon": lons},
    )
    path = tmp_path / "eca_cdd.nc"
    dataset.to_netcdf(path)
    dataset.close()
    return str(path)


# --- the live loader ------------------------------------------------------

def test_geocanvas_load_netcdf_records_every_variable(canvas, two_variable_nc):
    """``GeoCanvas.load_netcdf`` is the app's only live NetCDF loader.

    ``GeoCanvas.load_file`` dispatches ``.nc`` here, and ``load_raster``
    forwards to it as well; nothing else constructs a NetCDF layer.
    """
    canvas.load_netcdf(two_variable_nc, "eca_cdd")

    props = canvas.property_manager.get_layer_property("eca_cdd")
    assert props is not None and props.netcdf is not None
    assert props.netcdf.variables == [
        "consecutive_dry_days_index_per_time_period",
        "number_of_cdd_periods_with_more_than_5days_per_time_period",
    ]
    # The one actually drawn, and the one every reader falls back to.
    assert props.netcdf.current_variable == (
        "consecutive_dry_days_index_per_time_period")


def test_load_file_reaches_the_same_place(canvas, two_variable_nc):
    """The extension dispatch a user's File→Open goes through."""
    assert canvas.load_file(two_variable_nc)

    layer_name = "eca_cdd"
    props = canvas.property_manager.get_layer_property(layer_name)
    assert len(props.netcdf.variables) == 2


def test_a_single_variable_file_still_records_its_one(canvas, nc_standard):
    """The list is populated, not merely non-empty when there is a choice."""
    canvas.load_netcdf(nc_standard, "standard")
    props = canvas.property_manager.get_layer_property("standard")
    assert props.netcdf.variables == ["t2m"]


def test_the_variables_list_is_what_the_picker_reads(canvas, two_variable_nc,
                                                     qapp):
    """End to end: a two-variable result offers both in the properties tab.

    ``_show_variables`` shows the combo only when the list has more than one
    entry, so an unpopulated list and a single-variable file are indistinguishable
    from the widget's side — which is exactly how the mismatch stayed invisible.
    """
    from ncexplorer_toolkit.geocanvas.properties import NetCDFTabWidget

    canvas.load_netcdf(two_variable_nc, "eca_cdd")
    props = canvas.property_manager.get_layer_property("eca_cdd")

    tab = NetCDFTabWidget(props)
    tab.load_properties()

    assert tab.variable_combo.isVisibleTo(tab)
    assert [tab.variable_combo.itemText(i)
            for i in range(tab.variable_combo.count())] == props.netcdf.variables
    assert tab.variable_combo.currentText() == props.netcdf.current_variable


def test_picking_the_second_variable_reaches_the_map(canvas, two_variable_nc):
    """The choice has to move the data, not only the label.

    ``update_netcdf_layer`` renders from the property manager's variable, and
    the statistics panel reads the layer record's copy, so both are checked.
    """
    canvas.load_netcdf(two_variable_nc, "eca_cdd")
    first, second = canvas.property_manager.get_layer_property(
        "eca_cdd").netcdf.variables

    before = np.array(canvas.layers["eca_cdd"]["artist"].get_array(), dtype=float)
    canvas.set_netcdf_variable("eca_cdd", second)

    props = canvas.property_manager.get_layer_property("eca_cdd")
    assert props.netcdf.current_variable == second
    assert canvas.layers["eca_cdd"]["variable"] == second

    after = np.array(canvas.layers["eca_cdd"]["artist"].get_array(), dtype=float)
    assert not np.allclose(before, after), (
        f"switching from {first} to {second} left the drawn data unchanged")


# --- the picker's own wiring ----------------------------------------------
#
# The two tests above check the ends of the chain: the combo is built from the
# variables list, and set_netcdf_variable moves the data. Neither covers the
# span between them, which is where the picker was broken — the combo emitted
# a property path that the window wrote straight to the property manager, so
# the record and the map never heard about it.

def test_the_combo_emits_the_property_path_the_window_routes(canvas,
                                                             two_variable_nc):
    """The literal string both halves agree on, pinned from the widget's side."""
    from ncexplorer_toolkit.geocanvas.properties import NetCDFTabWidget

    canvas.load_netcdf(two_variable_nc, "eca_cdd")
    props = canvas.property_manager.get_layer_property("eca_cdd")
    _first, second = props.netcdf.variables

    tab = NetCDFTabWidget(props)
    tab.load_properties()

    emitted = []
    tab.property_changed.connect(lambda path, value: emitted.append((path, value)))
    tab.variable_combo.setCurrentText(second)

    assert emitted == [("netcdf.current_variable", second)]


def test_picking_in_the_properties_tab_reaches_the_map(canvas, two_variable_nc):
    """The window's slot has to route that path through set_netcdf_variable.

    Called unbound against a stand-in rather than a whole main window: the slot
    reads only the two attributes below, and building the real window pulls up
    the entire app for one branch.
    """
    from ncexplorer_toolkit.gui.main_window import NCExplorerOperatorGUI

    canvas.load_netcdf(two_variable_nc, "eca_cdd")
    first, second = canvas.property_manager.get_layer_property(
        "eca_cdd").netcdf.variables

    class Window:
        current_layer = "eca_cdd"
        geo_canvas = canvas

    announced = []
    canvas.variable_changed.connect(
        lambda name, var: announced.append((name, var)))

    before = np.array(canvas.layers["eca_cdd"]["artist"].get_array(), dtype=float)
    NCExplorerOperatorGUI.on_property_changed(
        Window(), "netcdf.current_variable", second)

    props = canvas.property_manager.get_layer_property("eca_cdd")
    assert props.netcdf.current_variable == second
    assert canvas.layers["eca_cdd"]["variable"] == second, (
        "the layer record still names the old variable, so the statistics "
        "panel and the plot dock keep summarising it")

    after = np.array(canvas.layers["eca_cdd"]["artist"].get_array(), dtype=float)
    assert not np.allclose(before, after), (
        f"picking {second} over {first} left the drawn data unchanged")
    assert announced == [("eca_cdd", second)]


def test_other_properties_still_go_straight_to_the_manager(canvas, nc_standard):
    """The netcdf branch is a special case, not a replacement for the slot."""
    from ncexplorer_toolkit.gui.main_window import NCExplorerOperatorGUI

    canvas.load_netcdf(nc_standard, "standard")

    class Window:
        current_layer = "standard"
        geo_canvas = canvas

    NCExplorerOperatorGUI.on_property_changed(Window(), "style.colormap", "magma")

    props = canvas.property_manager.get_layer_property("standard")
    assert props.style.colormap == "magma"


# --- the loader that was not live -----------------------------------------

def test_the_dead_netcdf_manager_is_gone():
    """``geocanvas/netcdf.py`` held the module that wrote ``available_variables``.

    It could not run: every method that touched a layer dereferenced
    ``canvas.layer_manager``, which ``GeoCanvas`` has no such attribute, and
    ``visualize_netcdf_layer`` used a ``ccrs`` the module never imported. It was
    removed rather than corrected — renaming an attribute in a class that raises
    on its first statement fixes nothing.
    """
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("ncexplorer_toolkit.geocanvas.netcdf")


@pytest.mark.parametrize("package", [
    "ncexplorer_toolkit",
    "ncexplorer_toolkit.geocanvas",
])
def test_netcdf_manager_is_not_exported(package):
    """Both lazy-import maps listed it; a stale entry raises on first access."""
    import importlib

    module = importlib.import_module(package)
    assert "NetCDFManager" not in module.__all__
    with pytest.raises(AttributeError):
        getattr(module, "NetCDFManager")


def test_no_loader_writes_the_old_attribute_name(canvas, two_variable_nc):
    """``available_variables`` is nobody's property and nobody's reader.

    ``NetCDFProperties`` declares ``variables``; a loader setting the other name
    would satisfy no reader while looking, in a debugger, like it had worked.
    """
    canvas.load_netcdf(two_variable_nc, "eca_cdd")
    props = canvas.property_manager.get_layer_property("eca_cdd")
    assert not hasattr(props.netcdf, "available_variables")
