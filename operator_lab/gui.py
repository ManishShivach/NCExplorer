# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""Pick operators, press Run, read the results.

One table does double duty. Before a run it is the picker — every operator with
its category, its signature, the file extensions it prefers and a tick for each
surface of the app that offers it. During the run the same rows fill in with
status and reason, so the thing you selected is the thing you watch, with no
second view to reconcile against the first.

The sweep itself runs on a worker thread. CDO is a blocking subprocess and 943
of them is several minutes; on the GUI thread that is a frozen window and no
way to stop it. :class:`SweepWorker` therefore owns the run and the window owns
only the display, which is also what makes Stop take effect between operators
rather than at the end.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QSpinBox, QSplitter, QTableWidget,
    QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from ncexplorer_toolkit.core.categories import OPERATOR_SCHEMA

from . import default_output_dir, write_report
from .harness import (
    DEFAULT_TIMEOUT, FAIL, PASS, SKIPPED, OperatorOutcome, OperatorTestRunner,
    RunReport, prune,
)
from .profiles import (
    DATA_EXTENSIONS, preferred_input_extension, preferred_output_extension,
    skip_reason,
)
from .samples import SampleError, SampleSet
from .surfaces import scan

logger = logging.getLogger(__name__)

FILE_FILTER = (
    "Climate data files ("
    + " ".join(f"*{extension}" for extension in DATA_EXTENSIONS)
    + ");;All files (*)"
)

#: ``(header, width)``. Everything the picker needs to decide *and* everything
#: the run produces, so the table never has to be swapped for another one.
COLUMNS = (
    ("Operator", 150), ("Category", 130), ("Sig", 55),
    ("Input", 60), ("Output", 65), ("Toolbar", 65), ("Builder", 60),
    ("Status", 80), ("Why", 520),
)
COL_OPERATOR, COL_CATEGORY, COL_SIG, COL_IN, COL_OUT = 0, 1, 2, 3, 4
COL_TOOLBAR, COL_BUILDER, COL_STATUS, COL_WHY = 5, 6, 7, 8

STATUS_COLOURS = {
    PASS: QColor("#1B7F3B"),
    FAIL: QColor("#B3261E"),
    SKIPPED: QColor("#8A6100"),
}


class SweepWorker(QThread):
    """Runs the selected operators off the GUI thread."""

    prepared = pyqtSignal(object)          # list[PreflightCheck]
    progress = pyqtSignal(int, int, object)  # index, total, OperatorOutcome
    completed = pyqtSignal(object)         # RunReport
    failed = pyqtSignal(str)

    def __init__(self, integration, operators: List[str], settings: dict, parent=None):
        super().__init__(parent)
        self.integration = integration
        self.operators = operators
        self.settings = settings
        self._stop = False

    def stop(self) -> None:
        """Ask the sweep to finish after the operator now running."""
        self._stop = True

    def run(self) -> None:  # noqa: D102 — QThread entry point
        try:
            started = datetime.now()
            output_dir = Path(self.settings["output_dir"])
            binary = self.integration.NCExplorer_binary

            files = self.settings.get("sample_files") or []
            samples = (
                SampleSet.from_files(files, binary) if files
                else SampleSet.generate(output_dir / "_samples", binary)
            )

            runner = OperatorTestRunner(
                self.integration, samples, output_dir,
                timeout=self.settings["timeout"],
                surface_scan=self.settings.get("surface_scan"),
                skip_untestable=self.settings["skip_untestable"],
            )

            preflight = runner.preflight()
            self.prepared.emit(preflight)

            outcomes = runner.run_many(
                self.operators,
                on_result=lambda index, total, outcome:
                    self.progress.emit(index, total, outcome),
                should_stop=lambda: self._stop,
            )

            # Same retention as the command line, and for the stronger reason:
            # a window nobody would expect to cost gigabytes should not.
            bytes_written = sum(outcome.output_bytes or 0 for outcome in outcomes)
            bytes_pruned = prune(outcomes, output_dir)

            version = self.integration.get_NCExplorer_version()
            version_lines = (version.stdout or version.stderr or "").strip().splitlines()

            self.completed.emit(RunReport(
                outcomes=outcomes, preflight=preflight, started=started,
                finished=datetime.now(), sample_description=samples.describe(),
                output_dir=output_dir, cdo_binary=binary,
                cdo_version=version_lines[0] if version_lines else "unknown",
                surface_errors=dict(runner.surfaces.errors),
                bytes_written=bytes_written, bytes_pruned=bytes_pruned,
            ))
        except SampleError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            logger.exception("The sweep failed")
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class OperatorLabWindow(QMainWindow):
    """The tester window."""

    def __init__(self, integration, parent=None):
        super().__init__(parent)
        self.integration = integration
        self.surface_scan = scan(integration)
        self.outcomes: Dict[str, OperatorOutcome] = {}
        self.report: Optional[RunReport] = None
        self.worker: Optional[SweepWorker] = None
        self.rows: Dict[str, int] = {}

        self.setWindowTitle("NCExplorer — CDO operator test lab")
        self.resize(1500, 900)
        self._build_ui()
        self._populate()

    # -- construction --------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        layout.addWidget(self._setup_group())
        layout.addWidget(self._filter_bar())

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._table())
        splitter.addWidget(self._detail())
        splitter.setSizes([620, 190])
        layout.addWidget(splitter, stretch=1)

        layout.addWidget(self._run_bar())
        self._build_menu()
        self.statusBar().showMessage("Ready")

    def _setup_group(self) -> QWidget:
        group = QGroupBox("Run settings")
        row = QHBoxLayout(group)

        row.addWidget(QLabel("Inputs:"))
        self.sample_label = QLineEdit()
        self.sample_label.setReadOnly(True)
        self.sample_label.setPlaceholderText(
            "Generated automatically — 2 years of daily data on r36x18")
        row.addWidget(self.sample_label, stretch=3)

        browse = QPushButton("Choose files…")
        browse.setToolTip("Run against your own NetCDF/GRIB files instead of "
                          "generated ones")
        browse.clicked.connect(self._choose_samples)
        row.addWidget(browse)

        clear = QPushButton("Use generated")
        clear.clicked.connect(self._clear_samples)
        row.addWidget(clear)

        row.addSpacing(12)
        row.addWidget(QLabel("Output:"))
        self.output_edit = QLineEdit(str(default_output_dir()))
        row.addWidget(self.output_edit, stretch=2)
        choose_output = QPushButton("…")
        choose_output.setFixedWidth(32)
        choose_output.clicked.connect(self._choose_output)
        row.addWidget(choose_output)

        row.addSpacing(12)
        row.addWidget(QLabel("Timeout:"))
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(5, 600)
        self.timeout_spin.setValue(DEFAULT_TIMEOUT)
        self.timeout_spin.setSuffix(" s")
        row.addWidget(self.timeout_spin)

        self.skip_check = QCheckBox("Skip operators that need stdin or external data")
        self.skip_check.setChecked(True)
        self.skip_check.setToolTip(
            "Unticking this attempts them anyway; most will time out or abort.")
        row.addWidget(self.skip_check)

        self.sample_files: List[Path] = []
        return group

    def _filter_bar(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter operators…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filters)
        row.addWidget(self.search, stretch=2)

        self.category_filter = QComboBox()
        self.category_filter.addItem("All categories")
        for category in sorted({
            spec.category.value for spec in OPERATOR_SCHEMA.values()
        }):
            self.category_filter.addItem(category)
        self.category_filter.currentIndexChanged.connect(self._apply_filters)
        row.addWidget(self.category_filter)

        self.status_filter = QComboBox()
        self.status_filter.addItems(
            ["Any result", "Passed", "Failed", "Skipped", "Not run yet"])
        self.status_filter.currentIndexChanged.connect(self._apply_filters)
        row.addWidget(self.status_filter)

        row.addStretch(1)
        for label, slot in (
            ("Select all", lambda: self._set_checked(True, visible_only=True)),
            ("Select none", lambda: self._set_checked(False, visible_only=False)),
            ("Select failures", self._select_failures),
        ):
            button = QPushButton(label)
            button.clicked.connect(slot)
            row.addWidget(button)

        return bar

    def _table(self) -> QWidget:
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels([header for header, _ in COLUMNS])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(False)
        self.table.itemSelectionChanged.connect(self._show_detail)

        header = self.table.horizontalHeader()
        for index, (_, width) in enumerate(COLUMNS):
            self.table.setColumnWidth(index, width)
        header.setSectionResizeMode(COL_WHY, QHeaderView.ResizeMode.Stretch)
        return self.table

    def _detail(self) -> QWidget:
        group = QGroupBox("Selected operator")
        layout = QVBoxLayout(group)
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setFont(QFont("Menlo", 11))
        layout.addWidget(self.detail)
        return group

    def _run_bar(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)

        self.run_button = QPushButton("▶  Run selected")
        self.run_button.setDefault(True)
        self.run_button.setMinimumHeight(34)
        self.run_button.clicked.connect(self.start_run)
        row.addWidget(self.run_button)

        self.run_all_button = QPushButton("Run all")
        self.run_all_button.setMinimumHeight(34)
        self.run_all_button.clicked.connect(self.start_full_run)
        row.addWidget(self.run_all_button)

        self.stop_button = QPushButton("■  Stop")
        self.stop_button.setMinimumHeight(34)
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_run)
        row.addWidget(self.stop_button)

        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setMinimumHeight(34)
        row.addWidget(self.progress, stretch=1)

        self.tally = QLabel("—")
        self.tally.setMinimumWidth(220)
        row.addWidget(self.tally)

        self.save_button = QPushButton("Save Excel report…")
        self.save_button.setMinimumHeight(34)
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.save_report)
        row.addWidget(self.save_button)

        return bar

    def _build_menu(self) -> None:
        run_menu = self.menuBar().addMenu("&Run")
        for label, shortcut, slot in (
            ("Run selected", "Ctrl+R", self.start_run),
            ("Run all operators", "Ctrl+Shift+R", self.start_full_run),
            ("Stop", "Ctrl+.", self.stop_run),
        ):
            action = QAction(label, self)
            action.setShortcut(shortcut)
            action.triggered.connect(slot)
            run_menu.addAction(action)

        report_menu = self.menuBar().addMenu("&Report")
        save = QAction("Save Excel report…", self)
        save.setShortcut("Ctrl+S")
        save.triggered.connect(self.save_report)
        report_menu.addAction(save)

        reveal = QAction("Open output folder", self)
        reveal.triggered.connect(self._open_output_dir)
        report_menu.addAction(reveal)

    # -- populating ----------------------------------------------------

    def _populate(self) -> None:
        """One row per installed operator, filled in from the surface scan."""
        names = (sorted(self.surface_scan.installed)
                 or sorted(OPERATOR_SCHEMA))
        self.table.setRowCount(len(names))

        for row, name in enumerate(names):
            spec = OPERATOR_SCHEMA.get(name)
            surfaces = self.surface_scan.get(name)
            signature = self.surface_scan.installed.get(name) or (
                (spec.nin, spec.nout) if spec else (1, 1))

            item = QTableWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            if skip_reason(name):
                item.setToolTip(f"Will be skipped: {skip_reason(name)}")
            self.table.setItem(row, COL_OPERATOR, item)

            values = {
                COL_CATEGORY: getattr(spec.category, "value", "") if spec else "",
                COL_SIG: _signature(*signature),
                COL_IN: preferred_input_extension(name),
                COL_OUT: preferred_output_extension(name),
                COL_TOOLBAR: _tick(surfaces.toolbar),
                COL_BUILDER: _tick(surfaces.model_builder),
                COL_STATUS: "",
                COL_WHY: spec.description if spec else "",
            }
            for column, value in values.items():
                cell = QTableWidgetItem(value)
                if column in (COL_SIG, COL_IN, COL_OUT, COL_TOOLBAR,
                              COL_BUILDER, COL_STATUS):
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if column in (COL_TOOLBAR, COL_BUILDER) and value == "no":
                    cell.setForeground(STATUS_COLOURS[FAIL])
                self.table.setItem(row, column, cell)

            self.rows[name] = row

        self.statusBar().showMessage(
            f"{len(names)} operators from {self.integration.NCExplorer_binary} — "
            f"toolbar {len(self.surface_scan.menus)}, "
            f"command palette {len(self.surface_scan.palette)}, "
            f"model builder {len(self.surface_scan.builder)}"
        )

    # -- filtering and selection ---------------------------------------

    def _apply_filters(self) -> None:
        text = self.search.text().strip().lower()
        category = self.category_filter.currentText()
        wanted = self.status_filter.currentText()

        for name, row in self.rows.items():
            outcome = self.outcomes.get(name)
            status = outcome.status if outcome else ""

            visible = (
                (not text or text in name.lower()
                 or text in self.table.item(row, COL_WHY).text().lower())
                and (category == "All categories"
                     or self.table.item(row, COL_CATEGORY).text() == category)
                and _status_matches(wanted, status)
            )
            self.table.setRowHidden(row, not visible)

    def _set_checked(self, checked: bool, *, visible_only: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self.table.rowCount()):
            if visible_only and self.table.isRowHidden(row):
                continue
            self.table.item(row, COL_OPERATOR).setCheckState(state)

    def _select_failures(self) -> None:
        """Tick exactly the operators that failed last time.

        The retry loop this tool exists for: run everything, fix a parameter
        default, run only what broke.
        """
        self._set_checked(False, visible_only=False)
        failures = [name for name, outcome in self.outcomes.items()
                    if outcome.status == FAIL]
        for name in failures:
            self.table.item(self.rows[name], COL_OPERATOR).setCheckState(
                Qt.CheckState.Checked)
        self.statusBar().showMessage(f"Selected {len(failures)} failed operator(s)")

    def selected_operators(self) -> List[str]:
        return [
            name for name, row in sorted(self.rows.items(), key=lambda item: item[1])
            if self.table.item(row, COL_OPERATOR).checkState() == Qt.CheckState.Checked
        ]

    # -- running -------------------------------------------------------

    def start_full_run(self) -> None:
        self._set_checked(True, visible_only=False)
        self.start_run()

    def start_run(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return

        operators = self.selected_operators()
        if not operators:
            QMessageBox.information(
                self, "Nothing selected",
                "Tick at least one operator, or use Run all.")
            return

        output_dir = Path(self.output_edit.text()).expanduser()
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(self, "Output directory",
                                 f"Could not create {output_dir}:\n{exc}")
            return

        for name in operators:
            row = self.rows[name]
            self.table.item(row, COL_STATUS).setText("…")
            self.table.item(row, COL_WHY).setText("")

        self.report = None
        self.progress.setRange(0, len(operators))
        self.progress.setValue(0)
        self._set_running(True)

        self.worker = SweepWorker(self.integration, operators, {
            "output_dir": output_dir,
            "timeout": self.timeout_spin.value(),
            "skip_untestable": self.skip_check.isChecked(),
            "sample_files": self.sample_files,
            "surface_scan": self.surface_scan,
        }, self)
        self.worker.prepared.connect(self._on_prepared)
        self.worker.progress.connect(self._on_progress)
        self.worker.completed.connect(self._on_completed)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()
        self.statusBar().showMessage(f"Preparing to run {len(operators)} operator(s)…")

    def stop_run(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.stop()
            self.stop_button.setEnabled(False)
            self.statusBar().showMessage("Stopping after the current operator…")

    def _set_running(self, running: bool) -> None:
        self.run_button.setEnabled(not running)
        self.run_all_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.save_button.setEnabled(not running and bool(self.outcomes))

    def _on_prepared(self, checks) -> None:
        problems = [check for check in checks if not check.ok]
        if problems:
            self.statusBar().showMessage(
                f"Preflight: {len(problems)} problem(s) — {problems[0].name}: "
                f"{problems[0].detail}")
        else:
            self.statusBar().showMessage("Preflight passed — running")

    def _on_progress(self, index: int, total: int, outcome: OperatorOutcome) -> None:
        self.outcomes[outcome.operator] = outcome
        row = self.rows.get(outcome.operator)
        if row is not None:
            status_item = self.table.item(row, COL_STATUS)
            status_item.setText(outcome.status)
            status_item.setForeground(STATUS_COLOURS.get(outcome.status, QColor()))
            self.table.item(row, COL_WHY).setText(outcome.why or outcome.description)

        self.progress.setValue(index)
        self.progress.setFormat(f"%v / %m — {outcome.operator}")
        self._update_tally()

    def _update_tally(self) -> None:
        counts = {status: 0 for status in (PASS, FAIL, SKIPPED)}
        for outcome in self.outcomes.values():
            counts[outcome.status] = counts.get(outcome.status, 0) + 1
        self.tally.setText(
            f"pass {counts[PASS]}   fail {counts[FAIL]}   skip {counts[SKIPPED]}")

    def _on_completed(self, report: RunReport) -> None:
        self.report = report
        self._set_running(False)
        self.progress.setFormat("%v / %m — done")

        # Written without being asked. A full sweep is twenty-odd minutes, and
        # losing it to a closed window because nobody pressed Save would be a
        # poor trade for the one file this costs. The button remains, for
        # putting a copy somewhere chosen.
        summary = (f"{report.count(PASS)} passed, {report.count(FAIL)} failed, "
                   f"{report.count(SKIPPED)} skipped")
        try:
            written = write_report(report, report.output_dir /
                                   f"cdo_operator_report_{report.started:%Y%m%d_%H%M%S}.xlsx")
            message = (f"Finished in {report.duration:.0f}s — {summary}. "
                       f"Report saved to {written}")
        except Exception as exc:
            logger.exception("Could not auto-save the report")
            message = (f"Finished in {report.duration:.0f}s — {summary}. "
                       f"Could not save automatically ({exc}); use "
                       f"“Save Excel report…”.")

        self.statusBar().showMessage(message)
        self._apply_filters()

    def _on_failed(self, message: str) -> None:
        self._set_running(False)
        self.statusBar().showMessage("The run could not start")
        QMessageBox.critical(self, "Run failed", message)

    # -- report --------------------------------------------------------

    def save_report(self) -> None:
        if self.report is None:
            QMessageBox.information(
                self, "Nothing to save",
                "Run some operators first — the report is written from a "
                "completed run.")
            return

        default = (Path(self.output_edit.text()).expanduser()
                   / f"cdo_operator_report_{datetime.now():%Y%m%d_%H%M%S}.xlsx")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Excel report", str(default), "Excel workbook (*.xlsx)")
        if not path:
            return

        try:
            written = write_report(self.report, Path(path))
        except Exception as exc:
            QMessageBox.critical(self, "Could not save", f"{type(exc).__name__}: {exc}")
            return

        self.statusBar().showMessage(f"Report written to {written}")
        QMessageBox.information(
            self, "Report saved",
            f"Written to:\n{written}\n\n"
            f"{self.report.count(PASS)} passed, {self.report.count(FAIL)} failed, "
            f"{self.report.count(SKIPPED)} skipped.")

    # -- small slots ---------------------------------------------------

    def _choose_samples(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Choose sample input files", str(Path.home()), FILE_FILTER)
        if not paths:
            return
        self.sample_files = [Path(path) for path in paths]
        self.sample_label.setText(", ".join(path.name for path in self.sample_files))
        self.statusBar().showMessage(
            f"{len(self.sample_files)} file(s) will be used instead of generated "
            f"samples; parameters follow the first file's variable name")

    def _clear_samples(self) -> None:
        self.sample_files = []
        self.sample_label.clear()
        self.statusBar().showMessage("Samples will be generated with CDO")

    def _choose_output(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Choose output directory", self.output_edit.text())
        if path:
            self.output_edit.setText(path)

    def _open_output_dir(self) -> None:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        path = Path(self.output_edit.text()).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _show_detail(self) -> None:
        rows = {index.row() for index in self.table.selectedIndexes()}
        if not rows:
            return
        row = next(iter(rows))
        name = self.table.item(row, COL_OPERATOR).text()
        self.detail.setPlainText(self._detail_text(name))

    def _detail_text(self, name: str) -> str:
        spec = OPERATOR_SCHEMA.get(name)
        surfaces = self.surface_scan.get(name)
        outcome = self.outcomes.get(name)

        lines = [f"{name} — {spec.description if spec else 'no description'}", ""]
        if spec:
            from ncexplorer_toolkit.core.categories import operator_syntax
            lines.append(f"  syntax          cdo {name} {operator_syntax(name)}")
            lines.append(f"  category        {spec.category.value}")
        lines += [
            f"  input file      {preferred_input_extension(name)}",
            f"  output file     {preferred_output_extension(name)}",
            "",
            f"  toolbar         {_tick(surfaces.toolbar)}"
            f"  ({surfaces.toolbar_category} — {surfaces.toolbar_placement or 'n/a'})",
            f"  command palette {_tick(surfaces.palette)}",
            f"  model builder   {_tick(surfaces.model_builder)}",
        ]

        reason = skip_reason(name)
        if reason:
            lines += ["", f"  not bulk-testable: {reason}"]

        if outcome is not None:
            lines += [
                "", f"  result          {outcome.status}"
                    f"  ({outcome.duration:.2f}s, exit {outcome.returncode})",
            ]
            if outcome.why:
                lines.append(f"  why             {outcome.why}")
            if outcome.issue_type:
                lines.append(f"  issue type      {outcome.issue_type}")
            if outcome.parameters:
                lines.append(f"  parameters      {outcome.parameters}")
            if outcome.output_path:
                lines.append(f"  output          {outcome.output_path}")
            if outcome.command:
                lines += ["", f"  {outcome.command}"]

        return "\n".join(lines)

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt naming
        """Never leave a CDO sweep running behind a closed window."""
        if self.worker is not None and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(5000)
        super().closeEvent(event)


def _signature(nin: int, nout: int) -> str:
    spell = lambda value: "n" if value == -1 else str(value)  # noqa: E731
    return f"{spell(nin)}→{spell(nout)}"


def _tick(value: Optional[bool]) -> str:
    if value is None:
        return "?"
    return "yes" if value else "no"


def _status_matches(wanted: str, status: str) -> bool:
    if wanted == "Any result":
        return True
    if wanted == "Not run yet":
        return not status
    return status == {"Passed": PASS, "Failed": FAIL, "Skipped": SKIPPED}[wanted]


def launch(argv: Optional[List[str]] = None) -> int:
    """Open the tester window. Returns the Qt exit code."""
    from ncexplorer_toolkit.core.nc_integration import (
        NCExplorerError, create_NCExplorer_integration,
    )

    app = QApplication.instance() or QApplication(argv or sys.argv[:1])
    app.setApplicationName("CDO operator test lab")

    try:
        integration = create_NCExplorer_integration()
    except NCExplorerError as exc:
        QMessageBox.critical(None, "CDO not available", str(exc))
        return 1

    window = OperatorLabWindow(integration)
    window.show()
    return app.exec()
