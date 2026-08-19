# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""Headless Qt test setup.

The suite runs with no display: ``QT_QPA_PLATFORM=offscreen python -m pytest tests/``.
pytest-qt is optional — the fixtures below only need a QApplication, so the
tests work either way.
"""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# In a conda environment Qt reports the plugin path of conda's own (Qt5) qt
# package, whose platform plugins cannot be loaded by Qt6 — QApplication then
# aborts with "Could not find the Qt platform plugin". Point Qt at PyQt6's own
# plugins, which are always the right ones for the PyQt6 in use.
if "QT_QPA_PLATFORM_PLUGIN_PATH" not in os.environ:
    import PyQt6

    bundled = Path(PyQt6.__file__).resolve().parent / "Qt6" / "plugins"
    if (bundled / "platforms").is_dir():
        os.environ["QT_PLUGIN_PATH"] = str(bundled)
        os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(bundled / "platforms")

import numpy as np  # noqa: E402
import xarray as xr  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402  (must follow the env setup)


@pytest.fixture(scope="session")
def qapp():
    """One QApplication for the whole session (Qt allows only one)."""
    app = QApplication.instance() or QApplication([])
    return app


# ----------------------------------------------------------------------
# Synthetic NetCDF fixtures
#
# Built with xarray so the suite needs no external data files. All of them use
# the same 36×72 half-degree-offset grid, and the same value pattern:
#
#     value = row * 100 + col + time * 10000
#
# which makes every cell in a time step unique. That is what lets the hover
# tests assert an exact value for a known position, and what makes a
# vertically mirrored read (the origin='lower'/'upper' trap) obvious rather
# than merely plausible.
# ----------------------------------------------------------------------

N_LAT, N_LON, N_TIME = 36, 72, 12
LATS = np.linspace(-87.5, 87.5, N_LAT)
LONS_180 = np.linspace(-177.5, 177.5, N_LON)
LONS_360 = np.linspace(2.5, 357.5, N_LON)


def _ramp_values():
    """The row*100 + col + time*10000 cube described above."""
    rows = np.arange(N_LAT)[None, :, None]
    cols = np.arange(N_LON)[None, None, :]
    times = np.arange(N_TIME)[:, None, None]
    return (rows * 100 + cols + times * 10000).astype(float)


def _write(path, values, lons, lats=LATS, attrs=None, name="t2m"):
    """Write one variable to NetCDF and return the path as a string."""
    if attrs is None:
        attrs = {"units": "K", "long_name": "Air temperature"}
    dataset = xr.Dataset(
        {name: (("time", "lat", "lon"), values, attrs)},
        coords={"time": np.arange(values.shape[0]), "lat": lats, "lon": lons},
    )
    dataset.to_netcdf(path)
    dataset.close()
    return str(path)


@pytest.fixture(scope="session")
def nc_standard(tmp_path_factory):
    """Ordinary −180…180 longitudes, 12 time steps, with CF attributes."""
    path = tmp_path_factory.mktemp("nc_standard") / "standard.nc"
    return _write(path, _ramp_values(), LONS_180)


@pytest.fixture(scope="session")
def nc_lon360(tmp_path_factory):
    """Same field on a 0…360 longitude axis, as many climate files use."""
    path = tmp_path_factory.mktemp("nc_lon360") / "lon360.nc"
    return _write(path, _ramp_values(), LONS_360)


@pytest.fixture(scope="session")
def nc_with_nans(tmp_path_factory):
    """Ramp field with a NaN block, for the missing-data readout."""
    values = _ramp_values()
    # A patch in the southern hemisphere, away from the cells the value tests
    # use, so the two do not interfere.
    values[:, 4:8, 4:8] = np.nan
    path = tmp_path_factory.mktemp("nc_nan") / "with_nans.nc"
    return _write(path, values, LONS_180)


@pytest.fixture(scope="session")
def nc_anomaly(tmp_path_factory):
    """Anomaly field spanning both signs, deliberately asymmetric.

    min is −40 and max is +12, so centring on zero must produce (−40, +40) —
    a range that neither bound alone would give. The same field is repeated at
    every time step, so the displayed slice spans both signs whichever step is
    selected.
    """
    plane = np.linspace(-40.0, 12.0, N_LAT * N_LON).reshape(N_LAT, N_LON)
    values = np.repeat(plane[None, :, :], N_TIME, axis=0)
    path = tmp_path_factory.mktemp("nc_anomaly") / "anomaly.nc"
    return _write(
        path, values, LONS_180,
        attrs={"units": "K", "long_name": "Temperature anomaly"},
        name="anom",
    )


@pytest.fixture
def canvas(qapp):
    """A bare GeoCanvas with a deterministic aspect ratio.

    GeoCanvas._constrain_extent fits every extent to ``aspect_ratio``; the
    default 12/8 = 1.5 would silently narrow the global extent to 270° of
    longitude, so the zoom tests pin the ratio to the 2:1 of [-180,180,-90,90].
    """
    from ncexplorer_toolkit.geocanvas.canvas import GeoCanvas

    widget = GeoCanvas()
    widget.aspect_ratio = 2.0
    widget.resize(800, 400)
    widget._set_constrained_extent([-180, 180, -90, 90])
    widget.zoom_history.clear()
    yield widget
    widget.close()
    widget.deleteLater()
