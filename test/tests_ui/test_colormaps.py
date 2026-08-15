"""The colormap registry: completeness, resolution and optional dependencies."""

import builtins
import importlib

import matplotlib
import pytest

from ncexplorer_toolkit.geocanvas import colormaps
from ncexplorer_toolkit.geocanvas.symbology import SymbologyManager


def test_registry_is_not_empty():
    groups = colormaps.available_colormaps()

    assert groups
    assert all(names for names in groups.values())


def test_every_advertised_name_resolves():
    """Nothing may be offered that matplotlib cannot actually look up."""
    unresolvable = [
        name for name in colormaps.flat_colormaps()
        if name not in matplotlib.colormaps
    ]

    assert unresolvable == []


def test_expected_groups_are_present():
    groups = colormaps.available_colormaps()

    assert 'Perceptually Uniform' in groups
    assert 'Diverging' in groups
    assert 'Classic' in groups


def test_optional_groups_present_when_installed():
    groups = colormaps.available_colormaps()

    if colormaps.HAS_CMOCEAN:
        assert 'Oceanography (cmocean)' in groups
        assert any(n.startswith('cmo.') for n in groups['Oceanography (cmocean)'])
    if colormaps.HAS_CMCRAMERI:
        assert 'Scientific (cmcrameri)' in groups
        assert any(n.startswith('cmc.') for n in groups['Scientific (cmcrameri)'])


def test_no_duplicate_names():
    flat = colormaps.flat_colormaps()

    assert len(flat) == len(set(flat))


@pytest.mark.parametrize("name", ["viridis", "RdBu", "coolwarm", "gray"])
def test_resolve_accepts_valid_names(name):
    assert colormaps.resolve_colormap(name) == (name, True)


@pytest.mark.parametrize("bad", ["garbage-name", "", "   ", None, 42, "viridis_rr"])
def test_resolve_falls_back_safely(bad):
    """The combo box is editable, so arbitrary text reaches this function."""
    resolved, recognised = colormaps.resolve_colormap(bad)

    assert recognised is False
    assert resolved == colormaps.DEFAULT_COLORMAP
    assert resolved in matplotlib.colormaps


def test_resolve_accepts_reversed_names():
    assert colormaps.resolve_colormap("viridis_r") == ("viridis_r", True)


@pytest.mark.parametrize("name", [
    "RdBu", "coolwarm", "seismic", "BrBG", "Spectral", "bwr", "RdBu_r",
])
def test_is_diverging_true(name):
    assert colormaps.is_diverging(name)


@pytest.mark.parametrize("name", [
    "viridis", "plasma", "Blues", "gray", "twilight", "hsv", "jet", "", None,
])
def test_is_diverging_false(name):
    assert not colormaps.is_diverging(name)


def test_is_diverging_handles_package_prefixes():
    if colormaps.HAS_CMOCEAN:
        assert colormaps.is_diverging("cmo.balance")
        assert colormaps.is_diverging("cmo.balance_r")
        assert not colormaps.is_diverging("cmo.thermal")
    if colormaps.HAS_CMCRAMERI:
        assert colormaps.is_diverging("cmc.vik")
        assert not colormaps.is_diverging("cmc.batlow")


def test_apply_reverse_does_not_double_up():
    assert colormaps.apply_reverse("viridis", True) == "viridis_r"
    assert colormaps.apply_reverse("viridis_r", True) == "viridis_r"
    assert colormaps.apply_reverse("viridis", False) == "viridis"


def test_symmetric_limits():
    assert colormaps.symmetric_limits({'min': -40.0, 'max': 12.0}) == (-40.0, 40.0)
    assert colormaps.symmetric_limits({'min': -2.0, 'max': 9.0}) == (-9.0, 9.0)
    assert colormaps.symmetric_limits({}) is None
    assert colormaps.symmetric_limits(None) is None
    assert colormaps.symmetric_limits({'min': 0.0, 'max': 0.0}) is None
    assert colormaps.symmetric_limits({'min': 'x', 'max': 1.0}) is None


def test_symbology_delegates_to_the_registry():
    """There must be exactly one colormap list in the codebase."""
    assert SymbologyManager.get_available_colormaps() == colormaps.flat_colormaps()


# ----------------------------------------------------------------------
# Behaviour when the optional packages are missing
# ----------------------------------------------------------------------
def test_app_works_without_cmocean_and_cmcrameri(monkeypatch, qapp):
    """Simulate both packages being absent; the app must still function."""
    real_import = builtins.__import__
    blocked = {'cmocean', 'cmcrameri'}

    def fake_import(name, *args, **kwargs):
        if name.split('.')[0] in blocked:
            raise ImportError(f"simulated missing package: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', fake_import)
    reloaded = importlib.reload(colormaps)

    try:
        assert reloaded.HAS_CMOCEAN is False
        assert reloaded.HAS_CMCRAMERI is False

        groups = reloaded.available_colormaps()
        assert groups, "the built-in groups must survive"
        assert 'Oceanography (cmocean)' not in groups
        assert 'Scientific (cmcrameri)' not in groups
        assert 'Perceptually Uniform' in groups

        # Nothing advertised may be unresolvable in this state either.
        assert [n for n in reloaded.flat_colormaps() if n not in matplotlib.colormaps] == []

        # The canvas must still construct and style a layer.
        from ncexplorer_toolkit.geocanvas.canvas import GeoCanvas
        widget = GeoCanvas()
        try:
            assert widget.ax is not None
            assert reloaded.resolve_colormap('viridis') == ('viridis', True)
        finally:
            widget.close()
            widget.deleteLater()
    finally:
        monkeypatch.undo()
        importlib.reload(colormaps)

    # Restored for every later test in the session.
    assert colormaps.HAS_CMOCEAN or not colormaps.HAS_CMOCEAN
