"""The Regression section says how to spell it, which file is which, and when.

Five operators — detrend, regres, trend, addtrend, subtrend — and four things
wrong with how the application offered them.

**The one parameter the whole section takes was declared nowhere.** ``equal``
appears in every one of the five CDO help pages and in none of ``_PARAM_SPECS``,
so the operator panel drew no field for it, the model builder's parameter form
had nothing to show, ``parameter_tokens`` passed values through unvalidated, and
``operator_syntax`` printed "ifile ofile". The parameter is not cosmetic: it
changes the answer, and its default is wrong for most data this application is
pointed at. Measured on 2.6.3 over twelve monthly steps, ``cdo regres`` gave a
slope of 1.0 and ``cdo regres,equal=false`` gave 1.01672 on the same file.

**And it is not spelled the way the synopsis reads.** ``detrend[,equal]`` looks
positional and looks like a flag and is neither — both spellings are a parse
error, and only ``equal=<bool>`` runs. That is the fifth grammar surprise the
schema records, and the tests below re-measure all four wrong spellings rather
than trusting this paragraph.

**addtrend and subtrend take three indistinguishable files in a fixed order.**
infile2 must be trend's outfile1 and infile3 its outfile2. All three carry the
input's variable name, grid and units, nothing in either coefficient file says
which it is, and CDO does not check: measured on a series with a=100 and b=3,
the swap exited 0 and wrote a full plausible field, and so did feeding the raw
series into both slots. The same failure class as ``fldcor``'s silent truncation
in ``core/pairing.py`` — a finished file of wrong numbers that every surface
reports as a success.

**trend writes two files that nothing distinguishes.** Both come back with the
input's own variable name, units and one timestep; their headers differ only in
the name of the file. The filenames a run was given are the only surviving
record of which is a and which is b.

The tests marked ``cdo_required`` re-measure their claims against the installed
binary rather than restating them, because on one point the binary and the
manual agree with each other and are both wrong: ``cdo -h subtrend`` prints the
synopsis as ``cdo trend[,equal] infile1 infile2 infile3 outfile``, naming a
(1|2) operator for a (3|1) one. That is asserted below as the bug it is.
"""

import itertools
import shutil
import subprocess

import pytest

from ncexplorer_toolkit.core.categories import (
    NCExplorerCategory, OPERATOR_CATEGORIES, OPERATOR_SCHEMA,
    _PARAM_SPECS, _REGRESSION_PARAMS,
    invalid_parameter_values, menu_operators, operator_inputs,
    operator_outputs, operator_syntax, parameter_tokens,
)
from ncexplorer_toolkit.core.model import ModelGraph
from ncexplorer_toolkit.core.pairing import pairs_must_match

cdo_required = pytest.mark.skipif(
    shutil.which("cdo") is None, reason="needs an installed CDO")


#: The CDO 2.6.3 Regression section, module by module, exactly as the binary
#: reports it. Written out rather than derived from the schema, because a test
#: that asks the schema what the schema contains asserts nothing.
REGRESSION_BY_MODULE = {
    "Detrend time series": "detrend",
    "Regression": "regres",
    "Trend of time series": "trend",
    "Add or subtract a trend": "addtrend subtrend",
}

SECTION = sorted(
    name for names in REGRESSION_BY_MODULE.values() for name in names.split())

#: The two that take three files: a series and trend's two outputs.
TRENDARITH = ["addtrend", "subtrend"]

#: The declared arity of each, from ``cdo --operators``.
ARITY = {
    "detrend": (1, 1), "regres": (1, 1), "trend": (1, 2),
    "addtrend": (3, 1), "subtrend": (3, 1),
}


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    """Twelve monthly steps on r4x2, values 1..12 — the docstring's sample.

    Monthly on purpose. The whole point of ``equal`` is that a month is not a
    fixed length, so a daily axis would make every measurement below come out
    the same either way and prove nothing.
    """
    if shutil.which("cdo") is None:
        pytest.skip("needs an installed CDO")
    workdir = tmp_path_factory.mktemp("regression")

    def cdo(*args):
        result = subprocess.run(["cdo", "-s", *args], capture_output=True,
                                text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        return result

    constant = workdir / "t.nc"
    base = workdir / "base.nc"
    series = workdir / "in.nc"
    cdo("-f", "nc", "const,1,r4x2", str(constant))
    cdo("-f", "nc", "setreftime,2000-01-01",
        "-settaxis,2000-01-01,1,1mon", "-for,1,12", str(base))
    cdo("-f", "nc", "enlarge," + str(constant), str(base), str(series))
    return {"dir": workdir, "series": series}


@pytest.fixture(scope="module")
def uneven(tmp_path_factory):
    """A series with a != b, so that swapping the two companions is visible.

    ``in.nc`` above has a = 1 and b = 1, which makes ``subtrend in a b`` and
    ``subtrend in b a`` return the same field — a sample on which the swap
    measurement cannot fail, which is the worst kind of fixture for it.
    Values 100, 103, 106 ... give a = 100 and b = 3.
    """
    if shutil.which("cdo") is None:
        pytest.skip("needs an installed CDO")
    workdir = tmp_path_factory.mktemp("regression_uneven")
    series = workdir / "nd.nc"
    result = subprocess.run(
        ["cdo", "-s", "-f", "nc", "setreftime,2000-01-01",
         "-settaxis,2000-01-01,1,1mon", "-expr,seq=100+3*(seq-1)",
         "-for,1,12", str(series)],
        capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr

    intercept, slope = workdir / "a.nc", workdir / "b.nc"
    result = subprocess.run(
        ["cdo", "-s", "trend", str(series), str(intercept), str(slope)],
        capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    return {"dir": workdir, "series": series,
            "intercept": intercept, "slope": slope}


def field_means(path):
    """The field mean at each timestep of ``path``, as floats."""
    result = subprocess.run(["cdo", "-s", "output", "-fldmean", str(path)],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    return [float(value) for value in result.stdout.split()]


# --- (1) the section, and that all of it is reachable ------------------------

def test_the_section_is_five_operators():
    assert len(SECTION) == 5


@pytest.mark.parametrize("operator", SECTION)
def test_every_regression_operator_is_filed_under_regression(operator):
    assert OPERATOR_SCHEMA[operator].category is NCExplorerCategory.REGRESSION


def test_the_curated_list_is_the_whole_section():
    """No shortlist to make when the curated list and "All Regression" agree.

    ``regres`` and ``addtrend`` were missing, which left one half of each of the
    section's two pairs reachable only through ``menu_operators``' ``rest``.
    """
    assert sorted(OPERATOR_CATEGORIES[NCExplorerCategory.REGRESSION]) == SECTION


def test_the_whole_section_is_top_level_in_the_menu():
    """Nothing behind an "All …" submenu, because there is nothing to demote."""
    top, rest = menu_operators(NCExplorerCategory.REGRESSION)
    assert sorted(top) == SECTION
    assert rest == []


@pytest.mark.parametrize("operator", SECTION)
def test_the_arity_matches_the_catalog(operator):
    spec = OPERATOR_SCHEMA[operator]
    assert (spec.nin, spec.nout) == ARITY[operator]


@cdo_required
@pytest.mark.parametrize("module,names", sorted(REGRESSION_BY_MODULE.items()))
def test_the_module_titles_still_agree_with_the_installed_binary(module, names):
    """Re-probed rather than trusted: a title that changed upstream is silent."""
    operator = names.split()[0]
    out = subprocess.run(["cdo", "-h", operator], capture_output=True,
                         text=True, timeout=30)
    assert module in (out.stdout + out.stderr).replace("\n", " ")


# --- (2) `equal` is declared, once, in keyword form --------------------------

@pytest.mark.parametrize("operator", SECTION)
def test_every_operator_declares_equal(operator):
    params = OPERATOR_SCHEMA[operator].params
    assert [param.name for param in params] == ["equal"]
    assert params[0].kind == "bool"
    assert params[0].optional


@pytest.mark.parametrize("operator", SECTION)
def test_the_declaration_is_shared_rather_than_copied(operator):
    """One object, five entries — the same rule ``_ETCCDI_BOOTSTRAP_PARAMS``
    follows, and what makes the five structurally incapable of disagreeing."""
    assert _PARAM_SPECS[operator] is _REGRESSION_PARAMS


@pytest.mark.parametrize("operator", SECTION)
def test_the_token_is_the_keyword_form(operator):
    """``equal=false``, never a bare ``false``. This is the whole fix."""
    assert parameter_tokens(operator, ["false"]) == ["equal=false"]
    assert parameter_tokens(operator, ["true"]) == ["equal=true"]


@pytest.mark.parametrize("operator", SECTION)
@pytest.mark.parametrize("supplied,expected", [
    ("yes", "equal=true"), ("no", "equal=false"),
    ("on", "equal=true"), ("off", "equal=false"),
    ("1", "equal=true"), ("0", "equal=false"),
])
def test_surface_spellings_are_normalised_to_what_cdo_accepts(
        operator, supplied, expected):
    """A surface may offer "yes"; CDO may not be handed it.

    ``cdo detrend,equal=yes`` is "Boolean parameter >yes< contains invalid
    characters!" — measured below.
    """
    assert parameter_tokens(operator, [supplied]) == [expected]


@pytest.mark.parametrize("operator", SECTION)
def test_an_unset_optional_keyword_renders_to_nothing(operator):
    """No token at all, so no trailing comma and nothing after it shifts."""
    assert parameter_tokens(operator, [""]) == []
    assert parameter_tokens(operator, ["   "]) == []


@pytest.mark.parametrize("operator", SECTION)
def test_an_unreadable_value_is_refused_before_the_run(operator):
    assert invalid_parameter_values(operator, ["garbage"])
    assert invalid_parameter_values(operator, ["2"])
    assert not invalid_parameter_values(operator, ["false"])


@pytest.mark.parametrize("operator,expected", [
    ("detrend", "ifile ofile [,equal=true]"),
    ("regres", "ifile ofile [,equal=true]"),
    ("trend", "ifile ofile1 ofile2 [,equal=true]"),
    ("addtrend", "ifile1 ifile2 ifile3 ofile [,equal=true]"),
    ("subtrend", "ifile1 ifile2 ifile3 ofile [,equal=true]"),
])
def test_the_syntax_line_spells_the_parameter_the_way_it_is_written(
        operator, expected):
    """"ifile ofile" was what a user saw, and it left out the only parameter.

    Spelled ``equal=true`` rather than ``equal``, for the reason
    ``bitrounding`` records: a usage line a user can copy has to be one CDO
    parses, and the bare name is a parse error.
    """
    assert operator_syntax(operator) == expected


# --- (3) the grammar, re-measured against the binary -------------------------

@cdo_required
@pytest.mark.parametrize("spelling", ["detrend,true", "detrend,equal",
                                      "regres,true", "regres,equal"])
def test_the_positional_and_flag_spellings_are_parse_errors(spelling, sample):
    """Why the parameter is ``_KEYWORD``. The synopsis reads like both."""
    out = subprocess.run(
        ["cdo", "-s", spelling, str(sample["series"]),
         str(sample["dir"] / "reject.nc")],
        capture_output=True, text=True, timeout=60)
    combined = out.stdout + out.stderr
    assert out.returncode != 0
    assert "missing '=' in key/value string" in combined
    assert "Parse error!" in combined


@cdo_required
def test_a_non_boolean_value_is_refused_by_cdo_itself(sample):
    """Which is why ``parameter_tokens`` normalises rather than passes through."""
    out = subprocess.run(
        ["cdo", "-s", "detrend,equal=yes", str(sample["series"]),
         str(sample["dir"] / "yes.nc")],
        capture_output=True, text=True, timeout=60)
    assert out.returncode != 0
    assert "Boolean parameter >yes< contains invalid characters!" in (
        out.stdout + out.stderr)


@cdo_required
@pytest.mark.parametrize("value", ["true", "false", "1", "0"])
def test_the_keyword_spellings_cdo_accepts(value, sample):
    """The four the bool reader takes, which is what the schema normalises to."""
    out = subprocess.run(
        ["cdo", "-s", f"detrend,equal={value}", str(sample["series"]),
         str(sample["dir"] / f"ok_{value}.nc")],
        capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stdout + out.stderr


@cdo_required
def test_a_half_filled_parameter_aborts_rather_than_hanging(sample):
    """``equal=`` with no value. Worth knowing which of the two shapes it is:
    nineteen other operators hang forever on a blank parameter."""
    out = subprocess.run(
        ["cdo", "-s", "detrend,equal=", str(sample["series"]),
         str(sample["dir"] / "blank.nc")],
        capture_output=True, text=True, timeout=60)
    assert out.returncode != 0
    assert "Missing value for parameter key >equal<!" in out.stdout + out.stderr


@cdo_required
def test_equal_changes_the_answer_on_a_monthly_axis(sample):
    """1.0 against 1.01672 — the measurement the help text quotes.

    If this ever comes out equal, the parameter has stopped doing anything and
    every description in the section is making a promise the binary is not
    keeping.
    """
    default = sample["dir"] / "slope_default.nc"
    uneven_spacing = sample["dir"] / "slope_false.nc"
    subprocess.run(["cdo", "-s", "regres", str(sample["series"]), str(default)],
                   check=True, timeout=60)
    subprocess.run(["cdo", "-s", "regres,equal=false", str(sample["series"]),
                    str(uneven_spacing)], check=True, timeout=60)

    assert field_means(default)[0] == pytest.approx(1.0, abs=1e-5)
    assert field_means(uneven_spacing)[0] == pytest.approx(1.01672, abs=1e-5)


# --- (4) trend's two outputs -------------------------------------------------

def test_trend_declares_which_output_is_which():
    a, b = operator_outputs("trend")
    assert "intercept" in a.role
    assert "slope" in b.role
    assert a.suffix and b.suffix and a.suffix != b.suffix


def test_the_trend_description_says_which_file_is_which():
    description = OPERATOR_SCHEMA["trend"].description
    assert "outfile1 is a, the intercept" in description
    assert "outfile2 is b, the slope" in description


def test_the_trend_description_says_nothing_inside_the_files_records_it():
    """The reason the filenames matter: the two headers differ only in the
    name of the file."""
    description = OPERATOR_SCHEMA["trend"].description
    assert "nothing inside either one records which it is" in description
    assert "units per timestep" in description


def test_regres_is_described_as_trends_slope_alone():
    assert "trend's second output on its own" in OPERATOR_SCHEMA["regres"].description


@cdo_required
def test_the_two_outputs_are_indistinguishable_by_content(sample):
    """Same variable name, same units, one timestep each — measured.

    This is the claim the description rests on. If CDO ever starts labelling
    them, the description becomes wrong rather than merely cautious.
    """
    a, b = sample["dir"] / "ta.nc", sample["dir"] / "tb.nc"
    subprocess.run(["cdo", "-s", "trend", str(sample["series"]), str(a), str(b)],
                   check=True, timeout=60)

    def probe(operator, path):
        out = subprocess.run(["cdo", "-s", operator, str(path)],
                             capture_output=True, text=True, timeout=60)
        return out.stdout.strip()

    assert probe("showname", a) == probe("showname", b)
    assert probe("ntime", a) == probe("ntime", b) == "1"


@cdo_required
def test_trend_refuses_a_single_output(sample):
    """The arity the app enforces before the run is the one CDO enforces."""
    out = subprocess.run(
        ["cdo", "-s", "trend", str(sample["series"]),
         str(sample["dir"] / "only_one.nc")],
        capture_output=True, text=True, timeout=60)
    assert out.returncode != 0
    assert "Missing inputs" in out.stdout + out.stderr


@cdo_required
def test_trends_second_output_is_what_regres_returns(uneven):
    """Which is the check that the two outputs are not the other way round."""
    alone = uneven["dir"] / "regres_alone.nc"
    subprocess.run(["cdo", "-s", "regres", str(uneven["series"]), str(alone)],
                   check=True, timeout=60)
    assert field_means(alone) == pytest.approx(field_means(uneven["slope"]))
    assert field_means(uneven["intercept"])[0] == pytest.approx(100.0)
    assert field_means(uneven["slope"])[0] == pytest.approx(3.0)


# --- (5) addtrend/subtrend's three slots -------------------------------------

@pytest.mark.parametrize("operator", TRENDARITH)
def test_the_three_slots_are_declared(operator):
    """"Input 1"/"Input 2"/"Input 3" said nothing, and the order is the operator."""
    slots = operator_inputs(operator)
    assert len(slots) == 3
    assert all(slot.role and not slot.role.startswith("Input ") for slot in slots)


@pytest.mark.parametrize("operator", TRENDARITH)
def test_the_slots_name_a_and_b_and_say_which_output_each_comes_from(operator):
    series, intercept, slope = operator_inputs(operator)
    assert "series" in series.role.lower()
    assert intercept.role.startswith("a —") and "outfile1" in intercept.role
    assert slope.role.startswith("b —") and "outfile2" in slope.role
    # The one a reader is likeliest to get wrong: slot 3 wants the *second*
    # file of the pair, and the recipe below cannot say so on its own.
    assert "second" in slope.field


@pytest.mark.parametrize("operator", TRENDARITH)
def test_the_coefficient_slots_carry_the_exact_recipe(operator):
    """Exact rather than advisory, like ``collgrid``'s: there is one command
    that builds these two files and it builds both at once."""
    _, intercept, slope = operator_inputs(operator)
    assert intercept.recipe == "trend {in1} afile bfile"
    assert slope.recipe == intercept.recipe


@pytest.mark.parametrize("operator", TRENDARITH)
def test_the_coefficient_slots_have_distinct_keys(operator):
    """What lets ``operator_lab`` build one pair and route each file to the
    slot that wants it, instead of guessing from the operator's name."""
    keys = [slot.key for slot in operator_inputs(operator)]
    assert keys == ["series", "trend_intercept", "trend_slope"]


@pytest.mark.parametrize("operator", TRENDARITH)
def test_the_declared_recipe_exempts_them_from_the_length_check(operator):
    """And that is the right answer, not a side effect worked around.

    a and b hold one timestep by construction. Length-comparing them against an
    N-step series would fire on every correct call — the thing
    ``core/pairing.py`` exists to avoid.
    """
    assert pairs_must_match(operator) is False


@pytest.mark.parametrize("operator", TRENDARITH)
def test_the_description_states_the_order_and_that_a_swap_is_silent(operator):
    description = OPERATOR_SCHEMA[operator].description
    assert "infile2 must be trend's outfile1" in description
    assert "infile3 its outfile2" in description
    assert "Nothing checks it" in description
    # It must not be possible to read this and think CDO will object.
    assert "exited 0" in description


@cdo_required
@pytest.mark.parametrize("operator", TRENDARITH)
def test_cdo_accepts_the_wrong_companions_silently(operator, uneven):
    """The finding that matters most in the section, re-measured.

    Right order, swapped order and raw-series-in-both all exit 0. Only the
    numbers differ, and only the first set of numbers is correct.
    """
    def run(second, third, name):
        target = uneven["dir"] / f"{operator}_{name}.nc"
        out = subprocess.run(
            ["cdo", "-s", "-O", operator, str(uneven["series"]),
             str(second), str(third), str(target)],
            capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, out.stdout + out.stderr
        return field_means(target)

    correct = run(uneven["intercept"], uneven["slope"], "ok")
    swapped = run(uneven["slope"], uneven["intercept"], "swapped")
    nonsense = run(uneven["series"], uneven["series"], "nonsense")

    # Every one of the three ran. Nothing in the exit code, and nothing in the
    # output, tells the two wrong ones apart from the right one.
    assert len(correct) == len(swapped) == len(nonsense) == 12
    assert correct != swapped
    assert correct != nonsense
    if operator == "subtrend":
        # The correct call removes the trend exactly; the wrong ones do not.
        assert correct == pytest.approx([0.0] * 12, abs=1e-6)


@cdo_required
def test_detrend_equals_trend_then_subtrend(uneven):
    """The Detrend page's Note, which is the reason both spellings exist."""
    direct = uneven["dir"] / "direct.nc"
    staged = uneven["dir"] / "staged.nc"
    subprocess.run(["cdo", "-s", "-O", "detrend", str(uneven["series"]),
                    str(direct)], check=True, timeout=60)
    subprocess.run(["cdo", "-s", "-O", "subtrend", str(uneven["series"]),
                    str(uneven["intercept"]), str(uneven["slope"]),
                    str(staged)], check=True, timeout=60)
    assert field_means(direct) == pytest.approx(field_means(staged))


def test_the_detrend_description_carries_the_memory_note():
    """The one thing the Detrend page says that no surface repeated."""
    description = OPERATOR_SCHEMA["detrend"].description
    assert "every timestep in memory at once" in description
    assert "cdo trend infile afile bfile" in description
    assert "cdo subtrend infile afile bfile outfile" in description


# --- (6) the documentation bug -----------------------------------------------

@cdo_required
@pytest.mark.parametrize("operator", TRENDARITH)
def test_cdos_own_help_names_the_wrong_operator(operator):
    """Not a test of this application — a test that the bug is still there.

    Both pages print the synopsis as ``cdo trend[,equal] infile1 infile2
    infile3 outfile``, and so does the reference manual. If CDO ever fixes it,
    ``_REGRESSION_SYNOPSIS_NOTE`` should go rather than quietly become false.
    """
    out = subprocess.run(["cdo", "-h", operator], capture_output=True,
                         text=True, timeout=30)
    synopsis = " ".join(
        (out.stdout + out.stderr).split("SYNOPSIS")[1]
                                 .split("DESCRIPTION")[0].split())

    assert synopsis == "cdo trend[,equal] infile1 infile2 infile3 outfile"
    # The whole of the bug: the operator being documented is not named in its
    # own synopsis, and the operator that *is* named takes one input and writes
    # two outputs rather than three and one.
    assert operator not in synopsis
    assert OPERATOR_SCHEMA["trend"].nin == 1
    assert OPERATOR_SCHEMA["trend"].nout == 2


@pytest.mark.parametrize("operator", TRENDARITH)
def test_the_description_warns_about_that_synopsis(operator):
    description = OPERATOR_SCHEMA[operator].description
    assert "CDO's own help is wrong about this operator's name" in description


@pytest.mark.parametrize("operator", TRENDARITH)
def test_the_syntax_string_is_derived_rather_than_read_off_that_line(operator):
    """Three inputs and one output, which is what the schema says and what the
    binary does — not the (1|2) shape its own help prints."""
    assert operator_syntax(operator).startswith("ifile1 ifile2 ifile3 ofile")


# --- (7) the equal note reaches every one of the five ------------------------

@pytest.mark.parametrize("operator", SECTION)
def test_every_description_states_what_equal_defaults_to(operator):
    description = OPERATOR_SCHEMA[operator].description
    assert "defaults to true" in description
    assert "1.01672" in description


@pytest.mark.parametrize("operator", ["timmean", "eof", "fldcor", "spectrum"])
def test_operators_outside_the_section_get_no_regression_note(operator):
    """The gate is the declared parameter, not the category or a name list —
    ``spectrum`` is here because it declares a parameter *called* detrend."""
    assert "1.01672" not in OPERATOR_SCHEMA[operator].description


# --- (8) the two-output compile path ----------------------------------------

def _graph(operator, sink_paths, nin=1):
    graph = ModelGraph()
    node = graph.add("OPERATOR", operator=operator)
    for index in range(nin):
        source = graph.add("SOURCE", path=f"/tmp/in{index + 1}.nc")
        graph.connect(source.id, 0, node.id, index)
    for path in sink_paths:
        sink = graph.add("SINK", path=path)
        graph.connect(node.id, 0, sink.id, 0)
    return graph, node


def _compile(graph):
    counter = itertools.count(1)
    return graph.compile(lambda suffix: f"/tmp/t{next(counter)}{suffix}")


def test_a_two_output_node_compiles_to_two_targets():
    """``_output_paths`` returned one path for every ``nout != 0``, and
    ``_resolve_operator_call`` then raised "expected 2 output target(s), got 1"
    at run time — so ``trend`` could not be drawn at all."""
    graph, _ = _graph("trend", ["/tmp/a.nc"])
    request = _compile(graph)[0]
    assert request.nout == 2
    assert len(request.output_files) == 2
    assert request.output_files[0] == "/tmp/a.nc"


def test_a_second_sink_names_the_second_output():
    """Two sinks, two named files. Stopping at the first silently lost one: the
    run succeeded and the file the user called slope.nc did not exist."""
    graph, _ = _graph("trend", ["/tmp/a.nc", "/tmp/b.nc"])
    assert _compile(graph)[0].output_files == ("/tmp/a.nc", "/tmp/b.nc")


def test_an_unnamed_second_output_is_derived_from_the_first():
    """So a user who goes looking can tell which file is which."""
    graph, _ = _graph("trend", ["/tmp/a.nc"])
    assert _compile(graph)[0].output_files[1] == "/tmp/a_slope.nc"


def test_adding_a_second_sink_renames_nothing_that_was_there():
    graph, _ = _graph("trend", ["/tmp/a.nc", "/tmp/b.nc"])
    assert _compile(graph)[0].output_files[0] == "/tmp/a.nc"


def test_more_sinks_than_outputs_is_reported_before_the_run():
    """Phrased as ``_resolve_operator_call`` phrases the same shortfall."""
    graph, node = _graph("trend", ["/tmp/a.nc", "/tmp/b.nc", "/tmp/c.nc"])
    messages = [issue.message for issue in graph.validate()
                if issue.node == node.id]
    assert any("expected 2 output target(s), got 3" in message
               for message in messages)


def test_the_shortfall_check_is_derived_from_nout_and_not_from_a_name():
    """A (1|1) node wired to two sinks is the same mistake and says so."""
    graph, node = _graph("detrend", ["/tmp/a.nc", "/tmp/b.nc"])
    messages = [issue.message for issue in graph.validate()
                if issue.node == node.id]
    assert any("expected 1 output target(s), got 2" in message
               for message in messages)


@pytest.mark.parametrize("operator", [
    "trend", "eof", "mrotuv", "complextopol", "complextorect", "samplegridicon",
])
def test_every_two_output_operator_compiles_to_its_full_arity(operator):
    """``nout > 1`` is a shape, not an operator: an implementation that
    special-cased ``trend`` or ``eof`` would fail the other four."""
    graph, _ = _graph(operator, ["/tmp/a.nc"])
    request = _compile(graph)[0]
    assert len(request.output_files) == request.nout == 2
    assert len(set(request.output_files)) == 2


def test_addtrend_compiles_with_three_operands_in_slot_order():
    graph, _ = _graph("addtrend", ["/tmp/out.nc"], nin=3)
    request = _compile(graph)[0]
    assert request.nin == 3
    assert request.input_files == ("/tmp/in1.nc", "/tmp/in2.nc", "/tmp/in3.nc")


# --- (9) the command the execution layer actually assembles ------------------

@pytest.fixture(scope="module")
def integration():
    from ncexplorer_toolkit.core.nc_integration import NCExplorerIntegration
    return NCExplorerIntegration()


@pytest.mark.parametrize("operator,inputs,outputs,params,expected", [
    ("detrend", ["in.nc"], ["out.nc"], ["false"],
     "detrend,equal=false in.nc out.nc"),
    ("trend", ["in.nc"], ["a.nc", "b.nc"], [],
     "trend in.nc a.nc b.nc"),
    ("subtrend", ["in.nc", "a.nc", "b.nc"], ["out.nc"], ["false"],
     "subtrend,equal=false in.nc a.nc b.nc out.nc"),
    # The unset optional keyword: no token, and so no trailing comma.
    ("detrend", ["in.nc"], ["out.nc"], [""], "detrend in.nc out.nc"),
    ("addtrend", ["in.nc", "a.nc", "b.nc"], ["out.nc"], [],
     "addtrend in.nc a.nc b.nc out.nc"),
])
def test_the_assembled_command_line(integration, operator, inputs, outputs,
                                    params, expected):
    """The command builder needed no change beyond the schema — asserted here
    rather than claimed, because that is the whole of the fix's reach into it."""
    call = integration._resolve_operator_call(operator, inputs, outputs, params)
    assert " ".join(call.cmd[1:]) == expected


def test_the_arity_refusal_is_phrased_the_way_the_model_builder_phrases_it(
        integration):
    with pytest.raises(ValueError, match=r"expected 2 output target\(s\), got 1"):
        integration._resolve_operator_call("trend", ["in.nc"], ["a.nc"], [])
