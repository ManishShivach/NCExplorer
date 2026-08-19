# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""The Statistic section, against what CDO 2.6.3 actually accepts.

Every assertion here corresponds to a command that was **run** against the
installed binary, and the command is quoted next to it. That is the rule the
rest of this repo follows and it is worth restating for this section, because
the Statistic modules are where guessing is cheapest and most wrong: three
parameter grammars, two of them mixed inside a single operator token, and a
family of operators that accept a misspelled key and silently ignore it.

The tests are grouped by the shape of the claim rather than by module:

* **grammar** — what the built token looks like, which is what
  ``parameter_tokens`` produces and the execution layer runs verbatim;
* **refusals** — parameters CDO does *not* take, asserted as absent from the
  schema so a future edit that adds them fails here rather than at a user;
* **inputs** — the three-file percentile operators, whose recipes were run;
* **categorisation** — the 47 operators that moved;
* **help text** — the distinctions the section turns on.

Where a test asserts a *negative* ("Zonstat does not take a keyword pn"), the
positive form was measured too and the error message is quoted in the comment.
An assertion that some spelling is rejected is only worth having when the
rejection was observed.
"""

import shutil
import subprocess

import pytest

from ncexplorer_toolkit.core.categories import (
    OPERATOR_SCHEMA, NCExplorerCategory, format_recipe, operator_env,
    operator_inputs, operator_options, operator_syntax, parameter_tokens,
)

cdo_required = pytest.mark.skipif(
    shutil.which("cdo") is None, reason="needs an installed CDO")


def token(operator, values):
    """The full operator token, as the execution layer would build it."""
    parts = parameter_tokens(operator, values)
    return ",".join([operator] + parts) if parts else operator


def run_cdo(args, timeout=60):
    """``(returncode, stderr)`` for one cdo call.

    ``subprocess`` rather than a shell pipeline, deliberately: a CDO abort
    writes to stderr and exits non-zero, but piping the output to another
    command makes ``$?`` the *pipeline's* status and hides it. That mistake
    turned a real abort into an apparent success once while this section was
    being measured — ``cdo remapmean,grid=r6x3 | head`` reads as exit 0 — so
    every claim below reads the returncode directly.
    """
    process = subprocess.run(["cdo", "-s"] + list(args),
                             capture_output=True, text=True, timeout=timeout)
    return process.returncode, " ".join((process.stderr or "").split())


@pytest.fixture(scope="module")
def series(tmp_path_factory):
    """A daily field with real spatial variation, on an 18x9 lonlat grid.

    ``-enlarge`` alone would broadcast one value across every cell, which makes
    min, max and every percentile identical and quietly turns the assertions
    below into tautologies — that happened while measuring this section. The
    ``topo`` field is what gives each cell a different value.
    """
    if shutil.which("cdo") is None:
        pytest.skip("needs an installed CDO")
    path = tmp_path_factory.mktemp("statistic") / "series.nc"
    subprocess.run(
        ["cdo", "-s", "-f", "nc", "-settaxis,2000-01-01,12:00,1day",
         "-add", "-enlarge,r18x9", "-for,1,60", "-topo,r18x9", str(path)],
        capture_output=True, check=True, timeout=120)
    return path


# ---------------------------------------------------------------------------
# W1 — the keyword parameters that were missing entirely
# ---------------------------------------------------------------------------

def test_fldstat_takes_area_weighting_as_a_keyword():
    """cdo fldmean,weights=FALSE v.nc o.nc -> exit 0.

    And the positional spelling is not a near miss but a parse error:
    ``cdo fldmean,90`` is "missing '=' in key/value string: >90<".
    """
    assert token("fldmean", ["false"]) == "fldmean,weights=false"
    assert token("fldmean", ["true"]) == "fldmean,weights=true"


def test_fldstat_verbose_is_a_keyword_and_not_a_flag():
    """cdo fldmean,verbose -> "missing '=' in key/value string: >verbose<".

    The distinction matters because this schema *does* have bare-name flags —
    ``splitname,swap`` — so the two spellings coexist and neither is guessable
    from the other.
    """
    assert token("fldmin", ["", "true"]) == "fldmin,verbose=true"
    assert "verbose=true" in operator_syntax("fldmin")
    assert not operator_syntax("fldmin").endswith(",verbose")


def test_fldstat_takes_both_keys_together_in_either_order():
    """cdo fldmean,weights=FALSE,verbose=TRUE and the reverse both exit 0."""
    assert token("fldmean", ["false", "true"]) == \
        "fldmean,weights=false,verbose=true"


def test_an_unset_keyword_leaves_no_empty_slot():
    """The property that makes these safe to declare as optional.

    A keyword left blank is absent from the token, so the ones after it do not
    shift — unlike a positional blank, which ``parameter_tokens`` preserves on
    purpose.
    """
    assert token("fldmean", ["", ""]) == "fldmean"
    assert token("fldmean", ["false", ""]) == "fldmean,weights=false"
    assert token("fldmean", ["", "true"]) == "fldmean,verbose=true"


def test_globavg_inherits_fldavg_parameters_rather_than_declaring_them():
    """It is an alias — the catalog describes it as "--> fldavg".

    Measured: ``cdo globavg,banana=42`` aborts as "cdo fldavg (Abort): Invalid
    parameter key >banana<!", naming the target. Sharing the object rather than
    copying it is what makes the two structurally unable to disagree.
    """
    assert OPERATOR_SCHEMA["globavg"].params is OPERATOR_SCHEMA["fldavg"].params


def test_vertstat_takes_weights_but_not_verbose():
    """cdo vertmean,weights=FALSE v3.nc o.nc -> exit 0.

    cdo vertmean,verbose=TRUE v3.nc o.nc -> "Invalid parameter key >verbose<!",
    which is why the Fldstat pair is not reused here.
    """
    assert token("vertmean", ["false"]) == "vertmean,weights=false"
    names = [p.name for p in OPERATOR_SCHEMA["vertmean"].params]
    assert names == ["weights"]


@pytest.mark.parametrize("operator", ["vertmax", "vertmin", "vertrange",
                                      "vertsum"])
def test_the_four_vertstat_operators_that_ignore_keys_declare_none(operator):
    """These accept ``banana=42`` and exit 0 — they validate nothing.

    Measured side by side on 2.6.3:

        cdo vertsum,banana=42 v3.nc o.nc   -> exit 0
        cdo vertmean,banana=42 v3.nc o.nc  -> Invalid parameter key >banana<!

    So ``weights`` on these four is a control CDO never reads. Declaring it
    would put a checkbox in the form that silently does nothing, which is worse
    than the missing checkbox: the user gets a plausible answer and a reason to
    believe they asked for it.
    """
    assert OPERATOR_SCHEMA[operator].params == ()


@pytest.mark.parametrize("operator", ["timmean", "daymean", "monmean",
                                      "yearmean", "hourmean"])
def test_the_five_modules_that_take_complete_only(operator):
    """cdo <op>,complete_only=TRUE -> exit 0 for all five modules.

    The brief this came from named mon/day/year; tim and hour take it too.
    """
    assert token(operator, ["true"]) == f"{operator},complete_only=true"


@pytest.mark.parametrize("operator", ["seasmean", "ymonmean", "ydaymean",
                                      "yearmonmean", "timselmean"])
def test_the_modules_that_refuse_complete_only(operator):
    """Measured refusals, so a later edit cannot add the key by symmetry.

        cdo seasmean,complete_only=TRUE     -> Too many arguments! Need 0 found 1
        cdo ymonmean,complete_only=TRUE     -> Too many arguments! Need 0 found 1
        cdo yearmonmean,complete_only=TRUE  -> Too many arguments! Need 0 found 1
        cdo ydaymean,complete_only=TRUE     -> Invalid parameter key >complete_only<!
        cdo timselmean,complete_only=TRUE   -> Integer parameter >complete_only=TRUE<
                                               contains invalid character
    """
    names = [p.name for p in OPERATOR_SCHEMA[operator].params]
    assert "complete_only" not in names


def test_zonmean_alone_takes_a_zonal_descriptor_and_takes_it_positionally():
    """cdo zonmean,zonal_10 v.nc o.nc -> exit 0, ysize 9 becomes 18.

    Keyword is not an alternative spelling: ``cdo zonmean,zonaldes=zonal_10``
    is "Open failed on zonaldes=zonal_10!" — CDO read the whole string as a
    filename.
    """
    assert token("zonmean", ["zonal_10"]) == "zonmean,zonal_10"
    assert "zonaldes=" not in token("zonmean", ["zonal_10"])


@pytest.mark.parametrize("operator", ["zonavg", "zonmax", "zonmin", "zonsum",
                                      "zonstd", "zonmedian", "zonrange",
                                      "zonvar", "zonskew", "zonkurt"])
def test_no_other_zonstat_operator_takes_a_parameter(operator):
    """cdo zonavg,zonal_10 v.nc o.nc -> "Too many arguments! Need 0 found 1".

    The brief said "zonmean (and the rest of Zonstat where CDO takes it)".
    Measured, that parenthetical is empty: a loop over the module would have
    declared a parameter that fails on thirteen of fourteen operators.
    """
    assert OPERATOR_SCHEMA[operator].params == ()


# ---------------------------------------------------------------------------
# W1/W2 — the mixed grammar: positional followed by keyword
# ---------------------------------------------------------------------------

def test_ydrunstat_mixes_a_positional_window_with_a_keyword_mode():
    """cdo ydrunmean,5,rm=c vy.nc o.nc -> exit 0.

    cdo ydrunmean,5,c -> "missing '=' in key/value string: >c<".

    This is the invariant ``parameter_tokens`` claims and this test pins: a
    form decides *rendering*, never index. The positional value is emitted
    verbatim at position 0 and the keyword is spelled at position 1.
    """
    assert token("ydrunmean", ["5", "c"]) == "ydrunmean,5,rm=c"
    assert token("ydrunmean", ["5", ""]) == "ydrunmean,5"


def test_ydrunpctl_mixes_two_positionals_with_two_keywords():
    """cdo ydrunpctl,90,5,rm=c,pm=r8 in min max out -> exit 0."""
    assert token("ydrunpctl", ["90", "5", "c", "r8"]) == \
        "ydrunpctl,90,5,rm=c,pm=r8"
    assert token("ydrunpctl", ["90", "5", "", "r8"]) == "ydrunpctl,90,5,pm=r8"
    assert token("ydrunpctl", ["90", "5", "", ""]) == "ydrunpctl,90,5"


def test_rm_accepts_only_c():
    """cdo ydrunmean,5,rm=n -> "Parameter rm must only contain 'c'!".

    So the choices tuple is the binary's own answer rather than a
    transcription of the manual.
    """
    rm = next(p for p in OPERATOR_SCHEMA["ydrunmean"].params if p.name == "rm")
    assert set(rm.choices) == {"", "c"}


def test_pm_accepts_r8_and_nrank_and_nothing_else():
    """Measured: r7, r1, 8 and hf8 are each "Percentile method X not available!".

    ``nrank`` is the default — it agrees with an unset pm to the last digit
    where r8 does not (ydrunpctl,90,11 on a 400-step daily series: unset
    3150.333252, nrank 3150.333252, r8 3150.866699).
    """
    pm = next(p for p in OPERATOR_SCHEMA["ydrunpctl"].params if p.name == "pm")
    assert set(pm.choices) == {"", "nrank", "r8"}
    # The placeholder is what a surface shows as "what you get if you leave
    # this alone", so it has to be the measured default rather than the first
    # choice alphabetically.
    assert pm.placeholder == "nrank"


@pytest.mark.parametrize("operator", ["ydrunmin", "ydrunmax", "ydrunsum",
                                      "ydrunmean", "ydrunavg", "ydrunvar",
                                      "ydrunstd", "ydrunvar1", "ydrunstd1"])
def test_every_ydrunstat_operator_declares_the_window_then_the_mode(operator):
    """All nine validate their keys — ``banana=42`` aborts on every one — so
    ``rm`` is a control CDO reads rather than one it tolerates."""
    names = [p.name for p in OPERATOR_SCHEMA[operator].params]
    assert names == ["nts", "rm"]


# ---------------------------------------------------------------------------
# W2 — the percentile grammar is per module
# ---------------------------------------------------------------------------

def test_fldstat_percentile_is_spelled_with_the_keyword_form():
    """cdo fldpctl,90 and cdo fldpctl,pn=90 both run and agree.

    Measured identical with ``cdo diffn``. Keyword is chosen because Fldstat's
    single argument slot will also swallow ``weights=FALSE``, and doing so
    defaults pn to 0 and returns the field **minimum** on exit 0:

        cdo fldpctl,weights=FALSE v.nc o.nc  -> -5864.667  (== cdo fldmin)
        cdo fldpctl,pn=0          v.nc o.nc  -> -5864.667

    Printing ``pn=`` in the usage line is what steers a user away from that.
    """
    assert token("fldpctl", ["90"]) == "fldpctl,pn=90"
    assert operator_syntax("fldpctl") == "ifile ofile pn=<float>"


def test_fldpctl_declares_only_the_percentile():
    """It takes exactly one argument: "Too many arguments! Need 1 found 2" for
    two of anything, including ``pn=90,verbose=TRUE``. So weights and verbose
    must not be declared on it even though each is accepted alone."""
    assert [p.name for p in OPERATOR_SCHEMA["fldpctl"].params] == ["pn"]


@pytest.mark.parametrize("operator", ["zonpctl", "merpctl", "varspctl",
                                      "enspctl", "monpctl", "timpctl",
                                      "yearpctl", "seaspctl", "daypctl",
                                      "hourpctl", "ydaypctl", "ymonpctl",
                                      "yseaspctl", "runpctl", "timselpctl",
                                      "ydrunpctl"])
def test_every_other_percentile_operator_is_positional(operator):
    """cdo zonpctl,pn=50 -> "Float parameter >pn=50< contains invalid character
    at position 1!", and the same for merpctl, varspctl, enspctl and every
    temporal pctl operator. Normalising the family to the keyword form would
    abort on all sixteen of these."""
    assert token(operator, ["90"]).startswith(f"{operator},90")


@pytest.mark.parametrize("operator", ["fldpctl", "zonpctl", "merpctl",
                                      "varspctl", "enspctl", "timpctl",
                                      "monpctl", "ydrunpctl", "runpctl"])
def test_the_percentile_parameter_is_named_pn_everywhere(operator):
    """It was ``p``. CDO calls it ``pn``, one module accepts the name as part
    of the value, and ``operator_syntax`` prints the name — so the old spelling
    told a Fldstat user to type something Fldstat does not take."""
    names = [p.name for p in OPERATOR_SCHEMA[operator].params]
    assert "pn" in names and "p" not in names


# ---------------------------------------------------------------------------
# W3 — the eleven three-input percentile operators
# ---------------------------------------------------------------------------

ELEVEN = ("timpctl", "daypctl", "monpctl", "yearpctl", "seaspctl", "hourpctl",
          "ydaypctl", "ymonpctl", "yseaspctl", "timselpctl", "ydrunpctl")


@pytest.mark.parametrize("operator", ELEVEN)
def test_all_eleven_declare_data_min_and_max(operator):
    """Every one reports nin=3 and declared nothing, so the GUI asked for
    "3 files" and said nothing about what they were. Three copies of the raw
    series produces a finished file of wrong numbers, not an error."""
    slots = operator_inputs(operator)
    assert len(slots) == 3
    assert not slots[0].recipe          # the data; nothing to derive it from
    assert slots[1].recipe and slots[2].recipe
    assert "min" in slots[1].recipe and "max" in slots[2].recipe


@pytest.mark.parametrize("operator,expected_min,expected_max", [
    ("timpctl", "cdo timmin data.nc", "cdo timmax data.nc"),
    ("daypctl", "cdo daymin data.nc", "cdo daymax data.nc"),
    ("monpctl", "cdo monmin data.nc", "cdo monmax data.nc"),
    ("yearpctl", "cdo yearmin data.nc", "cdo yearmax data.nc"),
    ("seaspctl", "cdo seasmin data.nc", "cdo seasmax data.nc"),
    ("hourpctl", "cdo hourmin data.nc", "cdo hourmax data.nc"),
    ("ydaypctl", "cdo ydaymin data.nc", "cdo ydaymax data.nc"),
    ("ymonpctl", "cdo ymonmin data.nc", "cdo ymonmax data.nc"),
    ("yseaspctl", "cdo yseasmin data.nc", "cdo yseasmax data.nc"),
    # The two that must carry the window. Both PDFs print the shorthand
    # *without* it, and neither shorthand is runnable — measured on 2.6.3 both
    # HANG rather than failing, killed after twenty seconds:
    #   cdo ydrunpctl,90,5  in -ydrunmin  in -ydrunmax  in out   -> hang
    #   cdo timselpctl,90,5 in -timselmin in -timselmax in out   -> hang
    ("timselpctl", "cdo timselmin,7 data.nc", "cdo timselmax,7 data.nc"),
    ("ydrunpctl", "cdo ydrunmin,7 data.nc", "cdo ydrunmax,7 data.nc"),
])
def test_each_recipe_renders_the_command_that_was_run(operator, expected_min,
                                                      expected_max):
    """Each of these was executed against a 400-step daily series before being
    written into the schema, and all eleven exit 0."""
    slots = operator_inputs(operator)
    assert format_recipe(slots[1].recipe, in1="data.nc", n="7") == expected_min
    assert format_recipe(slots[2].recipe, in1="data.nc", n="7") == expected_max


@pytest.mark.parametrize("operator", ["timselpctl", "ydrunpctl"])
def test_the_windowed_recipes_use_the_substitutable_placeholder(operator):
    """``{n}`` rather than ``{nsets}``/``{nts}``.

    ``format_recipe`` substitutes exactly two names, so a recipe spelled after
    the operator's own parameter would raise KeyError the moment a surface
    rendered it. This asserts the rendering rather than the spelling, which is
    the part that matters.
    """
    for slot in operator_inputs(operator)[1:]:
        rendered = format_recipe(slot.recipe, in1="x.nc", n="11")
        assert ",11 " in rendered
        assert "{" not in rendered


@pytest.mark.parametrize("operator", ELEVEN)
def test_the_histogram_bin_count_is_declared_on_exactly_these(operator):
    """CDO_PCTL_NBINS sizes the histogram these eleven bin their samples into,
    which is *why* they want a minimum and a maximum. Default 101, measured by
    an unset variable giving bit-identical output to CDO_PCTL_NBINS=101 where
    11 and 1001 do not (timpctl,90 over 400 daily steps: 3139.828369 /
    3140.037842 / 3139.333252)."""
    names = [e.name for e in operator_env(operator)]
    assert names == ["CDO_PCTL_NBINS"]
    assert operator_env(operator)[0].default == "101"


@pytest.mark.parametrize("operator", ["fldpctl", "zonpctl", "merpctl",
                                      "varspctl", "runpctl"])
def test_the_one_input_percentile_operators_have_no_bin_count(operator):
    """They have the whole sample in hand and sort it, which is why they need
    no minimum or maximum. Measured inert for the variable on all five."""
    assert operator_env(operator) == ()


# ---------------------------------------------------------------------------
# W5 — the ensemble modules' file semantics
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("operator", ["enscrps", "ensbrs"])
def test_ensval_declares_its_first_file_as_the_reference(operator):
    """The first file is a climatology/observation, not a member. Feeding six
    members scores the ensemble against one of its own and exits 0."""
    slot = operator_inputs(operator)[0]
    assert "Reference" in slot.role
    assert slot.key == "ens_reference"


@pytest.mark.parametrize("operator", ["ensrkhisttime", "ensrkhistspace",
                                      "ensroc"])
def test_ensstat2_declares_its_first_file_as_the_observations(operator):
    """CDO's own synopsis for this module names the first file ``obsfile``."""
    slot = operator_inputs(operator)[0]
    assert "Observations" in slot.role
    assert slot.key == "ens_obsfile"


@pytest.mark.parametrize("operator,expected", [
    # Measured on 2.6.3. The module page names five of these seven wrongly —
    # it says reli, crpspot, brsreli, brsreso, brsunct.
    ("enscrps", ("crps.nc", "crps_pot.nc", "crps_reli.nc")),
    ("ensbrs", ("brs.nc", "brs_reli.nc", "brs_reso.nc", "brs_unct.nc")),
])
def test_the_ensval_output_names_are_the_measured_ones(operator, expected):
    description = OPERATOR_SCHEMA[operator].description
    for suffix in expected:
        assert f"<base>.{suffix}" in description
    assert "base name, not a file" in description


def test_ensstat_says_it_holds_every_input_open():
    """The module page states it, and a 200-member ensemble otherwise fails on
    a message about file descriptors that names no operator."""
    assert "open-file limit" in OPERATOR_SCHEMA["ensmean"].description


@pytest.mark.parametrize("operator", ["ensmean", "enscrps", "ensrkhisttime"])
def test_the_ensemble_modules_name_their_overwrite_option(operator):
    """-O / --overwrite is Ensstat's documented global option."""
    assert "-O" in [o.name for o in operator_options(operator)]


# ---------------------------------------------------------------------------
# W6 — global options
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("operator", ["timmean", "monmean", "yearmean",
                                      "seasmean", "ymonmean", "ydrunpctl",
                                      "yearmonmean", "timselmean"])
def test_the_temporal_statistics_name_the_timestamp_option(operator):
    """--timestat_date decides the timestamp for roughly 200 operators and its
    default is ``middle``, not the ``last`` most people assume. Measured on
    monmean over a 400-step daily series from 2000-01-01:

        (unset)  2000-01-16   first  2000-01-01
        middle   2000-01-16   last   2000-01-31
    """
    option = next(o for o in operator_options(operator)
                  if o.name == "--timestat_date")
    assert option.default == "middle"
    assert option.choices == ("first", "middle", "last")


def test_async_read_is_a_bare_flag():
    """cdo --async_read true monmean in out -> "Operator >true< not found!".

    The true/false spelling belongs to the environment variable
    CDO_ASYNC_READ, per ``cdo --help``; the option itself takes no argument.
    """
    option = next(o for o in operator_options("timmean") if o.name == "-p")
    assert option.argument == ""
    assert "CDO_ASYNC_READ" in option.help


def test_operators_outside_the_temporal_modules_get_the_generic_hint():
    """The option list is deliberately narrow: -f/-b/-z/-r apply to everything
    and the surfaces already offer a free field for them."""
    from ncexplorer_toolkit.core.categories import operator_options_hint

    assert operator_options("fldmean") == ()
    assert operator_options_hint("fldmean") == "e.g. -f nc  (optional)"
    assert operator_options_hint("monmean") == \
        "e.g. --timestat_date first  (optional)"


# ---------------------------------------------------------------------------
# W4 — the 47 operators that moved
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("operator", [
    "varsmean", "varspctl", "varsstd1",          # 14 vars*
    "yhourmean", "yhourvar1",                    # 10 yhour*
    "dhourmean", "dhourvar1",                    # 10 dhour*
    "dminutemean", "dminutevar1",                # 10 dminute*
    "consecsum", "consects",                     # 2
    "globavg",                                   # the alias nothing named
])
def test_the_misfiled_statistic_operators_are_now_in_the_section(operator):
    """46 operators in five modules, plus ``globavg``, were in Miscellaneous —
    not claimed by a wrong branch, but by no branch at all: the cascade's
    Statistical values test is a prefix list none of these names begins with."""
    assert OPERATOR_SCHEMA[operator].category is \
        NCExplorerCategory.STATISTICAL_VALUES


@pytest.mark.parametrize("operator", ["remapmean", "remapmedian", "remapkurt"])
def test_remapstat_stays_in_interpolation_deliberately(operator):
    """CDO documents these thirteen under Statistic and this app files them
    under Interpolation. The decision and its cost are written out at the end
    of ``_MODULE_CATEGORY``; this pins it so the divergence stays deliberate
    rather than becoming an accident of the prefix cascade."""
    assert OPERATOR_SCHEMA[operator].category is \
        NCExplorerCategory.INTERPOLATION


def test_remapstat_takes_its_grid_positionally_and_warns_about_empty_cells():
    """cdo remapmean,grid=r6x3 -> "Open failed on grid=r6x3!" — CDO read the
    whole string as a filename. ``cdo remapmean,r6x3`` gives xsize 6, ysize 3.
    """
    assert token("remapmean", ["r6x3"]) == "remapmean,r6x3"
    help_text = OPERATOR_SCHEMA["remapmean"].params[0].help
    assert "setmisstonn" in help_text


# ---------------------------------------------------------------------------
# W8 — the distinctions the section turns on
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("operator", ["fldmean", "fldavg", "timmean", "timavg",
                                      "zonmean", "ymonmean", "vertmean"])
def test_every_mean_and_avg_carries_the_missing_value_rule(operator):
    """The overview page opens with it: the mean of 1, 2, miss, 3 is 2 and the
    average is missing. "Field mean" and "Field average" distinguish nothing.
    """
    assert "mean vs avg" in OPERATOR_SCHEMA[operator].description


@pytest.mark.parametrize("operator,marker", [
    ("timvar", "var vs var1"), ("timvar1", "var vs var1"),
    ("fldstd", "std vs std1"), ("fldstd1", "std vs std1"),
])
def test_the_normalisation_pairs_say_which_divisor_they_use(operator, marker):
    assert marker in OPERATOR_SCHEMA[operator].description


def test_the_weighted_and_unweighted_field_statistics_are_distinguished():
    """fldmean/fldavg/fldvar/fldstd are area-weighted; fldskew/fldkurt are not.
    Measured: fldmean gives -2380.56 weighted and -1877.01 unweighted on an
    18x9 field."""
    assert "Area-weighted" in OPERATOR_SCHEMA["fldmean"].description
    assert "Not area-weighted" in OPERATOR_SCHEMA["fldskew"].description


def test_vertstat_weights_by_thickness_and_says_it_needs_bounds():
    """Without layer bounds CDO warns "Layer bounds not available" and the
    setting is inert. With them, vertmean gives 760.8173 by default against
    840.2465 at weights=FALSE."""
    description = OPERATOR_SCHEMA["vertmean"].description
    assert "layer thickness" in description
    assert "genlevelbounds" in description


@pytest.mark.parametrize("operator,marker", [
    ("fldint", "INTEGRAL"),
    ("fldcount", "COUNT"),
    ("timminidx", "INDEX"),
    ("timmaxidx", "INDEX"),
    ("yearminidx", "INDEX"),
    ("yearmaxidx", "INDEX"),
])
def test_the_operators_whose_output_is_not_the_input_quantity(operator, marker):
    """Anything that labels an axis or a colorbar from the input's units is
    wrong for these."""
    assert marker in OPERATOR_SCHEMA[operator].description


def test_yearmean_and_yearmonmean_point_at_each_other():
    """Yearstat's own note. ``yearmean`` is an arithmetic mean over whatever
    timesteps fall in the year; over monthly input that weights a 28-day
    February like a 31-day January. ``yearmonmean`` is the day-weighted one."""
    assert "yearmonmean" in OPERATOR_SCHEMA["yearmean"].description
    assert "Day-weighted" in OPERATOR_SCHEMA["yearmonmean"].description
    assert "yearmean" in OPERATOR_SCHEMA["yearmonmean"].description


def test_timcumsum_says_missing_is_zero():
    assert "ZERO" in OPERATOR_SCHEMA["timcumsum"].description


# ---------------------------------------------------------------------------
# W7 — the preconditions, as warnings
# ---------------------------------------------------------------------------

def test_the_precondition_checks_are_quiet_on_a_file_that_satisfies_them(
        tmp_path):
    """The property that keeps them worth reading.

    A check that fires on correct input is how a user learns to ignore it —
    the argument ``OperatorInput.holds_variable`` makes at length. These are
    exercised against real files in the sweep; here the assertion is only that
    an unreadable path produces nothing, which is the same policy the units
    check follows: unreadable is never reported as wrong.
    """
    from ncexplorer_toolkit.core import units

    absent = str(tmp_path / "nothing.nc")
    assert units.check_structure("zonmean", [absent]) == []
    assert units.check_structure("consects", [absent]) == []
    assert units.check_vertical_weights("vertmean", [absent]) == []


def test_an_operator_with_no_precondition_is_not_checked():
    from ncexplorer_toolkit.core import units

    assert units.check_structure("timmean", ["whatever.nc"]) == []


# ---------------------------------------------------------------------------
# The same claims, re-run against the installed binary
#
# Everything above asserts what this schema *says*. These assert that what it
# says is still true of the CDO on this machine — which is the only thing that
# makes the comments above evidence rather than folklore. They skip cleanly
# when there is no cdo to ask.
# ---------------------------------------------------------------------------

@cdo_required
@pytest.mark.parametrize("accepted,operator,values", [
    # W1: the keyword parameters that were missing entirely.
    (True, "fldmean", ["false"]),
    (True, "fldmean", ["false", "true"]),
    (True, "fldmin", ["", "true"]),
    (True, "monmean", ["true"]),
    (True, "timmean", ["true"]),
    (True, "hourmean", ["true"]),
    (True, "zonmean", ["zonal_10"]),
    # W2: Fldstat takes the keyword percentile.
    (True, "fldpctl", ["90"]),
    # and every other module takes it positionally.
    (True, "zonpctl", ["90"]),
    (True, "merpctl", ["90"]),
    (True, "varspctl", ["90"]),
])
def test_the_built_token_is_one_cdo_accepts(accepted, operator, values,
                                            series, tmp_path):
    """Each row runs the exact token ``parameter_tokens`` produces.

    This is the test that would have caught every form mistake in this section:
    a wrong ``form`` is not a wrong-looking string, it is a command CDO
    refuses, and only the binary can say which.
    """
    out = tmp_path / f"{operator}_out.nc"
    code, stderr = run_cdo([token(operator, values), str(series), str(out)])
    assert (code == 0) is accepted, f"{token(operator, values)}: {stderr}"


@cdo_required
@pytest.mark.parametrize("spelling,expected_error", [
    # The positional spellings Fldstat and the temporal modules refuse.
    ("fldmean,90", "missing '=' in key/value string"),
    ("fldmean,verbose", "missing '=' in key/value string"),
    ("monmean,TRUE", "missing '=' in key/value string"),
    # The keyword spellings Zonstat and friends refuse.
    ("zonpctl,pn=50", "invalid character at position 1"),
    ("merpctl,pn=50", "invalid character at position 1"),
    ("varspctl,pn=50", "invalid character at position 1"),
    # The temporal pctl operators refuse it too, but they take three files, so
    # a one-input call fails on "Missing inputs" before the parameter is ever
    # parsed — they are asserted in
    # test_the_temporal_percentile_operators_refuse_a_keyword_pn below.
    # zonaldes is positional; the keyword form is read as a filename.
    ("zonmean,zonaldes=zonal_10", "Open failed on zonaldes=zonal_10"),
    # remapstat's grid, the same shape of mistake.
    ("remapmean,grid=r6x3", "Open failed on grid=r6x3"),
    # Zonstat takes a parameter on zonmean and nowhere else.
    ("zonavg,zonal_10", "Too many arguments"),
    # The modules that refuse complete_only.
    ("seasmean,complete_only=TRUE", "Too many arguments"),
    ("ymonmean,complete_only=TRUE", "Too many arguments"),
    ("ydaymean,complete_only=TRUE", "Invalid parameter key"),
    # Fldstat validates its keys; four Vertstat operators do not, which is
    # asserted separately below.
    ("fldmean,banana=42", "Invalid parameter key"),
    # fldpctl has exactly one argument slot.
    ("fldpctl,pn=90,weights=FALSE", "Too many arguments"),
])
def test_the_spellings_cdo_refuses(spelling, expected_error, series, tmp_path):
    """Each of these is a form this schema deliberately does not produce.

    Asserted as *refusals* rather than left as comments, because a negative is
    exactly what a later well-meaning edit will undo — "surely pn can be a
    keyword everywhere" is a one-line change that breaks sixteen operators.
    """
    out = tmp_path / "refused.nc"
    code, stderr = run_cdo([spelling, str(series), str(out)])
    assert code != 0, f"{spelling} was accepted, but should not be"
    assert expected_error in stderr, f"{spelling}: {stderr}"


@cdo_required
@pytest.mark.parametrize("operator,stat", [
    ("timpctl", "tim"), ("monpctl", "mon"), ("yearpctl", "year"),
    ("daypctl", "day"), ("ymonpctl", "ymon"),
])
def test_the_temporal_percentile_operators_refuse_a_keyword_pn(
        operator, stat, series, tmp_path):
    """Positional only, asserted with the three files these operators need.

    Given one input they abort with "Missing inputs" before the parameter is
    parsed, which proves nothing about the grammar — so the companions are
    built first and the refusal is the *parameter* refusal:

        cdo monpctl,pn=90 in min max out
          -> Float parameter >pn=90< contains invalid character at position 1!
    """
    minimum = tmp_path / f"{operator}_min.nc"
    maximum = tmp_path / f"{operator}_max.nc"
    for target, kind in ((minimum, "min"), (maximum, "max")):
        code, stderr = run_cdo([f"{stat}{kind}", str(series), str(target)])
        assert code == 0, stderr

    out = tmp_path / f"{operator}_kw.nc"
    code, stderr = run_cdo([f"{operator},pn=90", str(series), str(minimum),
                            str(maximum), str(out)])
    assert code != 0
    assert "invalid character at position 1" in stderr, stderr

    # And the positional spelling this schema produces does run.
    ok = tmp_path / f"{operator}_ok.nc"
    code, stderr = run_cdo([token(operator, ["90"]), str(series),
                            str(minimum), str(maximum), str(ok)])
    assert code == 0, stderr


@cdo_required
def test_fldpctl_spends_its_one_slot_on_whatever_you_put_there(series,
                                                               tmp_path):
    """The trap that decides how Fldstat's percentile is declared.

    ``weights=FALSE`` in the single argument slot is accepted, defaults pn to
    0, and returns the field **minimum** — exit 0, well-formed file, wrong
    number. So weights and verbose must not be declared on this operator even
    though each is accepted on its own.
    """
    def first_value(spelling):
        out = tmp_path / f"{abs(hash(spelling))}.nc"
        code, stderr = run_cdo([spelling, str(series), str(out)])
        assert code == 0, f"{spelling}: {stderr}"
        printed = subprocess.run(["cdo", "-s", "outputf,%14.6f,1", str(out)],
                                 capture_output=True, text=True, timeout=60)
        return printed.stdout.split("\n")[0].strip()

    assert first_value("fldpctl,weights=FALSE") == first_value("fldpctl,pn=0")
    assert first_value("fldpctl,weights=FALSE") == first_value("fldmin")
    assert first_value("fldpctl,pn=90") != first_value("fldpctl,pn=0")


@cdo_required
@pytest.mark.parametrize("operator", ["vertmax", "vertmin", "vertrange",
                                      "vertsum"])
def test_the_four_vertstat_operators_really_do_ignore_any_key(operator,
                                                              tmp_path):
    """The measurement behind leaving ``weights`` off these four.

    They accept ``banana=42`` and exit 0. A key CDO never reads is worse than
    a missing control: the checkbox would appear to work.
    """
    levels = tmp_path / "levels.nc"
    subprocess.run(
        ["cdo", "-s", "-f", "nc", "-settaxis,2000-01-01,12:00,1day",
         "-enlarge,r18x9", "-stdatm,0,500,1000,2000,5000", str(levels)],
        capture_output=True, check=True, timeout=120)

    out = tmp_path / f"{operator}_junk.nc"
    code, _ = run_cdo([f"{operator},banana=42", str(levels), str(out)])
    assert code == 0, f"{operator} rejected an unknown key — declare weights"

    # And the six that do validate, for contrast.
    strict = tmp_path / "strict.nc"
    code, stderr = run_cdo(["vertmean,banana=42", str(levels), str(strict)])
    assert code != 0 and "Invalid parameter key" in stderr


@cdo_required
@pytest.mark.parametrize("operator", ELEVEN)
def test_every_declared_recipe_actually_runs(operator, series, tmp_path):
    """The eleven three-input percentile operators, built from their own
    recipes and run end to end.

    This is the test that pins the two PDF errors: with the window dropped from
    the companions — the shape both the Ydrunpctl and Timselpctl pages print —
    CDO hangs rather than failing, so the wrong recipe cannot be caught by
    checking an exit code. Here the recipes come from the schema, and if one of
    them ever loses its window this run stops finishing.
    """
    window = "5"
    percentile = "90"
    values = [percentile]
    if any(p.name in ("nts", "nsets") for p in OPERATOR_SCHEMA[operator].params):
        values.append(window)

    slots = operator_inputs(operator)
    companions = []
    for index, slot in enumerate(slots[1:], start=1):
        built = tmp_path / f"{operator}_{index}.nc"
        recipe = format_recipe(slot.recipe, in1=str(series), n=window)
        code, stderr = run_cdo(recipe.split()[1:] + [str(built)])
        assert code == 0, f"{recipe}: {stderr}"
        companions.append(str(built))

    out = tmp_path / f"{operator}_out.nc"
    code, stderr = run_cdo(
        [token(operator, values), str(series)] + companions + [str(out)])
    assert code == 0, f"{operator}: {stderr}"


@cdo_required
def test_the_percentile_bin_count_default_is_101(series, tmp_path):
    """Measured rather than transcribed: an unset CDO_PCTL_NBINS gives output
    identical to 101, and 11 and 1001 both differ."""
    import os

    def value(nbins):
        out = tmp_path / f"nbins_{nbins or 'unset'}.nc"
        env = dict(os.environ)
        if nbins:
            env["CDO_PCTL_NBINS"] = nbins
        else:
            env.pop("CDO_PCTL_NBINS", None)
        subprocess.run(
            ["cdo", "-s", "timpctl,90", str(series),
             "-timmin", str(series), "-timmax", str(series), str(out)],
            capture_output=True, check=True, env=env, timeout=120)
        printed = subprocess.run(["cdo", "-s", "outputf,%14.6f,1", str(out)],
                                 capture_output=True, text=True, timeout=60)
        return printed.stdout.split("\n")[0].strip()

    assert value(None) == value("101")
    assert value("11") != value("101")


@cdo_required
def test_timestat_date_defaults_to_middle(series, tmp_path):
    """The option's whole reason for being surfaced: a monthly mean of January
    comes out dated the 16th unless the user says otherwise."""
    def stamps(*options):
        out = tmp_path / f"ts_{'_'.join(options) or 'unset'}.nc"
        subprocess.run(["cdo", "-s", *options, "monmean", str(series),
                        str(out)], capture_output=True, check=True,
                       timeout=120)
        printed = subprocess.run(["cdo", "-s", "showtimestamp", str(out)],
                                 capture_output=True, text=True, timeout=60)
        return printed.stdout.split()

    assert stamps() == stamps("--timestat_date", "middle")
    assert stamps("--timestat_date", "first")[0].startswith("2000-01-01")
    assert stamps("--timestat_date", "last")[0].startswith("2000-01-31")

    out = tmp_path / "rejected.nc"
    code, stderr = run_cdo(["--timestat_date", "bogus", "monmean",
                            str(series), str(out)])
    assert code != 0 and "unsupported argument" in stderr


@cdo_required
def test_async_read_takes_no_argument(series, tmp_path):
    """``--async_read true`` is "Operator >true< not found!" — the true/false
    spelling belongs to CDO_ASYNC_READ, not to the option."""
    out = tmp_path / "async.nc"
    code, _ = run_cdo(["--async_read", "monmean", str(series), str(out)])
    assert code == 0

    out2 = tmp_path / "async2.nc"
    code, stderr = run_cdo(["--async_read", "true", "monmean", str(series),
                            str(out2)])
    assert code != 0 and "not found" in stderr


@cdo_required
def test_ensval_writes_the_measured_file_names(series, tmp_path):
    """The manual names five of these seven suffixes wrongly.

    Also the end-to-end check on ``discovered_outputs``: ``nout == -1`` leaves
    ``output_file`` unset by design, so without the fan-out scan a successful
    run reported "completed successfully" and named nothing.
    """
    from ncexplorer_toolkit.core.nc_integration import (
        create_NCExplorer_integration,
    )

    members = []
    for index in range(1, 4):
        member = tmp_path / f"member{index}.nc"
        subprocess.run(["cdo", "-s", f"addc,{index}", str(series),
                        str(member)], capture_output=True, check=True,
                       timeout=120)
        members.append(str(member))

    integration = create_NCExplorer_integration()
    result = integration.execute_operator(
        "enscrps", input_files=[str(series)] + members,
        output_files=[str(tmp_path / "cbase")])

    assert result.success
    assert result.output_file is None      # a base name is not a file
    assert [name.rsplit("/", 1)[-1] for name in result.discovered_outputs] == [
        "cbase.crps.nc", "cbase.crps_pot.nc", "cbase.crps_reli.nc"]
