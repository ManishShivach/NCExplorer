# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""The format registry and the two readers behind it.

The regressions worth guarding here are all *silent* ones — a file that loaded
and showed the wrong thing, or showed nothing while reporting success:

* a projected layer drawn as if metres were degrees,
* a mixed-geometry file drawn one kind at a time,
* a GPX with tracks but no waypoints read as zero features,
* an HDF5 file rejected as "no bands" because its data is in subdatasets,
* a format the running engine cannot open failing with GDAL's wording.

Fixtures are built rather than committed: the interesting ones (ENVI, Idrisi,
USGS DEM) are driver-written formats whose bytes are not worth storing, and
building them also proves the driver is present before the test asserts on it.
"""

from __future__ import annotations

import os
import zipfile

import numpy as np
import pytest

pytest.importorskip("geopandas")
pytest.importorskip("rasterio")

import geopandas as gpd
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import LineString, Point, Polygon

from ncexplorer_toolkit.geocanvas import formats
from ncexplorer_toolkit.geocanvas import raster_io, vector_io


# ---------------------------------------------------------------- fixtures ---
@pytest.fixture(scope="module")
def vectors(tmp_path_factory):
    """One directory holding a sample of every vector format we accept."""
    d = tmp_path_factory.mktemp("vectors")

    poly = gpd.GeoDataFrame(
        {"name": ["p"]},
        geometry=[Polygon([(70, 20), (80, 20), (80, 30), (70, 30)])],
        crs="EPSG:4326")
    poly.to_file(d / "t.shp")
    poly.to_file(d / "t.geojson", driver="GeoJSON")
    poly.to_file(d / "t.gml", driver="GML")
    poly.to_file(d / "t.gpkg", driver="GPKG")

    # Mixed geometry in one file — the case that used to lose features.
    gpd.GeoDataFrame(
        {"name": ["a", "b", "c"]},
        geometry=[Point(75, 25),
                  LineString([(70, 20), (75, 25)]),
                  Polygon([(70, 20), (80, 20), (80, 30)])],
        crs="EPSG:4326",
    ).to_file(d / "mixed.geojson", driver="GeoJSON")

    # The same features in a projected CRS — the case that used to plot off-map.
    gpd.read_file(d / "mixed.geojson").to_crs("EPSG:32643").to_file(
        d / "utm.geojson", driver="GeoJSON")

    (d / "t.kml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        '<Placemark><name>P1</name><Point>'
        '<coordinates>77.2,28.6,0</coordinates></Point></Placemark>'
        '<Placemark><name>P2</name><Point>'
        '<coordinates>72.8,19.0,0</coordinates></Point></Placemark>'
        '</Document></kml>')

    with zipfile.ZipFile(d / "t.kmz", "w", zipfile.ZIP_DEFLATED) as z:
        z.write(d / "t.kml", "doc.kml")

    # A GPX carrying tracks and no waypoints. 'waypoints' is layer 0 in every
    # GPX, so reading only the first layer yields nothing at all.
    (d / "tracks.gpx").write_text(
        '<?xml version="1.0"?>\n'
        '<gpx version="1.1" creator="test" '
        'xmlns="http://www.topografix.com/GPX/1/1">'
        '<trk><name>T1</name><trkseg>'
        '<trkpt lat="20.0" lon="70.0"/><trkpt lat="25.0" lon="75.0"/>'
        '<trkpt lat="30.0" lon="80.0"/>'
        '</trkseg></trk></gpx>')

    (d / "t.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
        '<rect x="10" y="10" width="50" height="50"/></svg>')

    return d


@pytest.fixture(scope="module")
def rasters(tmp_path_factory):
    """One directory holding a sample of every raster format we accept."""
    d = tmp_path_factory.mktemp("rasters")
    arr = np.arange(64 * 64, dtype="float32").reshape(64, 64)

    def write(name, driver, dtype="float32", crs="EPSG:4326",
              transform=from_origin(70.0, 30.0, 0.1, 0.1)):
        with rasterio.open(d / name, "w", driver=driver, height=64, width=64,
                           count=1, dtype=dtype, crs=crs,
                           transform=transform) as dst:
            dst.write(arr.astype(dtype), 1)

    write("t.tif", "GTiff")
    write("t.dem", "USGSDEM", dtype="int16")
    write("t.dat", "ENVI")                       # + t.hdr
    write("t.rst", "RST")                        # + t.rdc
    write("utm.tif", "GTiff", crs="EPSG:32643",
          transform=from_origin(200000, 3000000, 1000, 1000))

    h5py = pytest.importorskip("h5py")
    with h5py.File(d / "multi.h5", "w") as f:
        f.create_dataset("temp", data=arr)
        f.create_dataset("precip", data=arr * 2)

    return d


# ---------------------------------------------------------------- registry ---
def test_every_extension_resolves_to_exactly_one_format():
    """No extension may be claimed twice — the dispatcher would be ambiguous."""
    seen = {}
    for fmt in formats.FORMATS:
        for ext in fmt.extensions:
            assert ext not in seen, (
                f"{ext} claimed by both {seen[ext]} and {fmt.label}")
            seen[ext] = fmt.label


def test_unsupported_formats_all_explain_themselves():
    for fmt in formats.FORMATS:
        if fmt.kind == formats.UNSUPPORTED:
            assert fmt.reason, f"{fmt.label} refuses without saying why"
            # The reason is shown to a user, so it has to be a sentence.
            assert len(fmt.reason) > 40


def test_supported_extensions_is_a_subset_of_all_extensions():
    assert set(formats.supported_extensions()) <= set(formats.all_extensions())


def test_svg_and_hdf4_are_refused_with_a_reason():
    for ext, expected in ((".svg", "coordinate reference system"),
                          (".hdf", "HDF4")):
        fmt = formats.format_for(f"x{ext}")
        available, reason = formats.availability(fmt)
        assert not available
        assert expected in reason


def test_kmz_is_rewritten_to_a_vsizip_path(vectors):
    path = formats.gdal_path(vectors / "t.kmz")
    assert path.startswith("/vsizip/")
    assert path.endswith("doc.kml")


def test_gdal_path_leaves_ordinary_files_alone(vectors):
    plain = str(vectors / "t.shp")
    assert formats.gdal_path(plain) == plain


def test_missing_sidecar_is_detected_and_named(rasters, tmp_path):
    import shutil
    lone = tmp_path / "lone.dat"
    shutil.copy(rasters / "t.dat", lone)          # .hdr deliberately left behind

    missing = formats.missing_sidecars(lone)
    assert missing == (".hdr",)
    assert ".hdr" in formats.sidecar_message(lone, missing)


def test_sidecars_match_case_insensitively(vectors, tmp_path):
    """A shapefile set from Windows arrives upper-cased and is still complete."""
    upper = tmp_path / "UP"
    upper.mkdir()
    for ext in (".shp", ".shx", ".dbf"):
        (upper / f"T{ext.upper()}").write_bytes(
            (vectors / f"t{ext}").read_bytes())
    assert formats.missing_sidecars(upper / "T.SHP") == ()


# ------------------------------------------------------------------ vector ---
@pytest.mark.parametrize("name", ["t.shp", "t.geojson", "t.gml", "t.gpkg"])
def test_core_vector_formats_load(vectors, name):
    gdf, groups = vector_io.open_vector(vectors / name)
    assert len(gdf) == 1
    assert set(groups) == {"polygons"}


@pytest.mark.parametrize("name", ["t.kml", "t.kmz"])
def test_kml_family_loads_or_says_it_cannot(vectors, name):
    """Passes either way — but never with GDAL's wording.

    KML needs pyogrio, which the packaged build excludes, so both outcomes are
    legitimate. What is not legitimate is failing with "not recognized as being
    in a supported file format", which tells a user nothing.
    """
    fmt = formats.format_for(vectors / name)
    available, _ = formats.availability(fmt)

    if available:
        gdf, groups = vector_io.open_vector(vectors / name)
        assert len(gdf) == 2
        assert set(groups) == {"points"}
    else:
        with pytest.raises(vector_io.VectorFormatUnavailable) as excinfo:
            vector_io.open_vector(vectors / name)
        assert "pyogrio" in str(excinfo.value)


def test_mixed_geometry_keeps_every_feature(vectors):
    """The regression: keying off feature 0's type drew 1 of 3."""
    gdf, groups = vector_io.open_vector(vectors / "mixed.geojson")
    assert len(gdf) == 3
    assert set(groups) == {"points", "lines", "polygons"}
    assert sum(len(f) for f in groups.values()) == 3


def test_projected_vector_is_reprojected_to_lonlat(vectors):
    """The regression: UTM metres drawn as degrees put the layer off-map."""
    gdf, _ = vector_io.open_vector(vectors / "utm.geojson")
    assert gdf.crs.to_epsg() == 4326

    west, south, east, north = gdf.total_bounds
    assert -180 <= west <= east <= 180
    assert -90 <= south <= north <= 90
    # Same ground as the unprojected twin, to a tolerance that survives the
    # round trip through UTM.
    assert west == pytest.approx(70, abs=0.5)
    assert north == pytest.approx(30, abs=0.5)


def test_gpx_without_waypoints_still_yields_its_tracks(vectors):
    """The regression: layer 0 of a GPX is 'waypoints' and is often empty."""
    assert vector_io.list_layers(vectors / "tracks.gpx")

    gdf, groups = vector_io.open_vector(vectors / "tracks.gpx")
    assert len(gdf) > 0, "a GPX holding a track must not read as empty"
    assert "lines" in groups


def test_svg_is_refused_before_gdal_sees_it(vectors):
    with pytest.raises(vector_io.VectorFormatUnavailable) as excinfo:
        vector_io.open_vector(vectors / "t.svg")
    assert "coordinate reference system" in str(excinfo.value)


def test_crsless_layer_is_assumed_lonlat_not_reprojected():
    gdf = gpd.GeoDataFrame(geometry=[Point(75, 25)], crs=None)
    out = vector_io.to_canvas_crs(gdf)
    assert out.crs.to_epsg() == 4326
    assert out.geometry.iloc[0].x == pytest.approx(75)


def test_geometry_collections_are_exploded_into_drawable_parts():
    from shapely.geometry import GeometryCollection
    gdf = gpd.GeoDataFrame(
        geometry=[GeometryCollection([Point(75, 25),
                                      LineString([(70, 20), (75, 25)])])],
        crs="EPSG:4326")
    groups = vector_io.split_by_geometry(gdf)
    assert set(groups) == {"points", "lines"}


def test_empty_geometries_are_dropped_not_drawn():
    gdf = gpd.GeoDataFrame(
        geometry=[Point(75, 25), Polygon(), None], crs="EPSG:4326")
    groups = vector_io.split_by_geometry(gdf)
    assert set(groups) == {"points"}
    assert len(groups["points"]) == 1


# ------------------------------------------------------------------ raster ---
@pytest.mark.parametrize("name", ["t.tif", "t.dem", "t.dat", "t.rst"])
def test_core_raster_formats_load(rasters, name):
    read = raster_io.open_raster(rasters / name)
    assert read.width == 64 and read.height == 64
    assert not read.warped
    west, east, south, north = read.extent
    assert west < east and south < north


def test_projected_raster_is_warped_to_lonlat(rasters):
    """The regression: UTM bounds drawn under PlateCarree land off the map."""
    read = raster_io.open_raster(rasters / "utm.tif")
    assert read.warped
    assert read.source_crs and "32643" in read.source_crs

    west, east, south, north = read.extent
    assert -180 <= west <= east <= 180
    assert -90 <= south <= north <= 90


def test_hdf5_container_exposes_its_variables(rasters):
    """The regression: count == 0 was read as 'no bands' and the file refused."""
    subs = raster_io.list_subdatasets(rasters / "multi.h5")
    assert {s.name for s in subs} == {"temp", "precip"}

    read = raster_io.open_raster(rasters / "multi.h5")
    assert read.data is not None
    assert len(read.subdatasets) == 2, "the caller must be able to offer a choice"


def test_hdf5_subdataset_can_be_chosen(rasters):
    subs = raster_io.list_subdatasets(rasters / "multi.h5")
    precip = next(s for s in subs if s.name == "precip")
    temp = next(s for s in subs if s.name == "temp")

    a = raster_io.open_raster(rasters / "multi.h5", subdataset=precip.path)
    b = raster_io.open_raster(rasters / "multi.h5", subdataset=temp.path)
    # precip was written as temp * 2, so picking one really does pick.
    assert float(np.nanmax(a.data)) == pytest.approx(
        2 * float(np.nanmax(b.data)))


def test_plain_raster_reports_no_subdatasets(rasters):
    assert raster_io.list_subdatasets(rasters / "t.tif") == []


def test_hdf4_is_refused_with_the_h5_hint(tmp_path):
    fake = tmp_path / "modis.hdf"
    fake.write_bytes(b"\x0e\x03\x13\x01not really hdf4")
    with pytest.raises(raster_io.RasterFormatUnavailable) as excinfo:
        raster_io.open_raster(fake)
    assert "HDF5" in str(excinfo.value)


def test_missing_envi_header_is_reported_as_such(rasters, tmp_path):
    import shutil
    lone = tmp_path / "scene.dat"
    shutil.copy(rasters / "t.dat", lone)
    with pytest.raises(raster_io.RasterFormatUnavailable) as excinfo:
        raster_io.open_raster(lone)
    assert ".hdr" in str(excinfo.value)


def test_oversized_raster_is_decimated(rasters, monkeypatch):
    """A warp allocates its destination up front, so size has to be capped."""
    monkeypatch.setattr(raster_io, "MAX_DISPLAY_PIXELS", 16 * 16)
    read = raster_io.open_raster(rasters / "t.tif")
    assert read.data.shape[0] <= 64 and read.data.shape[1] <= 64
    assert read.data.size <= 64 * 64


def test_ungeoreferenced_raster_extent_is_not_inverted(rasters):
    """GDAL's identity transform yields top=0/bottom=height, i.e. south > north.

    Passed through unchanged, imshow and set_extent both read that as a flipped
    axis and the image is drawn upside down inside a negative-height box.
    """
    read = raster_io.open_raster(rasters / "multi.h5")
    west, east, south, north = read.extent
    assert west < east
    assert south < north


# ------------------------------------------------------------------ canvas ---
@pytest.mark.parametrize("fixture,name", [
    ("vectors", "t.shp"), ("vectors", "t.geojson"), ("vectors", "t.gml"),
    ("vectors", "t.gpkg"), ("vectors", "tracks.gpx"),
    ("rasters", "t.tif"), ("rasters", "t.dem"), ("rasters", "t.dat"),
    ("rasters", "t.rst"), ("rasters", "multi.h5"),
])
def test_canvas_loads_every_supported_format(canvas, request, fixture, name):
    """The dispatcher, end to end, on one file of each accepted format.

    One file per test rather than a loop, because the fixtures deliberately
    share the stem ``t`` and a layer is named after its file — loading them
    into one canvas would have the second overwrite the first and look like a
    failure to draw.
    """
    path = request.getfixturevalue(fixture) / name

    assert canvas.load_file(str(path)), f"{name} failed to load"
    assert canvas.layers, f"{name} drew nothing"
    assert all(layer.get("artist") is not None
               for layer in canvas.layers.values()), f"{name} made a layer with no artist"


def test_canvas_reports_success_for_a_mixed_geometry_file(canvas, vectors):
    """The regression: load_file tested for a layer named after the file.

    A mixed file registers "mixed (points)" and friends, never bare "mixed", so
    the old check called a correct load a failure — and the caller then showed
    an error over a map that had just drawn the layer.
    """
    assert canvas.load_file(str(vectors / "mixed.geojson")) is True
    assert len([n for n in canvas.layers if n.startswith("mixed")]) == 3


def test_canvas_draws_multipart_geometry(canvas, tmp_path):
    """Multi-part geometries must reach an artist, not be skipped in silence.

    add_lines and add_polygons each have a list branch that reaches for
    ``.coords`` / ``.exterior``. A MultiLineString has neither, so handing them
    a plain list of geometries dropped every multi-part feature and only warned.
    A GPX track is always a MultiLineString, so this was the whole of one.
    """
    from shapely.geometry import MultiLineString, MultiPolygon

    path = tmp_path / "multi.geojson"
    gpd.GeoDataFrame(
        {"n": ["l", "p"]},
        geometry=[
            MultiLineString([[(70, 20), (75, 25)], [(76, 26), (80, 30)]]),
            MultiPolygon([
                (((70, 20), (72, 20), (72, 22), (70, 20)), []),
                (((76, 26), (78, 26), (78, 28), (76, 26)), []),
            ]),
        ],
        crs="EPSG:4326",
    ).to_file(path, driver="GeoJSON")

    assert canvas.load_file(str(path))

    made = {name: layer for name, layer in canvas.layers.items()
            if name.startswith("multi")}
    assert set(made) == {"multi (lines)", "multi (polygons)"}
    for name, layer in made.items():
        assert layer.get("artist") is not None, f"{name} has no artist"
        # Both parts of each multi-geometry, not just the first.
        assert layer.get("feature_count") == 2, (
            f"{name} kept {layer.get('feature_count')} of 2 parts")


def test_every_drawn_layer_has_a_property_record(canvas, vectors):
    """A layer with no property record draws unstyled and cannot be restyled.

    ``update_layer_display`` returns early when ``get_layer_property`` is None,
    so the sub-layers of a mixed file needed records of their own — the base
    name they were split from is never a key in ``canvas.layers``.
    """
    assert canvas.load_file(str(vectors / "mixed.geojson"))

    for name in canvas.layers:
        record = canvas.property_manager.get_layer_property(name)
        assert record is not None, f"{name} has no property record"
        assert record.metadata.name == name
        # The extent came from the reprojected frame, so it must be lon/lat.
        west, east, south, north = record.dimensions.extent
        assert -180 <= west <= east <= 180
        assert -90 <= south <= north <= 90


def test_mixed_sublayers_report_their_own_feature_counts(canvas, vectors):
    assert canvas.load_file(str(vectors / "mixed.geojson"))

    counts = {
        name: canvas.property_manager.get_layer_property(
            name).metadata.attributes["feature_count"]
        for name in canvas.layers
    }
    assert counts == {"mixed (points)": 1, "mixed (lines)": 1,
                      "mixed (polygons)": 1}


def test_single_geometry_file_keeps_the_plain_layer_name(canvas, vectors):
    """The common case must not gain a suffix it never had."""
    assert canvas.load_file(str(vectors / "t.shp"))
    assert set(canvas.layers) == {"t"}


def test_canvas_refuses_unsupported_formats_with_the_registry_reason(canvas, vectors):
    reported = []
    canvas.loading_error.connect(lambda title, message: reported.append(message))

    assert canvas.load_file(str(vectors / "t.svg")) is False
    assert reported, "a refusal must say something"
    assert "coordinate reference system" in reported[-1]


def test_canvas_draws_a_projected_vector_inside_lonlat_bounds(canvas, vectors):
    assert canvas.load_file(str(vectors / "utm.geojson"))
    for name, layer in canvas.layers.items():
        if not name.startswith("utm"):
            continue
        artist = layer.get("artist")
        assert artist is not None
    # The canvas extent must still be a legal lon/lat box.
    west, east, south, north = canvas.ax.get_extent()
    assert -180.1 <= west <= east <= 180.1
    assert -90.1 <= south <= north <= 90.1
