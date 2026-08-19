# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""Canvas zoom/pan arithmetic, clamping, and zoom-history behaviour."""

from types import SimpleNamespace

import pytest

GLOBAL_EXTENT = [-180, 180, -90, 90]


def centre(extent):
    return ((extent[0] + extent[1]) / 2, (extent[2] + extent[3]) / 2)


def width(extent):
    return extent[1] - extent[0]


def height(extent):
    return extent[3] - extent[2]


def test_zoom_in_narrows_and_keeps_centre(canvas):
    before = list(canvas.extent)
    after = canvas.zoom_in()

    assert width(after) < width(before)
    assert height(after) < height(before)
    assert centre(after) == pytest.approx(centre(before))


def test_zoom_out_widens(canvas):
    canvas.zoom_in(0.5)
    before = list(canvas.extent)
    after = canvas.zoom_out()

    assert width(after) > width(before)
    assert centre(after) == pytest.approx(centre(before))


def test_zoom_out_clamps_to_global_bounds(canvas):
    for _ in range(50):
        canvas.zoom_out()

    extent = canvas.extent
    assert width(extent) <= canvas.min_zoom_extent[0]
    assert height(extent) <= canvas.min_zoom_extent[1]
    assert extent[0] >= GLOBAL_EXTENT[0]
    assert extent[1] <= GLOBAL_EXTENT[1]
    assert extent[2] >= GLOBAL_EXTENT[2]
    assert extent[3] <= GLOBAL_EXTENT[3]


def test_zoom_in_clamps_without_inverting(canvas):
    for _ in range(50):
        canvas.zoom_in()

    extent = canvas.extent
    assert width(extent) > 0
    assert height(extent) > 0
    assert extent[0] < extent[1]
    assert extent[2] < extent[3]
    assert width(extent) >= canvas.max_zoom_extent[0] / 2


def test_zoom_full_extent(canvas):
    canvas.zoom_in(0.3)
    assert canvas.zoom_full_extent() == pytest.approx(GLOBAL_EXTENT)


def test_pan_by_shifts_by_expected_delta(canvas):
    canvas.set_extent([-100, -20, 0, 40])
    before = list(canvas.extent)

    after = canvas.pan_by(0.15, 0)

    expected_dx = width(before) * 0.15
    assert after[0] == pytest.approx(before[0] + expected_dx)
    assert after[1] == pytest.approx(before[1] + expected_dx)
    assert after[2] == pytest.approx(before[2])
    assert after[3] == pytest.approx(before[3])


def test_pan_east_clamps_at_antimeridian(canvas):
    canvas.set_extent([100, 180, 50, 90])
    before = list(canvas.extent)

    after = canvas.pan_by(0.15, 0)

    assert after[1] <= 180
    assert after == pytest.approx(before)


def test_pan_north_clamps_at_pole(canvas):
    canvas.set_extent([-40, 40, 50, 90])
    before = list(canvas.extent)

    after = canvas.pan_by(0, 0.15)

    assert after[3] <= 90
    assert after[2] < after[3]
    assert after == pytest.approx(before)


def test_pan_stays_valid_when_walking_into_a_corner(canvas):
    canvas.set_extent([-40, 40, 20, 60])
    for _ in range(20):
        canvas.pan_by(0.15, 0.15)

    extent = canvas.extent
    assert extent[0] < extent[1] and extent[2] < extent[3]
    assert extent[1] <= 180 and extent[3] <= 90


def test_zoom_previous_walks_the_whole_history(canvas):
    """Regression: zoom_previous() used to oscillate between the last two views.

    set_extent() records history, and zoom_previous() calls set_extent(), so
    without the restore guard 'back' pushed the current extent right back on.
    """
    steps = [
        [-100, -20, 0, 40],
        [-60, -20, 10, 30],
        [-50, -30, 15, 25],
        [-45, -35, 18, 23],
    ]
    seen = [list(canvas.extent)]
    for step in steps:
        canvas.set_extent(step)
        seen.append(list(canvas.extent))

    assert len(canvas.zoom_history) == 4

    for expected in reversed(seen[:-1]):
        canvas.zoom_previous()
        assert canvas.extent == pytest.approx(expected)

    assert canvas.zoom_history == []


def test_zoom_to_layer_records_history_once(canvas):
    extent = [-10, 10, -5, 5]
    canvas.property_manager.get_layer_property = lambda name: SimpleNamespace(
        dimensions=SimpleNamespace(extent=extent)
    )

    before = len(canvas.zoom_history)
    canvas.zoom_to_layer("fake")

    assert len(canvas.zoom_history) == before + 1


def test_scroll_and_zoom_in_share_one_implementation(canvas):
    """Wheel zoom and button zoom must not drift apart."""
    start = list(canvas.extent)
    focus_x, focus_y = centre(start)

    by_button = list(canvas.zoom_in(0.9))

    canvas._set_constrained_extent(start)
    event = SimpleNamespace(
        inaxes=canvas.ax, step=1, xdata=focus_x, ydata=focus_y, button="up"
    )
    canvas._on_scroll(event)
    by_wheel = list(canvas.extent)

    assert by_wheel == pytest.approx(by_button)


def test_zoom_respects_zoom_enabled_flag(canvas):
    canvas.zoom_enabled = False
    before = list(canvas.extent)
    canvas.zoom_in()
    canvas.zoom_out()
    assert canvas.extent == pytest.approx(before)
    canvas.zoom_enabled = True


def test_get_zoom_info_follows_the_extent(canvas):
    """@monitor_performance memoises get_zoom_info(); it must still stay fresh."""
    first = canvas.get_zoom_info()["width"]
    canvas.zoom_in(0.5)
    assert canvas.get_zoom_info()["width"] < first
