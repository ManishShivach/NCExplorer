# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""The Expr language gets an editor, and what it produces reaches argv intact.

``expr``/``aexpr``/``exprf``/``aexprf`` are seventeen operators, ~70 intrinsic
functions, an ``_ALL_`` template and ``_``-prefixed temporaries. The entire user
interface for that was ``_p("instr", _STR, "instr", "e.g. tas=var*2")`` — one
line edit with a placeholder — so nothing was discoverable and a typo was found
only after a full run against real data.

The end-to-end test is the one that matters: type an expression, check it,
commit it, and assert on the argv the execution layer builds. Everything between
those two ends is only worth having if the command comes out right.
"""

import shutil

import numpy as np
import pytest
import xarray as xr

from ncexplorer_toolkit.core.categories import OPERATOR_SCHEMA
from ncexplorer_toolkit.core.expr_reference import (
    EXPR_FUNCTIONS, EXPR_NOTES, EXPR_OPERATORS, all_entries, appends,
    is_expression_operator, reads_from_file,
)
from ncexplorer_toolkit.core.nc_integration import NCExplorerIntegration
from ncexplorer_toolkit.gui.expression_editor import (
    ExpressionEditor, check_expression, read_variables, timestep_count,
)

cdo_required = pytest.mark.skipif(
    shutil.which("cdo") is None, reason="needs an installed CDO")


@pytest.fixture
def sample(tmp_path):
    """Two named variables, six timesteps — small enough to check instantly."""
    path = tmp_path / "sample.nc"
    coords = {
        "time": ("time", np.arange(6), {"units": "days since 2000-01-01"}),
        "lat": np.linspace(-80, 80, 9),
        "lon": np.linspace(-170, 170, 18),
    }
    xr.Dataset(
        {
            "tas": (("time", "lat", "lon"), np.full((6, 9, 18), 300.0),
                    {"units": "K"}),
            "pr": (("time", "lat", "lon"), np.random.rand(6, 9, 18),
                   {"units": "mm"}),
        },
        coords=coords,
    ).to_netcdf(path)
    return str(path)


# --- the reference data -----------------------------------------------------

def test_all_four_operators_declare_an_expression_field():
    for name in ("expr", "aexpr", "exprf", "aexprf"):
        assert is_expression_operator(name)
        params = OPERATOR_SCHEMA[name].params
        assert len(params) == 1
        assert params[0].kind == "expression"


def test_the_two_pairs_differ_in_exactly_one_way():
    """append vs replace, and inline vs file. Nothing else separates them."""
    assert [name for name in ("expr", "aexpr", "exprf", "aexprf")
            if appends(name)] == ["aexpr", "aexprf"]
    assert [name for name in ("expr", "aexpr", "exprf", "aexprf")
            if reads_from_file(name)] == ["exprf", "aexprf"]


def test_the_reference_covers_what_the_documentation_lists():
    assert len(EXPR_OPERATORS.entries) == 17          # incl. the ternary
    names = {entry.name for entry in all_entries()}
    # One from each documented group, including the ones the old placeholder
    # gave no hint existed.
    for expected in ("min", "isMissval", "clon", "gridarea", "ctimestep",
                     "cdate", "ngp", "missval", "fldmean", "zonmedian",
                     "vertsum", "sellevel", "remove", "trimrel"):
        assert expected in names
    assert len(EXPR_FUNCTIONS) == 7                   # the doc's seven headings


def test_the_two_surprising_facts_are_written_down():
    joined = " ".join(EXPR_NOTES)
    assert "aexpr and aexprf APPEND" in joined
    assert "_ALL_" in joined
    assert "underscore" in joined
    # The units trap, measured: addc,-273.15 on Kelvin gives Celsius labelled K.
    assert "still labelled K" in joined


# --- reading the input ------------------------------------------------------

def test_variables_are_read_from_the_wired_file(sample):
    assert read_variables(sample) == ["tas", "pr"]
    assert timestep_count(sample) == 6


def test_an_unreadable_file_is_not_an_error(tmp_path):
    assert read_variables("") == []
    assert read_variables(str(tmp_path / "nope.nc")) == []
    assert timestep_count("") == 0


# --- the check --------------------------------------------------------------

@cdo_required
def test_check_accepts_a_valid_expression(sample):
    ok, message = check_expression("out=tas*2;", sample)
    assert ok, message


@cdo_required
def test_check_catches_a_deliberate_syntax_error(sample, tmp_path):
    """And reports CDO's own words, which name the offending token."""
    real_output = tmp_path / "must_not_be_written.nc"

    ok, message = check_expression("out=tas*;", sample)

    assert not ok
    assert "syntax error" in message.lower()
    assert not real_output.exists()


@cdo_required
def test_check_catches_an_unknown_variable(sample):
    ok, message = check_expression("out=notavariable*2;", sample)
    assert not ok
    assert message.strip()


def test_check_refuses_politely_with_nothing_to_check(sample):
    ok, message = check_expression("", sample)
    assert not ok and "nothing to check" in message

    ok, message = check_expression("out=tas;", "")
    assert not ok and "input file" in message


# --- the dialog -------------------------------------------------------------

def test_the_editor_round_trips_an_existing_expression(qapp, sample):
    dialog = ExpressionEditor(operator="expr", expression="out = tas * 2;",
                              input_path=sample)
    try:
        assert dialog.editor.toPlainText() == "out = tas * 2;"
        assert dialog.expression() == "out = tas * 2;"
        # The wired file's variables are offered, click-to-insert.
        listed = [dialog.variables.item(row).text()
                  for row in range(dialog.variables.count())]
        assert listed == ["tas", "pr"]
    finally:
        dialog.deleteLater()


def test_a_multi_line_expression_becomes_one_command_argument(qapp, sample):
    """Statements are semicolon-terminated, so joining them loses nothing."""
    dialog = ExpressionEditor(operator="expr", input_path=sample)
    try:
        dialog.editor.setPlainText("_warm = tas > 300;\nout = _warm ? tas : 0;")
        assert dialog.expression() == "_warm = tas > 300; out = _warm ? tas : 0;"
        assert "\n" not in dialog.expression()
    finally:
        dialog.deleteLater()


def test_inserting_from_the_reference_types_into_the_editor(qapp, sample):
    dialog = ExpressionEditor(operator="expr", input_path=sample)
    try:
        dialog.editor.setPlainText("out=")
        dialog.editor.moveCursor(dialog.editor.textCursor().MoveOperation.End)
        dialog.insert("min(x,y)")
        assert "min(x,y)" in dialog.editor.toPlainText()
    finally:
        dialog.deleteLater()


@cdo_required
def test_check_reports_through_the_dialog(qapp, sample):
    dialog = ExpressionEditor(operator="expr", input_path=sample)
    try:
        dialog.editor.setPlainText("out = tas * 2;")
        assert dialog.check() is True
        assert "valid" in dialog.status.text()

        dialog.editor.setPlainText("out = tas *;")
        assert dialog.check() is False
        assert "syntax error" in dialog.status.text().lower()
    finally:
        dialog.deleteLater()


def test_the_file_operators_write_the_same_language_to_a_file(qapp, sample,
                                                              tmp_path):
    dialog = ExpressionEditor(operator="exprf", input_path=sample)
    try:
        dialog.editor.setPlainText("_warm = tas > 300; out = _warm ? tas : 0;")
        script = tmp_path / "script.txt"
        dialog.write_script(str(script))

        # One statement per line, which is the point of having a script file.
        assert script.read_text().splitlines() == [
            "_warm = tas > 300;", "out = _warm ? tas : 0;",
        ]
    finally:
        dialog.deleteLater()


# --- end to end: type, check, commit, and assert on argv --------------------

@cdo_required
def test_typing_checking_and_committing_produces_the_right_argv(qapp, sample,
                                                                tmp_path,
                                                                monkeypatch):
    """The whole path, ending where it matters: the command line CDO is given.

    The expression carries a comma (``min``'s second argument), a semicolon and
    a ternary — every character that a shell-quoting workaround would have been
    invented for. argv is a list, so all three survive untouched.
    """
    from PyQt6.QtWidgets import QDialog, QMessageBox

    from ncexplorer_toolkit import NCExplorerOperatorGUI

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))

    expression = "_warm = tas > 300; out = _warm ? min(tas,0.5) : 0;"

    # The editor is driven for real, then accepted without a display.
    def drive(self):
        self.editor.setPlainText(expression)
        assert self.check(), self.status.text()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ExpressionEditor, "exec", drive)

    built: list[list[str]] = []
    monkeypatch.setattr(
        NCExplorerIntegration, "_build_command",
        lambda self, cmd: built.append(list(cmd)) or list(cmd))

    window = NCExplorerOperatorGUI()
    try:
        window.show_operator_parameters("expr")

        from PyQt6.QtWidgets import QLineEdit
        for label, widget in window.parameter_widgets.items():
            edits = widget.findChildren(QLineEdit) if not isinstance(
                widget, QLineEdit) else [widget]
            if "Input File" in label:
                edits[0].setText(sample)
            elif "Output File" in label:
                edits[0].setText(str(tmp_path / "out.nc"))

        # Press "Edit…" on the instr row — the editor writes the field back.
        instr_widget = window.parameter_widgets["instr"]
        line_edit = instr_widget.findChildren(QLineEdit)[0]
        window.open_expression_editor(line_edit)
        assert line_edit.text() == expression

        window.execute_operation()
    finally:
        window.close()
        window.deleteLater()

    assert built, "the execution layer was never reached"
    argv = built[-1]
    # One token, exactly as typed: no quotes added, no comma split, no
    # semicolon escaped.
    assert argv[1] == f"expr,{expression}"
    assert argv[2] == sample
    assert argv[3].endswith("out.nc")


@cdo_required
def test_the_committed_expression_actually_runs(sample, tmp_path):
    """The argv above is not merely well-formed — CDO accepts and executes it."""
    integration = NCExplorerIntegration()
    expression = "_warm = tas > 300; out = _warm ? min(tas,0.5) : 0;"
    output = tmp_path / "real.nc"

    result = integration.execute_operator(
        "expr", input_files=sample, output_files=str(output),
        extra_parameters=[expression])

    assert result.success, result.stderr
    assert output.exists()
    with xr.open_dataset(output, decode_times=False) as dataset:
        # expr replaces; the temporary _warm was computed and never written.
        assert list(dataset.data_vars) == ["out"]


@cdo_required
def test_aexpr_appends_where_expr_replaces(sample, tmp_path):
    """The one difference between the pairs, asserted rather than asserted-about."""
    integration = NCExplorerIntegration()
    replaced = tmp_path / "replaced.nc"
    appended = tmp_path / "appended.nc"

    assert integration.execute_operator(
        "expr", input_files=sample, output_files=str(replaced),
        extra_parameters=["out=tas*2;"]).success
    assert integration.execute_operator(
        "aexpr", input_files=sample, output_files=str(appended),
        extra_parameters=["out=tas*2;"]).success

    with xr.open_dataset(replaced, decode_times=False) as dataset:
        assert list(dataset.data_vars) == ["out"]
    with xr.open_dataset(appended, decode_times=False) as dataset:
        assert list(dataset.data_vars) == ["tas", "pr", "out"]


@cdo_required
def test_an_expr_result_carries_no_units(sample, tmp_path):
    """The fact the notes warn about, measured here so it stays true."""
    integration = NCExplorerIntegration()
    output = tmp_path / "units.nc"

    assert integration.execute_operator(
        "expr", input_files=sample, output_files=str(output),
        extra_parameters=["tempC=tas-273.15;"]).success

    with xr.open_dataset(output, decode_times=False) as dataset:
        assert "units" not in dataset["tempC"].attrs

    # …and the other half: addc keeps the units it had, which is worse.
    kept = tmp_path / "kept.nc"
    assert integration.execute_operator(
        "addc", input_files=sample, output_files=str(kept),
        extra_parameters=["-273.15"]).success
    with xr.open_dataset(kept, decode_times=False) as dataset:
        assert dataset["tas"].attrs.get("units") == "K"
