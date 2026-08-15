"""Conditional selection: say which file is the mask, and mean it.

Three problems, all the same family.

``nin`` said ``ifthen`` takes two files and nothing said which was which. All
five ``if*`` operators had no declared inputs, so the model builder printed no
slot rows and the operator form folded both files into one unlabelled two-row
widget. Getting them backwards is not an error — it is a clean exit 0 and a
wrong file, which is the failure this whole file exists to make impossible to
walk into unwarned.

``reducegrid`` was filed under Miscellaneous, because its module title was not
in ``_MODULE_CATEGORY`` and its name does not start with "if". CDO documents it
under Conditional selection.

And a file-valued *parameter* was not treated as a file: never aliased, so a
mask path with a space in it produced a command CDO could not parse, and never
converted properly for WSL, so ``reducegrid,C:\\...\\mask.nc`` reached WSL still
holding a Windows path.

Everything asserted here was measured against the installed CDO 2.6.3, not read
off the documentation; the tests marked ``cdo_required`` re-measure it.
"""

import shutil
import subprocess

import pytest

from ncexplorer_toolkit.core.categories import (
    NCExplorerCategory, OPERATOR_CATEGORIES, OPERATOR_SCHEMA,
    file_parameter_indexes, missing_parameter_files, operator_inputs,
    operator_module,
)
from ncexplorer_toolkit.core.nc_integration import NCExplorerIntegration

cdo_required = pytest.mark.skipif(
    shutil.which("cdo") is None, reason="needs an installed CDO")


#: The six operators CDO files under Conditional selection, by module. Written
#: out rather than derived from the schema, because a test that asks the schema
#: what the schema contains asserts nothing. ``reducegrid`` has a module of its
#: own — CDO gives it one — which is why two titles map to one category.
CONDITIONAL_BY_MODULE = {
    "Conditional selection": "ifthen ifnotthen ifthenelse ifthenc ifnotthenc",
    "Reduce fields to user-defined mask": "reducegrid",
}

#: The family whose *first* file is the mask.
MASK_FIRST = ("ifthen", "ifnotthen", "ifthenelse")

#: The two that take the mask as their only input.
MASK_ONLY = ("ifthenc", "ifnotthenc")


def conditional_operators():
    return sorted(
        name for name, spec in OPERATOR_SCHEMA.items()
        if spec.category is NCExplorerCategory.CONDITIONAL_SELECTION
    )


# --- the category ------------------------------------------------------------

def test_every_conditional_operator_is_in_the_category():
    """Including ``reducegrid``, which used to fall through to Miscellaneous."""
    expected = sorted(
        name
        for names in CONDITIONAL_BY_MODULE.values()
        for name in names.split()
    )
    assert conditional_operators() == expected


@pytest.mark.parametrize("operator,module", [
    (name, module)
    for module, names in CONDITIONAL_BY_MODULE.items()
    for name in names.split()
])
def test_the_category_comes_from_the_module_not_the_name(operator, module):
    """The module is the authority; ``reducegrid`` is why that matters here.

    Its name starts with "r", so no rule over names could have placed it, and
    the prefix cascade filed it under Miscellaneous for exactly that reason.
    """
    assert operator_module(operator) == module
    assert (OPERATOR_SCHEMA[operator].category
            is NCExplorerCategory.CONDITIONAL_SELECTION)


def test_reducegrid_is_not_in_the_curated_shortlist():
    """The curated list is a shortlist, and ``reducegrid`` is not one of five.

    It writes an unstructured grid this app cannot draw, which makes it the last
    of the six to reach for. Still one click away under the All submenu.
    """
    curated = OPERATOR_CATEGORIES[NCExplorerCategory.CONDITIONAL_SELECTION]
    assert "reducegrid" not in curated
    assert sorted(curated) == sorted(MASK_FIRST + MASK_ONLY)


# --- the input slots ---------------------------------------------------------

@pytest.mark.parametrize("operator", [
    name for name in ("ifthen", "ifnotthen", "ifthenelse", "ifthenc",
                      "ifnotthenc", "reducegrid")
])
def test_every_input_slot_is_declared(operator):
    """One ``OperatorInput`` per input the operator takes, none left generic.

    ``operator_inputs`` pads with "Input 2"-style placeholders, so the real
    assertion is that no slot is a placeholder.
    """
    spec = OPERATOR_SCHEMA[operator]
    slots = operator_inputs(operator)
    assert len(slots) == spec.nin
    assert len(spec.inputs) == spec.nin, "some slots are generic placeholders"
    for slot in slots:
        assert slot.role and slot.field


@pytest.mark.parametrize("operator", MASK_FIRST)
def test_the_mask_is_the_first_slot(operator):
    """Slot 0, which is the reverse of every ``*arith`` operator."""
    slots = operator_inputs(operator)
    assert "mask" in slots[0].role.lower()
    assert slots[0].key == "mask01"
    # And no *other* slot is the mask. Asserted on the key rather than on the
    # role text: ifthenelse's data slots legitimately mention the mask, since
    # what distinguishes them is which side of it they serve.
    assert [slot.key for slot in slots[1:]].count("mask01") == 0


@pytest.mark.parametrize("operator", MASK_FIRST)
def test_the_mask_slot_says_the_three_unguessable_things(operator):
    """Non-zero is true, missing means missing, and the field-count rule."""
    field = operator_inputs(operator)[0].field.lower()
    assert "zero" in field
    assert "missing" in field
    assert "one timestep" in field


@pytest.mark.parametrize("operator", MASK_ONLY)
def test_the_constant_forms_say_their_only_input_is_the_mask(operator):
    """"1 input" reads as "the data" everywhere else in the app."""
    slot = operator_inputs(operator)[0]
    assert "mask" in slot.role.lower()
    assert slot.key == "mask01"
    # No recipe: the only slot there is cannot be built from itself.
    assert slot.recipe == ""


def test_ifthenelse_says_a_missing_mask_does_not_fall_through():
    """The one rule that separates it from the two-file forms."""
    slots = operator_inputs("ifthenelse")
    assert "infile3" in slots[2].role or "false" in slots[2].role.lower()
    assert "missing" in slots[2].field.lower()
    # And that infile2/infile3 must agree in field count.
    assert "same number of fields" in slots[1].field.lower()
    assert "same number of fields" in slots[2].field.lower()


def test_a_mask_slot_has_no_unit_expectation():
    """A 0/1 field has no units worth checking, and saying so is the point.

    ``same_as_input1`` would compare the mask against the data it selects from
    and complain about a file that is exactly right.
    """
    for operator in MASK_FIRST + MASK_ONLY:
        assert operator_inputs(operator)[0].units == ""


def test_reducegrid_takes_data_not_a_mask_in_its_input_slot():
    """Its mask arrives as a parameter, which is the thing to say out loud."""
    slot = operator_inputs("reducegrid")[0]
    assert "mask" in slot.role.lower() and "parameter" in slot.role.lower()


# --- the reversed recipe -----------------------------------------------------

def test_the_mask_recipe_reads_the_data_slot_not_the_first_one():
    """``recipe_source`` is what stops the recipe describing itself.

    ``ifthen``'s mask is slot 0 and is built from slot 1. With the old fixed
    "build slot 1 from slot 0" the quoted command would have said "build the
    mask from the mask".
    """
    mask = operator_inputs("ifthen")[0]
    assert mask.recipe == "gtc,0 {in1}"
    assert mask.recipe_source == 1


def test_the_arith_family_still_reads_slot_zero():
    """The default is unchanged, which is what every declared slot meant."""
    companion = operator_inputs("ymonsub")[1]
    assert companion.recipe == "ymonavg {in1}"
    assert companion.recipe_source == 0


def test_the_lab_plans_a_mask_from_the_data_slot():
    """``operator_lab`` builds one mask and routes every operator to it.

    The plan used to skip slot 0 entirely, so a recipe declared there was
    invisible and the harness went on feeding ``ifthen`` two raw series.
    """
    from operator_lab.samples import _declared_companions

    plan = {key: (recipe, base) for key, recipe, base in _declared_companions()}
    assert plan["mask01"] == ("gtc,0 {in1}", "tg")


def test_the_lab_routes_the_mask_into_slot_zero(tmp_path):
    """Mask first, data second — the order CDO wants."""
    from operator_lab.samples import SampleSet

    samples = SampleSet(
        series=tmp_path / "series.nc",
        extra=[tmp_path / "extra.nc"],
        climate={"tg": tmp_path / "tg.nc", "tx": tmp_path / "tx.nc",
                 "mask01": tmp_path / "mask01.nc"},
    )
    assert [p.name for p in samples.inputs_for("ifthen", 2)] == [
        "mask01.nc", "tg.nc"]
    assert [p.name for p in samples.inputs_for("ifthenelse", 3)] == [
        "mask01.nc", "tg.nc", "tx.nc"]
    assert [p.name for p in samples.inputs_for("ifthenc", 1)] == ["mask01.nc"]


def test_a_parameterised_single_operator_is_wirable():
    """The mask's recipe is a comparison, and every comparison is parameterised.

    Refusing parameters cost this family its button entirely.
    """
    from ncexplorer_toolkit.gui.model_builder import _single_operator_recipe

    assert _single_operator_recipe("gtc,0 {in1}") == ("gtc", ("0",))


def test_inserting_the_mask_wires_it_into_the_mask_port(qapp, tmp_path):
    """The button must fill port 0 from port 1, not the other way round.

    Driven through the real ``insert_companion`` rather than through the graph,
    because re-implementing the wiring here would assert that the test can wire
    a graph rather than that the button does. A button that wired the mask into
    the data port would be worse than no button: the graph would run.
    """
    from ncexplorer_toolkit import NCExplorerOperatorGUI
    from ncexplorer_toolkit.core.model import ERROR, OPERATOR, SOURCE
    from ncexplorer_toolkit.gui.model_builder import ModelBuilderWindow

    source_path = tmp_path / "in.nc"
    source_path.write_bytes(b"CDF\x01")

    window = NCExplorerOperatorGUI()
    dock = ModelBuilderWindow(window)
    try:
        graph = dock.graph
        source = graph.add(SOURCE, path=str(source_path))
        node = graph.add(OPERATOR, operator="ifthen")
        # The data goes into port 1 — the natural way round, and the one the
        # old fixed wiring could not cope with.
        graph.connect(source.id, 0, node.id, 1)
        dock.canvas.rebuild()

        new_id = dock.insert_companion(node.id, "gtc", parameters=("0",),
                                       from_port=1, into_port=0)

        assert new_id, "nothing was inserted"
        new_node = graph.node(new_id)
        assert new_node.operator == "gtc"
        # The parameter came with it, so the node is not born invalid.
        assert new_node.parameters == ("0",)

        incoming = {c.target_port: c.source for c in graph.incoming(node.id)}
        assert incoming[0] == new_id, "the mask must fill port 0"
        assert incoming[1] == source.id, "the data must stay on port 1"
        # The same source feeds both branches.
        assert [c.source for c in graph.incoming(new_id)] == [source.id]
        assert not [i for i in graph.validate() if i.severity == ERROR]
    finally:
        dock.close()
        dock.deleteLater()
        window.close()
        window.deleteLater()


def test_the_button_is_not_offered_when_the_data_port_is_empty(qapp):
    """The recipe reads port 1; with nothing there, there is nothing to branch.

    Asking whether the node has *any* incoming connection would offer the button
    for ``ifthen`` when only the mask port is wired.
    """
    from ncexplorer_toolkit import NCExplorerOperatorGUI
    from ncexplorer_toolkit.core.model import OPERATOR, SOURCE
    from ncexplorer_toolkit.gui.model_builder import ModelBuilderWindow

    window = NCExplorerOperatorGUI()
    dock = ModelBuilderWindow(window)
    try:
        source = dock.graph.add(SOURCE, path="/nowhere.nc")
        node = dock.graph.add(OPERATOR, operator="ifthen")
        # Only the mask port is wired, which is not the port the recipe reads.
        dock.graph.connect(source.id, 0, node.id, 0)
        dock.canvas.rebuild()
        assert dock.insert_companion(node.id, "gtc", parameters=("0",),
                                     from_port=1, into_port=0) == ""
        assert [n.operator for n in dock.graph.nodes
                if n.kind == OPERATOR] == ["ifthen"]
    finally:
        dock.close()
        dock.deleteLater()
        window.close()
        window.deleteLater()


# --- the descriptions --------------------------------------------------------

@pytest.mark.parametrize("operator", MASK_FIRST)
def test_the_description_names_the_mask_and_the_metadata_rule(operator):
    description = OPERATOR_SCHEMA[operator].description
    assert "infile1 is the mask" in description
    assert "metadata from infile2" in description or \
           "metadata from infile2" in description.replace("its ", "")


@pytest.mark.parametrize("operator", MASK_FIRST + MASK_ONLY)
def test_the_description_carries_a_command_that_builds_a_mask(operator):
    """Somebody with data and no mask needs the line, not just the warning."""
    assert "cdo gtc,0 infile mask" in OPERATOR_SCHEMA[operator].description


@pytest.mark.parametrize("operator", ("ifthen", "ifnotthen"))
def test_the_description_warns_that_swapping_is_silent(operator):
    """Measured, not predicted — see the cdo_required test below."""
    description = OPERATOR_SCHEMA[operator].description
    assert "exits 0" in description
    assert "Swapping" in description


def test_the_ifthenelse_description_says_missing_beats_infile3():
    description = OPERATOR_SCHEMA["ifthenelse"].description
    assert "does not fall through to infile3" in description


def test_the_reducegrid_description_carries_the_docs_example():
    """The CDO documentation's own example, forcing NetCDF for a reason."""
    description = OPERATOR_SCHEMA["reducegrid"].description
    assert "cdo -f nc reducegrid,lsm.grb temp.grb tempOnLand.nc" in description
    assert "unstructured" in description


def test_the_reducegrid_description_admits_the_app_cannot_draw_it():
    """The canvas does not refuse the file — it draws time against cell index.

    A reduced variable is ``(time, ncells)``: two dimensions, so the canvas's
    ``len(dims) > 2`` guard skips the timestep selection and its ``ndim != 2``
    check passes. Saying so is better than letting a user find out.
    """
    description = OPERATOR_SCHEMA["reducegrid"].description
    assert "cannot draw" in description
    assert "cell index" in description


def test_every_surface_shows_the_same_description(qapp):
    """One sentence, three surfaces — the point of putting it in the schema.

    ``help_text`` rather than ``description``: the palette keeps the catalog's
    one-line title separately, because that is what it searches and shows in the
    list row, and the schema's full text is what it shows as help.
    """
    from ncexplorer_toolkit.gui.command_palette import build_entries
    from ncexplorer_toolkit.gui.main_window import NCExplorerOperatorGUI

    entries = {entry.name: entry for entry in build_entries()}
    for operator in conditional_operators():
        schema = OPERATOR_SCHEMA[operator].description        # model builder
        assert NCExplorerOperatorGUI.get_operator_description(operator) == schema
        assert entries[operator].help_text == schema


# --- the two-input comparisons, deliberately left alone ----------------------

def test_the_plain_comparisons_get_no_slot_declarations():
    """Checked, and there is no claim worth making.

    ``cdo -h gt`` calls them "two datasets" and distinguishes the two only by
    which side of the comparison they sit on — which the operator's own name and
    the formula in its description already state. Neither file is a mask and
    neither is a climatology, so a slot caption would be repeating "Input 1".
    The one real trap, that one file may hold a single timestep and be broadcast
    over the other, is already appended to all six by ``_BROADCAST_NOTE``.

    Asserted rather than left implicit so that a future reader knows the family
    was considered and excluded, not overlooked.
    """
    for operator in ("eq", "ne", "le", "lt", "ge", "gt"):
        spec = OPERATOR_SCHEMA[operator]
        assert spec.nin == 2
        assert spec.inputs == ()
        assert "single timestep" in spec.description


# --- file-valued parameters --------------------------------------------------

def test_the_schema_knows_which_parameters_are_files():
    """Driven off ``kind``, never off a list of operator names."""
    assert file_parameter_indexes("reducegrid") == (0,)
    assert file_parameter_indexes("writeremapscrip") == (0, 1)
    assert file_parameter_indexes("timmean") == ()
    # ``grid`` is excluded: its value may be a preset rather than a path.
    assert file_parameter_indexes("remapbil") == ()


def test_reducegrid_declares_both_parameters_required_first():
    params = OPERATOR_SCHEMA["reducegrid"].params
    assert [p.name for p in params] == ["mask", "limitCoordsOutput"]
    assert params[0].kind == "file" and not params[0].optional
    assert params[1].optional
    assert set(params[1].choices) == {"", "nobounds", "nocoords"}


def test_a_missing_parameter_file_is_named_before_argv():
    problems = missing_parameter_files("reducegrid", ["/no/such/mask.nc"])
    assert problems and "no such file" in problems[0]


def test_a_parameter_the_operator_writes_is_not_required_to_exist():
    """``tee``'s parameter is its output; requiring it would refuse every use.

    ``cdo -h tee`` calls it "Destination filename for the copy of the input
    file". ``writeremapscrip``'s second parameter is likewise the file it
    writes, and the ``setpartab*`` family may take a table name rather than a
    path — that one is unchecked because the installed CDO has no help for it.
    """
    assert missing_parameter_files("tee", ["/no/such/out.nc"]) == []
    assert missing_parameter_files(
        "writeremapscrip", ["/no/such/w.nc", "/no/such/scrip.nc"]) == [
            "weights: no such file, '/no/such/w.nc'"]
    assert missing_parameter_files("setpartab", ["some_table_name"]) == []


def test_a_blank_optional_parameter_is_not_a_missing_file():
    assert missing_parameter_files("reducegrid", ["", ""]) == []


# --- the WSL rewrite, which nobody runs locally ------------------------------

@pytest.mark.parametrize("argument,expected", [
    # The case that was broken: a path *inside* an operator token. The whole
    # token matched ``_is_file_path`` on its backslashes, so it went to
    # ``_win_to_wsl``, whose ``token[1] == ":"`` test saw "e" and fell through
    # to merely swapping separators.
    (r"reducegrid,C:\Users\me\mask.nc", "reducegrid,/mnt/c/Users/me/mask.nc"),
    (r"reducegrid,C:\Users\me\mask.nc,nobounds",
     "reducegrid,/mnt/c/Users/me/mask.nc,nobounds"),
    # A plain path still converts.
    (r"C:\Users\me\data.nc", "/mnt/c/Users/me/data.nc"),
    # And a token whose commas are not paths is left alone.
    ("sellonlatbox,-10,10,-5,5", "sellonlatbox,-10,10,-5,5"),
    ("timmean", "timmean"),
])
def test_a_path_inside_an_operator_token_is_converted_for_wsl(argument, expected):
    assert NCExplorerIntegration._to_wsl_argument(argument) == expected


# --- against the real binary -------------------------------------------------

@pytest.fixture(scope="module")
def files(tmp_path_factory):
    """A 5-step field, a 0/1 mask, and a mask holding missing values too."""
    directory = tmp_path_factory.mktemp("conditional")
    paths = {name: directory / f"{name}.nc" for name in
             ("data", "data_miss", "mask", "mask_miss", "neg")}
    run = lambda args: subprocess.run(  # noqa: E731
        ["cdo", "-O", "-f", "nc", *args], capture_output=True, timeout=120,
        check=True)
    run(["-settaxis,2000-01-01,00:00:00,1day", "-duplicate,5",
         "-random,r8x4,42", str(paths["data"])])
    run(["-setrtomiss,0,0.2", str(paths["data"]), str(paths["data_miss"])])
    run(["-gtc,0.5", "-seltimestep,1", str(paths["data"]), str(paths["mask"])])
    run(["-gtc,0.5", "-seltimestep,1", str(paths["data_miss"]),
         str(paths["mask_miss"])])
    run(["-mulc,-1", str(paths["data"]), str(paths["neg"])])
    return paths


def _values(path):
    out = subprocess.run(["cdo", "-s", "output", str(path)],
                         capture_output=True, text=True, timeout=120)
    return out.stdout.split()


@cdo_required
def test_the_mask_first_order_actually_selects(files, tmp_path):
    """13 of 32 cells kept, which is the number of non-zero mask cells."""
    out = tmp_path / "correct.nc"
    result = subprocess.run(
        ["cdo", "ifthen", str(files["mask"]), str(files["data"]), str(out)],
        capture_output=True, text=True, timeout=120)
    assert result.returncode == 0
    kept = [v for v in _values(out)[:32] if not v.startswith("-9e+33")]
    assert len(kept) == 13


@cdo_required
def test_swapping_the_mask_and_the_data_is_silent_and_wrong(files, tmp_path):
    """The failure the slot captions exist to prevent, re-measured.

    CDO exits 0 and the output is the *mask file*, unchanged: every value in a
    data field is non-zero, so the whole field is "true" and nothing is
    filtered. ``cdo diff`` printing nothing is the proof.
    """
    out = tmp_path / "swapped.nc"
    result = subprocess.run(
        ["cdo", "ifthen", str(files["data"]), str(files["mask"]), str(out)],
        capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, "if this ever fails, CDO started checking"

    diff = subprocess.run(["cdo", "diff", str(out), str(files["mask"])],
                          capture_output=True, text=True, timeout=120)
    assert "0 of 1 records differ" in (diff.stdout + diff.stderr) or \
        not diff.stdout.strip(), "the swapped output is the mask, copied through"


@cdo_required
def test_a_missing_mask_value_yields_missing_not_infile3(files, tmp_path):
    """``ifthenelse``'s one rule that the two-file forms do not share."""
    out = tmp_path / "ite.nc"
    subprocess.run(
        ["cdo", "ifthenelse", str(files["mask_miss"]), str(files["data"]),
         str(files["neg"]), str(out)],
        capture_output=True, timeout=120, check=True)

    mask = _values(files["mask_miss"])[:32]
    result = _values(out)[:32]
    third = _values(files["neg"])[:32]
    missing = [i for i, v in enumerate(mask) if v.startswith("-9e+33")]
    assert missing, "the fixture should hold missing mask cells"
    for index in missing:
        assert result[index].startswith("-9e+33")
        assert result[index] != third[index]


@cdo_required
def test_ifthenc_writes_the_constant_only_where_the_mask_is_true(files, tmp_path):
    out = tmp_path / "itc.nc"
    subprocess.run(["cdo", "ifthenc,7.5", str(files["mask_miss"]), str(out)],
                   capture_output=True, timeout=120, check=True)
    written = set(_values(out)[:32])
    assert written <= {"7.5", "-9e+33"}
    assert "7.5" in written


@cdo_required
def test_reducegrid_reduces_to_the_non_zero_locations(files, tmp_path):
    out = tmp_path / "reduced.nc"
    subprocess.run(
        ["cdo", "-f", "nc", f"reducegrid,{files['mask']}", str(files["data"]),
         str(out)], capture_output=True, timeout=120, check=True)
    info = subprocess.run(["cdo", "-s", "sinfo", str(out)],
                          capture_output=True, text=True, timeout=120).stdout
    assert "unstructured" in info
    assert "points=13" in info


@cdo_required
def test_a_mask_parameter_with_a_space_in_its_path_still_runs(files, tmp_path):
    """The aliasing, end to end, through the layer that builds the command.

    Without it the mask arrived as two arguments and CDO failed on a filename
    nobody typed.
    """
    spaced = tmp_path / "My Data"
    spaced.mkdir()
    mask = spaced / "mask file.nc"
    mask.write_bytes(files["mask"].read_bytes())

    integration = NCExplorerIntegration()
    result = integration.execute_operator(
        "reducegrid",
        input_files=[str(files["data"])],
        output_files=[str(tmp_path / "spaced.nc")],
        extra_parameters=[str(mask)],
    )
    assert result.success, result.stderr
    info = subprocess.run(["cdo", "-s", "sinfo", str(tmp_path / "spaced.nc")],
                          capture_output=True, text=True, timeout=120).stdout
    assert "points=13" in info


@cdo_required
def test_a_mask_that_does_not_exist_is_refused_before_cdo_sees_it(files, tmp_path):
    integration = NCExplorerIntegration()
    with pytest.raises(ValueError, match="no such file"):
        integration.execute_operator(
            "reducegrid",
            input_files=[str(files["data"])],
            output_files=[str(tmp_path / "never.nc")],
            extra_parameters=[str(tmp_path / "absent.nc")],
        )
