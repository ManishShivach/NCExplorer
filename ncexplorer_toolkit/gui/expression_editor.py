"""A real editor for the ``expr`` family, opened from the ``instr`` field.

``expr``/``aexpr``/``exprf``/``aexprf`` are an entire expression language —
seventeen operators including a ternary, about seventy intrinsic functions, an
``_ALL_`` template and ``_``-prefixed temporaries that are computed but never
written. The whole user interface for that was one ``QLineEdit`` carrying the
placeholder "e.g. tas=var*2": nothing above was discoverable from inside the
application, and a typo was only discovered after a full run against real data.

What this adds, in the order it matters:

* **Multi-line and monospaced.** Statements end in ``;`` and there is usually
  more than one of them.
* **The input file's own variable names**, listed and click-to-insert. Nothing
  else in the dialog is worth as much: an expression is written *about* those
  names and getting one wrong is the commonest possible mistake.
* **The function reference**, grouped as the documentation groups it, each entry
  insertable. See ``core/expr_reference.py``.
* **Check**, which runs the expression against the real input before committing
  it. ``cdo -s expr,<instr> <input> <temporary>`` answers in about 80 ms and
  reports CDO's own message, which is specific and good — "syntax error,
  unexpected ';'!" beats anything this dialog could say instead.

``exprf``/``aexprf`` read the same language from a file, so the same editor
writes that file. The two are one skill to learn rather than two.

Nothing here quotes anything. The integration layer passes argv as a list, so
the commas and semicolons inside an expression already survive —
``cdo expr,'out=min(tas,0.5);'`` was verified to reach CDO intact through
``execute_operator``. A quote added anywhere in this file would be a bug
somewhere else.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPlainTextEdit, QPushButton, QSplitter, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..core.expr_reference import (
    EXPR_FUNCTIONS, EXPR_NOTES, EXPR_OPERATORS, appends, reads_from_file,
)

logger = logging.getLogger(__name__)

#: Seconds the syntax check is allowed. It is a single timestep through a single
#: expression; anything past this is a hung CDO, not a slow one.
CHECK_TIMEOUT = 30

#: Above this many timesteps the check runs on the first one only. The whole
#: point of Check is that it costs nothing, and a decade of daily data through a
#: field statistic is not nothing.
SELTIMESTEP_ABOVE = 50


def read_variables(path: str) -> list[str]:
    """The data variables in ``path``, or [] if it cannot be read.

    Same source the map uses (``geocanvas/netcdf.py`` reads ``ds.data_vars``),
    so the names offered here are the names the rest of the application shows.
    """
    if not path or not os.path.exists(path):
        return []
    try:
        import xarray as xr

        with xr.open_dataset(path, decode_times=False) as dataset:
            return [str(name) for name in dataset.data_vars]
    except Exception:
        logger.debug("Could not read variables from %s", path, exc_info=True)
        return []


def timestep_count(path: str) -> int:
    """How many timesteps ``path`` holds; 0 when that cannot be determined."""
    if not path or not os.path.exists(path):
        return 0
    try:
        import xarray as xr

        with xr.open_dataset(path, decode_times=False) as dataset:
            return int(dataset.sizes.get("time", 0))
    except Exception:
        logger.debug("Could not count timesteps in %s", path, exc_info=True)
        return 0


def check_expression(expression: str, input_path: str, binary: str = "cdo",
                     operator: str = "expr") -> tuple[bool, str]:
    """Run ``expression`` against ``input_path`` and report what CDO said.

    Returns ``(ok, message)``. The output goes to a temporary file that is
    deleted here, so a check can never touch the path the user is actually
    writing to — that is the whole reason this does not simply run the operator.
    """
    expression = expression.strip()
    if not expression:
        return False, "There is nothing to check yet."
    if not input_path or not os.path.exists(input_path):
        return False, ("Wire up an input file first — the check runs the "
                       "expression against real data.")

    handle, scratch = tempfile.mkstemp(suffix=".nc", prefix="ncx_exprcheck_")
    os.close(handle)
    os.unlink(scratch)          # CDO writes it; it must not exist beforehand

    argv = [binary, "-s"]
    if timestep_count(input_path) > SELTIMESTEP_ABOVE:
        argv.append("-seltimestep,1")
    # No quoting: argv is a list, so the commas and semicolons in `expression`
    # reach CDO exactly as typed.
    argv += [f"{operator},{expression}", input_path, scratch]

    try:
        completed = subprocess.run(argv, capture_output=True, text=True,
                                   stdin=subprocess.DEVNULL,
                                   timeout=CHECK_TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, f"The check did not finish within {CHECK_TIMEOUT} seconds."
    except OSError as exc:
        return False, f"Could not run the check: {exc}"
    finally:
        if os.path.exists(scratch):
            os.unlink(scratch)

    if completed.returncode == 0:
        return True, "The expression is valid."

    # CDO's own words. They name the token and the position, which is more than
    # this dialog could work out for itself.
    message = "\n".join(
        line.strip() for line in
        ((completed.stderr or "") + (completed.stdout or "")).splitlines()
        if line.strip()
    )
    return False, message or "CDO rejected the expression but said nothing."


class ExpressionEditor(QDialog):
    """Compose one expression, check it, and commit it."""

    def __init__(self, parent=None, *, operator: str = "expr",
                 expression: str = "", input_path: str = "",
                 binary: str = "cdo"):
        super().__init__(parent)
        self.operator = operator
        self.input_path = input_path
        self.binary = binary

        self.setWindowTitle(f"Expression — {operator}")
        self.setMinimumSize(760, 520)

        self._build_ui()
        self.editor.setPlainText(expression)
        self._load_variables()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        headline = QLabel(
            f"<b>{self.operator}</b> — "
            + ("keeps the input variables and appends the results"
               if appends(self.operator) else
               "replaces the input variables with the results")
        )
        headline.setWordWrap(True)
        layout.addWidget(headline)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        # --- left: the expression itself ---
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self.editor = QPlainTextEdit()
        self.editor.setFont(QFont("Menlo, Monaco, Courier New, monospace"))
        self.editor.setPlaceholderText(
            "tempC = tas - 273.15;\n"
            "_wet = pr > 1;\n"
            "wetdays = _wet ? 1 : 0;"
        )
        self.editor.setTabChangesFocus(True)
        left_layout.addWidget(self.editor, 1)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        left_layout.addWidget(self.status)
        splitter.addWidget(left)

        # --- right: what there is to say ---
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        right_layout.addWidget(QLabel("Variables in the input file"))
        self.variables = QListWidget()
        self.variables.setMaximumHeight(130)
        self.variables.itemDoubleClicked.connect(
            lambda item: self.insert(item.text()))
        right_layout.addWidget(self.variables)

        right_layout.addWidget(QLabel("Operators and functions"))
        self.reference = QTreeWidget()
        self.reference.setHeaderHidden(True)
        self.reference.setColumnCount(1)
        for group in (EXPR_OPERATORS,) + EXPR_FUNCTIONS:
            parent = QTreeWidgetItem([group.title])
            self.reference.addTopLevelItem(parent)
            for entry in group.entries:
                child = QTreeWidgetItem([entry.signature])
                child.setToolTip(0, entry.summary)
                child.setData(0, Qt.ItemDataRole.UserRole, entry.text)
                parent.addChild(child)
        self.reference.itemDoubleClicked.connect(self._insert_reference)
        right_layout.addWidget(self.reference, 1)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        notes = QLabel("· " + "\n· ".join(EXPR_NOTES))
        notes.setWordWrap(True)
        notes.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(notes)

        buttons = QHBoxLayout()
        self.check_button = QPushButton("Check")
        self.check_button.setToolTip(
            "Run this expression against the input file. Nothing is written to "
            "the real output path.")
        self.check_button.clicked.connect(self.check)
        buttons.addWidget(self.check_button)
        buttons.addStretch(1)

        self.box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        self.box.accepted.connect(self.accept)
        self.box.rejected.connect(self.reject)
        buttons.addWidget(self.box)
        layout.addLayout(buttons)

    def _load_variables(self) -> None:
        names = read_variables(self.input_path)
        self.variables.clear()
        if not names:
            item = QListWidgetItem(
                "No input file wired up yet" if not self.input_path
                else "Could not read this file's variables")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.variables.addItem(item)
            return
        for name in names:
            self.variables.addItem(QListWidgetItem(name))

    # ------------------------------------------------------------------
    # Editing
    # ------------------------------------------------------------------
    def insert(self, text: str) -> None:
        """Put ``text`` at the caret and give the editor focus back."""
        cursor = self.editor.textCursor()
        cursor.insertText(text)
        # A function is inserted with the caret between its parentheses, which
        # is where the next thing typed has to go.
        if text.endswith("(x)") or "(" in text and text.endswith(")"):
            opening = text.index("(")
            cursor.setPosition(cursor.position() - (len(text) - opening - 1))
            self.editor.setTextCursor(cursor)
        self.editor.setFocus()

    def _insert_reference(self, item: QTreeWidgetItem, _column: int) -> None:
        text = item.data(0, Qt.ItemDataRole.UserRole)
        if text:
            self.insert(text)

    def expression(self) -> str:
        """What the user wrote, as one line if it was written as several.

        CDO takes the whole script as a single comma-parameter, and a newline
        inside argv is passed through literally — harmless, but it makes the
        command history and the session log unreadable. Statements are already
        semicolon-terminated, so joining them loses nothing.
        """
        text = self.editor.toPlainText().strip()
        return re.sub(r"\s*\n\s*", " ", text)

    # ------------------------------------------------------------------
    # Checking
    # ------------------------------------------------------------------
    def check(self) -> bool:
        """Validate against the real input. Returns True when CDO accepted it."""
        ok, message = check_expression(
            self.expression(), self.input_path, self.binary,
            # exprf's script is the same language; check it as expr so the file
            # does not have to exist yet.
            operator="aexpr" if appends(self.operator) else "expr",
        )
        self.status.setText(message)
        self.status.setStyleSheet(
            "color: #3f7d3f;" if ok else "color: #c0392b; font-family: monospace;")
        return ok

    def write_script(self, path: str) -> str:
        """Write the expression to ``path`` for ``exprf``/``aexprf``.

        One statement per line, which is what makes a script file worth having
        over the inline form. Returns the path written.
        """
        statements = [part.strip() for part in self.expression().split(";")]
        body = "\n".join(f"{part};" for part in statements if part)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(body + "\n")
        return path


def edit_expression(parent, operator: str, current: str, input_path: str,
                    binary: str = "cdo") -> str | None:
    """Open the editor on ``current`` and return the new value, or None.

    For ``exprf``/``aexprf`` the value is a path: an existing one is loaded and
    rewritten in place, and a fresh one is written next to the input file, so
    the two forms are the same dialog and the same skill.
    """
    script_path = current if reads_from_file(operator) else ""
    text = current
    if script_path and os.path.exists(script_path):
        try:
            with open(script_path, encoding="utf-8") as handle:
                text = handle.read()
        except OSError:
            logger.debug("Could not read %s", script_path, exc_info=True)
            text = ""

    dialog = ExpressionEditor(parent, operator=operator, expression=text,
                              input_path=input_path, binary=binary)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None

    if not reads_from_file(operator):
        return dialog.expression()

    target = script_path or _default_script_path(input_path)
    try:
        return dialog.write_script(target)
    except OSError:
        logger.warning("Could not write the expression script to %s", target,
                       exc_info=True)
        return None


def _default_script_path(input_path: str) -> str:
    """Where a new ``exprf`` script goes when the field was empty."""
    if input_path:
        base = os.path.splitext(input_path)[0]
        return f"{base}_expr.txt"
    handle, path = tempfile.mkstemp(suffix=".txt", prefix="ncx_expr_")
    os.close(handle)
    return path
