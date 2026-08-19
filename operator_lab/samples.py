# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""The input files a sweep runs against.

The harness generates its own with CDO rather than pointing at a fixed path on
someone's Desktop — the previous bulk tester hard-coded ``/tmp/cdo_samples`` and
stopped working the moment that directory went away. Generated samples make a
run reproducible on any machine that has CDO at all, which is the same
machine that can run the operators.

Supplying real files instead is a first-class option: :meth:`SampleSet.from_files`
takes whatever the user picked in the GUI and reads the variable name back out
of it, so parameters like ``selname,<var>`` name a variable the file has rather
than one the generator would have made.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from ncexplorer_toolkit.core.categories import OPERATOR_SCHEMA, operator_inputs

from .profiles import BRACKET_YEARS, PARAMETER_DEFAULTS, SAMPLE_GRID, SAMPLE_VARIABLE

logger = logging.getLogger(__name__)

#: Daily steps in the generated series: two full years, so ``yearmean`` has two
#: years to average, ``seasmean`` has every season, and ``ymonmean`` has twelve
#: months with two samples each. Anything shorter turns a real failure into an
#: indistinguishable "not enough time steps".
SERIES_STEPS = 730
SERIES_START = "2000-01-01"

#: Pressure levels in the vertical sample, and how many time steps it carries.
#: Ten is enough for a vertical operator to do something and small enough that
#: building it costs nothing.
SAMPLE_LEVELS = (1000, 850, 500)
LEVELS_STEPS = 10

#: Operator prefixes that want the multi-level sample instead of the flat one.
#: A vertical operator against a single-level file fails with "no vertical
#: levels", which says nothing about the operator; against this one, a failure
#: is about the operator.
_VERTICAL_PREFIXES = (
    "ml2", "pl2", "ap2", "hl2", "intlevel", "vert", "zaxis", "genlevelbounds",
)

#: The same need, for operators whose names share no prefix. All three reduce a
#: column to one horizontal field and say "No processable variable found!" when
#: there is no column to reduce.
_VERTICAL_OPERATORS = frozenset({"bottomvalue", "topvalue", "isosurface"})

#: ``operator -> the field sample it needs``, keyed into :attr:`SampleSet.fields`
#: and consulted before the prefix rules.
#:
#: These operators do not want *more* data, they want a *particular variable*:
#: sea water salinity, a wind pair, cloud cover as GRIB parameter 223. Against
#: the ordinary random field every one of them aborts by name — "Sea water
#: salinity not found!", "u not found!", "No spectral data found!" — which is a
#: statement about the sample and not about the operator.
_FIELD_SAMPLE_FOR = {
    # sao (code 5) + tho (code 2) + to (code 20), as MPIOM writes them.
    "adipot": "ocean",
    "adisit": "ocean",
    "rhopot": "ocean",
    # A variable whose standard_name is air_pressure, on model levels.
    "ap2pl": "airpressure",
    "ap2plx": "airpressure",
    "ap2hl": "airpressure",
    "ap2hlx": "airpressure",
    # The same idea one standard name over: gh2hl wants the 3D geometric
    # height, identified as geometric_height_at_full_level_center.
    "gh2hl": "geoheight",
    "gh2hlx": "geoheight",
    # u and v, by name and by GRIB code 33/34.
    "uv2dv_cfd": "wind",
    "uv2vr_cfd": "wind",
    # …and the same pair on a rotated pole, which is all rotuvb will accept.
    # The other four are routed here so they get as far as their real problem
    # (CDO 2.6.0 never dispatches them) rather than stopping at "u not found".
    "rotuvb": "wind_rotated",
    "rotuvN": "wind_rotated",
    "rotuvNorth": "wind_rotated",
    "projuvLatLon": "wind_rotated",
    "uvDestag": "wind_rotated",
    # The whole Transformation section used to be two entries here — sp2sp and
    # spcut — and is now none. Its twelve operators declare input slots whose
    # ``key`` names the sample they need, and ``_slot_file`` resolves those
    # against ``fields``, so the routing comes from the schema. Removed rather
    # than left as duplicates: two tables saying the same thing is two tables
    # that can disagree, and ``_declared_inputs_for`` runs first, so these
    # entries were already dead.
    # A zonal mean of v-velocity (code 132), which is the only thing mastrfu
    # will take: "Grid must be a zonal mean!".
    "mastrfu": "zonal_v",
    # Cloud cover as parameter 223 *on pressure levels* — the operator checks
    # both and names both.
    "cloudlayer": "cloud",
    # The ICON ocean column variables: depth_c for the zs2z* pair, and
    # prism_thick_c / stretch_c / zos for zsdepth.
    "zs2zl": "icon_ocean",
    "zs2zlx": "icon_ocean",
    "zsdepth": "icon_ocean",
    # Complex-valued data, which CDO will make for itself once it is allowed to
    # write NetCDF4 — see ``harness._OUTPUT_FORMAT_FOR``. ``fourier`` has left
    # this group: it now declares a slot keyed ``complex``, which routes it to
    # the same file through the schema. The other five have no declared slots,
    # so they still need naming here.
    "conj": "complex",
    "im": "complex",
    "re": "complex",
    "complextopol": "complex",
    "complextorect": "complex",
}

#: ``operator -> the pair of field samples that fill its two input slots``.
#: ``mrotuvb`` rejects a file holding both components ("More than one variable
#: found"), so u and v arrive as one file each.
_FIELD_PAIR_FOR = {
    "mrotuvb": ("wind_u", "wind_v"),
    # infile1 = the 3D data variables, infile2 = the 3D vertical source
    # coordinate, in that order and only that order: swapping them still exits
    # 0 and writes a file of missing values. See the intlevel3d input-slot
    # declaration in core/categories.py for the measurement.
    "intlevel3d": ("levels3d_data", "levels3d_coord"),
    "intlevelx3d": ("levels3d_data", "levels3d_coord"),
}

#: ``prefix -> CDO operator that builds the companion``, longest prefix first.
#:
#: The two-input members of the ``ymon``/``yseas``/``yday``/``yhour``/``year``
#: families do not take two data files. The second is a *statistics* file with
#: one field per month, season, day or year, and handing them a second raw
#: series produces "January already allocated! The second input file must
#: contain..." — 33 failures that say nothing about the operators and
#: everything about the inputs they were given.
_COMPANION_FOR = (
    ("yseas", "yseasmean"),
    ("yhour", "yhourmean"),
    ("ymon", "ymonmean"),
    # After "ymon" so it cannot shadow it. monadd and its three siblings want a
    # file with one field per month of the series, not one field per calendar
    # month, and a raw second series gets them "Timestep 2 has wrong date!".
    ("mon", "monmean"),
    ("yday", "ydaymean"),
    # After "yday" for the same reason "mon" follows "ymon": a bare "day"
    # prefix would otherwise claim ydayadd and its three siblings. dayadd wants
    # one field per day of the *series*, and was the one family in this table
    # with no entry at all — so it was handed the raw second series and passed,
    # which tested that two files with identical dates can be added rather than
    # the documented "time series against one timestep with the same day".
    ("day", "daymean"),
    ("year", "yearmean"),
    ("anomaly", "ymonmean"),   # CDO aliases anomaly to ymonsub
)

#: Operators whose *primary* input must already be monthly. ``yearmonmean`` and
#: friends weight each month by its length, so a daily series makes them abort
#: with "Month does not change!".
_WANT_MONTHLY = frozenset({
    "seasmonavg", "seasmonmean", "yearmonavg", "yearmonmean",
})

#: Operators whose *primary* input must already be yearly. The exact mirror of
#: ``_WANT_MONTHLY`` one period up: Timyearstat weights each year by its length,
#: so it needs one timestep per year and aborts on anything finer.
#:
#: Measured on 2.6.3 against the sweep's own daily series:
#:
#:     cdo timyearmean series.nc out.nc
#:       -> (Warning)    last timestep:  2000-01-01T00:00:00
#:          (Warning) current timestep:  2000-01-02T00:00:00
#:          (Abort): Years does not change!
#:
#: which is the same shape of failure ``_WANT_MONTHLY`` was written for, with
#: "Years" in place of "Month". Both operators passed the sweep as failures
#: before this entry, and the failure was the sample rather than the operator.
_WANT_YEARLY = frozenset({"timyearavg", "timyearmean"})

#: The two Ensval operators, which need a reference file **and at least two
#: ensemble members** — three files, where ``nin == -1`` otherwise hands out two.
#:
#: ``cdo ensbrs,x rfile infiles obase`` spends its first input on the reference,
#: so the ordinary pair leaves a one-member ensemble, and the Brier score's
#: decomposition into reliability, resolution and uncertainty is not defined
#: over one member. CDO says so, eventually:
#:
#:     cdo ensbrs (Abort): Internal error - normalization constraint of
#:                         problem not fulfilled
#:
#: Measured: one member aborts at every threshold tried (0.3, 0.5, 0.7, and one
#: above the data range); two members exit 0 at all of them. So it is the member
#: count and not the parameter, which is worth recording because the message
#: points at neither.
#:
#: ``enscrps`` is here for consistency rather than for a failure — it exits 0 on
#: one member, and a CRPS "averaged over field members" computed from a single
#: member is a number with nothing in it. Both are validation tools; a sweep
#: that runs them on a degenerate ensemble is not testing what they are for.
#:
#: This surfaced when the series gained real variation over time: with the old
#: constant-in-time sample every timestep produced the same forecast
#: probability, and the constraint CDO checks was satisfied trivially by a
#: one-member ensemble. The operator was always being called wrongly; the sample
#: was hiding it.
_WANT_ENSEMBLE_MEMBERS = frozenset({"ensbrs", "enscrps"})

#: The four daily series the climate indices are defined over, and the CDO
#: expression that turns the existing random field into each one.
#:
#: The units are the point. ``eca_su`` reads its field as Kelvin and its
#: threshold as degrees Celsius, and ``eca_pd`` wants millimetres rather than a
#: rate — so a sample carrying neither unit tests the operator's plumbing and
#: nothing about whether the app can be used correctly. ``tn``/``tx`` are the
#: mean shifted by ±5 K so that TN ≤ TG ≤ TX holds, which is what ``eca_etr``
#: and the wave indices assume of their two inputs.
_CLIMATE_BASES = (
    ("tg", ["-setunit,K", "-setname,tg", "-addc,273.15", "-mulc,40",
            "-subc,0.5"]),
    ("tn", ["-setunit,K", "-setname,tn", "-subc,5"]),
    ("tx", ["-setunit,K", "-setname,tx", "-addc,5"]),
    ("rr", ["-setunit,mm", "-setname,rr", "-mulc,30"]),
)

#: The land-water mask ``eca_gsl`` takes as its second input. Declared with no
#: recipe in the schema — it is not derived from the temperature series at all —
#: so it is the one companion built by hand rather than from a recipe.
_LANDMASK_KEY = "landmask"

#: Operators whose two inputs have to *bracket* a year rather than merely differ.
#: ``intyear`` interpolates between the year of infile1 and the year of infile2,
#: so the two-year series handed to it twice leaves nothing in between and it
#: aborts with "Year 2000 out of bounds (first year 2001; last year 2001)!".
_BRACKETING = ("intyear", ("bracket_early", "bracket_late"))


class SampleError(RuntimeError):
    """Raised when the sample inputs could not be built."""


@dataclass
class SampleSet:
    """The files a run uses, plus the variable name inside them.

    ``series`` is the workhorse — a daily field over two years. ``extra`` holds
    the alternates handed to two- and three-input operators; they are separate
    files with different values so an operator that is supposed to combine two
    fields cannot pass by accident on two identical ones. ``companions`` holds
    the per-month/season/day/hour/year statistics that the ``ymon``/``yseas``
    families require as their *second* input rather than a second data file.
    """

    series: Path
    levels: Optional[Path] = None
    extra: List[Path] = field(default_factory=list)
    variable: str = SAMPLE_VARIABLE
    generated: bool = True
    #: ``ymonmean`` → the file holding that statistic, when one was built.
    companions: Dict[str, Path] = field(default_factory=dict)
    #: Files that fill a *parameter* slot rather than an input slot: remap
    #: weights, a reduction mask. Kept apart from ``companions`` because the
    #: harness reaches for them at a different point — when it is resolving
    #: ``spec.params``, not when it is choosing input files — and because a
    #: parameter of kind ``file`` otherwise gets a synthesised text stub that
    #: every NetCDF-reading operator rejects.
    parameter_files: Dict[str, Path] = field(default_factory=dict)
    #: ``ocean`` / ``wind`` / ``spectral`` … → the file carrying that field, for
    #: the operators in :data:`_FIELD_SAMPLE_FOR`.
    fields: Dict[str, Path] = field(default_factory=dict)
    #: ``OperatorInput.key`` → the file that slot wants: ``tg``, ``rr``,
    #: ``tgn10``, ``landmask``, ``tx_runmin`` … Keyed by the schema's own slug
    #: rather than by operator, because two indices that want the same
    #: climatology must be given the same file and not two guesses at it.
    climate: Dict[str, Path] = field(default_factory=dict)

    def inputs_for(self, operator: str, nin: int) -> List[Path]:
        """The ``nin`` input paths for one operator, longest-lived first.

        ``nin == -1`` is CDO's "any number"; two files exercise the merging
        operators that share that signature better than one, and every operator
        that accepts many also accepts two.
        """
        primary = self._primary_for(operator)
        pool = [primary] + [path for path in self.extra if path != primary]

        if nin == 0:
            return []
        # Before every other rule: an operator whose input slots are declared
        # has said exactly what each one must hold, and no prefix guess beats
        # a declaration.
        declared = self._declared_inputs_for(operator, nin)
        if declared is not None:
            return declared
        bracket = self._bracketing_for(operator)
        if bracket is not None:
            return bracket
        pair = self._field_pair_for(operator)
        if pair is not None:
            return pair
        if nin == 2:
            companion = self._companion_for(operator)
            if companion is not None:
                return [primary, companion]
        if nin == -1:
            # A reference file plus a real ensemble, not a reference plus one.
            wanted = 3 if operator in _WANT_ENSEMBLE_MEMBERS else 2
            return pool[:wanted] if len(pool) >= wanted else pool[:1]
        if nin <= len(pool):
            return pool[:nin]
        # More inputs than distinct samples: repeat the last one rather than
        # refuse. An operator that needs four files still gets four.
        return pool + [pool[-1]] * (nin - len(pool))

    def _slot_file(self, key: str) -> Optional[Path]:
        """The file one ``OperatorInput.key`` names, or None if it is not built.

        Climatologies first, then the two plain series. ``series`` and
        ``series2`` are not climate companions and never will be — they are the
        schema's way of saying "an ordinary data series, and another one like
        it", which is what the Correlation section's four operators take and
        what ``_build`` already generates: ``series`` and ``extra[0]`` are built
        on the same grid and the same time axis from different random seeds, so
        they are a genuine pair rather than one file handed over twice.

        Resolving them here rather than leaving them unresolved matters because
        of the all-or-nothing rule below: an unresolvable key sends the whole
        operator back to the prefix guesses, and the prefix guesses know nothing
        about ``fldcor``.

        ``fields`` is consulted too, and that one line is what lets the
        Transformation section route itself. Its slots declare ``key="spectral"``,
        ``"gaussian"``, ``"wind_gaussian"``, ``"divergence_vorticity"`` and
        ``"complex"`` — the names :func:`_build_fields` already builds under —
        so twelve operators are routed by the schema rather than by twelve more
        lines in :data:`_FIELD_SAMPLE_FOR`. That is the shape the module
        docstring argues for and the one ``_declared_companions`` already uses;
        the hand-kept table stays for the operators whose requirement is a
        *variable* nobody has declared a slot for.
        """
        if key in self.climate:
            return self.climate[key]
        if key in self.fields:
            return self.fields[key]
        if key == "series":
            return self.series
        if key == "series2":
            return self.extra[0] if self.extra else None
        return None

    def _declared_inputs_for(self, operator: str, nin: int) -> Optional[List[Path]]:
        """One file per declared input slot, or None when they are not all built.

        This is the whole point of ``OperatorInput.key``. ``eca_cwfi`` takes two
        files, and until this existed the harness picked the second one by
        matching the operator's *name* against a prefix table — which knew
        nothing of climatologies, so every two-input climate index in the sweep
        was passing on a file it should have rejected. The pass was real: CDO
        ran, wrote output and exited 0. The numbers were nonsense.

        All-or-nothing on purpose. A partially-built set would send an operator
        its real first input beside a wrong second one, which is exactly the
        silent-wrong-answer this replaces; falling back to the ordinary samples
        at least fails visibly.
        """
        slots = operator_inputs(operator)
        if not slots or not any(slot.key for slot in slots):
            return None
        paths = [self._slot_file(slot.key) for slot in slots[:max(nin, 1)]]
        if any(path is None for path in paths):
            logger.debug("Declared inputs incomplete for %s; falling back",
                         operator)
            return None
        return [path for path in paths if path is not None]

    def _primary_for(self, operator: str) -> Path:
        # A declared first slot outranks the prefix rules for the same reason
        # the rest of the slots do: it names the variable, not merely a shape.
        # Climate keys only, deliberately: this branch exists so a declared
        # slot's *variable* beats a guess from the operator's name, and only a
        # climatology key carries that. ``series``/``series2`` are resolved in
        # :meth:`_slot_file` for the slot list, but must not be resolved here —
        # they name the ordinary sample, which is what the final line of this
        # method already returns, so honouring them here would change nothing
        # except to jump the ``_FIELD_SAMPLE_FOR`` and vertical rules below.
        slots = operator_inputs(operator)
        if slots and slots[0].key and slots[0].key in self.climate:
            return self.climate[slots[0].key]

        # A field sample wins over every other rule: an operator listed there
        # needs a named variable, and no amount of the right *shape* substitutes.
        # Missing (it would not build) falls through, so the operator fails on
        # ordinary data with the message that says what it wanted.
        wanted = _FIELD_SAMPLE_FOR.get(operator)
        if wanted is not None and wanted in self.fields:
            return self.fields[wanted]
        if operator in _WANT_MONTHLY:
            return self.companions.get("monmean", self.series)
        if operator in _WANT_YEARLY:
            return self.companions.get("yearmean", self.series)
        if operator.startswith(_VERTICAL_PREFIXES) or operator in _VERTICAL_OPERATORS:
            return self.levels or self.series
        return self.series

    def _companion_for(self, operator: str) -> Optional[Path]:
        """The statistics file this operator wants as its second input."""
        for prefix, statistic in _COMPANION_FOR:
            if operator.startswith(prefix):
                return self.companions.get(statistic)
        return None

    def _bracketing_for(self, operator: str) -> Optional[List[Path]]:
        """The single-year pair for an operator that interpolates between two.

        None unless both halves were built, so a pair that failed to generate
        leaves the operator with the ordinary inputs and a clear failure rather
        than a crash here.
        """
        name, keys = _BRACKETING
        if operator != name:
            return None
        pair = [self.companions.get(key) for key in keys]
        return [path for path in pair if path is not None] if all(pair) else None

    def _field_pair_for(self, operator: str) -> Optional[List[Path]]:
        """The two single-field files this operator wants, one per input slot."""
        keys = _FIELD_PAIR_FOR.get(operator)
        if keys is None:
            return None
        pair = [self.fields.get(key) for key in keys]
        return [path for path in pair if path is not None] if all(pair) else None

    def describe(self) -> str:
        source = "generated" if self.generated else "supplied"
        parts = [f"{self.series.name} ({source})"]
        if self.levels is not None:
            parts.append(self.levels.name)
        parts.extend(path.name for path in self.extra)
        if self.companions:
            parts.append(f"+{len(self.companions)} statistics companions")
        return ", ".join(parts)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_files(cls, paths: Sequence[Path], binary: str = "cdo") -> "SampleSet":
        """Use the caller's own files.

        The first is the primary input; the rest fill the multi-input slots.
        The variable name is read back out of the first file so the parameter
        defaults refer to something it contains — a run against a rainfall file
        should select ``RAINFALL``, not the generator's ``random``.
        """
        resolved = [Path(path).expanduser().resolve() for path in paths]
        missing = [str(path) for path in resolved if not path.is_file()]
        if missing:
            raise SampleError("Sample files not found: " + ", ".join(missing))
        if not resolved:
            raise SampleError("No sample files given")

        return cls(
            series=resolved[0],
            levels=None,
            extra=resolved[1:],
            variable=detect_variable(resolved[0], binary) or SAMPLE_VARIABLE,
            generated=False,
        )

    @classmethod
    def generate(cls, workdir: Path, binary: str = "cdo",
                 reuse: bool = True) -> "SampleSet":
        """Build the synthetic samples under ``workdir``.

        ``reuse`` keeps files that are already there, which matters because
        every run of the GUI would otherwise rebuild ~2 MB of NetCDF before it
        could start.
        """
        # Absolute, always: operators run with CDO's temp directory as their
        # working directory, so a relative sample path would resolve against
        # the wrong place and every operator would fail "Open failed".
        workdir = Path(workdir).expanduser()
        workdir.mkdir(parents=True, exist_ok=True)
        workdir = workdir.resolve()

        series = workdir / "sample_series.nc"
        second = workdir / "sample_series_b.nc"
        third = workdir / "sample_series_c.nc"
        levels = workdir / "sample_levels.nc"

        if not (reuse and all(p.is_file() for p in (series, second, third, levels))):
            _build(binary, series, second, third, levels)

        companions = _build_companions(binary, workdir, series, reuse)
        companions.update(_build_bracketing(binary, workdir, series, second, reuse))
        return cls(series=series, levels=levels, extra=[second, third],
                   variable=SAMPLE_VARIABLE, generated=True,
                   companions=companions,
                   parameter_files=_build_parameter_files(
                       binary, workdir, series, reuse),
                   fields=_build_fields(binary, workdir, series, second, levels,
                                        reuse),
                   climate=_build_climate(binary, workdir, series, reuse))


#: The two modes that give the series its variation *over time*, as
#: ``(amplitude, radians per day)``. One cycle a year and one every ten weeks,
#: so a two-year series carries two full turns of the first and about ten of
#: the second — enough separation between the two eigenvalues for a solver to
#: tell the modes apart.
#:
#: The amplitudes, with ``_NOISE_AMPLITUDE``, sum to 0.5, which is what keeps
#: the field inside the [0, 1) band ``-random`` used to produce on its own. That
#: band is load-bearing downstream: ``_CLIMATE_BASES`` turns it into Kelvin with
#: ``-mulc,40 -addc,273.15`` and into millimetres with ``-mulc,30``, so a field
#: that strayed negative would hand ``eca_pd`` negative rainfall.
_SERIES_MODES = ((0.20, 0.0172), (0.15, 0.0861))

#: Amplitude of the time-constant spatial noise the modes are added to.
_NOISE_AMPLITUDE = 0.15


def _build(binary: str, series: Path, second: Path, third: Path, levels: Path) -> None:
    """Run the four generator commands, or explain why they could not run."""
    for path, seed in ((series, 1), (second, 2), (third, 3)):
        # Three fields on the same grid and time axis, differing in value.
        _build_series(binary, path, seed)

    level_steps = f"-seltimestep,1/{LEVELS_STEPS}"
    merge: List[str] = ["-O", "-f", "nc", "merge"]
    for index, level in enumerate(SAMPLE_LEVELS, start=1):
        merge += [f"-setlevel,{level}", f"-mulc,{index}", *level_steps.split(), str(series)]
    merge.append(str(levels))
    _run(binary, merge)


def _build_series(binary: str, path: Path, seed: int) -> None:
    """One daily series that varies in space **and** in time.

    The series used to be ``-duplicate,730 -random,<grid>,<seed>``: one random
    field repeated at every timestep. That is the ``-enlarge`` trap one axis
    over — the field varied across the grid and was *identical* at all 730
    steps, so its variance over time was exactly zero.

    The cost was not theoretical. ``cdo sub series -timmean series`` is
    identically zero on such a series, so the anomaly companion was an all-zero
    file, and the whole EOFs section decomposed a zero matrix: the one-sided
    jacobi solver reported "Setting Matrix and Eigenvalues to 0 before return"
    on every column pair and the five runnable ``eof*`` operators were reported
    as failures. Measured against a field with real temporal structure the same
    solver converges on the first try, so what looked like a CDO defect — and is
    recorded as one in ``_build_eof_companions`` and in the EOFs fix plan — was
    the sample all along.

    The construction is a spatial field plus two travelling modes::

        field(t, x) = 0.5 + noise(x) + Σ  a_k · sin(rate_k · t) · pattern_k(x)

    ``noise`` is the old time-constant random field, kept so that nothing which
    passed on spatial variation alone loses it. Each mode multiplies a
    one-timestep spatial pattern by a 730-step time series, which CDO does by
    broadcasting the shorter stream — the "Filling up stream2 by copying the
    first timestep" notice — so no mode costs more than one extra pass. The two
    patterns are deliberately unalike: ``-topo`` is real orography and carries
    large smooth structure, the second is another random draw, so the leading
    eigenvectors are distinguishable rather than two versions of the same noise.
    """
    scratch: List[Path] = []

    def part(name: str) -> Path:
        target = path.parent / f"_{path.stem}_{name}.nc"
        scratch.append(target)
        return target

    try:
        # The time-constant half, which is what the sample used to be entirely.
        total = part("noise")
        _run(binary, ["-O", "-f", "nc", f"-duplicate,{SERIES_STEPS}",
                      f"-mulc,{_NOISE_AMPLITUDE * 2}", "-subc,0.5",
                      f"-random,{SAMPLE_GRID},{seed}", str(total)])

        for index, (amplitude, rate) in enumerate(_SERIES_MODES):
            # sin over the timestep index, on the 1x1 grid -for produces. The
            # variable -for writes is called `seq`, not `for`.
            when = part(f"time{index}")
            _run(binary, ["-O", "-f", "nc", f"-expr,seq={amplitude}*sin(seq*{rate});",
                          f"-for,1,{SERIES_STEPS}", str(when)])

            # -topo for the first mode, a second random draw for the rest, each
            # normalised to roughly [-1, 1] so the amplitude above is the whole
            # story about how far this mode moves the field.
            where = part(f"space{index}")
            pattern = (["-mulc,0.000125", f"-topo,{SAMPLE_GRID}"] if index == 0 else
                       ["-mulc,2", "-subc,0.5", f"-random,{SAMPLE_GRID},{seed + 10 + index}"])
            _run(binary, ["-O", "-f", "nc", *pattern, str(where)])

            mode = part(f"mode{index}")
            _run(binary, ["-O", "-f", "nc", "mul", f"-enlarge,{SAMPLE_GRID}",
                          str(when), str(where), str(mode)])

            combined = part(f"sum{index}")
            _run(binary, ["-O", "-f", "nc", "add", str(total), str(mode), str(combined)])
            total = combined

        # The name and the time axis are set last, in their own pass: `add` is a
        # two-input operator and a -setname chained in front of one is read as a
        # third input ("No Operators with missing input left").
        _run(binary, ["-O", "-f", "nc", f"-setname,{SAMPLE_VARIABLE}",
                      f"-settaxis,{SERIES_START},00:00:00,1day", "-addc,0.5",
                      str(total), str(path)])
    finally:
        for target in scratch:
            target.unlink(missing_ok=True)


#: The statistics companions, each built from the main series by the CDO
#: operator it is named after.
_COMPANIONS = ("ymonmean", "yseasmean", "ydaymean", "yhourmean", "yearmean",
               "monmean", "daymean")


def _build_companions(binary: str, workdir: Path, series: Path,
                      reuse: bool) -> Dict[str, Path]:
    """Derive the per-month/season/day/hour/year statistics files.

    A companion that will not build is left out rather than raising: it costs
    the handful of operators that wanted it a clear failure, which is a far
    better outcome than no sweep at all.
    """
    built: Dict[str, Path] = {}
    for statistic in _COMPANIONS:
        path = workdir / f"sample_{statistic}.nc"
        if not (reuse and path.is_file()):
            try:
                _run(binary, ["-O", "-f", "nc", statistic, str(series), str(path)])
            except SampleError as exc:
                logger.warning("Could not build the %s companion: %s", statistic, exc)
                continue
        built[statistic] = path
    return built


def _build_bracketing(binary: str, workdir: Path, series: Path, second: Path,
                      reuse: bool) -> Dict[str, Path]:
    """The two single-year files ``intyear`` interpolates between.

    Three time steps each rather than a whole year: the operator cares about the
    year on the time axis, not how much of it there is, and a short file keeps
    generation cheap. Built from two different series so the interpolation has
    something to interpolate.
    """
    early_year, late_year = BRACKET_YEARS
    plan = (
        ("bracket_early", series, early_year),
        ("bracket_late", second, late_year),
    )

    built: Dict[str, Path] = {}
    for key, source, year in plan:
        path = workdir / f"sample_{key}.nc"
        if not (reuse and path.is_file()):
            try:
                _run(binary, [
                    "-O", "-f", "nc",
                    f"-settaxis,{year}-01-01,00:00:00,1day",
                    "-seltimestep,1/3", str(source), str(path),
                ])
            except SampleError as exc:
                logger.warning("Could not build the %s sample: %s", key, exc)
                continue
        built[key] = path
    return built


#: Descriptions CDO can only be handed as a file. Written next to the samples so
#: a run is reproducible from the directory alone and a reader can see exactly
#: what "on pressure levels" or "on a rotated pole" was taken to mean.
_DESCRIPTIONS = {
    "grid_rotated.txt": """\
gridtype  = projection
gridsize  = 648
xsize     = 36
ysize     = 18
xname     = rlon
xunits    = "degrees"
yname     = rlat
yunits    = "degrees"
xfirst    = -17.5
xinc      = 1.0
yfirst    = -8.5
yinc      = 1.0
grid_mapping = rotated_pole
grid_mapping_name = rotated_latitude_longitude
grid_north_pole_longitude = -162.
grid_north_pole_latitude = 39.25
""",
    "zaxis_pressure.txt": """\
zaxistype = pressure
size      = 3
levels    = 100000 85000 50000
""",
    "zaxis_model.txt": """\
zaxistype = generic
size      = 3
levels    = 1 2 3
""",
}


def _build_fields(binary: str, workdir: Path, series: Path, second: Path,
                  levels: Path, reuse: bool) -> Dict[str, Path]:
    """The geophysical fields that :data:`_FIELD_SAMPLE_FOR` routes operators to.

    Everything is derived from the random fields already built, by renaming and
    re-coding them into the variables each operator looks for. The values are
    nonsense as physics — salinity here is a random number plus 34 — and that is
    deliberate: these exist to exercise a code path, not to be realistic, and
    keeping them small is what holds sample generation to a few seconds.

    Built in order, because several are built from earlier ones. A field that
    will not build is logged and left out; its operators then run against the
    ordinary sample and fail with the message naming what they wanted, which is
    the same outcome as before this function existed.
    """
    for name, body in _DESCRIPTIONS.items():
        path = workdir / name
        if not (reuse and path.is_file()):
            path.write_text(body)

    rotated = str(workdir / "grid_rotated.txt")
    pressure = str(workdir / "zaxis_pressure.txt")
    model = str(workdir / "zaxis_model.txt")
    three = "-seltimestep,1/3"
    five = "-seltimestep,1/5"

    built: Dict[str, Path] = {}

    def path_for(key: str) -> str:
        return str(workdir / f"sample_{key}.nc")

    # (key, argv without the output path). Order matters: "wind_rotated" reads
    # "wind", and "spectral" reads the Gaussian field.
    plan: List = [
        ("ocean", ["-O", "-f", "nc", "merge",
                   "-setattribute,sao@standard_name=sea_water_salinity",
                   "-setunit,psu", "-setname,sao", "-setcode,5",
                   "-addc,34", "-mulc,0.1", three, str(levels),
                   "-setunit,C", "-setname,tho", "-setcode,2",
                   "-addc,5", "-mulc,2", three, str(levels),
                   "-setunit,C", "-setname,to", "-setcode,20",
                   "-addc,6", "-mulc,2", three, str(levels)]),
        ("airpressure", ["-O", "-f", "nc", "merge",
                         "-setattribute,pfull@standard_name=air_pressure",
                         "-setunit,Pa", "-setname,pfull", f"-setzaxis,{model}",
                         "-addc,50000", "-mulc,10000", three, str(levels),
                         "-setname,ta", f"-setzaxis,{model}", three, str(levels)]),
        # The geometric-height analogue of "airpressure", and it closes the
        # gh2hl/gh2hlx skips. gh2hl identifies its vertical coordinate by CF
        # standard name exactly as ap2pl does — "The input file must contain the
        # 3D geometric height in meter. The geometric height is identified by
        # the NetCDF CF standard name geometric_height_at_full_level_center"
        # (cdo -h gh2hl) — so the recipe is the same shape with that name and
        # metres in place of air_pressure and pascal. Verified: against this
        # sample, `cdo gh2hl,600,1200,2400` exits 0 and writes a height axis
        # running 600 to 2400 [m].
        ("geoheight", ["-O", "-f", "nc", "merge",
                       "-setattribute,zg@standard_name="
                       "geometric_height_at_full_level_center",
                       "-setunit,m", "-setname,zg", f"-setzaxis,{model}",
                       "-addc,500", "-mulc,1000", three, str(levels),
                       "-setname,ta", f"-setzaxis,{model}", three, str(levels)]),
        # intlevel3d's two input slots, as two files rather than one, because
        # the operator takes them that way: infile1 the 3D data variables,
        # infile2 the 3D vertical *source* coordinate. The target coordinate is
        # the tgtcoordinate parameter and is built in _build_parameter_files.
        #
        # Slot order is load-bearing and silent — swapping the two exits 0 and
        # writes a file of nothing but missing values (see the intlevel3d note
        # in core/categories.py) — so the lab running it in the declared order
        # is what makes that declaration tested rather than merely written down.
        ("levels3d_data", ["-O", "-f", "nc", "-setname,ta", three, str(levels)]),
        ("levels3d_coord", ["-O", "-f", "nc", "-setunit,m", "-setname,zcoord",
                            "-addc,500", "-mulc,1000", three, str(levels)]),
        ("wind", ["-O", "-f", "nc", "merge",
                  "-setunit,m/s", "-setname,u", "-setcode,33", "-mulc,10",
                  five, str(series),
                  "-setunit,m/s", "-setname,v", "-setcode,34", "-mulc,5",
                  five, str(second)]),
        ("wind_rotated", ["-O", "-f", "nc", f"setgrid,{rotated}", path_for("wind")]),
        ("wind_u", ["-O", "-f", "nc", "selname,u", path_for("wind")]),
        ("wind_v", ["-O", "-f", "nc", "selname,v", path_for("wind")]),
        # The Transformation section's four field kinds, built in dependency
        # order. Every key here is an ``OperatorInput.key`` from the schema, so
        # ``_slot_file`` routes them without a line in ``_FIELD_SAMPLE_FOR``:
        #
        #   gaussian              -> gp2sp, gp2spl
        #   spectral              -> sp2gp, sp2gpl, sp2sp, spcut
        #   wind_gaussian         -> uv2dv, uv2dvl
        #   divergence_vorticity  -> dv2uv, dv2uvl, dv2ps
        #
        # "the Gaussian field is not routed to anything by itself" is what this
        # comment used to say, and it was the whole defect: seven of these
        # operators were being run against the ordinary lonlat sample, where
        # they exit 0 having copied their input, and the sweep recorded them as
        # passing. See ``core/fieldshape.py`` for the measurements.
        ("gaussian", ["-O", "-f", "nc", "-settaxis,2000-01-01,00:00:00,1day",
                      "-duplicate,5", "-random,t21grid,1"]),
        ("spectral", ["-O", "-f", "nc", "gp2sp", path_for("gaussian")]),
        # The existing "wind" sample is on a *lonlat* grid, which uv2dv refuses
        # outright — "(Abort): U-wind is not on Gaussian grid!", measured on
        # 2.6.3 — so this is a new sample rather than a reroute of that one.
        # Codes 131/132 are the ones uv2dv looks for, as against the 33/34 the
        # NCL wind pair carries.
        ("wind_gaussian", ["-O", "-f", "nc", "merge",
                           "-setunit,m/s", "-setname,u", "-setcode,131",
                           "-mulc,10", "-random,t21grid,1",
                           "-setunit,m/s", "-setname,v", "-setcode,132",
                           "-mulc,5", "-random,t21grid,2"]),
        # sd and svo, which is exactly what uv2dv writes: the pair comes back
        # named sd (code 155) and svo (code 138) on the spectral axis. Built
        # with the operator rather than by renaming, so the file the dv2* slots
        # get is the file their declared recipe says it is.
        ("divergence_vorticity", ["-O", "-f", "nc", "uv2dv",
                                  path_for("wind_gaussian")]),
        ("zonal_v", ["-O", "-f", "nc", "zonmean", "-setcode,132", "-setname,v",
                     "-mulc,3", str(levels)]),
        ("cloud", ["-O", "-f", "nc", "-setparam,223", f"-setzaxis,{pressure}",
                   "-mulc,0.5", str(levels)]),
        # NetCDF4 is not optional here: CDI refuses to write a complex type
        # into NetCDF classic, which is the whole reason these operators were
        # failing in the first place.
        ("complex", ["-O", "-f", "nc4", "retocomplex", str(series)]),
        ("icon_ocean", ["-O", "-f", "nc", "merge",
                        "-setname,prism_thick_c", "-addc,10", three, str(levels),
                        "-setname,depth_c", "-addc,100", three, str(levels),
                        "-setname,stretch_c", "-addc,1", three, str(levels),
                        "-setname,zos", "-addc,1", three, "-sellevidx,1",
                        str(levels)]),
    ]

    for key, command in plan:
        path = workdir / f"sample_{key}.nc"
        if not (reuse and path.is_file()):
            try:
                _run(binary, [*command, str(path)])
            except SampleError as exc:
                logger.warning("Could not build the %s field sample: %s", key, exc)
                continue
        built[key] = path
    return built


def _build_climate(binary: str, workdir: Path, series: Path,
                   reuse: bool) -> Dict[str, Path]:
    """The daily series and climatologies the climate indices declare.

    Nothing here is written out per operator. The four base series are built
    once, and then every *other* slot the schema declares is built by running
    that slot's own ``recipe`` against the base its operator's first slot names
    — so an index and its companion cannot drift apart, and adding an index to
    ``_OPERATOR_INPUTS`` is enough to have its inputs generated.

    ``{n}`` is filled from the same :data:`PARAMETER_DEFAULTS` the harness will
    pass the operator, because the ETCCDI bootstrapping indices take their
    running minimum and maximum over the window their own ``n`` names; building
    them over a different one would be a subtler version of the bug this
    function exists to fix.
    """
    built: Dict[str, Path] = {}

    for key, expression in _CLIMATE_BASES:
        path = workdir / f"sample_climate_{key}.nc"
        if not (reuse and path.is_file()):
            # tn and tx are offsets of tg, which has to exist first; the plan is
            # ordered so it does.
            #
            # tg and rr come off the main series rather than a random field of
            # their own. They used to be built from `-duplicate,730 -random,…,11`,
            # which made every climate base *constant in time* — and a climate
            # index over a series with no temporal variation is measuring
            # nothing, whatever it reports. `_build_series` is the one place that
            # decides what a generated series looks like; deriving from it here
            # means these four cannot drift away from that decision again.
            source = str(built["tg"] if key in ("tn", "tx") else series)
            try:
                _run(binary, ["-O", "-f", "nc", *expression, source, str(path)])
            except SampleError as exc:
                logger.warning("Could not build the %s climate sample: %s", key, exc)
                continue
        built[key] = path

    # The one companion with no recipe: a 0/1 land fraction on the same grid.
    mask = workdir / f"sample_climate_{_LANDMASK_KEY}.nc"
    if not (reuse and mask.is_file()):
        try:
            _run(binary, ["-O", "-f", "nc", "-setunit,1", "-setname,lwm",
                          "-gtc,0.5", "-seltimestep,1", str(series), str(mask)])
        except SampleError as exc:
            logger.warning("Could not build the land-water mask: %s", exc)
        else:
            built[_LANDMASK_KEY] = mask
    else:
        built[_LANDMASK_KEY] = mask

    built.update(_build_eof_companions(binary, workdir, series, reuse))
    built.update(_build_trend_companions(binary, workdir, series, reuse))

    window = PARAMETER_DEFAULTS.get("n", "5")
    for key, recipe, base in _declared_companions():
        if key in built:
            continue
        source = built.get(base)
        if source is None:
            continue
        path = workdir / f"sample_climate_{key}.nc"
        if not (reuse and path.is_file()):
            command = recipe.format(in1=str(source), n=window)
            try:
                _run(binary, ["-O", "-f", "nc", *command.split(), str(path)])
            except SampleError as exc:
                logger.warning("Could not build the %s companion: %s", key, exc)
                continue
        built[key] = path

    return built


def _build_eof_companions(binary: str, workdir: Path, series: Path,
                          reuse: bool) -> Dict[str, Path]:
    """The two files the EOFs section's declared slots name.

    The only companion in the lab that takes **two** steps and the only one
    whose second step is a *multi-output* command, which is why it cannot go
    through ``_declared_companions``: that mechanism formats one recipe into one
    ``cdo`` call with one target, and the file wanted here is outfile2 of a
    command with two.

        anomalies:  cdo sub series -timmean series           anom.nc
        eofs:       cdo eof,<neof> anom.nc  evals.nc  eofs.nc
                                                      ^^^^^^^^ this one

    Without them the sweep fed ``eofcoeff`` two ordinary series, which is
    exactly the argument swap its schema entry warns about — and measured on
    2.6.3 that does not fail, it exits 0 and writes **730** files (one per
    timestep of the series) into the output directory instead of 2. A sweep that
    reports a pass while emptying a disk is worse than one that reports a
    failure, so this is the fix for a real hazard and not a tidying-up.

    Both are built with ``neof`` taken from the same ``PARAMETER_DEFAULTS`` the
    harness will pass ``eof`` itself, for the reason ``_build_climate`` gives
    about ``{n}``: a companion built to different parameters than the operator
    under test is a subtler version of the bug companions exist to prevent.

    This docstring used to record, as a deliberate consequence, that the
    one-sided jacobi solver "does not converge over anomalies" on the generated
    sample and that the five runnable ``eof*`` operators were therefore
    *expected* to be reported as failures. **That was wrong, and the mistake is
    worth keeping written down.** The solver was not failing on the anomalies;
    there were no anomalies. ``_build_series`` repeated one random field at
    every timestep, so ``sub series -timmean series`` returned a file that was
    identically zero, and what the warning reported was a zero matrix rather
    than a hard decomposition. Given a series with real temporal structure the
    same solver converges on the first attempt and all five operators pass.

    The general lesson, which cost two rounds of measurement: a warning that
    names the *solver* still has to be checked against the **input**, because
    CDO will decompose a zero matrix without ever mentioning that it is one.
    """
    built: Dict[str, Path] = {}
    neof = PARAMETER_DEFAULTS.get("neof", "1")

    anomalies = workdir / "sample_climate_anomalies.nc"
    if not (reuse and anomalies.is_file()):
        try:
            _run(binary, ["-O", "-f", "nc", "sub", str(series),
                          "-timmean", str(series), str(anomalies)])
        except SampleError as exc:
            logger.warning("Could not build the anomaly companion: %s", exc)
            return built
    built["anomalies"] = anomalies

    eofs = workdir / "sample_climate_eofs.nc"
    # outfile1 is written and thrown away: the eigenvalue spectrum is not what
    # any declared slot asks for, and CDO has no way to skip an output.
    eigenvalues = workdir / "sample_climate_eigenvalues.nc"
    if not (reuse and eofs.is_file()):
        try:
            _run(binary, ["-O", "-f", "nc", f"eof,{neof}", str(anomalies),
                          str(eigenvalues), str(eofs)])
        except SampleError as exc:
            logger.warning("Could not build the EOF companion: %s", exc)
            return built
    built["eofs"] = eofs
    return built


def _build_trend_companions(binary: str, workdir: Path, series: Path,
                            reuse: bool) -> Dict[str, Path]:
    """The two files ``addtrend`` and ``subtrend`` take in slots 2 and 3.

    The second companion in the lab built outside ``_declared_companions``, and
    for the same reason as the EOF pair: one ``cdo`` call, two outputs, and that
    mechanism formats one recipe into one command with one target. Here *both*
    outputs are wanted, which is the difference from the EOF pair — nothing is
    written and thrown away.

        cdo trend series.nc  intercept.nc  slope.nc
                             ^^^^^^^^^^^^  ^^^^^^^^ both, in this order

    They are routed to the slots by ``OperatorInput.key`` — ``trend_intercept``
    and ``trend_slope`` — the same mechanism that routes the ECA climatologies,
    so the harness needs no rule about these two operators and the files cannot
    reach the wrong slot by being built in the wrong order.

    Without them the sweep fed ``addtrend`` and ``subtrend`` three ordinary
    series, which is precisely the mistake their schema entry warns about and
    which CDO does not report: measured on 2.6.3, three raw series in exits 0
    and writes a full, plausible, entirely wrong field. So the sweep's pass was
    real and its numbers were nonsense — the same hazard, and the same argument,
    as ``_build_eof_companions``.

    ``equal`` is deliberately *not* passed here, although the harness will pass
    it to the operator under test. On the generated daily series it cannot
    change the answer — measured, see ``PARAMETER_DEFAULTS["equal"]`` — so a
    companion built without it is not a companion built to different parameters,
    and leaving it off keeps this call the plain two-output command the
    docstring above quotes.
    """
    built: Dict[str, Path] = {}

    intercept = workdir / "sample_climate_trend_intercept.nc"
    slope = workdir / "sample_climate_trend_slope.nc"
    if not (reuse and intercept.is_file() and slope.is_file()):
        try:
            _run(binary, ["-O", "-f", "nc", "trend", str(series),
                          str(intercept), str(slope)])
        except SampleError as exc:
            # Both or neither: ``_declared_inputs_for`` is all-or-nothing, so a
            # half-built pair would send addtrend its real series beside one
            # right file and one wrong one — worse than falling back visibly.
            logger.warning("Could not build the trend companions: %s", exc)
            return built

    built["trend_intercept"] = intercept
    built["trend_slope"] = slope
    return built


def _declared_companions():
    """``(key, recipe, base_key)`` for every declared slot that has a recipe.

    Deduplicated by key, because ``eca_r75p`` and ``eca_r75ptot`` want the same
    75th-percentile file and building it twice would be two chances to build it
    differently.

    Every slot is considered, not ``slots[1:]``. The derived file is usually the
    second one — an index and its climatology — but conditional selection runs
    the other way round: ``ifthen`` takes the mask in slot 0 and the data in
    slot 1, and it is the mask that is built from the data. Skipping slot 0 meant
    a recipe declared there was invisible here, so the lab never built a mask and
    went on feeding ``ifthen`` two raw series — the exact silent-wrong-answer
    that declaring the slots was meant to end.

    ``recipe_source`` names the slot the recipe's ``{in1}`` refers to, so the
    base is that slot's key rather than slot 0's. A slot whose base has no key,
    or which names itself, is skipped: neither can be built.
    """
    seen = set()
    plan = []
    for name in sorted(OPERATOR_SCHEMA):
        slots = operator_inputs(name)
        for slot in slots:
            if not slot.recipe or not slot.key or slot.key in seen:
                continue
            if not 0 <= slot.recipe_source < len(slots):
                continue
            base = slots[slot.recipe_source]
            if not base.key or base.key == slot.key:
                continue
            seen.add(slot.key)
            plan.append((slot.key, slot.recipe, base.key))
    return plan


def _build_parameter_files(binary: str, workdir: Path, series: Path,
                           reuse: bool) -> Dict[str, Path]:
    """NetCDF files that fill a parameter slot, keyed by parameter name.

    ``remap``, ``verifyweights`` and ``writeremapscrip`` all read a *weight
    file*, and ``reducegrid`` reads a *mask file*; every one of them is opened
    with ``nc_open``. A synthesised text stub gets them "NetCDF: Unknown file
    format", which is a fact about the stub and not about the operator.
    """
    plan = (
        # cdo genbil writes exactly the SCRIP weight file remap expects.
        ("weights", ["-O", f"genbil,{SAMPLE_GRID}", str(series)]),
        # Any single-timestep 0/1 field will do as a reduction mask.
        ("mask", ["-O", "-f", "nc", "-gtc,0.5", "-seltimestep,1", str(series)]),
        # intlevel3d's ``tgtcoordinate``: a DATA file holding the 3D vertical
        # *target* coordinate, not a Z-axis description — see the parameter's
        # note in core/categories.py, where it was renamed from ``zdes``.
        # Deliberately a different scaling from the source coordinate built in
        # _build_fields, so the interpolation has somewhere to interpolate to.
        ("tgtcoordinate", ["-O", "-f", "nc", "-setunit,m", "-setname,zcoord",
                           "-addc,700", "-mulc,900", "-seltimestep,1/3",
                           str(workdir / "sample_levels.nc")]),
    )

    built: Dict[str, Path] = {}
    for name, command in plan:
        path = workdir / f"sample_{name}.nc"
        if not (reuse and path.is_file()):
            try:
                _run(binary, [*command, str(path)])
            except SampleError as exc:
                logger.warning("Could not build the %s parameter file: %s", name, exc)
                continue
        built[name] = path
    return built


def _run(binary: str, arguments: Sequence[str]) -> None:
    argv = [binary, "-s", *arguments]
    logger.debug("Building sample: %s", " ".join(argv))
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=180)
    except FileNotFoundError as exc:
        raise SampleError(f"CDO binary '{binary}' not found: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise SampleError(f"Timed out building a sample: {' '.join(argv)}") from exc

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        raise SampleError(
            f"Could not build a sample ({' '.join(argv)}): "
            + (detail[-1] if detail else f"exit {completed.returncode}")
        )


def detect_variable(path: Path, binary: str = "cdo") -> Optional[str]:
    """The first variable name in ``path``, or None if it cannot be read.

    None is not an error here: the caller falls back to the generator's name,
    and an unreadable file will fail loudly enough on the first operator.
    """
    try:
        completed = subprocess.run(
            [binary, "-s", "showname", str(path)],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        logger.debug("Could not read variable names from %s", path, exc_info=True)
        return None

    if completed.returncode != 0:
        return None
    names = completed.stdout.split()
    return names[0] if names else None
