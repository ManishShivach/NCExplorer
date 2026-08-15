"""A required parameter left blank must never reach CDO.

CDO does not abort on a missing comma-parameter. It prompts for it on stdin,
and it keeps prompting after EOF — measured on CDO 2.6.0, ``cdo pow a.nc p.nc``
with stdin closed writes 39 MB of "cdo pow : Enter value > " in five seconds and
does not stop. Against a pipe the application is draining, that is a frozen
window rather than a failed run, which is why none of the surfaces could report
it: there is no error to report.

So the assertion these tests care about is not "a dialog appeared". It is that
**no argv was ever built**, because building one is the last step before the
process exists. Every test below spies on the point the execution layer turns a
call into a command line and asserts it was not reached.

The model builder has refused this since it was written; the toolbar and the
command palette share ``show_operator_parameters`` and had no equivalent.
"""

import pytest

from ncexplorer_toolkit.core.categories import (
    NCExplorerCategory, OPERATOR_SCHEMA, missing_required_parameters,
)
from ncexplorer_toolkit.core.model import ERROR, OPERATOR, SINK, SOURCE, ModelGraph
from ncexplorer_toolkit.core.nc_integration import NCExplorerIntegration
from ncexplorer_toolkit.gui.main_window import (
    CDO_OPTIONS_LABEL, STDIN_FILE_LABEL, STDOUT_FILE_LABEL,
)

#: The three the plan named, one per shape of the problem: a numeric value
#: (``pow``), an expression (``expr``), and a constant on an operator whose
#: whole family takes one (``addc``).
NAMED = ("pow", "expr", "addc")


def arithmetic_with_required_params():
    """Every Arithmetic operator that declares at least one required parameter."""
    return sorted(
        name for name, spec in OPERATOR_SCHEMA.items()
        if spec.category is NCExplorerCategory.ARITHMETIC
        and any(not param.optional for param in spec.params)
    )


# ---------------------------------------------------------------------------
# The rule itself
# ---------------------------------------------------------------------------

def test_required_params_come_first():
    """The positional check is only sound if no required param follows an optional one.

    ``missing_required_parameters`` reads the first ``required`` positions of the
    supplied list. That maps onto the right parameters exactly while
    ``_PARAM_SPECS`` keeps required entries ahead of optional ones — so the
    ordering is pinned here rather than left as a comment.
    """
    offenders = []
    for name, spec in OPERATOR_SCHEMA.items():
        seen_optional = False
        for param in spec.params:
            if param.optional:
                seen_optional = True
            elif seen_optional:
                offenders.append(name)
                break
    assert offenders == []


@pytest.mark.parametrize("operator", NAMED)
def test_blank_and_absent_are_both_missing(operator):
    """A whitespace-only field is as missing as no field at all."""
    assert missing_required_parameters(operator, [])
    assert missing_required_parameters(operator, [""])
    assert missing_required_parameters(operator, ["   "])
    assert missing_required_parameters(operator, ["2"]) == []


def test_optional_tail_is_not_required():
    """Only the required head has to be filled in.

    ``random`` takes a grid and an optional seed; supplying the grid alone is a
    complete call, and the blank the form hands back for the empty seed field
    must not be read as an omission.
    """
    assert missing_required_parameters("random", ["r36x18", ""]) == []
    assert missing_required_parameters("random", [""]) == ["grid"]


def test_unknown_operator_requires_nothing():
    """Nothing here knows better than the schema what an operator needs."""
    assert missing_required_parameters("not_a_cdo_operator", []) == []


# ---------------------------------------------------------------------------
# The execution layer — the last gate before argv
# ---------------------------------------------------------------------------

@pytest.fixture
def spy_integration(monkeypatch):
    """An integration whose command builder records instead of being reached.

    ``_build_command`` is the step between a resolved call and a real process,
    so an empty record is proof no CDO was spawned — a stronger claim than any
    assertion about a dialog's text.
    """
    integration = NCExplorerIntegration.__new__(NCExplorerIntegration)
    integration.NCExplorer_binary = "cdo"
    integration.operator_signatures = {
        name: (spec.nin, spec.nout) for name, spec in OPERATOR_SCHEMA.items()
    }
    # Built without ``__init__`` so no temp store is ever created; ``__del__``
    # cleans one up unconditionally, so it needs something to call.
    integration._tstore = type("_NoStore", (), {"cleanup": lambda self: None})()
    built: list[list[str]] = []
    monkeypatch.setattr(
        NCExplorerIntegration, "_build_command",
        lambda self, cmd: built.append(list(cmd)) or list(cmd),
    )
    integration.built = built
    return integration


@pytest.mark.parametrize("operator", arithmetic_with_required_params())
def test_execution_layer_refuses_every_arithmetic_operator(operator, spy_integration):
    """No arithmetic operator can be resolved into argv with its value blank."""
    spec = OPERATOR_SCHEMA[operator]
    inputs = ["in.nc"] * (1 if spec.nin == -1 else spec.nin)
    blanks = [""] * len(spec.params)

    with pytest.raises(ValueError) as excinfo:
        spy_integration._resolve_operator_call(operator, inputs, ["out.nc"], blanks)

    # The message names the field, so the surfaces above can pass it straight on.
    first_required = next(p for p in spec.params if not p.optional)
    assert (first_required.label or first_required.name) in str(excinfo.value)
    assert spy_integration.built == []


@pytest.mark.parametrize("operator", NAMED)
def test_execution_layer_still_accepts_a_supplied_value(operator, spy_integration):
    """The refusal is about blanks only — a filled-in call still builds argv."""
    call = spy_integration._resolve_operator_call(operator, ["in.nc"], ["out.nc"], ["2"])
    assert call.cmd[1] == f"{operator},2"


def test_optional_blanks_are_still_trimmed(spy_integration):
    """The behaviour the trim existed for has to survive the refusal.

    A form renders one field per declared parameter and hands back one value per
    field, so an untouched optional field arrives as "". That must not become a
    trailing comma in the operator token.
    """
    call = spy_integration._resolve_operator_call(
        "random", [], ["out.nc"], ["r36x18", ""])
    assert call.cmd[1] == "random,r36x18"


# ---------------------------------------------------------------------------
# The toolbar form — which the command palette also opens
# ---------------------------------------------------------------------------

@pytest.fixture
def window(qapp, monkeypatch):
    """The real main window, with its modal boxes captured rather than shown."""
    from PyQt6.QtWidgets import QMessageBox

    from ncexplorer_toolkit import NCExplorerOperatorGUI

    boxes: list[tuple[str, str]] = []
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(
            QMessageBox, name,
            staticmethod(lambda parent, title, text, *a, _n=name, **k:
                         boxes.append((title, text))
                         or QMessageBox.StandardButton.Ok),
        )

    widget = NCExplorerOperatorGUI()
    widget.boxes = boxes
    yield widget
    widget.close()
    widget.deleteLater()


def fill_form(window, operator, tmp_path, value_for_extras=None):
    """Open ``operator``'s form and fill every field except the parameters.

    Returns the labels of the rows deliberately left blank, so a test can say
    which field it expects to be named back at it.
    """
    from PyQt6.QtWidgets import QLineEdit, QWidget

    window.show_operator_parameters(operator)
    spec = OPERATOR_SCHEMA[operator]

    source = tmp_path / "in.nc"
    source.write_bytes(b"CDF\x01")          # only its existence is checked here

    blank_labels = []
    for label, widget in window.parameter_widgets.items():
        if label == "multi_file_widget":
            widget.set_files([str(source)] * (1 if spec.nin == -1 else spec.nin))
            continue
        # The rows that are not the operator's arguments. This helper's contract
        # is "every row left blank is a required parameter, name it back at me",
        # and these three are neither parameters nor required: the options field
        # is optional on every operator, and the two redirection rows belong to
        # the run rather than to the command. Left in ``blank_labels`` they made
        # three arithmetic tests demand that "CDO global options" be reported as
        # a missing parameter.
        if label in (STDIN_FILE_LABEL, STDOUT_FILE_LABEL, CDO_OPTIONS_LABEL):
            continue
        if "Input File" in label:
            text = str(source)
        elif "Output File" in label:
            text = str(tmp_path / "out.nc")
        elif value_for_extras is not None:
            text = value_for_extras
        else:
            blank_labels.append(label)
            continue

        if isinstance(widget, QLineEdit):
            widget.setText(text)
        elif isinstance(widget, QWidget):
            for child in widget.findChildren(QLineEdit):
                child.setText(text)
                break
    return blank_labels


@pytest.mark.parametrize("operator", NAMED)
def test_form_refuses_a_blank_parameter(operator, window, tmp_path, monkeypatch):
    """Execute with the value field empty names the field and spawns nothing."""
    resolved: list[str] = []
    monkeypatch.setattr(
        type(window.NCExplorer), "_resolve_operator_call",
        lambda self, *a, **k: resolved.append(a[0]),
    )

    blank_labels = fill_form(window, operator, tmp_path)
    window.current_operator = operator
    window.execute_operation()

    assert resolved == [], "the execution layer was reached with a blank parameter"
    assert window.boxes, "the user was told nothing"
    title, text = window.boxes[-1]
    assert title == "Missing parameter"
    for label in blank_labels:
        assert label in text


@pytest.mark.parametrize("operator", arithmetic_with_required_params())
def test_form_refuses_every_arithmetic_operator(operator, window, tmp_path, monkeypatch):
    """The same across the whole category, not just the three named ones."""
    resolved: list[str] = []
    monkeypatch.setattr(
        type(window.NCExplorer), "_resolve_operator_call",
        lambda self, *a, **k: resolved.append(a[0]),
    )

    fill_form(window, operator, tmp_path)
    window.current_operator = operator
    window.execute_operation()

    assert resolved == []
    assert window.boxes[-1][0] == "Missing parameter"


def test_palette_opens_the_same_form(window):
    """One fix covers two surfaces only while the palette routes to this form."""
    from ncexplorer_toolkit.gui.command_palette import CommandPalette

    opened = []
    window.show_operator_parameters = opened.append

    palette = CommandPalette(window)
    palette.search.setText("pow")
    row = next(index for index, entry in enumerate(palette._shown)
               if entry.name == "pow")
    palette.results.setCurrentRow(row)
    palette._accept_current()

    assert opened == ["pow"]
    palette.deleteLater()


# ---------------------------------------------------------------------------
# The model builder — already correct, pinned so it stays that way
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("operator", NAMED)
def test_model_builder_refuses_a_blank_parameter(operator, tmp_path):
    """The surface that always got this right must keep getting it right.

    Driven through ``ModelGraph.validate`` rather than the widget, because that
    is what the builder's Run button is gated on.
    """
    source_path = tmp_path / "in.nc"
    source_path.write_bytes(b"CDF\x01")

    graph = ModelGraph()
    source = graph.add(SOURCE, path=str(source_path))
    node = graph.add(OPERATOR, operator=operator, parameters=("",))
    sink = graph.add(SINK, path=str(tmp_path / "out.nc"))
    graph.connect(source.id, 0, node.id, 0)
    graph.connect(node.id, 0, sink.id, 0)

    errors = [issue for issue in graph.validate()
              if issue.severity == ERROR and issue.node == node.id]
    assert any("needs" in issue.message for issue in errors), \
        [issue.message for issue in errors]
    # And the field is named, the same way the other two surfaces name it.
    label = next(p.label or p.name
                 for p in OPERATOR_SCHEMA[operator].params if not p.optional)
    assert any(label in issue.message for issue in errors)
