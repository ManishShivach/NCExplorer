"""Colorbar behaviour: targeting, labelling, placement and axes hygiene."""

import cartopy.crs as ccrs
import pytest

from ncexplorer_toolkit.geocanvas.colorbar import ColorbarManager


def load(canvas, path):
    """Load a NetCDF file and return the layer name the canvas gave it."""
    canvas.load_netcdf(path)
    assert canvas.layers, "loader produced no layer"
    return next(iter(canvas.layers))


def test_colorbar_appears_after_loading_netcdf(canvas, nc_standard):
    load(canvas, nc_standard)
    canvas.colorbar_manager.set_visible(True)

    assert canvas.colorbar_manager.visible
    assert canvas.colorbar_manager._colorbar is not None
    assert canvas.colorbar_manager._cax in canvas.fig.axes


def test_colorbar_hidden_until_asked(canvas, nc_standard):
    load(canvas, nc_standard)
    assert canvas.colorbar_manager._colorbar is None


def test_label_is_long_name_with_units(canvas, nc_standard):
    load(canvas, nc_standard)
    canvas.colorbar_manager.set_visible(True)

    assert canvas.colorbar_manager.label == "Air temperature (K)"


def test_label_falls_back_when_attrs_missing(canvas, nc_standard):
    """A file with no long_name/units must still label, not raise."""
    layer_name = load(canvas, nc_standard)
    record = canvas.layers[layer_name]
    record['dataset'][record['variable']].attrs.clear()

    canvas.colorbar_manager.set_visible(True)
    assert canvas.colorbar_manager.label == record['variable']


def test_colorbar_disappears_when_layer_removed(canvas, nc_standard):
    layer_name = load(canvas, nc_standard)
    canvas.colorbar_manager.set_visible(True)
    assert canvas.colorbar_manager._colorbar is not None

    canvas.remove_layer(layer_name)

    assert canvas.colorbar_manager._colorbar is None
    assert canvas.colorbar_manager._cax is None
    assert canvas.colorbar_manager.layer_name is None


def test_colorbar_hidden_when_layer_made_invisible(canvas, nc_standard):
    layer_name = load(canvas, nc_standard)
    canvas.colorbar_manager.set_visible(True)

    canvas.toggle_layer(layer_name, False)
    assert canvas.colorbar_manager._colorbar is None

    canvas.toggle_layer(layer_name, True)
    assert canvas.colorbar_manager._colorbar is not None


def test_repeated_refresh_does_not_leak_axes(canvas, nc_standard):
    """The trap: fig.add_axes appends, so a missed removal leaks per refresh."""
    load(canvas, nc_standard)
    canvas.colorbar_manager.set_visible(True)

    before = len(canvas.fig.axes)
    for _ in range(10):
        canvas.colorbar_manager.refresh()

    assert len(canvas.fig.axes) == before


def test_toggling_visibility_does_not_leak_axes(canvas, nc_standard):
    load(canvas, nc_standard)
    baseline = len(canvas.fig.axes)

    for _ in range(10):
        canvas.colorbar_manager.set_visible(True)
        canvas.colorbar_manager.set_visible(False)

    assert len(canvas.fig.axes) == baseline


@pytest.mark.parametrize("position", ColorbarManager.POSITIONS)
def test_every_position_applies(canvas, nc_standard, position):
    load(canvas, nc_standard)
    canvas.colorbar_manager.set_visible(True)

    canvas.colorbar_manager.set_position(position)

    assert canvas.colorbar_manager.position == position
    assert canvas.colorbar_manager._colorbar is not None
    canvas.draw()  # would raise if the geometry were invalid


def test_position_survives_refresh(canvas, nc_standard):
    load(canvas, nc_standard)
    canvas.colorbar_manager.set_visible(True)
    canvas.colorbar_manager.set_position('bottom')

    canvas.colorbar_manager.refresh()

    assert canvas.colorbar_manager.position == 'bottom'


def test_unknown_position_is_ignored(canvas, nc_standard):
    load(canvas, nc_standard)
    canvas.colorbar_manager.set_visible(True)

    canvas.colorbar_manager.set_position('sideways')

    assert canvas.colorbar_manager.position == 'right'


def test_colorbar_does_not_shrink_the_map_axes(canvas, nc_standard):
    """The map is deliberately full-bleed; the colorbar must not steal space."""
    load(canvas, nc_standard)
    before = canvas.ax.get_position().bounds

    canvas.colorbar_manager.set_visible(True)

    assert canvas.ax.get_position().bounds == pytest.approx(before)


def test_vertical_positions_clear_the_nav_overlay(canvas):
    """The on-canvas navigation cluster owns the bottom-right of the map.

    nav_overlay.reposition() pins a ~40x170 px cluster to the bottom-right
    corner, so a vertical colorbar has to start above it.
    """
    from ncexplorer_toolkit.geocanvas.colorbar import ColorbarManager

    # Generous allowance for the cluster's height as a fraction of a short canvas.
    nav_top = 0.26

    for position in ('right', 'left'):
        _bar, backdrop, _orientation, _side = ColorbarManager.GEOMETRY[position]
        assert backdrop[1] >= nav_top, f"{position} backdrop overlaps the nav cluster"


def test_horizontal_positions_clear_the_attribution(canvas):
    """The basemap attribution sits at y≈0.008 and can run far to the left."""
    from ncexplorer_toolkit.geocanvas.colorbar import ColorbarManager

    _bar, backdrop, _orientation, _side = ColorbarManager.GEOMETRY['bottom']
    assert backdrop[1] > 0.04


def test_no_colorbar_without_a_scalar_layer(canvas):
    canvas.colorbar_manager.set_visible(True)

    assert canvas.colorbar_manager._colorbar is None
    assert canvas.colorbar_manager.layer_name is None


def test_target_layer_pins_the_colorbar(canvas, nc_standard, nc_anomaly):
    first = load(canvas, nc_standard)
    canvas.load_netcdf(nc_anomaly)
    canvas.colorbar_manager.set_visible(True)

    # The most recently added layer is topmost, so it wins by default.
    assert canvas.colorbar_manager.layer_name != first

    canvas.colorbar_manager.set_target_layer(first)
    assert canvas.colorbar_manager.layer_name == first
    assert canvas.colorbar_manager.label == "Air temperature (K)"


def colorbar_axes(canvas):
    """Axes belonging to a colorbar, i.e. everything that is not a map axes.

    apply_theme() adds a fresh GeoAxes per call and leaves the previous one in
    fig.axes — pre-existing behaviour, so counting total axes would measure that
    rather than the colorbar.
    """
    from cartopy.mpl.geoaxes import GeoAxes

    return [ax for ax in canvas.fig.axes if not isinstance(ax, GeoAxes)]


def test_theme_change_keeps_the_data_layers_drawn(canvas, nc_standard):
    """A rebuild re-draws the layers rather than orphaning them.

    apply_theme() builds a new axes, so the old artist is genuinely gone — but
    the layer is drawn again on the new one and the record points at that. The
    colorbar, the layer list and the hover readout all key off "what is
    currently on the map", so canvas.layers has to keep agreeing with it.
    """
    layer_name = load(canvas, nc_standard)
    before = canvas.layers[layer_name]['artist']

    canvas.apply_theme('dark')

    after = canvas.layers[layer_name]['artist']
    assert after is not None and after is not before
    assert after in canvas.ax.get_children()
    assert canvas.scalar_layer_order() == [layer_name]


def test_theme_change_never_stacks_colorbars(canvas, nc_standard):
    """The colorbar follows the data: it comes back, and it must not accumulate."""
    load(canvas, nc_standard)
    canvas.colorbar_manager.set_visible(True)
    assert len(colorbar_axes(canvas)) == 1

    canvas.apply_theme('dark')
    canvas.apply_theme('light')

    assert len(colorbar_axes(canvas)) == 1
    assert canvas.colorbar_manager._colorbar is not None


def test_repeated_theme_changes_leak_nothing(canvas, nc_standard):
    load(canvas, nc_standard)
    canvas.colorbar_manager.set_visible(True)

    for _ in range(5):
        canvas.apply_theme('dark')
        canvas.apply_theme('light')

    assert len(colorbar_axes(canvas)) == 1
    # One map, one bar. add_subplot() has not reused an existing subplot since
    # matplotlib 3.6, so a rebuild that does not clear the figure first stacks
    # a second GeoAxes per call.
    assert len(canvas.fig.axes) == 2


def test_colorbar_returns_when_a_layer_is_reloaded_after_theming(canvas, nc_standard):
    """Once something is drawn again the bar comes back on the new axes."""
    canvas.colorbar_manager.set_visible(True)
    canvas.apply_theme('dark')

    load(canvas, nc_standard)

    assert canvas.colorbar_manager._colorbar is not None
    assert len(colorbar_axes(canvas)) == 1
    assert canvas.colorbar_manager._cax in canvas.fig.axes


def test_refresh_survives_a_stale_artist(canvas, nc_standard):
    """A mappable whose layer was already removed must not raise."""
    layer_name = load(canvas, nc_standard)
    canvas.colorbar_manager.set_visible(True)

    canvas.layers[layer_name]['artist'].remove()
    canvas.colorbar_manager.refresh()

    assert canvas.colorbar_manager._colorbar is None


def test_resize_keeps_a_single_colorbar(canvas, nc_standard):
    load(canvas, nc_standard)
    canvas.colorbar_manager.set_visible(True)
    before = len(canvas.fig.axes)

    canvas.resize(1000, 500)
    canvas.resize(640, 320)

    assert len(canvas.fig.axes) == before
    assert canvas.colorbar_manager._colorbar is not None


def test_symbology_change_refreshes_the_colorbar(canvas, nc_standard):
    layer_name = load(canvas, nc_standard)
    canvas.colorbar_manager.set_visible(True)

    layer_prop = canvas.property_manager.get_layer_property(layer_name)
    layer_prop.style.colormap = 'cmo.balance' if _has('cmo.balance') else 'RdBu'
    canvas.symbology_manager.symbology_changed.emit(layer_name)

    assert canvas.colorbar_manager._colorbar is not None
    # The map and the bar, and nothing else: a refresh that forgets to remove
    # the previous cax leaves one behind on every symbology change.
    assert len(canvas.fig.axes) == 2


def _has(name):
    import matplotlib
    return name in matplotlib.colormaps


def test_extent_change_leaves_colorbar_intact(canvas, nc_standard):
    load(canvas, nc_standard)
    canvas.colorbar_manager.set_visible(True)

    canvas.set_extent([-40, 40, -20, 20])

    assert canvas.colorbar_manager._colorbar is not None
    assert canvas.ax.get_extent(ccrs.PlateCarree()) is not None
