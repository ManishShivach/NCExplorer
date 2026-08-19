# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""The Symbology tab of a layer's properties.

This is where a layer's appearance is edited: its opacity and, for a raster or
NetCDF layer, its colour scale. The tab existed in the source but was never
added to the properties editor, so none of it had ever reached a user; these
tests hold it to the two things that make it worth having — every control
reaches the map, and it acts on the layer being shown and no other.
"""

import pytest

from ncexplorer_toolkit.geocanvas.properties import LayerPropertyEditor


def artist_of(canvas, name):
    return canvas.layers[name]['artist']


@pytest.fixture
def editor(canvas, nc_standard):
    """A properties editor over one loaded NetCDF layer, wired as the window wires it."""
    canvas.load_netcdf(str(nc_standard), layer_name="field")
    widget = LayerPropertyEditor(canvas.property_manager)
    # What main_window.on_property_changed does, minus the parts that need a
    # whole window: write the edit to the layer the editor is showing.
    widget.property_changed.connect(
        lambda path, value: canvas.property_manager.update_property(
            widget.current_layer_name, path, value
        )
    )
    assert widget.load_layer_properties("field")
    yield widget
    widget.deleteLater()


# ----------------------------------------------------------------------
# The tab exists and shows the truth
# ----------------------------------------------------------------------
def test_the_editor_offers_a_symbology_tab(editor):
    titles = [editor.tabs.tabText(i) for i in range(editor.tabs.count())]
    assert titles[0] == "Symbology"


def test_the_opacity_shown_is_the_layer_s_own(editor):
    """A NetCDF layer loads at alpha 0.8, so the control must say 80%."""
    assert editor.symbology_tab.opacity_slider.value() == 80
    assert editor.symbology_tab.opacity_spin.value() == 80


def test_only_the_symbology_of_this_kind_of_layer_is_shown(editor, canvas):
    tab = editor.symbology_tab
    assert tab.raster_group.isVisibleTo(tab)
    assert not tab.vector_group.isVisibleTo(tab)


def test_a_vector_layer_shows_vector_symbology(canvas):
    canvas.add_points([(10.0, 20.0)], layer_name="pins")
    prop = canvas.property_manager.add_layer("pins")
    prop.metadata.layer_type = "vector"

    widget = LayerPropertyEditor(canvas.property_manager)
    assert widget.load_layer_properties("pins")
    tab = widget.symbology_tab

    assert tab.vector_group.isVisibleTo(tab)
    assert not tab.raster_group.isVisibleTo(tab)
    widget.deleteLater()


# ----------------------------------------------------------------------
# Opacity
# ----------------------------------------------------------------------
def test_the_opacity_slider_dims_the_layer(editor, canvas):
    editor.symbology_tab.opacity_slider.setValue(35)

    assert artist_of(canvas, "field").get_alpha() == pytest.approx(0.35)
    # Stored as its complement, which is what a project saves.
    prop = canvas.property_manager.get_layer_property("field")
    assert prop.style.transparency == pytest.approx(0.65)


def test_the_opacity_can_be_typed_as_a_percentage(editor, canvas):
    editor.symbology_tab.opacity_spin.setValue(10)

    assert editor.symbology_tab.opacity_slider.value() == 10
    assert artist_of(canvas, "field").get_alpha() == pytest.approx(0.1)


def test_opacity_survives_a_redraw(editor, canvas):
    """A projection change rebuilds the artist from the stored style."""
    editor.symbology_tab.opacity_slider.setValue(40)
    canvas.set_projection("Mollweide")

    assert artist_of(canvas, "field").get_alpha() == pytest.approx(0.4)


# ----------------------------------------------------------------------
# Colormap
# ----------------------------------------------------------------------
def test_the_colormap_combo_changes_the_layer_s_colour_scale(editor, canvas):
    editor.symbology_tab.colormap_combo.setCurrentText("plasma")

    assert artist_of(canvas, "field").get_cmap().name == "plasma"
    assert canvas.property_manager.get_layer_property("field").style.colormap == "plasma"


def test_the_colormap_can_be_reversed(editor, canvas):
    editor.symbology_tab.colormap_combo.setCurrentText("viridis")
    editor.symbology_tab.reverse_colormap_check.setChecked(True)

    assert artist_of(canvas, "field").get_cmap().name == "viridis_r"


def test_the_combo_offers_the_registry_s_colormaps(editor):
    from ncexplorer_toolkit.geocanvas import colormaps as registry

    combo = editor.symbology_tab.colormap_combo
    offered = {combo.itemText(i) for i in range(combo.count())}
    for group, names in registry.available_colormaps().items():
        assert group in offered
        assert set(names) <= offered


def test_each_layer_keeps_its_own_colormap(canvas, nc_standard, nc_lon360):
    """The point of a per-layer colour scale: two rasters, told apart."""
    canvas.load_netcdf(str(nc_standard), layer_name="one")
    canvas.load_netcdf(str(nc_lon360), layer_name="two")

    widget = LayerPropertyEditor(canvas.property_manager)
    widget.property_changed.connect(
        lambda path, value: canvas.property_manager.update_property(
            widget.current_layer_name, path, value
        )
    )

    widget.load_layer_properties("one")
    widget.symbology_tab.colormap_combo.setCurrentText("magma")
    widget.load_layer_properties("two")
    widget.symbology_tab.colormap_combo.setCurrentText("cividis")

    assert artist_of(canvas, "one").get_cmap().name == "magma"
    assert artist_of(canvas, "two").get_cmap().name == "cividis"
    # Reopening the first layer shows the colormap it was given, not the last
    # one chosen.
    widget.load_layer_properties("one")
    assert widget.symbology_tab.colormap_combo.currentText() == "magma"
    widget.deleteLater()


def test_the_value_range_reads_auto_until_it_is_set(editor, canvas):
    """Both ends say "Auto", and both mean it — Qt only draws that at a minimum."""
    tab = editor.symbology_tab
    assert tab.vmin_spin.text() == "Auto"
    assert tab.vmax_spin.text() == "Auto"

    tab.vmax_spin.setValue(300.0)
    style = canvas.property_manager.get_layer_property("field").style
    assert style.vmax == pytest.approx(300.0)
    assert style.vmin is None
    assert artist_of(canvas, "field").get_clim()[1] == pytest.approx(300.0)

    tab.vmax_spin.setValue(tab.vmax_spin.minimum())
    assert canvas.property_manager.get_layer_property("field").style.vmax is None


def test_the_interpolation_choice_reaches_the_image(editor, canvas):
    """It was a stored value nothing applied until the tab was wired in."""
    editor.symbology_tab.interpolation_combo.setCurrentText("bilinear")

    assert artist_of(canvas, "field").get_interpolation() == "bilinear"


# ----------------------------------------------------------------------
# Editing the layer that is shown, and only it
# ----------------------------------------------------------------------
def test_a_refresh_keeps_the_tabs_alive(editor):
    """Rebuilding them would delete the slider being dragged, mid-drag."""
    tab = editor.symbology_tab
    index = editor.tabs.currentIndex()

    editor.refresh_current_layer()

    assert editor.symbology_tab is tab
    assert editor.tabs.currentIndex() == index


def test_an_edit_follows_the_shown_layer_not_the_last_one_loaded(window, nc_standard,
                                                                nc_lon360):
    """The regression: loading a file made it current while the panel showed another."""
    canvas = window.geo_canvas
    canvas.clear_layers()
    canvas.load_netcdf(str(nc_standard), layer_name="shown")

    window.handle_layer_properties("shown")
    # Loading another layer makes it the window's current layer, but the panel
    # is still showing the first one.
    canvas.load_netcdf(str(nc_lon360), layer_name="other")
    assert window.current_layer == "other"

    window.property_editor.symbology_tab.colormap_combo.setCurrentText("plasma")

    assert canvas.property_manager.get_layer_property("shown").style.colormap == "plasma"
    assert canvas.property_manager.get_layer_property("other").style.colormap != "plasma"


def test_the_colorbar_follows_the_chosen_colormap(window, nc_standard):
    canvas = window.geo_canvas
    canvas.clear_layers()
    canvas.load_netcdf(str(nc_standard), layer_name="scaled")
    window.toggle_colorbar(True)

    window.handle_layer_properties("scaled")
    window.property_editor.symbology_tab.colormap_combo.setCurrentText("magma")

    colorbar = canvas.colorbar_manager._colorbar
    assert colorbar is not None
    assert colorbar.mappable.get_cmap().name == "magma"

    # Taken down here rather than left to the module teardown: a colorbar still
    # attached to its mappable when the figure is collected raises inside
    # matplotlib's weakref cleanup, which pytest then reports against whichever
    # unrelated test happens to be running.
    window.toggle_colorbar(False)


@pytest.fixture(scope="module")
def window(qapp):
    from ncexplorer_toolkit.gui.main_window import NCExplorerOperatorGUI

    gui = NCExplorerOperatorGUI()
    yield gui
    gui.close()
    gui.deleteLater()
