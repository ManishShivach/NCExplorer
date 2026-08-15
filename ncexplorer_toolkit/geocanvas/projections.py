"""The map projections the canvas can be drawn in.

Which projections exist, and what each one needs to be told, follows Basemap's
"Setting up the map" page. That page is the *specification* here and nothing
more: ``mpl_toolkits.basemap`` is archived and its axes cannot host the Cartopy
``GeoAxes`` this application is built on, so every entry below is a plain
:mod:`cartopy.crs` class and Basemap's keyword names survive only as the
mapping each spec documents:

===================  ==========================================================
Basemap              Cartopy
===================  ==========================================================
``lon_0``            ``central_longitude``
``lat_0``            ``central_latitude``
``lat_1``/``lat_2``  ``standard_parallels``
``lat_ts``           ``latitude_true_scale``
``boundinglat``      the latitude the polar cap is cut off at — an *extent*,
                     not a CRS parameter, so it is applied to the axes
corner lat/lons      the extent, likewise set on the axes rather than the CRS
===================  ==========================================================

Nothing is ever asked of the user. Every parameter is derived from the extent
the loaded data already covers, which is exactly how Basemap's own examples
arrive at theirs — they are written for a region and their numbers describe it.

Three properties of a projection matter to the canvas beyond its CRS, and each
one is a fact about the projection rather than a preference:

* **global_only** — Robinson and Mollweide draw the whole world and nothing
  else. A rectangular extent means nothing to them, so the axes is set global.
* **polar** — the polar stereographics are a circle cut at one latitude, not a
  box. They need a circular boundary path and that cutoff.
* **lat_limits** — Mercator reaches infinity before it reaches either pole, so
  the box it can be given is narrower than the world.

There is no Qt import here on purpose: both the canvas and the toolbar read
this registry, and it stays testable with no display.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

import cartopy.crs as ccrs
import numpy as np
from matplotlib.path import Path

from . import cartopy_polygon_fix

logger = logging.getLogger(__name__)

# Repair cartopy's boundary-polygon handling before any CRS built here is asked
# to project anything. This module is the first thing in the package to bind
# cartopy and every drawing path reaches the registry, so it is the one place
# the fix is guaranteed to be in by the time a FeatureArtist draws. The package
# __init__ would be the obvious home, but it promises to stay importable
# without loading cartopy at all, and this needs cartopy loaded to probe it.
cartopy_polygon_fix.apply()

#: What the canvas starts in, and what everything falls back to.
DEFAULT_PROJECTION = "PlateCarree"

#: The whole world in lon/lat, and the stand-in for an unusable extent.
WORLD = (-180.0, 180.0, -90.0, 90.0)

#: Fraction of the latitude span the conic standard parallels are inset by.
#: Basemap's ``lcc`` and ``aea`` examples both put ``lat_1``/``lat_2`` inside
#: the band being mapped rather than at its edges; the one-sixth rule is the
#: usual way of choosing where.
CONIC_INSET = 1.0 / 6.0

#: Standard parallels for a conic whose extent yields none that work — see
#: :func:`_conic_parallels`. Cartopy's own ``AlbersEqualArea`` default.
CONIC_FALLBACK = (20.0, 50.0)

#: Two parallels this close to symmetric about the equator make the cone
#: degenerate: its apex runs off to infinity and the CRS cannot be drawn.
CONIC_MIN_ASYMMETRY = 1.0

#: Latitude past which a conic's map has to be cut off, on the side away from
#: its standard parallels. Cartopy's ``LambertConformal`` default, signed here
#: to follow the hemisphere the parallels actually landed in.
CONIC_CUTOFF = 30.0

#: The band Mercator can usefully cover, matching Cartopy's own defaults.
MERCATOR_LIMITS = (-80.0, 84.0)

#: How far inside a projection's latitude limit an extent bound is pulled.
#: Cartopy works the axes box out by intersecting the requested extent with the
#: projection's boundary, and a bound sitting exactly *on* that boundary
#: intersects it at infinity — which arrives as "Axis limits cannot be NaN or
#: Inf" from inside ``set_extent``. A tenth of a degree is invisible on the map
#: and is the difference between a drawn map and a traceback.
LIMIT_MARGIN = 0.1

#: Mercator's latitude of true scale is clamped here: nearer the pole than
#: this the scale factor collapses and the map becomes unreadable.
TRUE_SCALE_LIMIT = 75.0

#: How far from the pole a polar cap may be cut — Basemap's ``boundinglat``.
#: The band runs from the equator (the widest cap worth drawing) to a degree
#: short of the pole, which is as far as zooming in can usefully go.
POLAR_CAP_LIMIT = 89.0

#: Spans at or above which a whole-world projection is drawn as the globe
#: rather than as a box. Deliberately short of a full 360/180: the extent is
#: fitted to the canvas' aspect ratio before it gets here, which trims whichever
#: dimension is the larger, and a view a few degrees off full is still the
#: globe as far as anyone looking at it is concerned.
GLOBAL_SPAN_LON = 350.0
GLOBAL_SPAN_LAT = 175.0

#: Latitudes a *zoomed* box on a whole-world projection is kept inside. The
#: globe itself is drawn with set_global, which has no such trouble, but an
#: arbitrary box reaching a pole makes Mollweide's boundary intersection
#: degenerate and the projected box collapses.
GLOBAL_BOX_LIMITS = (-89.0, 89.0)

#: The circular map boundary the polar stereographics are drawn inside, in axes
#: coordinates. Built once: it is a constant, and the canvas re-applies it on
#: every axes rebuild.
_theta = np.linspace(0, 2 * np.pi, 180)
CIRCULAR_BOUNDARY = Path(
    np.vstack([np.sin(_theta), np.cos(_theta)]).T * 0.5 + [0.5, 0.5]
)
del _theta


# ----------------------------------------------------------------------
# Deriving parameters from the mapped extent
# ----------------------------------------------------------------------
def clean_extent(extent) -> tuple[float, float, float, float]:
    """A usable ``(west, east, south, north)`` out of whatever was passed.

    Anything malformed — the wrong length, non-numeric, inverted, or degenerate
    — becomes the whole world. Deriving a projection is not the place to raise:
    the caller is mid-way through rebuilding the map.
    """
    try:
        west, east, south, north = (float(value) for value in extent)
    except (TypeError, ValueError):
        return WORLD

    if not all(np.isfinite(value) for value in (west, east, south, north)):
        return WORLD
    if east <= west or north <= south:
        return WORLD

    return (max(-180.0, west), min(180.0, east),
            max(-90.0, south), min(90.0, north))


def _centre(extent) -> tuple[float, float]:
    """Basemap's ``lon_0``/``lat_0``: the middle of the mapped region."""
    west, east, south, north = clean_extent(extent)
    return (west + east) / 2.0, (south + north) / 2.0


def _conic_parallels(extent) -> tuple[float, float]:
    """Basemap's ``lat_1``/``lat_2`` for a conic, from the latitude range.

    Inset by :data:`CONIC_INSET` at each end, which is where the standard
    parallels of a regional conic belong. Parallels symmetric about the equator
    are the one case this cannot produce: the cone they describe degenerates
    into a cylinder with its apex at infinity, and Cartopy builds a CRS that
    then fails to draw. That is not a corner case either — it is precisely what
    a whole-world extent gives — so those fall back to a northern pair, on the
    grounds that a conic cannot show the whole globe under any parameters and
    the honest thing is to show one hemisphere properly.
    """
    _west, _east, south, north = clean_extent(extent)
    span = north - south
    lat_1 = south + span * CONIC_INSET
    lat_2 = north - span * CONIC_INSET

    if abs(lat_1 + lat_2) < CONIC_MIN_ASYMMETRY:
        _lon_0, lat_0 = _centre(extent)
        sign = -1.0 if lat_0 < 0 else 1.0
        return sign * CONIC_FALLBACK[0], sign * CONIC_FALLBACK[1]

    return lat_1, lat_2


def _conic_cutoff(lat_1: float, lat_2: float) -> float:
    """Where a conic's map stops, on the far side from its parallels.

    Opposite the cone's pole the projection runs to infinity, so Cartopy needs
    to be told where to stop drawing. Its default assumes northern parallels; a
    southern conic given that default is silently cropped at 30°S, losing the
    bottom of the very region it was set up for.
    """
    return -CONIC_CUTOFF if (lat_1 + lat_2) >= 0 else CONIC_CUTOFF


def _true_scale(extent) -> float:
    """Basemap's ``lat_ts`` for Mercator: where the scale is exact.

    The middle of the mapped band, which is the latitude a single-scale map is
    least wrong about, clamped short of the poles where ``cos(lat_ts)`` — and
    with it the whole map — collapses.
    """
    _lon_0, lat_0 = _centre(extent)
    return max(-TRUE_SCALE_LIMIT, min(TRUE_SCALE_LIMIT, lat_0))


def _bounding_lat(extent, pole: int) -> float:
    """Basemap's ``boundinglat``: the latitude circle the cap is cut at.

    Taken from the edge of the view facing the equator, so a cap over an Arctic
    dataset is the Arctic rather than an arbitrary circle — and so that zooming,
    which moves that edge, tightens and loosens the cap. A view reaching past
    the equator gives the hemisphere: beyond that a polar stereographic stops
    being readable long before it stops being defined.
    """
    _west, _east, south, north = clean_extent(extent)
    if pole > 0:
        return max(0.0, min(POLAR_CAP_LIMIT, south))
    return min(0.0, max(-POLAR_CAP_LIMIT, north))


# ----------------------------------------------------------------------
# The registry
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class ProjectionSpec:
    """One selectable projection: how to build it, and how it behaves."""

    name: str
    #: One line for the selector's tooltip, in Basemap's own terms.
    summary: str
    #: ``extent -> {Basemap parameter name: value}``. Kept in Basemap's
    #: vocabulary so the derivation can be read against the page it came from.
    derive: Callable[[tuple], dict]
    #: ``derived parameters -> cartopy CRS``.
    factory: Callable[[dict], ccrs.CRS]
    #: ``derived parameters -> (low, high)``: the latitude band the axes can be
    #: given. A function rather than a constant because a conic's usable band
    #: depends on where its own standard parallels ended up.
    limits: Callable[[dict], tuple[float, float]] = lambda _params: (-90.0, 90.0)
    #: Draws the whole world and only the whole world; no box applies.
    global_only: bool = False
    #: ``+1`` north-polar, ``-1`` south-polar, ``0`` not a polar cap.
    pole: int = 0

    @property
    def polar(self) -> bool:
        return self.pole != 0

    @property
    def fixed_shape(self) -> bool:
        """True when the projection's outline is its own, not the canvas box.

        A globe and a polar cap have a shape of their own that stretching to
        fill a widget would simply falsify, and neither has a rectangular
        extent for a drag to move. The canvas reads this for both decisions.
        """
        return self.global_only or self.polar


def _plate_carree(_params):
    return ccrs.PlateCarree()


def _mercator(params):
    return ccrs.Mercator(
        central_longitude=params["lon_0"],
        latitude_true_scale=params["lat_ts"],
        min_latitude=MERCATOR_LIMITS[0],
        max_latitude=MERCATOR_LIMITS[1],
    )


def _robinson(params):
    return ccrs.Robinson(central_longitude=params["lon_0"])


def _mollweide(params):
    return ccrs.Mollweide(central_longitude=params["lon_0"])


def _lambert_conformal(params):
    return ccrs.LambertConformal(
        central_longitude=params["lon_0"],
        central_latitude=params["lat_0"],
        standard_parallels=(params["lat_1"], params["lat_2"]),
        cutoff=_conic_cutoff(params["lat_1"], params["lat_2"]),
    )


def _albers_equal_area(params):
    return ccrs.AlbersEqualArea(
        central_longitude=params["lon_0"],
        central_latitude=params["lat_0"],
        standard_parallels=(params["lat_1"], params["lat_2"]),
    )


def _north_polar_stereo(params):
    return ccrs.NorthPolarStereo(central_longitude=params["lon_0"])


def _south_polar_stereo(params):
    return ccrs.SouthPolarStereo(central_longitude=params["lon_0"])


def _no_params(_extent):
    """Basemap's ``cyl`` takes none: its region is the corners and nothing else.

    Leaving ``lon_0`` off is also what makes the default a true no-op — the
    projection every extent, project file and existing test already assumes.
    """
    return {}


def _centre_only(extent):
    lon_0, _lat_0 = _centre(extent)
    return {"lon_0": lon_0}


def _mercator_params(extent):
    lon_0, _lat_0 = _centre(extent)
    return {"lon_0": lon_0, "lat_ts": _true_scale(extent)}


def _conic_params(extent):
    lon_0, lat_0 = _centre(extent)
    lat_1, lat_2 = _conic_parallels(extent)
    return {"lon_0": lon_0, "lat_0": lat_0, "lat_1": lat_1, "lat_2": lat_2}


def _mercator_limits(_params):
    return MERCATOR_LIMITS


def _conic_limits(params):
    """The half of the world a conic can draw: everything up to its cutoff.

    Opposite the cone's own pole the projection diverges, so a conic asked for
    a whole-world extent produces either a traceback (Lambert Conformal, which
    takes a cutoff and refuses to intersect past it) or a silently nonsensical
    box (Albers, which takes none and lets the diverging pole dominate). Both
    want the same answer: keep to the side the standard parallels are on.
    """
    cutoff = _conic_cutoff(params["lat_1"], params["lat_2"])
    return (cutoff, 90.0) if cutoff < 0 else (-90.0, cutoff)


def _north_polar_params(extent):
    lon_0, _lat_0 = _centre(extent)
    return {"lon_0": lon_0, "boundinglat": _bounding_lat(extent, 1)}


def _south_polar_params(extent):
    lon_0, _lat_0 = _centre(extent)
    return {"lon_0": lon_0, "boundinglat": _bounding_lat(extent, -1)}


#: Every projection the canvas offers, in the order the selector shows them:
#: the default first, then the two other rectangular ones, the two globes, the
#: two conics and the two caps.
REGISTRY: dict[str, ProjectionSpec] = {
    spec.name: spec for spec in (
        ProjectionSpec(
            name="PlateCarree",
            summary="Equidistant cylindrical — plain lon/lat, the default",
            derive=_no_params,
            factory=_plate_carree,
        ),
        ProjectionSpec(
            name="Mercator",
            summary="Conformal cylindrical; heavy distortion at high latitudes",
            derive=_mercator_params,
            factory=_mercator,
            limits=_mercator_limits,
        ),
        ProjectionSpec(
            name="Robinson",
            summary="Whole-world compromise projection",
            derive=_centre_only,
            factory=_robinson,
            global_only=True,
        ),
        ProjectionSpec(
            name="Mollweide",
            summary="Whole-world equal-area ellipse",
            derive=_centre_only,
            factory=_mollweide,
            global_only=True,
        ),
        ProjectionSpec(
            name="LambertConformal",
            summary="Conformal conic; standard parallels from the data's latitudes",
            derive=_conic_params,
            factory=_lambert_conformal,
            limits=_conic_limits,
        ),
        ProjectionSpec(
            name="AlbersEqualArea",
            summary="Equal-area conic; standard parallels from the data's latitudes",
            derive=_conic_params,
            factory=_albers_equal_area,
            limits=_conic_limits,
        ),
        ProjectionSpec(
            name="NorthPolarStereo",
            summary="North polar cap, cut at the data's southern edge",
            derive=_north_polar_params,
            factory=_north_polar_stereo,
            pole=1,
        ),
        ProjectionSpec(
            name="SouthPolarStereo",
            summary="South polar cap, cut at the data's northern edge",
            derive=_south_polar_params,
            factory=_south_polar_stereo,
            pole=-1,
        ),
    )
}

#: Display names in selector order. Both the canvas and the toolbar read this;
#: neither keeps a list of its own.
PROJECTION_CHOICES: tuple[str, ...] = tuple(REGISTRY)


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------
def spec(name: str) -> ProjectionSpec | None:
    """The registry entry for a display name, or None if there is not one."""
    return REGISTRY.get(str(name))


def derive(name: str, extent=WORLD) -> dict:
    """The parameters ``name`` would be built with for ``extent``.

    In Basemap's vocabulary — ``lon_0``, ``lat_1``, ``boundinglat`` and so on.
    An unknown name derives the default's parameters, matching :func:`build`.
    """
    entry = spec(name) or REGISTRY[DEFAULT_PROJECTION]
    return entry.derive(extent)


def build(name: str, extent=WORLD) -> tuple[ccrs.CRS, str]:
    """``(crs, name actually used)`` for a display name and a lon/lat extent.

    Never raises. An unrecognised name, or a CRS that will not construct with
    the parameters this extent implies, falls back to PlateCarree with a logged
    warning — the same way an unavailable basemap provider degrades instead of
    taking the window down with it. The returned name is what was really built,
    so the caller can record and report it rather than claiming otherwise.
    """
    entry = spec(name)
    if entry is None:
        logger.warning("Unknown projection '%s'; falling back to %s",
                       name, DEFAULT_PROJECTION)
        entry = REGISTRY[DEFAULT_PROJECTION]

    try:
        return entry.factory(entry.derive(extent)), entry.name
    except Exception as exc:
        logger.warning("Could not build the %s projection for extent %s: %s",
                       entry.name, extent, exc, exc_info=True)

    if entry.name == DEFAULT_PROJECTION:
        # The fallback itself failed, which leaves only a bare default. Cartopy
        # constructing PlateCarree() at all is not something we can degrade past.
        return ccrs.PlateCarree(), DEFAULT_PROJECTION

    default = REGISTRY[DEFAULT_PROJECTION]
    return default.factory(default.derive(extent)), DEFAULT_PROJECTION


def is_whole_world(extent) -> bool:
    """True when the view is the whole globe, near enough to be drawn as one."""
    west, east, south, north = clean_extent(extent)
    return (east - west) >= GLOBAL_SPAN_LON or (north - south) >= GLOBAL_SPAN_LAT


def axes_extent(name: str, extent, params: dict | None = None):
    """The lon/lat box to give the axes, or None when it must be set global.

    The distinctions the caller cannot make for itself:

    * A whole-world projection asked for the whole world has no rectangular
      extent that would mean anything, and is drawn as the globe. Asked for
      less, it takes the box — so it zooms like any other projection. Only the
      poles stay out of reach of a box, because one that touches a pole makes
      Mollweide's boundary intersection collapse.
    * A polar cap is always the full circle, cut at the latitude the view's
      equator-facing edge reaches. Zooming moves that edge, and that is what
      tightens and loosens the cap.
    * Everything else is the requested box, pulled inside the latitudes the
      projection can actually reach.

    ``params`` are the parameters the live CRS was built with. Passing them
    matters for the conics, whose usable half of the world follows the cutoff
    baked into that CRS rather than whatever the current view would imply.
    """
    entry = spec(name)
    if entry is None:
        return None

    west, east, south, north = clean_extent(extent)

    if entry.global_only:
        if is_whole_world(extent):
            return None
        low, high = GLOBAL_BOX_LIMITS
    elif entry.polar:
        cut = _bounding_lat(extent, entry.pole)
        if entry.pole > 0:
            return -180.0, 180.0, cut + LIMIT_MARGIN, 90.0
        return -180.0, 180.0, -90.0, cut - LIMIT_MARGIN
    else:
        low, high = entry.limits(params if params is not None else entry.derive(extent))
        # Only a real pole may be asked for exactly; every other limit is a
        # boundary the extent has to stop just short of. See LIMIT_MARGIN.
        if low > -90.0:
            low += LIMIT_MARGIN
        if high < 90.0:
            high -= LIMIT_MARGIN

    south = max(low, min(high, south))
    north = max(low, min(high, north))
    if north <= south:
        south, north = low, high
    return west, east, south, north
