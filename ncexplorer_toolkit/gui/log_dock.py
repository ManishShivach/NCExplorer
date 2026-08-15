"""Collapsible dock showing this session's log records.

The whole point of the dock is to make the log file readable without leaving the
application — including the CDO commands that ``core/nc_integration.py`` records
at DEBUG.

The threading rule is the thing to be careful about. ``GeoCanvas`` runs basemap
fetches and file loads on a thread pool, so log records arrive on non-GUI
threads, and touching a widget from one is undefined behaviour that shows up as
an occasional hard crash rather than as an exception. :class:`QtLogHandler`
therefore never sees a widget: it formats the record and emits a Qt signal, and
the signal-to-slot connection is what hands the text to the GUI thread. Qt makes
a cross-thread connection queued automatically, which is exactly the marshalling
needed here — so the handler must stay ignorant of the widget for the guarantee
to hold.

Records are also kept in a bounded deque, not just appended to the widget. That
is what lets the level filter be re-applied to history instead of only affecting
records that arrive after the combo is changed.
"""

from __future__ import annotations

import html
import logging
from collections import deque
from dataclasses import dataclass

from PyQt6.QtCore import QObject, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QPalette, QTextCursor
from PyQt6.QtWidgets import (
    QComboBox, QDockWidget, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QVBoxLayout, QWidget
)

from ..utils.logging_setup import debug_console_requested, install_redaction

logger = logging.getLogger(__name__)

#: Records retained for re-filtering, and the widget's own block ceiling. Both
#: are needed: one record can span several blocks once a traceback is attached.
MAX_RECORDS = 5000
MAX_BLOCKS = 5000

DOCK_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
DOCK_DATE_FORMAT = "%H:%M:%S"

# Chosen to stay legible against both a light and a dark base; the level name is
# in the text as well, so colour is a convenience and not the only signal.
ERROR_COLOR = "#d05050"
WARNING_COLOR = "#c08000"


@dataclass(frozen=True)
class LevelChoice:
    """One entry of the level filter combo."""

    label: str
    level: int


LEVEL_CHOICES: tuple[LevelChoice, ...] = (
    LevelChoice("Debug", logging.DEBUG),
    LevelChoice("Info", logging.INFO),
    LevelChoice("Warning", logging.WARNING),
    LevelChoice("Error", logging.ERROR),
)


def default_level_index() -> int:
    """Combo entry to start on: Debug under NCEXPLORER_DEBUG, otherwise Info.

    Following the environment variable means a developer run shows the CDO
    commands (logged at DEBUG by core/nc_integration.py) without anyone having
    to touch the combo, while an ordinary run never displays them.
    """
    return 0 if debug_console_requested() else 1


class LogSignals(QObject):
    """Signal carrier for :class:`QtLogHandler`.

    A ``logging.Handler`` cannot inherit from ``QObject`` — both define
    ``emit``, with incompatible meanings — so the handler owns one of these
    instead.
    """

    record_emitted = pyqtSignal(int, str)


class QtLogHandler(logging.Handler):
    """Logging handler that forwards formatted records as a Qt signal.

    Deliberately holds no reference to any widget: see the module docstring.
    """

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.signals = LogSignals()
        self.setFormatter(logging.Formatter(DOCK_FORMAT, datefmt=DOCK_DATE_FORMAT))
        # This dock is a sink like any other, so it carries the same redaction
        # the console and the log file do. It has to be installed on the handler
        # rather than on a logger; utils/logging_setup.py explains why.
        install_redaction(self)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.signals.record_emitted.emit(record.levelno, self.format(record))
        except Exception:
            # A failing log handler must never propagate into the code that
            # logged; logging's own error path reports it on stderr.
            self.handleError(record)


class LogDock(QDockWidget):
    """Bottom dock with the session log, a level filter, Clear and Copy All.

    Hidden on construction — the View menu action is what reveals it.
    """

    def __init__(self, parent=None):
        super().__init__("Log", parent)
        self.setAllowedAreas(
            Qt.DockWidgetArea.BottomDockWidgetArea | Qt.DockWidgetArea.TopDockWidgetArea
        )
        self.setObjectName("LogDock")

        self._records: deque[tuple[int, str]] = deque(maxlen=MAX_RECORDS)
        self._default_index = default_level_index()
        self._threshold = LEVEL_CHOICES[self._default_index].level

        self._build_ui()

        self.handler = QtLogHandler()
        # The cross-thread hop that makes this safe. Qt.AutoConnection resolves
        # to a queued connection whenever the emitting thread is not the GUI one.
        self.handler.signals.record_emitted.connect(self.append_record)
        logging.getLogger().addHandler(self.handler)

        self.hide()

    def _build_ui(self) -> None:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Level:"))

        self.level_combo = QComboBox()
        for choice in LEVEL_CHOICES:
            self.level_combo.addItem(choice.label, choice.level)
        self.level_combo.setCurrentIndex(self._default_index)
        self.level_combo.currentIndexChanged.connect(self._on_level_changed)
        controls.addWidget(self.level_combo)

        controls.addStretch(1)

        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self.clear)
        controls.addWidget(self.clear_button)

        self.copy_button = QPushButton("Copy All")
        self.copy_button.clicked.connect(self.copy_all)
        controls.addWidget(self.copy_button)

        layout.addLayout(controls)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(MAX_BLOCKS)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.view.setPlaceholderText("Log records appear here while the application runs.")
        layout.addWidget(self.view)

        self.setWidget(container)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------
    @pyqtSlot(int, str)
    def append_record(self, levelno: int, text: str) -> None:
        """Store one record and show it if it passes the current filter.

        Runs on the GUI thread even when the record was logged from a worker,
        because of how the handler's signal is connected.
        """
        self._records.append((levelno, text))
        if levelno >= self._threshold:
            self.view.appendHtml(self._html_for(levelno, text))

    def _on_level_changed(self, index: int) -> None:
        self._threshold = self.level_combo.itemData(index) or logging.DEBUG
        self._rerender()

    def clear(self) -> None:
        """Discard the retained records and empty the view."""
        self._records.clear()
        self.view.clear()

    def copy_all(self) -> None:
        """Copy everything currently displayed to the clipboard."""
        self.view.selectAll()
        self.view.copy()
        # Leaving the whole document selected looks like a bug; put the cursor
        # back at the end where new records land.
        self.view.moveCursor(QTextCursor.MoveOperation.End)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _rerender(self) -> None:
        """Rebuild the view from the retained records under the current filter."""
        self.view.clear()
        for levelno, text in self._records:
            if levelno >= self._threshold:
                self.view.appendHtml(self._html_for(levelno, text))

    def _html_for(self, levelno: int, text: str) -> str:
        if levelno >= logging.ERROR:
            color = ERROR_COLOR
        elif levelno >= logging.WARNING:
            color = WARNING_COLOR
        else:
            # Follow the palette rather than hard-coding black, which would be
            # invisible under a dark theme.
            color = self.palette().color(QPalette.ColorRole.Text).name()

        # A record carrying a traceback is multi-line; pre-wrap keeps its
        # indentation instead of collapsing it into one paragraph.
        body = html.escape(text, quote=False).replace("\n", "<br>")
        return f'<span style="color:{color}; white-space:pre-wrap;">{body}</span>'

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------
    def detach(self) -> None:
        """Remove the handler from the root logger.

        Called from the main window's ``closeEvent``: records logged during
        shutdown would otherwise be delivered to a widget Qt is dismantling.
        """
        logging.getLogger().removeHandler(self.handler)
        try:
            self.handler.signals.record_emitted.disconnect(self.append_record)
        except TypeError:
            pass  # already disconnected
        self.handler.close()
