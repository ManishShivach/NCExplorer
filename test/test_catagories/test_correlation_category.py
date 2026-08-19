# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""The Correlation section: where it lives, what it takes, what it gives back.

Four operators — ``fldcor``, ``fldcovar``, ``timcor``, ``timcovar`` — and five
separate problems, none of which is visible from the catalog line CDO gives
them ("Correlation over time.").

**Where they live.** All four reached Statistical values through the
``startswith(("fld", …, "tim", …))`` prefix cascade in ``_infer_category``, and
sat among ninety-six one-input reductions. That is the wrong shelf twice over:
they take two files, and what they return is a relationship between two fields
rather than a summary of one. CDO gives each its own module title and groups all
four under one Correlation section, so ``_MODULE_CATEGORY`` names the modules —
the same fix as the Arithmetic, Comparison, Conditional selection and File
operation sections before them.

**Whether they can be run at all.** ``timcor`` and ``timcovar`` were among the
38 operators absent from the old hand-maintained ``OPERATOR_SIGNATURES``, so the
operator panel read them as ``(1, 1)``, drew one input row, and built
``cdo timcor in.nc out.nc`` — "cdo (Abort): Missing inputs".

**What their inputs must be.** Two raw series on the same grid, the same length,
holding the same physical quantity. None of that was declared, so the model
builder captioned the slots "Input 1"/"Input 2" and the units check had no
expectation to test.

**What the result is shaped like.** ``fldcor`` writes a 1x1 grid — a scalar time
series, not a map. ``timcor`` writes a map with exactly one timestep, and a
second variable called ``pvalue``.

**How they fail.** ``fldcor`` and ``fldcovar`` handed series of different lengths
warn, **exit 0**, and truncate. That is the failure this whole file is really
about: a plausible, scientifically wrong answer reported as a success.

Everything asserted here was measured against CDO 2.6.3 on the machine this was
written on; the tests marked ``cdo_required`` re-measure it, so a CDO that
changes any of it fails a test rather than leaving the app confidently wrong.
"""

import shutil
import subprocess

import pytest

from ncexplorer_toolkit.core.categories import (
    NCExplorerCategory, OPERATOR_CATEGORIES, OPERATOR_SCHEMA,
    menu_operators, operator_inputs, operator_module,
)
from ncexplorer_toolkit.core.pairing import (
    BLOCK, SILENT_TRUNCATION, check_pairing, pairs_must_match,
)

cdo_required = pytest.mark.skipif(
    shutil.which("cdo") is None, reason="needs an installed CDO")

#: ``operator -> the module title the binary prints``, written out rather than
#: derived from the schema: a test that read the same table the code reads would
#: agree with it by construction and prove nothing.
CORRELATION_SECTION = {
    "fldcor": "Correlation in grid space",
    "fldcovar": "Covariance in grid space",
    "timcor": "Correlation over time",
    "timcovar": "Covariance over time",
}

#: Operators that measure agreement between two fields and are *not* in this
#: category, with the reason. All three are (2|1), all three read like members
#: of the section, and the binary will not place any of them: none appears in
#: ``CDO_OPERATOR_MODULES`` and ``cdo -h varrms`` answers "No help available for
#: this operator!". Same evidence, same verdict, as ``harmonic`` and ``lic``.
UNPLACEABLE_NEIGHBOURS = ("varrms", "fldrms", "timrmsd")


# ---------------------------------------------------------------------------
# Where they live
# ---------------------------------------------------------------------------

def test_all_four_are_in_the_correlation_category():
    for name in CORRELATION_SECTION:
        assert OPERATOR_SCHEMA[name].category is NCExplorerCategory.CORRELATION


def test_the_category_is_exactly_these_four():
    """No fifth operator arrives by accident through a module title."""
    members = {name for name, spec in OPERATOR_SCHEMA.items()
               if spec.category is NCExplorerCategory.CORRELATION}
    assert members == set(CORRELATION_SECTION)


def test_the_grouping_comes_from_the_modules_not_the_names():
    """Every member is placed by a module CDO itself reports."""
    for name, module in CORRELATION_SECTION.items():
        assert operator_module(name) == module


def test_the_rms_neighbours_are_left_where_the_binary_leaves_them():
    """They read like members and cannot be placed, so they are not moved.

    The point is not which category they end up in — they end up in two
    different ones — but that neither was chosen by this change.
    """
    for name in UNPLACEABLE_NEIGHBOURS:
        assert operator_module(name) == "", (
            f"{name} now has a module; reconsider its category deliberately")
        assert OPERATOR_SCHEMA[name].category is not NCExplorerCategory.CORRELATION


def test_the_menu_lists_all_four_at_the_top_level():
    curated, rest = menu_operators(NCExplorerCategory.CORRELATION,
                                   set(OPERATOR_SCHEMA))
    assert curated == sorted(CORRELATION_SECTION)
    assert rest == []
    assert (OPERATOR_CATEGORIES[NCExplorerCategory.CORRELATION]
            == sorted(CORRELATION_SECTION))


def test_the_category_has_an_icon():
    """``CATEGORY_ICONS`` is exhaustive and raises rather than drawing nothing."""
    from ncexplorer_toolkit.resources.icons import CATEGORY_ICONS

    assert CATEGORY_ICONS[NCExplorerCategory.CORRELATION]


# ---------------------------------------------------------------------------
# Whether they can be run
# ---------------------------------------------------------------------------

def test_every_member_takes_two_inputs_and_writes_one_file():
    for name in CORRELATION_SECTION:
        spec = OPERATOR_SCHEMA[name]
        assert (spec.nin, spec.nout) == (2, 1)


def test_the_operator_form_draws_two_input_rows(qapp):
    """The row that decides whether a second file can be supplied at all.

    ``timcor`` used to get one, because the form read a table this operator was
    missing from and defaulted to ``(1, 1)``.
    """
    from ncexplorer_toolkit.gui.main_window import NCExplorerOperatorGUI

    for name in CORRELATION_SECTION:
        rows = NCExplorerOperatorGUI.parse_parameters(name, "")
        labels = [label for _kind, label, *_rest in rows]
        assert sum(1 for label in labels if "Input File" in label) == 2, name
        assert sum(1 for label in labels if "Output File" in label) == 1, name


def test_the_form_and_the_schema_agree_on_arity(qapp):
    from ncexplorer_toolkit.gui.main_window import _operator_arity

    for name in CORRELATION_SECTION:
        spec = OPERATOR_SCHEMA[name]
        assert _operator_arity(name) == (spec.nin, spec.nout)


def test_the_model_builder_offers_two_ports():
    from ncexplorer_toolkit.core.model import OperatorCatalog

    catalog = OperatorCatalog()
    for name in CORRELATION_SECTION:
        assert catalog.signature(name) == (2, 1)
        assert len(operator_inputs(name)) == 2


# ---------------------------------------------------------------------------
# What their inputs must be
# ---------------------------------------------------------------------------

def test_both_slots_are_declared_with_a_role_and_a_units_expectation():
    for name in CORRELATION_SECTION:
        slots = operator_inputs(name)
        assert len(slots) == 2
        for slot in slots:
            assert slot.role and not slot.role.startswith("Input ")
            assert slot.field
            assert slot.units == "same_as_input1"
            assert slot.key


def test_neither_slot_carries_a_recipe():
    """The second input is another measurement, not something derivable.

    An empty recipe here is a claim, not an omission: it is what stops the
    model builder offering to wire in a node that would correlate a file with
    a function of itself.
    """
    for name in CORRELATION_SECTION:
        assert all(slot.recipe == "" for slot in operator_inputs(name))


def test_the_second_slot_says_what_the_sign_of_the_result_means():
    """The caption has to distinguish the two axes, not merely number them."""
    assert "map" in operator_inputs("fldcor")[1].role
    assert "gridpoint" in operator_inputs("timcor")[1].role


def test_the_sweep_builds_a_real_pair_for_them():
    """Two files on one grid and one time axis, not the same file twice."""
    from pathlib import Path

    from operator_lab.samples import SampleSet

    samples = SampleSet(series=Path("series.nc"),
                        extra=[Path("second.nc"), Path("third.nc")])
    for name in CORRELATION_SECTION:
        chosen = samples.inputs_for(name, 2)
        assert len(chosen) == 2
        assert chosen[0] != chosen[1], name


# ---------------------------------------------------------------------------
# The pre-flight check
# ---------------------------------------------------------------------------

def test_the_pairing_rule_reaches_them_and_spares_the_companion_families():
    """Derived from the schema, not from a list of these four names."""
    for name in CORRELATION_SECTION:
        assert pairs_must_match(name)
    # Two raw series, so the same rule reaches them too.
    assert pairs_must_match("eca_etr")
    # A derived second input is *supposed* to be a different length.
    for name in ("ymonadd", "ymoneq", "yseassub", "eca_cwfi", "ifthen"):
        assert not pairs_must_match(name)


def test_only_the_truncating_pair_blocks():
    assert SILENT_TRUNCATION == {"fldcor", "fldcovar"}


@cdo_required
def test_unequal_lengths_are_refused_for_fldcor_and_reported_for_timcor(tmp_path):
    """The whole point of the module, re-measured rather than assumed."""
    long_file = _series(tmp_path, "long.nc", steps=6, seed=1)
    short_file = _series(tmp_path, "short.nc", steps=3, seed=2)

    blocking_kinds = {
        problem.kind
        for problem in check_pairing("fldcor", [str(long_file), str(short_file)])
        if problem.severity == BLOCK
    }
    assert "timesteps" in blocking_kinds

    # timcor fails loudly on its own, so this is said and not enforced.
    assert not [problem for problem
                in check_pairing("timcor", [str(long_file), str(short_file)])
                if problem.severity == BLOCK]


@cdo_required
def test_a_matching_pair_raises_nothing(tmp_path):
    first = _series(tmp_path, "a.nc", steps=6, seed=1)
    second = _series(tmp_path, "b.nc", steps=6, seed=2)
    for name in CORRELATION_SECTION:
        assert check_pairing(name, [str(first), str(second)]) == [], name


@cdo_required
def test_mismatched_grids_block(tmp_path):
    first = _series(tmp_path, "big.nc", steps=6, seed=1)
    small = _series(tmp_path, "small.nc", steps=6, seed=2, grid="r9x5")
    kinds = {problem.kind
             for problem in check_pairing("fldcor", [str(first), str(small)])
             if problem.severity == BLOCK}
    assert "grid" in kinds


# ---------------------------------------------------------------------------
# What comes back — measured against the binary
# ---------------------------------------------------------------------------

def _series(directory, name, *, steps, seed, grid="r18x9"):
    """One synthetic series, built by CDO so the grid is one CDO wrote."""
    path = directory / name
    parts = []
    for step in range(1, steps + 1):
        piece = directory / f"_{name}_{step}.nc"
        subprocess.run(
            ["cdo", "-s", "-O", "-f", "nc",
             f"-settaxis,2000-01-{step:02d},00:00:00,1day",
             "-setname,tas", f"-random,{grid},{seed * 10 + step}", str(piece)],
            check=True, capture_output=True)
        parts.append(str(piece))
    subprocess.run(["cdo", "-s", "-O", "mergetime", *parts, str(path)],
                   check=True, capture_output=True)
    return path


def _shape(path):
    from ncexplorer_toolkit.core.units import result_shape

    return result_shape(str(path))


@cdo_required
def test_fldcor_writes_a_scalar_series_not_a_map(tmp_path):
    """points=1 with one value per timestep — the reason it is routed to a plot."""
    first = _series(tmp_path, "a.nc", steps=6, seed=1)
    second = _series(tmp_path, "b.nc", steps=6, seed=2)

    for name in ("fldcor", "fldcovar"):
        out = tmp_path / f"{name}.nc"
        subprocess.run(["cdo", "-s", name, str(first), str(second), str(out)],
                       check=True, capture_output=True)
        shape = _shape(out)
        assert shape.points == 1, name
        assert shape.steps == 6, name
        assert shape.is_single_point and shape.is_series, name


@cdo_required
def test_timcor_writes_a_map_with_exactly_one_timestep(tmp_path):
    first = _series(tmp_path, "a.nc", steps=6, seed=1)
    second = _series(tmp_path, "b.nc", steps=6, seed=2)

    for name in ("timcor", "timcovar"):
        out = tmp_path / f"{name}.nc"
        subprocess.run(["cdo", "-s", name, str(first), str(second), str(out)],
                       check=True, capture_output=True)
        shape = _shape(out)
        assert shape.points == 18 * 9, name
        assert shape.steps == 1, name
        assert not shape.is_single_point, name


@cdo_required
def test_timcor_writes_a_pvalue_only_for_a_single_field_input(tmp_path):
    """CDO's docs say "only one input field"; this is what that means in files.

    One variable in, two out (the correlation and ``pvalue``). Two variables
    in, two out — and neither of them is a pvalue.
    """
    first = _series(tmp_path, "a.nc", steps=6, seed=1)
    second = _series(tmp_path, "b.nc", steps=6, seed=2)

    one = tmp_path / "one.nc"
    subprocess.run(["cdo", "-s", "timcor", str(first), str(second), str(one)],
                   check=True, capture_output=True)
    assert "pvalue" in _shape(one).variables

    def two_variable(source, target):
        renamed = tmp_path / f"r_{target.name}"
        subprocess.run(["cdo", "-s", "chname,tas,tas2", str(source), str(renamed)],
                       check=True, capture_output=True)
        subprocess.run(["cdo", "-s", "-O", "merge", str(source), str(renamed),
                        str(target)], check=True, capture_output=True)
        return target

    pair_a = two_variable(first, tmp_path / "a2.nc")
    pair_b = two_variable(second, tmp_path / "b2.nc")
    many = tmp_path / "many.nc"
    subprocess.run(["cdo", "-s", "timcor", str(pair_a), str(pair_b), str(many)],
                   check=True, capture_output=True)
    assert "pvalue" not in _shape(many).variables


@cdo_required
def test_fldcor_exits_zero_and_truncates(tmp_path):
    """The measurement the blocking check exists for. If CDO ever fixes this,
    this test fails and the block can be reconsidered — which is the point of
    re-measuring rather than trusting a note."""
    long_file = _series(tmp_path, "long.nc", steps=6, seed=1)
    short_file = _series(tmp_path, "short.nc", steps=3, seed=2)
    out = tmp_path / "out.nc"

    finished = subprocess.run(
        ["cdo", "fldcor", str(long_file), str(short_file), str(out)],
        capture_output=True, text=True)

    assert finished.returncode == 0, "CDO now fails on this; revisit the block"
    assert "different number of time steps" in finished.stderr
    assert _shape(out).steps == 3, "truncated to the shorter series"


@cdo_required
def test_the_pvalue_is_a_confidence_level_not_a_p_value(tmp_path):
    """Contradicts CDO's own wording, so it is asserted rather than described.

    ``cdo -h timcor`` calls it "the p-value (probability value)". Measured, it
    rises with |r| — it is near 0.5 where the correlation is nothing and 1.0
    where it is perfect, which is the opposite of a p-value. The app's
    description and the layer picker both say so, and this is what keeps that
    claim honest.
    """
    numpy = pytest.importorskip("numpy")
    xarray = pytest.importorskip("xarray")

    rng = numpy.random.default_rng(11)
    steps, rows, columns = 20, 9, 18
    a = rng.normal(size=(steps, rows, columns))
    rho = numpy.linspace(-0.95, 0.95, rows * columns).reshape(rows, columns)
    b = rho * a + numpy.sqrt(1 - rho ** 2) * rng.normal(size=(steps, rows, columns))

    paths = []
    for name, values in (("pa.nc", a), ("pb.nc", b)):
        path = tmp_path / name
        xarray.Dataset(
            {"tas": (("time", "lat", "lon"), values)},
            coords={"time": numpy.arange(steps, dtype="f8"),
                    "lat": numpy.linspace(-80, 80, rows),
                    "lon": numpy.linspace(0, 340, columns)},
        ).to_netcdf(path)
        paths.append(str(path))

    out = tmp_path / "pc.nc"
    subprocess.run(["cdo", "-s", "timcor", *paths, str(out)],
                   check=True, capture_output=True)

    with xarray.open_dataset(out) as result:
        correlation = numpy.asarray(result["tas"].values).ravel()
        pvalue = numpy.asarray(result["pvalue"].values).ravel()

    good = numpy.isfinite(correlation) & numpy.isfinite(pvalue)
    strong = good & (numpy.abs(correlation) > 0.9)
    weak = good & (numpy.abs(correlation) < 0.1)
    assert strong.any() and weak.any()

    # A real p-value would run the other way.
    assert pvalue[strong].min() > pvalue[weak].max()
    assert pvalue[weak].mean() < 0.7
    assert pvalue[strong].mean() > 0.95


# ---------------------------------------------------------------------------
# What the app says about them
# ---------------------------------------------------------------------------

def test_the_descriptions_carry_the_shape_of_the_answer():
    for name in ("fldcor", "fldcovar"):
        description = OPERATOR_SCHEMA[name].description
        assert "area" in description
        assert "1x1" in description or "single-point" in description
        assert "both" in description.lower()

    for name in ("timcor", "timcovar"):
        description = OPERATOR_SCHEMA[name].description
        assert "one timestep" in description
        assert "gridpoint" in description


def test_the_truncation_is_stated_before_the_run():
    """It belongs in the description because it is not a failure to report."""
    for name in ("fldcor", "fldcovar"):
        description = OPERATOR_SCHEMA[name].description
        assert "exits 0" in description
        assert "truncat" in description


def test_timcor_advertises_the_pvalue_and_corrects_its_name():
    description = OPERATOR_SCHEMA["timcor"].description
    assert "pvalue" in description
    assert "not a p-value" in description
    # timcovar writes no pvalue, so it must not claim one.
    assert "pvalue" not in OPERATOR_SCHEMA["timcovar"].description
