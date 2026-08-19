# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""Selectable map projections: the registry, the axes rebuild, and the audit.

Two things are being pinned here. The first is that every entry in the registry
really builds — a projection offered in the selector but unbuildable is a dead
control, and the parameters are derived rather than typed, so nobody would find
out by hand. The second is the consequence the rest of the canvas has to live
with: outside PlateCarree the axes are in metres, not degrees, and the lon/lat
extent is the only thing that survives a switch.
"""

import math

import cartopy.crs as ccrs
import numpy as np
import pytest
from cartopy.mpl.gridliner import Gridliner

from ncexplorer_toolkit.geocanvas import projections

#: 60° by 30°, i.e. already at the conftest canvas' 2:1 aspect, so
#: _constrain_extent leaves it alone and a round trip is exactly a round trip.
REGIONAL = [-130.0, -70.0, 25.0, 55.0]
GLOBAL = [-180.0, 180.0, -90.0, 90.0]

#: The eight the toolkit ships. Named here rather than read off the registry so
#: that dropping one is a test failure and not a silently smaller menu.
EXPECTED = (
    "PlateCarree", "Mercator", "Robinson", "Mollweide",
    "LambertConformal", "AlbersEqualArea", "NorthPolarStereo", "SouthPolarStereo",
)


def gridliner_count(canvas):
    """How many Gridliners are attached to the live axes."""
    return sum(1 for child in canvas.ax.get_children() if isinstance(child, Gridliner))


def load(canvas, path):
    """Load a NetCDF file and return the layer name the canvas gave it."""
    canvas.load_netcdf(path)
    assert canvas.layers, "loader produced no layer"
    return next(iter(canvas.layers))


def _write_region(path, lats, lons):
    """A small regional NetCDF, for the zoom-to-layer path.

    conftest's fixtures are all whole-world, and zooming to a layer that already
    covers the globe cannot narrow anything.
    """
    import numpy as np
    import xarray as xr

    values = (np.cos(np.radians(lats))[:, None] * 20
              + np.sin(np.radians(lons))[None, :] * 5)
    dataset = xr.Dataset(
        {"t2m": (("time", "lat", "lon"), values[None], {"units": "K"})},
        coords={"time": [0], "lat": lats, "lon": lons},
    )
    dataset.to_netcdf(path)
    dataset.close()
    return str(path)


@pytest.fixture(scope="session")
def nc_north(tmp_path_factory):
    """A northern-hemisphere field, roughly North America."""
    path = tmp_path_factory.mktemp("nc_north") / "north.nc"
    return _write_region(path, np.linspace(12, 68, 40), np.linspace(-138, -42, 60))


@pytest.fixture(scope="session")
def nc_south(tmp_path_factory):
    """The same field in the southern hemisphere, for the south-polar cap."""
    path = tmp_path_factory.mktemp("nc_south") / "south.nc"
    return _write_region(path, np.linspace(-68, -12, 40), np.linspace(110, 170, 60))


# ----------------------------------------------------------------------
# The registry
# ----------------------------------------------------------------------
def test_the_eight_are_offered_in_order():
    assert projections.PROJECTION_CHOICES == EXPECTED
    assert projections.PROJECTION_CHOICES[0] == projections.DEFAULT_PROJECTION


@pytest.mark.parametrize("name", EXPECTED)
@pytest.mark.parametrize("extent", [GLOBAL, REGIONAL, [110, 160, -45, -10], [-30, 60, 55, 88]])
def test_every_entry_builds_a_real_crs(name, extent):
    """Whatever the data covers, the derived parameters have to construct."""
    crs, used = projections.build(name, extent)

    assert used == name
    assert isinstance(crs, ccrs.CRS)


@pytest.mark.parametrize("name", EXPECTED)
def test_derived_parameters_are_the_ones_used(name):
    """The CRS is built from derive()'s output, so the two must agree."""
    params = projections.derive(name, REGIONAL)
    spec = projections.spec(name)

    assert spec.factory(params) is not None
    for value in params.values():
        assert math.isfinite(value)


def test_central_longitude_is_the_extent_midpoint():
    """Basemap's lon_0, and every entry that takes one derives it the same way."""
    for name in EXPECTED:
        params = projections.derive(name, REGIONAL)
        if "lon_0" in params:
            assert params["lon_0"] == pytest.approx(-100.0)


def test_conic_standard_parallels_come_from_the_latitude_range():
    """Basemap's lat_1/lat_2, inset inside the band being mapped."""
    for name in ("LambertConformal", "AlbersEqualArea"):
        params = projections.derive(name, REGIONAL)
        assert REGIONAL[2] < params["lat_1"] < params["lat_2"] < REGIONAL[3]
        assert params["lat_0"] == pytest.approx(40.0)


def test_conic_parallels_never_straddle_the_equator_symmetrically():
    """A symmetric pair is a cone with its apex at infinity — unbuildable.

    It is also exactly what a whole-world extent would otherwise produce, so
    this is the default startup case rather than a curiosity.
    """
    params = projections.derive("LambertConformal", GLOBAL)
    assert abs(params["lat_1"] + params["lat_2"]) >= projections.CONIC_MIN_ASYMMETRY


def test_southern_conic_is_cut_off_on_the_southern_side():
    """Cartopy's default cutoff assumes northern parallels and crops the rest."""
    northern = projections.derive("LambertConformal", REGIONAL)
    southern = projections.derive("LambertConformal", [110, 160, -45, -10])

    assert projections._conic_cutoff(northern["lat_1"], northern["lat_2"]) < 0
    assert projections._conic_cutoff(southern["lat_1"], southern["lat_2"]) > 0


def test_polar_boundinglat_follows_the_data():
    """Basemap's boundinglat: the cap is cut where the data stops."""
    assert projections.derive("NorthPolarStereo", REGIONAL)["boundinglat"] == pytest.approx(25.0)
    assert projections.derive("SouthPolarStereo", [110, 160, -45, -10])["boundinglat"] == pytest.approx(-10.0)
    # Data reaching past the equator gives the hemisphere and no more.
    assert projections.derive("NorthPolarStereo", GLOBAL)["boundinglat"] == pytest.approx(0.0)


def test_mercator_true_scale_is_the_middle_of_the_band():
    assert projections.derive("Mercator", REGIONAL)["lat_ts"] == pytest.approx(40.0)


def test_global_projections_get_no_box_only_for_the_whole_world():
    """The globe has no box worth setting; a zoomed view into it does."""
    for name in ("Robinson", "Mollweide"):
        assert projections.axes_extent(name, GLOBAL) is None
        assert projections.axes_extent(name, REGIONAL) is not None


def test_polar_projections_get_the_whole_cap():
    box = projections.axes_extent("NorthPolarStereo", REGIONAL)
    assert box[:2] == (-180.0, 180.0)
    assert box[3] == 90.0
    assert box[2] > 25.0  # just inside the cut, never exactly on it


def test_mercator_box_stops_short_of_the_poles():
    """Mercator reaches infinity before ±90; the extent has to stop first."""
    west, east, south, north = projections.axes_extent("Mercator", GLOBAL)
    assert (west, east) == (-180.0, 180.0)
    assert projections.MERCATOR_LIMITS[0] < south < north < projections.MERCATOR_LIMITS[1]


def test_plate_carree_box_is_the_extent_untouched():
    """The default has to stay a no-op: everything else already assumes it."""
    assert projections.axes_extent("PlateCarree", GLOBAL) == tuple(GLOBAL)
    assert projections.axes_extent("PlateCarree", REGIONAL) == tuple(REGIONAL)


@pytest.mark.parametrize("junk", ["Nonsense", "", None, "platecarree"])
def test_an_unknown_name_falls_back_without_raising(junk):
    crs, used = projections.build(junk, REGIONAL)

    assert used == projections.DEFAULT_PROJECTION
    assert isinstance(crs, ccrs.PlateCarree)


@pytest.mark.parametrize("junk", ["not an extent", [], [1, 2], [float("nan")] * 4,
                                  [10, 5, 10, 5], None])
def test_a_nonsensical_extent_derives_the_whole_world(junk):
    """Deriving a projection is not the place to raise: the map is mid-rebuild."""
    assert projections.clean_extent(junk) == projections.WORLD
    for name in EXPECTED:
        crs, used = projections.build(name, junk)
        assert used == name and isinstance(crs, ccrs.CRS)


# ----------------------------------------------------------------------
# Switching on the canvas
# ----------------------------------------------------------------------
@pytest.mark.parametrize("name", EXPECTED)
def test_every_projection_can_be_selected_and_drawn(canvas, name):
    assert canvas.set_projection(name) == name
    assert canvas.projection_name == name
    canvas.draw()


def test_an_unknown_projection_name_falls_back_on_the_canvas(canvas):
    """A project file from a later build must not take the window down."""
    assert canvas.set_projection("Bonne") == projections.DEFAULT_PROJECTION
    assert canvas.projection_name == projections.DEFAULT_PROJECTION
    assert isinstance(canvas.ax.projection, ccrs.PlateCarree)
    canvas.draw()


@pytest.mark.parametrize("name", EXPECTED[1:])
def test_switching_out_and_back_preserves_the_view(canvas, name):
    """The lon/lat extent is canonical, so a round trip has to be lossless."""
    canvas.set_extent(list(REGIONAL))
    before = list(canvas.extent)

    canvas.set_projection(name)
    canvas.set_projection("PlateCarree")

    assert canvas.extent == pytest.approx(before, abs=1e-6)
    assert list(canvas.ax.get_extent(crs=ccrs.PlateCarree())) == pytest.approx(before, abs=0.5)


def test_a_rectangular_projection_shows_the_extent_it_was_given(canvas):
    canvas.set_extent(list(REGIONAL))
    canvas.set_projection("Mercator")

    shown = canvas.ax.get_extent(crs=ccrs.PlateCarree())
    assert list(shown) == pytest.approx(REGIONAL, abs=1.0)


@pytest.mark.parametrize("name", ("Robinson", "Mollweide"))
def test_a_global_projection_draws_the_globe_at_full_extent(canvas, name):
    """Asked for the whole world, it has no box worth setting — it is the globe."""
    canvas.set_projection(name)
    canvas.zoom_full_extent()

    west, east, south, north = canvas.ax.get_extent(crs=ccrs.PlateCarree())
    assert west < -170 and east > 170
    assert south < -85 and north > 85
    assert projections.axes_extent(name, canvas.extent) is None


@pytest.mark.parametrize("name", ("Robinson", "Mollweide"))
def test_a_global_projection_takes_a_box_once_zoomed(canvas, name):
    """Asked for less than the world it takes the box, so it zooms like the rest."""
    canvas.set_extent(list(REGIONAL))
    canvas.set_projection(name)

    assert projections.axes_extent(name, canvas.extent) is not None
    _west, _east, south, north = canvas.ax.get_extent(crs=ccrs.PlateCarree())
    assert south == pytest.approx(REGIONAL[2], abs=1.0)
    assert north == pytest.approx(REGIONAL[3], abs=1.0)


def test_the_axes_stop_being_degrees(canvas):
    """The premise of the whole audit, stated as a test."""
    canvas.set_extent(list(REGIONAL))
    canvas.set_projection("Mercator")

    x0, x1 = canvas.ax.get_xlim()
    assert abs(x1 - x0) > 1e6  # metres, not the 70° of the extent


def test_hover_readout_converts_out_of_axes_coordinates(canvas):
    canvas.set_extent(list(REGIONAL))
    canvas.set_projection("Mercator")

    centre_x = sum(canvas.ax.get_xlim()) / 2
    centre_y = sum(canvas.ax.get_ylim()) / 2
    lon, lat = canvas._axes_to_lonlat(centre_x, centre_y)

    assert lon == pytest.approx(-100.0, abs=1.0)
    assert 25.0 < lat < 55.0


def test_off_globe_positions_report_nothing_rather_than_raising(canvas):
    """The space in the corners beside a Mollweide ellipse is not anywhere."""
    canvas.set_projection("Mollweide")

    x_max = canvas.ax.projection.x_limits[1]
    y_max = canvas.ax.projection.y_limits[1]
    assert canvas._axes_to_lonlat(x_max * 0.99, y_max * 0.99) == (None, None)
    assert canvas._axes_to_lonlat(None, None) == (None, None)

    # …while a point inside it still reads back as a position.
    lon, lat = canvas._axes_to_lonlat(0.0, 0.0)
    assert lon is not None and lat == pytest.approx(0.0, abs=1e-6)


# ----------------------------------------------------------------------
# Zoom, in every projection
#
# Measured as the area of the drawn map in *projected* coordinates — literally
# what is on screen. The lon/lat box is the wrong yardstick here: a conic's
# cone wraps, so a narrow wedge and a wide one report almost the same box, and
# a polar cap is always a full 360° of longitude however far in you are.
# ----------------------------------------------------------------------
def drawn_area(canvas):
    """Area of the visible map in projected units."""
    x0, x1 = canvas.ax.get_xlim()
    y0, y1 = canvas.ax.get_ylim()
    return abs((x1 - x0) * (y1 - y0))


@pytest.mark.parametrize("name", EXPECTED)
def test_zoom_in_narrows_the_view(canvas, name):
    canvas.set_projection(name)
    canvas.zoom_full_extent()
    full = drawn_area(canvas)

    previous = full
    for step in range(4):
        canvas.zoom_in()
        now = drawn_area(canvas)
        assert now != pytest.approx(previous, rel=1e-3), f"zoom in #{step + 1} did nothing"
        previous = now

    # Four clicks of 0.8 is a real change by any measure, whatever the outline
    # does in between — an ellipse is widest across its middle, so a step that
    # trades latitude for longitude can grow the bounding box while showing less.
    assert previous < full * 0.6


@pytest.mark.parametrize("name", EXPECTED)
def test_zoom_out_widens_the_view_back_to_full(canvas, name):
    canvas.set_projection(name)
    canvas.zoom_full_extent()
    full = drawn_area(canvas)

    for _ in range(4):
        canvas.zoom_in()
    assert drawn_area(canvas) < full

    # Deliberately more clicks out than in. A conic is not symmetric: zooming in
    # starts from the half of the world it can actually draw, while zooming out
    # has to re-grow the whole request behind it, so getting back takes longer
    # than getting there. Every click still widens, and "full extent" is the
    # shortcut for anyone who does not want to click.
    widths = [drawn_area(canvas)]
    for _ in range(20):
        canvas.zoom_out()
        widths.append(drawn_area(canvas))

    assert widths[-1] > widths[0]
    # Zooming out saturates at the whole view rather than running past it.
    assert widths[-1] == pytest.approx(full, rel=0.02)


@pytest.mark.parametrize("name", EXPECTED)
def test_full_extent_returns_from_anywhere(canvas, name):
    canvas.set_projection(name)
    canvas.zoom_full_extent()
    full = drawn_area(canvas)

    canvas.set_extent(list(REGIONAL))
    canvas.zoom_in()
    canvas.zoom_in()
    assert drawn_area(canvas) < full

    canvas.zoom_full_extent()
    assert drawn_area(canvas) == pytest.approx(full, rel=0.01)


@pytest.mark.parametrize("name", EXPECTED)
def test_the_axes_stay_valid_through_a_long_zoom(canvas, name):
    """No projection may be walked into a state Cartopy cannot draw."""
    canvas.set_projection(name)

    for _ in range(12):
        canvas.zoom_in()
    for _ in range(20):
        canvas.zoom_out()
    for _ in range(6):
        canvas.pan_by(0.3, 0.3)

    x0, x1 = canvas.ax.get_xlim()
    y0, y1 = canvas.ax.get_ylim()
    assert x0 < x1 and y0 < y1
    assert all(math.isfinite(v) for v in (x0, x1, y0, y1))
    canvas.draw()


@pytest.mark.parametrize("name", EXPECTED)
def test_zoom_to_layer_after_loading_a_file(canvas, name, nc_north, nc_south):
    """The path a user actually takes: open a file, pick the projection, zoom to it.

    The layer is put in the hemisphere the projection faces — a north polar map
    cannot show a southern dataset, and showing its own hemisphere instead is
    the right answer rather than a failure.
    """
    canvas.set_projection(name)
    canvas.zoom_full_extent()
    full = drawn_area(canvas)

    canvas.load_netcdf(nc_south if name == "SouthPolarStereo" else nc_north)
    layer_name = next(iter(canvas.layers))
    canvas.zoom_to_layer(layer_name)

    assert drawn_area(canvas) < full
    assert canvas.layers[layer_name]['artist'] in canvas.ax.get_children()
    canvas.draw()


def test_a_polar_map_facing_away_from_the_data_shows_its_own_hemisphere(canvas, nc_north):
    """Not a failure: a north-polar map simply cannot show the far hemisphere."""
    canvas.load_netcdf(nc_north)
    canvas.set_projection("SouthPolarStereo")
    canvas.zoom_to_layer(next(iter(canvas.layers)))

    _west, _east, south, north = canvas.ax.get_extent(crs=ccrs.PlateCarree())
    assert south == pytest.approx(-90.0, abs=1.0)
    assert north <= 20.0


@pytest.mark.parametrize("name", EXPECTED)
def test_panning_stays_inside_the_world(canvas, name):
    canvas.set_projection(name)
    canvas.set_extent(list(REGIONAL))

    for _ in range(15):
        canvas.pan_by(0.25, 0.25)

    west, east, south, north = canvas.extent
    assert -180 <= west < east <= 180
    assert -90 <= south < north <= 90


def test_zoom_history_stays_in_lon_lat_across_a_switch(canvas):
    """Metres on the history stack would restore a nonsensical view later."""
    canvas.set_extent(list(REGIONAL))
    canvas.set_projection("Mercator")
    canvas.set_extent([-100.0, -80.0, 30.0, 40.0])

    canvas.zoom_previous()

    assert canvas.extent == pytest.approx(REGIONAL, abs=1e-6)


# ----------------------------------------------------------------------
# Everything bound to the old axes
# ----------------------------------------------------------------------
def test_a_switch_leaves_every_overlay_drawn(canvas, nc_standard):
    """Data, graticule, scale bar and colorbar all live on the axes being thrown away."""
    canvas.set_extent(list(REGIONAL))
    layer_name = load(canvas, nc_standard)
    canvas.set_graticule(True)
    canvas.scalebar_manager.set_visible(True)
    canvas.colorbar_manager.set_visible(True)

    canvas.set_projection("Mercator")

    artist = canvas.layers[layer_name]['artist']
    assert artist is not None and artist in canvas.ax.get_children()
    assert canvas.scalar_layer_order() == [layer_name]

    assert gridliner_count(canvas) == 1

    assert canvas.scalebar_manager._artists
    assert all(a.axes is canvas.ax for a in canvas.scalebar_manager._artists)

    assert canvas.colorbar_manager._colorbar is not None
    assert canvas.colorbar_manager._cax in canvas.fig.axes


@pytest.mark.parametrize("name", EXPECTED)
def test_a_switch_with_everything_on_never_raises(canvas, nc_standard, name):
    load(canvas, nc_standard)
    canvas.set_graticule(True)
    canvas.scalebar_manager.set_visible(True)
    canvas.colorbar_manager.set_visible(True)

    canvas.set_projection(name)
    canvas.draw()

    assert canvas.layers
    assert gridliner_count(canvas) == 1


def test_repeated_switching_leaks_no_axes(canvas, nc_standard):
    load(canvas, nc_standard)
    canvas.colorbar_manager.set_visible(True)

    for name in ("Mercator", "Robinson", "NorthPolarStereo", "PlateCarree"):
        canvas.set_projection(name)

    # One map, one colorbar. add_subplot() has not reused an existing subplot
    # since matplotlib 3.6, so a rebuild that skips fig.clear() stacks one per
    # switch and every redraw afterwards is slower than the last.
    assert len(canvas.fig.axes) == 2


def test_the_layer_is_redrawn_from_the_source_array(canvas, nc_standard):
    """Not from the artist: that holds whatever the last projection warped.

    Re-warping a warped array compounds the resampling, so the array a layer is
    redrawn from has to be the one that came out of the file.
    """
    layer_name = load(canvas, nc_standard)
    source_shape = canvas.layers[layer_name]['array'].shape

    for name in ("Mercator", "Robinson", "AlbersEqualArea", "PlateCarree"):
        canvas.set_projection(name)
        assert canvas.layers[layer_name]['array'].shape == source_shape

    # Back where it started, the drawn array is the file's own again.
    assert canvas.layers[layer_name]['artist'].get_array().shape == source_shape


def test_a_zoomed_layer_is_not_resampled_across_the_whole_world(canvas, nc_standard):
    """The extent has to be on the axes before the layers go back on it.

    Cartopy warps an image the moment imshow receives it, into whatever extent
    the axes has then, at a fixed target resolution. A fresh axes starts global,
    so redrawing first and setting the extent afterwards resamples the layer
    across the world and crops — losing exactly the detail being looked at.
    """
    layer_name = load(canvas, nc_standard)
    canvas.set_extent(list(REGIONAL))
    canvas.set_projection("Mercator")

    drawn = canvas.layers[layer_name]['artist'].get_extent()
    assert list(drawn[:2]) == pytest.approx(list(canvas.ax.get_xlim()), rel=1e-3)
    assert list(drawn[2:]) == pytest.approx(list(canvas.ax.get_ylim()), rel=1e-3)


def test_a_vector_layer_survives_a_switch(canvas):
    """Vectors go back through the loader that drew them the first time."""
    canvas.add_points([(40.0, -100.0), (45.0, -90.0)], layer_name='sites')
    assert 'sites' in canvas.layers

    canvas.set_projection("AlbersEqualArea")

    artist = canvas.layers['sites']['artist']
    assert artist is not None and artist in canvas.ax.get_children()
    assert canvas.layers['sites']['type'] == 'points'


def test_layer_visibility_survives_a_switch(canvas, nc_standard):
    layer_name = load(canvas, nc_standard)
    canvas.toggle_layer(layer_name, False)

    canvas.set_projection("Mercator")

    assert canvas.layers[layer_name]['visible'] is False
    assert canvas.layers[layer_name]['artist'].get_visible() is False


def test_a_theme_change_goes_through_the_same_rebuild(canvas, nc_standard):
    """One path, or the two drift and a layer quietly stops being drawn."""
    layer_name = load(canvas, nc_standard)
    canvas.set_projection("Mercator")

    canvas.apply_theme('dark')

    assert canvas.projection_name == "Mercator"
    assert canvas.layers[layer_name]['artist'] in canvas.ax.get_children()


# ----------------------------------------------------------------------
# Polar and global specifics
# ----------------------------------------------------------------------
def test_a_polar_cap_is_drawn_as_a_circle(canvas):
    canvas.set_projection("PlateCarree")
    box = canvas.ax.patch.get_path().vertices
    assert len(box) < 10  # a rectangle, corners and all

    canvas.set_projection("NorthPolarStereo")

    # set_boundary() has replaced that rectangle with the circular path, which
    # cartopy re-interpolates — so the count is "many", not an exact match.
    circle = canvas.ax.patch.get_path().vertices
    assert len(circle) >= len(projections.CIRCULAR_BOUNDARY.vertices)
    radii = np.hypot(circle[:, 0] - 0.5, circle[:, 1] - 0.5)
    assert radii.max() == pytest.approx(0.5, abs=0.02)
    assert radii.min() == pytest.approx(0.5, abs=0.02)


def test_the_scale_bar_is_not_clipped_away_by_a_map_boundary(canvas):
    """A polar cap's circle cut the bar off and left only its label behind."""
    canvas.scalebar_manager.set_visible(True)
    canvas.set_projection("NorthPolarStereo")

    assert canvas.scalebar_manager._artists
    assert not any(a.get_clip_on() for a in canvas.scalebar_manager._artists)


@pytest.mark.parametrize("name,pole", [("NorthPolarStereo", 90.0), ("SouthPolarStereo", -90.0)])
def test_a_polar_cap_reaches_its_pole(canvas, name, pole):
    canvas.set_extent(list(REGIONAL))
    canvas.set_projection(name)

    _west, _east, south, north = canvas.ax.get_extent(crs=ccrs.PlateCarree())
    assert (north == pytest.approx(pole) if pole > 0 else south == pytest.approx(pole))


@pytest.mark.parametrize("name", ("Robinson", "Mollweide", "NorthPolarStereo", "SouthPolarStereo"))
def test_fixed_shape_projections_keep_an_equal_aspect(canvas, name):
    """Stretching a globe or a cap to the widget would draw a false map."""
    canvas.set_projection(name)
    assert canvas.ax.get_aspect() == 1.0


@pytest.mark.parametrize("name", ("PlateCarree", "Mercator", "LambertConformal", "AlbersEqualArea"))
def test_box_projections_still_fill_the_canvas(canvas, name):
    """The 'auto' aspect is what keeps white margins off the map — see _style_axes."""
    canvas.set_projection(name)
    assert canvas.ax.get_aspect() == 'auto'


# ----------------------------------------------------------------------
# Basemaps under a projection
# ----------------------------------------------------------------------
def test_tiles_reproject_into_any_projection(canvas):
    """The XYZ draw path is projection-agnostic; only the warp cost changes."""
    image = np.zeros((32, 32, 4), dtype=np.uint8)
    mercator_extent = [-2e7, 2e7, -1e7, 1e7]

    for name in EXPECTED:
        canvas.set_projection(name)
        canvas._basemap_request_id += 1
        canvas._on_basemap_ready(image, mercator_extent, canvas._basemap_request_id)
        assert canvas._basemap_artist is not None
        canvas.draw()


def test_stretched_tiles_are_warned_about_but_not_disabled(canvas):
    """Requirement is a hint, not a restriction — the provider stays selectable."""
    messages = []
    canvas.status_update.connect(messages.append)

    canvas._basemap_kind = 'xyz'
    canvas.set_projection("NorthPolarStereo")
    assert any("reprojected" in m for m in messages)
    assert canvas._basemap_kind == 'xyz'

    messages.clear()
    canvas.set_projection("PlateCarree")
    assert not any("reprojected" in m for m in messages)


def test_the_offline_module_never_reaches_the_network():
    """Unchanged by the projection work, and the guarantee it exists for.

    Both offline sources are drawn through the same paths a projection change
    rebuilds, so it is worth restating that neither may acquire an import of
    contextily or of anything that fetches.
    """
    from pathlib import Path

    from ncexplorer_toolkit.geocanvas import offline_basemap

    source = Path(offline_basemap.__file__).read_text(encoding="utf-8")
    for forbidden in ("import contextily", "import requests", "import urllib",
                      "urlopen", "http://", "https://"):
        assert forbidden not in source, forbidden


def test_the_offline_vector_backdrop_reprojects(canvas):
    """offline_basemap builds its features in PlateCarree; Cartopy does the rest."""
    from ncexplorer_toolkit.geocanvas.offline_basemap import natural_earth_available

    if not natural_earth_available():
        pytest.skip("Natural Earth shapefiles are not cached on this machine")

    canvas.set_basemap(canvas.OFFLINE_NATURAL_EARTH)
    assert canvas._basemap_kind == 'natural_earth'

    canvas.set_projection("Mollweide")

    assert canvas._basemap_kind == 'natural_earth'
    assert canvas._ne_backdrop.active
    canvas.draw()


def test_the_basemap_fetch_bounds_stay_in_lon_lat(canvas):
    """bounds2img takes degrees; the axes have not been in degrees since the switch."""
    canvas.set_extent(list(REGIONAL))
    canvas.set_projection("LambertConformal")

    west, east, south, north = canvas.ax.get_extent(crs=ccrs.PlateCarree())
    assert -180 <= west < east <= 180
    assert -90 <= south < north <= 90


# ----------------------------------------------------------------------
# Projects
# ----------------------------------------------------------------------
def test_canvas_state_round_trips_a_projection(tmp_path):
    from ncexplorer_toolkit.core.project import (
        CanvasState, ProjectState, load_project, save_project,
    )

    state = ProjectState(canvas=CanvasState(
        extent=tuple(REGIONAL), projection="AlbersEqualArea", basemap="None",
    ))
    path = str(tmp_path / "study.ncx")

    save_project(path, state)
    loaded = load_project(path)

    assert loaded.canvas.projection == "AlbersEqualArea"
    assert list(loaded.canvas.extent) == pytest.approx(REGIONAL)


def test_a_project_extent_is_stored_in_lon_lat(canvas):
    """What a project saves is canvas.extent, so it must not be metres."""
    canvas.set_extent(list(REGIONAL))
    canvas.set_projection("Mercator")

    west, east, south, north = canvas.extent
    assert -180 <= west < east <= 180
    assert -90 <= south < north <= 90


def test_an_unknown_stored_projection_loads_as_the_default():
    from ncexplorer_toolkit.core.project import CanvasState

    state = CanvasState.from_dict({"projection": "Bonne"})
    crs, used = projections.build(state.projection, GLOBAL)

    assert used == projections.DEFAULT_PROJECTION
    assert isinstance(crs, ccrs.PlateCarree)


# ----------------------------------------------------------------------
# The window
#
# Constructing NCExplorerOperatorGUI is slow (it builds the whole CDO operator
# UI), so it is built once for the module, as tests/test_overlays_smoke.py does.
# ----------------------------------------------------------------------
@pytest.fixture(scope="module")
def window(qapp):
    from ncexplorer_toolkit.gui.main_window import NCExplorerOperatorGUI

    gui = NCExplorerOperatorGUI()
    yield gui
    gui.close()
    gui.deleteLater()


def test_the_selector_offers_exactly_the_registry(window):
    """Neither the canvas nor the window keeps a projection list of its own."""
    combo = window.projection_combo
    offered = [combo.itemText(i) for i in range(combo.count())]

    assert offered == list(projections.PROJECTION_CHOICES)
    assert combo.currentText() == window.geo_canvas.projection_name


def test_the_selector_drives_the_canvas(window):
    window.projection_combo.setCurrentText("Robinson")
    assert window.geo_canvas.projection_name == "Robinson"

    window.projection_combo.setCurrentText("PlateCarree")
    assert window.geo_canvas.projection_name == "PlateCarree"


def test_statistics_over_the_visible_extent_stay_in_lon_lat(window):
    """A box of metres read as degrees reports numbers for somewhere else.

    Nothing raises in that case, which is exactly why it is worth a test: the
    dock would quietly measure a region the user never asked about.
    """
    from ncexplorer_toolkit.gui.stats_dock import SOURCE_EXTENT

    canvas = window.geo_canvas
    canvas.set_extent(list(REGIONAL))
    canvas.set_projection("Mercator")

    # Against what the canvas actually shows, not the extent it was handed —
    # the real window has its own aspect ratio and _constrain_extent refits to it.
    shown = canvas.extent

    window.stats_dock.source_combo.setCurrentText(SOURCE_EXTENT)
    west, south, east, north = window.stats_dock.current_region().bounds

    assert (west, east) == pytest.approx(shown[:2], abs=1.0)
    assert (south, north) == pytest.approx(shown[2:], abs=1.0)

    canvas.set_projection("PlateCarree")
