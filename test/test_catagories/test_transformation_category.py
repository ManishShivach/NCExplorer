"""The Transformation section is spelled the way the installed binary parses it.

Every claim below was measured against CDO 2.6.3 (x86_64-apple-darwin23.6.0,
built without LIBFFTW3), using a T21 spectral file made with ``cdo gp2sp`` over
``-random,t21grid,1`` and a Gaussian u/v pair. The manual disagrees with the
binary here more sharply than anywhere else in the app, and in a way no amount
of reading would catch: its ``Spectral`` page and its ``Wind`` page document the
same-looking ``[,type]`` / ``[,gridtype]`` slot with two grammars that reject
each other's spelling.

**One slot, three spellings, and no two of them at once.** ``sp2gp`` takes a
bare type word, ``type=<word>`` **or** ``trunc=<n>``. Any two is "(Abort): Too
many parameters", and so is a blank in front of one, which is why this is
declared as a single parameter rather than as a ``type`` and a ``trunc``: the
schema preserves blanks in positional slots, so two parameters would have
emitted ``sp2gp,,trunc=42``.

**The neighbouring module rejects the spelling this one documents.**
``uv2dv,linear`` works; ``uv2dv,type=linear`` is "Unsupported type:
type=linear". A bare integer is no better — ``uv2dv,42`` is "Unsupported type:
42" — because these read the value as a *type*, never as a truncation.

**Three operators hang rather than fail.** ``cdo fourier``, ``cdo sp2sp`` and
``cdo spcut`` with the required parameter omitted never exit, with stdin at
/dev/null; confirmed past 20 seconds each. In the app that is a frozen panel,
so the refusal has to happen at the execution layer, which is where the batch
runner and a loaded model reach it too.

**And the defect the section is really about: five operators that succeed at
doing nothing.** Handed a field they cannot use they warn on stderr, exit 0, and
copy the input through unchanged. See ``core/fieldshape.py``.

The tests marked ``cdo_required`` re-measure the claim against the installed
binary rather than trusting this docstring; the rest assert what the schema
builds, which is the half that has to survive a refactor.
"""

import shutil
import subprocess

import pytest

from ncexplorer_toolkit.core.categories import (
    CATEGORY_FOR_OPERATOR, OPERATOR_CATEGORIES, OPERATOR_SCHEMA,
    NCExplorerCategory, invalid_parameter_values, missing_required_parameters,
    operator_inputs, operator_syntax, parameter_tokens,
)
from ncexplorer_toolkit.core.fieldshape import DETECTORS, check_fields

cdo_required = pytest.mark.skipif(
    shutil.which("cdo") is None, reason="needs an installed CDO")


#: The section this suite is about: the seven documented operators plus the
#: five undocumented ones that ``OPERATOR_CATEGORIES`` already listed.
SCOPE = (
    "sp2gp", "gp2sp", "sp2sp", "dv2uv", "uv2dv", "dv2ps", "fourier",
    "sp2gpl", "gp2spl", "dv2uvl", "uv2dvl", "spcut",
)


def _token(operator, values):
    """The operator token exactly as ``_resolve_operator_call`` builds it."""
    tokens = parameter_tokens(operator, values)
    return operator if not tokens else f"{operator},{','.join(tokens)}"


def _run(*arguments):
    """One cdo call with stdin closed, so a prompting operator cannot hang."""
    with open("/dev/null") as devnull:
        return subprocess.run(
            ["cdo", "-O", *arguments], stdin=devnull, capture_output=True,
            text=True, timeout=90)


# ---------------------------------------------------------------------------
# The exact token, per operator
# ---------------------------------------------------------------------------

#: ``(operator, values, expected token)``.
EXPECTED_TOKENS = [
    # -- Spectral: one slot, three accepted spellings ------------------------
    ("sp2gp", ["quadratic"], "sp2gp,quadratic"),
    ("sp2gp", ["type=linear"], "sp2gp,type=linear"),
    ("sp2gp", ["trunc=42"], "sp2gp,trunc=42"),
    ("gp2sp", ["trunc=10"], "gp2sp,trunc=10"),
    ("sp2gpl", ["trunc=42"], "sp2gpl,trunc=42"),
    ("gp2spl", ["linear"], "gp2spl,linear"),
    # Optional and empty: the bare operator, because the execution layer trims
    # a trailing blank. See test_a_blank_optional_reaches_argv_as_the_bare_form.
    ("sp2gp", [], "sp2gp"),

    # -- Wind: one bare positional word, and nothing else --------------------
    ("dv2uv", ["quadratic"], "dv2uv,quadratic"),
    ("uv2dv", ["linear"], "uv2dv,linear"),
    ("dv2uvl", ["cubic"], "dv2uvl,cubic"),
    ("uv2dvl", ["quadratic"], "uv2dvl,quadratic"),
    ("dv2uv", [], "dv2uv"),

    # -- Specconv: positional integers ---------------------------------------
    ("sp2sp", ["10"], "sp2sp,10"),
    ("spcut", ["1,2,3"], "spcut,1,2,3"),

    # -- Fourier: positional epsilon -----------------------------------------
    ("fourier", ["-1"], "fourier,-1"),
    ("fourier", ["1"], "fourier,1"),

    # -- dv2ps declares nothing, and passes anything through unchanged -------
    ("dv2ps", [], "dv2ps"),
]


@pytest.mark.parametrize("operator,values,expected", EXPECTED_TOKENS)
def test_the_token_is_what_cdo_parses(operator, values, expected):
    assert _token(operator, values) == expected


def test_the_spectral_four_have_exactly_one_parameter():
    """Two would emit ``sp2gp,,trunc=42``, which CDO counts as two parameters.

    Measured: ``cdo sp2gp,,trunc=42`` and ``cdo sp2gp,type=linear,trunc=42``
    are both "(Abort): Too many parameters". The schema has no mutual
    exclusion, and ``parameter_tokens`` preserves blanks in positional slots by
    design, so the only spelling that cannot produce a rejected command is one
    parameter carrying all three forms.
    """
    for operator in ("sp2gp", "gp2sp", "sp2gpl", "gp2spl"):
        params = OPERATOR_SCHEMA[operator].params
        assert len(params) == 1, f"{operator} must declare one parameter"
        assert params[0].optional
        assert params[0].form == "positional"


def test_the_spectral_slot_cannot_emit_both_forms_at_once():
    """Whatever a surface hands over, one slot can only render one value."""
    for value in ("linear", "type=linear", "trunc=42"):
        tokens = parameter_tokens("sp2gp", [value])
        assert tokens == [value]
        assert len(tokens) == 1


def test_the_wind_four_take_a_closed_set_of_bare_words():
    """``type=`` and ``gridtype=`` are rejected here; only the bare word works.

    Measured: ``cdo uv2dv,type=linear`` -> "(Abort): Unsupported type:
    type=linear", ``cdo uv2dv,gridtype=linear`` -> the same for gridtype, and
    ``cdo uv2dvl,trunc=10`` -> "Unsupported type: trunc=10".
    """
    for operator in ("dv2uv", "uv2dv", "dv2uvl", "uv2dvl"):
        params = OPERATOR_SCHEMA[operator].params
        assert len(params) == 1
        assert params[0].kind == "select"
        assert params[0].form == "positional"
        assert params[0].choices == ("quadratic", "linear", "cubic")
        # A bare word only: the rendered token never carries an "=".
        assert "=" not in _token(operator, ["linear"])


@pytest.mark.parametrize("rejected", ["type=linear", "gridtype=linear",
                                      "trunc=10", "lonlat", "42"])
def test_the_wind_four_refuse_every_other_spelling(rejected):
    """Refused by the app, in its own words, before argv."""
    assert invalid_parameter_values("uv2dv", [rejected])


# ---------------------------------------------------------------------------
# The hang: a required parameter left blank
# ---------------------------------------------------------------------------

#: The three that never exit when their parameter is omitted. Measured with
#: stdin at /dev/null, each still running after 20 seconds with no output.
HANGS_WITHOUT_ITS_PARAMETER = ("fourier", "sp2sp", "spcut")


@pytest.mark.parametrize("operator", HANGS_WITHOUT_ITS_PARAMETER)
@pytest.mark.parametrize("supplied", [[], [""], ["   "]])
def test_a_missing_required_parameter_is_refused_not_passed_through(
        operator, supplied):
    assert missing_required_parameters(operator, supplied)


@pytest.mark.parametrize("operator", HANGS_WITHOUT_ITS_PARAMETER)
def test_the_execution_layer_refuses_the_bare_form(operator, tmp_path):
    """The gate that covers the batch runner, a loaded model and a project.

    ``missing_required_parameters`` is the rule; this asserts it is enforced at
    the *choke point* rather than only on the operator form, because the two
    other surfaces that reach argv never go near a widget.
    """
    from ncexplorer_toolkit.core.nc_integration import NCExplorerIntegration

    infile = tmp_path / "in.nc"
    infile.write_bytes(b"")
    integration = NCExplorerIntegration()
    with pytest.raises(ValueError):
        integration.prepare_operator_run(
            operator, input_files=[str(infile)],
            output_files=[str(tmp_path / "out.nc")], extra_parameters=[])


def test_the_app_never_builds_a_bare_fourier_command(tmp_path):
    """The regression this section's hang deserves its own test for.

    ``cdo fourier ifile ofile`` does not abort — it waits on stdin forever, and
    the app feeds it /dev/null, so it waits forever there too. Nothing may
    assemble that argv.
    """
    from ncexplorer_toolkit.core.nc_integration import NCExplorerIntegration

    infile = tmp_path / "in.nc"
    infile.write_bytes(b"")
    integration = NCExplorerIntegration()

    for supplied in ([], [""], ["  "], None):
        with pytest.raises(ValueError):
            prepared = integration.prepare_operator_run(
                "fourier", input_files=[str(infile)],
                output_files=[str(tmp_path / "out.nc")],
                extra_parameters=supplied)
            pytest.fail(f"built {list(prepared.argv)} for fourier {supplied!r}")


def test_a_blank_optional_reaches_argv_as_the_bare_form(tmp_path):
    """The other half: an *optional* blank must not become a dangling comma.

    ``cdo sp2gp,`` is "(Abort): sp2gp: ',' is not followed by any operator
    argument", so a form that leaves the one optional slot empty has to produce
    ``cdo sp2gp`` and not ``cdo sp2gp,``.
    """
    from ncexplorer_toolkit.core.nc_integration import NCExplorerIntegration

    infile = tmp_path / "in.nc"
    infile.write_bytes(b"")
    integration = NCExplorerIntegration()
    prepared = integration.prepare_operator_run(
        "sp2gp", input_files=[str(infile)],
        output_files=[str(tmp_path / "out.nc")], extra_parameters=[""])
    assert "sp2gp" in prepared.argv
    assert not any(token.endswith(",") for token in prepared.argv)


# ---------------------------------------------------------------------------
# fourier's epsilon
# ---------------------------------------------------------------------------

def test_fourier_names_its_parameter_epsilon():
    """CDO and the manual both call it epsilon; the schema called it ``sign``."""
    params = OPERATOR_SCHEMA["fourier"].params
    assert [p.name for p in params] == ["epsilon"]
    assert params[0].kind == "int"
    assert not params[0].optional
    assert params[0].choices == ("-1", "1")


@pytest.mark.parametrize("rejected", ["0", "2", "-2", "10"])
def test_fourier_refuses_an_epsilon_cdo_would_accept(rejected):
    """The point of constraining it: CDO does not.

    Measured on a complex field, ``cdo -f nc4 fourier,0`` and ``fourier,2``
    both exit 0 and write a file. The -1/1 contract is documented and enforced
    nowhere but here.
    """
    assert invalid_parameter_values("fourier", [rejected])


@pytest.mark.parametrize("accepted", ["-1", "1", "+1"])
def test_fourier_accepts_the_two_documented_values(accepted):
    """``+1`` too: choices are compared numerically, not as punctuation."""
    assert invalid_parameter_values("fourier", [accepted]) == []


def test_choices_are_enforced_for_every_kind_not_only_select():
    """``invalid_parameter_values`` used to parse int/float/bool and ignore
    ``choices`` entirely, so a closed set was a widget hint and nothing more."""
    assert invalid_parameter_values("fourier", ["0"])          # int + choices
    assert invalid_parameter_values("dv2uv", ["lonlat"])       # select
    # An unparseable value is reported as unparseable, not as out-of-set: the
    # first is what the user actually did wrong.
    complaint = invalid_parameter_values("fourier", ["abc"])[0]
    assert "whole number" in complaint


# ---------------------------------------------------------------------------
# Every operator is reachable, categorised and correctly spelled
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("operator", SCOPE)
def test_every_operator_has_a_schema_entry_and_a_category(operator):
    assert operator in OPERATOR_SCHEMA
    assert OPERATOR_SCHEMA[operator].category is NCExplorerCategory.TRANSFORMATION


@pytest.mark.parametrize("operator", sorted(set(SCOPE) - {"fourier"}))
def test_every_curated_operator_is_filed_under_transformation(operator):
    """``CATEGORY_FOR_OPERATOR`` is the reverse of the *curated* lists only.

    ``fourier`` is excluded here because it is deliberately not curated — it
    reaches the category through ``_infer_category`` instead, which is what
    ``OPERATOR_SCHEMA[...].category`` reports and what the test above asserts
    for all twelve.
    """
    assert CATEGORY_FOR_OPERATOR[operator] is NCExplorerCategory.TRANSFORMATION


#: What ``operator_syntax`` must say, per operator — the file part plus the
#: trailing parameters, without the operator's own name, which is how the
#: function has always spelled it. The parameter names are the ones the manual
#: uses for each module — ``type`` for Spectral, ``gridtype`` for Wind — so a
#: usage line does not send a user to the wrong page.
EXPECTED_SYNTAX = {
    "sp2gp":  "ifile ofile [,type]",
    "sp2gpl": "ifile ofile [,type]",
    "gp2sp":  "ifile ofile [,type]",
    "gp2spl": "ifile ofile [,type]",
    "dv2uv":  "ifile ofile [,gridtype]",
    "dv2uvl": "ifile ofile [,gridtype]",
    "uv2dv":  "ifile ofile [,gridtype]",
    "uv2dvl": "ifile ofile [,gridtype]",
    "sp2sp":  "ifile ofile trunc",
    "spcut":  "ifile ofile wnums",
    "fourier": "ifile ofile epsilon",
    "dv2ps":  "ifile ofile",
}


@pytest.mark.parametrize("operator,expected", sorted(EXPECTED_SYNTAX.items()))
def test_the_usage_line_matches_the_measured_grammar(operator, expected):
    assert operator_syntax(operator) == expected


def test_dv2ps_declares_no_parameters_deliberately():
    """It takes none and does not enforce that: ``cdo dv2ps,a,b,c`` exits 0."""
    assert OPERATOR_SCHEMA["dv2ps"].params == ()


def test_dv2ps_is_browsable_and_fourier_is_searchable():
    """The curated-list decision, pinned so it is revisited rather than drifted.

    ``dv2ps`` was reachable only by search; it belongs beside ``dv2uv``, whose
    input it shares. ``fourier`` is deliberately left out while no surface can
    emit the ``-f nc4`` its every documented use needs — see
    ``_GLOBAL_OPTION_USERS``.
    """
    curated = OPERATOR_CATEGORIES[NCExplorerCategory.TRANSFORMATION]
    assert "dv2ps" in curated
    assert "fourier" not in curated
    # Still filed under the section, and so still reachable by search from the
    # palette and the model builder — just not offered as a headline act.
    assert OPERATOR_SCHEMA["fourier"].category is NCExplorerCategory.TRANSFORMATION


def test_fourier_and_retocomplex_are_flagged_as_needing_a_global_option():
    """Both need ``-f nc4``, and no operator form can produce one.

    Measured: ``cdo retocomplex gauss.nc c.nc`` is "cdi error
    (cdfDefDatatype): CDI library does not support complex numbers with NetCDF
    classic!", exit 1; with ``-f nc4`` it exits 0.
    """
    for operator in ("fourier", "retocomplex"):
        assert "-f nc4" in OPERATOR_SCHEMA[operator].description


# ---------------------------------------------------------------------------
# The declared input slots, and the shape check reading them
# ---------------------------------------------------------------------------

#: ``operator -> the shape its single input slot must hold``.
EXPECTED_SHAPES = {
    "sp2gp": "spectral", "sp2gpl": "spectral",
    "sp2sp": "spectral", "spcut": "spectral",
    "gp2sp": "gaussian", "gp2spl": "gaussian",
    "uv2dv": "uv", "uv2dvl": "uv",
    "dv2uv": "divergence_vorticity", "dv2uvl": "divergence_vorticity",
    "dv2ps": "divergence_vorticity",
    "fourier": "complex",
}


@pytest.mark.parametrize("operator,shape", sorted(EXPECTED_SHAPES.items()))
def test_every_operator_declares_what_kind_of_field_it_needs(operator, shape):
    slots = operator_inputs(operator)
    assert len(slots) == 1, f"{operator} should declare its one input slot"
    assert slots[0].shape == shape
    assert slots[0].key, "a stable key, so operator_lab can route a sample"
    assert shape in DETECTORS, "a declared shape needs a detector"


def test_operators_wanting_the_same_field_share_a_key():
    """``dv2uv``, ``dv2uvl`` and ``dv2ps`` must be given the *same* file.

    Two guesses at one climatology is the mistake ``OperatorInput.key`` exists
    to prevent, and it applies here for the same reason.
    """
    keys = {op: operator_inputs(op)[0].key
            for op in ("dv2uv", "dv2uvl", "dv2ps")}
    assert len(set(keys.values())) == 1, keys


# -- the detectors, against small synthetic files ---------------------------

@pytest.fixture(scope="module")
def fields(tmp_path_factory):
    """One file of each kind this section needs, built with the binary itself."""
    if shutil.which("cdo") is None:
        pytest.skip("needs an installed CDO")

    directory = tmp_path_factory.mktemp("transformation")
    built = {}

    def make(name, *arguments):
        path = directory / f"{name}.nc"
        completed = _run(*arguments, str(path))
        if completed.returncode != 0 or not path.is_file():
            pytest.skip(f"could not build the {name} sample: "
                        f"{completed.stderr.strip().splitlines()[-1:]}")
        built[name] = path

    make("gaussian", "-f", "nc", "-random,t21grid,1")
    make("lonlat", "-f", "nc", "-random,r18x9,1")
    make("spectral", "-f", "nc", "gp2sp", str(built["gaussian"]))
    make("wind", "-f", "nc", "merge",
         "-setname,u", "-setcode,131", "-random,t21grid,1",
         "-setname,v", "-setcode,132", "-random,t21grid,2")
    make("sd_svo", "-f", "nc", "uv2dv", str(built["wind"]))
    make("complex", "-f", "nc4", "retocomplex", str(built["gaussian"]))
    return built


#: ``(operator, sample, should_warn)``. The "correct" rows are as important as
#: the others: a check that fires on correct input is how a user learns to
#: ignore it.
SHAPE_CASES = [
    ("sp2gp", "spectral", False),
    ("sp2gp", "lonlat", True),
    ("sp2gp", "gaussian", True),
    ("sp2sp", "spectral", False),
    ("spcut", "lonlat", True),
    ("gp2sp", "gaussian", False),
    ("gp2sp", "lonlat", True),
    ("gp2spl", "gaussian", False),
    ("uv2dv", "wind", False),
    ("uv2dv", "lonlat", True),
    ("dv2uv", "sd_svo", False),
    ("dv2uv", "lonlat", True),
    ("dv2ps", "sd_svo", False),
    ("fourier", "complex", False),
    ("fourier", "gaussian", True),
]


@cdo_required
@pytest.mark.parametrize("operator,sample,should_warn", SHAPE_CASES)
def test_the_shape_check_reads_the_file_it_is_given(
        operator, sample, should_warn, fields):
    warnings = check_fields(operator, [str(fields[sample])])
    assert bool(warnings) == should_warn, [str(w) for w in warnings]


@cdo_required
def test_a_gaussian_grid_is_told_from_a_regular_one_by_its_spacing(fields):
    """Not by pole-inclusiveness: ``r360x180`` is non-pole-inclusive and regular.

    Measured ``(max_gap - min_gap) / mean_gap`` over the latitude axis: 8.3e-3
    for t21grid, t63grid and t255grid alike, and exactly 0 for r18x9, r72x36
    and r360x180.
    """
    assert check_fields("gp2sp", [str(fields["gaussian"])]) == []
    assert check_fields("gp2sp", [str(fields["lonlat"])])


@cdo_required
def test_complex_is_detected_through_a_structured_dtype(fields):
    """Not ``dtype.kind == "c"``, which is what a NetCDF4 complex is *not*.

    Read back through xarray, ``cdo -f nc4 retocomplex`` output carries a
    structured dtype with fields ``r`` and ``i``.
    """
    assert check_fields("fourier", [str(fields["complex"])]) == []
    assert check_fields("fourier", [str(fields["gaussian"])])


@cdo_required
def test_an_unreadable_file_is_never_reported_as_wrong(tmp_path):
    """Unverifiable is not the same as wrong — the rule ``units.py`` states."""
    empty = tmp_path / "not-netcdf.nc"
    empty.write_text("this is not a NetCDF file")
    assert check_fields("sp2gp", [str(empty)]) == []
    assert check_fields("sp2gp", [str(tmp_path / "missing.nc")]) == []


@cdo_required
def test_the_shape_warnings_reach_the_units_entry_point(fields):
    """Folded into ``check_inputs`` so every existing caller gets them."""
    from ncexplorer_toolkit.core.units import check_inputs

    assert check_inputs("sp2gp", [str(fields["lonlat"])])
    assert check_inputs("sp2gp", [str(fields["spectral"])]) == []


# ---------------------------------------------------------------------------
# The measured grammar, re-run against the binary
# ---------------------------------------------------------------------------

@cdo_required
def test_two_parameters_are_too_many_for_the_spectral_module(fields):
    """The measurement the one-parameter declaration rests on."""
    out = str(fields["spectral"].parent / "toomany.nc")
    for token in ("sp2gp,type=linear,trunc=42", "sp2gp,,trunc=42"):
        completed = _run(token, str(fields["spectral"]), out)
        assert completed.returncode != 0
        assert "Too many parameters" in completed.stderr


@cdo_required
def test_a_bare_integer_is_read_as_a_type_not_a_truncation(fields):
    out = str(fields["spectral"].parent / "bareint.nc")
    completed = _run("sp2gp,42", str(fields["spectral"]), out)
    assert completed.returncode != 0
    assert "Unsupported type: 42" in completed.stderr


@cdo_required
def test_the_wind_module_rejects_the_spectral_modules_keyword(fields):
    out = str(fields["wind"].parent / "windkw.nc")
    for token, message in (("uv2dv,type=linear", "Unsupported type: type=linear"),
                           ("uv2dv,gridtype=linear", "Unsupported type: gridtype=linear"),
                           ("uv2dvl,trunc=10", "Unsupported type: trunc=10")):
        completed = _run(token, str(fields["wind"]), out)
        assert completed.returncode != 0
        assert message in completed.stderr


@cdo_required
def test_sp2sp_takes_digits_only(fields):
    out = str(fields["spectral"].parent / "sp2sp.nc")
    completed = _run("sp2sp,trunc=10", str(fields["spectral"]), out)
    assert completed.returncode != 0
    assert "must comprise only digits" in completed.stderr

    assert _run("sp2sp,10", str(fields["spectral"]), out).returncode == 0


@cdo_required
def test_the_silent_pass_through_is_still_silent(fields):
    """The defect this whole section's work is about, re-measured.

    Each of these exits 0 against a field it cannot use, and writes the input
    back out. If a future CDO makes one of them fail properly, this test is how
    that is noticed — and the ``_SURPRISING_DEFAULTS`` note should then go.
    """
    import xarray as xr

    source = fields["lonlat"]
    with xr.open_dataset(source, decode_times=False) as dataset:
        before = (dict(dataset.sizes), sorted(dataset.data_vars))

    for operator, warning in (
            ("gp2sp", "No data on regular Gaussian grid found!"),
            ("sp2gp", "No spectral data found!"),
            ("uv2dv", "U-wind not found!"),
            ("dv2uv", "Divergence not found!"),
            ("dv2ps", "Divergence not found!"),
    ):
        out = source.parent / f"passthrough_{operator}.nc"
        completed = _run(operator, str(source), str(out))
        assert completed.returncode == 0, f"{operator} now fails — update the note"
        assert warning in completed.stderr
        with xr.open_dataset(out, decode_times=False) as result:
            assert (dict(result.sizes), sorted(result.data_vars)) == before, (
                f"{operator} now transforms this input — update the note")
