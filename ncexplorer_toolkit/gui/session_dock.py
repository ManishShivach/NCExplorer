"""Session panel: what was run, exported as something that runs elsewhere.

The panel listens to :class:`~.execution_controller.ExecutionController` rather
than being told about runs by the main window, for the same reason the animation
dock listens to the canvas: a step recorded here can then never disagree with a
step that was actually executed.

What it records is the :class:`~..core.session_log.OperatorRequest` — the
operator and the paths *the user chose*. It deliberately does not build anything
from the resolved ``argv``, which may name temporary symlink aliases that will
not exist tomorrow; ``core/session_log.py`` explains that trap at length.

Replay reuses the ordinary execution controller, one step at a time, so a
replayed step is executed by exactly the code path that ran it the first time —
cancellable, streamed to the console, and recorded like any other run.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QDockWidget, QFileDialog,
    QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu,
    QMessageBox, QPushButton, QScrollArea, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget
)

from ..core import filetypes as ft
from ..core.categories import depends_on_working_directory
from ..core.session_log import (
    CANCELLED, FAILED, OK, STATUS_LABELS, OperatorRequest, SessionLog,
    SessionStep, export_for
)

logger = logging.getLogger(__name__)

COLUMNS = ("#", "Operator", "Inputs", "Output", "Status")

#: Export formats, in the order the button's menu offers them.
EXPORT_FORMATS = (
    ("shell", "Shell script…", "Shell script (*.sh);;All Files (*)", "replay.sh"),
    ("makefile", "Makefile…", "Makefile (Makefile *.mk);;All Files (*)", "Makefile"),
    ("notebook", "Jupyter notebook…", "Notebook (*.ipynb);;All Files (*)", "replay.ipynb"),
)


class SessionDock(QDockWidget):
    """The ordered list of this session's operations, with export and replay."""

    def __init__(self, main_window):
        super().__init__("Session", main_window)
        self.setAllowedAreas(
            Qt.DockWidgetArea.BottomDockWidgetArea | Qt.DockWidgetArea.TopDockWidgetArea
        )
        self.setObjectName("SessionDock")

        self.main_window = main_window
        self.log = SessionLog()
        self.replay = SessionReplay(main_window, self)
        self._cdo_version = ""

        self._build_ui()

        controller = main_window.execution
        controller.finished.connect(self._on_finished)
        controller.failed.connect(self._on_failed)
        controller.cancelled.connect(self._on_cancelled)

        self.replay.step_started.connect(self._on_replay_step)
        self.replay.finished.connect(self._on_replay_finished)
        self.replay.failed.connect(self._on_replay_failed)

        self.hide()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)

        controls = QHBoxLayout()

        self.up_button = QPushButton("↑")
        self.up_button.setToolTip("Move the selected step earlier")
        self.up_button.clicked.connect(lambda: self._move(-1))
        controls.addWidget(self.up_button)

        self.down_button = QPushButton("↓")
        self.down_button.setToolTip("Move the selected step later")
        self.down_button.clicked.connect(lambda: self._move(1))
        controls.addWidget(self.down_button)

        self.delete_button = QPushButton("Delete")
        self.delete_button.setToolTip("Remove the selected step from the session")
        self.delete_button.clicked.connect(self._delete_selected)
        controls.addWidget(self.delete_button)

        self.clear_button = QPushButton("Clear")
        self.clear_button.setToolTip("Forget every recorded step")
        self.clear_button.clicked.connect(self._clear)
        controls.addWidget(self.clear_button)

        controls.addStretch(1)

        self.export_button = QPushButton("Export ▾")
        export_menu = QMenu(self.export_button)
        for fmt, label, file_filter, suggestion in EXPORT_FORMATS:
            action = export_menu.addAction(label)
            action.triggered.connect(
                lambda _checked, f=fmt, g=file_filter, s=suggestion: self._export(f, g, s)
            )
        self.export_button.setMenu(export_menu)
        controls.addWidget(self.export_button)

        self.replay_button = QPushButton("Replay…")
        self.replay_button.setToolTip("Re-run the session against different input files")
        self.replay_button.clicked.connect(self._open_replay_dialog)
        controls.addWidget(self.replay_button)

        layout.addLayout(controls)

        self.status_label = QLabel("No operations recorded yet.")
        self.status_label.setStyleSheet("color: gray; font-size: 10px;")
        layout.addWidget(self.status_label)

        self.setWidget(container)

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------
    def _record(self, request: OperatorRequest, status: str) -> None:
        """Append one executed step, taking its argv from the integration.

        ``_record_command`` runs before the controller emits, on every path
        including cancel and timeout, so the last history entry is always this
        run's — which is where the return code and the resolved command come
        from. They are kept for reference only; nothing exported reads them.

        The one exception is the working directory, and it is an exception
        because for one operator it is not reference information: a ``cmor`` run
        reads ``CWD/.cdocmorinfo`` and writes its tree under a ``drs_root`` that
        defaults to the same place, so a command replayed from elsewhere is a
        different run that looks identical. Copied onto the *request*, which is
        what the exporters read, and only for the operators
        ``categories.CWD_DEPENDENT_OPERATORS`` names — every other step keeps the
        command line it had before this existed.
        """
        history = getattr(self.main_window.NCExplorer, "command_history", [])
        record = history[-1] if history else None
        if record and depends_on_working_directory(request.operator):
            request = replace(request, cwd=record.cwd)
        self.log.record(SessionStep(
            request=request,
            status=status,
            returncode=record.returncode if record else None,
            duration=record.duration if record else 0.0,
            argv=record.argv if record else (),
        ))
        self.refresh()

    def _on_finished(self, request, result) -> None:
        self._record(request, OK if result.success else FAILED)

    def _on_failed(self, request, _message) -> None:
        self._record(request, FAILED)

    def _on_cancelled(self, request) -> None:
        self._record(request, CANCELLED)

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """Rebuild the table from the log."""
        steps = self.log.steps
        self.table.setRowCount(len(steps))

        for row, step in enumerate(steps):
            request = step.request
            inputs = ", ".join(Path(path).name for path in request.input_files)
            outputs = ", ".join(Path(path).name for path in request.output_files)
            if request.is_split:
                outputs += "*"

            cells = (
                str(row + 1),
                request.operator,
                inputs,
                outputs or "—",
                STATUS_LABELS.get(step.status, step.status),
            )
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setToolTip(step.request.command_line())
                self.table.setItem(row, column, item)

        if steps:
            succeeded = sum(1 for step in steps if step.succeeded)
            # Stated outright because the integration's own command history *is*
            # capped, and a user who knows that would reasonably wonder whether
            # this panel silently drops the start of a long session. It does not.
            summary = (f"{len(steps)} step(s) recorded, {succeeded} successful. "
                       "The whole session is kept — nothing is dropped.")
        else:
            summary = "No operations recorded yet."
        self.status_label.setText(summary)
        self.table.resizeColumnToContents(0)
        self.table.resizeColumnToContents(1)
        self.table.resizeColumnToContents(4)

    def _selected_row(self) -> int:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return rows[0].row() if rows else -1

    def _move(self, offset: int) -> None:
        row = self._selected_row()
        if row < 0:
            return
        moved = self.log.move(row, offset)
        self.refresh()
        self.table.selectRow(moved)

    def _delete_selected(self) -> None:
        row = self._selected_row()
        if row < 0:
            self.main_window.statusBar().showMessage("Select a step to delete", 3000)
            return
        self.log.remove(row)
        self.refresh()

    def _clear(self) -> None:
        if not len(self.log):
            return
        confirm = QMessageBox.question(
            self, "Clear session",
            f"Forget all {len(self.log)} recorded step(s)?\n"
            "The files they produced are not touched.",
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self.log.clear()
            self.refresh()

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def cdo_version(self) -> str:
        """The installed CDO's version line, asked for once and remembered."""
        if self._cdo_version:
            return self._cdo_version
        try:
            result = self.main_window.NCExplorer.get_NCExplorer_version()
            text = (result.stdout or result.stderr).strip()
            self._cdo_version = text.splitlines()[0] if text else "unknown"
        except Exception:
            logger.debug("Could not read the engine version", exc_info=True)
            self._cdo_version = "unknown"
        return self._cdo_version

    def _export(self, fmt: str, file_filter: str, suggestion: str) -> None:
        if not len(self.log):
            QMessageBox.information(self, "Export session",
                                    "Nothing has been run yet, so there is nothing to export.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Export session", suggestion, file_filter,
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if not path:
            return

        try:
            text = export_for(fmt, self.log.steps, cdo_version=self.cdo_version())
            Path(path).write_text(text, encoding="utf-8")
            if fmt == "shell":
                # Useless as a script otherwise, and the user has to remember to
                # chmod it at exactly the moment they are least expecting to.
                Path(path).chmod(0o755)
        except OSError as exc:
            logger.error("Could not write %s: %s", path, exc)
            QMessageBox.critical(self, "Export failed", f"Could not write {path}:\n{exc}")
            return

        logger.info("Session exported as %s to %s", fmt, path)
        self.main_window.statusBar().showMessage(f"Session exported to {path}", 5000)

    # ------------------------------------------------------------------
    # Replay
    # ------------------------------------------------------------------
    def _open_replay_dialog(self) -> None:
        runnable = [step for step in self.log.steps if step.succeeded]
        if not runnable:
            QMessageBox.information(
                self, "Replay session",
                "There are no successful steps to replay.")
            return
        if self.main_window.execution.is_running():
            QMessageBox.information(self, "Replay session",
                                    "Wait for the running operation to finish first.")
            return

        dialog = ReplayDialog(self.log, self.main_window)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self.main_window.output_console.append(
            f"▶ Replaying {len(runnable)} step(s) into {dialog.output_directory()}"
        )
        self.replay.run(runnable, dialog.substitutions(), dialog.output_directory())

    def _on_replay_step(self, number: int, total: int, operator: str) -> None:
        self.main_window.statusBar().showMessage(
            f"Replay step {number}/{total}: {operator}", 5000)

    def _on_replay_finished(self, produced: int) -> None:
        self.main_window.output_console.append(
            f"✓ Replay finished — {produced} step(s) re-run")
        QMessageBox.information(self, "Replay finished",
                                f"All {produced} step(s) completed.")

    def _on_replay_failed(self, number: int, operator: str, message: str) -> None:
        self.main_window.output_console.append(
            f"✗ Replay stopped at step {number} ({operator}): {message}")
        QMessageBox.critical(
            self, "Replay failed",
            f"Step {number} ({operator}) failed, so the replay stopped there.\n\n{message}")


class SessionReplay(QObject):
    """Re-runs a recorded chain against substituted paths, one step at a time.

    Sequential on purpose: step *n* usually consumes what step *n-1* wrote, so
    running them concurrently would race on files that do not exist yet. The
    first failure stops the chain — carrying on would feed the next operator a
    file that was never written and produce a second, more confusing error.
    """

    #: (step number, total, operator)
    step_started = pyqtSignal(int, int, str)
    #: (steps completed)
    finished = pyqtSignal(int)
    #: (step number, operator, message)
    failed = pyqtSignal(int, str, str)

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self._queue: list[OperatorRequest] = []
        self._index = 0
        self._connected = False

    def run(self, steps, substitutions: dict[str, str], output_directory: str) -> None:
        """Start replaying ``steps`` with every recorded path remapped."""
        mapping = plan_replacements(steps, substitutions, output_directory)
        self._queue = [
            step.request.with_paths(
                [mapping.get(path, path) for path in step.request.input_files],
                [mapping.get(path, path) for path in step.request.output_files],
            )
            for step in steps
        ]
        self._index = 0
        self._connect()
        self._start_next()

    def _connect(self) -> None:
        if self._connected:
            return
        controller = self.main_window.execution
        controller.finished.connect(self._on_finished)
        controller.failed.connect(self._on_failed)
        controller.cancelled.connect(self._on_cancelled)
        self._connected = True

    def _disconnect(self) -> None:
        if not self._connected:
            return
        controller = self.main_window.execution
        controller.finished.disconnect(self._on_finished)
        controller.failed.disconnect(self._on_failed)
        controller.cancelled.disconnect(self._on_cancelled)
        self._connected = False

    def _start_next(self) -> None:
        if self._index >= len(self._queue):
            self._disconnect()
            self.finished.emit(len(self._queue))
            return

        request = self._queue[self._index]
        self.step_started.emit(self._index + 1, len(self._queue), request.operator)
        self.main_window.output_console.append(
            f"  [{self._index + 1}/{len(self._queue)}] {request.command_line()}")

        if not self.main_window.execution.start(request):
            self._stop(request.operator, "the operation could not be started")

    def _on_finished(self, request, result) -> None:
        if not self._active():
            return
        if not result.success:
            self._stop(request.operator,
                       result.stderr.strip() or "the operation reported a failure")
            return
        self._index += 1
        self._start_next()

    def _on_failed(self, request, message: str) -> None:
        if self._active():
            self._stop(request.operator, message)

    def _on_cancelled(self, request) -> None:
        if self._active():
            self._stop(request.operator, "cancelled")

    def _active(self) -> bool:
        return self._connected and self._index < len(self._queue)

    def _stop(self, operator: str, message: str) -> None:
        number = self._index + 1
        self._disconnect()
        self._queue = []
        self._index = 0
        logger.warning("Replay stopped at step %d (%s): %s", number, operator, message)
        self.failed.emit(number, operator, message)


def plan_replacements(steps, substitutions: dict[str, str],
                      output_directory: str) -> dict[str, str]:
    """Map every recorded path to the one the replay should use.

    The user's input substitutions come first; every output the chain produces
    is then redirected into ``output_directory`` under its own basename. Both
    halves live in one map so a step reading the previous step's output picks up
    the redirected copy automatically, which is what makes the chain hold
    together on new inputs.
    """
    mapping = dict(substitutions)
    claimed = set(mapping.values())
    original_paths = {
        path
        for step in steps
        for path in (*step.request.input_files, *step.request.output_files)
    }

    for step in steps:
        for original in step.request.output_files:
            if original in mapping:
                continue
            target = _free_path(output_directory, original, claimed, original_paths,
                                is_prefix=step.request.is_split)
            mapping[original] = target
            claimed.add(target)
    return mapping


def _free_path(directory: str, original: str, claimed: set, protected: set,
               *, is_prefix: bool = False) -> str:
    """A path under ``directory`` that overwrites nothing.

    Anything already on disk, already claimed by this replay, or named anywhere
    in the recorded session is stepped over — the whole point of a replay is to
    leave the original run's files intact. A split operator's output is a
    *prefix*, which never exists as a file itself, so what has to be free there
    is every name beginning with it.
    """
    source = Path(original)
    candidate = Path(directory) / source.name
    counter = 2
    while (str(candidate) in claimed or str(candidate) in protected
           or _occupied(candidate, is_prefix)):
        candidate = Path(directory) / f"{source.stem}_{counter}{source.suffix}"
        counter += 1
    return str(candidate)


def _occupied(candidate: Path, is_prefix: bool) -> bool:
    if not is_prefix:
        return candidate.exists()
    return any(candidate.parent.glob(f"{candidate.name}*"))


class ReplayDialog(QDialog):
    """Asks which inputs to substitute, and where the new outputs should go."""

    def __init__(self, log: SessionLog, main_window):
        super().__init__(main_window)
        self.setWindowTitle("Replay session")
        self.setMinimumWidth(640)

        self._log = log
        self._fields: dict[str, QLineEdit] = {}
        self._runnable = [step for step in log.steps if step.succeeded]

        layout = QVBoxLayout(self)

        intro = QLabel(
            f"{len(self._runnable)} successful step(s) will be re-run in order. "
            "Substitute any input below; outputs are written to the chosen "
            "folder under new names, so nothing from the original run is "
            "overwritten. Parameters are replayed unchanged — a grid or table "
            "file named in one still points at its original path."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        content = QWidget()
        grid = QGridLayout(content)
        grid.setColumnStretch(1, 1)
        for row, path in enumerate(log.input_paths()):
            grid.addWidget(QLabel(Path(path).name), row, 0)
            field = QLineEdit(path)
            field.setToolTip(path)
            grid.addWidget(field, row, 1)
            browse = QPushButton("Browse")
            browse.clicked.connect(lambda _checked, f=field: self._browse_into(f))
            grid.addWidget(browse, row, 2)
            self._fields[path] = field

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        layout.addWidget(scroll)

        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("Output folder:"))
        self.output_field = QLineEdit(self._default_output_directory())
        output_row.addWidget(self.output_field)
        choose = QPushButton("Browse")
        choose.clicked.connect(self._choose_directory)
        output_row.addWidget(choose)
        layout.addLayout(output_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Replay")
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _default_output_directory(self) -> str:
        """Beside the first recorded output, which is where the user was working."""
        for step in self._runnable:
            for path in step.request.output_files:
                parent = Path(path).parent
                if parent.is_dir():
                    return str(parent)
        return str(Path.home())

    def _browse_into(self, field: QLineEdit) -> None:
        # The same chooser the operator form gives a data input. Written out
        # separately before, and short by three formats CDO reads and this app
        # runs: GRIB2, and the local SERVICE/EXTRA/IEG.
        path, _ = QFileDialog.getOpenFileName(
            self, "Select replacement input", field.text(),
            ft.dialog_filter(ft.DATA),
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if path:
            field.setText(path)

    def _choose_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "Select output folder", self.output_field.text(),
            QFileDialog.Option.DontUseNativeDialog,
        )
        if directory:
            self.output_field.setText(directory)

    def _validate_and_accept(self) -> None:
        missing = [
            field.text() for field in self._fields.values()
            if field.text() and not Path(field.text()).exists()
        ]
        if missing:
            QMessageBox.warning(
                self, "Replay session",
                "These input files do not exist:\n" + "\n".join(missing))
            return

        directory = self.output_field.text().strip()
        if not directory or not Path(directory).is_dir():
            QMessageBox.warning(self, "Replay session",
                                "Choose an existing folder for the new outputs.")
            return
        self.accept()

    def substitutions(self) -> dict[str, str]:
        """Original input path → replacement, for the paths that changed."""
        return {
            original: field.text()
            for original, field in self._fields.items()
            if field.text() and field.text() != original
        }

    def output_directory(self) -> str:
        return self.output_field.text().strip()
