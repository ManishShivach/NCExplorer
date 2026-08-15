"""Do these two files actually go together?

``core/units.py`` asks whether one file holds what the operator will assume it
holds. This module asks the next question, and it only exists for operators that
take more than one file: whether the files are *comparable to each other*.

Three ways two operands can fail to match, in the order they bite. Every claim
below was measured on the installed CDO 2.6.3 with two 18x9 lonlat series.

* **Grid.** ``cdo fldcor a.nc smaller.nc out.nc`` aborts with "Grid size of the
  input field 'tas' do not match!", exit 1, and leaves no output behind. CDO
  does enforce this one, so the only thing gained by checking first is that the
  message names the two files and arrives before a subprocess.

* **Timestep count.** This is the one worth the module. ``fldcor`` and
  ``fldcovar`` handed a 6-step file and a 3-step file **warn on stdout and exit
  0**, writing a 3-step answer: the longer series is silently truncated and
  timestep 1 is paired with timestep 1 whatever dates they carry. A finished
  file full of plausible, wrong numbers that every surface in this application
  reports as a success. ``timcor``/``timcovar`` on the same pair fail properly
  (exit 1), so this is not a family-wide behaviour and cannot be inferred from
  the family's name.

* **Variable.** Nothing in CDO checks that the two files hold the same physical
  quantity. Correlating temperature against precipitation is a perfectly good
  run and a meaningless number.

**Which operators are checked is derived from the schema, not listed here.** An
operator qualifies when it declares two or more input slots and *no* slot
carries a ``recipe``. That is not a proxy for "the correlation family" — it is
the schema's own statement of the distinction that matters: a ``recipe`` is how
:class:`~.categories.OperatorInput` says "this input is built *from* another
one", which is exactly the case where the two files are *supposed* to differ in
length. ``ymonadd`` takes a series and a twelve-field climatology and must not
be told they disagree; ``fldcor`` and ``eca_etr`` take two raw series and must
be. So the rule reaches every present and future operator of the second shape
without naming any of them.

The one thing that is a list is :data:`SILENT_TRUNCATION`, and it is a list
because it records a *measurement* rather than a rule — which CDO operators exit
0 on mismatched lengths instead of failing. That is the same kind of fact, kept
the same way and for the same reason, as ``_SURPRISING_DEFAULTS`` and
``_ECA_WHOLE_SERIES`` in ``core/categories.py``: measured per operator against a
named build, with the command that measures it written down.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .categories import operator_inputs

logger = logging.getLogger(__name__)

#: Operators that accept two series of unequal length, exit 0, and quietly
#: truncate the result to the shorter of the two.
#:
#: Measured on CDO 2.6.3, 6 timesteps against 3::
#:
#:     cdo fldcor a.nc b3.nc out.nc
#:     -> "(Warning): Input streams have different number of time steps!"
#:        exit 0; cdo ntime out.nc -> 3
#:
#: and the controls that show it is these two rather than the situation::
#:
#:     cdo timcor a.nc b3.nc out.nc
#:     -> "(Warning): Input streams have different number of fields!"
#:        then "cdi error (stream_inq_field): record 2 not available at
#:        timestep 3", exit 1
#:
#: A mismatch is refused outright for these, and merely reported for everything
#: else, because everything else already fails loudly on its own.
SILENT_TRUNCATION = frozenset({"fldcor", "fldcovar"})

#: Severity of one finding. ``block`` means the run must not start without the
#: user saying so explicitly; ``warn`` means say it and carry on.
BLOCK = "block"
WARN = "warn"


@dataclass(frozen=True)
class PairingProblem:
    """One reason two of an operator's inputs do not go together."""

    severity: str       # BLOCK or WARN
    kind: str           # "grid" | "timesteps" | "variable"
    message: str        # the whole thing as one sentence, fit for a dialog

    @property
    def blocking(self) -> bool:
        return self.severity == BLOCK

    def __str__(self) -> str:
        return self.message


def _describe_grid(path: str) -> Optional[Tuple]:
    """A comparable fingerprint of one file's horizontal grid, or None.

    None means "could not be read", never "no grid": an unreadable file is not
    evidence of a mismatch, and the caller treats the two differently.

    The fingerprint is the *shape* of the horizontal axes plus their first and
    last values, rather than every coordinate. Two grids that agree on all four
    and disagree in between are not something CDO will let through either, and
    hashing whole coordinate arrays makes the check cost grow with the file for
    no gain in what it can catch.
    """
    try:
        import xarray as xr
    except ImportError:                     # pragma: no cover - hard dependency
        return None

    names = (("lon", "lat"), ("longitude", "latitude"), ("x", "y"), ("X", "Y"))
    try:
        with xr.open_dataset(path, decode_times=False) as dataset:
            for lon_name, lat_name in names:
                if lon_name in dataset.coords and lat_name in dataset.coords:
                    lon = dataset.coords[lon_name].values
                    lat = dataset.coords[lat_name].values
                    if lon.size == 0 or lat.size == 0:
                        continue
                    return (
                        int(lon.size), int(lat.size),
                        round(float(lon.flat[0]), 6), round(float(lon.flat[-1]), 6),
                        round(float(lat.flat[0]), 6), round(float(lat.flat[-1]), 6),
                    )
    except Exception:
        logger.debug("Could not read the grid of %s", path, exc_info=True)
    return None


def _timestep_count(path: str) -> Optional[int]:
    """How many timesteps ``path`` holds, or None when that cannot be read.

    A file with no time dimension returns None rather than 0 or 1. "This has no
    time axis" is not a length that can disagree with another length, and
    reporting it as one would refuse valid pairs — a single-timestep field
    written without a time dimension is a perfectly good second operand.
    """
    try:
        import xarray as xr
    except ImportError:                     # pragma: no cover - hard dependency
        return None

    try:
        with xr.open_dataset(path, decode_times=False) as dataset:
            for name in ("time", "t", "Time", "TIME"):
                if name in dataset.dims:
                    return int(dataset.sizes[name])
    except Exception:
        logger.debug("Could not read the time axis of %s", path, exc_info=True)
    return None


def pairs_must_match(operator: str) -> bool:
    """True when ``operator``'s inputs are two or more comparable data files.

    See the module docstring: two or more declared slots, none of which is built
    from another. An operator with undeclared slots answers False — not because
    its inputs need not match, but because nothing has said what they are, and
    inventing a constraint for an operator whose slots nobody has described is
    how a check starts refusing correct commands.

    ``addtrend``/``subtrend`` answer False, and that is the right answer for
    them: their second and third inputs are ``trend``'s two outputs, one
    timestep each by construction, and length-comparing a 1-step coefficient
    field against an N-step series would fire on every correct call.

    **Two things measured while declaring those two slots, neither acted on
    here.**

    *A "slots 2 and 3 must hold exactly one timestep" rule would be wrong.* CDO
    reads field 1 of those slots and ignores the rest — which is what
    ``o(t,x) = i1(t,x) - (i2(1,x) + i3(1,x)*t)`` says, and it holds in
    practice. On 2.6.3, against a 12-step series with a = 100 and b = 3::

        cdo cat afile bfile two.nc        # two.nc has 2 timesteps
        cdo subtrend series two.nc bfile out.nc
        -> exit 0, and the *correct* answer: field mean 0 at every timestep

    So a longer file in those slots is not a mistake to report, and a check
    that refused it would refuse a correct command.

    *The grid check is skipped for 74 operators CDO does enforce grids on.*
    ``recipe`` earns an exemption from the **timestep** comparison — that is the
    case the module docstring argues, and it is only about length. Nothing about
    being derived from another file makes two operands allowed to sit on
    different horizontal grids, and CDO agrees::

        cdo subtrend series a_r2x2.nc b_r2x2.nc out.nc
        -> "cdo subtrend (Abort): Grid size of the input field 'seq' do not
            match!", exit 1
        cdo ymonadd series ymonavg_r2x2.nc out.nc
        -> the same abort, from a recipe operator in a different section

    Splitting the two halves — grid for every declared multi-slot operator,
    timesteps only for the recipe-free ones — is a rule that can be stated
    without naming an operator, so it belongs in this function rather than in a
    list. It is deliberately *not* done as part of the Regression change,
    because it would newly check 74 operators whose grid behaviour has not been
    measured, and this module's own standard is that a check is measured before
    it is switched on. ``collgrid`` is the reason that is not a formality: its
    inputs are pieces of one grid and are *supposed* to differ, and it is only
    out of range today because ``nin == -1`` collapses it to a single slot.
    """
    slots = operator_inputs(operator)
    if len(slots) < 2:
        return False
    return not any(slot.recipe for slot in slots)


def check_pairing(operator: str, paths: Sequence[str], *,
                  variable: Optional[str] = None) -> List[PairingProblem]:
    """Every way ``paths`` fail to go together as ``operator``'s inputs.

    Empty means "they match" or "there was nothing to check"; the two are not
    distinguished, because the caller has nothing different to do about them.

    Ordered grid, then timesteps, then variable — the order in which they bite,
    so the first line a user reads is the one that stops the run.
    """
    if not pairs_must_match(operator):
        return []
    files = [str(path) for path in paths if str(path).strip()]
    if len(files) < 2:
        return []

    problems: List[PairingProblem] = []
    slots = operator_inputs(operator)

    def role(index: int) -> str:
        if index < len(slots) and slots[index].role:
            return slots[index].role
        return f"input {index + 1}"

    # -- grid -------------------------------------------------------------
    grids = [_describe_grid(path) for path in files]
    reference = grids[0]
    if reference is not None:
        for index in range(1, len(files)):
            other = grids[index]
            if other is None or other == reference:
                continue
            problems.append(PairingProblem(
                BLOCK, "grid",
                f"The two inputs are on different horizontal grids: "
                f"{role(0)} is {reference[0]}x{reference[1]} and {role(index)} "
                f"is {other[0]}x{other[1]}. {operator} needs them identical and "
                f"will abort with \"Grid size of the input field ... do not "
                f"match!\". Regrid one of them first, e.g. "
                f"cdo remapbil,<grid> infile outfile."))

    # -- timestep count ---------------------------------------------------
    counts = [_timestep_count(path) for path in files]
    first = counts[0]
    if first is not None:
        for index in range(1, len(files)):
            other = counts[index]
            if other is None or other == first:
                continue
            if operator in SILENT_TRUNCATION:
                problems.append(PairingProblem(
                    BLOCK, "timesteps",
                    f"{role(0)} has {first} timesteps and {role(index)} has "
                    f"{other}. {operator} does not fail on this — it warns, "
                    f"exits 0, and silently truncates the answer to the shorter "
                    f"of the two, pairing timestep 1 with timestep 1 whatever "
                    f"they are dated. The result would look like a success and "
                    f"be computed from {min(first, other)} timesteps. Select a "
                    f"matching span first, e.g. "
                    f"cdo seltimestep,1/{min(first, other)} infile outfile."))
            elif 1 in (first, other):
                # CDO's documented broadcast rule: "One of the input files can
                # contain only one timestep or one field", which Arith and Comp
                # both state and which is the whole of the anomaly idiom —
                # ``cdo sub infile -timmean infile outfile``. Warning about the
                # commonest correct two-file command in the application would
                # train the user to ignore this check, so a length of exactly
                # one is not a mismatch here.
                #
                # Deliberately below the truncating branch and not above it: for
                # ``fldcor`` a one-timestep operand is not a broadcast, it is
                # the worst case of the truncation — six timesteps in, one out,
                # exit 0. That still blocks.
                logger.debug("%s: broadcasting %d timesteps against %d",
                             operator, first, other)
            else:
                problems.append(PairingProblem(
                    WARN, "timesteps",
                    f"{role(0)} has {first} timesteps and {role(index)} has "
                    f"{other}. {operator} expects them to match, and neither "
                    f"holds the single field CDO would broadcast over the "
                    f"other, so this will not compare what you meant."))

    # -- the variable ------------------------------------------------------
    # Warn only. A file may legitimately name its variable differently in each
    # half of a pair — two models, two conventions — and CDO pairs by position
    # rather than by name, so a rename is not an error. It is worth saying
    # because the commonest cause is having picked the wrong second file.
    #
    # Slots declared ``holds_variable=False`` are skipped, because for them the
    # named variable is not merely allowed to be absent, it is *supposed* to be:
    # ``intlevel3d``'s second input is a 3D vertical coordinate, so every
    # correct call has a second file holding zcoord rather than the data
    # variable, and warning about it would fire on every correct call.
    if variable:
        from .units import data_variables

        for index, path in enumerate(files):
            if index < len(slots) and not slots[index].holds_variable:
                continue
            names = data_variables(path)
            if names and variable not in names:
                problems.append(PairingProblem(
                    WARN, "variable",
                    f"{role(index)} does not hold a variable called "
                    f"'{variable}' — it holds {', '.join(names)}. CDO pairs the "
                    f"two files by position rather than by name, so this will "
                    f"run and compare whatever is there."))

    return problems


def blocking(problems: Sequence[PairingProblem]) -> List[PairingProblem]:
    """Just the findings that should stop a run."""
    return [problem for problem in problems if problem.blocking]
