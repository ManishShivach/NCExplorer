# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""The dialog that points a recorded pipeline at a folder full of files.

Setup and report live in one window rather than in a wizard: the table that
shows what *will* be processed is the same table that shows how each file went,
so nobody has to hold a list of file names in their head across two screens.

The dialog is modal while it runs, which is a deliberate restriction rather than
an oversight. A batch and a hand-started operator run would otherwise share one
:class:`~..core.nc_integration.NCExplorerIntegration` — and its command history,
which the session panel reads the last entry of every time a run settles.
Modal keeps that history unambiguous. A nested event loop is still an event
loop, so the processes and their progress carry on exactly as they would
otherwise; only the window behind is out of reach.

Everything the run itself does lives in ``core/batch.py``. This module chooses
files, shows rows, and forwards Cancel.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QMenu,
    QMessageBox, QProgressBar, QPushButton, QRadioButton, QSpinBox,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget
)

from ..core.batch import (
    COLLISION_LABELS, COLLISION_SUFFIX, DEFAULT_PATTERN, DEFAULT_TEMPLATE,
    DONE, FAILED, PENDING, STATUS_LABELS, TEMPLATE_TOKENS, BatchManifest,
    BatchPlan, BatchRunner, OutputNaming, default_concurrency, discover_inputs,
    ensure_output_extension, export_report_csv, export_report_json,
    pipeline_operator, render_output_name
)
from ..core.project import ProjectError, read_pipeline
from ..core import filetypes as ft

logger = logging.getLogger(__name__)

COLUMNS = ("#", "Input", "Status", "Steps", "Duration", "Output", "Message")

#: Offered in the file picker for hand-picked inputs. The same chooser the
#: operator form's data inputs get, from ``core/filetypes.py``, since a batch
#: runs the same pipeline over the same kind of file — the two lists were
#: written out separately and this one had no SERVICE/EXTRA/IEG entry.
INPUT_FILTER = ft.dialog_filter(ft.DATA)


class BatchDialog(QDialog):
    """Choose a pipeline, a set of files and a destination; then watch it run."""

    def __init__(self, main_window, pipeline=None, source: str = ""):
        super().__init__(main_window)
        self.setWindowTitle("Batch Process")
        self.setMinimumSize(900, 640)

        self.main_window = main_window
        self._runner: BatchRunner | None = None
        # A caller may supply the steps — the model builder hands over a chain
        # that was drawn rather than recorded, which is the same list of requests
        # either way and needs nothing else to be different.
        self._pipeline = list(pipeline) if pipeline else self._session_pipeline()
        self._pipeline_source = source or "the current session"
        self._explicit_files: list[str] = []

        self._build_ui()
        self._refresh_pipeline_summary()
        self._refresh_inputs()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(self._build_pipeline_box())
        layout.addWidget(self._build_input_box())
        layout.addWidget(self._build_output_box())
        layout.addWidget(self._build_report_box(), 1)
        layout.addLayout(self._build_buttons())

    def _build_pipeline_box(self) -> QGroupBox:
        box = QGroupBox("Pipeline")
        layout = QVBoxLayout(box)

        row = QHBoxLayout()
        self.pipeline_label = QLabel()
        self.pipeline_label.setWordWrap(True)
        row.addWidget(self.pipeline_label, 1)

        self.load_project_button = QPushButton("From a project file…")
        self.load_project_button.setToolTip(
            "Take the pipeline from a saved .ncx project instead of this session"
        )
        self.load_project_button.clicked.connect(self._load_pipeline_from_project)
        row.addWidget(self.load_project_button)

        self.use_session_button = QPushButton("From this session")
        self.use_session_button.clicked.connect(self._use_session_pipeline)
        row.addWidget(self.use_session_button)

        self.use_model_button = QPushButton("From the model")
        self.use_model_button.setToolTip(
            "Take the pipeline the model builder's graph compiles to")
        self.use_model_button.clicked.connect(self._use_model_pipeline)
        row.addWidget(self.use_model_button)
        layout.addLayout(row)

        self.pipeline_list = QListWidget()
        self.pipeline_list.setMaximumHeight(90)
        self.pipeline_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        layout.addWidget(self.pipeline_list)
        return box

    def _build_input_box(self) -> QGroupBox:
        box = QGroupBox("Input files")
        layout = QVBoxLayout(box)

        folder_row = QHBoxLayout()
        self.folder_radio = QRadioButton("Folder:")
        self.folder_radio.setChecked(True)
        self.folder_radio.toggled.connect(self._refresh_inputs)
        folder_row.addWidget(self.folder_radio)

        self.folder_field = QLineEdit()
        self.folder_field.setPlaceholderText("Folder holding the files to process")
        self.folder_field.textChanged.connect(self._refresh_inputs)
        folder_row.addWidget(self.folder_field, 1)

        browse = QPushButton("Browse")
        browse.clicked.connect(self._choose_input_folder)
        folder_row.addWidget(browse)
        layout.addLayout(folder_row)

        options_row = QHBoxLayout()
        options_row.addSpacing(22)
        options_row.addWidget(QLabel("Pattern:"))
        self.pattern_field = QLineEdit(DEFAULT_PATTERN)
        self.pattern_field.setFixedWidth(140)
        self.pattern_field.textChanged.connect(self._refresh_inputs)
        options_row.addWidget(self.pattern_field)

        self.recursive_check = QCheckBox("Include sub-folders")
        self.recursive_check.toggled.connect(self._refresh_inputs)
        options_row.addWidget(self.recursive_check)
        options_row.addStretch(1)
        layout.addLayout(options_row)

        picked_row = QHBoxLayout()
        self.picked_radio = QRadioButton("Hand-picked files")
        self.picked_radio.toggled.connect(self._refresh_inputs)
        picked_row.addWidget(self.picked_radio)

        self.choose_files_button = QPushButton("Choose files…")
        self.choose_files_button.clicked.connect(self._choose_input_files)
        picked_row.addWidget(self.choose_files_button)
        picked_row.addStretch(1)

        self.match_label = QLabel()
        self.match_label.setStyleSheet("color: gray;")
        picked_row.addWidget(self.match_label)
        layout.addLayout(picked_row)
        return box

    def _build_output_box(self) -> QGroupBox:
        box = QGroupBox("Output")
        layout = QVBoxLayout(box)

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Destination:"))
        self.destination_field = QLineEdit()
        self.destination_field.setPlaceholderText("Folder the finished files are written to")
        self.destination_field.textChanged.connect(self._refresh_manifest_note)
        folder_row.addWidget(self.destination_field, 1)
        browse = QPushButton("Browse")
        browse.clicked.connect(self._choose_destination)
        folder_row.addWidget(browse)
        layout.addLayout(folder_row)

        naming_row = QHBoxLayout()
        naming_row.addWidget(QLabel("Name:"))
        self.template_field = QLineEdit(DEFAULT_TEMPLATE)
        self.template_field.setToolTip(
            "Tokens:\n" + "\n".join(f"  {token} — {what}" for token, what in TEMPLATE_TOKENS)
        )
        self.template_field.textChanged.connect(self._refresh_preview)
        naming_row.addWidget(self.template_field, 1)

        naming_row.addWidget(QLabel("If it exists:"))
        self.collision_combo = QComboBox()
        for policy, label in COLLISION_LABELS.items():
            self.collision_combo.addItem(label, policy)
        self.collision_combo.setCurrentIndex(
            self.collision_combo.findData(COLLISION_SUFFIX)
        )
        naming_row.addWidget(self.collision_combo)
        layout.addLayout(naming_row)

        limits_row = QHBoxLayout()
        limits_row.addWidget(QLabel("Files at once:"))
        self.concurrency_spin = QSpinBox()
        self.concurrency_spin.setRange(1, 16)
        self.concurrency_spin.setValue(default_concurrency())
        self.concurrency_spin.setToolTip(
            "How many files are processed in parallel. The steps within one file "
            "always run in order."
        )
        limits_row.addWidget(self.concurrency_spin)

        self.resume_check = QCheckBox("Skip files already recorded in this folder")
        self.resume_check.setToolTip(
            "Reads batch_manifest.json in the destination folder, so an "
            "interrupted batch carries on instead of starting again."
        )
        limits_row.addWidget(self.resume_check)
        limits_row.addStretch(1)

        self.preview_label = QLabel()
        self.preview_label.setStyleSheet("color: gray;")
        limits_row.addWidget(self.preview_label)
        layout.addLayout(limits_row)

        self.manifest_label = QLabel()
        self.manifest_label.setStyleSheet("color: gray;")
        layout.addWidget(self.manifest_label)
        return box

    def _build_report_box(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)

        progress_row = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        progress_row.addWidget(self.progress_bar, 1)

        self.counts_label = QLabel("Nothing has run yet.")
        progress_row.addWidget(self.counts_label)
        layout.addLayout(progress_row)
        return container

    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()

        self.export_button = QPushButton("Export report ▾")
        self.export_button.setEnabled(False)
        menu = QMenu(self.export_button)
        menu.addAction("CSV…").triggered.connect(lambda: self._export("csv"))
        menu.addAction("JSON…").triggered.connect(lambda: self._export("json"))
        self.export_button.setMenu(menu)
        row.addWidget(self.export_button)
        row.addStretch(1)

        self.run_button = QPushButton("Run batch")
        self.run_button.setDefault(True)
        self.run_button.clicked.connect(self._start)
        row.addWidget(self.run_button)

        self.cancel_button = QPushButton("Cancel run")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel)
        row.addWidget(self.cancel_button)

        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.reject)
        row.addWidget(self.close_button)
        return row

    # ------------------------------------------------------------------
    # Pipeline
    # ------------------------------------------------------------------
    def _session_pipeline(self) -> list:
        """The successful steps of this session, in order.

        Failed and cancelled steps are left out for the same reason the replay
        dialog leaves them out: a step that did not work once is not a recipe,
        and running it against a hundred files would produce a hundred copies of
        the same error.
        """
        dock = getattr(self.main_window, "session_dock", None)
        if dock is None:
            return []
        return [step.request for step in dock.log.steps if step.succeeded]

    def _use_session_pipeline(self) -> None:
        self._pipeline = self._session_pipeline()
        self._pipeline_source = "the current session"
        self._refresh_pipeline_summary()

    def _use_model_pipeline(self) -> None:
        """Take the compiled graph out of the model builder."""
        builder = getattr(self.main_window, "model_builder", None)
        pipeline = builder.compiled_pipeline() if builder is not None else []
        if not pipeline:
            QMessageBox.information(
                self, "Batch Process",
                "The model builder has nothing to run, or its model still has "
                "errors to fix.")
            return
        self._pipeline = pipeline
        self._pipeline_source = "the model builder"
        self._refresh_pipeline_summary()

    def _load_pipeline_from_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open project", "", "NCExplorer project (*.ncx);;All Files (*)",
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if not path:
            return
        try:
            self._pipeline = read_pipeline(path)
        except ProjectError as exc:
            QMessageBox.warning(self, "Batch Process", f"Could not read that project:\n{exc}")
            return
        self._pipeline_source = Path(path).name
        self._refresh_pipeline_summary()

    def _refresh_pipeline_summary(self) -> None:
        self.pipeline_list.clear()
        for number, request in enumerate(self._pipeline, start=1):
            self.pipeline_list.addItem(f"{number}. {request.command_line()}")

        if self._pipeline:
            self.pipeline_label.setText(
                f"{len(self._pipeline)} step(s) from {self._pipeline_source}. "
                "Every input file is taken through all of them in order; only the "
                "last step's output is kept."
            )
        else:
            self.pipeline_label.setText(
                f"No steps in {self._pipeline_source}. Run an operator first, or "
                "load a saved project."
            )
        self._refresh_preview()

    # ------------------------------------------------------------------
    # Inputs
    # ------------------------------------------------------------------
    def _choose_input_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Select input folder", self.folder_field.text(),
            QFileDialog.Option.DontUseNativeDialog,
        )
        if folder:
            self.folder_radio.setChecked(True)
            self.folder_field.setText(folder)
            if not self.destination_field.text():
                self.destination_field.setText(str(Path(folder) / "processed"))

    def _choose_destination(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Select destination folder", self.destination_field.text(),
            QFileDialog.Option.DontUseNativeDialog,
        )
        if folder:
            self.destination_field.setText(folder)

    def _choose_input_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select input files", self.folder_field.text(), INPUT_FILTER,
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if paths:
            self._explicit_files = list(paths)
            self.picked_radio.setChecked(True)
            self._refresh_inputs()

    def _sources(self) -> list[str]:
        """The files this batch would process, in order."""
        if self.picked_radio.isChecked():
            return list(self._explicit_files)
        folder = self.folder_field.text().strip()
        if not folder:
            return []
        return discover_inputs(
            folder,
            self.pattern_field.text().strip() or DEFAULT_PATTERN,
            self.recursive_check.isChecked(),
        )

    def _apply_input_mode(self) -> None:
        """Enable the fields belonging to the chosen way of picking files."""
        using_folder = self.folder_radio.isChecked()
        self.folder_field.setEnabled(using_folder)
        self.pattern_field.setEnabled(using_folder)
        self.recursive_check.setEnabled(using_folder)
        self.choose_files_button.setEnabled(not using_folder)

    def _refresh_inputs(self) -> None:
        """Recount the matches and rebuild the table as a preview of the run."""
        self._apply_input_mode()

        sources = self._sources()
        self.match_label.setText(
            f"{len(sources)} file(s) matched" if sources else "no files matched"
        )

        # Shown before the run so the file list can be checked while it is still
        # cheap to change the pattern.
        self.table.setRowCount(len(sources))
        for row, path in enumerate(sources):
            self._set_row(row, [
                str(row + 1), path, STATUS_LABELS[PENDING],
                f"0/{len(self._pipeline)}", "—", "", "",
            ])
        self.table.resizeColumnToContents(0)
        self._refresh_preview()
        self._refresh_manifest_note()

    def _refresh_preview(self) -> None:
        """Show what the first file would be called once processed."""
        sources = self._sources()
        if not sources or not self.template_field.text().strip():
            self.preview_label.setText("")
            return
        rendered = render_output_name(
            self.template_field.text(), sources[0], 1, pipeline_operator(self._pipeline)
        )
        self.preview_label.setText(
            f"First file → {Path(ensure_output_extension(rendered, sources[0])).name}"
        )

    def _refresh_manifest_note(self) -> None:
        """Report what the destination folder already knows about."""
        destination = self.destination_field.text().strip()
        if not destination or not Path(destination).is_dir():
            self.manifest_label.setText("")
            self.resume_check.setEnabled(False)
            return

        manifest = BatchManifest.load(destination)
        done = manifest.completed(self._sources())
        self.resume_check.setEnabled(bool(done))
        if done:
            self.manifest_label.setText(
                f"{len(done)} of these file(s) are already recorded in this folder's "
                "batch_manifest.json and can be skipped."
            )
        else:
            self.manifest_label.setText("")

    # ------------------------------------------------------------------
    # Running
    # ------------------------------------------------------------------
    def _validation_error(self, sources: list[str], destination: str) -> str:
        if not self._pipeline:
            return "There are no pipeline steps to apply."
        if not sources:
            return "No input files matched."
        if not destination:
            return "Choose a destination folder."
        if not self.template_field.text().strip():
            return "Give the output files a name template."
        if getattr(self.main_window, "execution", None) is not None \
                and self.main_window.execution.is_running():
            return "Wait for the running operation to finish first."
        return ""

    def _start(self) -> None:
        sources = self._sources()
        destination = self.destination_field.text().strip()

        problem = self._validation_error(sources, destination)
        if problem:
            QMessageBox.information(self, "Batch Process", problem)
            return

        try:
            os.makedirs(destination, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "Batch Process",
                                f"Could not use that destination folder:\n{exc}")
            return

        plan = BatchPlan(
            pipeline=tuple(self._pipeline),
            sources=tuple(sources),
            naming=OutputNaming(
                directory=destination,
                template=self.template_field.text().strip(),
                collision=self.collision_combo.currentData(),
            ),
            concurrency=self.concurrency_spin.value(),
            skip_completed=self.resume_check.isChecked() and self.resume_check.isEnabled(),
        )

        self._runner = BatchRunner(self.main_window.NCExplorer, plan, self)
        self._runner.job_changed.connect(self._on_job_changed)
        self._runner.progress.connect(self._on_progress)
        self._runner.finished.connect(self._on_finished)

        self._lock_setup(True)
        self.progress_bar.setValue(0)
        self.main_window.output_console.append(
            f"▶ Batch: {len(sources)} file(s) × {len(self._pipeline)} step(s) → {destination}"
        )
        self._runner.start()

    def _cancel(self) -> None:
        if self._runner is not None:
            self.cancel_button.setEnabled(False)
            self._runner.cancel()

    def _lock_setup(self, running: bool) -> None:
        """Freeze the settings for the duration of a run."""
        for widget in (self.folder_radio, self.picked_radio, self.folder_field,
                       self.pattern_field, self.recursive_check, self.choose_files_button,
                       self.destination_field, self.template_field, self.collision_combo,
                       self.concurrency_spin, self.resume_check, self.run_button,
                       self.load_project_button, self.use_session_button):
            widget.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        if not running:
            # Restores the folder/hand-picked enabling the blanket loop above
            # has just undone. Deliberately not a full _refresh_inputs(): that
            # rebuilds the table from scratch, which after a run would throw
            # away the report the user is looking at.
            self._apply_input_mode()

    # ------------------------------------------------------------------
    # Runner signals
    # ------------------------------------------------------------------
    def _on_job_changed(self, position: int) -> None:
        if self._runner is None:
            return
        job = self._runner.jobs[position]
        self._set_row(position, [
            str(job.index),
            job.source,
            STATUS_LABELS.get(job.status, job.status),
            f"{job.step}/{self._runner.plan.steps_per_job}",
            f"{job.duration:.1f}s" if job.duration else "—",
            job.output,
            job.message,
        ])

    def _on_progress(self, settled: int, total: int) -> None:
        self.progress_bar.setRange(0, max(1, total))
        self.progress_bar.setValue(settled)
        counts = self._runner.counts() if self._runner else {}
        self.counts_label.setText(
            f"{settled}/{total} — " + ", ".join(
                f"{count} {STATUS_LABELS.get(status, status).lower()}"
                for status, count in sorted(counts.items())
            )
        )

    def _on_finished(self, jobs) -> None:
        self._lock_setup(False)
        self.export_button.setEnabled(True)

        done = sum(1 for job in jobs if job.status == DONE)
        failed = sum(1 for job in jobs if job.status == FAILED)
        self.main_window.output_console.append(
            f"✓ Batch finished — {done} written, {failed} failed, {len(jobs)} total"
        )
        if self._runner is not None:
            # The intermediates were the only reason the temp store existed.
            self._runner.cleanup()
        QMessageBox.information(
            self, "Batch finished",
            f"{done} file(s) written, {failed} failed, out of {len(jobs)}.\n\n"
            "Use “Export report” for the full list."
        )

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------
    def _set_row(self, row: int, values: list[str]) -> None:
        for column, text in enumerate(values):
            item = QTableWidgetItem(text)
            if column in (1, 5):
                item.setToolTip(text)
            self.table.setItem(row, column, item)

    def _export(self, fmt: str) -> None:
        if self._runner is None:
            return
        suggestion = "batch_report.csv" if fmt == "csv" else "batch_report.json"
        file_filter = ("CSV (*.csv);;All Files (*)" if fmt == "csv"
                       else "JSON (*.json);;All Files (*)")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export report",
            str(Path(self._runner.plan.naming.directory) / suggestion), file_filter,
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if not path:
            return

        steps = self._runner.plan.steps_per_job
        text = (export_report_csv(self._runner.jobs, steps) if fmt == "csv"
                else export_report_json(self._runner.jobs, steps))
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", f"Could not write {path}:\n{exc}")
            return
        self.main_window.statusBar().showMessage(f"Batch report written to {path}", 5000)

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------
    def reject(self) -> None:
        """Closing mid-run stops it, once the user has confirmed.

        No ``closeEvent`` to go with it: ``QDialog`` routes a window close
        through ``reject()`` already, so overriding both would ask twice.
        """
        if self._runner is not None and self._runner.is_running():
            confirm = QMessageBox.question(
                self, "Batch Process",
                "A batch is still running. Stop it and close?",
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return
            self._runner.cancel()

        if self._runner is not None:
            self._runner.cleanup()
        super().reject()
