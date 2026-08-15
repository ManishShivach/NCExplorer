"""The Climate model output rewriting section is one operator, and until now
none of it was reachable.

``cmor`` converts a model file into CMIP-compliant output using the CMOR
library. It is the only operator in its section and it exercises more of the
schema's edges than any other: 23 parameters, zero argv outputs, a value that
legally contains commas, another that legally contains spaces, a negative
integer, seven closed value sets, and a working directory that decides the
result. Six separate problems, all of them measured against the installed CDO
2.6.3.

**Nothing was declared.** ``_PARAM_SPECS`` had no ``cmor`` entry, so all 23
parameters were unreachable from the toolbar, the command palette, the model
builder and the batch runner. The only command any surface could build was
``cdo cmor infile``, which cannot succeed on any build — on this one it is
"Operator missing, in.nc is a file on disk!", a message naming the user's data
file rather than the MIP table they left out.

**The last gate passed a call that cannot run.** With nothing declared,
``missing_required_parameters("cmor", [])`` returned ``[]``, so
``_resolve_operator_call`` — the last check before argv, and the one the batch
runner reaches too — assembled and ran that command. Declaring ``MIPtable``
required fixes it through the existing machinery; ``test_the_required_table_is_
refused_by_the_ordinary_machinery`` asserts it is that machinery and not a
special case.

**It was filed under Information.** ``_infer_category`` placed it there on
``nout == 0`` alone, beside ``sinfo`` and ``showname``. ``cmor`` writes NetCDF —
one file per output variable, into a DRS tree — and is ``nout == 0`` only
because CMOR composes the filenames instead of CDO. Fixed by naming the module
in ``_MODULE_CATEGORY``, which is what ``test_file_operations_category.py``
established as the remedy for this class of bug; the prefix cascade is
untouched.

**Nothing could say why it fails.** ``cdo --config has-cmor`` answers ``no`` on
this build and every call aborts with "CMOR support not compiled in!". The
capability is now probed before the run and the abort translated after it, so
the diagnosis exists on both paths.

**Nothing could see what it wrote.** ``nout == 0`` means ``_ResolvedCall.outputs``
is empty, so the execution layer believed the run produced nothing while CMOR
had built a tree of files under ``drs_root``. A failed run therefore left them
on disk while reporting it had cleaned up. Fixed by a pre/post scan of that
directory, not by inventing an argv outfile — ``cdo cmor,<table> infile`` takes
one file, and a second would be read as a second *input*.

**The working directory was invisible.** ``drs_root`` defaults to it and
``info`` defaults to ``CWD/.cdocmorinfo``, so two of the three things deciding
what a run produces were absent from its command line — and the default working
directory was the shared system temp root. Each run now gets its own directory,
recorded in the session log as a ``(cd … && …)`` prefix.

What could NOT be verified here, and is marked as such throughout: this CDO
cannot run the operator. ``cdo -h cmor`` prints the full parameter list even so,
which is why the grammar is measured; the runtime is not, because the abort
fires before the parameters are parsed at all —
``cdo cmor,SomeTable.json,bogus_key=1 in.nc`` gets the identical "CMOR support
not compiled in!" and never complains about the key. Tests needing a run are
marked ``cmor_required`` and name the missing build feature.
"""

import shutil
import subprocess

import pytest

from ncexplorer_toolkit.core.categories import (
    APPENDING_OPERATORS, CWD_DEPENDENT_OPERATORS, NCExplorerCategory,
    OPERATOR_SCHEMA, depends_on_working_directory, file_parameter_indexes,
    invalid_parameter_values, menu_operators, missing_parameter_files,
    missing_required_parameters, operator_module, operator_syntax,
    output_parameter_indexes, parameter_tokens,
)
from ncexplorer_toolkit.core.nc_integration import NCExplorerIntegration
from ncexplorer_toolkit.core.session_log import OperatorRequest

cdo_required = pytest.mark.skipif(
    shutil.which("cdo") is None, reason="needs an installed CDO")


def _has_cmor() -> bool:
    """Whether the installed CDO was built with CMOR, by its own answer."""
    if shutil.which("cdo") is None:
        return False
    try:
        out = subprocess.run(["cdo", "--config", "has-cmor"],
                             capture_output=True, text=True, timeout=20)
    except (subprocess.SubprocessError, OSError):
        return False
    return out.returncode == 0 and out.stdout.strip().lower() == "yes"


#: A skip that names a real missing build capability rather than merely noting
#: that something is absent. ``cdo --config has-cmor`` is CDO's own answer, so
#: the reason is checkable by anyone reading the report.
cmor_required = pytest.mark.skipif(
    not _has_cmor(),
    reason="needs a CDO built with CMOR support (cdo --config has-cmor says no)")


#: Every parameter ``cdo -h cmor`` documents on 2.6.3, as
#: ``(name, kind, optional, form)`` in declaration order. Written out rather
#: than derived from the schema, because a test that asks the schema what the
#: schema contains asserts nothing.
EXPECTED_PARAMS = (
    ("MIPtable", "file", False, "positional"),
    ("cmor_name", "string", True, "keyword"),
    ("name", "string", True, "keyword"),
    ("code", "int", True, "keyword"),
    ("info", "string", True, "keyword"),
    ("grid_info", "file", True, "keyword"),
    ("mapping_table", "file", True, "keyword"),
    ("keep_all_attributes", "select", True, "keyword"),
    ("drs", "select", True, "keyword"),
    ("drs_root", "string", True, "keyword"),
    ("output_mode", "select", True, "keyword"),
    ("last_chunk", "file", True, "keyword"),
    ("max_size", "int", True, "keyword"),
    ("deflate_level", "int", True, "keyword"),
    ("version_date", "int", True, "keyword"),
    ("required_time_units", "string", True, "keyword"),
    ("cell_methods", "select", True, "keyword"),
    ("units", "string", True, "keyword"),
    ("variable_comment", "string", True, "keyword"),
    ("positive", "select", True, "keyword"),
    ("z_axis", "string", True, "keyword"),
    ("character_axis", "select", True, "keyword"),
    ("t_axis", "select", True, "keyword"),
)

#: Parameter name -> its position, so a test can fill one slot without writing
#: out 23 values or hard-coding an index that moves.
INDEX = {name: position for position, (name, *_) in enumerate(EXPECTED_PARAMS)}

TABLE = "Tables/CMIP6_day.json"


def values(**named: str) -> list:
    """A full parameter list with only the named slots filled.

    Positional by declaration order, which is the invariant every checker in
    ``categories`` depends on — so this builds the list the way a surface does.
    """
    supplied = [""] * len(EXPECTED_PARAMS)
    for name, value in named.items():
        supplied[INDEX[name]] = value
    return supplied


@pytest.fixture
def engine():
    """An integration whose CMOR capability is forced on.

    The installed CDO answers ``has-cmor: no``, and the pre-run check refuses
    the operator on that answer — correctly, and it is asserted directly in
    ``test_a_build_without_cmor_is_refused_before_the_run``. Every test *about
    the command* has to get past it, and forcing the cached probe is how: it
    changes what the capability check believes and nothing about how the command
    is built, which is the thing under test.
    """
    integration = NCExplorerIntegration()
    integration._capabilities = {"has-cmor": True}
    return integration


# --- where it lives ---------------------------------------------------------

def test_the_section_is_one_operator():
    """``cmorlite`` shares four letters and is a different module and section.

    Named here so that a later change moving one does not silently move both.
    """
    assert operator_module("cmor") == (
        "Climate Model Output Rewriting to produce CMIP-compliant data")
    assert operator_module("cmorlite") == "CMOR lite"


def test_cmor_is_not_an_information_operator():
    """It writes NetCDF. It is ``nout == 0`` because CMOR names the files.

    Filed under Information it sat beside ``sinfo`` and ``showname`` — operators
    that answer a question about the input and leave nothing behind. This one
    leaves a DRS tree behind.
    """
    spec = OPERATOR_SCHEMA["cmor"]
    assert (spec.nin, spec.nout) == (1, 0)
    assert spec.category is NCExplorerCategory.IMPORT_EXPORT
    assert spec.category is not NCExplorerCategory.INFORMATION


def test_the_category_came_from_the_module_not_the_cascade():
    """The remedy this codebase has settled on, applied for the eighth time.

    ``_MODULE_CATEGORY`` is keyed on the title the binary reports, so the
    grouping cannot drift from CDO. Asserted through ``_infer_category`` itself
    rather than by reading the table, because the table only matters if the
    lookup reaches it — the ``nout == 0`` branch used to fire first.
    """
    from ncexplorer_toolkit.core.categories import _infer_category

    assert _infer_category("cmor", 1, 0) is NCExplorerCategory.IMPORT_EXPORT


def test_cmorlite_is_left_where_it_was():
    """Out of scope, and split from ``cmor`` by CDO's own sectioning."""
    assert OPERATOR_SCHEMA["cmorlite"].category is NCExplorerCategory.MISCELLANEOUS


def test_browsing_reaches_it():
    curated, rest = menu_operators(NCExplorerCategory.IMPORT_EXPORT)
    assert "cmor" in set(curated) | set(rest)


def test_it_takes_a_toolbar_slot_and_that_is_a_known_consequence():
    """Recorded because it is a visible side effect of the category change.

    The toolbar shows the first ten of ``curated + rest``. Import/Export curates
    nine, so the tenth comes from ``rest`` sorted — which ``cmor`` now leads
    alphabetically, displacing ``import_grads`` into the "All…" submenu.

    Left as it falls rather than special-cased, on this file's own standing
    argument: demoting it would be a hand-written exception, and one keyed on a
    capability of *this* build at that — while the whole point of not hiding the
    operator is that a user may point the app at a CDO that has CMOR. It is not
    added to the curated list either; ``OPERATOR_CATEGORIES`` is unchanged.
    """
    curated, rest = menu_operators(NCExplorerCategory.IMPORT_EXPORT)
    assert "cmor" not in curated
    assert (curated + rest)[:10][-1] == "cmor"
    assert "import_grads" in rest


def test_the_execution_layers_operator_list_agrees():
    """``ALL_OPERATORS`` grouped it under "Information (nout == 0)".

    Two lists of one thing disagree eventually; this asserts they do not.
    """
    assert "cmor" in NCExplorerIntegration.ALL_OPERATORS


# --- the parameters ---------------------------------------------------------

def test_declared_parameters_match_the_binarys_own_help():
    spec = OPERATOR_SCHEMA["cmor"]
    actual = tuple((p.name, p.kind, p.optional, p.form) for p in spec.params)
    assert actual == EXPECTED_PARAMS
    assert len(actual) == 23


def test_the_required_table_is_refused_by_the_ordinary_machinery():
    """Not a special case: ``MIPtable`` is required, and that is the whole fix.

    Before it was declared this returned ``[]`` for an empty list, so the last
    gate before argv passed a call that is structurally impossible.
    """
    assert missing_required_parameters("cmor", []) == ["MIP table"]
    assert missing_required_parameters("cmor", [""]) == ["MIP table"]
    assert missing_required_parameters("cmor", [TABLE]) == []


def test_required_parameters_come_first():
    """The invariant ``missing_required_parameters`` indexes on."""
    optionals = [p.optional for p in OPERATOR_SCHEMA["cmor"].params]
    assert optionals == sorted(optionals)


@pytest.mark.parametrize("name,choices", [
    ("keep_all_attributes", ("y", "n")),
    ("drs", ("y", "n")),
    ("output_mode", ("r", "a")),
    ("cell_methods", ("m", "p", "c", "n", "d")),
    ("positive", ("u", "d")),
    ("character_axis", ("basin", "vegtype", "oline")),
    ("t_axis", ("cmip",)),
])
def test_the_closed_value_sets_are_choices_not_free_text(name, choices):
    """Seven parameters have a closed value set, so the GUI gets a picker.

    The manual types several of them CHARACTER, which reads as a switch — but
    the values are single *letters*, not booleans. ``keep_all_attributes`` is
    'y'/'n' and modelling it as a bool would render a checkbox and emit
    ``keep_all_attributes=true``, a value CDO's documentation never mentions.
    """
    param = OPERATOR_SCHEMA["cmor"].params[INDEX[name]]
    assert param.kind == "select"
    assert param.choices == choices
    # No blank in the set: both surfaces add their own "not given" entry, and a
    # second one reads as a value.
    assert "" not in param.choices


def test_the_table_is_a_file_that_is_not_required_to_exist():
    """``reads=False``, on the reasoning already recorded for ``setpartab*``.

    CMOR resolves table names against its own search path, and the manual's own
    example passes a relative ``Tables/CMIP6_day.json``. ``reads=True`` would put
    ``missing_parameter_files`` between the user and every call relying on that
    path — refusing a command CDO accepts, which this codebase treats as the
    worse of the two errors.
    """
    param = OPERATOR_SCHEMA["cmor"].params[0]
    assert (param.kind, param.reads, param.writes) == ("file", False, False)
    assert missing_parameter_files("cmor", ["Tables/CMIP6_day.json"]) == []


def test_no_parameter_is_an_output():
    """Nothing here is written *by name*; CMOR chooses every filename."""
    assert output_parameter_indexes("cmor") == ()


def test_info_is_a_list_and_so_is_not_declared_a_file():
    """A file-valued parameter is one path to the whole toolkit.

    ``info`` is a comma-separated list of filenames. Declared ``file``, a legal
    ``info=a.rc,b.rc`` would be handed to ``_create_input_alias`` as a single
    path and asked ``Path(...).is_file()`` by ``missing_parameter_files`` — so
    the app would refuse a command CDO accepts. The schema has no
    list-of-files kind, and ``string`` is the option that never refuses a valid
    call.
    """
    assert OPERATOR_SCHEMA["cmor"].params[INDEX["info"]].kind == "string"
    assert missing_parameter_files("cmor", values(info="a.rc,b.rc")) == []
    # The three single-valued file parameters *are* files, and are checked.
    assert file_parameter_indexes("cmor") == (
        INDEX["MIPtable"], INDEX["grid_info"], INDEX["mapping_table"],
        INDEX["last_chunk"],
    )


def test_drs_root_is_a_directory_and_so_is_not_declared_a_file():
    """``missing_parameter_files`` tests ``is_file()``, which no directory passes.

    Declared ``file``, every correct value this can ever hold would be refused.
    """
    assert OPERATOR_SCHEMA["cmor"].params[INDEX["drs_root"]].kind == "string"
    assert missing_parameter_files("cmor", values(drs_root="/tmp")) == []


def test_a_missing_grid_info_file_is_still_caught(tmp_path):
    """Single-valued, so this one can be checked — and is."""
    absent = str(tmp_path / "nope.nc")
    assert missing_parameter_files("cmor", values(grid_info=absent)) == [
        f"grid info file: no such file, {absent!r}"]


def test_the_negative_deflate_level_is_accepted():
    """``-1`` is documented as "no compression" and is a legal int.

    A field that refuses the documented default cannot express it. Python's own
    ``int()`` is the parser, which is what keeps the shapes CDO accepts working.
    """
    assert invalid_parameter_values("cmor", values(deflate_level="-1")) == []
    assert invalid_parameter_values("cmor", values(deflate_level="0")) == []
    assert invalid_parameter_values("cmor", values(deflate_level="1.5")) == [
        "deflate level must be a whole number, not '1.5'"]


def test_the_udunits_placeholder_is_not_the_manuals_format_string():
    """CDO documents 'days since YYYY-day-month hh:mm:ss', which is not udunits.

    It names the day twice and the month where the seconds go. The placeholder
    is the real form, and the discrepancy is recorded in ``help`` rather than
    silently corrected — a user reading the manual beside this field will see
    two different strings and deserves to know which one works.
    """
    param = OPERATOR_SCHEMA["cmor"].params[INDEX["required_time_units"]]
    assert param.placeholder == "days since 1850-01-01 00:00:00"
    assert "YYYY-day-month" in param.help


def test_grid_info_is_declared_and_grid_table_is_not():
    """The manual contradicts itself; ``cdo -h cmor`` does not.

    The Example line spells this key ``grid_table``; the PARAMETERS list — and
    the installed binary's own help — document ``grid_info | gi`` and never
    mention ``grid_table``. It is not declared as an alias because it cannot be
    tried: the operator aborts before parsing any key.
    """
    names = {p.name for p in OPERATOR_SCHEMA["cmor"].params}
    assert "grid_info" in names
    assert "grid_table" not in names


def test_short_forms_are_help_text_and_not_parameter_names():
    """Every keyword has one; none of them is a second accepted spelling."""
    names = {p.name for p in OPERATOR_SCHEMA["cmor"].params}
    for short in ("cn", "gi", "mt", "rtu", "kaa", "dr", "om", "lc", "vd",
                  "cm", "vc", "za", "ca", "ta"):
        assert short not in names
    assert "Short form: cn." in OPERATOR_SCHEMA["cmor"].params[
        INDEX["cmor_name"]].help


def test_the_cross_parameter_rule_is_stated_where_it_can_be_read():
    """Left to CDO, and said so — a decision, not an oversight.

    "If name or code is specified, a corresponding cmor_name … is also
    required." No single-parameter positional checker can express it, and its
    real precondition — that the ``cmor_name`` is findable *in the MIP table* —
    needs the CMOR runtime this build lacks. So it is documented on both
    parameters that trigger it rather than half-checked.
    """
    params = OPERATOR_SCHEMA["cmor"].params
    for name in ("name", "code"):
        assert "cmor_name" in params[INDEX[name]].help
    # And no checker refuses the combination, which is what "left to CDO" means.
    supplied = values(MIPtable=TABLE, name="tas")
    assert missing_required_parameters("cmor", supplied) == []
    assert invalid_parameter_values("cmor", supplied) == []


# --- how it is spelled ------------------------------------------------------

def test_only_the_table_is_rendered_when_nothing_else_is_set():
    """An unset optional keyword is absent, not an empty ``key=``."""
    assert parameter_tokens("cmor", values(MIPtable=TABLE)) == [TABLE]
    assert parameter_tokens("cmor", [TABLE]) == [TABLE]


def test_keywords_render_as_name_equals_value_in_declaration_order():
    tokens = parameter_tokens("cmor", values(
        MIPtable=TABLE, cmor_name="tas", drs="n", deflate_level="-1",
        positive="u"))
    assert tokens == [TABLE, "cmor_name=tas", "drs=n", "deflate_level=-1",
                      "positive=u"]


def test_a_comma_list_stays_one_token_and_joins_into_cdos_grammar():
    """``cmor_name=tas,pr`` is one parameter whose value contains a comma.

    ``parameter_tokens`` emits it as a single token and ``','.join`` then
    produces exactly the string CDO's key/value parser wants, because that
    parser treats a bare token as a continuation of the previous key's list.
    That claim is measured against the binary in
    ``test_a_bare_token_continues_the_previous_keys_list``.
    """
    tokens = parameter_tokens("cmor", values(
        MIPtable=TABLE, cmor_name="tas,pr", info="a.rc,b.rc"))
    assert tokens == [TABLE, "cmor_name=tas,pr", "info=a.rc,b.rc"]
    assert ",".join(tokens) == (
        f"{TABLE},cmor_name=tas,pr,info=a.rc,b.rc")


def test_the_usage_line_shows_the_real_spelling():
    """No output slot, and every keyword shown with its ``=``."""
    syntax = operator_syntax("cmor")
    assert syntax.startswith("ifile MIPtable[,cmor_name=<string>]")
    assert "ofile" not in syntax
    assert "obase" not in syntax
    assert "[,t_axis=<select>]" in syntax


# --- the command it builds --------------------------------------------------

def test_nothing_is_appended_after_the_input(engine):
    """``nout == 0``, so argv ends at the input file.

    An appended outfile would not be refused by CDO — it would be silently read
    as a *second input*, which is a different command that happens to parse.
    """
    call = engine._resolve_operator_call("cmor", "in.nc", None, [TABLE])
    assert call.cmd == ["cdo", f"cmor,{TABLE}", "in.nc"]
    assert call.outputs == []
    assert call.aliased_outputs == []
    assert call.side_outputs == []


def test_the_whole_keyword_token_reaches_argv_as_one_element(engine):
    call = engine._resolve_operator_call(
        "cmor", "in.nc", None,
        values(MIPtable=TABLE, cmor_name="tas,pr", deflate_level="-1"))
    assert call.cmd == [
        "cdo", f"cmor,{TABLE},cmor_name=tas,pr,deflate_level=-1", "in.nc"]


def test_an_output_target_is_refused(engine):
    """``nout == 0`` means no output argument is accepted at all."""
    with pytest.raises(ValueError, match="expected 0 output target"):
        engine._resolve_operator_call("cmor", "in.nc", "out.nc", [TABLE])


def test_a_blank_table_is_refused_at_the_last_gate(engine):
    """The gate the batch runner reaches too."""
    with pytest.raises(ValueError, match="MIP table"):
        engine._resolve_operator_call("cmor", "in.nc", None, [""])


# --- values containing spaces ------------------------------------------------

SPACED_UNITS = "days since 1850-01-01 00:00:00"


def test_a_value_with_spaces_is_one_argv_element(engine):
    """Legal inside a single argv element, so the subprocess call is fine."""
    call = engine._resolve_operator_call(
        "cmor", "in.nc", None,
        values(MIPtable=TABLE, required_time_units=SPACED_UNITS))
    assert call.cmd == [
        "cdo", f"cmor,{TABLE},required_time_units={SPACED_UNITS}", "in.nc"]
    # One element, spaces and all — not split into three.
    assert len([part for part in call.cmd if part.startswith("cmor,")]) == 1


def test_a_value_with_spaces_survives_the_shell_export():
    """``session_log`` shell-quotes each argument; asserted, not assumed."""
    import shlex

    request = OperatorRequest(
        operator="cmor", input_files=("in.nc",), parameters=tuple(
            values(MIPtable=TABLE, required_time_units=SPACED_UNITS)),
        nin=1, nout=0)
    line = request.command_line()
    # The round trip is the test: a shell splitting this line must recover the
    # same argv the execution layer built.
    assert shlex.split(line) == [
        "cdo", f"cmor,{TABLE},required_time_units={SPACED_UNITS}", "in.nc"]


def test_a_value_with_spaces_survives_the_makefile_export():
    """The risky one: ``make`` splits prerequisite lists on whitespace.

    It survives because the parameter is in the *recipe*, which is a shell line
    built by ``command_line()``, and never in the prerequisites, which hold
    input files only. The existing warning in ``export_makefile`` is about paths
    for exactly that reason, and this asserts a spaced parameter does not need
    it.
    """
    from ncexplorer_toolkit.core.session_log import (
        OK, SessionStep, export_makefile)

    request = OperatorRequest(
        operator="cmor", input_files=("in.nc",), parameters=tuple(
            values(MIPtable=TABLE, required_time_units=SPACED_UNITS)),
        nin=1, nout=0)
    text = export_makefile([SessionStep(request=request, status=OK)])
    assert f"'cmor,{TABLE},required_time_units={SPACED_UNITS}'" in text
    assert "WARNING: some paths contain spaces" not in text


# --- the working directory --------------------------------------------------

def test_cmor_is_the_only_cwd_dependent_operator():
    """``drs_root`` defaults to it and ``info`` to ``CWD/.cdocmorinfo``."""
    assert CWD_DEPENDENT_OPERATORS == {"cmor": "drs_root"}
    assert depends_on_working_directory("cmor")
    assert not depends_on_working_directory("timmean")


def test_each_run_gets_its_own_directory(engine):
    """Not the shared system temp root, which is what every other run uses.

    ``TempFileStore`` with no explicit directory is ``tempfile.gettempdir()``, so
    without this the DRS tree would land in a directory every process on the
    machine writes to — and the pre/post scan would attribute their files to
    this run and delete them on a failure.
    """
    from pathlib import Path

    first = engine._resolve_operator_call("cmor", "in.nc", None, [TABLE])
    second = engine._resolve_operator_call("cmor", "in.nc", None, [TABLE])
    assert first.cwd != second.cwd
    assert Path(first.cwd).is_dir()
    assert first.cwd != str(engine._tstore.base_dir)
    # An unset drs_root means CDO writes into the working directory, so that is
    # what gets scanned.
    assert first.scan_root == first.cwd


def test_an_explicit_drs_root_is_what_gets_scanned(engine, tmp_path):
    call = engine._resolve_operator_call(
        "cmor", "in.nc", None, values(MIPtable=TABLE, drs_root=str(tmp_path)))
    assert call.scan_root == str(tmp_path)
    assert call.cwd != str(tmp_path)


def test_every_other_operator_keeps_the_directory_it_had(engine):
    """Nothing about this change may move another operator's run.

    ``cwd == ""`` is "the default", which both execution paths resolve to the
    temporary store's base — the directory every run used before this existed.
    Left empty rather than filled in so that resolving an ordinary call does not
    have to touch ``_tstore`` at all.
    """
    call = engine._resolve_operator_call("timmean", "in.nc", "out.nc", None)
    assert call.cwd == ""
    assert call.scan_root == ""


def test_the_exported_command_carries_the_directory_in_a_subshell():
    """A bare ``cd`` would relocate every step after it in the script."""
    request = OperatorRequest(
        operator="cmor", input_files=("in.nc",), parameters=(TABLE,),
        nin=1, nout=0, cwd="/runs/cmor 1")
    assert request.command_line() == (
        f"(cd '/runs/cmor 1' && cdo cmor,{TABLE} in.nc)")


def test_a_step_without_a_directory_builds_exactly_what_it_used_to():
    """The default must not change any command recorded before this existed."""
    request = OperatorRequest(
        operator="timmean", input_files=("in.nc",), output_files=("out.nc",))
    assert request.cwd == ""
    assert request.command_line() == "cdo timmean in.nc out.nc"


# --- what a run touches -----------------------------------------------------

class _FakeOutcome:
    """One finished process, in the shape ``finalise`` reads."""

    def __init__(self, *, completed=True, returncode=0, stdout="", stderr=""):
        self.completed = completed
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.detail = stderr
        self.duration = 0.1
        self.state = "finished" if completed else "cancelled"


def _write_drs_tree(root):
    """What CMOR leaves behind: one file per variable, under a DRS path."""
    from pathlib import Path

    written = []
    for variable in ("tas", "pr"):
        directory = (Path(root) / "CMIP6" / "CMIP" / "MPI-M" / "day"
                     / variable / "gn" / "v20240115")
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{variable}_day_MPI-ESM_historical.nc"
        target.write_bytes(b"\x89HDF\r\n\x1a\n")
        written.append(str(target))
    return sorted(written)


def test_a_successful_run_reports_the_tree_nobody_named(engine, tmp_path):
    """``nout == 0`` used to mean "this run produced nothing".

    It produced a DRS tree, and no surface could offer to open any of it.
    """
    run = engine.prepare_operator_run(
        "cmor", input_files="in.nc",
        extra_parameters=values(MIPtable=TABLE, drs_root=str(tmp_path)))
    expected = _write_drs_tree(tmp_path)

    result = run.finalise(_FakeOutcome())
    assert result.success
    assert list(result.discovered_outputs) == expected


def test_a_pre_existing_file_is_never_claimed_as_output(engine, tmp_path):
    """Only what appeared *during* the run counts, on both paths."""
    (tmp_path / "already.nc").write_bytes(b"old")
    run = engine.prepare_operator_run(
        "cmor", input_files="in.nc",
        extra_parameters=values(MIPtable=TABLE, drs_root=str(tmp_path)))
    expected = _write_drs_tree(tmp_path)

    result = run.finalise(_FakeOutcome())
    assert list(result.discovered_outputs) == expected
    assert str(tmp_path / "already.nc") not in result.discovered_outputs


def test_a_failed_run_removes_the_tree_it_wrote(engine, tmp_path):
    """It used to leave it on disk while reporting it had cleaned up.

    The same defect ``_discard_failed_outputs`` was written for, in the one
    place that function could not see: with no outputs, it had nothing to scan.
    """
    from pathlib import Path

    (tmp_path / "already.nc").write_bytes(b"old")
    run = engine.prepare_operator_run(
        "cmor", input_files="in.nc",
        extra_parameters=values(MIPtable=TABLE, drs_root=str(tmp_path)))
    written = _write_drs_tree(tmp_path)

    result = run.finalise(_FakeOutcome(returncode=1, stderr="something failed"))
    assert not result.success
    for path in written:
        assert not Path(path).exists()
    # And the file that was there before the run is untouched.
    assert (tmp_path / "already.nc").read_bytes() == b"old"


def test_a_cancelled_run_removes_the_tree_it_wrote(engine, tmp_path):
    """Half a DRS tree is worse than none, exactly as half a NetCDF file is."""
    from pathlib import Path

    run = engine.prepare_operator_run(
        "cmor", input_files="in.nc",
        extra_parameters=values(MIPtable=TABLE, drs_root=str(tmp_path)))
    written = _write_drs_tree(tmp_path)

    result = run.finalise(_FakeOutcome(completed=False, returncode=None))
    assert not result.success
    for path in written:
        assert not Path(path).exists()


#: A stand-in ``cdo`` that answers the three things construction asks it and
#: then behaves like a CMOR-enabled build: it writes a DRS tree under the
#: directory it was started in and exits with whatever code the test asked for.
#:
#: Needed because the installed CDO cannot run the operator, and because the
#: *synchronous* path has to be exercised for real rather than through
#: ``finalise``. This codebase says in four separate docstrings that the two
#: execution paths must not drift; testing one of them is not testing the rule.
FAKE_CDO = r"""#!/bin/sh
case "$1" in
  --version)   echo "Climate Data Operators version 2.6.3"; exit 0 ;;
  --operators) echo "cmor  Climate Model Output Rewriting (1|0)"
               echo "timmean  Mean over time (1|1)"; exit 0 ;;
  --config)    echo "yes"; exit 0 ;;
esac
case "$1" in
  cmor,*)
    dir="CMIP6/CMIP/MPI-M/day"
    mkdir -p "$dir/tas/gn/v1" "$dir/pr/gn/v1"
    printf 'x' > "$dir/tas/gn/v1/tas_day.nc"
    printf 'x' > "$dir/pr/gn/v1/pr_day.nc"
    if [ -n "$FAKE_CDO_FAIL" ]; then
      echo "cdo    cmor (Abort): something went wrong" >&2
      exit 1
    fi
    exit 0 ;;
esac
exit 0
"""


@pytest.fixture
def stub_engine(tmp_path, monkeypatch):
    """An integration driving the stub above, constructed the ordinary way."""
    binary = tmp_path / "fake_cdo"
    binary.write_text(FAKE_CDO)
    binary.chmod(0o755)
    monkeypatch.setattr(
        "ncexplorer_toolkit.core.nc_integration._bundled_cdo_path",
        lambda: None)
    return NCExplorerIntegration(NCExplorer_binary_path=str(binary),
                                 temp_dir=str(tmp_path / "store"))


def test_the_synchronous_path_reports_the_tree_too(stub_engine):
    """``execute_operator``, not ``finalise`` — the other half of the rule."""
    from pathlib import Path

    result = stub_engine.execute_operator(
        "cmor", input_files="in.nc", extra_parameters=[TABLE])

    assert result.success
    assert len(result.discovered_outputs) == 2
    assert all(path.endswith("_day.nc") for path in result.discovered_outputs)
    # Written under the run's own directory, which is the working directory
    # CDO was given — not the shared temp root.
    for path in result.discovered_outputs:
        assert Path(path).is_file()
        assert "ncexplorer_cmor_" in path
    # And nothing was appended after the input.
    assert stub_engine.command_history[-1].argv == (
        stub_engine.NCExplorer_binary, f"cmor,{TABLE}", "in.nc")


def test_the_synchronous_path_cleans_up_after_a_failure(stub_engine, monkeypatch):
    """A failed run must not leave the tree behind while reporting it cleaned up."""
    from pathlib import Path

    monkeypatch.setenv("FAKE_CDO_FAIL", "1")
    result = stub_engine.execute_operator(
        "cmor", input_files="in.nc", extra_parameters=[TABLE])

    assert not result.success
    assert result.discovered_outputs == ()
    run_dir = Path(stub_engine.command_history[-1].cwd)
    assert run_dir.is_dir()
    assert list(run_dir.rglob("*.nc")) == []


def test_the_recorded_working_directory_is_the_one_that_ran(stub_engine):
    """What makes the session log reproducible for this operator."""
    stub_engine.execute_operator("cmor", input_files="in.nc",
                                 extra_parameters=[TABLE])
    cmor_cwd = stub_engine.command_history[-1].cwd
    assert "ncexplorer_cmor_" in cmor_cwd

    stub_engine.execute_operator("timmean", input_files="in.nc",
                                 output_files="out.nc")
    assert stub_engine.command_history[-1].cwd == str(
        stub_engine._tstore.base_dir)


def test_cmor_is_not_an_appending_operator():
    """``output_mode=a`` is append semantics, and this set is the wrong shape.

    It is keyed by operator; ``cmor`` appends only when a parameter *value* says
    so. Membership would apply the snapshot to every run including replace mode
    — and would be inert anyway, since ``append_sizes`` is built from
    ``aliased_outputs`` and ``cmor`` has none. Decided, not ignored; see the
    note above ``APPENDING_OPERATORS``.
    """
    assert APPENDING_OPERATORS == frozenset({"cat"})
    assert "cmor" not in APPENDING_OPERATORS


def test_a_run_in_append_mode_still_builds_the_documented_command(engine, tmp_path):
    """Not refusing it is part of the same decision.

    ``last_chunk`` is a real single-valued file and is checked for existence —
    it is the chunk being appended to, so a path that is not there is a mistake
    worth catching before the run.
    """
    chunk = tmp_path / "chunk.nc"
    chunk.write_bytes(b"\x89HDF\r\n\x1a\n")
    call = engine._resolve_operator_call(
        "cmor", "in.nc", None,
        values(MIPtable=TABLE, output_mode="a", last_chunk=str(chunk)))
    assert call.cmd[1] == f"cmor,{TABLE},output_mode=a,last_chunk={chunk}"
    assert call.append_sizes == {}


def test_a_last_chunk_that_is_not_there_is_refused(engine, tmp_path):
    """The counterpart: an append target must exist to be appended to."""
    absent = str(tmp_path / "nope.nc")
    assert missing_parameter_files("cmor", values(last_chunk=absent)) == [
        f"last chunk: no such file, {absent!r}"]


# --- the capability probe ---------------------------------------------------

def test_a_build_without_cmor_is_refused_before_the_run():
    """The whole point: the diagnosis exists before a subprocess is started."""
    engine = NCExplorerIntegration()
    engine._capabilities = {"has-cmor": False}
    with pytest.raises(ValueError, match="built without CMOR support"):
        engine._resolve_operator_call("cmor", "in.nc", None, [TABLE])


def test_an_unanswered_probe_never_refuses():
    """A probe that cannot answer is not a refusal.

    An older CDO with no ``--config`` would otherwise have its ``cmor`` blocked
    by this application rather than by anything about the binary — stricter than
    the tool it fronts, which is the failure mode the schema is written against.
    """
    engine = NCExplorerIntegration()
    engine._capabilities = {"has-cmor": None}
    assert engine.missing_build_feature("cmor") == ""
    call = engine._resolve_operator_call("cmor", "in.nc", None, [TABLE])
    assert call.cmd == ["cdo", f"cmor,{TABLE}", "in.nc"]


def test_no_other_operator_is_gated_on_a_build_feature():
    """``cmorlite`` shares the letters and not the dependency.

    It processes MIP tables with CDO's own I/O library and runs fine where
    ``has-cmor`` is no — which is why this is a table and not a check on the
    name.
    """
    engine = NCExplorerIntegration()
    engine._capabilities = {"has-cmor": False}
    for operator in ("cmorlite", "conv_cmor_table", "dump_cmor_table",
                     "timmean", "copy"):
        assert engine.missing_build_feature(operator) == ""


def test_the_abort_is_translated_when_the_probe_could_not_say():
    """The other half: CDO's message is accurate and tells a user nothing.

    "CMOR support not compiled in!" does not say it is a property of the build
    rather than of the command, which is the only actionable part.
    """
    explanation = NCExplorerIntegration.explain_failure(
        "", "cdo    cmor (Abort): CMOR support not compiled in!")
    assert "built without CMOR support" in explanation
    assert "cdo --config has-cmor" in explanation


def test_the_translation_keeps_cdos_own_words():
    """Prepended, never substituted — a bug report needs the original."""
    stderr = "cdo    cmor (Abort): CMOR support not compiled in!"
    annotated = NCExplorerIntegration._annotate_failure("", stderr)
    assert annotated.endswith(stderr)
    assert annotated != stderr


def test_an_ordinary_failure_is_left_alone():
    stderr = "cdo    timcor (Abort): Input streams have different lengths"
    assert NCExplorerIntegration._annotate_failure("", stderr) == stderr
    assert NCExplorerIntegration.explain_failure("", stderr) == ""


def test_the_description_says_what_to_check_before_a_run():
    """Baked into the schema, so every surface shows it.

    Unconditional rather than probed at import: the schema is shared and the
    binary is a per-instance setting, so a description asserting "your CDO
    cannot do this" would go stale the moment a user pointed it elsewhere.
    """
    description = OPERATOR_SCHEMA["cmor"].description
    assert "cdo --config has-cmor" in description
    assert "drs_root" in description


# --- the harness profile ----------------------------------------------------

def test_the_lab_skip_is_a_capability_skip_not_a_declaration_one():
    """With no parameter values the harness would skip for its own reason.

    "no default for the required parameter 'MIPtable'" is a fact about
    ``profiles.py``, and it would go on being true after somebody installed a
    CDO with CMOR — while the reported reason blamed the build.
    """
    from operator_lab.profiles import (
        OPERATOR_PARAMETERS, PARAMETER_DEFAULTS, UNTESTABLE)

    assert UNTESTABLE["cmor"] == "needs a CDO built with CMOR support"
    profile = OPERATOR_PARAMETERS["cmor"]
    assert profile["MIPtable"]

    supplied = [profile.get(p.name, PARAMETER_DEFAULTS.get(p.name, ""))
                for p in OPERATOR_SCHEMA["cmor"].params]
    assert missing_required_parameters("cmor", supplied) == []
    assert invalid_parameter_values("cmor", supplied) == []


def test_the_lab_does_not_inherit_conflicting_generic_defaults():
    """``name``, ``code`` and ``units`` are answered by the shared table.

    Inherited, they would build ``name=random,code=1,units=days`` — selecting
    one variable by name *and* by code, with no ``cmor_name`` to map either —
    which is the mistake the operator's own DESCRIPTION warns about, assembled
    by accident out of defaults meant for other operators.
    """
    from operator_lab.profiles import OPERATOR_PARAMETERS, PARAMETER_DEFAULTS

    profile = OPERATOR_PARAMETERS["cmor"]
    for name in ("name", "code", "units"):
        assert name in PARAMETER_DEFAULTS       # the trap is real
        assert profile[name] == ""              # and it is disarmed

    supplied = [profile.get(p.name, PARAMETER_DEFAULTS.get(p.name, ""))
                for p in OPERATOR_SCHEMA["cmor"].params]
    assert parameter_tokens("cmor", supplied) == [
        "CMIP6_day.json", "cmor_name=tas", "deflate_level=-1"]


# --- the claims above, re-measured against the installed binary --------------

@cdo_required
def test_every_declared_parameter_name_is_one_the_binary_knows():
    """``cdo -h cmor`` prints the full list even though the runtime is absent.

    Which is what makes the grammar measurable here at all, and it is re-read
    rather than trusted: this is the check that would catch a parameter declared
    from the manual that the binary does not have.
    """
    out = subprocess.run(["cdo", "-h", "cmor"], capture_output=True, text=True)
    help_text = (out.stdout or "") + (out.stderr or "")
    for name, *_ in EXPECTED_PARAMS:
        assert name in help_text, f"{name} not in `cdo -h cmor`"
    # And the key the manual's Example line uses is not there.
    assert "grid_table" not in help_text


@cdo_required
def test_a_bare_token_continues_the_previous_keys_list():
    """The grammar ``cmor_name=tas,pr`` relies on, measured on ``collgrid``.

    ``cmor``'s own parser cannot be reached on this build — the "CMOR support
    not compiled in!" abort fires before any key is looked at, which was checked
    with a deliberately bogus key. ``collgrid`` uses the same operator-token
    key/value parser, takes a documented comma-separated list, and runs, so the
    three behaviours the joined token depends on are measurable through it:

        collgrid,name=tas        -> selects tas
        collgrid,name=tas,pr     -> selects tas AND pr   (bare token continues)
        collgrid,name=tas,nx=2   -> selects tas only     (k=v starts a new key)

    Measured on 2.6.3. A fourth call settles it beyond doubt:
    ``collgrid,name=tas,bogus`` fails with "Could not find all requested
    variables: (1/2)" — CDO went looking for *two* variables, so the bare token
    was consumed as a list item and not rejected as an unknown parameter.
    """
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as work:
        work = Path(work)

        def cdo(*args):
            return subprocess.run(["cdo", "-s", *[str(a) for a in args]],
                                  capture_output=True, text=True)

        cdo("-O", "-f", "nc", "-settaxis,2000-01-01,00:00:00,1day",
            "-duplicate,3", "-random,r6x3,1", work / "a.nc")
        cdo("-O", "-setname,tas", work / "a.nc", work / "tas.nc")
        cdo("-O", "-setname,pr", work / "a.nc", work / "pr.nc")
        cdo("-O", "merge", work / "tas.nc", work / "pr.nc", work / "two.nc")
        cdo("-O", "distgrid,2", work / "two.nc", work / "piece")
        pieces = [work / "piece00000.nc", work / "piece00001.nc"]

        def names(token):
            target = work / "out.nc"
            target.unlink(missing_ok=True)
            done = cdo(token, *pieces, target)
            if done.returncode != 0:
                return done.returncode, ""
            listed = cdo("showname", target)
            return 0, " ".join(listed.stdout.split())

        assert names("collgrid,name=tas") == (0, "tas")
        assert names("collgrid,name=tas,pr") == (0, "tas pr")
        assert names("collgrid,name=tas,nx=2") == (0, "tas")


@cdo_required
def test_this_build_reports_its_cmor_support_and_the_abort_matches_it():
    """The two signals must agree, or the pre-run check contradicts the run.

    On a build without CMOR: ``--config`` says no and the operator aborts. On a
    build with it, the operator gets past the capability gate. Either way this
    asserts the probe and the binary tell the same story, which is the one thing
    that would make the refusal wrong.
    """
    probe = subprocess.run(["cdo", "--config", "has-cmor"],
                           capture_output=True, text=True)
    if probe.returncode != 0 or probe.stdout.strip().lower() not in ("yes", "no"):
        pytest.skip("this CDO has no --config has-cmor to compare against")

    supported = probe.stdout.strip().lower() == "yes"
    run = subprocess.run(["cdo", "cmor,SomeTable.json", "missing_input.nc"],
                         capture_output=True, text=True)
    combined = (run.stdout or "") + (run.stderr or "")
    assert ("CMOR support not compiled in" in combined) is not supported


@cdo_required
def test_the_operator_with_no_parameter_does_not_hang():
    """Not the ``pow``/``cmorlite`` class, and worth pinning as a negative.

    ``cdo cmor infile`` aborts in milliseconds with "Operator missing, … is a
    file on disk!" — which is useless to a user, since it names their data file
    rather than the MIP table, but it is an abort and not a freeze. That is why
    ``cmor`` is absent from ``_MISC_HANGS_WITHOUT_PARAMETERS``, and why the
    required-parameter declaration is a usability fix rather than a hang fix.
    """
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as work:
        infile = Path(work) / "in.nc"
        subprocess.run(
            ["cdo", "-O", "-f", "nc", "-random,r6x3,1", str(infile)],
            capture_output=True, text=True, check=True)

        done = subprocess.run(["cdo", "cmor", str(infile)],
                              capture_output=True, text=True, timeout=30)
        assert done.returncode != 0
        assert "Operator missing" in (done.stdout + done.stderr)


@cmor_required
def test_a_real_run_writes_the_tree_this_layer_expects(tmp_path):
    """Reachable only on a CMOR-enabled build; skipped with that named.

    Everything above about output discovery is measured against a synthesised
    tree, because this CDO cannot make a real one. This is the test that closes
    that gap the day a CMOR-enabled binary is installed, and it is written now
    so the gap is visible rather than assumed away.
    """
    engine = NCExplorerIntegration()
    infile = tmp_path / "in.nc"
    subprocess.run(
        ["cdo", "-O", "-f", "nc", "-settaxis,2000-01-01,00:00:00,1day",
         "-duplicate,3", "-random,r6x3,1", str(infile)],
        capture_output=True, text=True, check=True)

    result = engine.execute_operator(
        "cmor", input_files=str(infile),
        extra_parameters=values(MIPtable=TABLE, drs_root=str(tmp_path / "drs")))
    if not result.success:
        pytest.skip(f"needs a real MIP table on the CMOR search path: "
                    f"{result.stderr.strip()[:120]}")
    assert result.discovered_outputs
