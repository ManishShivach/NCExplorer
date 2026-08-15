"""Does this file hold what the operator is about to assume it holds?

The climate indices are the reason this module exists. Their documentation is
explicit about two traps, and both of them produce a finished file full of
wrong numbers rather than an error:

* the temperature indices read the input field as **Kelvin** while their
  threshold argument is written in **degrees Celsius**, and CDO converts
  neither. Handing ``eca_su`` a field already in °C does not fail — it counts
  every day of the year as a summer day.
* the precipitation indices want an **amount** in mm (equivalently kg m-2). A
  field carrying a rate in mm/s is ~10⁵ times too small, so every threshold
  count comes back zero. ``eca_pd``'s own documentation says to multiply by
  86400 first.

So this checks the ``units`` attribute of the variable a file carries against
what :data:`~.categories.UNIT_FAMILIES` says the slot expects, and reports the
disagreement. It **warns and never blocks**: a units attribute is a claim about
the data rather than the data itself, plenty of valid model output carries none
at all, and a user who knows better than their own metadata must still be able
to press Run.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from .categories import UNIT_FAMILIES, operator_inputs
from .fieldshape import check_fields

logger = logging.getLogger(__name__)

#: Anything that looks like a per-second rate. Matched after normalisation, so
#: ``kg m-2 s-1``, ``mm/s`` and ``mm s**-1`` all arrive in the same shape.
_RATE = re.compile(r"(/s\b|/sec|s-1|persecond|/second)")

#: Units that name degrees Celsius, in the normalised form.
_CELSIUS = frozenset({
    "c", "degc", "deg_c", "degreec", "degreesc", "degree_c", "degrees_c",
    "celsius", "degreescelsius", "degreecelsius", "°c",
})


@dataclass(frozen=True)
class UnitWarning:
    """One input slot whose units are not what the operator expects."""

    slot: int           # 1-based, matching the "Input 2" a user sees
    role: str           # the slot's declared role
    found: str          # the units attribute actually on the file
    expected: str       # what the operator wants, in words
    message: str        # the whole thing as one sentence, fit for a console

    def __str__(self) -> str:
        return self.message


def normalise(units: str) -> str:
    """A units string reduced to something comparable.

    Case, spaces, ``**`` and ``^`` all vary between models writing the same
    unit, and none of the variation is meaningful: ``kg m**-2`` and ``kg/m^2``
    are the same unit written by two different tools.
    """
    text = (units or "").strip().lower()
    text = text.replace("**", "").replace("^", "")
    return re.sub(r"[\s_]+", "", text)


def read_units(path: str, variable: Optional[str] = None) -> Optional[str]:
    """The ``units`` attribute of ``variable`` in ``path``, or None.

    None covers every way this can come back unanswered — no such file, not
    NetCDF, no variables, no units attribute — because the caller treats all of
    them the same way: as *unverifiable*, never as *wrong*. A file we cannot
    read is not evidence of a units problem.
    """
    try:
        import xarray as xr
    except ImportError:  # pragma: no cover - xarray is a hard dependency
        logger.debug("xarray unavailable; skipping the units check")
        return None

    try:
        with xr.open_dataset(path, decode_times=False) as dataset:
            names = list(dataset.data_vars)
            if variable and variable in dataset.data_vars:
                names = [variable]
            for name in names:
                units = dataset[name].attrs.get("units")
                if units:
                    return str(units)
    except Exception:
        logger.debug("Could not read units from %s", path, exc_info=True)
    return None


#: ``operator -> (standard name it looks for, what that field is)``.
#:
#: Three operators of the Interpolation section identify their vertical
#: coordinate by CF standard name and by nothing else, so a file that does not
#: carry it fails on a name the user has very likely never seen. Each entry is
#: quoted from the installed binary's own help rather than the manual prose:
#:
#:   cdo -h ap2pl  "The input file must contain the 3D air pressure in pascal.
#:                  The air pressure is identified by the NetCDF CF standard
#:                  name air_pressure."
#:   cdo -h gh2hl  "The input file must contain the 3D geometric height in
#:                  meter. The geometric height is identified by the NetCDF CF
#:                  standard name geometric_height_at_full_level_center."
#:
#: Deliberately only the operators that identify a field *solely* by standard
#: name. ml2pl is not here: it accepts a GRIB1 code number **or** a CF standard
#: name for each of its five required fields, so the absence of a standard name
#: is not evidence of anything and warning on it would fire on correct GRIB
#: input.
_REQUIRED_STANDARD_NAME = {
    "ap2pl": ("air_pressure", "the 3D air pressure, in pascal"),
    "ap2plx": ("air_pressure", "the 3D air pressure, in pascal"),
    "ap2hl": ("air_pressure", "the 3D air pressure, in pascal"),
    "ap2hlx": ("air_pressure", "the 3D air pressure, in pascal"),
    "gh2hl": ("geometric_height_at_full_level_center",
              "the 3D geometric height, in metres"),
    "gh2hlx": ("geometric_height_at_full_level_center",
               "the 3D geometric height, in metres"),
}


def standard_names(path: str) -> List[str]:
    """Every ``standard_name`` attribute in ``path``, in file order.

    Empty covers both "none of the variables carries one" and "the file could
    not be read", for the same reason :func:`read_units` returns None for both:
    a file we cannot read is not evidence of a problem, and the caller must not
    turn it into one. :func:`check_standard_names` therefore only warns when it
    found *some* variables and none of them matched.
    """
    try:
        import xarray as xr
    except ImportError:  # pragma: no cover - xarray is a hard dependency
        return []

    try:
        with xr.open_dataset(path, decode_times=False) as dataset:
            found = []
            for name in list(dataset.data_vars) + list(dataset.coords):
                value = dataset[name].attrs.get("standard_name")
                if value:
                    found.append(str(value))
            return found
    except Exception:
        logger.debug("Could not read standard names from %s", path,
                     exc_info=True)
        return []


def check_standard_names(operator: str,
                         paths: Sequence[str]) -> List["UnitWarning"]:
    """Warn when the input lacks the CF standard name ``operator`` looks for.

    The same shape as the units check and for the same reason: the failure it
    anticipates is a CDO abort naming a variable the user did not choose and
    may not recognise. Measured on 2.6.3, that abort is exactly

        cdo    ap2pl (Abort): air_pressure not found!
        cdo    gh2hl (Abort): geometric_height_at_full_level_center not found!

    — a bare standard name with no mention of the file it was looked for in.
    Saying it beforehand costs one file open and turns that into a sentence
    about the file that was picked.

    Warn, never block. A file may carry the field under a convention this does
    not know, and refusing a run that CDO would have accepted is the worse of
    the two errors — the same rule the units check follows.
    """
    entry = _REQUIRED_STANDARD_NAME.get(operator)
    if entry is None or not paths:
        return []
    required, description = entry

    found = standard_names(str(paths[0]))
    if not found or required in found:
        # No standard names at all means unreadable or unannotated, which is
        # not evidence; a match means there is nothing to say.
        return []

    return [UnitWarning(
        slot=1,
        role="Input 1",
        expected=required,
        found=", ".join(sorted(set(found))),
        message=(
            f"{operator} identifies its vertical coordinate only by CF "
            f"standard name, and this file does not carry '{required}' — it "
            f"carries {', '.join(sorted(set(found)))}. It needs {description}. "
            f"CDO will abort naming the standard name rather than the file."),
    )]


def data_variables(path: str) -> List[str]:
    """The data variables in ``path``, in file order; empty when unreadable.

    Here rather than in the canvas because the question it answers is the same
    one this module exists for — what does the file actually hold, as against
    what the caller assumed. Most of the climate indices write *two* variables
    (``eca_cdd`` writes the index and a count of qualifying periods), while the
    map draws ``data_vars[0]`` and says nothing about the rest.
    """
    try:
        import xarray as xr
    except ImportError:  # pragma: no cover - xarray is a hard dependency
        return []

    try:
        with xr.open_dataset(path, decode_times=False) as dataset:
            return [str(name) for name in dataset.data_vars]
    except Exception:
        logger.debug("Could not list variables in %s", path, exc_info=True)
        return []


@dataclass(frozen=True)
class ResultShape:
    """What a result file actually holds, as against what it is drawn as.

    ``points`` is the number of horizontal gridpoints, ``steps`` the length of
    the time axis, and ``variables`` every data variable in file order. All
    three are ``None``/empty when the file could not be read, which is not the
    same as zero and must not be treated as one.

    :attr:`is_single_point` is the question this exists to answer. The map
    canvas rejects data that is not two-dimensional and nothing else, so a
    ``(1, 1)`` field passes that guard and is drawn as one degenerate cell
    covering a zero-width extent — technically a successful render of a result
    that has no spatial extent at all. Every reducing operator produces one:
    ``fldcor`` and ``fldcovar`` by correlating a whole map into a number per
    timestep, and ``fldmean``/``fldsum``/``fldpctl`` and the rest of the field
    statistics the same way. Asking the *file* rather than the operator's name
    is what makes the answer cover all of them, including the ones that reach
    this application through a model, a batch or a saved project rather than
    through a click.
    """

    points: Optional[int] = None
    steps: Optional[int] = None
    variables: tuple = ()

    @property
    def is_single_point(self) -> bool:
        """True when the file has exactly one gridpoint — a series, not a map."""
        return self.points == 1

    @property
    def is_series(self) -> bool:
        """True when that single point carries more than one timestep."""
        return self.is_single_point and (self.steps or 0) > 1


def result_shape(path: str) -> ResultShape:
    """How many gridpoints, timesteps and variables ``path`` holds.

    Read from the file rather than predicted from the operator that wrote it,
    for the reason given on :class:`ResultShape`: the shape is a property of
    the result, the same reduction arrives from several operators and from
    surfaces that never name one, and a file the user opened by hand deserves
    the same treatment as one this application produced.

    The horizontal count is the product of the recognised lat/lon coordinate
    sizes rather than the array size, so a long time axis is not mistaken for
    spatial extent — which is exactly the confusion that lets a ``(time=6,
    lat=1, lon=1)`` field be drawn as a 6x1 map.
    """
    try:
        import xarray as xr
    except ImportError:  # pragma: no cover - xarray is a hard dependency
        return ResultShape()

    try:
        with xr.open_dataset(path, decode_times=False) as dataset:
            names = tuple(str(name) for name in dataset.data_vars)

            points = None
            for lon_name, lat_name in (("lon", "lat"), ("longitude", "latitude"),
                                       ("x", "y"), ("X", "Y")):
                if lon_name in dataset.sizes and lat_name in dataset.sizes:
                    points = int(dataset.sizes[lon_name]) * int(dataset.sizes[lat_name])
                    break
            else:
                # An unstructured grid names one horizontal dimension, not two.
                for cell_name in ("ncells", "cell", "nod2", "location"):
                    if cell_name in dataset.sizes:
                        points = int(dataset.sizes[cell_name])
                        break

            steps = None
            for time_name in ("time", "t", "Time", "TIME"):
                if time_name in dataset.sizes:
                    steps = int(dataset.sizes[time_name])
                    break

            return ResultShape(points=points, steps=steps, variables=names)
    except Exception:
        logger.debug("Could not read the shape of %s", path, exc_info=True)
        return ResultShape()


def _describe(family: str) -> str:
    entry = UNIT_FAMILIES.get(family)
    return entry.label if entry is not None else ""


def _check_slot(slot: int, role: str, family: str, found: str,
                first_units: Optional[str]) -> Optional[UnitWarning]:
    """One slot's verdict, or None when there is nothing to complain about."""
    entry = UNIT_FAMILIES.get(family)
    if entry is None:
        return None

    normalised = normalise(found)
    where = f"Input {slot} ({role})" if role else f"Input {slot}"

    if family == "same_as_input1":
        # Nothing to compare against, or they already agree.
        if not first_units or normalise(first_units) == normalised:
            return None
        return UnitWarning(
            slot, role, found, f"the same units as input 1 ({first_units})",
            f"{where} is in '{found}' while input 1 is in '{first_units}'. This "
            "operator compares the two files directly and does not convert "
            "between them.",
        )

    if normalised in entry.accepts:
        return None

    if family == "kelvin" and normalised in _CELSIUS:
        return UnitWarning(
            slot, role, found, entry.label,
            f"{where} is in '{found}', but this operator reads the field as "
            "Kelvin while its threshold argument is in degrees Celsius. CDO "
            "converts neither, so the counts will be wrong rather than "
            "refused. Convert with 'addc,273.15' first.",
        )

    if family == "precip" and _RATE.search(normalised):
        return UnitWarning(
            slot, role, found, entry.label,
            f"{where} is in '{found}', which is a rate. This operator wants an "
            "amount in mm (kg m-2); a daily rate needs 'mulc,86400' first or "
            "every threshold count comes out near zero.",
        )

    return UnitWarning(
        slot, role, found, entry.label,
        f"{where} is in '{found}', but this operator expects "
        f"{entry.label}. Check the field before trusting the result.",
    )


# ---------------------------------------------------------------------------
# Structural preconditions — the Statistic section
#
# The units check asks "is this field the quantity the operator assumes?". These
# ask "is this file the *shape* the operator assumes?", which for a whole run of
# Statistic modules is the question that decides whether the answer means
# anything. They live here rather than in a module of their own because they
# obey the same policy, stated at the top of this file and worth repeating
# because it is what makes them safe to add: **they warn and never block.**
#
# Every one of them is a documented precondition that CDO does not enforce. The
# common failure is not an abort — it is a well-formed file whose numbers answer
# a slightly different question than the one asked.
#
# One deliberate limit: these read the file with xarray and never run CDO. A
# precondition check that shells out costs a subprocess per input on a path that
# runs before every click, and the structural facts needed here — coordinate
# rank, level count, timestep spacing, value range — are all in the header.

#: Modules that require every variable on the same regular lon/lat grid.
#: Zonstat's ``zonmean`` is the documented exception, and only when it is given
#: a ``zonaldes``: with one it accepts an unstructured grid, which is the whole
#: reason that parameter exists.
_NEEDS_REGULAR_LONLAT = frozenset({
    "zonmin", "zonmax", "zonsum", "zonmean", "zonavg", "zonvar", "zonvar1",
    "zonstd", "zonstd1", "zonrange", "zonskew", "zonkurt", "zonmedian",
    "zonpctl",
    "mermin", "mermax", "mersum", "mermean", "meravg", "mervar", "mervar1",
    "merstd", "merstd1", "merrange", "merskew", "merkurt", "mermedian",
    "merpctl",
})

#: Gridboxstat works only on quadrilateral curvilinear grids — which includes
#: the regular lon/lat case, since that is a degenerate curvilinear grid. What
#: it cannot do is an unstructured or reduced grid, where "nx by ny cells" has
#: no meaning.
_NEEDS_QUADRILATERAL = frozenset({
    "gridboxmin", "gridboxmax", "gridboxsum", "gridboxmean", "gridboxavg",
    "gridboxvar", "gridboxvar1", "gridboxstd", "gridboxstd1", "gridboxrange",
    "gridboxskew", "gridboxkurt", "gridboxmedian",
})

#: Varsstat reduces *across variables* at each gridpoint, so the variables have
#: to be commensurable: same gridsize, same level count.
_NEEDS_MATCHING_VARIABLES = frozenset({
    "varsmin", "varsmax", "varssum", "varsmean", "varsavg", "varsvar",
    "varsvar1", "varsstd", "varsstd1", "varsrange", "varsskew", "varskurt",
    "varsmedian", "varspctl",
})

#: The Ydrunstat family and Ydrunpctl: a continuous daily series, and an output
#: shorter than the input at both ends.
_NEEDS_CONTINUOUS_DAILY = frozenset({
    "ydrunmin", "ydrunmax", "ydrunsum", "ydrunmean", "ydrunavg", "ydrunvar",
    "ydrunvar1", "ydrunstd", "ydrunstd1", "ydrunpctl",
})

#: Seasstat and Seaspctl: an incomplete first or last season is not detected.
_SEASONAL = frozenset({
    "seasmin", "seasmax", "seassum", "seasmean", "seasavg", "seasvar",
    "seasvar1", "seasstd", "seasstd1", "seasrange", "seaspctl",
})

#: Consecstat expects a 0/1 mask, not data.
_NEEDS_MASK = frozenset({"consecsum", "consects"})

#: ``ensrkhist*``: single level only, warned rather than refused by CDO.
_NEEDS_SINGLE_LEVEL = frozenset({"ensrkhisttime", "ensrkhistspace"})


@dataclass(frozen=True)
class _Structure:
    """The header facts the precondition checks need, read once per file."""

    lonlat_rank: Optional[int] = None    # 1 regular, 2 curvilinear, 0 neither
    levels: Optional[int] = None
    steps: Optional[int] = None
    #: True when consecutive timesteps are evenly spaced, False when they are
    #: not, None when it could not be determined.
    evenly_spaced: Optional[bool] = None
    #: Days between consecutive steps, when the axis is in days and even.
    step_days: Optional[float] = None
    #: ``variable -> (gridsize, levels)``, for the across-variable check.
    variable_shapes: Dict[str, tuple] = None
    #: True when every value read is 0, 1 or missing.
    is_mask: Optional[bool] = None
    has_level_bounds: Optional[bool] = None
    #: ``(month, day)`` of the first and last timestep, decoded; None when the
    #: time axis could not be decoded to real dates.
    first_date: Optional[tuple] = None
    last_date: Optional[tuple] = None


def _read_structure(path: str) -> _Structure:
    """Header facts about ``path``; every field None when it cannot be read.

    Unreadable is never reported as wrong, exactly as :func:`read_units` treats
    a missing units attribute. The whole point of these checks is to catch a
    file that is *readably* the wrong shape.
    """
    try:
        import numpy as np
        import xarray as xr
    except ImportError:  # pragma: no cover - both are hard dependencies
        return _Structure()

    try:
        with xr.open_dataset(path, decode_times=False) as dataset:
            lat = _coordinate(dataset, ("lat", "latitude", "nav_lat", "clat"))
            lon = _coordinate(dataset, ("lon", "longitude", "nav_lon", "clon"))
            if lat is None or lon is None:
                rank = 0
            else:
                rank = max(lat.ndim, lon.ndim)

            level = _coordinate(
                dataset, ("lev", "level", "plev", "height", "depth", "z"))
            levels = int(level.size) if level is not None else 1

            time = _coordinate(dataset, ("time", "t"))
            steps = int(time.size) if time is not None else None
            evenly_spaced, step_days = _spacing(time)

            shapes = {}
            for name, array in dataset.data_vars.items():
                horizontal = 1
                for dim, size in zip(array.dims, array.shape):
                    if str(dim).lower() in ("time", "t"):
                        continue
                    if level is not None and str(dim) == str(level.name):
                        continue
                    horizontal *= int(size)
                own_levels = 1
                if level is not None and str(level.name) in array.dims:
                    own_levels = int(level.size)
                shapes[str(name)] = (horizontal, own_levels)

            bounds = any(
                str(name).endswith(("_bnds", "_bounds"))
                and ("lev" in str(name) or "height" in str(name)
                     or "depth" in str(name) or "plev" in str(name))
                for name in list(dataset.variables))

            first_date, last_date = _endpoint_dates(path)

            return _Structure(
                lonlat_rank=rank, levels=levels, steps=steps,
                evenly_spaced=evenly_spaced, step_days=step_days,
                variable_shapes=shapes, is_mask=_looks_like_mask(dataset, np),
                has_level_bounds=bounds,
                first_date=first_date, last_date=last_date)
    except Exception:
        logger.debug("Could not read structure from %s", path, exc_info=True)
        return _Structure()


def _coordinate(dataset, names):
    """The first coordinate or variable in ``dataset`` matching ``names``."""
    for name in names:
        if name in dataset.coords:
            return dataset.coords[name]
        if name in dataset.variables:
            return dataset.variables[name]
    return None


def _spacing(time):
    """``(evenly_spaced, days_between_steps)`` for a raw time axis.

    Read from the undecoded values plus the axis's own ``units`` string, so no
    calendar library is needed and a non-standard calendar cannot throw. Only
    a ``days since`` or ``hours since`` axis yields a day count; anything else
    still yields the evenness answer, which is the half these checks need most.
    """
    if time is None or time.size < 3:
        return None, None
    try:
        values = [float(v) for v in time.values.ravel()]
    except Exception:
        return None, None

    gaps = [round(b - a, 6) for a, b in zip(values, values[1:])]
    if not gaps:
        return None, None
    even = len(set(gaps)) == 1

    units = str(getattr(time, "attrs", {}).get("units", "")).lower()
    per_day = None
    if units.startswith("days"):
        per_day = 1.0
    elif units.startswith("hours"):
        per_day = 1.0 / 24.0
    elif units.startswith("minutes"):
        per_day = 1.0 / 1440.0
    elif units.startswith("seconds"):
        per_day = 1.0 / 86400.0
    step_days = gaps[0] * per_day if (even and per_day) else None
    return even, step_days


def _endpoint_dates(path: str):
    """``((month, day), (month, day))`` for the first and last timestep.

    A second open, with decoding on, because the seasonal check needs real
    calendar dates and the rest of this function deliberately reads raw values
    — decoding can throw on an exotic calendar, and one check wanting dates is
    not a reason to risk the other six.

    ``(None, None)`` whenever the axis will not decode, which is treated as
    "cannot tell" and produces no warning.
    """
    try:
        import xarray as xr

        with xr.open_dataset(path) as decoded:
            if "time" not in decoded.coords or decoded.coords["time"].size == 0:
                return None, None
            axis = decoded.coords["time"]
            first = axis.values[0]
            last = axis.values[-1]

            def month_day(value):
                for attr in ("month", "day"):
                    if not hasattr(value, attr):
                        import pandas as pd

                        value = pd.Timestamp(str(value))
                        break
                return int(value.month), int(value.day)

            return month_day(first), month_day(last)
    except Exception:
        logger.debug("Could not decode the time axis of %s", path,
                     exc_info=True)
        return None, None


def _looks_like_mask(dataset, np) -> Optional[bool]:
    """True when every finite value in the first data variable is 0 or 1.

    Sampled rather than exhaustive — the first variable and at most the first
    100000 values — because this runs before a click and a full scan of a
    multi-gigabyte file to produce a *warning* is the wrong trade.
    """
    names = list(dataset.data_vars)
    if not names:
        return None
    try:
        values = np.asarray(dataset[names[0]].values).ravel()[:100000]
        finite = values[np.isfinite(values)]
        if finite.size == 0:
            return None
        return bool(np.all((finite == 0) | (finite == 1)))
    except Exception:
        return None


def _structure_warning(slot: int, role: str, message: str,
                       expected: str, found: str) -> UnitWarning:
    return UnitWarning(slot=slot, role=role, found=found, expected=expected,
                       message=message)


def check_structure(operator: str,
                    paths: Sequence[str]) -> List["UnitWarning"]:
    """Warn when the input's shape is not what ``operator``'s module assumes.

    Nine documented preconditions across the Statistic section, none of which
    CDO enforces. Each returns at most one warning, and every one of them warns
    rather than blocks — see the block comment above.
    """
    if not paths:
        return []

    interesting = (
        _NEEDS_REGULAR_LONLAT | _NEEDS_QUADRILATERAL
        | _NEEDS_MATCHING_VARIABLES | _NEEDS_CONTINUOUS_DAILY
        | _SEASONAL | _NEEDS_MASK | _NEEDS_SINGLE_LEVEL
    )
    if operator not in interesting:
        return []

    shape = _read_structure(str(paths[0]))
    warnings: List[UnitWarning] = []

    # Zonstat and Merstat: a zonal or meridional mean is a mean along a row or
    # a column, which only exists on a regular lon/lat grid. On a curvilinear
    # or unstructured grid CDO does not refuse — it reduces along whatever the
    # first dimension turns out to be.
    if operator in _NEEDS_REGULAR_LONLAT and shape.lonlat_rank in (0, 2):
        kind = ("a curvilinear grid (2D lat/lon)" if shape.lonlat_rank == 2
                else "no recognisable lat/lon coordinates")
        exception = ""
        if operator == "zonmean":
            exception = (" zonmean is the one operator of these two modules "
                         "that can work off a regular grid, and only when it "
                         "is given a zonaldes parameter.")
        warnings.append(_structure_warning(
            1, "Input 1",
            f"{operator} requires every variable on the same regular lon/lat "
            f"grid, and this file has {kind}. CDO will not refuse it; the "
            f"reduction just runs along the wrong axis.{exception}",
            "a regular lon/lat grid", kind))

    # Gridboxstat: nx-by-ny boxes need a quadrilateral grid to count cells on.
    if operator in _NEEDS_QUADRILATERAL and shape.lonlat_rank == 0:
        warnings.append(_structure_warning(
            1, "Input 1",
            f"{operator} works only on quadrilateral curvilinear grids "
            f"(a regular lon/lat grid is one), and this file has no "
            f"recognisable lat/lon coordinates. nx-by-ny boxes have no "
            f"meaning on an unstructured or reduced grid.",
            "a quadrilateral grid", "no lat/lon coordinates"))

    # Varsstat reduces across variables at each point, so they must line up.
    if operator in _NEEDS_MATCHING_VARIABLES and shape.variable_shapes:
        distinct = set(shape.variable_shapes.values())
        if len(distinct) > 1:
            detail = ", ".join(
                f"{name} ({size} points, {levels} level(s))"
                for name, (size, levels) in
                sorted(shape.variable_shapes.items()))
            warnings.append(_structure_warning(
                1, "Input 1",
                f"{operator} reduces across all variables at each gridpoint, "
                f"so every variable must share a gridsize and a level count. "
                f"This file mixes: {detail}.",
                "one gridsize and level count for all variables", detail))

    # Ydrunstat/Ydrunpctl: a continuous daily series, and a shorter output.
    if operator in _NEEDS_CONTINUOUS_DAILY:
        if shape.evenly_spaced is False:
            warnings.append(_structure_warning(
                1, "Input 1",
                f"{operator} needs a continuous daily series and this file's "
                f"timesteps are not evenly spaced. A gap is not detected — "
                f"the running window simply spans it, mixing dates that are "
                f"not neighbours.",
                "evenly spaced daily timesteps", "uneven spacing"))
        elif shape.step_days is not None and abs(shape.step_days - 1.0) > 1e-6:
            warnings.append(_structure_warning(
                1, "Input 1",
                f"{operator} expects a daily series; this file's timesteps "
                f"are {shape.step_days:g} days apart. The window parameter "
                f"counts timesteps, so it will not mean the number of days "
                f"you intend.",
                "one timestep per day", f"{shape.step_days:g} days per step"))

    # Seasstat/Seaspctl: an incomplete season at either end is computed from
    # the part that is there, silently.
    #
    # Conditional on the actual dates rather than warned unconditionally, and
    # that distinction is the point: an unconditional caution fires on every
    # correct call, and a check that fires on correct input is how a user
    # learns to ignore it. Seasons start on the first of December, March, June
    # and September; a series that starts on one of those and ends the day
    # before the next has no partial season and gets nothing said to it.
    if operator in _SEASONAL and shape.first_date and shape.last_date:
        starts = shape.first_date in ((12, 1), (3, 1), (6, 1), (9, 1))
        ends = shape.last_date in ((11, 30), (2, 28), (2, 29), (5, 31),
                                   (8, 31))
        if not (starts and ends):
            where = []
            if not starts:
                where.append(
                    f"starts on {shape.first_date[0]:02d}-"
                    f"{shape.first_date[1]:02d}")
            if not ends:
                where.append(
                    f"ends on {shape.last_date[0]:02d}-"
                    f"{shape.last_date[1]:02d}")
            warnings.append(_structure_warning(
                1, "Input 1",
                f"{operator}'s first and last output timesteps will be "
                f"computed from incomplete seasons: this series "
                f"{' and '.join(where)}, and a season runs Dec-Feb, Mar-May, "
                f"Jun-Aug or Sep-Nov. CDO averages the part it has rather "
                f"than skipping the season, and says nothing.",
                "a series spanning whole seasons", " and ".join(where)))

    # Consecstat wants a mask, and treats missing as 0.
    if operator in _NEEDS_MASK and shape.is_mask is False:
        warnings.append(_structure_warning(
            1, "Input 1",
            f"{operator} expects a 0/1 mask, not data, and this file holds "
            f"values other than 0 and 1. Build the mask first — the module's "
            f"own example is 'cdo consects -gtc,20.0 infile outfile'. Missing "
            f"values count as 0 and end a period.",
            "a 0/1 mask", "values outside {0, 1}"))

    # ensrkhist*: more than one level is a warning in CDO and a wrong answer.
    if operator in _NEEDS_SINGLE_LEVEL and (shape.levels or 1) > 1:
        warnings.append(_structure_warning(
            1, "Input 1",
            f"{operator} does not support more than one level and this file "
            f"has {shape.levels}. CDO warns and carries on rather than "
            f"refusing — use splitlevel and run it once per level.",
            "a single level", f"{shape.levels} levels"))

    return warnings


def check_vertical_weights(operator: str,
                           paths: Sequence[str]) -> List["UnitWarning"]:
    """Warn when Vertstat's layer weighting has nothing to weight by.

    Separate from :func:`check_structure` because it is conditional on a
    *parameter* rather than on the operator alone, and because the finding is
    the opposite shape: the run is fine, the setting is inert.

    Measured on 2.6.3: without layer bounds CDO prints "Layer bounds not
    available, using constant vertical weights for variable P!" and weighted
    and unweighted results agree to the last digit. After ``cdo
    genlevelbounds`` they differ — vertmean gives 760.8173 by default against
    840.2465 at weights=FALSE on the same file.
    """
    if operator not in _VERTSTAT_WEIGHTED or not paths:
        return []
    shape = _read_structure(str(paths[0]))
    if shape.has_level_bounds is not False or (shape.levels or 1) < 2:
        return []
    return [_structure_warning(
        1, "Input 1",
        f"{operator} weights each level by its layer thickness, and this file "
        f"carries no layer bounds — CDO will fall back to constant weights "
        f"and the weighting setting will make no difference. Run "
        f"genlevelbounds first if the thickness weighting is what you want.",
        "levels with bounds", "no layer bounds")]


#: The six Vertstat operators that read the ``weights`` key. The other four —
#: vertmax, vertmin, vertrange, vertsum — accept any key at all and read none
#: of them, so there is no weighting to warn about. See ``_VERT_WEIGHTS`` in
#: core/categories.py for that measurement.
_VERTSTAT_WEIGHTED = frozenset({
    "vertmean", "vertavg", "vertvar", "vertvar1", "vertstd", "vertstd1",
})


def check_inputs(operator: str, paths: Sequence[str]) -> List[UnitWarning]:
    """Every units disagreement between ``paths`` and what ``operator`` wants.

    An empty list means either "they agree" or "there was nothing to check" —
    the two are deliberately not distinguished, because neither is a reason to
    stop and the caller has nothing different to do about them.

    The CF standard-name check is folded in here rather than offered as a
    second function, so every caller that already asks about units gets it
    without a new call site. It is the same question one level down: the units
    check asks whether the *field* is what the operator assumes, and for the
    ICON vertical operators the field is identified by standard name and by
    nothing else. See :func:`check_standard_names`.

    The Statistic section's structural preconditions are folded in for the same
    reason and on the same terms — see :func:`check_structure` and
    :func:`check_vertical_weights`. They ask about the file's *shape* rather
    than its units, and they are the same kind of finding: something the
    operator's own documentation requires, that CDO does not enforce, and whose
    absence produces a plausible wrong answer rather than a failure.

    ``core/fieldshape.py`` is folded in last and on the same terms again. It
    asks the question this module cannot — not what quantity the file holds but
    what *representation* — and it is the strongest instance of the same
    finding: measured on 2.6.3, every operator of the Transformation section
    handed a field it cannot use warns on stderr, exits 0, and copies the input
    through unchanged. Its warnings are converted rather than returned as their
    own type because that module is imported here and naming ``UnitWarning``
    there would be a cycle; the four fields line up one to one.
    """
    warnings_from_names = check_standard_names(operator, paths)
    warnings_from_names += check_structure(operator, paths)
    warnings_from_names += check_vertical_weights(operator, paths)
    warnings_from_names += [
        UnitWarning(slot=finding.slot, role=finding.role, found=finding.found,
                    expected=finding.expected, message=finding.message)
        for finding in check_fields(operator, paths)
    ]

    slots = operator_inputs(operator)
    if not slots or not paths:
        return warnings_from_names

    found: Dict[int, Optional[str]] = {}
    warnings: List[UnitWarning] = list(warnings_from_names)
    for index, slot in enumerate(slots, start=1):
        if index > len(paths):
            break
        units = read_units(str(paths[index - 1]))
        found[index] = units
        if not units:
            continue
        warning = _check_slot(index, slot.role, slot.units, units, found.get(1))
        if warning is not None:
            warnings.append(warning)
    return warnings


def expectation(operator: str) -> str:
    """One line naming the units each input slot wants, or "" when none do.

    Used where there is room for a hint but not for a warning — a tooltip on
    the operator, the line above a parameter form.
    """
    parts = []
    for index, slot in enumerate(operator_inputs(operator), start=1):
        label = _describe(slot.units)
        if label:
            parts.append(f"input {index} in {label}")
    return "; ".join(parts)
