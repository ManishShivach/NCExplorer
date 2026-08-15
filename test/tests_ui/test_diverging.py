"""Diverging colour scales centred on zero."""

import pytest

from ncexplorer_toolkit.geocanvas import colormaps
from ncexplorer_toolkit.geocanvas.properties import LayerStyleProperties


def load_anomaly(canvas, path):
    """Load the anomaly fixture and return (layer_name, layer_prop, artist)."""
    canvas.load_netcdf(path)
    layer_name = next(iter(canvas.layers))
    layer_prop = canvas.property_manager.get_layer_property(layer_name)
    return layer_name, layer_prop, canvas.layers[layer_name]['artist']


def test_default_is_off():
    assert LayerStyleProperties().diverging_center_zero is False


def test_serialised_in_to_dict():
    style = LayerStyleProperties()
    style.diverging_center_zero = True

    assert style.to_dict()['diverging_center_zero'] is True


def test_round_trips_through_from_dict():
    style = LayerStyleProperties()
    style.diverging_center_zero = True

    restored = LayerStyleProperties()
    restored.from_dict(style.to_dict())

    assert restored.diverging_center_zero is True


def test_statistics_are_available_to_centre_on(canvas, nc_anomaly):
    """The loader already computes min/max; this feature must reuse them."""
    _name, layer_prop, _artist = load_anomaly(canvas, nc_anomaly)
    stats = layer_prop.metadata.statistics

    assert stats['min'] == pytest.approx(-40.0)
    assert stats['max'] == pytest.approx(12.0)


def test_enabled_gives_a_symmetric_clim(canvas, nc_anomaly):
    layer_name, layer_prop, artist = load_anomaly(canvas, nc_anomaly)
    layer_prop.style.colormap = 'RdBu'
    layer_prop.style.diverging_center_zero = True

    canvas.update_layer_display(layer_name)

    vmin, vmax = artist.get_clim()
    assert vmin == pytest.approx(-vmax)
    assert vmax == pytest.approx(40.0)


def test_disabled_leaves_the_scale_asymmetric(canvas, nc_anomaly):
    layer_name, layer_prop, artist = load_anomaly(canvas, nc_anomaly)
    layer_prop.style.diverging_center_zero = False

    canvas.update_layer_display(layer_name)

    vmin, vmax = artist.get_clim()
    assert vmin != pytest.approx(-vmax)


def test_explicit_limits_override_centering(canvas, nc_anomaly):
    """A user who typed a range means it — auto-centring must not fight them."""
    layer_name, layer_prop, artist = load_anomaly(canvas, nc_anomaly)
    layer_prop.style.colormap = 'RdBu'
    layer_prop.style.diverging_center_zero = True
    layer_prop.style.vmin = -5.0
    layer_prop.style.vmax = 25.0

    canvas.update_layer_display(layer_name)

    assert artist.get_clim() == pytest.approx((-5.0, 25.0))


def test_partial_explicit_limit_still_overrides(canvas, nc_anomaly):
    layer_name, layer_prop, artist = load_anomaly(canvas, nc_anomaly)
    layer_prop.style.diverging_center_zero = True
    layer_prop.style.vmax = 30.0

    canvas.update_layer_display(layer_name)

    assert artist.get_clim()[1] == pytest.approx(30.0)


def test_honoured_by_get_matplotlib_style(canvas, nc_anomaly):
    """The symbology path must agree with the canvas path."""
    layer_name, layer_prop, _artist = load_anomaly(canvas, nc_anomaly)
    layer_prop.style.colormap = 'RdBu'
    layer_prop.style.diverging_center_zero = True

    style = canvas.symbology_manager.get_matplotlib_style(layer_name)

    assert style['vmin'] == pytest.approx(-40.0)
    assert style['vmax'] == pytest.approx(40.0)
    assert style['cmap'] == 'RdBu'


def test_symbology_respects_explicit_limits(canvas, nc_anomaly):
    layer_name, layer_prop, _artist = load_anomaly(canvas, nc_anomaly)
    layer_prop.style.diverging_center_zero = True
    layer_prop.style.vmin = -1.0
    layer_prop.style.vmax = 1.0

    style = canvas.symbology_manager.get_matplotlib_style(layer_name)

    assert (style['vmin'], style['vmax']) == pytest.approx((-1.0, 1.0))


def test_reverse_and_centering_combine(canvas, nc_anomaly):
    layer_name, layer_prop, artist = load_anomaly(canvas, nc_anomaly)
    layer_prop.style.colormap = 'RdBu'
    layer_prop.style.reverse_colormap = True
    layer_prop.style.diverging_center_zero = True

    canvas.update_layer_display(layer_name)

    assert artist.get_cmap().name == 'RdBu_r'
    vmin, vmax = artist.get_clim()
    assert vmin == pytest.approx(-vmax)


def test_raster_clim_helper():
    style = LayerStyleProperties()
    stats = {'min': -40.0, 'max': 12.0}

    assert colormaps.raster_clim(style, stats) is None

    style.diverging_center_zero = True
    assert colormaps.raster_clim(style, stats) == (-40.0, 40.0)

    style.vmin = 3.0
    assert colormaps.raster_clim(style, stats) == (3.0, None)


def test_unknown_colormap_warns_and_falls_back(canvas, nc_anomaly):
    """Free-typed text in the editable combo must not break the display."""
    layer_name, layer_prop, artist = load_anomaly(canvas, nc_anomaly)
    warnings = []
    canvas.status_update.connect(warnings.append)

    layer_prop.style.colormap = 'not-a-real-colormap'
    canvas.update_layer_display(layer_name)

    assert artist.get_cmap().name == colormaps.DEFAULT_COLORMAP
    assert any('not-a-real-colormap' in message for message in warnings)
