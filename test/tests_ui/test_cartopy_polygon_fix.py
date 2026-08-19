# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""The cartopy boundary-polygon repair, and the two warnings beside it.

The defect being pinned here is SciTools/cartopy#2176: projecting a polygon
whose outline runs *along* the projection boundary — an ocean wrapping a pole
is the everyday case — leaves cartopy intersecting the inverted interior with
the boundary and getting back a ``GeometryCollection`` of real polygons plus
the zero-width seam where the two coincide. It appends that collection whole
and hands it to ``MultiPolygon(...)``, which subscripts it and raises. Nothing
catches it: matplotlib's callback machinery prints the traceback and carries
on, so the app survives but the frame never lands and the basemap drops out
mid-gesture. Wheel-zoom and drag-pan both redraw, so it repeats for as long as
the pointer moves.

Two things therefore have to hold, and the second matters as much as the first:
the failing case must come back as a polygon, and every case that already
worked must come back *unchanged*. A repair that quietly emptied a geometry
would leave a clean terminal and a map missing its coastlines.
"""

import warnings

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import pytest
import shapely
import shapely.geometry as sgeom

# Importing the registry is what installs the repair — see the call at the top
# of projections.py. Nothing here applies it by hand, so this import failing to
# patch is itself a test failure.
from ncexplorer_toolkit.geocanvas import cartopy_polygon_fix, projections

#: The patched method, captured once. One test swaps cartopy's own back in to
#: compare the two, and has to be able to restore this afterwards — reading it
#: off the class at that point would only find whatever is installed then.
wrapper_under_test = ccrs.Projection._rings_to_multi_polygon

#: The projections a user can zoom and pan in. The defect is in code every one
#: of them shares, so the repair is checked against all of them rather than
#: against the polar caps where the shipped Natural Earth data happens to reach
#: it.
EVERY_PROJECTION = (
    "PlateCarree", "Mercator", "Robinson", "Mollweide",
    "LambertConformal", "AlbersEqualArea", "NorthPolarStereo", "SouthPolarStereo",
)

WORLD = [-180.0, 180.0, -90.0, 90.0]


def boundary_hugging_rings(projection):
    """Interior rings whose inversion lands on the projection boundary.

    Two clockwise rings — clockwise is what makes cartopy file them as interior
    — covering the domain apart from a strip at one edge. Inverting them leaves
    that strip, which is a real polygon, joined at a point to the seam between
    the two rings, which is a line. That mixture is the whole defect.
    """
    west, south, east, north = projection.domain.bounds
    middle = (west + east) / 2.0
    inset = east - (east - west) * 0.05

    def clockwise(coords):
        ring = sgeom.LinearRing(coords)
        return sgeom.LinearRing(list(ring.coords)[::-1]) if ring.is_ccw else ring

    return [
        clockwise([(west, south), (middle, south), (middle, north), (west, north)]),
        clockwise([(middle, south), (inset, south), (inset, north), (middle, north)]),
    ]


# ----------------------------------------------------------------------
# The repair itself
# ----------------------------------------------------------------------
def test_the_repair_is_installed_by_importing_the_registry():
    """Nothing in the app should have to remember to switch this on."""
    assert cartopy_polygon_fix._applied, "the fix never ran"
    assert getattr(
        ccrs.Projection._rings_to_multi_polygon, "_ncexplorer_patch", False
    ), "cartopy was left unpatched despite still having the defect"


@pytest.mark.parametrize("name", EVERY_PROJECTION)
def test_a_boundary_hugging_polygon_no_longer_raises(name):
    """The exception the user saw, in every projection that can show it.

    Without the repair this is
    ``TypeError: 'GeometryCollection' object is not subscriptable``.
    """
    crs, _used = projections.build(name, WORLD)
    result = crs._rings_to_multi_polygon(boundary_hugging_rings(crs), True)

    assert isinstance(result, sgeom.MultiPolygon)
    assert result.is_valid


@pytest.mark.parametrize("name", EVERY_PROJECTION)
def test_the_recovered_geometry_is_the_area_and_not_an_empty_stub(name):
    """Dropping the frame would also stop the traceback. That is not a fix.

    Inverting those rings against the boundary means, in plain terms, "the part
    of the domain they do not cover" — so that is what the answer has to be,
    computed here independently of anything cartopy does. Only the seam is
    allowed to go missing, and a seam has no area. A repair that returned an
    empty geometry would pass the test above and still lose the coastlines.
    """
    crs, _used = projections.build(name, WORLD)
    rings = boundary_hugging_rings(crs)
    covered = shapely.union_all([sgeom.Polygon(ring) for ring in rings])
    expected = crs.domain.difference(covered).area

    result = crs._rings_to_multi_polygon(rings, True)

    assert expected > 0, "the case under test has no area to begin with"
    assert result.area == pytest.approx(expected, rel=1e-6)


def test_the_probe_reports_the_defect_gone_once_patched():
    """The guard that makes this module retire itself.

    :func:`apply` runs the failing case before touching anything, so a cartopy
    that has fixed #2176 upstream is left alone. Asking the probe again after
    patching is the same question, and it has to answer no.
    """
    assert cartopy_polygon_fix._defect_present(ccrs.Projection) is False


def test_applying_twice_does_not_stack_a_second_wrapper():
    patched_once = ccrs.Projection._rings_to_multi_polygon
    cartopy_polygon_fix.apply()
    assert ccrs.Projection._rings_to_multi_polygon is patched_once


def test_unrelated_type_errors_still_reach_the_caller():
    """The repair catches one sentence, not the draw path.

    A blanket ``except TypeError`` here would swallow real breakage in
    everything cartopy calls while projecting, and the symptom would be a map
    that silently stops updating rather than a traceback anyone could act on.
    """
    class DomainRaises:
        """Stands in for a projection that fails for some *other* reason."""

        @property
        def domain(self):
            raise TypeError("nothing to do with subscripting")

    wrapper = ccrs.Projection._rings_to_multi_polygon
    rings = boundary_hugging_rings(ccrs.PlateCarree())

    with pytest.raises(TypeError, match="nothing to do with subscripting"):
        wrapper(DomainRaises(), rings, True)


@pytest.mark.parametrize("name", EVERY_PROJECTION)
def test_ordinary_coastlines_project_exactly_as_before(name):
    """The repair must be inert on everything that already worked.

    Not "still looks reasonable" — identical. Cartopy's own method is put back
    for the comparison, so this asks the only question worth asking: does a
    patched draw and an unpatched one produce the same geometry, coordinate for
    coordinate? The repair runs only after cartopy has already raised, so any
    difference at all would mean it had started firing when it should not.
    """
    crs, _used = projections.build(name, WORLD)
    source = ccrs.PlateCarree()
    land = list(cfeature.LAND.geometries())[:20]

    patched = [crs.project_geometry(geometry, source).wkt for geometry in land]

    ccrs.Projection._rings_to_multi_polygon = cartopy_polygon_fix._original
    try:
        unpatched = [crs.project_geometry(geometry, source).wkt for geometry in land]
    finally:
        ccrs.Projection._rings_to_multi_polygon = wrapper_under_test

    assert patched == unpatched


# ----------------------------------------------------------------------
# The two warnings that came with it
# ----------------------------------------------------------------------
def test_the_theme_features_do_not_warn_about_facecolour(canvas):
    """COASTLINE and BORDERS are strokes, and cartopy defines them as such.

    Both are built with ``facecolor='never'``; ``color=`` sets face and edge
    together, so asking for a fill they refuse warned once per feature on every
    single draw.
    """
    for theme in ("light", "dark"):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            canvas.apply_theme(theme)
            canvas.draw()

        offenders = [w for w in caught if "facecolor will have no effect" in str(w.message)]
        assert not offenders, f"{theme} theme warned {len(offenders)} time(s)"


def test_reading_a_netcdf_does_not_warn_about_ds_dims(canvas, nc_standard):
    """``ds.dims`` is becoming a set of names, so ``.keys()`` is on its way out.

    ``ds.sizes`` is the name→length mapping the lookup always wanted.
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        canvas.load_netcdf(nc_standard)

    offenders = [
        w for w in caught
        if issubclass(w.category, FutureWarning) and "dims" in str(w.message)
    ]
    assert not offenders, str(offenders[0].message)
