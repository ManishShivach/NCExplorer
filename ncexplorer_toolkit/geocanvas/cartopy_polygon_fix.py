"""A compatibility shim for one defect in ``cartopy.crs``.

**What upstream does wrong.** ``Projection._rings_to_multi_polygon`` turns the
rings of a projected polygon back into a ``MultiPolygon``. Rings that came out
"backwards" are interior rings, and any that no exterior ring swallowed have to
be inverted against the projection's own boundary — the last branch of that
method, ``cartopy/crs.py`` around lines 1258-1268 in 0.25.0::

    polygon = boundary_poly.intersection(polygon)

    if not polygon.is_empty:
        if isinstance(polygon, sgeom.MultiPolygon):
            polygon_bits.extend(polygon.geoms)
        else:
            polygon_bits.append(polygon)

That intersection is not guaranteed to be polygonal. When the inverted interior
runs along the projection boundary rather than merely crossing it, Shapely
returns the mixed result it honestly is: a ``GeometryCollection`` of the real
polygons *plus* the zero-width seam where the two outlines coincide, as a
``LineString`` or a ``Point``. Where the interior covers the domain completely
it returns a bare ``MultiLineString`` — the boundary and nothing else.

The ``else`` above appends whichever of those it got, verbatim, and four lines
later ``sgeom.MultiPolygon(polygon_bits)`` reaches ``shell = ob[0]`` on it and
raises ``TypeError: 'GeometryCollection' object is not subscriptable``.

The defect is a gap rather than an oversight in principle: the ``make_valid``
branch thirty lines above (0.25.0 lines ~1226-1239) already unpacks a
``GeometryCollection`` into its ``Polygon``/``MultiPolygon`` members and drops
everything else with the comment "make_valid may produce some linestrings.
Ignore these". This branch was simply never given the same treatment.

**Why we carry a shim rather than a version pin.** It is
https://github.com/SciTools/cartopy/issues/2176, open since May 2023, and the
same unguarded ``else`` is still on cartopy ``main`` as of this writing — there
is no release to upgrade to. :func:`apply` therefore probes for the defect
before touching anything, so the day a fixed cartopy arrives this module
becomes a no-op on its own rather than needing to be remembered and removed.

**What the repair does.** It keeps upstream's own answer from the branch above:
a filled polygon is made of areas, so the polygonal parts of that collection are
the result and the seam contributes nothing. Dropping it is not an
approximation — a ``LineString`` has no area to lose.
"""

from __future__ import annotations

import contextlib
import logging

logger = logging.getLogger(__name__)

#: Set once :func:`apply` has run, whatever it decided. The patch must never be
#: installed twice: the second wrapper would call the first as its "original"
#: and the retry would run the whole projection a second time for nothing.
_applied = False

#: True when :func:`apply` actually installed the wrapper. Read by the tests.
_patched = False

#: Cartopy's own method, kept so the tests can put it back and check that a
#: patched draw and an unpatched one agree wherever the unpatched one works.
_original = None


def _polygonal_parts(geometry) -> list:
    """The ``Polygon`` members of ``geometry``, however deeply it is nested.

    A ``GeometryCollection`` can hold further collections, so this recurses.
    Lines and points come back as nothing at all, which is the whole point.
    """
    import shapely.geometry as sgeom

    if geometry.is_empty:
        return []
    if isinstance(geometry, sgeom.Polygon):
        return [geometry]
    if isinstance(geometry, (sgeom.MultiPolygon, sgeom.GeometryCollection)):
        parts = []
        for member in geometry.geoms:
            parts.extend(_polygonal_parts(member))
        return parts
    return []


def _keep_polygons(items):
    """``polygon_bits`` with every non-polygonal geometry taken back out.

    ``polygon_bits`` is a deliberately mixed list: most entries are the
    ``(shell coords, [hole coords])`` tuples the exterior loop builds, and only
    the inverted-interior branch appends real geometry objects. The tuples are
    passed through untouched — they are what the constructor wants — and only
    the geometry objects are filtered.
    """
    from shapely.geometry.base import BaseGeometry

    kept = []
    for item in items:
        if isinstance(item, BaseGeometry):
            kept.extend(_polygonal_parts(item))
        else:
            kept.append(item)
    return kept


@contextlib.contextmanager
def _polygonal_multipolygon():
    """Make ``sgeom.MultiPolygon`` inside ``cartopy.crs`` skip non-polygons.

    ``cartopy.crs`` binds Shapely once, at import, as the module global
    ``sgeom``; ``_rings_to_multi_polygon`` looks that name up afresh on every
    call. Swapping it for a proxy is therefore enough to reach the one
    constructor call that matters, without editing site-packages and without
    changing ``shapely`` for anything else in the process.

    The swap is held only for the retry of a call that has *already* raised, so
    the normal draw path runs entirely unmodified — this is a repair, not an
    interception. That also keeps the window in which the global differs down
    to a single synchronous call, which matters because the alias is process-
    wide: rendering is on the Qt main thread here (the canvas' thread pool
    fetches basemap tiles and never projects geometry), so nothing else can be
    inside ``cartopy.crs`` while it is swapped.
    """
    import shapely.geometry as sgeom

    import cartopy.crs

    class _MultiPolygonMeta(type):
        """Filters construction while leaving ``isinstance`` telling the truth.

        The stand-in cannot simply be a function: ``_rings_to_multi_polygon``
        spends ``sgeom.MultiPolygon`` on ``isinstance`` checks as well as on
        construction, and ``isinstance`` demands a type. Nor can it be a
        subclass — then ``isinstance(a_real_multipolygon, ...)`` goes False, and
        the ``make_valid`` branch just above the defect would take that as "not
        polygonal" and quietly drop a legitimate interior polygon. Answering
        both questions separately is the only stand-in that changes nothing but
        the one thing it is here to change.
        """

        def __instancecheck__(cls, instance):
            return isinstance(instance, sgeom.MultiPolygon)

        def __subclasscheck__(cls, subclass):
            return issubclass(subclass, sgeom.MultiPolygon)

        def __call__(cls, polygons=None):
            if polygons is None:
                return sgeom.MultiPolygon()
            return sgeom.MultiPolygon(_keep_polygons(polygons))

    tolerant_multipolygon = _MultiPolygonMeta("MultiPolygon", (), {})

    class _PolygonalGeometry:
        """``shapely.geometry`` with that one name replaced."""

        MultiPolygon = tolerant_multipolygon

        def __getattr__(self, name):
            return getattr(sgeom, name)

    original = cartopy.crs.sgeom
    cartopy.crs.sgeom = _PolygonalGeometry()
    try:
        yield
    finally:
        cartopy.crs.sgeom = original


def _degenerate_rings(projection):
    """Rings that drive the branch into a mixed-geometry intersection.

    Two clockwise rings — clockwise so ``_rings_to_multi_polygon`` files them as
    interior — that between them cover the projection's domain apart from a
    strip at one edge. Inverting them leaves that strip (a real polygon) joined
    to the seam where the two rings meet (a line), which is precisely the
    ``GeometryCollection`` upstream cannot handle.
    """
    import shapely.geometry as sgeom

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


def _defect_present(projection_class) -> bool:
    """Ask this cartopy directly whether it still has the defect.

    A version comparison would be a guess — the fix is unreleased, so there is
    no number to compare against, and a distribution could carry a backport.
    Running the failing case is the only answer that cannot be wrong.
    """
    import cartopy.crs as ccrs

    projection = ccrs.PlateCarree()
    try:
        projection_class._rings_to_multi_polygon(
            projection, _degenerate_rings(projection), True
        )
    except TypeError as exc:
        if "not subscriptable" in str(exc):
            return True
        logger.warning("Unexpected TypeError probing cartopy: %s", exc)
        return False
    except Exception as exc:
        # Some cartopy we do not recognise. Leaving it alone is the safe
        # answer: a wrong patch here silently corrupts every coastline.
        logger.warning("Could not probe cartopy's polygon handling: %s", exc)
        return False
    return False


def apply() -> bool:
    """Install the repair if this cartopy needs it. True when it was installed.

    Idempotent, and safe to call from more than one module — the canvas is not
    the only thing that can put a ``FeatureArtist`` on screen.
    """
    global _applied, _patched, _original
    if _applied:
        return _patched
    _applied = True

    try:
        from cartopy.crs import Projection
    except Exception as exc:
        logger.warning("Cartopy is unavailable; polygon fix not applied: %s", exc)
        return False

    original = getattr(Projection, "_rings_to_multi_polygon", None)
    if original is None:
        # A cartopy restructured far enough that the method is gone is one this
        # shim knows nothing about.
        logger.info("cartopy has no _rings_to_multi_polygon; polygon fix skipped")
        return False

    if not _defect_present(Projection):
        logger.debug("cartopy handles boundary-hugging polygons; fix not needed")
        return False

    def _rings_to_multi_polygon(self, rings, is_ccw):
        try:
            return original(self, rings, is_ccw)
        except TypeError as exc:
            if "not subscriptable" not in str(exc):
                raise
            # Exactly the upstream defect, and only that: anything else raised
            # from inside cartopy still reaches the caller untouched.
            with _polygonal_multipolygon():
                return original(self, rings, is_ccw)

    _rings_to_multi_polygon.__doc__ = original.__doc__
    _rings_to_multi_polygon._ncexplorer_patch = True  # what the tests look for
    Projection._rings_to_multi_polygon = _rings_to_multi_polygon
    _original = original
    _patched = True
    logger.debug("Applied the cartopy boundary-polygon fix (SciTools/cartopy#2176)")
    return True
