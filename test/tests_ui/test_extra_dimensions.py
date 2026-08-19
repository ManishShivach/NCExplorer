# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""Variables that are not simply (time, lat, lon).

NOAAGlobalTemp's gridded product carries a singleton ``z`` level between the
time axis and the grid, and model output can add an ensemble member on top of
that. The loader half-handled it — it dropped one extra dimension and stopped —
while the time slider and the variable picker did not handle it at all, so
moving the slider on such a file passed a 3-D array to imshow and killed the
application: matplotlib rejects the shape on the default projection, and on any
other cartopy raises from inside its own warp first.
"""

import numpy as np
import pytest
import xarray as xr


N_LAT, N_LON, N_TIME = 36, 72, 6
LATS = np.linspace(-87.5, 87.5, N_LAT)
LONS = np.linspace(-177.5, 177.5, N_LON)


def _write(path, dims, shape, name="anom"):
    """A file whose variable has exactly ``dims``, each step distinguishable."""
    values = np.arange(np.prod(shape), dtype="float32").reshape(shape)
    coords = {"lat": LATS, "lon": LONS, "time": np.arange(N_TIME, dtype="float64")}
    coords = {d: coords[d] for d in dims if d in coords}
    for dim, size in zip(dims, shape):
        coords.setdefault(dim, np.arange(size, dtype="float64"))
    xr.Dataset({name: (dims, values)}, coords=coords).to_netcdf(path)
    return str(path)


@pytest.fixture(scope="module")
def nc_with_level(tmp_path_factory):
    """(time, z, lat, lon) — the NOAAGlobalTemp shape."""
    path = tmp_path_factory.mktemp("level") / "noaa_like.nc"
    return _write(path, ("time", "z", "lat", "lon"), (N_TIME, 1, N_LAT, N_LON))


@pytest.fixture(scope="module")
def nc_with_two_extras(tmp_path_factory):
    """(time, z, ensemble, lat, lon) — one extra dimension too many for a `break`."""
    path = tmp_path_factory.mktemp("ens") / "ensemble.nc"
    return _write(path, ("time", "z", "ensemble", "lat", "lon"),
                  (N_TIME, 1, 2, N_LAT, N_LON))


def drawn(canvas, name):
    return canvas.layers[name]['array']


def test_a_level_dimension_loads_as_a_flat_field(canvas, nc_with_level):
    canvas.load_netcdf(nc_with_level, layer_name="noaa")
    assert drawn(canvas, "noaa").ndim == 2


def test_two_extra_dimensions_also_load_flat(canvas, nc_with_two_extras):
    """The loader dropped the first extra dimension and stopped."""
    canvas.load_netcdf(nc_with_two_extras, layer_name="ens")
    assert drawn(canvas, "ens").ndim == 2


def test_the_time_slider_steps_a_levelled_file(canvas, nc_with_level):
    """The crash: opening the slider on such a file took the app down."""
    canvas.load_netcdf(nc_with_level, layer_name="noaa")
    canvas.open_time_slider("noaa")

    first = np.array(drawn(canvas, "noaa"))
    assert first.ndim == 2

    canvas.update_time_step(3)
    later = np.array(drawn(canvas, "noaa"))

    assert later.ndim == 2
    assert not np.allclose(first, later), "the slider moved but the data did not"


def test_the_time_slider_steps_under_another_projection(canvas, nc_with_level):
    """The reported traceback: cartopy warps the array, and dies before imshow."""
    canvas.load_netcdf(nc_with_level, layer_name="noaa")
    canvas.set_projection("Robinson")
    canvas.open_time_slider("noaa")
    canvas.update_time_step(2)

    assert drawn(canvas, "noaa").ndim == 2
    assert canvas.layers["noaa"]['artist'] is not None


def test_the_properties_path_also_flattens(canvas, nc_with_level):
    """update_netcdf_layer is the variable picker's and the animation dock's route."""
    canvas.load_netcdf(nc_with_level, layer_name="noaa")
    canvas.set_netcdf_time_index("noaa", 4)
    canvas.update_netcdf_layer("noaa")

    assert drawn(canvas, "noaa").ndim == 2


def test_an_out_of_range_time_index_falls_back_to_the_first_step(canvas, nc_with_level):
    canvas.load_netcdf(nc_with_level, layer_name="noaa")
    canvas.set_netcdf_time_index("noaa", 99)
    canvas.update_netcdf_layer("noaa")

    expected = xr.open_dataset(nc_with_level, decode_times=False)["anom"].isel(
        time=0, z=0).values
    assert np.allclose(drawn(canvas, "noaa"), expected)


def test_a_non_flat_array_is_refused_rather_than_drawn(canvas, nc_standard):
    """The guard: neither way out of _set_layer_array survives a 3-D array."""
    canvas.load_netcdf(str(nc_standard), layer_name="field")
    record = canvas.layers["field"]
    before = np.array(record['array'])

    complaints = []
    canvas.status_update.connect(complaints.append)

    # No exception: raised in a Qt slot, it would abort the application.
    canvas._set_layer_array("field", record, np.zeros((1, N_LAT, N_LON)))

    assert np.allclose(record['array'], before), "the bad array was kept"
    assert any("2-D field" in message for message in complaints)
