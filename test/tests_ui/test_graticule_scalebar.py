# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""Graticule toggling and the manually-built scale bar."""

import math

import cartopy.crs as ccrs
import pytest
from cartopy.mpl.gridliner import Gridliner


def gridliner_count(canvas):
    """How many Gridliners are attached to the live axes."""
    return sum(1 for child in canvas.ax.get_children() if isinstance(child, Gridliner))


# ----------------------------------------------------------------------
# Graticule
# ----------------------------------------------------------------------
def test_graticule_on_and_off(canvas):
    canvas.set_graticule(True)
    assert canvas.graticule_visible
    assert gridliner_count(canvas) == 1

    canvas.set_graticule(False)
    assert not canvas.graticule_visible
    assert gridliner_count(canvas) == 0


def test_repeated_toggling_does_not_accumulate(canvas):
    """The old add_gridlines() made a new Gridliner per call with no removal."""
    for _ in range(2):
        canvas.set_graticule(True)
        canvas.set_graticule(False)

    assert gridliner_count(canvas) == 0

    canvas.set_graticule(True)
    canvas.set_graticule(True)
    canvas.set_graticule(True)

    assert gridliner_count(canvas) == 1


def test_toggling_draws_without_raising(canvas):
    for _ in range(2):
        canvas.set_graticule(True)
        canvas.draw()
        canvas.set_graticule(False)
        canvas.draw()


def test_graticule_labels_follow_the_theme(canvas):
    canvas.apply_theme('dark')
    canvas.set_graticule(True)
    dark_label = canvas._gridliner.xlabel_style['color']

    canvas.apply_theme('light')
    light_label = canvas._gridliner.xlabel_style['color']

    assert dark_label != light_label


def test_graticule_survives_a_theme_change(canvas):
    canvas.set_graticule(True)

    canvas.apply_theme('dark')

    assert canvas.graticule_visible
    assert gridliner_count(canvas) == 1


def test_graticule_labels_are_inside_the_figure(canvas):
    """The map fills the whole figure, so labels must be drawn *inside* it.

    With cartopy's default positive padding every label lands off-canvas and is
    silently clipped — visible as a graticule with no numbers on it.
    """
    canvas.set_graticule(True)
    canvas.draw()

    width, height = canvas.fig.get_size_inches() * canvas.fig.dpi
    renderer = canvas.get_renderer()
    labels = [
        artist
        for artist in canvas._gridliner.xlabel_artists + canvas._gridliner.ylabel_artists
        if artist.get_visible()
    ]
    assert labels, "the graticule drew no labels at all"

    outside = []
    for artist in labels:
        box = artist.get_window_extent(renderer)
        if box.x0 < 0 or box.x1 > width or box.y0 < 0 or box.y1 > height:
            outside.append(artist.get_text())

    assert outside == []


def test_add_gridlines_still_works(canvas):
    """The old entry point is kept as a wrapper for backwards compatibility."""
    canvas.add_gridlines()

    assert canvas.graticule_visible
    assert gridliner_count(canvas) == 1


# ----------------------------------------------------------------------
# Scale bar
# ----------------------------------------------------------------------
def is_nice_number(meters):
    """True if ``meters`` is 1, 2 or 5 times a power of ten."""
    exponent = math.floor(math.log10(meters))
    mantissa = meters / (10.0 ** exponent)
    return any(abs(mantissa - candidate) < 1e-9 for candidate in (1.0, 2.0, 5.0))


def test_scalebar_shows_and_hides(canvas):
    canvas.scalebar_manager.set_visible(True)
    assert canvas.scalebar_manager.visible
    assert canvas.scalebar_manager._artists

    canvas.scalebar_manager.set_visible(False)
    assert not canvas.scalebar_manager._artists


def test_scalebar_length_is_positive_and_round(canvas):
    canvas.scalebar_manager.set_visible(True)

    length = canvas.scalebar_manager.length_m

    assert length > 0
    assert is_nice_number(length)


def test_different_extents_give_different_lengths(canvas):
    canvas.scalebar_manager.set_visible(True)

    canvas.ax.set_extent([-180, 180, -90, 90], crs=ccrs.PlateCarree())
    canvas.scalebar_manager.refresh()
    wide = canvas.scalebar_manager.length_m

    canvas.ax.set_extent([-10, 10, -5, 5], crs=ccrs.PlateCarree())
    canvas.scalebar_manager.refresh()
    narrow = canvas.scalebar_manager.length_m

    assert wide > narrow > 0
    assert is_nice_number(wide) and is_nice_number(narrow)


def test_unit_switches_to_metres_when_zoomed_right_in(canvas):
    canvas.scalebar_manager.set_visible(True)

    canvas.ax.set_extent([0.0, 0.02, 0.0, 0.01], crs=ccrs.PlateCarree())
    canvas.scalebar_manager.refresh()

    assert canvas.scalebar_manager.length_m < 1000
    assert canvas.scalebar_manager.label.endswith(" m")


def test_label_uses_km_at_continental_scale(canvas):
    canvas.scalebar_manager.set_visible(True)

    canvas.ax.set_extent([-40, 40, -20, 20], crs=ccrs.PlateCarree())
    canvas.scalebar_manager.refresh()

    assert canvas.scalebar_manager.label.endswith(" km")


def test_bar_fraction_stays_within_the_axes(canvas):
    canvas.scalebar_manager.set_visible(True)

    _length, fraction, _label = canvas.scalebar_manager.measure()

    # Rounding down from a 20% candidate can only shrink the bar.
    assert 0 < fraction <= canvas.scalebar_manager.TARGET_FRACTION + 1e-9


def test_scalebar_refreshes_on_extent_change(canvas):
    canvas.scalebar_manager.set_visible(True)
    canvas.set_extent([-180, 180, -90, 90])
    wide = canvas.scalebar_manager.length_m

    canvas.set_extent([-20, 20, -10, 10])

    assert canvas.scalebar_manager.length_m != wide


def test_scalebar_does_not_accumulate_artists(canvas):
    canvas.scalebar_manager.set_visible(True)
    first = len(canvas.scalebar_manager._artists)

    for _ in range(10):
        canvas.scalebar_manager.refresh()

    assert len(canvas.scalebar_manager._artists) == first


def test_scalebar_sits_clear_of_the_attribution(canvas):
    """The basemap attribution owns the bottom-right corner."""
    assert canvas.scalebar_manager.ANCHOR_X < 0.5


def test_scalebar_box_clears_the_graticule_labels(canvas):
    """Both overlays live at the bottom of the map and must not overlap.

    The graticule's bottom labels are drawn just inside the map edge, so the
    scale bar's backdrop has to start above them.
    """
    manager = canvas.scalebar_manager
    box_bottom = manager.ANCHOR_Y - 0.014 - 0.008  # cap height plus padding

    assert box_bottom > 0.05


def test_scalebar_and_graticule_render_together(canvas):
    canvas.set_graticule(True)
    canvas.scalebar_manager.set_visible(True)
    canvas.draw()

    assert canvas.scalebar_manager._artists
    assert canvas._gridliner is not None


def test_scalebar_colors_follow_the_theme(canvas):
    canvas.apply_theme('dark')
    canvas.scalebar_manager.set_visible(True)
    dark_fg, _bg = canvas.scalebar_manager._theme_colors()

    canvas.apply_theme('light')
    light_fg, _bg = canvas.scalebar_manager._theme_colors()

    assert dark_fg != light_fg


def test_scalebar_survives_a_theme_change(canvas):
    canvas.scalebar_manager.set_visible(True)

    canvas.apply_theme('dark')

    assert canvas.scalebar_manager.visible
    assert canvas.scalebar_manager._artists


@pytest.mark.parametrize("meters,expected", [
    (1.0, 1.0), (3.4e5, 2e5), (9.9e5, 5e5), (1.0e6, 1.0e6),
    (7.5, 5.0), (23.0, 20.0), (999.0, 500.0),
])
def test_nice_length_rounding(canvas, meters, expected):
    assert canvas.scalebar_manager._nice_length(meters) == pytest.approx(expected)
