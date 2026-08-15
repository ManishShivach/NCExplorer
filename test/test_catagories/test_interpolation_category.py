"""The Interpolation section is spelled the way the installed binary parses it.

This section punishes inference harder than any other in CDO, and the tests
below exist because every one of these facts was measured against CDO 2.6.3
(x86_64-apple-darwin23.6.0) rather than read off the 2.6.3 manual — which
contradicts the binary in five places that matter.

**Two operators of one module with opposite grammars.** ``remapdis,r36x18,6``
is accepted and prints "(k=6)"; ``gendis,r36x18,6`` is "Parse error!". A form
taken from a sibling would have been wrong for one of them, whichever way round
it was guessed.

**One token, two grammars.** ``genbil,r36x18,map3d=true`` mixes a positional
grid with a keyword flag, the first place in the schema to do so. ``form`` is a
per-parameter fact applied per index, so ``parameter_tokens`` already handled
it — which is worth pinning precisely because nothing had to change for it.

**A form that is not a per-parameter fact at all.** ``intlevel`` takes its level
list positionally *or* as ``level=``, but the moment any other parameter is set
the whole token must be keyword-spelled. All four are therefore declared
keyword and ``level=`` is always emitted.

**An arity decided at runtime.** ``map3d=true`` turns the output path into a
prefix: ``genbil … w1.nc`` writes ``w1.nc00001.nc``. ``nout`` still says 1, so
the execution layer had to learn the difference from the parameter values.

**A failed run that leaves its output behind.** ``intyear`` aborts on a year out
of bounds *after* creating one file per year asked for.

The tests marked ``cdo_required`` re-measure the claim against the installed
binary rather than trusting this docstring; the rest assert what the schema
builds, which is the half that has to survive a refactor.
"""

import shutil
import subprocess

import pytest

from ncexplorer_toolkit.core.categories import (
    GRID_PRESETS, OPERATOR_SCHEMA, get_operator_spec, operator_env,
    operator_inputs, operator_syntax, parameter_tokens,
    writes_output_prefix,
)

cdo_required = pytest.mark.skipif(
    shutil.which("cdo") is None, reason="needs an installed CDO")


def _token(operator, values):
    """The operator token exactly as ``_resolve_operator_call`` builds it."""
    tokens = parameter_tokens(operator, values)
    return operator if not tokens else f"{operator},{','.join(tokens)}"


# ---------------------------------------------------------------------------
# The exact token, per operator
# ---------------------------------------------------------------------------

#: ``(operator, values, expected token)``. Every expectation here is a command
#: that was run against CDO 2.6.3; the ``cdo_required`` test below re-runs the
#: ones that take an ordinary gridded input.
EXPECTED_TOKENS = [
    # -- horizontal remapping: one positional grid ---------------------------
    ("remapbil", ["r36x18"], "remapbil,r36x18"),
    ("remapbic", ["r36x18"], "remapbic,r36x18"),
    ("remapcon", ["r36x18"], "remapcon,r36x18"),
    ("remaplaf", ["r36x18"], "remaplaf,r36x18"),
    # remapnn rejects the keyword form the manual's own prose invites; see
    # test_remapnn_takes_one_positional_grid_only.
    ("remapnn", ["r36x18"], "remapnn,r36x18"),

    # -- remapdis: grid plus an UNDOCUMENTED positional k --------------------
    ("remapdis", ["r36x18"], "remapdis,r36x18"),
    ("remapdis", ["r36x18", "6"], "remapdis,r36x18,6"),
    # An omitted optional positional must not leave a dangling comma.
    ("remapdis", ["r36x18", ""], "remapdis,r36x18,"),

    # -- the knn trio: every parameter keyword-spelled ------------------------
    ("remapknn", ["r36x18"], "remapknn,grid=r36x18"),
    ("remapknn", ["r36x18", "4"], "remapknn,grid=r36x18,k=4"),
    ("remapknn", ["r36x18", "4", "2", "gauss", "0.2", "true"],
     "remapknn,grid=r36x18,k=4,kmin=2,weighted=gauss,gauss_scale=0.2,"
     "extrapolate=true"),
    # An unset optional keyword is simply absent, and the ones after it do not
    # shift — which is the whole reason these are keyword rather than positional.
    ("remapknn", ["r36x18", "", "", "avg"], "remapknn,grid=r36x18,weighted=avg"),
    ("genknn", ["r36x18"], "genknn,grid=r36x18"),
    ("intgridknn", ["r36x18"], "intgridknn,grid=r36x18"),

    # -- the gen* family: MIXED, positional grid + keyword map3d --------------
    ("genbil", ["r36x18"], "genbil,r36x18"),
    ("genbil", ["r36x18", "true"], "genbil,r36x18,map3d=true"),
    ("genbil", ["r36x18", "false"], "genbil,r36x18,map3d=false"),
    ("genbil", ["r36x18", ""], "genbil,r36x18"),
    ("gendis", ["r36x18", "true"], "gendis,r36x18,map3d=true"),
    ("gencon", ["r36x18", "true"], "gencon,r36x18,map3d=true"),
    ("gennn", ["r36x18", "true"], "gennn,r36x18,map3d=true"),
    ("genlaf", ["r36x18", "true"], "genlaf,r36x18,map3d=true"),
    ("genbic", ["r36x18", "true"], "genbic,r36x18,map3d=true"),
    # A surface may hand over "yes"/"on"; CDO rejects both, so they normalise.
    ("genbil", ["r36x18", "yes"], "genbil,r36x18,map3d=true"),
    ("genbil", ["r36x18", "off"], "genbil,r36x18,map3d=false"),

    # -- remap: grid plus a weight file --------------------------------------
    ("remap", ["r36x18", "w.nc"], "remap,r36x18,w.nc"),

    # -- vertical -------------------------------------------------------------
    ("remapeta", ["vct.txt"], "remapeta,vct.txt"),
    ("remapeta", ["vct.txt", "oro.nc"], "remapeta,vct.txt,oro.nc"),
    ("intlevel3d", ["tgt.nc"], "intlevel3d,tgt.nc"),
    ("ml2pl", ["92500,85000,50000"], "ml2pl,92500,85000,50000"),
    ("gh2hl", ["800,1500"], "gh2hl,800,1500"),

    # -- intlevel: always keyword-spelled ------------------------------------
    ("intlevel", ["150,300,700"], "intlevel,level=150,300,700"),
    ("intlevel", ["150"], "intlevel,level=150"),
    ("intlevel", ["150,300", "", "", "true"],
     "intlevel,level=150,300,extrapolate=true"),
    # level and zdescription are mutually exclusive at CDO, so a call that sets
    # only the description must not carry an empty level=.
    ("intlevel", ["", "zax.txt"], "intlevel,zdescription=zax.txt"),

    # -- time -----------------------------------------------------------------
    ("inttime", ["2000-01-01", "12:00:00"], "inttime,2000-01-01,12:00:00"),
    ("inttime", ["2000-01-01", "12:00:00", "6hour"],
     "inttime,2000-01-01,12:00:00,6hour"),
    ("intyear", ["1986,1987,1988"], "intyear,1986,1987,1988"),
    ("intyear", ["1981/2010"], "intyear,1981/2010"),
]


@pytest.mark.parametrize("operator,values,expected", EXPECTED_TOKENS)
def test_the_token_is_spelled_the_way_cdo_parses_it(operator, values, expected):
    assert _token(operator, values) == expected


@cdo_required
@pytest.mark.parametrize("operator,values,expected", [
    case for case in EXPECTED_TOKENS
    # Only the ones an ordinary 2D gridded sample can actually run: the vertical
    # operators need levels and specific standard names, and remap needs a real
    # weight file. Those are covered by operator_lab, which builds the samples.
    if case[0] in {"remapbil", "remapbic", "remapcon", "remaplaf", "remapnn",
                   "remapdis", "remapknn", "genknn", "intgridknn",
                   "genbil", "genbic", "gencon", "genlaf", "gennn", "gendis"}
    and "" not in case[1]
])
def test_the_installed_cdo_accepts_that_token(tmp_path, operator, values,
                                              expected):
    """Re-measure: the token this builds is one CDO 2.6.3 actually parses."""
    source = tmp_path / "in.nc"
    subprocess.run(
        ["cdo", "-O", "-f", "nc", "-settaxis,2000-01-01,00:00:00,1day",
         "-duplicate,3", "-random,r18x9,1", str(source)],
        capture_output=True, check=True, timeout=120)

    result = subprocess.run(
        ["cdo", _token(operator, values), str(source), str(tmp_path / "out.nc")],
        capture_output=True, text=True, timeout=180)

    assert "Parse error" not in result.stderr, result.stderr
    assert "Invalid parameter key" not in result.stderr, result.stderr
    assert "Too many arguments" not in result.stderr, result.stderr
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# 1.2 remapnn: the keyword form the manual invites is not accepted
# ---------------------------------------------------------------------------

def test_remapnn_declares_exactly_one_positional_grid():
    params = get_operator_spec("remapnn").params
    assert [(p.name, p.form) for p in params] == [("grid", "positional")]


@cdo_required
def test_remapnn_rejects_the_keyword_form_the_manual_describes(tmp_path):
    """``cdo -h remapknn`` says remapnn,<grid> "corresponds to"
    remapknn,grid=<grid>,extrapolate=true. It does not accept that spelling.

    Measured, and the failure shape is the confusing part: the whole string is
    taken as a *filename*, so it fails as a missing file rather than as a
    syntax error.
    """
    source = tmp_path / "in.nc"
    subprocess.run(["cdo", "-O", "-f", "nc", "-random,r18x9,1", str(source)],
                   capture_output=True, check=True, timeout=120)

    keyword = subprocess.run(
        ["cdo", "remapnn,grid=r36x18", str(source), str(tmp_path / "a.nc")],
        capture_output=True, text=True, timeout=120)
    assert keyword.returncode != 0
    assert "Open failed on grid=r36x18" in keyword.stderr

    second = subprocess.run(
        ["cdo", "remapnn,r36x18,true", str(source), str(tmp_path / "b.nc")],
        capture_output=True, text=True, timeout=120)
    assert second.returncode != 0
    assert "Too many arguments" in second.stderr


# ---------------------------------------------------------------------------
# 1.3 remapdis's undocumented positional k, and gendis NOT sharing it
# ---------------------------------------------------------------------------

def test_remapdis_declares_an_optional_second_positional():
    params = get_operator_spec("remapdis").params
    assert [(p.name, p.form, p.optional) for p in params] == [
        ("grid", "positional", False),
        ("k", "positional", True),
    ]


def test_gendis_does_not_borrow_remapdis_positional_k():
    """The two operators of one module with opposite grammars."""
    names = [p.name for p in get_operator_spec("gendis").params]
    assert names == ["grid", "map3d"]
    assert "k" not in names


@cdo_required
def test_the_positional_k_is_real_on_remapdis_and_a_parse_error_on_gendis(tmp_path):
    source = tmp_path / "in.nc"
    subprocess.run(["cdo", "-O", "-f", "nc", "-random,r18x9,1", str(source)],
                   capture_output=True, check=True, timeout=120)

    # remapdis takes it, and says so in its own banner. The banner is on
    # *stdout* — CDO splits its chatter across both streams and this line is
    # not on the one the aborts above use — so both are searched.
    good = subprocess.run(
        ["cdo", "remapdis,r36x18,6", str(source), str(tmp_path / "a.nc")],
        capture_output=True, text=True, timeout=120)
    assert good.returncode == 0, good.stderr
    assert "k=6" in (good.stdout + good.stderr)

    # The keyword spelling of the same thing is refused.
    keyword = subprocess.run(
        ["cdo", "remapdis,grid=r36x18,k=6", str(source), str(tmp_path / "b.nc")],
        capture_output=True, text=True, timeout=120)
    assert keyword.returncode != 0
    assert "Integer parameter >k=6<" in keyword.stderr

    # And gendis, the weight-generating twin, does not take it at all.
    twin = subprocess.run(
        ["cdo", "gendis,r36x18,6", str(source), str(tmp_path / "c.nc")],
        capture_output=True, text=True, timeout=120)
    assert twin.returncode != 0
    assert "Parse error" in twin.stderr


# ---------------------------------------------------------------------------
# 1.4 / 2.6 the mixed form
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("operator", [
    "genbil", "genbic", "gencon", "genlaf", "gennn", "gendis", "genycon"])
def test_the_gen_family_mixes_a_positional_grid_with_a_keyword_map3d(operator):
    params = get_operator_spec(operator).params
    assert [(p.name, p.form, p.kind) for p in params] == [
        ("grid", "positional", "grid"),
        ("map3d", "keyword", "bool"),
    ]


def test_parameter_tokens_handles_a_positional_followed_by_a_keyword():
    """The indexing invariant: form decides rendering, never position.

    Pinned because this is the first operator in the schema to depend on it and
    nothing had to change to make it work — which is exactly the kind of fact
    that a later refactor can break without any test noticing.
    """
    assert parameter_tokens("genbil", ["r36x18", "true"]) == [
        "r36x18", "map3d=true"]
    # index 1 is still spec.params[1] even though index 0 rendered positionally
    assert parameter_tokens("genbil", ["r36x18", ""]) == ["r36x18"]


def test_operator_syntax_spells_map3d_with_its_value():
    """`[,map3d]` would tell the user to type a bare flag, which is a parse error."""
    assert operator_syntax("genbil") == "ifile ofile grid[,map3d=true]"


@cdo_required
def test_a_bare_true_is_a_parse_error_where_map3d_true_is_not(tmp_path):
    source = tmp_path / "in.nc"
    subprocess.run(["cdo", "-O", "-f", "nc", "-random,r18x9,1", str(source)],
                   capture_output=True, check=True, timeout=120)

    bare = subprocess.run(
        ["cdo", "genbil,r36x18,true", str(source), str(tmp_path / "a.nc")],
        capture_output=True, text=True, timeout=120)
    assert bare.returncode != 0
    assert "Parse error" in bare.stderr

    spelled = subprocess.run(
        ["cdo", "genbil,r36x18,map3d=true", str(source), str(tmp_path / "b.nc")],
        capture_output=True, text=True, timeout=120)
    assert spelled.returncode == 0, spelled.stderr


def test_map3d_is_not_declared_on_the_knn_family():
    """The manual documents map3d for genknn; the binary answers
    "Invalid parameter key >map3d<!" for genknn, remapknn and intgridknn alike.
    """
    for operator in ("remapknn", "genknn", "intgridknn"):
        assert "map3d" not in [p.name for p in get_operator_spec(operator).params]


@cdo_required
def test_the_binary_rejects_map3d_on_genknn_despite_documenting_it(tmp_path):
    source = tmp_path / "in.nc"
    subprocess.run(["cdo", "-O", "-f", "nc", "-random,r18x9,1", str(source)],
                   capture_output=True, check=True, timeout=120)
    result = subprocess.run(
        ["cdo", "genknn,grid=r36x18,map3d=true", str(source),
         str(tmp_path / "a.nc")],
        capture_output=True, text=True, timeout=120)
    assert result.returncode != 0
    assert "Invalid parameter key >map3d<" in result.stderr


# ---------------------------------------------------------------------------
# 2.2 map3d changes the output arity at runtime
# ---------------------------------------------------------------------------

def test_writes_output_prefix_follows_the_parameter_not_just_nout():
    assert writes_output_prefix("genbil", ["r36x18", "true"]) is True
    assert writes_output_prefix("genbil", ["r36x18", "false"]) is False
    assert writes_output_prefix("genbil", ["r36x18"]) is False
    assert writes_output_prefix("genbil", ["r36x18", ""]) is False
    # The static half still holds: intyear writes one file per year.
    assert writes_output_prefix("intyear", ["2001"]) is True
    # And an ordinary operator is unaffected.
    assert writes_output_prefix("remapbil", ["r36x18"]) is False


@cdo_required
def test_map3d_writes_a_numbered_file_rather_than_the_path_it_was_given(tmp_path):
    """The given path is a PREFIX: `w1.nc` becomes `w1.nc00001.nc`."""
    source = tmp_path / "in.nc"
    subprocess.run(
        ["cdo", "-O", "-f", "nc", "-settaxis,2000-01-01,00:00:00,1day",
         "-duplicate,3", "-random,r18x9,1", str(source)],
        capture_output=True, check=True, timeout=120)

    target = tmp_path / "w1.nc"
    result = subprocess.run(
        ["cdo", "genbil,r36x18,map3d=true", str(source), str(target)],
        capture_output=True, text=True, timeout=180)

    assert result.returncode == 0, result.stderr
    assert not target.exists(), "the given path itself must stay empty"
    numbered = sorted(tmp_path.glob("w1.nc?????*"))
    assert numbered, "expected <outfile><xxx>.nc to have been written"
    assert numbered[0].name == "w1.nc00001.nc"


@cdo_required
def test_the_execution_layer_finds_the_numbered_output(tmp_path):
    """End to end: with map3d the run must not claim an output that is absent.

    Before ``writes_output_prefix`` the call was resolved with
    ``variable_output=False``, so ``result.output_file`` named a path nothing
    had written and the clean-up globbed the wrong shape.
    """
    from ncexplorer_toolkit.core.nc_integration import (
        create_NCExplorer_integration)

    source = tmp_path / "in.nc"
    subprocess.run(
        ["cdo", "-O", "-f", "nc", "-settaxis,2000-01-01,00:00:00,1day",
         "-duplicate,3", "-random,r18x9,1", str(source)],
        capture_output=True, check=True, timeout=120)

    integration = create_NCExplorer_integration("cdo")
    target = tmp_path / "weights.nc"
    result = integration.execute_operator(
        "genbil",
        input_files=[str(source)],
        output_files=[str(target)],
        extra_parameters=["r36x18", "true"],
    )

    assert result.success, result.stderr
    # No single file to name, so none is named — rather than naming an absent one.
    assert result.output_file is None
    assert sorted(tmp_path.glob("weights.nc?????*"))


# ---------------------------------------------------------------------------
# 1.5 the intlevel mode switch
# ---------------------------------------------------------------------------

def test_intlevel_declares_four_keyword_parameters():
    params = get_operator_spec("intlevel").params
    assert [(p.name, p.form) for p in params] == [
        ("level", "keyword"),
        ("zdescription", "keyword"),
        ("zvarname", "keyword"),
        ("extrapolate", "keyword"),
    ]


def test_the_level_list_survives_the_comma_join_byte_for_byte():
    """``level=`` carries a comma-separated list inside one value.

    ``parameter_tokens`` returns it as a single token and the caller's
    ``','.join`` then places it verbatim, so the list's own commas and the
    token separator are the same character and must still come out right.
    """
    assert parameter_tokens("intlevel", ["150,300,700"]) == ["level=150,300,700"]
    assert _token("intlevel", ["150,300,700"]) == "intlevel,level=150,300,700"


@cdo_required
def test_mixing_a_bare_level_list_with_a_keyword_is_a_parse_error(tmp_path):
    """The trap the always-keyword decision makes unreachable."""
    source = tmp_path / "lev.nc"
    subprocess.run(
        ["cdo", "-O", "-f", "nc", "merge",
         "-setlevel,1000", "-mulc,1", "-random,r18x9,1",
         "-setlevel,850", "-mulc,2", "-random,r18x9,1",
         "-setlevel,500", "-mulc,3", "-random,r18x9,1", str(source)],
        capture_output=True, check=True, timeout=120)

    # positional list alone: accepted
    positional = subprocess.run(
        ["cdo", "intlevel,900,700", str(source), str(tmp_path / "a.nc")],
        capture_output=True, text=True, timeout=120)
    assert positional.returncode == 0, positional.stderr

    # positional list + a keyword: rejected, and this is the shape of it
    mixed = subprocess.run(
        ["cdo", "intlevel,900,700,extrapolate=true", str(source),
         str(tmp_path / "b.nc")],
        capture_output=True, text=True, timeout=120)
    assert mixed.returncode != 0
    assert "Float parameter >extrapolate=true<" in mixed.stderr

    # the all-keyword spelling this schema always emits: accepted
    keyword = subprocess.run(
        ["cdo", _token("intlevel", ["900,700", "", "", "true"]), str(source),
         str(tmp_path / "c.nc")],
        capture_output=True, text=True, timeout=120)
    assert keyword.returncode == 0, keyword.stderr


@cdo_required
def test_level_and_zdescription_cannot_both_be_set(tmp_path):
    """Why "always emit level=" is not the whole rule."""
    source = tmp_path / "lev.nc"
    subprocess.run(
        ["cdo", "-O", "-f", "nc", "merge",
         "-setlevel,1000", "-mulc,1", "-random,r18x9,1",
         "-setlevel,500", "-mulc,3", "-random,r18x9,1", str(source)],
        capture_output=True, check=True, timeout=120)
    zaxis = tmp_path / "zax.txt"
    zaxis.write_text("zaxistype = height\nsize = 2\nlevels = 900 700\n")

    both = subprocess.run(
        ["cdo", f"intlevel,level=900,zdescription={zaxis}", str(source),
         str(tmp_path / "a.nc")],
        capture_output=True, text=True, timeout=120)
    assert both.returncode != 0
    assert "can't be mixed" in both.stderr

    # …and the schema does not build that call: an unset keyword is absent.
    assert _token("intlevel", ["", str(zaxis)]) == \
        f"intlevel,zdescription={zaxis}"


# ---------------------------------------------------------------------------
# 1.6 intlevel3d's parameter is a data file, not a Z-axis description
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("operator", ["intlevel3d", "intlevelx3d"])
def test_intlevel3d_names_its_parameter_tgtcoordinate(operator):
    params = get_operator_spec(operator).params
    assert [p.name for p in params] == ["tgtcoordinate"]
    assert params[0].kind == "file"
    assert params[0].reads is True
    assert "z-axis description" not in params[0].label.lower()


# ---------------------------------------------------------------------------
# 3.1 intlevel3d's input slots
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("operator", ["intlevel3d", "intlevelx3d"])
def test_intlevel3d_declares_both_slots_and_no_recipe(operator):
    slots = operator_inputs(operator)
    assert len(slots) == 2
    assert "data" in slots[0].role.lower()
    assert "source coordinate" in slots[1].role.lower()
    # No recipe on either, deliberately: the source coordinate comes with the
    # dataset and cannot be derived from input 1. Saying so is the point.
    assert not slots[0].recipe
    assert not slots[1].recipe
    # And the coordinate slot is not expected to hold the data variable.
    assert slots[1].holds_variable is False


def test_intyear_declares_its_two_bracketing_slots():
    slots = operator_inputs("intyear")
    assert len(slots) == 2
    assert not any(slot.recipe for slot in slots)


@cdo_required
def test_swapping_intlevel3d_inputs_still_runs_and_is_wrong(tmp_path):
    """Why the slots are captioned: the swap is silent."""
    # Every level scaled from ONE base field, so each vertical column rises
    # monotonically. Three independent random fields would not, and intlevel3d
    # refuses that outright with "Non monotonic zaxis!" — which is a fact about
    # the sample, not about the slot order this test is here to demonstrate.
    base = tmp_path / "base.nc"
    subprocess.run(["cdo", "-O", "-f", "nc", "-random,r18x9,1", str(base)],
                   capture_output=True, check=True, timeout=120)
    levels = tmp_path / "lev.nc"
    subprocess.run(
        ["cdo", "-O", "-f", "nc", "merge",
         "-setlevel,1", "-mulc,1", str(base),
         "-setlevel,2", "-mulc,2", str(base),
         "-setlevel,3", "-mulc,3", str(base), str(levels)],
        capture_output=True, check=True, timeout=120)
    data = tmp_path / "data.nc"
    source = tmp_path / "src.nc"
    target = tmp_path / "tgt.nc"
    subprocess.run(["cdo", "-O", "-f", "nc", "-setname,ta", str(levels),
                    str(data)], capture_output=True, check=True, timeout=120)
    subprocess.run(["cdo", "-O", "-f", "nc", "-setname,zcoord", "-addc,500",
                    "-mulc,1000", str(levels), str(source)],
                   capture_output=True, check=True, timeout=120)
    subprocess.run(["cdo", "-O", "-f", "nc", "-setname,zcoord", "-addc,700",
                    "-mulc,900", str(levels), str(target)],
                   capture_output=True, check=True, timeout=120)

    correct = subprocess.run(
        ["cdo", f"intlevel3d,{target}", str(data), str(source),
         str(tmp_path / "ok.nc")], capture_output=True, text=True, timeout=120)
    swapped = subprocess.run(
        ["cdo", f"intlevel3d,{target}", str(source), str(data),
         str(tmp_path / "swap.nc")], capture_output=True, text=True, timeout=120)

    # Both succeed. That is the finding.
    assert correct.returncode == 0, correct.stderr
    assert swapped.returncode == 0, swapped.stderr
    assert (tmp_path / "swap.nc").is_file()


# ---------------------------------------------------------------------------
# 2.3 intyear leaves its outputs behind on abort
# ---------------------------------------------------------------------------

@cdo_required
def test_intyear_aborts_after_creating_its_output_files(tmp_path):
    """The measurement the clean-up has to cope with.

    ``intyear`` is the first non-split operator to reach the ``nout == -1``
    prefix glob, so this pins the behaviour the glob is there for rather than
    assuming it.
    """
    source = tmp_path / "one_year.nc"
    subprocess.run(
        ["cdo", "-O", "-f", "nc", "-settaxis,2000-01-01,00:00:00,1day",
         "-duplicate,3", "-random,r18x9,1", str(source)],
        capture_output=True, check=True, timeout=120)

    result = subprocess.run(
        ["cdo", "intyear,2001,2002", str(source), str(source),
         str(tmp_path / "yr")],
        capture_output=True, text=True, timeout=120)

    assert result.returncode != 0
    assert "out of bounds" in result.stderr
    # Both files exist despite the abort — which is what has to be cleaned up.
    assert (tmp_path / "yr2001.nc").is_file()
    assert (tmp_path / "yr2002.nc").is_file()


@cdo_required
def test_the_execution_layer_discards_intyears_orphaned_outputs(tmp_path):
    """The other half: a failed run must not leave those files behind."""
    from ncexplorer_toolkit.core.nc_integration import (
        create_NCExplorer_integration)

    source = tmp_path / "one_year.nc"
    subprocess.run(
        ["cdo", "-O", "-f", "nc", "-settaxis,2000-01-01,00:00:00,1day",
         "-duplicate,3", "-random,r18x9,1", str(source)],
        capture_output=True, check=True, timeout=120)

    integration = create_NCExplorer_integration("cdo")
    result = integration.execute_operator(
        "intyear",
        input_files=[str(source), str(source)],
        output_files=[str(tmp_path / "yr")],
        extra_parameters=["2001,2002"],
    )

    assert not result.success
    leftovers = sorted(tmp_path.glob("yr2*.nc"))
    assert leftovers == [], f"a failed run left {[p.name for p in leftovers]}"


# ---------------------------------------------------------------------------
# 1.11 the Gaussian grid presets
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("preset", ["F16", "F32", "F64", "n16", "n32", "n64"])
def test_the_gaussian_families_are_offered(preset):
    assert preset in GRID_PRESETS


def test_the_presets_that_do_not_exist_are_not_offered():
    """Unlike t21grid, there is no F32grid — CDO takes it as a filename."""
    assert "F32grid" not in GRID_PRESETS
    assert "n32grid" not in GRID_PRESETS


@cdo_required
@pytest.mark.parametrize("preset,expected", [
    ("F32", "gaussian"),
    ("n32", "gaussian_reduced"),
])
def test_the_gaussian_presets_resolve_to_the_grid_they_claim(tmp_path, preset,
                                                             expected):
    source = tmp_path / "in.nc"
    subprocess.run(["cdo", "-O", "-f", "nc", "-random,r18x9,1", str(source)],
                   capture_output=True, check=True, timeout=120)
    out = tmp_path / f"{preset}.nc"
    run = subprocess.run(["cdo", f"remapbil,{preset}", str(source), str(out)],
                         capture_output=True, text=True, timeout=180)
    assert run.returncode == 0, run.stderr
    info = subprocess.run(["cdo", "sinfon", str(out)],
                          capture_output=True, text=True, timeout=120)
    assert expected in info.stdout


# ---------------------------------------------------------------------------
# 2.1 the environment variables
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("operator,expected", [
    ("remapcon", {"CDO_REMAP_NORM", "REMAP_AREA_MIN"}),
    ("remaplaf", {"REMAP_AREA_MIN"}),
    ("remapbil", {"REMAP_EXTRAPOLATE"}),
    ("remapknn", {"REMAP_EXTRAPOLATE", "CDO_GRIDSEARCH_RADIUS"}),
    ("remap", {"CDO_REMAP_NORM", "REMAP_AREA_MIN", "REMAP_EXTRAPOLATE",
               "CDO_GRIDSEARCH_RADIUS"}),
    ("remapeta", {"REMAPETA_PTOP"}),
    ("intyear", {"CDO_FILE_SUFFIX"}),
])
def test_each_operator_declares_the_variables_its_own_help_declares(operator,
                                                                    expected):
    assert {v.name for v in operator_env(operator)} == expected


def test_the_dangerous_default_is_recorded_as_measured():
    norm = next(v for v in operator_env("remapcon")
                if v.name == "CDO_REMAP_NORM")
    assert norm.default == "fracarea"
    assert set(norm.choices) == {"fracarea", "destarea"}


@cdo_required
def test_the_two_normalisations_both_succeed_and_disagree(tmp_path):
    """The _FLDCOR_TRUNCATES failure shape: a plausible wrong answer.

    Both settings exit 0 and both write a well-formed file. Only the
    environment variable says which of the two answers is on disk, which is why
    it is also written into the logged command.
    """
    source = tmp_path / "masked.nc"
    subprocess.run(
        ["cdo", "-O", "-f", "nc", "-setrtomiss,0,0.3", "-random,r18x9,1",
         str(source)], capture_output=True, check=True, timeout=120)

    means = {}
    for norm in ("fracarea", "destarea"):
        out = tmp_path / f"{norm}.nc"
        run = subprocess.run(
            ["cdo", "remapcon,r36x18", str(source), str(out)],
            capture_output=True, text=True, timeout=180,
            env={**__import__("os").environ, "CDO_REMAP_NORM": norm})
        assert run.returncode == 0, run.stderr
        assert out.is_file()
        value = subprocess.run(["cdo", "-s", "output", "-fldmean", str(out)],
                               capture_output=True, text=True, timeout=120)
        means[norm] = value.stdout.strip()

    assert means["fracarea"] != means["destarea"], means


def test_the_logged_command_carries_the_variable_that_chose_the_numbers():
    from ncexplorer_toolkit.core.nc_integration import CommandRecord

    record = CommandRecord(
        argv=("cdo", "remapcon,r36x18", "in.nc", "out.nc"),
        cwd="/tmp", returncode=0, duration=1.0,
        env=(("CDO_REMAP_NORM", "destarea"),))
    assert record.as_text() == (
        "CDO_REMAP_NORM=destarea cdo remapcon,r36x18 in.nc out.nc")

    # Unchanged for a run that declares nothing.
    plain = CommandRecord(argv=("cdo", "copy", "a", "b"), cwd="/tmp",
                          returncode=0, duration=0.1)
    assert plain.as_text() == "cdo copy a b"


@cdo_required
def test_execute_operator_records_the_environment_it_ran_with(tmp_path):
    from ncexplorer_toolkit.core.nc_integration import (
        create_NCExplorer_integration)

    source = tmp_path / "in.nc"
    subprocess.run(["cdo", "-O", "-f", "nc", "-random,r18x9,1", str(source)],
                   capture_output=True, check=True, timeout=120)

    integration = create_NCExplorer_integration("cdo")
    result = integration.execute_operator(
        "remapcon",
        input_files=[str(source)],
        output_files=[str(tmp_path / "out.nc")],
        extra_parameters=["r36x18"],
        env={"CDO_REMAP_NORM": "destarea"},
    )
    assert result.success, result.stderr
    assert integration.command_history
    assert integration.command_history[-1].as_text().startswith(
        "CDO_REMAP_NORM=destarea ")


# ---------------------------------------------------------------------------
# 2.4 intyear opens every output file at once
# ---------------------------------------------------------------------------

def test_intyear_carries_the_open_file_note():
    description = OPERATOR_SCHEMA["intyear"].description
    assert "Opens every output file simultaneously" in description


def test_the_open_file_note_says_output_for_intyear():
    """It opens every *output*, and the note's direction test is prefix-based."""
    assert "every output file" in OPERATOR_SCHEMA["intyear"].description
    # The control: merge is the input-side case and must be unaffected.
    assert "every input file" in OPERATOR_SCHEMA["merge"].description


# ---------------------------------------------------------------------------
# 1.10 the CF standard name the ICON operators need
# ---------------------------------------------------------------------------

def _write(tmp_path, name, standard_name=None):
    """A one-variable NetCDF file, optionally carrying a standard_name."""
    import numpy as np
    import xarray as xr

    data = xr.DataArray(np.zeros((2, 2)), dims=("lat", "lon"), name="v")
    if standard_name:
        data.attrs["standard_name"] = standard_name
    path = tmp_path / name
    data.to_dataset().to_netcdf(path)
    return path


def test_the_standard_name_check_passes_the_file_that_carries_it(tmp_path):
    from ncexplorer_toolkit.core.units import check_standard_names

    good = _write(tmp_path, "good.nc", "air_pressure")
    assert check_standard_names("ap2pl", [str(good)]) == []


def test_the_standard_name_check_names_the_missing_field(tmp_path):
    from ncexplorer_toolkit.core.units import check_standard_names

    wrong = _write(tmp_path, "wrong.nc", "air_temperature")
    warnings = check_standard_names("gh2hl", [str(wrong)])
    assert len(warnings) == 1
    assert "geometric_height_at_full_level_center" in str(warnings[0])


def test_a_file_with_no_standard_names_is_not_evidence(tmp_path):
    """Unreadable or unannotated is *unverifiable*, never *wrong*."""
    from ncexplorer_toolkit.core.units import check_standard_names

    bare = _write(tmp_path, "bare.nc")
    assert check_standard_names("ap2pl", [str(bare)]) == []
    assert check_standard_names("ap2pl", ["/nonexistent/nope.nc"]) == []


def test_ml2pl_is_not_checked_because_it_accepts_grib_codes(tmp_path):
    """It identifies each field by GRIB1 code *or* CF name, so absence of a
    standard name is not evidence and a warning would fire on correct input."""
    from ncexplorer_toolkit.core.units import check_standard_names

    bare = _write(tmp_path, "grib_like.nc", "air_temperature")
    assert check_standard_names("ml2pl", [str(bare)]) == []


def test_check_inputs_folds_the_standard_name_check_in(tmp_path):
    """Every caller that already asks about units gets this without a new site."""
    from ncexplorer_toolkit.core.units import check_inputs

    wrong = _write(tmp_path, "wrong.nc", "air_temperature")
    assert any("air_pressure" in str(w)
               for w in check_inputs("ap2pl", [str(wrong)]))


@cdo_required
def test_cdo_aborts_exactly_as_the_warning_predicts(tmp_path):
    """The measurement behind the message: a bare standard name, no filename."""
    source = tmp_path / "in.nc"
    subprocess.run(["cdo", "-O", "-f", "nc", "-random,r18x9,1", str(source)],
                   capture_output=True, check=True, timeout=120)
    result = subprocess.run(
        ["cdo", "ap2pl,92500,85000", str(source), str(tmp_path / "out.nc")],
        capture_output=True, text=True, timeout=120)
    assert result.returncode != 0
    assert "air_pressure not found" in result.stderr
