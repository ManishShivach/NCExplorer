"""The Miscellaneous section builds tokens CDO accepts, and says what it needs.

Four problems, and the first is the one that made the rest worth chasing.

**The grammars were guesses, and CDO uses three of them.** ``sethalo`` was
declared ``lhalo,rhalo`` positional, ``smooth`` as a single free-text box,
``mrotuvb``'s flag as a one-choice string, and twenty-two of the section's
operators had no declaration at all — so a form offered a free-text field, or
none, for parameters with a real grammar behind them. This section is also
where the assumption "siblings in one module share a grammar" dies: ``strwin``
takes its threshold positionally and ``strbre``, ``strgal`` and ``hurr`` take
the identically-named, identically-documented threshold as ``v=``.

**A blank required parameter hangs CDO rather than failing it.** Nineteen
operators here do this — measured, with stdin closed, no output and no exit.
Every other way of getting a parameter wrong aborts in milliseconds; only the
empty one hangs, and empty is what a form holds before anyone types. The app's
own required-parameter check is the entire defence, which is why it is tested
here against the real specs rather than assumed.

**One operator's only output is a file nothing was tracking.** ``gradsdes`` is
``nout == 0`` and writes ``<stem>.ctl`` into the directory of its *input*, with
no argument that can redirect it. Inputs whose path contains a space are
relocated to a temp alias directory, so for exactly those files the descriptor —
the whole result of the run — landed in the temp directory under the alias's
mangled name.

**Three things the manual says are not what the binary does.** ``uv2vr_cfd``'s
synopsis shows a positional parameter list that is "Parse error!";
``strbre``/``strgal``/``hurr`` are presented as having fixed thresholds, and
``hurr`` documents no parameter at all, yet all three accept and use ``v=``;
and ``topo``'s grid is documented as required and is optional.

Everything asserted here was measured against the installed CDO 2.6.3. The
tests marked ``cdo_required`` re-measure it, because a token that CDO accepts
is worth more than a token that matches a string literal.
"""

import os
import shutil
import subprocess

import pytest

from ncexplorer_toolkit.core.categories import (
    NCExplorerCategory, OPERATOR_CATEGORIES, OPERATOR_SCHEMA, UNIT_FAMILIES,
    _MISC_HANGS_WITHOUT_PARAMETERS, _MODULE_CATEGORY,
    invalid_parameter_values, menu_operators, missing_required_parameters,
    operator_env, operator_inputs, operator_module, operator_syntax,
    parameter_tokens, reads_stdin,
)
from ncexplorer_toolkit.core.nc_integration import NCExplorerIntegration

cdo_required = pytest.mark.skipif(
    shutil.which("cdo") is None, reason="needs an installed CDO")


#: The 54 operators of CDO 2.6.3's Miscellaneous section, by module title as
#: the binary reports it. Written out rather than derived from the schema,
#: because a test that asks the schema what the schema contains asserts
#: nothing.
#:
#: "Wind transformation" appears once here and covers only the five operators
#: CDO's *manual* files under Miscellaneous. The binary prints that same title
#: for two other modules — one of which is the Transformation section's
#: dv2uv/uv2dv — which is exactly why the title is not usable as a key. See
#: ``_MODULE_CATEGORY``.
SECTION_BY_MODULE = {
    "GrADS data descriptor file": "gradsdes",
    "ECHAM standard post processor": "after",
    "Time series filtering": "bandpass lowpass highpass",
    "Grid cell quantities": "gridarea gridweights",
    "Smooth grid points": "smooth smooth9",
    "Difference between timesteps": "deltat",
    "Replace data values": "setvals setrtoc setrtoc2",
    "Get grid cell index": "gridcellindex",
    "Generate a field": "const random topo seq stdatm",
    "Temporal sorting": "timsort",
    "Wind transformation": "uvDestag rotuvNorth projuvLatLon uv2vr_cfd uv2dv_cfd",
    "Backward wind rotation": "rotuvb",
    "Backward rotation of MPIOM data": "mrotuvb",
    "Mass stream function": "mastrfu",
    "Pressure on model levels": "pressure_half pressure delta_pressure",
    "Derived model parameters": "sealevelpressure gheight gheight_half air_density",
    "Potential temperature to in-situ temperature and vice versa": "adisit adipot",
    "Calculates potential density": "rhopot",
    "Histogram": "histcount histsum histmean histfreq",
    "Set the bounds of a field": "sethalo",
    "Windchill temperature": "wct",
    "Frost days where no snow index per time period": "fdns",
    "Strong wind days index per time period": "strwin",
    "Strong breeze days index per time period": "strbre",
    "Strong gale days index per time period": "strgal",
    "Hurricane days index per time period": "hurr",
    "CMOR lite": "cmorlite",
    "Verify grid coordinates": "verifygrid",
    "Change healpix resolution": "hpdegrade hpupgrade",
    "Mirrors data at the equator": "symmetrize",
}

SECTION = sorted(
    name for names in SECTION_BY_MODULE.values() for name in names.split())

#: The two operators of the section deliberately left in Information. Both are
#: ``nout == 0``, both print a report about their input and write nothing —
#: which is what ``info`` and ``griddes`` do. See the note above the
#: ``nout == 0`` branch in ``_infer_category`` for the argument and its
#: counter-argument.
INFORMATION_BY_DESIGN = ("gridcellindex", "verifygrid")


# --------------------------------------------------------------------------
# Placement
# --------------------------------------------------------------------------

def test_the_section_is_fifty_four_operators():
    assert len(SECTION) == 54


@pytest.mark.parametrize("operator", SECTION)
def test_every_operator_is_placed_where_the_manual_puts_it(operator):
    """...with the two ``nout == 0`` diagnostics as the stated exception."""
    expected = (NCExplorerCategory.INFORMATION
                if operator in INFORMATION_BY_DESIGN
                else NCExplorerCategory.MISCELLANEOUS)
    assert OPERATOR_SCHEMA[operator].category is expected


@pytest.mark.parametrize("operator", [
    # Each of these was claimed by a *prefix* before its module was named, and
    # three of the five split a module across two categories.
    "deltat",           # "del"  -> Selection, while timederivative went to
    "timederivative",   # "tim"  -> Statistical values. One module, two wrong
    "delta_pressure",   # "del"  -> Selection, while pressure/pressure_half
                        #           fell through to Miscellaneous
    "setvals",          # "set"  -> Modification, while setrtoc/setrtoc2 did not
    "sethalo",          # named outright in the cascade's exception set
    "mask",             # "mask" -> Modification, though CDO files it in Vargen
])
def test_the_prefix_cascade_no_longer_claims_these(operator):
    assert OPERATOR_SCHEMA[operator].category is NCExplorerCategory.MISCELLANEOUS
    assert _MODULE_CATEGORY[operator_module(operator)] \
        is NCExplorerCategory.MISCELLANEOUS


def test_naming_the_module_kept_its_halves_together():
    """The point of naming a module rather than patching the cascade."""
    for module in ("Difference between timesteps", "Pressure on model levels",
                   "Replace data values", "Set the bounds of a field",
                   "Histogram", "Generate a field"):
        members = [name for name, spec in OPERATOR_SCHEMA.items()
                   if operator_module(name) == module]
        assert members, module
        categories = {OPERATOR_SCHEMA[name].category for name in members}
        assert categories == {NCExplorerCategory.MISCELLANEOUS}, (module, members)


def test_the_wind_transformation_title_is_not_usable_as_a_key():
    """Three unrelated CDO modules print it, so naming it would move too much.

    This is the one place in the section where a per-operator list is right,
    and this test is why: ``dv2uv`` and ``uv2dv`` carry the identical module
    title and belong to CDO's Transformation section.
    """
    sharing = {name for name in OPERATOR_SCHEMA
               if operator_module(name) == "Wind transformation"}
    assert {"uv2dv", "dv2uv", "uv2vr_cfd", "uvDestag"} <= sharing
    assert "Wind transformation" not in _MODULE_CATEGORY
    for name in ("uv2dv", "dv2uv", "uv2dvl", "dv2uvl"):
        assert OPERATOR_SCHEMA[name].category \
            is NCExplorerCategory.TRANSFORMATION, name
    for name in ("uv2vr_cfd", "uv2dv_cfd"):
        assert name in OPERATOR_CATEGORIES[NCExplorerCategory.MISCELLANEOUS]


def test_the_curated_shortlist_is_a_shortlist():
    """Ten picks plus two placements, because the toolbar shows ten.

    The old list was nineteen and the alphabet chose four histogram spellings
    out of it while leaving out smooth, gridarea, sethalo and setvals.
    """
    curated, rest = menu_operators(NCExplorerCategory.MISCELLANEOUS)
    assert (curated + rest)[:10] == [
        "const", "deltat", "gridarea", "random", "sethalo", "setrtoc",
        "setvals", "smooth", "smooth9", "topo",
    ]
    # The two placement-only entries sort last and displace none of the ten.
    assert set(curated) - set((curated + rest)[:10]) == {
        "uv2vr_cfd", "uv2dv_cfd"}


def test_browsing_reaches_the_whole_section():
    """Everything is reachable, shortlisted or not — that is what `rest` is."""
    curated, rest = menu_operators(NCExplorerCategory.MISCELLANEOUS)
    reachable = set(curated) | set(rest)
    missing = [name for name in SECTION
               if name not in INFORMATION_BY_DESIGN and name not in reachable]
    assert missing == []


# --------------------------------------------------------------------------
# The three grammars
# --------------------------------------------------------------------------

def test_every_operator_in_the_section_has_a_verified_declaration():
    """An explicit empty tuple is a claim; a missing key is a gap.

    ``params`` being empty means "measured to take nothing" — which is what
    stops a surface offering a free-text box whose contents CDO will reject or,
    for ``fdns``, silently discard.
    """
    from ncexplorer_toolkit.core.categories import _PARAM_SPECS
    undeclared = [name for name in SECTION if name not in _PARAM_SPECS]
    assert undeclared == []


@pytest.mark.parametrize("operator,values,expected", [
    # -- keyword --
    ("sethalo", ["1", "2", "3", "4", "-9"],
     "sethalo,east=1,west=2,south=3,north=4,value=-9"),
    ("gridarea", ["6371000"], "gridarea,radius=6371000"),
    ("gridcellindex", ["10", "20"], "gridcellindex,lon=10,lat=20"),
    ("symmetrize", ["negative", ""], "symmetrize,lat=negative"),
    ("smooth", ["2", "5deg", "", "gauss", "", ""],
     "smooth,nsmooth=2,radius=5deg,weighted=gauss"),
    ("hpdegrade", ["4", "nested", ""], "hpdegrade,nside=4,order=nested"),
    ("uv2vr_cfd", ["u", "v", "1", "new"],
     "uv2vr_cfd,u=u,v=v,boundOpt=1,outMode=new"),
    # -- positional --
    ("strwin", ["12"], "strwin,12"),
    ("rhopot", ["10"], "rhopot,10"),
    ("gradsdes", ["2"], "gradsdes,2"),
    ("seq", ["1", "10", "2"], "seq,1,10,2"),
    ("lowpass", ["10"], "lowpass,10"),
    ("bandpass", ["1", "10"], "bandpass,1,10"),
    ("setrtoc2", ["0", "1", "5", "9"], "setrtoc2,0,1,5,9"),
    ("histcount", ["-inf,0,inf"], "histcount,-inf,0,inf"),
    ("stdatm", ["0,100,500"], "stdatm,0,100,500"),
    # -- flag --
    ("cmorlite", ["table.txt", "true"], "cmorlite,table.txt,convert"),
    ("cmorlite", ["table.txt", "false"], "cmorlite,table.txt"),
    ("mrotuvb", ["true"], "mrotuvb,noint"),
    ("mrotuvb", [""], "mrotuvb"),
])
def test_the_token_each_grammar_builds(operator, values, expected):
    tokens = parameter_tokens(operator, values)
    built = operator if not tokens else f"{operator},{','.join(tokens)}"
    assert built == expected


def test_strwin_emits_a_bare_number_and_its_siblings_emit_v():
    """The counterexample that killed "one module, one grammar".

    Same parameter name, same meaning, same manual page, opposite spelling.
    """
    assert parameter_tokens("strwin", ["12"]) == ["12"]
    for operator in ("strbre", "strgal", "hurr"):
        assert parameter_tokens(operator, ["12"]) == ["v=12"], operator


@pytest.mark.parametrize("operator,count", [
    ("sethalo", 5), ("smooth", 6), ("gridcellindex", 2), ("symmetrize", 2),
    ("hpdegrade", 3), ("hpupgrade", 2), ("uv2vr_cfd", 4), ("gridarea", 1),
])
def test_a_blank_optional_keyword_vanishes_from_the_token(operator, count):
    """Not ``name=``, and not a placeholder — absent.

    This is what makes keyword parameters individually skippable in a way
    positional ones are not, and it is the property a form depends on when a
    user fills in one field of six.
    """
    assert parameter_tokens(operator, [""] * count) == []


@pytest.mark.parametrize("operator", [
    name for name in SECTION if not OPERATOR_SCHEMA[name].params])
def test_the_operators_that_take_nothing_offer_no_field(operator):
    assert OPERATOR_SCHEMA[operator].params == ()
    assert "," not in operator_syntax(operator).split(" ", 1)[-1]


def test_the_usage_hint_spells_the_parameter_the_way_it_is_written():
    """``operator_syntax`` is what the user reads; it must not lie about form."""
    assert operator_syntax("sethalo").endswith(
        "[,east=<int>][,west=<int>][,south=<int>][,north=<int>]"
        "[,value=<float>]")
    assert operator_syntax("strwin").endswith("[,v]")
    assert operator_syntax("strbre").endswith("[,v=<float>]")
    assert operator_syntax("cmorlite").endswith("table[,convert]")
    assert operator_syntax("gridcellindex") == \
        "ifile [,lon=<float>][,lat=<float>]"


# --------------------------------------------------------------------------
# The doc-vs-binary disagreements
# --------------------------------------------------------------------------

def test_lowpass_and_highpass_are_named_for_the_description_not_the_synopsis():
    """``cdo -h lowpass`` contradicts itself; the descriptions are right.

    SYNOPSIS says ``lowpass,fmin`` / ``highpass,fmax``. The OPERATORS text says
    lowpass passes "frequencies lower than fmax" and highpass "greater than
    fmin". A lowpass is bounded above, so its argument is a maximum — and that
    also agrees with bandpass, whose two arguments are fmin,fmax in that order.
    """
    assert [p.name for p in OPERATOR_SCHEMA["lowpass"].params] == ["fmax"]
    assert [p.name for p in OPERATOR_SCHEMA["highpass"].params] == ["fmin"]
    assert [p.name for p in OPERATOR_SCHEMA["bandpass"].params] == \
        ["fmin", "fmax"]
    # The contradiction is recorded where a reader will find it.
    for operator in ("lowpass", "highpass"):
        assert "synopsis" in OPERATOR_SCHEMA[operator].params[0].help.lower()


def test_the_undocumented_thresholds_are_declared_and_say_so():
    """The manual presents these as fixed; ``hurr`` documents no parameter."""
    for operator in ("strbre", "strgal", "hurr"):
        param, = OPERATOR_SCHEMA[operator].params
        assert param.name == "v"
        assert param.optional
        assert "undocumented" in param.help.lower(), operator


def test_topos_grid_is_optional_against_the_manual():
    """``cdo topo`` alone exits 0 and writes a global half-degree grid."""
    param, = OPERATOR_SCHEMA["topo"].params
    assert param.name == "grid" and param.optional
    assert operator_syntax("topo") == "ofile [,grid]"


def test_seq_uses_the_manuals_parameter_names():
    """``start,end[,inc]`` — the names are user-visible, so they are CDO's."""
    assert [p.name for p in OPERATOR_SCHEMA["seq"].params] == \
        ["start", "end", "inc"]


def test_gridweights_takes_nothing_although_its_sibling_takes_a_radius():
    """One synopsis documents the pair; the binary disagrees, with a reason.

    ``gridweights`` returns weights normalised to sum to 1, so the planet
    radius cancels out of every one of them — which is why it is not offered
    there and PLANET_RADIUS is declared on ``gridarea`` alone.
    """
    assert OPERATOR_SCHEMA["gridweights"].params == ()
    assert [p.name for p in OPERATOR_SCHEMA["gridarea"].params] == ["radius"]
    assert [e.name for e in operator_env("gridarea")] == ["PLANET_RADIUS"]
    assert operator_env("gridweights") == ()


# --------------------------------------------------------------------------
# The hang, and what stands between a user and it
# --------------------------------------------------------------------------

@pytest.mark.parametrize("operator", _MISC_HANGS_WITHOUT_PARAMETERS)
def test_every_hanging_operator_is_stopped_by_the_required_check(operator):
    """The app's own check is the whole defence — CDO never returns.

    Not a stdin wait, so closing stdin does not release it and the execution
    layer cannot rescue it after the fact.
    """
    spec = OPERATOR_SCHEMA[operator]
    required = [p for p in spec.params if not p.optional]
    assert required, f"{operator} would hang and nothing would refuse it"
    blanks = [""] * len(spec.params)
    assert missing_required_parameters(operator, blanks), operator
    assert missing_required_parameters(operator, []), operator


@pytest.mark.parametrize("operator", _MISC_HANGS_WITHOUT_PARAMETERS)
def test_the_hang_is_stated_before_the_run(operator):
    assert "hangs" in OPERATOR_SCHEMA[operator].description.lower(), operator


def test_the_infinity_bounds_the_manual_uses_survive_validation():
    """``-inf,0,inf`` is CDO's own spelling for open end bins.

    ``bounds`` is one ``string`` parameter rather than a list of floats for
    exactly this reason: ``invalid_parameter_values`` refuses a non-finite
    number, so declared as a float the app — not CDO — would reject the
    manual's example.
    """
    for operator in ("histcount", "histsum", "histmean", "histfreq"):
        assert invalid_parameter_values(operator, ["-inf,0,inf"]) == []
        assert invalid_parameter_values(operator, ["0,10,20"]) == []


def test_a_single_uvdestag_offset_is_refused():
    """Two offsets or none: one crashes CDO on an internal assertion.

    ``cdo uvDestag,u,v,0.5`` is SIGABRT — "Assertion failed: idx < argc" — not
    an abort a caller can report. Both numbers live in one parameter so the
    half-given case cannot be spelled from a form.
    """
    assert [p.name for p in OPERATOR_SCHEMA["uvDestag"].params] == \
        ["pairs", "offsets"]
    assert parameter_tokens("uvDestag", ["u,v", "-0.5,-0.5"]) == \
        ["u,v", "-0.5,-0.5"]


# --------------------------------------------------------------------------
# Inputs, units and the things that abort before anything runs
# --------------------------------------------------------------------------

def test_wct_and_fdns_disagree_about_temperature_units_and_both_say_so():
    """The trap: ``wct`` reads °C where the whole ECA family reads Kelvin.

    A Kelvin field through wct is not an error. 288 K reads as 288 °C, which is
    outside the documented validity range, so every cell is written missing and
    the run exits 0.
    """
    wct_t, wct_v = operator_inputs("wct")
    assert wct_t.units == "celsius"
    assert wct_v.units == "wind_speed"
    fdns_t, fdns_snow = operator_inputs("fdns")
    assert fdns_t.units == "kelvin"
    assert fdns_snow.units == "snow_amount"
    # The two families must not accept each other, or the check is decorative.
    celsius = set(UNIT_FAMILIES["celsius"].accepts)
    kelvin = set(UNIT_FAMILIES["kelvin"].accepts)
    assert celsius.isdisjoint(kelvin)


@pytest.mark.parametrize("operator", ["strwin", "strbre", "strgal", "hurr"])
def test_the_wind_indices_say_vx_is_derived_and_how_to_derive_it(operator):
    """VX is not a field a model writes: it is sqrt(u^2+v^2) over the day."""
    slot, = operator_inputs(operator)
    assert slot.units == "wind_speed"
    assert slot.recipe
    assert "sqrt" in slot.recipe and "daymax" in slot.recipe


@pytest.mark.parametrize("operator,wanted", [
    ("adisit", "tho"), ("adipot", "sao"), ("rhopot", "to"),
])
def test_the_ocean_operators_name_the_fields_they_abort_without(operator, wanted):
    """"Sea water salinity not found!" arrives after a run and names nothing."""
    slot, = operator_inputs(operator)
    assert wanted in slot.field
    assert "salinity" in slot.field


@pytest.mark.parametrize("operator", [
    "pressure", "pressure_half", "delta_pressure", "sealevelpressure",
    "gheight", "gheight_half", "air_density"])
def test_the_hybrid_level_operators_state_the_requirement(operator):
    slot, = operator_inputs(operator)
    assert "hybrid sigma" in slot.field


def test_mrotuvb_says_which_file_holds_which_component():
    u, v = operator_inputs("mrotuvb")
    assert u.role.startswith("U") and v.role.startswith("V")
    assert "Arakawa C" in u.field


def test_mastrfu_names_the_zonal_mean_it_needs():
    slot, = operator_inputs("mastrfu")
    assert "zonmean" in slot.recipe
    assert "pressure levels" in slot.field


def test_after_gets_a_stdin_row_rather_than_a_button_that_does_nothing():
    """Its controls are a namelist on stdin, and nothing offered a way in.

    Measured: with stdin closed it does not hang — it prints its default
    namelist and copies the fields through. So it was never a freeze; it was
    simply impossible to steer.
    """
    assert reads_stdin("after")
    assert reads_stdin("afterburner")
    description = OPERATOR_SCHEMA["after"].description.lower()
    assert "namelist" in description and "standard input" in description
    assert "cannot be piped" in description


# --------------------------------------------------------------------------
# What the execution layer builds — against the real binary
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def integration():
    return NCExplorerIntegration()


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    path = tmp_path_factory.mktemp("misc") / "in.nc"
    subprocess.run(["cdo", "-s", "-f", "nc", "random,r36x18", str(path)],
                   check=True, capture_output=True)
    return str(path)


@pytest.fixture(scope="module")
def series(tmp_path_factory):
    """A 64-step daily series, for the Filter module.

    ``bandpass`` and friends abort with "Number of time steps <= 1!" against a
    single-step field, which is what ``random`` produces.
    """
    path = tmp_path_factory.mktemp("misc_series") / "series.nc"
    subprocess.run(
        ["cdo", "-s", "-f", "nc", "-settaxis,2000-01-01,00:00:00,1day",
         "-duplicate,64", "-random,r18x9,1", str(path)],
        check=True, capture_output=True)
    return str(path)


@cdo_required
@pytest.mark.parametrize("operator", sorted(SECTION_BY_MODULE))
def test_the_module_titles_still_agree_with_the_installed_binary(operator):
    """Read back off the binary, so a CDO that renames a module fails here."""
    name = SECTION_BY_MODULE[operator].split()[0]
    out = subprocess.run(["cdo", "--help", name], capture_output=True,
                         text=True).stdout
    assert operator in out, (name, out[:200])


@cdo_required
@pytest.mark.parametrize("operator,params", [
    ("gridcellindex", ["10", "20"]),
    ("verifygrid", []),
    ("gradsdes", ["2"]),
])
def test_a_nout0_operator_builds_a_command_with_no_output_path(
        integration, sample, operator, params):
    call = integration._resolve_operator_call(operator, [sample], [], params)
    assert call.nout == 0
    # binary, token, one input — and nothing after it.
    assert call.cmd[2:] == [sample], call.cmd


@cdo_required
@pytest.mark.parametrize("operator,params", [
    ("random", ["r18x9", "42"]), ("topo", [""]), ("const", ["5", "r18x9"]),
    ("seq", ["1", "10", "2"]), ("stdatm", ["0,100,500"]),
])
def test_a_generator_builds_a_command_with_no_input_slot(
        integration, tmp_path, operator, params):
    out = str(tmp_path / "gen.nc")
    call = integration._resolve_operator_call(operator, [], [out], params)
    assert call.cmd[2:] == [out], call.cmd


@pytest.mark.parametrize("operator,params,expected", [
    ("random", ("r18x9", "42"), "random,r18x9,42"),
    ("topo", (), "topo"),
    ("const", ("5", "r18x9"), "const,5,r18x9"),
    ("seq", ("1", "10", "2"), "seq,1,10,2"),
    ("stdatm", ("0,100,500",), "stdatm,0,100,500"),
])
def test_the_model_builder_can_start_a_pipeline_from_nothing(
        tmp_path, operator, params, expected):
    """These five are the only operators in the app that begin from no file.

    A graph whose first node has no incoming edge has to validate — the arity
    check is ``supplied != nin``, and ``nin`` is 0 — and compile to a command
    with an output path and no input slot.
    """
    from ncexplorer_toolkit.core.model import (
        ModelGraph, OperatorCatalog, OPERATOR, SINK, command_lines)

    catalog = OperatorCatalog()
    graph = ModelGraph()
    generator = graph.add(OPERATOR, operator=operator, parameters=params,
                          path=str(tmp_path / f"{operator}.nc"),
                          keep_output=True)
    sink = graph.add(SINK, path=str(tmp_path / f"{operator}_final.nc"))
    graph.connect(generator.id, 0, sink.id, 0)

    errors = [i.message for i in graph.validate(catalog) if i.level == "ERROR"]
    assert errors == [], errors

    line, = command_lines(graph.compile(catalog))
    assert line.split()[1] == expected
    # One file token — the output — and nothing standing in for an input.
    assert len(line.split()) == 3, line


@cdo_required
@pytest.mark.parametrize("operator", ["wct", "fdns", "mrotuvb"])
def test_a_two_input_operator_orders_argv_in1_in2_out(
        integration, sample, tmp_path, operator):
    second = str(tmp_path / "in2.nc")
    subprocess.run(["cdo", "-s", "-f", "nc", "random,r36x18", second],
                   check=True, capture_output=True)
    out = str(tmp_path / "out.nc")
    params = ["true"] if operator == "mrotuvb" else []
    call = integration._resolve_operator_call(
        operator, [sample, second], [out], params)
    assert call.cmd[2:] == [sample, second, out], call.cmd


@cdo_required
@pytest.mark.parametrize("operator,params", [
    ("sethalo", ["1", "1", "1", "1", ""]),
    ("smooth", ["2", "5deg", "", "gauss", "", ""]),
    ("gridarea", ["6371000"]),
    ("gridweights", []),
    ("strwin", ["12"]),
    ("strbre", ["12"]),
    ("strgal", ["12"]),
    ("hurr", ["12"]),
    ("histcount", ["-inf,0,inf"]),
    ("setvals", ["0,1"]),
    ("setrtoc", ["0", "1", "5"]),
    ("symmetrize", ["negative", ""]),
    ("deltat", []),
    ("timsort", []),
    ("smooth9", []),
])
def test_cdo_accepts_the_token_the_schema_builds(
        integration, sample, tmp_path, operator, params):
    """The point of the whole exercise: not a string match, a run.

    A token that matches a literal proves the schema agrees with itself. This
    proves it agrees with CDO.
    """
    out = str(tmp_path / f"{operator}.nc")
    result = integration.execute_operator(
        operator, input_files=[sample], output_files=[out],
        extra_parameters=list(params))
    assert result, f"{operator}: {result.stderr or result.stdout}"
    assert os.path.exists(out)


@cdo_required
@pytest.mark.parametrize("operator,params", [
    ("bandpass", ["1", "10"]),
    ("lowpass", ["10"]),
    ("highpass", ["1"]),
    ("histcount", ["-inf,0,inf"]),
])
def test_cdo_accepts_the_filter_tokens_over_a_real_series(
        integration, series, tmp_path, operator, params):
    """Same claim as above, for the operators that need more than one step."""
    out = str(tmp_path / f"{operator}.nc")
    result = integration.execute_operator(
        operator, input_files=[series], output_files=[out],
        extra_parameters=list(params))
    assert result, f"{operator}: {result.stderr or result.stdout}"
    assert os.path.exists(out)


@cdo_required
@pytest.mark.parametrize("operator,roles", [
    ("wct", ("Temperature", "Wind speed")),
    ("fdns", ("TN", "Surface snow")),
    ("mrotuvb", ("U —", "V —")),
])
def test_the_pairing_check_names_the_slots_it_is_comparing(
        tmp_path, operator, roles):
    """Declaring the slots enrols these three in ``check_pairing`` for free.

    Which operators are checked is derived from the schema — two or more input
    slots, none carrying a recipe — so the three two-input operators of this
    section qualified the moment their slots were declared. What the
    declaration buys on top of that is the wording: the complaint names
    "Temperature — in °C" and "Wind speed — in m/s" rather than "Input 1" and
    "Input 2", which for ``mrotuvb`` is the difference between a message a user
    can act on and one they cannot.
    """
    from ncexplorer_toolkit.core.pairing import check_pairing

    small = str(tmp_path / "small.nc")
    large = str(tmp_path / "large.nc")
    subprocess.run(["cdo", "-s", "-f", "nc", "random,r18x9", small],
                   check=True, capture_output=True)
    subprocess.run(["cdo", "-s", "-f", "nc", "random,r36x18", large],
                   check=True, capture_output=True)

    assert check_pairing(operator, [small, small]) == []
    problems = check_pairing(operator, [small, large])
    assert problems, operator
    message = " ".join(str(p) for p in problems)
    for role in roles:
        assert role in message, (role, message)


@cdo_required
def test_the_nout0_operators_stdout_reaches_the_caller(integration, sample):
    """It is the entire result of the run; a GUI that discards it shows nothing."""
    result = integration.execute_operator(
        "gridcellindex", input_files=[sample], output_files=[],
        extra_parameters=["10", "20"])
    assert result
    assert result.stdout.strip().isdigit(), result.stdout

    result = integration.execute_operator(
        "verifygrid", input_files=[sample], output_files=[])
    assert result
    assert "Grid consists of" in result.stdout
    assert len(result.stdout.splitlines()) > 1


@cdo_required
def test_gradsdes_writes_its_descriptor_beside_the_users_file(
        integration, tmp_path):
    """Even when the path has a space, which is when it used to be lost.

    A plain file alias put the descriptor in the temp alias directory under the
    alias's mangled name — ``in_1_sample.ctl`` — and it is the only thing the
    run produces. The input is aliased by *directory* instead, so CDO's write
    lands back in the user's own folder under the user's own name.
    """
    spaced = tmp_path / "with space"
    spaced.mkdir()
    target = spaced / "sample.nc"
    # Built somewhere plain and copied in: CDO cannot write to a path with a
    # space in it either. ``cdo random,r18x9 "…/with space/sample.nc"`` is
    # "Bracket not closed" — CDO re-splits its own command line — which is the
    # same reason the input has to be aliased at all, seen from the other end.
    plain = tmp_path / "plain.nc"
    subprocess.run(["cdo", "-s", "-f", "nc", "random,r18x9", str(plain)],
                   check=True, capture_output=True)
    shutil.copy(plain, target)

    result = integration.execute_operator(
        "gradsdes", input_files=[str(target)], output_files=[])
    assert result, result.stderr
    assert (spaced / "sample.ctl").is_file(), sorted(
        p.name for p in spaced.iterdir())


@cdo_required
def test_planet_radius_reaches_cdo_through_the_environment(
        integration, sample, tmp_path):
    out = str(tmp_path / "area.nc")
    result = integration.execute_operator(
        "gridarea", input_files=[sample], output_files=[out],
        env={"PLANET_RADIUS": "1234567"})
    assert result
    assert "PLANET_RADIUS" in result.stdout + result.stderr


@cdo_required
@pytest.mark.parametrize("token,rejected", [
    # The four-argument positional form the old lhalo/rhalo declaration would
    # have grown into. Two arguments *are* accepted (see the test below); three
    # and four are not, which is why the keyword form is the only one that can
    # reach south and north.
    ("sethalo,1,1,1,1", "positional 4-arg"),
    ("sethalo,1,1,1", "positional 3-arg"),
    ("strwin,v=12", "keyword on the positional one"),
    ("gridarea,6371000", "positional on the keyword one"),
    ("strbre,12", "positional on a keyword one"),
    ("gridweights,radius=6371000", "a radius on the operator that takes none"),
    ("uv2vr_cfd,u,v,1,1", "the manual's positional synopsis"),
    ("smooth,2", "a bare number in the old free-text options box"),
    ("gradsdes,mapversion=2", "keyword on the positional one"),
    ("seq,start=1,end=10", "keyword on the positional one"),
    ("hpdegrade,4", "positional on the keyword one"),
    ("symmetrize,negative", "positional on the keyword one"),
])
def test_the_spellings_the_schema_avoids_are_still_the_wrong_ones(
        sample, tmp_path, token, rejected):
    """Re-measured, so a CDO that changes its grammar fails here.

    Each of these is a call some earlier or more obvious version of the schema
    would have built. They are passed to CDO verbatim rather than through the
    schema, which is the only way to assert that the alternative is *wrong*
    rather than merely not chosen — a test that only checks what the schema
    emits cannot tell a decision from a coincidence.
    """
    out = str(tmp_path / "x.nc")
    proc = subprocess.run(["cdo", token, sample, out],
                          capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=30)
    assert proc.returncode != 0, f"{token} was accepted; {rejected} is no longer wrong"


@cdo_required
def test_the_two_argument_sethalo_works_and_is_still_not_enough(
        sample, tmp_path):
    """The old declaration was not broken — it was incomplete.

    ``cdo sethalo,1,1`` exits 0 and widens 36x18 to 38x18, the same as
    ``east=1,west=1``; the binary accepts a two-argument positional form its
    own help does not document. What it cannot do is reach south or north, and
    there is no positional spelling that can — three and four arguments are
    both "Parse error!". That is the reason for the change, and stating it as a
    measurement keeps the comment in ``_PARAM_SPECS`` honest.
    """
    def xsize(path):
        out = subprocess.run(["cdo", "griddes", path],
                             capture_output=True, text=True).stdout
        return next(line.split("=")[1].strip() for line in out.splitlines()
                    if line.strip().startswith("xsize"))

    positional = str(tmp_path / "pos.nc")
    keyword = str(tmp_path / "kw.nc")
    assert subprocess.run(["cdo", "-s", "sethalo,1,1", sample, positional],
                          capture_output=True).returncode == 0
    assert subprocess.run(["cdo", "-s", "sethalo,east=1,west=1", sample, keyword],
                          capture_output=True).returncode == 0
    assert xsize(positional) == xsize(keyword) == "38"
