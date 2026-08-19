# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""The File operation section is what CDO says it is, and spells its parameters
the way the installed binary parses them.

Four separate problems, in the same seventeen modules.

**Where they live.** ``_MODULE_CATEGORY`` named no File operation module at all,
so the whole section was placed by the prefix cascade in ``_infer_category``,
whose only file-operation branch tested a ``split`` prefix and a six-name list.
Twelve operators landed in three wrong categories: ``setchunkspec`` and
``setfilter`` under Modification on their ``set`` prefix, ``mergegrid`` under
Statistical values on the ``mer`` *meridional* prefix, and ``clone``, ``tee``,
``pack``, ``unpack``, ``duplicate``, ``bitrounding``, ``distgrid``, ``collgrid``
and ``szip`` under Miscellaneous by falling off the end. ``mergegrid`` is the
one that settles the argument: ``mermean`` is a meridional statistic and
``mergegrid`` is a file operation, they differ by two letters, and no rule over
names can separate them. Name the module, do not patch the cascade.

**Whether the parameters were reachable.** Fourteen of the seventeen modules
declared no parameters, so ``bitrounding``'s eight, ``collgrid``'s four,
``pack``'s two and the rest reached the GUI as no fields at all.

**How they are spelled.** CDO uses three grammars here and the manual does not
say which is which, so every form below was measured. The three places the
2.6.3 binary contradicts its own documentation are asserted directly, because
each is a silent-wrong-answer rather than a failed command:

* the BOOL parameters are keywords taking ``true``/``false``, not bare flags —
  ``cdo pack,printparam`` is "missing '=' in key/value string";
* ``format`` belongs to ``splitmon`` alone, not to the six operators on
  Splittime's Name line, and it is positional — ``splitmon,format=%B`` exits 0
  and writes ``monformat=January.nc``;
* ``swap`` is the section's one true flag, spelled bare.

**What a run touches.** ``tee`` writes a second file through a *parameter*, so
``(nin, nout)`` understates it: the path was aliased as an input, never moved
back from its alias, and left behind by a failed run.

Everything asserted here was measured against the installed CDO 2.6.3, not read
off the documentation; the tests marked ``cdo_required`` re-measure it.
"""

import shutil
import subprocess

import pytest

from ncexplorer_toolkit.core.categories import (
    APPENDING_OPERATORS, NCExplorerCategory, OPERATOR_CATEGORIES,
    OPERATOR_SCHEMA, file_parameter_indexes, invalid_parameter_values,
    menu_operators, missing_required_parameters, operator_inputs,
    operator_module, operator_syntax, output_parameter_indexes,
    parameter_tokens,
)

cdo_required = pytest.mark.skipif(
    shutil.which("cdo") is None, reason="needs an installed CDO")


#: The CDO 2.6.3 File operation section, module by module, exactly as the
#: "Here is a short overview of all operators in this section" table lists it.
#: Written out rather than derived from the schema, because a test that asks the
#: schema what the schema contains asserts nothing.
FILE_OPERATIONS_BY_MODULE = {
    "Copy datasets":                   "copy clone cat",
    "Duplicate a data stream and write it to file": "tee",
    "Pack data":                       "pack",
    "Unpack data":                     "unpack",
    "Specify chunking":                "setchunkspec",
    "Specify filter":                  "setfilter",
    "Bit rounding":                    "bitrounding",
    "Replace variables":               "replace",
    "Duplicates a dataset":            "duplicate",
    "Merge grid":                      "mergegrid",
    "Merge datasets":                  "merge mergetime",
    "Split a dataset":                 "splitcode splitparam splitname splitlevel "
                                       "splitgrid splitzaxis splittabnum splitensemble",
    "Split timesteps of a dataset":    "splithour splitday splitseas splityear "
                                       "splityearmon splitmon",
    "Split selected timesteps":        "splitsel",
    "Splits a file into dates":        "splitdate",
    "Distribute horizontal grid":      "distgrid",
    "Collect horizontal grid":         "collgrid",
}

#: The thirty-two operators the 2.6.3 manual documents for this section.
DOCUMENTED = sorted(
    name for names in FILE_OPERATIONS_BY_MODULE.values() for name in names.split()
)

#: The five the binary carries and the manual does not, each placed
#: deliberately. Four are placed by their module like everything else; ``ncopy``
#: is the one CDO will not place, and it stays in Miscellaneous for the same
#: reason ``harmonic`` and ``lic`` do — see the note above ``_MODULE_CATEGORY``.
UNDOCUMENTED = {
    "szip": NCExplorerCategory.FILE_OPERATIONS,
    "splitvar": NCExplorerCategory.FILE_OPERATIONS,
    "splitrec": NCExplorerCategory.FILE_OPERATIONS,
    "splitdatetime": NCExplorerCategory.FILE_OPERATIONS,
    "ncopy": NCExplorerCategory.MISCELLANEOUS,
}

#: The nine Split-module operators that take ``swap`` and ``uuid``. ``splitrec``
#: is in the module and takes neither: ``cdo splitrec,swap`` is "Too many
#: arguments! Need 0 found 1."
SPLIT_MODULE = ("splitcode splitparam splitname splitlevel splitgrid "
                "splitzaxis splittabnum splitensemble splitvar").split()

#: The six Splittime operators. Only ``splitmon`` takes ``format``.
SPLITTIME = "splithour splitday splitseas splityear splityearmon splitmon".split()


# --- where they live --------------------------------------------------------

def test_the_section_is_thirty_two_operators():
    assert len(DOCUMENTED) == 32


@pytest.mark.parametrize("operator", DOCUMENTED)
def test_every_documented_operator_is_filed_under_file_operations(operator):
    spec = OPERATOR_SCHEMA[operator]
    assert spec.category is NCExplorerCategory.FILE_OPERATIONS


@pytest.mark.parametrize("operator,category", sorted(UNDOCUMENTED.items()))
def test_the_undocumented_five_are_placed_deliberately(operator, category):
    """Each of the five is where the *binary* puts it, not where its name does.

    ``ncopy`` is the whole point of this test. It reads as "NetCDF copy" and
    probably is one, but ``cdo --operators`` gives it no description, ``cdo -h
    ncopy`` answers "No help available for this operator!", and it has no
    module — so placing it in File operations would be a guess, which is
    precisely what naming modules exists to stop.
    """
    assert OPERATOR_SCHEMA[operator].category is category


def test_ncopy_is_the_only_one_the_binary_will_not_place():
    from ncexplorer_toolkit.core.nc_operator_catalog import CDO_OPERATOR_MODULES

    assert CDO_OPERATOR_MODULES.get("ncopy", "") == ""
    for operator in set(UNDOCUMENTED) - {"ncopy"}:
        assert CDO_OPERATOR_MODULES.get(operator, "") != ""


@pytest.mark.parametrize("module,names", sorted(FILE_OPERATIONS_BY_MODULE.items()))
def test_module_membership_matches_the_catalog(module, names):
    for name in names.split():
        assert operator_module(name) == module


@pytest.mark.parametrize("operator", ["setchunkspec", "setfilter", "mergegrid"])
def test_the_operators_a_prefix_stole_are_back(operator):
    """The three the cascade actively misfiled, rather than merely missed.

    ``setchunkspec``/``setfilter`` went to Modification on ``op.startswith("set")``
    and ``mergegrid`` to Statistical values on the ``mer`` prefix — which is the
    meridional-statistics prefix, shared with ``mermean`` and ``merstd``.
    """
    assert OPERATOR_SCHEMA[operator].category is NCExplorerCategory.FILE_OPERATIONS


def test_browsing_reaches_the_whole_section():
    """Everything in the category is reachable from the menu, curated or not."""
    curated, rest = menu_operators(NCExplorerCategory.FILE_OPERATIONS)
    reachable = set(curated) | set(rest)
    assert set(DOCUMENTED) <= reachable


def test_the_shortlist_is_ten_and_is_what_the_toolbar_shows():
    """A curated list longer than TOP_LEVEL_LIMIT is not a shortlist.

    The sixteen names that used to be here sorted to ``cat copy merge mergetime
    replace splitcode splitday splitgrid splithour splitlevel`` — six of the
    fifteen splits, and neither ``splitname`` nor ``splitmon`` nor ``splitsel``.
    """
    curated = OPERATOR_CATEGORIES[NCExplorerCategory.FILE_OPERATIONS]
    assert len(curated) == 10
    assert len(set(curated)) == 10
    for name in curated:
        assert OPERATOR_SCHEMA[name].category is NCExplorerCategory.FILE_OPERATIONS


# --- the parameters ---------------------------------------------------------

#: Every parameter the 2.6.3 manual documents for this section, as
#: ``operator -> ((name, kind, optional, form), …)`` in declaration order.
#: Quoted from the manual, then confirmed one at a time against ``cdo -h`` and
#: an actual run — which is where the three disagreements below come from.
EXPECTED_PARAMS = {
    "copy": (),
    "clone": (),
    "cat": (),
    "unpack": (),
    "replace": (),
    "mergegrid": (),
    "merge": (),
    "splitdate": (),
    "splitrec": (),
    "tee": (("outfile2", "file", False, "positional"),),
    "duplicate": (("ndup", "int", True, "positional"),),
    "pack": (
        ("printparam", "bool", True, "keyword"),
        ("filename", "file", True, "keyword"),
    ),
    "setchunkspec": (("filename", "file", False, "keyword"),),
    "setfilter": (("filename", "file", False, "keyword"),),
    "bitrounding": (
        ("inflevel", "float", True, "keyword"),
        ("addbits", "int", True, "keyword"),
        ("minbits", "int", True, "keyword"),
        ("maxbits", "int", True, "keyword"),
        ("numsteps", "int", True, "keyword"),
        ("numbits", "int", True, "keyword"),
        ("printbits", "bool", True, "keyword"),
        ("filename", "file", True, "keyword"),
    ),
    "mergetime": (
        ("skip_same_time", "bool", True, "keyword"),
        ("names", "select", True, "keyword"),
    ),
    "splitsel": (
        ("nsets", "int", False, "positional"),
        ("noffset", "int", True, "positional"),
        ("nskip", "int", True, "positional"),
    ),
    "distgrid": (
        ("nx", "int", False, "positional"),
        ("ny", "int", True, "positional"),
    ),
    "collgrid": (
        ("nx", "int", True, "keyword"),
        ("name", "string", True, "keyword"),
        ("levidx", "string", True, "keyword"),
        ("gridtype", "select", True, "keyword"),
    ),
    "splitmon": (("format", "string", True, "positional"),),
}


@pytest.mark.parametrize("operator,expected", sorted(EXPECTED_PARAMS.items()))
def test_declared_parameters_match_the_manual(operator, expected):
    spec = OPERATOR_SCHEMA[operator]
    actual = tuple((p.name, p.kind, p.optional, p.form) for p in spec.params)
    assert actual == expected


@pytest.mark.parametrize("operator", SPLIT_MODULE)
def test_the_split_module_declares_swap_and_uuid(operator):
    spec = OPERATOR_SCHEMA[operator]
    assert tuple((p.name, p.kind, p.optional, p.form) for p in spec.params) == (
        ("swap", "bool", True, "flag"),
        ("uuid", "string", True, "keyword"),
    )


def test_splitrec_shares_the_module_and_takes_no_parameters():
    """Sharing a module is not evidence; the binary is.

    ``cdo splitrec,swap infile obase`` is "Too many arguments! Need 0 found 1."
    """
    assert operator_module("splitrec") == "Split a dataset"
    assert OPERATOR_SCHEMA["splitrec"].params == ()


@pytest.mark.parametrize("operator", [n for n in SPLITTIME if n != "splitmon"])
def test_format_belongs_to_splitmon_alone(operator):
    """Splittime's Name line lists six operators; one of them takes ``format``.

    ``cdo splithour,%Y infile obase`` and the other four answer "Too many
    arguments! Need 0 found 1." on 2.6.3. The manual's own prose agrees — the
    sentence about ``format`` sits under splitmon — and only the Name line
    suggested otherwise.
    """
    assert OPERATOR_SCHEMA[operator].params == ()


def test_splittabnum_no_longer_declares_a_parameter_cdo_does_not_have():
    """``tabnums`` appears in no 2.6.3 documentation, and the binary refuses it.

    ``cdo splittabnum,5 infile obase`` is "Unknown parameter: >5<". It was a
    field that could only ever break the run it was filled into.
    """
    assert [p.name for p in OPERATOR_SCHEMA["splittabnum"].params] == ["swap", "uuid"]


def test_distgrid_ny_is_optional():
    """The manual gives ``ny`` "[default: 1]", and ``cdo distgrid,2`` runs.

    Declared required, it made the model builder and the operator form both
    refuse a call CDO accepts.
    """
    assert missing_required_parameters("distgrid", ["2"]) == []
    assert missing_required_parameters("distgrid", [""]) == ["nx"]


@pytest.mark.parametrize("operator,label", [
    ("setchunkspec", "chunk specification file"),
    ("setfilter", "filter specification file"),
])
def test_the_two_required_filenames_are_required(operator, label):
    """Their synopsis is ``<op>,parameter``, not ``<op>[,parameter]``.

    Reported by ``label`` rather than by ``name``, which is the documented
    contract: the caller names the *field* the user left empty, and every one
    of these operators calls its parameter ``filename``.
    """
    assert missing_required_parameters(operator, []) == [label]
    assert missing_required_parameters(operator, ["spec.txt"]) == []


# --- how they are spelled ---------------------------------------------------

def test_positional_operators_build_byte_identically():
    """The one line that builds every operator token in the app changed.

    ``parameter_tokens`` replaced ``','.join(parameters)`` in
    ``_resolve_operator_call``. For every operator whose parameters are all
    positional — which is every operator outside this section — it must return
    the values unchanged, or the comparison, arithmetic, selection and ECA
    sections all silently change what they run.
    """
    unchanged = [
        ("gtc", ["273.15"]),
        ("expr", ["x=y+1"]),
        ("distgrid", ["2", "3"]),
        ("splitsel", ["10", "0", "0"]),
        ("eca_cwdi", ["6", "5"]),
        ("addc", ["-273.15"]),
        ("selname", ["tas,pr"]),
        ("ifthenc", ["5"]),
        ("splitmon", ["%B"]),
        ("duplicate", ["3"]),
        ("tee", ["/tmp/second.nc"]),
    ]
    for operator, values in unchanged:
        assert parameter_tokens(operator, values) == values, operator


@pytest.mark.parametrize("operator,values,expected", [
    # keyword: name=value, and a blank one is simply absent rather than
    # holding a comma open
    ("bitrounding", ["0.999", "", "", "", "", "", "", ""], ["inflevel=0.999"]),
    ("bitrounding", ["", "", "", "", "", "", "true", ""], ["printbits=true"]),
    ("bitrounding", ["0.999", "2", "", "", "", "", "", ""],
     ["inflevel=0.999", "addbits=2"]),
    ("pack", ["true", ""], ["printparam=true"]),
    ("pack", ["", "/tmp/p.txt"], ["filename=/tmp/p.txt"]),
    ("mergetime", ["1", "union"], ["skip_same_time=true", "names=union"]),
    ("collgrid", ["3", "", "", "unstructured"],
     ["nx=3", "gridtype=unstructured"]),
    # flag: the bare name when set, nothing at all when not
    ("splitname", ["true", ""], ["swap"]),
    ("splitname", ["false", ""], []),
    ("splitname", ["", ""], []),
    ("splitname", ["true", "myattr"], ["swap", "uuid=myattr"]),
    ("splitname", ["", "myattr"], ["uuid=myattr"]),
])
def test_keyword_and_flag_parameters_build_the_argv_cdo_documents(
        operator, values, expected):
    assert parameter_tokens(operator, values) == expected


@pytest.mark.parametrize("spelling", ["yes", "on", "1", "TRUE"])
def test_a_bool_is_normalised_to_what_cdo_accepts(spelling):
    """CDO's own parser is narrower than the schema's, deliberately.

    ``cdo pack,printparam=yes`` is "Boolean parameter >yes< contains invalid
    characters!". A surface may hand over any of these spellings and still
    produce a command that runs.
    """
    assert parameter_tokens("pack", [spelling, ""]) == ["printparam=true"]


def test_an_unreadable_bool_is_refused_before_it_reaches_argv():
    """A checkbox cannot produce "maybe"; a saved project or a batch CSV can."""
    problems = invalid_parameter_values("pack", ["maybe", ""])
    # Labelled, like every other complaint these three checkers return, so the
    # message names the field on screen rather than the CDO parameter behind it.
    assert problems == [
        "print pack parameters must be one of "
        "0, 1, false, no, off, on, true, yes, not 'maybe'"]
    assert invalid_parameter_values("pack", ["true", ""]) == []


@pytest.mark.parametrize("operator,values", [
    ("bitrounding", ["0.999", "", "", "", "", "", "true", ""]),
    ("pack", ["true", ""]),
    ("splitname", ["true", "myattr"]),
    ("mergetime", ["1", "union"]),
    ("splitmon", ["%B"]),
    ("gtc", ["273.15"]),
    ("distgrid", ["2", "3"]),
    ("timmean", []),
    ("sellonlatbox", ["0", "30", "-10", "10"]),
])
def test_the_exported_command_is_the_command_that_ran(operator, values):
    """``session_log.operator_token`` and ``parameter_tokens`` cannot disagree.

    Everything ``core/session_log.py`` renders claims to be the command that
    ran: the session log, the exported shell script, the Python and CDO-chain
    exporters, and the model builder's preview. It used to build the token with
    its own ``','.join`` — a second copy of the rule — which for a keyword
    operator would have exported ``cdo bitrounding,0.999``. That is not a
    different spelling of what ran, it is a parse error, so the script would not
    have run at all.
    """
    from ncexplorer_toolkit.core.session_log import operator_token

    rendered = parameter_tokens(operator, values)
    expected = operator if not rendered else f"{operator},{','.join(rendered)}"
    assert operator_token(operator, values) == expected


@pytest.mark.parametrize("operator,expected", [
    ("bitrounding", "ifile ofile [,inflevel=<float>][,addbits=<int>]"
                    "[,minbits=<int>][,maxbits=<int>][,numsteps=<int>]"
                    "[,numbits=<int>][,printbits=true][,filename=<file>]"),
    ("splitname", "ifile obase [,swap][,uuid=<string>]"),
    ("splitmon", "ifile obase [,format]"),
    ("setchunkspec", "ifile ofile filename=<file>"),
    ("distgrid", "ifile obase nx[,ny]"),
    ("tee", "ifile ofile outfile2"),
])
def test_the_usage_line_shows_the_real_spelling(operator, expected):
    """A usage line naming a keyword without its ``=`` teaches the wrong call.

    ``bitrounding,0.999`` is a parse error on 2.6.3; ``bitrounding,inflevel=0.999``
    is the command.
    """
    assert operator_syntax(operator) == expected


# --- what a run touches -----------------------------------------------------

def test_tees_second_file_is_declared_an_output():
    """``(nin, nout)`` says (1, 1); the run writes two files.

    Everything the execution layer does about outputs follows ``writes``, not
    ``nout``: the alias is an output alias, the file is moved back to the path
    the user asked for, and a run that did not finish removes it.
    """
    spec = OPERATOR_SCHEMA["tee"]
    assert (spec.nin, spec.nout) == (1, 1)
    assert output_parameter_indexes("tee") == (0,)
    assert file_parameter_indexes("tee") == (0,)
    param = spec.params[0]
    assert param.writes is True
    assert param.reads is False


def test_only_the_two_operators_that_write_a_parameter_declare_it():
    """``writes`` is not the negation of ``reads``.

    The ``setpartab*`` family is ``reads=False`` because its value may be a
    built-in table *name* — neither read as a path nor written to.
    """
    writers = {name for name in OPERATOR_SCHEMA
               if output_parameter_indexes(name)}
    assert writers == {"tee", "writeremapscrip"}


def test_cat_is_the_only_appending_operator_and_says_so():
    assert APPENDING_OPERATORS == frozenset({"cat"})
    description = OPERATOR_SCHEMA["cat"].description.lower()
    assert "append" in description
    for other in ("copy", "clone", "merge"):
        assert "appends to outfile" not in OPERATOR_SCHEMA[other].description.lower()


@pytest.mark.parametrize("operator", [
    "merge", "mergetime", "collgrid", "splitname", "splitmon", "distgrid",
])
def test_the_open_file_limit_is_stated_before_the_run(operator):
    """CDO's own note says the limit "depends on the operating system".

    True, and unactionable. These say which direction the operator is exposed
    in and what this machine's limit actually is.
    """
    description = OPERATOR_SCHEMA[operator].description
    assert "simultaneously" in description
    assert "ulimit -n" in description


@pytest.mark.parametrize("operator", [
    "copy", "clone", "cat", "pack", "unpack", "bitrounding",
    "setchunkspec", "setfilter",
])
def test_operators_needing_a_global_option_say_the_forms_have_no_field(operator):
    """Scoped out, and said so on all three surfaces rather than left silent.

    The execution layer takes ``options=``; the graphical surfaces have nowhere
    to set one. Shipping ``copy`` and ``bitrounding`` without saying that would
    be shipping them as complete when they are not.
    """
    description = OPERATOR_SCHEMA[operator].description
    assert "global option" in description
    assert "no field" in description


# --- the multi-file inputs --------------------------------------------------

@pytest.mark.parametrize("operator", [
    "replace", "mergegrid", "merge", "mergetime", "collgrid",
])
def test_the_second_file_says_what_it_must_hold(operator):
    """``nin`` counts files and says nothing about the relationship between them.

    For all five the relationship *is* the operator, and getting it wrong is not
    always an error: CDO's complaint about a bad ``replace`` names a variable,
    not the file that was wrong.
    """
    slots = operator_inputs(operator)
    assert slots
    for slot in slots:
        assert slot.role and not slot.role.startswith("Input ")
        assert len(slot.field) > 40


def test_collgrids_recipe_is_the_distgrid_that_built_its_inputs():
    """The one exact recipe in the file rather than an advisory one."""
    slot = operator_inputs("collgrid")[0]
    assert slot.recipe.startswith("distgrid,")
    assert "{in1}" in slot.recipe


def test_merge_and_mergetime_point_at_each_other():
    """The two operators users confuse, so each names the other's case."""
    assert "mergetime" in operator_inputs("merge")[0].field
    assert "merge" in operator_inputs("mergetime")[0].field


# --- the claims above, re-measured against the installed binary --------------

def installed_cdo_version() -> str:
    out = subprocess.run(["cdo", "--version"], capture_output=True, text=True)
    text = (out.stdout or "") + (out.stderr or "")
    for token in text.split():
        if token[:1].isdigit() and token.count(".") == 2:
            return token
    return ""


@cdo_required
def test_every_declared_parameter_name_is_one_the_binary_knows():
    """``cdo -h <operator>`` is the authority, and it is re-read here.

    This is what caught ``tabnums``: it was declared, it is in no 2.6.3
    documentation, and the binary answers a value for it with "Unknown
    parameter".
    """
    undocumented_by_cdo = {
        # ``cdo -h`` for these prints the module page, which documents the
        # module's parameters under the *documented* operators' names.
        "splitvar", "splitrec", "splitdatetime", "szip",
    }
    for operator, expected in sorted(EXPECTED_PARAMS.items()):
        if not expected or operator in undocumented_by_cdo:
            continue
        out = subprocess.run(["cdo", "-h", operator],
                             capture_output=True, text=True)
        help_text = (out.stdout or "") + (out.stderr or "")
        for name, _kind, _optional, _form in expected:
            assert name in help_text, f"{operator}: {name} not in `cdo -h`"


@cdo_required
def test_the_bool_parameters_really_are_keywords_on_this_build(tmp_path):
    """The measurement the ``bool`` kind exists for.

    The manual types these BOOL, which reads as a bare switch. This build
    refuses a bare key outright — so rendering them as flags, which is what the
    plan for this work assumed, would have produced a command that cannot run.
    """
    infile = tmp_path / "in.nc"
    subprocess.run(
        ["cdo", "-O", "-f", "nc", "-settaxis,2000-01-01,00:00:00,1day",
         "-duplicate,4", "-random,r6x3,1", str(infile)],
        capture_output=True, text=True, check=True)

    bare = subprocess.run(
        ["cdo", "pack,printparam", str(infile), str(tmp_path / "bare.nc")],
        capture_output=True, text=True)
    assert bare.returncode != 0
    assert "missing '='" in (bare.stdout + bare.stderr)

    keyword = subprocess.run(
        ["cdo", "pack,printparam=true", str(infile), str(tmp_path / "kw.nc")],
        capture_output=True, text=True)
    assert keyword.returncode == 0
    # …and it prints its answer, which is why _STDOUT_NOTICES matches it.
    assert "add_offset" in keyword.stdout


@cdo_required
def test_splitmons_format_is_positional_and_the_keyword_form_leaks(tmp_path):
    """Both spellings exit 0. Only one of them names the files correctly.

    This is the case that makes ``form`` worth declaring rather than assuming:
    the wrong form here is not a failed command, it is twelve files called
    ``monformat=January.nc``.
    """
    infile = tmp_path / "in.nc"
    subprocess.run(
        ["cdo", "-O", "-f", "nc", "-settaxis,2000-01-01,00:00:00,1month",
         "-duplicate,3", "-random,r6x3,1", str(infile)],
        capture_output=True, text=True, check=True)

    positional = tmp_path / "pos"
    subprocess.run(["cdo", "splitmon,%B", str(infile), str(positional)],
                   capture_output=True, text=True, check=True)
    assert (tmp_path / "posJanuary.nc").is_file()

    keyword = tmp_path / "kw"
    subprocess.run(["cdo", "splitmon,format=%B", str(infile), str(keyword)],
                   capture_output=True, text=True, check=True)
    assert (tmp_path / "kwformat=January.nc").is_file()


@cdo_required
def test_swap_is_a_bare_flag_and_does_what_it_says(tmp_path, monkeypatch):
    """``splitname,swap`` writes ``<xxx><obase>``, so obase must be relative."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(
        ["cdo", "-O", "-f", "nc", "-settaxis,2000-01-01,00:00:00,1day",
         "-duplicate,4", "-random,r6x3,1", "in.nc"],
        capture_output=True, text=True, check=True)

    subprocess.run(["cdo", "splitname", "in.nc", "plain_"],
                   capture_output=True, text=True, check=True)
    subprocess.run(["cdo", "splitname,swap", "in.nc", "_swapped"],
                   capture_output=True, text=True, check=True)

    assert (tmp_path / "plain_random.nc").is_file()
    assert (tmp_path / "random_swapped.nc").is_file()


@cdo_required
@pytest.mark.parametrize("operator,extra", [
    ("copy", []), ("clone", []), ("cat", []), ("szip", []),
    ("pack", []), ("unpack", []), ("bitrounding", []),
    ("duplicate", []), ("mergetime", []), ("splitname", []),
    ("splitmon", []), ("splitdate", []), ("splitrec", []),
    ("distgrid", ["2"]), ("splitsel", ["2"]),
])
def test_the_whole_section_exits_zero_on_success(tmp_path, operator, extra):
    """Phase 4g's deliverable: the conclusion, written down as a test.

    ``_determine_command_success`` special-cases the Diff module, ``nout == 0``
    and the check/verify family. Nothing in this section needs a case, and this
    is the evidence — so a later change that adds one has to explain itself
    against a measurement rather than a guess. ``yseasgt`` exits 1 and must keep
    being reported as the failure it is.
    """
    infile = tmp_path / "in.nc"
    subprocess.run(
        ["cdo", "-O", "-f", "nc", "-settaxis,2000-01-01,00:00:00,1month",
         "-duplicate,6", "-random,r6x3,1", str(infile)],
        capture_output=True, text=True, check=True)

    spec = OPERATOR_SCHEMA[operator]
    token = operator if not extra else f"{operator},{','.join(extra)}"
    target = str(tmp_path / ("obase_" if spec.nout == -1 else "out.nc"))
    inputs = [str(infile)] * (2 if spec.nin == 2 else 1)

    done = subprocess.run(["cdo", token, *inputs, target],
                          capture_output=True, text=True)
    assert done.returncode == 0, (
        f"{operator} exited {done.returncode}: {done.stdout}{done.stderr}")
