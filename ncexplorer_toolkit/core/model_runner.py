"""Running a compiled model, one step at a time, without freezing the window.

This is ``SessionReplay`` (``gui/session_dock.py``) generalised: a queue of
requests driven through the ordinary :class:`~..gui.execution_controller.
ExecutionController`, each step started from the previous step's slot. Driving
the same controller matters more than it looks — a step of a model run is then
executed by exactly the code path that runs a single operator from the form, so
it is cancellable, streamed to the console, redacted, and recorded in the session
log without any of that being reimplemented here.

Two rules it exists to enforce:

* **Sequential.** Step *n* usually consumes what *n-1* wrote, so concurrency
  would race on files that do not exist yet. Independent branches of a graph
  genuinely could run at once — see the note at the foot of this module for why
  that is not what this first version does.
* **The first failure stops everything.** Carrying on would hand the next
  operator a file that was never written, and the second error is always the
  more confusing one to read. The runner reports *which* step failed so the
  editor can tint that node rather than merely saying the run failed.

The module holds no widgets. It is a ``QObject`` because it emits signals, which
is the same latitude ``core/batch.BatchRunner`` takes and for the same reason.
"""

from __future__ import annotations

import logging
from typing import Sequence

from PyQt6.QtCore import QObject, pyqtSignal

from ..utils.logging_setup import redact_text
from ..utils.tempfile_store import TempFileStore
from .model import OPERATOR, ModelGraph, OperatorCatalog
from .nc_integration import stream_notices
from .session_log import OperatorRequest

logger = logging.getLogger(__name__)

#: Tag on every intermediate this runner allocates, so a stray file in the temp
#: directory says which part of the application left it there.
TEMP_TAG = "ncexplorer_model"


class ModelRunner(QObject):
    """Executes one compiled graph through the main window's execution controller."""

    #: (index from 0, total, operator) — that step is starting.
    step_started = pyqtSignal(int, int, str)
    #: (index from 0, NCExplorerResult) — that step finished successfully.
    step_finished = pyqtSignal(int, object)
    #: (index from 0, operator, message) — that step failed; the run has stopped.
    failed = pyqtSignal(int, str, str)
    #: (steps completed) — every step ran.
    finished = pyqtSignal(int)
    #: Stopped by the user.
    cancelled = pyqtSignal()

    def __init__(self, main_window, parent: QObject | None = None):
        super().__init__(parent)
        self.main_window = main_window
        self._temp = TempFileStore(tag=TEMP_TAG)
        self._queue: list[OperatorRequest] = []
        #: Compiled request index → the graph node that produced it, so a
        #: failure can name the box on the canvas rather than a step number.
        self._nodes: list[str] = []
        self._index = 0
        self._connected = False
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    @property
    def requests(self) -> list[OperatorRequest]:
        return list(self._queue)

    def is_running(self) -> bool:
        return self._running

    def node_for(self, index: int) -> str:
        """The graph node id behind one compiled step, or ""."""
        return self._nodes[index] if 0 <= index < len(self._nodes) else ""

    def compile(self, graph: ModelGraph,
                catalog: OperatorCatalog | None = None) -> list[OperatorRequest]:
        """Turn a graph into the requests this runner would execute.

        Allocating from the runner's own temp store here, rather than inside
        :meth:`run`, is what lets the caller compile for a preview, a batch
        handoff or an export and get the same list the run would use.
        """
        self._nodes = [
            node_id for node_id in graph.topological_order()
            if (node := graph.node(node_id)) is not None and node.kind == OPERATOR
        ]
        self._queue = graph.compile(
            lambda suffix: self._temp.new(suffix=suffix), catalog
        )
        return list(self._queue)

    def run(self, requests: Sequence[OperatorRequest] | None = None) -> bool:
        """Start the run. False when it could not begin.

        With no argument it runs whatever :meth:`compile` last produced, which is
        what the editor does: it has already compiled to show the preview, and
        recompiling would allocate a second set of temporary files for no reason.
        """
        if self._running:
            logger.warning("Ignoring run(): this model is already running")
            return False
        if requests is not None:
            self._queue = list(requests)
        if not self._queue:
            return False
        if self.main_window.execution.is_running():
            logger.warning("Ignoring run(): another operation is already in flight")
            return False

        self._index = 0
        self._running = True
        self._connect()
        logger.info("Model run started: %d step(s)", len(self._queue))
        self._start_next()
        return True

    def cancel(self) -> None:
        """Stop the running step and abandon the rest."""
        if not self._running:
            return
        # ExecutionController.cancel() kills the process itself; the controller's
        # cancelled signal then arrives here and settles the run.
        self.main_window.execution.cancel()

    def cleanup(self) -> None:
        """Delete the intermediates. Safe whether or not a run happened."""
        self._temp.cleanup()

    # ------------------------------------------------------------------
    # Stepping
    # ------------------------------------------------------------------
    def _start_next(self) -> None:
        if self._index >= len(self._queue):
            completed = len(self._queue)
            self._settle()
            logger.info("Model run finished: %d step(s)", completed)
            self.finished.emit(completed)
            return

        request = self._queue[self._index]
        self.step_started.emit(self._index, len(self._queue), request.operator)
        self.main_window.output_console.append(
            f"  [{self._index + 1}/{len(self._queue)}] {request.command_line()}")

        if not self.main_window.execution.start(request):
            self._stop(request.operator, "the operation could not be started")

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

    def _active(self) -> bool:
        return self._running and self._index < len(self._queue)

    # ------------------------------------------------------------------
    # Controller slots
    # ------------------------------------------------------------------
    def _on_finished(self, request, result) -> None:
        if not self._active():
            return
        if not result.success:
            # CDO ran and reported a failure of its own; the last non-empty
            # stderr line is the one that says what went wrong.
            lines = [line.strip() for line in (result.stderr or "").splitlines()
                     if line.strip()]
            self._stop(request.operator,
                       lines[-1] if lines else "the operation reported a failure")
            return

        # A step that succeeded can still have done something other than what
        # the graph says. CDO reports that on stdout *or stderr* and exits 0,
        # and in a run of eight steps nobody reads eight of either — so it is
        # lifted out here, named with the step it came from.
        for notice in stream_notices(result.stdout, result.stderr):
            self.main_window.output_console.append(
                f"  ⚠️ {request.operator}: {redact_text(notice)}")

        # The capture used to be written here. It is not any more, and the
        # reason is that this is no longer the only listener: the operator panel
        # now writes it too, from ``handle_operation_finished``, and both are
        # connected to the *same* ``ExecutionController.finished`` signal — so a
        # model step would file its reading twice and say so twice. One writer
        # per path, and for every run that goes through the controller that
        # writer is the panel's. The batch runner has its own path and keeps its
        # own call.
        self.step_finished.emit(self._index, result)
        self._index += 1
        self._start_next()

    def _on_failed(self, request, message: str) -> None:
        if self._active():
            self._stop(request.operator, message)

    def _on_cancelled(self, _request) -> None:
        if not self._active():
            return
        index = self._index
        self._settle()
        logger.info("Model run cancelled at step %d", index + 1)
        self.cancelled.emit()

    def _stop(self, operator: str, message: str) -> None:
        index = self._index
        self._settle()
        logger.warning("Model run stopped at step %d (%s): %s",
                       index + 1, operator, message)
        self.failed.emit(index, operator, message)

    def _settle(self) -> None:
        """Release the controller and forget the queue's position.

        The queue itself is kept: the editor still shows the command preview
        after a run, and the panel's failure highlight is indexed against it.
        """
        self._disconnect()
        self._running = False
        self._index = 0


# ---------------------------------------------------------------------------
# On running independent branches at once
#
# A diamond's two arms share nothing, so both could run while one core sits idle,
# and ``core/batch.default_concurrency`` already caps that sort of thing at four
# with the right reasoning: these processes are bound by reading and writing
# NetCDF far more than by arithmetic.
#
# It is not done here, and the reason is the driver rather than the idea.
# ``ExecutionController`` is explicitly one run at a time — it locks the operator
# form for the duration and refuses a second ``start()`` — so running two steps
# concurrently means not using it, and therefore reimplementing the console
# streaming, the cancel path and the session recording that come with it. Doing
# that correctly is worth more than the wall-clock saving on the graphs a person
# draws by hand, but it is a change to the execution layer rather than an
# addition to this one, and it should be made deliberately.
# ---------------------------------------------------------------------------
