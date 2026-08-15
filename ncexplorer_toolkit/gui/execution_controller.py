"""Runs the selected operator without freezing the window.

The main window used to call the operator straight from ``execute_operation``,
which meant the whole application stopped responding for as long as CDO took —
minutes, on a remapping of a decade of daily data — with no progress and no way
out. This controller owns the other side of that: it starts the run through
:meth:`~..core.nc_integration.NCExplorerIntegration.execute_operator_async`,
keeps the UI honest while it is in flight, and offers a Cancel that actually
stops the process.

Two rules it exists to enforce:

* **The form is locked for exactly as long as the run.** Execute, the parameter
  panel and Cancel are driven from one place, so there is no path — success,
  CDO failure, cancel, timeout, or an exception while starting — that can leave
  the window permanently disabled.
* **Nothing here touches a worker thread.** ``QProcess`` is driven by the event
  loop, so every slot below already runs on the GUI thread; the widget updates
  are safe for that reason and not by accident.

The controller reports outcomes through its own signals rather than updating the
output console itself, so the main window keeps the display logic it already had
and the session panel can listen to the same run.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QProgressBar, QPushButton

from ..core.nc_integration import DEFAULT_COMMAND_TIMEOUT
from ..core.session_log import OperatorRequest
from ..utils.logging_setup import redact_text

logger = logging.getLogger(__name__)

#: Width of the status-bar progress bar. Fixed so it does not fight the cursor
#: readout for space as its text changes.
PROGRESS_WIDTH = 160


class ExecutionController(QObject):
    """One CDO run at a time, driven from the main window's Execute button."""

    #: (OperatorRequest, NCExplorerResult) — CDO ran to completion. The result
    #: says whether it succeeded, exactly as the synchronous API does.
    finished = pyqtSignal(object, object)
    #: (OperatorRequest, message) — the run never produced a usable result:
    #: it could not start, timed out, or died.
    failed = pyqtSignal(object, str)
    #: (OperatorRequest,) — stopped by the user.
    cancelled = pyqtSignal(object)

    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window

        self._worker = None
        self._request: OperatorRequest | None = None

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setToolTip("Stop the running operation")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(PROGRESS_WIDTH)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()

    # ------------------------------------------------------------------
    # Running
    # ------------------------------------------------------------------
    def is_running(self) -> bool:
        return self._worker is not None

    def start(self, request: OperatorRequest, *, timeout: int = DEFAULT_COMMAND_TIMEOUT) -> bool:
        """Begin one run. False if another is already in flight or it failed to start."""
        if self.is_running():
            logger.warning("Ignoring %s: a run is already in flight", request.operator)
            return False

        self._request = request
        self._lock_ui(True)

        try:
            worker = self.main_window.NCExplorer.execute_operator_async(
                request.operator,
                input_files=list(request.input_files),
                output_files=list(request.output_files),
                extra_parameters=list(request.parameters),
                timeout=timeout,
                parent=self,
                # Empty for all but the EOFs section, and the reason it is
                # passed from here rather than looked up in the schema: what the
                # user chose for *this* run is on the request, and the schema
                # only says which variables the operator reads.
                env=request.environment(),
                # CDO's global options — the ``-f nc`` every Import/Export
                # example in the manual opens with. Empty for every run that
                # does not ask, so the argv built for one that does not is
                # byte-identical to what it was before this was passed.
                options=list(request.options),
                # The file the three Formatted input operators read their data
                # from. Empty everywhere else, and empty still closes the
                # child's stdin rather than inheriting a terminal.
                stdin_path=request.stdin_file,
            )
        except Exception as exc:
            # Argument validation and alias creation both happen before the
            # process exists, so this is the one place a start can raise.
            logger.error("Could not start %s: %s", request.operator, exc, exc_info=True)
            self._lock_ui(False)
            self._request = None
            self.failed.emit(request, str(exc))
            return False

        self._worker = worker
        worker.output_line.connect(self._on_output_line)
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(self._on_cancelled)

        self.main_window.handle_status_update(f"Running {request.operator}…")
        logger.info("Started %s", request.operator)
        return True

    def cancel(self) -> None:
        """Stop the running process, if there is one."""
        if self._worker is None:
            return
        self.cancel_button.setEnabled(False)
        self.main_window.handle_status_update("Cancelling…")
        self._worker.cancel()

    def shutdown(self) -> None:
        """Stop any run still going, for the main window's ``closeEvent``.

        Without this the process outlives the window that was watching it, and
        Qt tears the ``QProcess`` down underneath a child that is still running.
        """
        if self._worker is not None:
            self._worker.cancel()

    # ------------------------------------------------------------------
    # Worker slots
    # ------------------------------------------------------------------
    def _on_output_line(self, line: str) -> None:
        # The worker already logs each line under its own logger, so the log
        # dock has it; this is the copy that lands in the output console the
        # user is actually looking at. The output console is a widget and not a
        # logging sink, so the handler filter never sees this copy — it is
        # redacted here instead, by the same function.
        self.main_window.output_console.append(redact_text(line))

    def _on_progress(self, percent: int) -> None:
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(percent)
        self.main_window.handle_progress_update(percent)

    def _on_finished(self, result) -> None:
        request = self._settle()
        if request is not None:
            self.finished.emit(request, result)

    def _on_failed(self, message: str) -> None:
        request = self._settle()
        if request is not None:
            self.failed.emit(request, message)

    def _on_cancelled(self) -> None:
        request = self._settle()
        if request is not None:
            self.cancelled.emit(request)

    # ------------------------------------------------------------------
    # Shared teardown
    # ------------------------------------------------------------------
    def _settle(self) -> OperatorRequest | None:
        """Release the UI and forget the worker. Returns the finished request."""
        request, self._request = self._request, None
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.deleteLater()
        self._lock_ui(False)
        return request

    def _lock_ui(self, running: bool) -> None:
        """Disable the operator form while a run is in flight, and restore it after."""
        window = self.main_window
        window.execute_btn.setEnabled(not running)
        window.params_container.setEnabled(not running)
        self.cancel_button.setEnabled(running)

        if running:
            # Indeterminate until an operator turns out to print a percentage;
            # most of them never do, and a bar stuck at 0% reads as a hang.
            self.progress_bar.setRange(0, 0)
            self.progress_bar.show()
        else:
            self.progress_bar.hide()
            self.progress_bar.setRange(0, 100)
            self.progress_bar.reset()
            window.handle_progress_update(0)
