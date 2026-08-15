"""The Import/Export section is a section, and its operators can be run.

CDO's 2.6.3 reference manual has one Import/Export section holding six modules
and thirteen operators. The app had no such category, and the thirteen were
spread across three:

    Miscellaneous   import_binary, import_cmsaf  (and the alias import_grads)
    Formatted I/O   input inputsrv inputext output outputf outputint
                    outputsrv outputext
    Information     outputtab, gmtxyz, gmtcells  (and outputkey, outputbounds,
                    outputcenter)

**Where they live.** The Information placements were not a missing table entry
but an *ordering* bug: ``_infer_category`` returned Information for any
``nout == 0`` operator several branches before it consulted ``_MODULE_CATEGORY``,
so CDO could be sitting there with a module title saying "GMT output" and never
be asked. This is the first section whose fix needed the order of two tests
changed as well as the module table widened, and the reorder was safe to make
because *no* ``nout == 0`` operator in the catalog had a module named in that
table at the time — measured, and pinned below.

"Formatted I/O" is gone rather than kept alongside. It was never CDO's name for
anything, and it named the middle two of the section's six modules, so keeping
both would have needed an invented rule for which operator falls on which side
of a line the manual does not draw.

**What they take.** ``import_binary`` was modelled as one input *plus* a
``ctlfile`` file parameter, which built ``cdo import_binary,demo.ctl in.nc
out.nc`` — three file tokens for a two-file operator. ``input`` was missing the
``zaxis`` its synopsis brackets, ``outputf`` had both of its optional parameters
declared required, and ``outputtab``'s eighteen-keyname grammar was a free-text
box.

**Whether they run at all.** ``input``/``inputsrv``/``inputext`` read their data
from standard input, and neither execution path closed stdin — so all three
blocked until the run timeout, and no surface offered a way to supply data
either.

Everything asserted here was measured against the installed CDO 2.6.3; the
tests marked ``cdo_required`` re-measure it.
"""

import os
import shutil
import struct
import subprocess

import pytest

from ncexplorer_toolkit.core.categories import (
    NCExplorerCategory, OPERATOR_CATEGORIES, OPERATOR_SCHEMA,
    _MODULE_CATEGORY, _OUTPUTTAB_KEYNAME_CHOICES, invalid_parameter_values,
    menu_operators, missing_required_parameters, operator_module,
    parameter_tokens, reads_stdin,
)
from ncexplorer_toolkit.core.cdo_operator_catalog import CDO_OPERATORS
from ncexplorer_toolkit.core.nc_integration import NCExplorerIntegration
from ncexplorer_toolkit.core.session_log import OperatorRequest

cdo_required = pytest.mark.skipif(
    shutil.which("cdo") is None, reason="needs an installed CDO")


#: The CDO 2.6.3 Import/Export section, module by module, exactly as the binary
#: reports it. Written out rather than derived from the schema, because a test
#: that asks the schema what the schema contains asserts nothing.
#:
#: Thirteen operators, not the eleven the section is sometimes described as
#: having: ``cdo --help input`` and ``cdo --help output`` each document a family,
#: and counting the families rather than their members loses ``inputsrv``,
#: ``inputext``, ``outputint``, ``outputsrv`` and ``outputext``.
IMPORT_EXPORT_BY_MODULE = {
    "Import binary data sets":  "import_binary",
    "Import CM-SAF HDF5 files": "import_cmsaf",
    "Formatted input":          "input inputsrv inputext",
    "Formatted output":         "output outputf outputint outputsrv outputext",
    "Table output":             "outputtab",
    "GMT output":               "gmtxyz gmtcells",
}

#: The thirteen the manual documents.
DOCUMENTED = sorted(
    name for names in IMPORT_EXPORT_BY_MODULE.values() for name in names.split()
)

#: ``alias -> the operator it is another name for``, as ``cdo --operators``
#: spells it ("--> gmtcells"). Four of them, and they are the reason
#: ``_resolve_params`` exists: an alias must not be able to disagree with its
#: target about the operator's own shape.
ALIASES = {
    "import_grads": "import_binary",
    "outputkey":    "outputtab",
    "outputbounds": "gmtcells",
    "outputcenter": "gmtxyz",
}

#: What each one's command line looks like, from the SYNOPSIS the binary prints.
#: The point of listing it is the arity: three of these take no input file and
#: six write no output file, and both are shapes the app got wrong somewhere.
SYNOPSIS = {
    "import_binary": ("cdo import_binary infile.ctl outfile",         1, 1),
    "import_cmsaf":  ("cdo import_cmsaf infile outfile",              1, 1),
    "input":         ("cdo input,grid[,zaxis] outfile",               0, 1),
    "inputsrv":      ("cdo inputsrv outfile",                         0, 1),
    "inputext":      ("cdo inputext outfile",                         0, 1),
    "output":        ("cdo output infiles",                          -1, 0),
    "outputf":       ("cdo outputf[,format[,nelem]] infiles",        -1, 0),
    "outputint":     ("cdo outputint infiles",                       -1, 0),
    "outputsrv":     ("cdo outputsrv infiles",                       -1, 0),
    "outputext":     ("cdo outputext infiles",                       -1, 0),
    "outputtab":     ("cdo outputtab,keynames infiles",              -1, 0),
    "gmtxyz":        ("cdo gmtxyz infile",                            1, 0),
    "gmtcells":      ("cdo gmtcells infile",                          1, 0),
}

#: The eighteen keynames of ``cdo --help outputtab``, in the order its table
#: prints them. The manual's list, written out, so the schema is checked against
#: the documentation rather than against itself.
MANUAL_KEYNAMES = [
    "value", "name", "param", "code", "x", "y", "lon", "lat", "lev",
    "xind", "yind", "timestep", "date", "time", "year", "month", "day",
    "nohead",
]


# --- where they live --------------------------------------------------------

@pytest.mark.parametrize("operator", DOCUMENTED + sorted(ALIASES))
def test_every_operator_of_the_section_is_import_export(operator):
    """All thirteen and all four aliases, in one category.

    They were in three. An alias lands with the operator it aliases, which is
    the only answer that does not split the two.
    """
    assert OPERATOR_SCHEMA[operator].category is NCExplorerCategory.IMPORT_EXPORT


def test_formatted_io_is_gone_rather_than_coexisting():
    """One category over one manual section, not two claiming the same names.

    The brief allowed either — rename, or keep both with a stated rule — and
    forbade leaving both claiming the same operators. This asserts the choice
    that was made, so a later reinstatement of "Formatted I/O" has to be
    deliberate.
    """
    assert not hasattr(NCExplorerCategory, "FORMATTED_IO")
    assert "Formatted I/O" not in {c.value for c in NCExplorerCategory}
    assert NCExplorerCategory.IMPORT_EXPORT.value == "Import/Export"


@pytest.mark.parametrize("module,names", sorted(IMPORT_EXPORT_BY_MODULE.items()))
def test_the_schema_agrees_with_the_installed_binarys_modules(module, names):
    """The category comes from CDO's module title, not from the operator's name.

    Both halves matter: the operator really is in that module according to the
    installed binary, and that module really is named in ``_MODULE_CATEGORY``.
    """
    assert _MODULE_CATEGORY[module] is NCExplorerCategory.IMPORT_EXPORT
    for name in names.split():
        assert operator_module(name) == module


def test_naming_the_modules_moved_the_undocumented_siblings_too():
    """28 operators, not 13, and that is the point rather than a side effect.

    Keying on the module means the binary decides membership, so the eleven
    operators CDO files in these six modules without documenting them in the
    Import/Export section come along. They are exporters, and they were in
    Information — which is where a user looks for ``info`` and ``sinfo``.

    Counted over the six modules rather than over the whole category, which is
    the claim the docstring actually makes. The two stopped being the same
    number when ``cmor`` joined the category from a module of its own: it is not
    one of the six, so counting the category would make this test fail for a
    change it says nothing about — and would have made the previous count a
    claim about ``_MODULE_CATEGORY`` at large rather than about this section.
    ``test_the_category_holds_these_six_modules_and_cmor`` covers the rest.
    """
    from ncexplorer_toolkit.core.categories import operator_module

    modules = set(IMPORT_EXPORT_BY_MODULE)
    in_modules = {name for name, spec in OPERATOR_SCHEMA.items()
                  if spec.category is NCExplorerCategory.IMPORT_EXPORT
                  and operator_module(name) in modules}
    assert len(in_modules) == 28
    assert in_modules >= set(DOCUMENTED) | set(ALIASES)

    extra = in_modules - set(DOCUMENTED) - set(ALIASES)
    assert extra == {
        "outputarr", "outputfld", "outputts", "outputxyz",
        "outputkml", "outputvrml", "outputtri", "outputvector",
        "outputcenter2", "outputboundscpt", "outputcentercpt",
    }


def test_the_category_holds_these_six_modules_and_cmor():
    """Nothing else may drift into Import/Export unnoticed.

    ``cmor`` is here deliberately and from a module of its own — CDO's "Climate
    Model Output Rewriting to produce CMIP-compliant data" — because it is a
    ``nout == 0`` *writer*, which is exactly what the six modules above are and
    exactly why they were moved out of Information. The argument is set out in
    ``_MODULE_CATEGORY`` and asserted in
    ``test/test_catagories/test_climate_model_output_rewriting.py``; this is the
    line that stops a ninth module arriving here by accident.
    """
    from ncexplorer_toolkit.core.categories import operator_module

    in_category = {name for name, spec in OPERATOR_SCHEMA.items()
                   if spec.category is NCExplorerCategory.IMPORT_EXPORT}
    assert len(in_category) == 29
    assert {operator_module(name) for name in in_category} == (
        set(IMPORT_EXPORT_BY_MODULE)
        | {"Climate Model Output Rewriting to produce CMIP-compliant data"})


#: The one module outside this section that overrides the ``nout == 0``
#: branch, and the decision behind it.
#:
#: ``gradsdes`` and ``dumpmap`` write no file through CDO's output slot, so the
#: branch calls them Information. ``gradsdes`` writes a GrADS ``.ctl``
#: descriptor beside its *input* instead — a file for another program, which is
#: the same argument that moved the GMT and table exporters out of Information
#: below, and it is why naming the module is right for these two.
#:
#: ``gridcellindex`` and ``verifygrid`` are the deliberate counter-case: both
#: are also ``nout == 0`` and also documented under Miscellaneous, and both are
#: left in Information because they print a report about their input and leave
#: nothing behind. Their modules are correspondingly *not* named in
#: ``_MODULE_CATEGORY``. See the note above the ``nout == 0`` branch in
#: ``_infer_category`` for the full argument.
NOUT0_MODULES_OUTSIDE_IMPORT_EXPORT = {
    "GrADS data descriptor file": NCExplorerCategory.MISCELLANEOUS,
}


def test_the_reorder_alone_would_have_moved_nothing():
    """Why swapping the ``nout == 0`` test with the module lookup was safe.

    The claim the reorder rests on: at the time it was made, no ``nout == 0``
    operator had a module named in ``_MODULE_CATEGORY``, so consulting the table
    first changed nothing on its own — every operator that moved did so because
    of the six titles added with it.

    Asserted as a standing property rather than a historical note: a
    ``nout == 0`` operator whose module is named is one somebody decided to
    claim, and if one appears that nothing above accounts for, the module table
    has grown somewhere that needs its own decision.

    It has grown once since, and the exception is enumerated rather than
    waived: the Miscellaneous pass named "GrADS data descriptor file", which
    holds two ``nout == 0`` operators. Widening this test to "any named module
    wins" would have made it stop asking the question; listing the module keeps
    the next one failing here, which is the whole point.
    """
    for name, spec in OPERATOR_SCHEMA.items():
        module = operator_module(name)
        if spec.nout == 0 and module in _MODULE_CATEGORY:
            expected = NOUT0_MODULES_OUTSIDE_IMPORT_EXPORT.get(
                module, NCExplorerCategory.IMPORT_EXPORT)
            assert _MODULE_CATEGORY[module] is expected, name


def test_the_curated_shortlist_is_a_shortlist():
    """Nine names, because the toolbar shows ten and the category holds 28.

    The old list was the whole of Formatted I/O — eight names that were the
    category rather than a selection from it, and which sorted put both ASCII
    header formats in front of every operator anyone reaches for.
    """
    curated = OPERATOR_CATEGORIES[NCExplorerCategory.IMPORT_EXPORT]
    assert len(curated) == 9
    assert set(curated) <= set(DOCUMENTED)
    # The two imports and the table writer are the section's headline acts.
    assert {"import_binary", "import_cmsaf", "outputtab"} <= set(curated)
    # Every curated name is really in the category it is curated under.
    for name in curated:
        assert OPERATOR_SCHEMA[name].category is NCExplorerCategory.IMPORT_EXPORT


def test_the_whole_category_is_reachable_from_its_menu():
    """Curated plus rest is the category, with nothing offered twice."""
    curated, rest = menu_operators(NCExplorerCategory.IMPORT_EXPORT)
    assert not set(curated) & set(rest)
    assert set(curated) | set(rest) == {
        name for name, spec in OPERATOR_SCHEMA.items()
        if spec.category is NCExplorerCategory.IMPORT_EXPORT}


# --- what each one's syntax is ----------------------------------------------

@pytest.mark.parametrize("operator", DOCUMENTED)
def test_the_arity_matches_the_synopsis(operator):
    """``(nin|nout)`` as the binary's own SYNOPSIS states it."""
    _synopsis, nin, nout = SYNOPSIS[operator]
    spec = OPERATOR_SCHEMA[operator]
    assert (spec.nin, spec.nout) == (nin, nout)


def test_import_binary_takes_no_parameter_because_the_ctl_is_the_input():
    """The bug this section's schema work started from.

    ``cdo import_binary infile.ctl outfile`` is the whole synopsis. Declared
    with a ``ctlfile`` parameter *as well as* its one input, the app built
    ``cdo import_binary,demo.ctl in.nc out.nc``.
    """
    assert OPERATOR_SCHEMA["import_binary"].params == ()


@pytest.mark.parametrize("alias,target", sorted(ALIASES.items()))
def test_an_alias_cannot_disagree_with_its_target(alias, target):
    """Same parameter objects, not merely equal ones.

    ``import_grads`` had no parameter while ``import_binary`` had ``ctlfile``,
    so the same operator asked for a different number of files depending on
    which name was used. Identity rather than equality is what makes a future
    edit to one of them impossible to apply to only one.
    """
    assert OPERATOR_SCHEMA[alias].params is OPERATOR_SCHEMA[target].params


@pytest.mark.parametrize("alias,target", sorted(ALIASES.items()))
def test_an_alias_says_what_it_is_an_alias_for(alias, target):
    """Not the catalog's raw "--> gmtcells", which is notation rather than prose."""
    description = OPERATOR_SCHEMA[alias].description
    assert not description.startswith("-->")
    assert target in description


def test_input_takes_the_optional_zaxis_its_synopsis_brackets():
    """``cdo input,grid[,zaxis] outfile`` — grid required, zaxis not."""
    grid, zaxis = OPERATOR_SCHEMA["input"].params
    assert (grid.name, grid.optional) == ("grid", False)
    assert (zaxis.name, zaxis.optional) == ("zaxis", True)
    assert missing_required_parameters("input", ["r4x2"]) == []
    assert missing_required_parameters("input", [""]) == ["grid"]


def test_outputf_brackets_both_of_its_parameters():
    """``cdo outputf[,format[,nelem]] infiles``. Both were declared required.

    Declared required, the form refused to run the operator until two fields
    were filled, and the second had no documented value to fill it with — the
    binary's own help says "The default for nelem is 1".
    """
    assert [p.optional for p in OPERATOR_SCHEMA["outputf"].params] == [True, True]
    assert missing_required_parameters("outputf", []) == []


# --- outputtab's eighteen keynames ------------------------------------------

def test_the_keyname_list_is_exactly_the_manuals_eighteen():
    """Not seventeen, not nineteen, and in the manual's own order."""
    assert list(_OUTPUTTAB_KEYNAME_CHOICES) == MANUAL_KEYNAMES
    assert len(_OUTPUTTAB_KEYNAME_CHOICES) == 18


def test_keynames_is_a_validated_multiselect_rather_than_a_text_box():
    keynames, = OPERATOR_SCHEMA["outputtab"].params
    assert keynames.kind == "multiselect"
    assert keynames.choices == tuple(MANUAL_KEYNAMES)


@pytest.mark.parametrize("value", [
    "value", "name,date,value", "nohead,value",
    "name:12,value:8",          # the documented :len suffix
    "value:0", "value:-3", "value:8.5",   # measured to be accepted by CDO
    "date,date",                # repeats are legal
])
def test_accepted_keynames_are_accepted(value):
    assert invalid_parameter_values("outputtab", [value]) == []


@pytest.mark.parametrize("value,fragment", [
    ("bogus", "not one of"),
    ("value,bogus", "not one of"),
    ("value:abc", "number after the colon"),
    ("value:", "number after the colon"),
    ("name,,value", "empty entry"),
])
def test_rejected_keynames_are_named_in_the_apps_own_words(value, fragment):
    """Two of these crash CDO rather than failing it.

    ``outputtab,value:abc`` and ``outputtab,value:`` were measured to exit 134
    on an uncaught ``std::invalid_argument`` from ``stoi`` — a signal, not an
    abort, with nothing on stderr a caller can report. That is why the ``:len``
    is checked here and not left to the binary.
    """
    problems = invalid_parameter_values("outputtab", [value])
    assert problems, value
    assert fragment in problems[0]


def test_the_keynames_token_is_the_command_cdo_documents():
    """One comma-joined value, joined again into the operator token."""
    assert parameter_tokens("outputtab", ["date,time,value"]) == ["date,time,value"]
    request = OperatorRequest(operator="outputtab", input_files=("a.nc",),
                              parameters=("date,time,value",), nin=-1, nout=0)
    assert request.command_line() == "cdo outputtab,date,time,value a.nc"


# --- the byte-identity guarantee --------------------------------------------

def test_positional_operators_still_build_byte_identically():
    """The guarantee ``test_file_operations_category.py`` pins, re-checked here.

    Extended with Import/Export cases rather than by editing that test, which
    the brief required to stay green and unchanged.
    """
    unchanged = [
        # the pre-existing cases, unchanged
        ("gtc", ["273.15"]),
        ("expr", ["x=y+1"]),
        ("distgrid", ["2", "3"]),
        # Import/Export: the positional cases this section adds
        ("input", ["r4x2"]),
        ("input", ["r4x2", "surface"]),
        ("outputf", ["%8.4g"]),
        ("outputf", ["%8.4g", "6"]),
        ("outputtab", ["date,value"]),
        ("outputkey", ["date,value"]),
        ("outputvector", ["2"]),
    ]
    for operator, values in unchanged:
        assert parameter_tokens(operator, values) == values, operator


@pytest.mark.parametrize("operator", DOCUMENTED)
def test_no_options_builds_the_argv_it_always_built(operator, tmp_path):
    """Adding an options slot must change the argv of no call that passes one.

    The brief's constraint, asserted directly: for every operator in the
    section, resolving a call with no options produces exactly
    ``[cdo, <token>, *inputs, *outputs]`` — no extra token anywhere.
    """
    integration = NCExplorerIntegration()
    spec = OPERATOR_SCHEMA[operator]

    source = tmp_path / "in.nc"
    source.write_bytes(b"")
    inputs = [str(source)] * (1 if spec.nin in (-1, 1) else 0)
    outputs = [str(tmp_path / "out.nc")] * spec.nout
    parameters = {"input": ["r4x2"], "outputtab": ["value"]}.get(operator, [])

    without = integration._resolve_operator_call(
        operator, inputs, outputs, parameters)
    expected = [integration.NCExplorer_binary,
                operator if not parameters
                else f"{operator},{','.join(parameters)}",
                *inputs, *outputs]
    assert list(without.cmd) == expected

    # And with options, they land between the binary and the operator token —
    # the only place CDO accepts them — leaving everything else alone.
    with_options = integration._resolve_operator_call(
        operator, inputs, outputs, parameters, ["-f", "nc"])
    assert list(with_options.cmd) == [expected[0], "-f", "nc", *expected[1:]]


def test_a_request_with_no_options_or_redirection_renders_as_before():
    """The same guarantee one level up, on ``OperatorRequest``."""
    request = OperatorRequest(operator="gtc", input_files=("a.nc",),
                              output_files=("b.nc",), parameters=("273.15",))
    assert request.arguments() == ["gtc,273.15", "a.nc", "b.nc"]
    assert request.command_line() == "cdo gtc,273.15 a.nc b.nc"


def test_options_and_redirection_render_where_a_shell_would_put_them():
    request = OperatorRequest(
        operator="input", output_files=("out.nc",), parameters=("r4x2",),
        nin=0, nout=1, stdin_file="dump.txt", options=("-f", "nc"))
    assert request.command_line() == "cdo -f nc input,r4x2 out.nc < dump.txt"

    reading = OperatorRequest(operator="gmtxyz", input_files=("a.nc",),
                              nin=1, nout=0, stdout_file="data.gmt")
    assert reading.command_line() == "cdo gmtxyz a.nc > data.gmt"
    # A redirection is not an argument.
    assert reading.arguments() == ["gmtxyz", "a.nc"]


# --- standard input ---------------------------------------------------------

@pytest.mark.parametrize("operator", DOCUMENTED)
def test_only_the_formatted_input_operators_read_stdin(operator):
    """Three of the thirteen, decided by CDO's module rather than by a list."""
    assert reads_stdin(operator) == (operator in
                                     {"input", "inputsrv", "inputext"})


def test_the_extra_rows_cannot_be_mistaken_for_files():
    """The three non-argument rows must not collide with the file scans.

    ``execute_operation`` collects the operator's real files by scanning widget
    labels for "Input File", "Output" and "prefix". A caption containing one of
    those would be silently swept into ``input_files`` or read as the output
    path — a wrong command built from a form that looks right.
    """
    from ncexplorer_toolkit.gui.main_window import (
        CDO_OPTIONS_LABEL, STDIN_FILE_LABEL, STDOUT_FILE_LABEL)

    for label in (STDIN_FILE_LABEL, STDOUT_FILE_LABEL, CDO_OPTIONS_LABEL):
        assert "Input File" not in label
        assert "Output" not in label
        assert "output" not in label.lower()
        assert "prefix" not in label.lower()
        assert "operfix" not in label.lower()


def test_the_import_extensions_are_selectable():
    """A .ctl, .hdf or .h5 could not be picked without switching to All Files.

    The choosers filtered to what the app can *draw*, and the two import
    operators exist to read what it cannot.

    Fixed once by widening one shared filter until it held every extension any
    operator might want — which put ``.ctl`` and ``.h5`` in front of all 943 of
    them, alongside the shapefiles and GeoTIFFs already there. The filter now
    comes from the slot, so this asserts the narrower thing: that these
    extensions reach the operators that read them.
    """
    from ncexplorer_toolkit.core import filetypes as ft
    from ncexplorer_toolkit.core.categories import input_file_kind

    assert "*.ctl" in ft.dialog_filter(input_file_kind("import_binary"))
    assert "*.ctl" in ft.dialog_filter(input_file_kind("import_grads"))
    for extension in (".h5", ".hdf"):
        assert f"*{extension}" in ft.dialog_filter(input_file_kind("import_cmsaf"))


def test_the_import_extensions_are_offered_nowhere_else():
    """And an ordinary operator is not offered them.

    The other half of the pair above, and the reason the shared filter had to
    go: CDO reads GRIB, NetCDF, SERVICE, EXTRA and IEG, so a ``.ctl`` or a
    ``.h5`` in ``timmean``'s chooser is a file the run cannot open. ``sinfo``
    stands for every operator that is not in this section.
    """
    from ncexplorer_toolkit.core import filetypes as ft
    from ncexplorer_toolkit.core.categories import input_file_kind

    for operator in ("timmean", "sinfo", "remapbil", "eca_su"):
        chooser = ft.dialog_filter(input_file_kind(operator))
        for extension in (".ctl", ".h5", ".hdf", ".tif", ".shp", ".geojson",
                          ".kml", ".gpkg"):
            assert f"*{extension}" not in chooser, (operator, extension)


# --- running them -----------------------------------------------------------

@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    """A small field to export, built with CDO itself."""
    if shutil.which("cdo") is None:                     # pragma: no cover
        pytest.skip("needs an installed CDO")
    path = tmp_path_factory.mktemp("import_export") / "sample.nc"
    subprocess.run(["cdo", "-f", "nc", "const,5,r4x2", str(path)],
                   check=True, capture_output=True, stdin=subprocess.DEVNULL)
    return path


@pytest.fixture(scope="module")
def grads(tmp_path_factory):
    """A GrADS .ctl plus the 32-bit IEEE .bin it describes.

    Written here rather than committed, so the fixture states the one format
    constraint the operator has: "Only 32-bit IEEE floats are supported for
    standard binary files".
    """
    directory = tmp_path_factory.mktemp("grads")
    values = [float(v) for v in range(8 * 4)]
    (directory / "demo.bin").write_bytes(
        struct.pack(">" + "f" * len(values), *values))
    (directory / "demo.ctl").write_text(
        "DSET ^demo.bin\n"
        "TITLE demo\n"
        "OPTIONS big_endian\n"
        "UNDEF -9e33\n"
        "XDEF 8 LINEAR 0 45\n"
        "YDEF 4 LINEAR -67.5 45\n"
        "ZDEF 1 LEVELS 1000\n"
        "TDEF 1 LINEAR 00Z01jan2000 1dy\n"
        "VARS 1\n"
        "t 0 99 test field\n"
        "ENDVARS\n"
    )
    return directory / "demo.ctl"


@cdo_required
@pytest.mark.parametrize("operator", [
    "output", "outputf", "outputint", "outputsrv", "outputext",
    "outputtab", "gmtxyz", "gmtcells",
])
def test_the_export_operators_run_and_print(operator, sample, tmp_path):
    """Every operator that needs nothing but a field: exit 0, and output on stdout.

    ``outputtab`` gets keynames because they are required; ``outputf`` gets a
    format because printing with none is not a useful check of anything.
    """
    parameters = {"outputtab": ["date,lon,lat,value"],
                  "outputf": ["%8.4g", "4"]}.get(operator, [])
    integration = NCExplorerIntegration()
    result = integration.execute_operator(
        operator, input_files=[str(sample)], extra_parameters=parameters)

    assert result.success, result.stderr
    assert result.stdout.strip(), f"{operator} printed nothing"


@cdo_required
def test_the_gmt_and_table_operators_produce_their_documented_file(sample,
                                                                   tmp_path):
    """``cdo gmtxyz temp > data.gmt`` is how the manual writes gmtxyz.

    Writing the text to a file is the entire documented purpose of these three,
    and the operator panel had no way to do it.
    """
    from ncexplorer_toolkit.core.session_log import write_stdout_capture

    integration = NCExplorerIntegration()
    for operator, suffix, parameters in (("gmtxyz", ".gmt", []),
                                         ("gmtcells", ".gmt", []),
                                         ("outputtab", ".tab", ["lon,lat,value"])):
        target = tmp_path / f"{operator}{suffix}"
        result = integration.execute_operator(
            operator, input_files=[str(sample)], extra_parameters=parameters)
        assert result.success, result.stderr

        request = OperatorRequest(operator=operator, input_files=(str(sample),),
                                  nin=1, nout=0, stdout_file=str(target))
        assert write_stdout_capture(request, result.stdout)
        assert target.read_text() == result.stdout
        assert target.stat().st_size > 0


@cdo_required
def test_import_binary_runs_from_the_ctl_alone(grads, tmp_path):
    """Two file tokens, and ``-f nc`` to get the format the filename claims."""
    integration = NCExplorerIntegration()
    target = tmp_path / "imported.nc"
    result = integration.execute_operator(
        "import_binary", input_files=[str(grads)], output_files=[str(target)],
        options=["-f", "nc"])

    assert result.success, result.stderr
    assert target.is_file()

    fmt = subprocess.run(["cdo", "showformat", str(target)],
                         capture_output=True, text=True,
                         stdin=subprocess.DEVNULL)
    assert "NetCDF" in fmt.stdout


@cdo_required
def test_without_f_nc_import_binary_writes_grib_whatever_the_file_is_called(
        grads, tmp_path):
    """The measurement the pre-run warning is built on.

    Exit 0, a file called ``.nc``, and GRIB inside it — so the user loses both
    the format they named and, at 16-bit packing, most of their precision.
    """
    integration = NCExplorerIntegration()
    target = tmp_path / "misleading.nc"
    result = integration.execute_operator(
        "import_binary", input_files=[str(grads)], output_files=[str(target)])

    assert result.success
    fmt = subprocess.run(["cdo", "showformat", str(target)],
                         capture_output=True, text=True,
                         stdin=subprocess.DEVNULL)
    assert "GRIB" in fmt.stdout
    assert "NetCDF" not in fmt.stdout


@cdo_required
def test_a_ctl_in_a_path_with_a_space_still_finds_its_binary(grads, tmp_path):
    """The alias bug, and the reason a .ctl is aliased by its directory.

    ``DSET ^demo.bin`` is resolved against the lexical directory of the path CDO
    was handed, so symlinking the descriptor alone left its data behind:
    "Could not open file: <alias dir>/demo.bin".
    """
    spaced = tmp_path / "with space"
    spaced.mkdir()
    shutil.copy2(grads, spaced / "demo.ctl")
    shutil.copy2(grads.parent / "demo.bin", spaced / "demo.bin")

    integration = NCExplorerIntegration()
    target = tmp_path / "from_spaced.nc"
    result = integration.execute_operator(
        "import_binary", input_files=[str(spaced / "demo.ctl")],
        output_files=[str(target)], options=["-f", "nc"])

    assert result.success, result.stderr
    assert target.is_file()


@cdo_required
def test_the_alias_of_a_ctl_keeps_its_siblings_reachable(tmp_path):
    """The mechanism, separately from the run: a directory alias, not a file one."""
    integration = NCExplorerIntegration()
    spaced = tmp_path / "with space"
    spaced.mkdir()
    (spaced / "demo.ctl").write_text("DSET ^demo.bin\n")
    (spaced / "demo.bin").write_bytes(b"\x00" * 16)

    alias = integration._create_input_alias(str(spaced / "demo.ctl"))
    assert alias != str(spaced / "demo.ctl")
    assert os.path.exists(alias)
    # The sibling the descriptor names resolves next to the alias.
    assert os.path.exists(os.path.join(os.path.dirname(alias), "demo.bin"))


@cdo_required
@pytest.mark.parametrize("operator", ["input", "inputsrv", "inputext"])
def test_the_stdin_operators_fail_fast_instead_of_hanging(operator, tmp_path):
    """No data and a closed stdin is an immediate, reportable failure.

    Before this, neither execution path closed stdin, so all three waited for a
    terminal until the run timeout. The assertion that matters is that the call
    *returns* — a regression would hang the test rather than fail it, so the
    timing is asserted too.
    """
    integration = NCExplorerIntegration()
    parameters = ["r4x2"] if operator == "input" else []
    result = integration.execute_operator(
        operator, output_files=[str(tmp_path / f"{operator}.nc")],
        extra_parameters=parameters, options=["-f", "nc"])

    assert not result.success
    assert result.execution_time < 10
    assert "input elements" in (result.stderr or "").lower() or result.stderr


@cdo_required
def test_input_reads_the_file_it_is_fed(sample, tmp_path):
    """``cdo output`` and ``cdo input`` round-trip through the stdin slot.

    This is the whole point of giving the three operators a data file: the
    numbers ``output`` prints are, in CDO's own words, "exactly that ones which
    are written out by the output operator".
    """
    integration = NCExplorerIntegration()

    printed = integration.execute_operator("output", input_files=[str(sample)])
    assert printed.success
    dump = tmp_path / "dump.txt"
    dump.write_text(printed.stdout)

    target = tmp_path / "roundtrip.nc"
    result = integration.execute_operator(
        "input", output_files=[str(target)], extra_parameters=["r4x2"],
        options=["-f", "nc"], stdin_path=str(dump))
    assert result.success, result.stderr
    assert target.is_file()

    # And the values survived the trip.
    back = integration.execute_operator("output", input_files=[str(target)])
    assert back.stdout.split() == printed.stdout.split()


@cdo_required
def test_the_async_path_closes_stdin_too(tmp_path):
    """The QProcess path, which is the one the window actually uses.

    ``QProcess`` holds the write end of the child's stdin open until told
    otherwise and nothing ever wrote to it, so a stdin-reading operator waited
    on a pipe that would never deliver and never close. Fixing only the blocking
    path would have moved the hang rather than removed it — the operator panel
    runs everything through here.

    A regression makes this test *time out* rather than fail, which is why the
    event loop is given a deadline of its own and the assertion is that the run
    settled at all.
    """
    from PyQt6.QtCore import QCoreApplication, QTimer

    application = QCoreApplication.instance() or QCoreApplication([])
    integration = NCExplorerIntegration()
    settled = {}

    worker = integration.execute_operator_async(
        "input", output_files=[str(tmp_path / "async.nc")],
        extra_parameters=["r4x2"], options=["-f", "nc"], timeout=20)
    worker.finished.connect(lambda result: settled.setdefault("ok", result))
    worker.failed.connect(lambda message: settled.setdefault("failed", message))

    deadline = QTimer()
    deadline.setSingleShot(True)
    deadline.timeout.connect(application.quit)
    deadline.start(15000)

    poll = QTimer()
    poll.timeout.connect(lambda: settled and application.quit())
    poll.start(50)
    application.exec()

    assert settled, "the async run never settled — stdin was left open"
    # No data was fed, so CDO's own complaint is the right outcome.
    result = settled.get("ok")
    assert result is not None and not result.success
    assert "input elements" in (result.stderr or "").lower()


@cdo_required
def test_import_cmsaf_reports_its_own_requirement(tmp_path):
    """It needs an HDF5 build; the app now says so before the run.

    The operator cannot be exercised without a CM-SAF file, which is why the
    requirement is stated on the operator rather than discovered from a failed
    run. Asserted on the description, which is what all three surfaces show.
    """
    description = OPERATOR_SCHEMA["import_cmsaf"].description
    assert "HDF5" in description
    assert "PROJ" in description


def test_import_binary_states_the_32_bit_limit_and_the_format_trap():
    """Both caveats reach every surface, because all three show the description."""
    description = OPERATOR_SCHEMA["import_binary"].description
    assert "32-bit IEEE" in description
    assert "-f nc" in description
    # And the alias says the same thing, because it is the same operator.
    assert OPERATOR_SCHEMA["import_grads"].description.endswith(
        description.split(". ", 1)[1])
