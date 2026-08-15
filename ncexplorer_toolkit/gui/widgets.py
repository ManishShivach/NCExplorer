from PyQt6.QtGui import QAction, QValidator
from PyQt6.QtWidgets import (QHBoxLayout, QLineEdit, QMenu, QToolButton, QWidget)

class MultiSelectEdit(QWidget):
    """A ``multiselect`` parameter: a comma-separated list built from a menu.

    One widget shared by the operator panel and the model builder, because the
    two rendering it differently is exactly what ``audit_operator_surfaces.py``
    exists to catch.

    The value is a single comma-joined string — ``date,time,lon,lat,value`` —
    which is what the schema declares and what ``parameter_tokens`` joins
    straight into ``outputtab,date,time,lon,lat,value``. So the widget's job is
    only to make that string hard to get wrong.

    It is a line edit *plus* a menu rather than a list of checkboxes, and the
    three reasons are all things a checkbox list cannot do:

    * **Order is meaning.** The keynames are the table's columns in the order
      given, so ``date,value`` and ``value,date`` are different tables. A
      checkbox list has no order to offer; appending on click does, and it is
      the order the user clicked.
    * **Repeats are legal.** Nothing stops the same column appearing twice.
    * **``:len`` has to be typeable.** Every keyname takes an optional field
      width — ``name:12`` — which is not a choice from a list.

    The menu is what makes it a real multi-select rather than a text box: a
    keyname can be picked without being spelled, and the eighteen legal ones are
    visible rather than remembered. Typing stays possible for the width suffix,
    and what is typed is still checked by ``invalid_parameter_values`` before
    anything reaches argv — the widget narrows the mistakes, it does not replace
    the check.
    """

    def __init__(self, choices, value="", placeholder="", parent=None):
        super().__init__(parent)
        self._choices = tuple(choices)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._edit = QLineEdit(value)
        self._edit.setPlaceholderText(placeholder)
        layout.addWidget(self._edit)

        self._button = QToolButton()
        self._button.setText("Add…")
        self._button.setToolTip("Append one of the values CDO accepts")
        self._button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self._button)
        for choice in self._choices:
            action = QAction(str(choice), menu)
            action.triggered.connect(
                lambda _checked=False, c=str(choice): self._append(c))
            menu.addAction(action)
        self._button.setMenu(menu)
        layout.addWidget(self._button)

        self.textChanged = self._edit.textChanged

    def _append(self, choice: str) -> None:
        current = self._edit.text().strip().rstrip(",")
        self._edit.setText(f"{current},{choice}" if current else choice)

    def text(self) -> str:
        return self._edit.text()

    def setText(self, value: str) -> None:
        self._edit.setText(value)

    def setToolTip(self, text: str) -> None:      # noqa: N802 - Qt spelling
        super().setToolTip(text)
        self._edit.setToolTip(text)


class QIntValidator(QValidator):
    """Simple integer validator"""
    def validate(self, text, pos):
        if text == "" or text.isdigit():
            return (QValidator.State.Acceptable, text, pos)
        return (QValidator.State.Invalid, text, pos)

class QDoubleValidator(QValidator):
    """Simple float validator"""
    def validate(self, text, pos):
        if text == "":
            return (QValidator.State.Acceptable, text, pos)
        try:
            float(text)
            return (QValidator.State.Acceptable, text, pos)
        except ValueError:
            return (QValidator.State.Invalid, text, pos)

# Add a new validator for double values with an auto option
class QDoubleAutoValidator(QValidator):
    """Float validator that allows 'Auto' (0) value"""
    def validate(self, text, pos):
        if text == "" or text == "0":
            return (QValidator.State.Acceptable, text, pos)
        try:
            float(text)
            return (QValidator.State.Acceptable, text, pos)
        except ValueError:
            return (QValidator.State.Invalid, text, pos)