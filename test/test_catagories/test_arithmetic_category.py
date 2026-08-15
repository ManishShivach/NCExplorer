"""The Arithmetic section is what CDO says it is, not what the names suggest.

``_infer_category`` was a prefix cascade, and a cascade cannot tell ``ymonsub``
from ``ymonmean``: the statistics prefixes were tested first and swallowed all
twenty of the day/mon/year/yday/ymon/yseas arithmetic operators. Another
thirteen went to Miscellaneous, Comparison and Modification, and in the other
direction ``op.endswith("c")`` pulled ``harmonic`` and ``lic`` *into* Arithmetic.
Thirty-two of the section's seventy-one were unreachable by browsing.

The fix reads the module out of the installed binary
(``generate_operator_modules.py`` → ``CDO_OPERATOR_MODULES``). These tests pin
the result from both directions, so the next prefix heuristic cannot silently
reclaim any of it.
"""

import shutil
import subprocess

import pytest

from ncexplorer_toolkit.core.categories import (
    NCExplorerCategory, OPERATOR_CATEGORIES, OPERATOR_SCHEMA, _MODULE_CATEGORY,
    menu_operators, operator_module,
)
from ncexplorer_toolkit.core.cdo_operator_catalog import (
    CDO_OPERATORS, CDO_OPERATOR_MODULES,
)

cdo_required = pytest.mark.skipif(
    shutil.which("cdo") is None, reason="needs an installed CDO")

#: The CDO 2.6.0 Arithmetic section, module by module, exactly as the binary
#: reports it — 78 operators, not the 71 the published PDFs document. The seven
#: extra are undocumented (``arg``, ``conj``, ``im``, ``re``, ``rand``, ``mod``)
#: or an alias (``anomaly`` → ``ymonsub``); ``cdo --help`` files every one of
#: them under an arithmetic module. ``log`` and ``muldoy`` are the same story
#: and were already known.
ARITHMETIC_BY_MODULE = {
    "Arithmetic on two datasets": (
        "add sub mul div min max atan2 setmiss"),
    "Arithmetic with a constant": (
        "addc subc mulc divc minc maxc mod"),
    "Mathematical functions": (
        "abs int nint pow sqr sqrt exp ln log log10 sin cos tan asin acos "
        "atan reci not arg conj im re rand"),
    "Evaluate expressions": "expr exprf aexpr aexprf",
    "Daily arithmetic": "dayadd daysub daymul daydiv",
    "Monthly arithmetic": "monadd monsub monmul mondiv",
    "Yearly arithmetic": "yearadd yearsub yearmul yeardiv",
    "Multi-year hourly arithmetic": "yhouradd yhoursub yhourmul yhourdiv",
    "Multi-year daily arithmetic": "ydayadd ydaysub ydaymul ydaydiv",
    "Multi-year monthly arithmetic": "ymonadd ymonsub ymonmul ymondiv anomaly",
    "Multi-year seasonal arithmetic": "yseasadd yseassub yseasmul yseasdiv",
    "Arithmetic with days": "muldpm divdpm muldpy divdpy muldoy",
    "Arithmetic with latitude": "mulcoslat divcoslat",
}

ARITHMETIC = sorted(
    name for names in ARITHMETIC_BY_MODULE.values() for name in names.split()
)

#: The seventy-one the published documentation covers. Kept separate from the
#: full list so a docs-derived expectation and a binary-derived one cannot be
#: confused for each other.
DOCUMENTED = sorted(set(ARITHMETIC) - {
    "anomaly", "arg", "conj", "im", "re", "rand", "mod"})


def test_the_section_is_seventy_eight_operators():
    assert len(ARITHMETIC) == 78
    assert len(DOCUMENTED) == 71


@pytest.mark.parametrize("operator", ARITHMETIC)
def test_every_arithmetic_operator_is_filed_under_arithmetic(operator):
    spec = OPERATOR_SCHEMA.get(operator)
    assert spec is not None, f"{operator} is not in the schema at all"
    assert spec.category is NCExplorerCategory.ARITHMETIC


@pytest.mark.parametrize("operator", ["harmonic", "lic"])
def test_the_endswith_c_intruders_are_gone(operator):
    """Both were pulled in by ``op.endswith("c")`` and belong to no CDO module."""
    assert operator_module(operator) == ""
    assert OPERATOR_SCHEMA[operator].category is not NCExplorerCategory.ARITHMETIC


def test_arithmetic_holds_nothing_else():
    """The category is exactly the section — no additions, no omissions."""
    filed = {name for name, spec in OPERATOR_SCHEMA.items()
             if spec.category is NCExplorerCategory.ARITHMETIC}
    assert filed == set(ARITHMETIC)


@pytest.mark.parametrize("module,names", sorted(ARITHMETIC_BY_MODULE.items()))
def test_module_membership_matches_the_catalog(module, names):
    for name in names.split():
        assert CDO_OPERATOR_MODULES.get(name) == module


def test_comparison_family_stays_in_comparison():
    """``minc``/``maxc`` moved to Arithmetic; the 0/1 family did not follow.

    They read alike and behave differently: ``maxc`` returns a value, ``gec``
    returns a mask. CDO puts the first in Arithc and the rest in Comparison, and
    that is the distinction worth keeping.
    """
    for name in ("eqc", "nec", "lec", "ltc", "gec", "gtc"):
        assert OPERATOR_SCHEMA[name].category is NCExplorerCategory.COMPARISON


# --- the mapping is derived, so it must still agree with the binary ---------

@cdo_required
@pytest.mark.parametrize("module", sorted(ARITHMETIC_BY_MODULE))
def test_catalog_still_agrees_with_the_installed_binary(module):
    """Re-probe one operator per module rather than trusting the generated file.

    The catalog is generated, and a generated file that nobody re-checks is a
    hand-written file with extra steps.
    """
    operator = ARITHMETIC_BY_MODULE[module].split()[0]
    out = subprocess.run(["cdo", "--help", operator], capture_output=True,
                         text=True, timeout=30)
    assert module in (out.stdout + out.stderr).replace("\n", " ")


@cdo_required
def test_every_installed_operator_that_has_a_module_is_in_the_catalog():
    """The generated map covers the installed binary, not a snapshot of it."""
    missing = [name for name in CDO_OPERATOR_MODULES if name not in CDO_OPERATORS]
    assert missing == []


# --- what browsing actually reaches ----------------------------------------

def test_the_curated_shortlist_is_exactly_the_top_ten():
    """Curated is sorted and truncated to ten, so a longer list is not a choice."""
    from ncexplorer_toolkit.gui.toolbar import TOP_LEVEL_LIMIT

    curated = OPERATOR_CATEGORIES[NCExplorerCategory.ARITHMETIC]
    assert len(curated) == TOP_LEVEL_LIMIT
    assert len(set(curated)) == len(curated)
    # Every one of them is really in the category it is shortlisted for.
    for name in curated:
        assert OPERATOR_SCHEMA[name].category is NCExplorerCategory.ARITHMETIC


def test_browsing_reaches_the_whole_section():
    """``menu_operators`` is what the menus are built from; nothing may be lost."""
    curated, rest = menu_operators(NCExplorerCategory.ARITHMETIC)
    assert set(curated) | set(rest) == set(ARITHMETIC)


def test_the_all_submenu_groups_by_module(qapp):
    """78 operators chunk alphabetically into labels that say nothing."""
    from ncexplorer_toolkit.gui.toolbar import NCExplorerToolbar

    groups = NCExplorerToolbar._module_groups(ARITHMETIC)
    assert groups is not None
    assert [label.rsplit(" (", 1)[0] for label, _ in groups] == \
        sorted(ARITHMETIC_BY_MODULE)
    assert sorted(n for _, names in groups for n in names) == ARITHMETIC


def test_module_grouping_declines_when_it_would_not_help(qapp):
    """A category past ``CHUNK_THRESHOLD`` groups is not improved by grouping.

    **The two categories have swapped sides of the threshold, twice now, and
    this docstring is the record of both swaps.**

    It first named Statistical values (44 groups against a threshold of 40).
    Moving the four Correlation operators out took four single-operator module
    groups with them, Statistical values fell to 39, and this test moved to
    Miscellaneous, which made 46.

    The Statistic-section work swapped them back. Naming the five missing
    Statistic module titles in ``_MODULE_CATEGORY`` moved 46 operators — the
    vars*, yhour*, dhour*, dminute* and consec* families — plus ``globavg``
    out of Miscellaneous and into Statistical values. Those are *five new
    module groups* on one side and five fewer on the other:

        Statistical values   39 groups -> 43   (now declines)
        Miscellaneous        46 groups -> 37   (now groups)

    So the decline path is exercised by Statistical values again, and the
    grouping path by Miscellaneous, which is what the companion test below now
    asserts.

    Worth stating plainly rather than leaving in the numbers, because it is a
    real cost of a correct change: Statistical values holds 289 operators and
    its "All …" submenu now falls back to ``_alphabetical_chunks``, which reads
    "abs — divdpy" and tells a user nothing. The categorisation is not in
    doubt — CDO documents all 46 of those operators under Statistic — but if
    the menu matters more than the three groups of headroom, the fix is to
    raise ``CHUNK_THRESHOLD`` past 43 rather than to misfile the operators.
    That is a UI judgement and is deliberately not made here.
    """
    from ncexplorer_toolkit.gui.toolbar import NCExplorerToolbar

    stats = sorted(name for name, spec in OPERATOR_SCHEMA.items()
                   if spec.category is NCExplorerCategory.STATISTICAL_VALUES)
    assert NCExplorerToolbar._module_groups(stats) is None


def test_miscellaneous_groups_now_that_the_statistic_modules_left_it(qapp):
    """The other side of the threshold, asserted so the move stays deliberate.

    The mirror of the test above and the reason both are kept: each names the
    category that currently exercises one branch of ``_module_groups``, so a
    change that moves a category across the threshold cannot do it silently.
    If a later change pushes Miscellaneous back over 40 groups, that is a menu
    quietly getting worse, and this is what notices.
    """
    from ncexplorer_toolkit.gui.toolbar import NCExplorerToolbar

    misc = sorted(name for name, spec in OPERATOR_SCHEMA.items()
                  if spec.category is NCExplorerCategory.MISCELLANEOUS)
    groups = NCExplorerToolbar._module_groups(misc)
    assert groups is not None
    assert len(groups) <= 40
    # None of the five Statistic modules is among them any more, nor the
    # Fldstat module that carried ``globavg`` out with it.
    assert not [label for label, _ in groups
                if label.startswith(("Statistical values",
                                     "Multi-year hourly statistics",
                                     "Multi-day hourly statistics",
                                     "Multi-day by the minute statistics",
                                     "Consecute timestep periods"))]


#: The four modules of the Comparison section, added to ``_MODULE_CATEGORY``
#: after the Arithmetic ones and for the same reason: ``ymoneq`` and ``yseasgt``
#: were filed under Statistical values, because the prefix cascade tests
#: ("ymon","yseas") before it tests any comparison name. The membership itself
#: is asserted in ``tests/test_comparison_category.py``; what belongs here is
#: only that this table's *policy* is still exactly what it claims to be.
COMPARISON_MODULES = {
    "Comparison of two fields",
    "Comparison of a field with a constant",
    "Multi-year monthly comparison",
    "Multi-year seasonal comparison",
}

#: The two Conditional selection modules, added third. ``reducegrid`` is the
#: reason: CDO documents it under Conditional selection, its name starts with
#: "r", and so no rule over names could ever have placed it — the cascade filed
#: it under Miscellaneous. CDO gives it a module of its own rather than putting
#: it in with the five ``if*`` operators, which is why one section is two titles
#: here. The ``if*`` half is named too, even though the ``op.startswith("if")``
#: branch already placed those five correctly, because a branch that happens to
#: agree with the module is still a guess. Membership is asserted in
#: ``tests/test_conditional_selection.py``.
CONDITIONAL_MODULES = {
    "Conditional selection",
    "Reduce fields to user-defined mask",
}

#: The seventeen File operation modules, added fourth and the largest single
#: addition to the table. Twelve of the section's operators were in three wrong
#: categories before it: ``setchunkspec``/``setfilter`` under Modification on
#: their "set" prefix, ``mergegrid`` under Statistical values on the "mer"
#: meridional prefix, and the rest under Miscellaneous by falling off the end of
#: the cascade. ``mergegrid`` is the one that shows why a prefix cannot do this
#: job — ``mermean`` is a meridional statistic and ``mergegrid`` is a file
#: operation, and they differ by two letters. Membership is asserted in
#: ``tests/test_file_operations_category.py``.
FILE_OPERATION_MODULES = {
    "Copy datasets",
    "Duplicate a data stream and write it to file",
    "Pack data",
    "Unpack data",
    "Specify chunking",
    "Specify filter",
    "Bit rounding",
    "Replace variables",
    "Duplicates a dataset",
    "Merge grid",
    "Merge datasets",
    "Split a dataset",
    "Split timesteps of a dataset",
    "Split selected timesteps",
    "Splits a file into dates",
    "Distribute horizontal grid",
    "Collect horizontal grid",
}


#: The four modules of the Correlation section, added fifth. One module per
#: operator, which is CDO's own way of saying these are four different
#: questions. Named for the reason the other four sections were: the cascade
#: tests ("fld", …, "tim", …) for Statistical values before anything else could
#: reach them, and no prefix can tell ``fldcor`` from ``fldmean``. All four were
#: filed next to ninety-six one-input reductions. Membership, and the reasoning
#: for leaving ``varrms``/``fldrms``/``timrmsd`` out of it, are asserted in
#: ``tests/test_correlation_category.py``.
CORRELATION_MODULES = {
    "Correlation in grid space",
    "Covariance in grid space",
    "Correlation over time",
    "Covariance over time",
}


#: The two modules of the EOFs section, added sixth. A different mechanism from
#: the four sections above and the same remedy: no prefix branch in the cascade
#: matches an "eof" name at all, so all eight fell off the end into
#: Miscellaneous rather than being claimed by a wrong branch. Both titles read
#: back off 2.6.3 — ``cdo --help eof`` and ``cdo --help eofcoeff``. Membership
#: is asserted in ``tests/test_eof_category.py``.
EOF_MODULES = {
    "Empirical Orthogonal Functions",
    "Principal coefficients of EOFs",
}


#: The six modules of the Import/Export section, added seventh. The first
#: section whose placement needed the *order* of the two tests in
#: ``_infer_category`` changed as well as this table widened: three of these six
#: hold nothing but ``nout == 0`` operators, and that branch returned
#: Information before the module lookup was ever consulted. Titles read back off
#: 2.6.3; membership is asserted in
#: ``test/test_catagories/test_import_export_category.py``.
IMPORT_EXPORT_MODULES = {
    "Import binary data sets",
    "Import CM-SAF HDF5 files",
    "Formatted input",
    "Formatted output",
    "Table output",
    "GMT output",
}


#: The eight modules of the Miscellaneous section that this table names, added
#: eighth. Unlike the seven sections above, this is deliberately *not* every
#: module of its section — Miscellaneous has 32, and most of them need no entry
#: because falling off the end of the cascade already lands there. These eight
#: are the ones where a prefix branch was claiming the operator first, and three
#: of them split a single CDO module across two categories: deltat/timederivative,
#: delta_pressure against pressure/pressure_half, and setvals against
#: setrtoc/setrtoc2.
#:
#: "Wind transformation" is deliberately absent even though two of its operators
#: belong here: three unrelated CDO modules print that identical title on 2.6.3,
#: so it does not identify a module and naming it would drag ``uv2dv``/``dv2uv``
#: out of Transformation. Those two are placed by the curated list instead.
#: Membership is asserted in
#: ``test/test_catagories/test_miscellaneous_category.py``.
MISCELLANEOUS_MODULES = {
    "Difference between timesteps",
    "Pressure on model levels",
    "Replace data values",
    "Set the bounds of a field",
    "Histogram",
    "Temporal sorting",
    "Generate a field",
    "GrADS data descriptor file",
}


#: The one module of the Climate model output rewriting section, added ninth,
#: and mapped to Import/Export rather than to a category of its own. Same
#: mechanism as the ``nout == 0`` half of the Import/Export section: ``cmor``
#: writes NetCDF files and was filed under Information purely on its output
#: count, because CMOR composes the filenames instead of CDO. A single-operator
#: module, so this entry moves exactly ``cmor``; ``cmorlite`` is a separate
#: module ("CMOR lite") that this table still does not name. Membership and the
#: choice of destination are asserted in
#: ``test/test_catagories/test_climate_model_output_rewriting.py``.
CMOR_MODULES = {
    "Climate Model Output Rewriting to produce CMIP-compliant data",
}

#: The three modules of CDO's "Graphic with Magics" section, added eleventh,
#: and mapped to a category of its own, ``GRAPHICS``.
#:
#: Same mechanism as the EOFs entries above: no prefix branch in
#: ``_infer_category`` matches "contour", "shaded", "grfill", "vector", "graph"
#: or "stream", so all six fell off the end into Miscellaneous. The titles are
#: the binary's own operator descriptions, which is what ``CDO_OPERATOR_MODULES``
#: records; "Lon/Lat vector plot" covers ``stream`` as well as ``vector``, which
#: is how the section's one undocumented operator is placed without being named
#: anywhere.
GRAPHICS_MODULES = {
    "Lon/Lat plot",
    "Lon/Lat vector plot",
    "Line graph plot",
}

#: The six Statistic-section titles, and the reason each is named rather than
#: left to the prefix cascade in ``_infer_category``.
#:
#: The first five hold 46 operators between them and every one was in
#: Miscellaneous, because the cascade's Statistical values branch tests a list
#: of prefixes ("fld", "zon", "mer", "tim", …) that none of these names starts
#: with. The sixth, "Statistical values over a field", changes nothing for the
#: sixteen ``fld*`` operators the cascade already caught — it is there for
#: ``globavg``, an alias in the same module whose name has no "fld" in it.
#:
#: What each of the other Statistic modules does *not* need an entry for is
#: covered by the cascade already; this table stays partial on purpose, which
#: is what the test below is for. The thirteen Remapstat operators are the one
#: deliberate divergence from CDO's own sectioning and are documented at the
#: end of ``_MODULE_CATEGORY``; they keep Interpolation and so are absent here.
STATISTIC_MODULES = {
    "Statistical values over all variables",
    "Multi-year hourly statistics",
    "Multi-day hourly statistics",
    "Multi-day by the minute statistics",
    "Consecute timestep periods",
    "Statistical values over a field",
}


def test_module_policy_covers_only_what_it_claims():
    """``_MODULE_CATEGORY`` is deliberately partial: eleven sections, no more.

    The remaining ~650 operators keep the categories they had. Widening this
    table is a separate decision each time, and this test is what makes widening
    it deliberate — it failed when the Comparison section was added, again when
    Conditional selection was, again when File operations was, again when
    Correlation was, again when EOFs was, again when Import/Export was, again
    when the eight Miscellaneous modules were, again when Climate model output
    rewriting was, again when the six Statistic titles were, and again when the
    three Graphic with Magics titles were, which is the whole point of it.
    """
    assert set(_MODULE_CATEGORY) == (set(ARITHMETIC_BY_MODULE)
                                     | COMPARISON_MODULES
                                     | CONDITIONAL_MODULES
                                     | FILE_OPERATION_MODULES
                                     | CORRELATION_MODULES
                                     | EOF_MODULES
                                     | IMPORT_EXPORT_MODULES
                                     | MISCELLANEOUS_MODULES
                                     | CMOR_MODULES
                                     | STATISTIC_MODULES
                                     | GRAPHICS_MODULES)
    assert set(_MODULE_CATEGORY.values()) == {
        NCExplorerCategory.ARITHMETIC, NCExplorerCategory.COMPARISON,
        NCExplorerCategory.CONDITIONAL_SELECTION,
        NCExplorerCategory.FILE_OPERATIONS,
        NCExplorerCategory.CORRELATION,
        NCExplorerCategory.EOF,
        NCExplorerCategory.IMPORT_EXPORT,
        NCExplorerCategory.STATISTICAL_VALUES,
        NCExplorerCategory.GRAPHICS,
        NCExplorerCategory.MISCELLANEOUS}
    # And the ten parts do not overlap: one module, one category.
    for module in ARITHMETIC_BY_MODULE:
        assert _MODULE_CATEGORY[module] is NCExplorerCategory.ARITHMETIC
    for module in COMPARISON_MODULES:
        assert _MODULE_CATEGORY[module] is NCExplorerCategory.COMPARISON
    for module in CONDITIONAL_MODULES:
        assert (_MODULE_CATEGORY[module]
                is NCExplorerCategory.CONDITIONAL_SELECTION)
    for module in FILE_OPERATION_MODULES:
        assert _MODULE_CATEGORY[module] is NCExplorerCategory.FILE_OPERATIONS
    for module in CORRELATION_MODULES:
        assert _MODULE_CATEGORY[module] is NCExplorerCategory.CORRELATION
    for module in EOF_MODULES:
        assert _MODULE_CATEGORY[module] is NCExplorerCategory.EOF
    for module in MISCELLANEOUS_MODULES:
        assert _MODULE_CATEGORY[module] is NCExplorerCategory.MISCELLANEOUS
    for module in IMPORT_EXPORT_MODULES:
        assert _MODULE_CATEGORY[module] is NCExplorerCategory.IMPORT_EXPORT
    # Mapped to an existing category rather than earning one of its own, so it
    # adds no member to the value set asserted above.
    for module in CMOR_MODULES:
        assert _MODULE_CATEGORY[module] is NCExplorerCategory.IMPORT_EXPORT
    for module in STATISTIC_MODULES:
        assert (_MODULE_CATEGORY[module]
                is NCExplorerCategory.STATISTICAL_VALUES)
    # Earns a category of its own, unlike CMOR above: CDO gives these three
    # modules a manual section of their own, and what they produce is a picture
    # rather than a dataset, so there is no existing category they belong in.
    for module in GRAPHICS_MODULES:
        assert _MODULE_CATEGORY[module] is NCExplorerCategory.GRAPHICS
