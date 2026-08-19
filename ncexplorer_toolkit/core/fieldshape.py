# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""Is this file the right *kind* of field for the slot it was dropped into?

``core/units.py`` asks whether a file's numbers are in the units the operator
will assume. ``core/pairing.py`` asks whether two files are comparable to each
other. Neither can ask the question the Transformation section turns on: not
"what quantity is this" but "**what representation** is this" — spherical
harmonics or gridpoints, a Gaussian grid or a lonlat one, divergence and
vorticity or a wind pair, complex or real.

**Why a third module rather than a branch in units.py.** ``units.check_structure``
already answers shape questions, and folding these in there was the obvious
move. Two things argue against it, and the second is the deciding one.

* Its questions are about a field that is already the right kind of thing —
  are the levels bounded, is the series daily, is this a 0/1 mask. Ours is
  whether it is that kind of thing at all, and a file that fails it is not a
  file with a defect but the wrong file.
* Every one of those checks is keyed off a hand-written frozenset of operator
  names — ``_NEEDS_MASK``, ``_NEEDS_SINGLE_LEVEL`` — and a table like that is
  what this application has spent the Transformation work getting *away* from.
  What is checked here is read off :attr:`~.categories.OperatorInput.shape`, so
  declaring the slot is what turns the check on, and there is no second list to
  keep in step with the first. That is the same argument ``pairing.py`` makes
  for deriving its own applicability from the schema instead of naming the
  correlation family, and the same one ``samples._declared_companions`` makes
  in ``operator_lab``.

The findings are folded into :func:`units.check_inputs` rather than offered as
a new call site, exactly as ``check_structure`` is, so every surface that
already asks about a file gets these for free.

**It warns; it never blocks.** On the same grounds ``units.py`` gives: what is
read here is metadata — a coordinate array, a variable name, a dtype — and
metadata is a claim about the data rather than the data. A Gaussian grid
written without its latitudes, a wind pair named ``U``/``V`` by a model that
does not follow CDO's convention, a spectral field this module has not been
taught to recognise: all three are files a user may know are right, and they
must still be able to press Run. Unreadable is never reported as wrong, for the
same reason.

**What the check is worth.** Measured on CDO 2.6.3 (macOS, no LIBFFTW3) against
a plain ``cdo -f nc random,r18x9,1`` lonlat field, every operator in this
section *succeeded*::

    cdo gp2sp lonlat.nc out.nc  -> "(Warning): No data on regular Gaussian
                                    grid found!"                      exit 0
    cdo sp2gp lonlat.nc out.nc  -> "(Warning): No spectral data found!" exit 0
    cdo uv2dv lonlat.nc out.nc  -> "(Warning): U-wind not found!" and
                                   "(Warning): V-wind not found!"      exit 0
    cdo dv2uv lonlat.nc out.nc  -> "(Warning): Divergence not found!" and
                                   "(Warning): Vorticity not found!"   exit 0
    cdo dv2ps lonlat.nc out.nc  -> the same two warnings                exit 0

and in every case ``cdo sinfon`` on the output showed the input's own variable
on the input's own 18x9 lonlat grid. The warning goes to stderr, the exit code
says success, the file opens and the map draws. That is ``fldcor``'s silent
truncation one section over, and it is the whole reason this module exists.

**One thing that is not silent, and is worth knowing apart from the rest.**
The missing-variable case above is the quiet one. Getting the *grid* wrong
while the variables are present is loud::

    cdo uv2dv uv_lonlat.nc out.nc -> "(Abort): U-wind is not on Gaussian grid!"
                                                                       exit 1

So the ``uv`` detector reports the two separately and says which is which: a
file with no u/v is a run that will look like it worked, and a u/v pair on the
wrong grid is a run that will stop.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .categories import operator_inputs

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ShapeWarning:
    """One input slot holding the wrong kind of field.

    Deliberately its own type rather than ``units.UnitWarning``: this module is
    imported *by* ``units``, so naming its type here would be a cycle. The
    fields are the same four so the conversion at the fold-in point is a
    constructor call and nothing more.
    """

    slot: int           # 1-based, matching the "Input 2" a user sees
    role: str           # the slot's declared role
    found: str          # what the file looks like instead
    expected: str       # what the slot wants, in words
    message: str        # the whole thing as one sentence, fit for a dialog

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class _Facts:
    """The header facts every detector reads, gathered in one open.

    Every field is None or empty when it could not be read, and no detector
    treats that as evidence — see the module docstring on warning versus
    blocking.
    """

    readable: bool = False
    #: Dimension names, lowercased.
    dims: Tuple[str, ...] = ()
    #: Data variables, excluding coordinate bounds, lowercased.
    variables: Tuple[str, ...] = ()
    #: GRIB parameter codes carried on those variables.
    codes: Tuple[int, ...] = ()
    #: Variables whose dtype is a complex pair.
    complex_variables: Tuple[str, ...] = ()
    #: True when a latitude axis is present.
    has_latitude: bool = False
    #: True when the latitudes are unevenly spaced — the Gaussian signature.
    #: None when there is no latitude axis or too few points to tell.
    uneven_latitudes: Optional[bool] = None
    #: True when the latitude axis reaches a pole exactly.
    pole_inclusive: Optional[bool] = None
    latitude_count: int = 0


#: Coordinate names a latitude axis may go by, in the order they are tried.
_LATITUDE_NAMES = ("lat", "latitude", "nav_lat", "clat")

#: How far the gaps in a latitude axis may vary, relative to their mean, before
#: the axis counts as unevenly spaced.
#:
#: Measured on 2.6.3, ``cdo -f nc random,<grid>,1`` for each grid, as
#: ``(max_gap - min_gap) / mean_gap`` over ``numpy.diff(lat)``::
#:
#:     t21grid   n=32   8.31e-03      r360x180  n=180  0.00e+00
#:     t63grid   n=96   8.38e-03      r72x36    n=36   0.00e+00
#:     t255grid  n=384  8.38e-03      r18x9     n=9    0.00e+00
#:
#: Four orders of magnitude of margin, and the Gaussian figure barely moves
#: with resolution, so the threshold is not tuned to one grid. Pole
#: inclusiveness is *not* used as the test — ``r360x180`` and ``r72x36`` are
#: both non-pole-inclusive and both perfectly regular — it is only reported
#: alongside, because it is the part a user can see for themselves.
_UNEVEN_ABOVE = 1e-4


def _read_facts(path: str) -> _Facts:
    """Everything the detectors need from ``path``, or an empty answer."""
    try:
        import numpy as np
        import xarray as xr
    except ImportError:                     # pragma: no cover - hard dependencies
        return _Facts()

    try:
        with xr.open_dataset(path, decode_times=False) as dataset:
            names: List[str] = []
            codes: List[int] = []
            complex_names: List[str] = []
            for raw in dataset.data_vars:
                name = str(raw)
                # ``lat_bnds`` and friends are data variables to xarray and
                # coordinates to everyone else. Counting them would make every
                # Gaussian file look like it holds an extra field.
                if name.endswith(("_bnds", "_bounds")):
                    continue
                names.append(name.lower())
                code = dataset[raw].attrs.get("code")
                if code is not None:
                    try:
                        codes.append(int(code))
                    except (TypeError, ValueError):
                        pass
                if _is_complex(dataset[raw].dtype):
                    complex_names.append(name.lower())

            latitude = None
            for candidate in _LATITUDE_NAMES:
                if candidate in dataset.coords:
                    latitude = dataset.coords[candidate]
                    break
                if candidate in dataset.variables:
                    latitude = dataset.variables[candidate]
                    break

            uneven: Optional[bool] = None
            pole: Optional[bool] = None
            count = 0
            if latitude is not None:
                try:
                    values = np.asarray(latitude.values, dtype=float).ravel()
                except (TypeError, ValueError):
                    values = np.empty(0)
                count = int(values.size)
                if count >= 3:
                    gaps = np.abs(np.diff(values))
                    mean = float(gaps.mean())
                    if mean > 0:
                        spread = float(gaps.max() - gaps.min()) / mean
                        uneven = bool(spread > _UNEVEN_ABOVE)
                    pole = bool(
                        abs(abs(float(values[0])) - 90.0) < 1e-6
                        or abs(abs(float(values[-1])) - 90.0) < 1e-6)

            return _Facts(
                readable=True,
                dims=tuple(str(dim).lower() for dim in dataset.sizes),
                variables=tuple(names),
                codes=tuple(codes),
                complex_variables=tuple(complex_names),
                has_latitude=latitude is not None,
                uneven_latitudes=uneven,
                pole_inclusive=pole,
                latitude_count=count,
            )
    except Exception:
        logger.debug("Could not read the field shape of %s", path, exc_info=True)
        return _Facts()


def _is_complex(dtype) -> bool:
    """Whether a variable's dtype is a complex pair, in either spelling.

    Not ``dtype.kind == "c"``, which is what this looked for first and which is
    wrong for the files CDO actually writes. Measured: ``cdo -f nc4 retocomplex``
    output read back through xarray carries a **structured** dtype —
    ``{'names': ['r', 'i'], 'formats': ['<f4', '<f4'], …}``, kind ``V`` — not a
    native complex one. Both are accepted, since a NetCDF library that presents
    it natively is not a file this should refuse.
    """
    if getattr(dtype, "kind", "") == "c":
        return True
    names = getattr(dtype, "names", None)
    return bool(names) and set(names) <= {"r", "i", "real", "imag"}


# ---------------------------------------------------------------------------
# The detectors. One per ``OperatorInput.shape`` value.
#
# Each takes the facts and returns ``(looks_right, found_in_words, instead)``:
#
#   looks_right  True, False, or None for "could not tell" — which never
#                produces a warning.
#   found        what the file is, in words, for the middle of the sentence.
#   instead      a consequence replacing the shape's usual one, or "" to use
#                it. Present because one detector can find two different
#                problems whose outcomes differ: a file with no u/v makes
#                ``uv2dv`` exit 0 having copied its input, and a u/v pair on
#                the wrong grid makes it abort. Telling a user both, and
#                leaving them to work out which they have, is the sort of
#                hedged warning people learn to skip.
#
# Anything that could report two problems returns the first that applies, so a
# slot never produces two warnings that have to be reconciled.
# ---------------------------------------------------------------------------

#: A detector's answer. Aliased rather than spelled out at seven call sites.
_Answer = Tuple[Optional[bool], str, str]


def _spectral(facts: _Facts) -> _Answer:
    """Spherical-harmonic coefficients.

    CDO writes these with no horizontal coordinates at all and a 1-D ``nsp``
    axis: measured, ``cdo gp2sp`` output reads back as dims ``{'nsp': 253,
    'nc2': 2}`` with no ``lat``/``lon``, against ``{'lat': 32, 'lon': 64}`` for
    the Gaussian field it came from.
    """
    if not facts.readable:
        return None, "", ""
    if "nsp" in facts.dims:
        return True, "", ""
    if facts.has_latitude:
        return False, "a gridpoint field with a latitude axis", ""
    return False, "a field with no spectral (nsp) axis", ""


def _gaussian(facts: _Facts) -> _Answer:
    """A global regular Gaussian grid.

    Told from a regular lonlat grid by the *spacing* of the latitudes, which is
    uneven on a Gaussian grid and exactly uniform on a lonlat one — see
    :data:`_UNEVEN_ABOVE` for the measurements behind the threshold.
    """
    if not facts.readable:
        return None, "", ""
    if "nsp" in facts.dims:
        return False, "a spectral field", ""
    if not facts.has_latitude or facts.uneven_latitudes is None:
        return None, "", ""
    if facts.uneven_latitudes:
        return True, "", ""
    poles = " reaching the poles" if facts.pole_inclusive else ""
    return False, (f"a regular grid of {facts.latitude_count} evenly spaced "
                   f"latitudes{poles}"), ""


def _named(facts: _Facts, names: Sequence[str],
           codes: Sequence[int]) -> List[str]:
    """Which of ``names`` the file carries, by variable name or by GRIB code.

    Either signal is enough. CDO itself accepts both — ``uv2dv`` finds its
    components by name *or* by codes 131/132 — and model output routinely
    carries one without the other: the ``cdo uv2dv`` output measured for this
    module names its variables ``sd``/``svo`` **and** codes them 155/138, while
    a plain ``cdo -setname,u`` file has the name and no code at all.
    """
    return [name for name, code in zip(names, codes)
            if name in facts.variables or code in facts.codes]


def _pair_detector(names: Sequence[str], codes: Sequence[int], what: str):
    """A detector for "this file must hold these two variables"."""

    def detect(facts: _Facts) -> _Answer:
        if not facts.readable or not facts.variables:
            return None, "", ""
        present = _named(facts, names, codes)
        if len(present) == len(names):
            return True, "", ""
        missing = [name for name in names if name not in present]
        held = ", ".join(facts.variables) or "nothing"
        return False, f"missing {' and '.join(missing)} — it holds {held}", ""

    detect.__doc__ = f"{what}, by variable name or GRIB code."
    return detect


#: ``sd``/``svo``, codes 155/138 — which is exactly what ``uv2dv`` writes.
#: Measured: ``cdo uv2dv`` output carries ``sd`` with ``code = 155`` and ``svo``
#: with ``code = 138``, both on the spectral axis.
_divergence_vorticity = _pair_detector(
    ("sd", "svo"), (155, 138), "Spectral divergence and vorticity")


def _uv(facts: _Facts) -> Tuple[Optional[bool], str]:
    """A u/v wind pair on a Gaussian grid.

    Two findings in one slot, and they are not the same kind of problem — see
    the module docstring. Missing components are the silent case (exit 0, input
    copied through); the right components on the wrong grid is an abort. The
    variable check comes first because it is the one that will not announce
    itself.
    """
    if not facts.readable or not facts.variables:
        return None, "", ""
    present = _named(facts, ("u", "v"), (131, 132))
    if len(present) < 2:
        missing = [name for name in ("u", "v") if name not in present]
        held = ", ".join(facts.variables) or "nothing"
        return False, f"missing {' and '.join(missing)} — it holds {held}", ""
    if facts.uneven_latitudes is False:
        return False, (
            f"u and v on a regular grid of {facts.latitude_count} evenly "
            f"spaced latitudes rather than a Gaussian one"
        ), ("This one does not pass silently: measured on 2.6.3, uv2dv with "
            "both components present on a lonlat grid aborts with \"U-wind is "
            "not on Gaussian grid!\", exit 1. Regrid first, e.g. "
            "cdo remapbil,t63grid infile outfile.")
    return True, "", ""


def _complex(facts: _Facts) -> _Answer:
    """Complex-valued data, which only NetCDF4 and EXTRA can hold."""
    if not facts.readable or not facts.variables:
        return None, "", ""
    if facts.complex_variables:
        return True, "", ""
    return False, f"real-valued data ({', '.join(facts.variables)})", ""


@dataclass(frozen=True)
class _Shape:
    """One kind of field a slot can require."""

    #: What the slot wants, in words, for the "expected" half of a warning.
    expected: str
    detect: Callable[[_Facts], _Answer]
    #: What CDO does with the wrong thing — the half of the message that says
    #: why this is worth reading. Measured, and quoted in CDO's own words.
    consequence: str


#: ``OperatorInput.shape`` → how to check it. Adding a slot to the schema with
#: one of these keys is the whole of switching the check on for that operator.
DETECTORS: Dict[str, _Shape] = {
    # Each ``consequence`` is worded for whichever operator declares the shape,
    # not for the one it was measured on — four operators share ``spectral``
    # and three share ``divergence_vorticity``, so "sp2gp warns …" read as a
    # non-sequitur under ``spcut``. The measurement is still named.
    "spectral": _Shape(
        "spectral coefficients",
        _spectral,
        "This does not fail: measured on 2.6.3, sp2gp against a lonlat field "
        "warns \"No spectral data found!\", exits 0, and copies the input "
        "through unchanged — a finished file on the original grid with nothing "
        "transformed. Build the input with cdo gp2sp first."),
    "gaussian": _Shape(
        "a field on a global regular Gaussian grid",
        _gaussian,
        "This does not fail: measured on 2.6.3, gp2sp against a lonlat field "
        "warns \"No data on regular Gaussian grid found!\", exits 0, and "
        "copies the input through unchanged. Regrid first, e.g. "
        "cdo remapbil,t63grid infile outfile."),
    "divergence_vorticity": _Shape(
        "spectral divergence (sd, code 155) and vorticity (svo, code 138)",
        _divergence_vorticity,
        "This does not fail: measured on 2.6.3, dv2uv against a field holding "
        "neither warns \"Divergence not found!\" and \"Vorticity not found!\", "
        "exits 0, and copies the input through unchanged. Build the pair with "
        "cdo uv2dv over a Gaussian u/v file."),
    "uv": _Shape(
        "u and v wind (codes 131 and 132) on a Gaussian grid",
        _uv,
        "This does not fail: measured on 2.6.3, uv2dv against a field holding "
        "neither warns \"U-wind not found!\" and \"V-wind not found!\", exits "
        "0, and copies the input through unchanged."),
    "complex": _Shape(
        "a complex-valued field",
        _complex,
        "Measured on 2.6.3, this one aborts rather than passing silently — "
        "\"This operator needs fields with complex numbers!\", exit 1. Build "
        "the input with cdo -f nc4 retocomplex; complex numbers exist only in "
        "NetCDF4 and EXTRA."),
}


def expected_shapes(operator: str) -> List[Tuple[int, str]]:
    """``(1-based slot, shape key)`` for every slot of ``operator`` declaring one.

    The schema is the only source: a slot with no ``shape`` is not checked, and
    an operator with no declared slots is not checked at all.
    """
    return [
        (index, slot.shape)
        for index, slot in enumerate(operator_inputs(operator), start=1)
        if slot.shape
    ]


def check_fields(operator: str,
                 paths: Sequence[str]) -> List[ShapeWarning]:
    """Every input of ``operator`` that is not the kind of field it must be.

    Empty means "they look right" or "there was nothing to check" — the two are
    not distinguished, for the reason :func:`units.check_inputs` gives about the
    same distinction: neither is a reason to stop, and the caller has nothing
    different to do about them.

    At most one warning per slot. A file that is wrong in two ways is one wrong
    file, and saying so twice trains the reader to skim.
    """
    wanted = expected_shapes(operator)
    if not wanted or not paths:
        return []

    slots = operator_inputs(operator)
    warnings: List[ShapeWarning] = []
    for index, shape in wanted:
        if index > len(paths):
            break
        path = str(paths[index - 1]).strip()
        if not path:
            continue
        detector = DETECTORS.get(shape)
        if detector is None:
            # A shape declared in the schema with no detector here. Logged
            # rather than raised: a schema that names a check this module has
            # not learned yet is a gap, and a gap must not stop a run.
            logger.debug("No detector for the %r shape (%s)", shape, operator)
            continue

        looks_right, found, instead = detector.detect(_read_facts(path))
        if looks_right is not False:
            continue

        role = slots[index - 1].role if index <= len(slots) else f"input {index}"
        # Led by the operator rather than by the slot's role, because the role
        # is already a restatement of ``expected`` — "Spectral coefficients
        # does not look like spectral coefficients" is what naming the slot
        # first produced. The role is still carried on the finding, for a
        # caller that wants to point at the widget.
        where = f" (input {index})" if len(slots) > 1 else ""
        warnings.append(ShapeWarning(
            slot=index, role=role, found=found, expected=detector.expected,
            message=(f"{operator} needs {detector.expected}, and this "
                     f"file{where} is {found}. "
                     f"{instead or detector.consequence}")))
    return warnings
