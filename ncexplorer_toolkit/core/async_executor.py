# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""Non-blocking execution of a single CDO invocation.

``NCExplorerIntegration._execute_command`` blocks on ``subprocess.run``, which
freezes the whole window for however long CDO takes. This module is its
non-blocking counterpart: :class:`CdoProcessWorker` drives one ``QProcess``,
streams the output as it arrives, and can be cancelled.

Four decisions shape it:

* **QProcess rather than a thread wrapped around Popen.** The process is driven
  by the Qt event loop, so the reader slots already run on the GUI thread and
  none of the signals below need cross-thread marshalling. It also supplies
  ``terminate()``/``kill()`` without a second mechanism for aborting.
* **The integration keeps ownership of what a result means.** The worker knows
  nothing about CDO's exit-code quirks, the temporary path aliases or the
  command history: it hands a :class:`ProcessOutcome` to the ``finalise``
  callback that :meth:`~.nc_integration.NCExplorerIntegration.prepare_operator_run`
  supplied, and *that* is what produces the ``NCExplorerResult``. Bypassing it
  would lose ``diff``'s non-zero-means-success rule and leave the history with
  holes in it.
* **Both streams are read, and ``\\r`` breaks a line as well as ``\\n``.** CDO
  2.6.0 writes its running commentary to stdout and its warnings to stderr, and
  the in-place percentage meter some operators emit ends each update with a
  carriage return. Reading one stream, or splitting on newlines alone, means the
  output only surfaces once the run is already over.
* **The process is launched from the event loop, not from ``start()``.** Qt can
  emit ``errorOccurred(FailedToStart)`` synchronously, which would reach a
  caller that has not connected its slots yet. Deferring by one turn makes
  "create, start, then connect" safe.

Exactly one of ``finished`` / ``failed`` / ``cancelled`` is emitted per worker.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, pyqtSignal

from .nc_integration import PreparedRun, run_environment

logger = logging.getLogger(__name__)

#: Dedicated logger for the streamed CDO output, so the log dock shows the
#: process's own words under a name the user recognises rather than under this
#: module's.
cdo_logger = logging.getLogger("cdo")

#: How long a terminated process is given to exit before it is killed outright.
#: Long enough for CDO to close its NetCDF handles, short enough that Cancel
#: still feels immediate.
KILL_GRACE_MS = 800

#: How long :meth:`CdoProcessWorker._launch` waits for the process to exist
#: before writing to its standard input. Covers a fork/exec and nothing else —
#: a process that has not started in this long has failed to start, and
#: ``errorOccurred`` reports that separately.
STARTUP_GRACE_MS = 2000

#: A percentage anywhere in a line of CDO output. Only used as a hint — most
#: operators print no percentage at all, and the caller falls back to
#: indeterminate progress when nothing matches.
PERCENT_PATTERN = re.compile(r"(\d{1,3})\s*%")

# Terminal states of one run.
FINISHED = "finished"
CANCELLED = "cancelled"
TIMEOUT = "timeout"
FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ProcessOutcome:
    """What one CDO process produced, whatever its fate.

    ``returncode`` is ``None`` when the process produced no exit status at all —
    cancelled, timed out, or never started — which is exactly the case the
    ``finalise`` callback must not treat as a result.

    A **crash is not that case.** A process killed by a signal is reported as
    the negative signal number, ``-11`` for SIGSEGV, matching what
    ``subprocess`` gives the synchronous path so that one rule in
    ``_determine_command_success`` covers both. ``state`` is still ``FAILED``
    and ``completed`` is still False — a crashed run is not a result — but the
    signal survives so the user can be told their run crashed rather than
    merely failed. See :meth:`CdoProcessWorker._on_process_finished` for the
    measurement behind reading it out of ``exitCode()``.
    """

    state: str
    returncode: int | None
    stdout: str
    stderr: str
    duration: float
    detail: str = ""

    @property
    def completed(self) -> bool:
        """True only when CDO ran to its own end, successfully or not."""
        return self.state == FINISHED


class CdoProcessWorker(QObject):
    """One CDO invocation running under the event loop.

    Construct from a :class:`~.nc_integration.PreparedRun`, connect the signals,
    and call :meth:`start`. ``progress`` carries a percentage, or ``-1`` when the
    operator gives no way to tell how far along it is.
    """

    progress = pyqtSignal(int)
    output_line = pyqtSignal(str)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, run: PreparedRun, parent: QObject | None = None):
        super().__init__(parent)
        self._run = run

        self._process = QProcess(self)
        self._process.setWorkingDirectory(run.cwd)
        self._process.setProgram(run.argv[0])
        self._process.setArguments(list(run.argv[1:]))

        # The environment, by exactly the rule the blocking path uses — same
        # function, so the two cannot drift about what an override means. A
        # QProcess with no environment set inherits the parent's, which is what
        # ``run_environment`` returning None stands for and what every run did
        # before this existed.
        #
        # QProcessEnvironment rather than ``setEnvironment(["K=V", …])``: the
        # list form is positional and splits on the first "=", which mangles any
        # value containing one — a PATH entry, or a CDO_FILE_SUFFIX somebody
        # typed oddly.
        environment = run_environment(run.env)
        if environment is not None:
            process_environment = QProcessEnvironment()
            for name, value in environment.items():
                process_environment.insert(name, value)
            self._process.setProcessEnvironment(process_environment)
        self._process.readyReadStandardOutput.connect(self._read_stdout)
        self._process.readyReadStandardError.connect(self._read_stderr)
        self._process.finished.connect(self._on_process_finished)
        self._process.errorOccurred.connect(self._on_process_error)

        self._stdout_chunks: list[str] = []
        self._stderr_chunks: list[str] = []
        self._stdout_tail = ""
        self._stderr_tail = ""

        self._started_at = 0.0
        self._settled = False
        self._cancelling = False
        self._timed_out = False

        self._timeout_timer = QTimer(self)
        self._timeout_timer.setSingleShot(True)
        self._timeout_timer.timeout.connect(self._on_timeout)

        self._kill_timer = QTimer(self)
        self._kill_timer.setSingleShot(True)
        self._kill_timer.timeout.connect(self._process.kill)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    @property
    def command(self) -> str:
        """The invocation being run, for status messages and logs."""
        return " ".join(self._run.argv)

    def is_running(self) -> bool:
        """True between :meth:`start` and the terminal signal."""
        return not self._settled

    def start(self) -> None:
        """Begin the run. The process itself starts on the next event-loop turn."""
        self._started_at = time.time()
        if self._run.timeout > 0:
            self._timeout_timer.start(int(self._run.timeout * 1000))
        QTimer.singleShot(0, self._launch)

    def _launch(self) -> None:
        if self._settled:
            # Cancelled between start() and this turn of the event loop.
            return
        logger.debug("Starting the processing engine: %s", self.command)
        self._process.start()

        # Standard input, and closing it is not optional.
        #
        # A QProcess opens a pipe for the child's stdin and holds the write end
        # open until told otherwise. Nothing here ever wrote to it, so for the
        # three operators that read their data from standard input — and for
        # any operator CDO decides to prompt on — the child sat waiting on a
        # pipe that would never deliver and never close, and the run ended at
        # the timeout with nothing to report. That is the asynchronous twin of
        # the missing ``stdin=`` on the blocking path; both are fixed here and
        # in ``_execute_command`` together, because a fix on one path only moves
        # the hang to whichever surface uses the other.
        #
        # ``waitForStarted`` is the one blocking call in this class and it is
        # deliberate: the write channel cannot be closed before the process
        # exists, and its absence is what would otherwise make this silently do
        # nothing. The wait is bounded and only covers the fork/exec, not the
        # run.
        if self._run.stdin_path:
            if self._process.waitForStarted(STARTUP_GRACE_MS):
                try:
                    with open(self._run.stdin_path, "rb") as handle:
                        self._process.write(handle.read())
                except OSError as error:
                    logger.warning("Could not feed %s to the operator: %s",
                                   self._run.stdin_path, error)
            self._process.closeWriteChannel()
        else:
            # No data to send, so the child gets an immediate EOF. Safe to call
            # before the process has started — Qt defers it to the channel.
            self._process.closeWriteChannel()

    def cancel(self) -> None:
        """Ask the process to stop, killing it if it will not.

        Safe to call more than once, and safe to call on a worker whose process
        has not managed to start yet.
        """
        if self._settled or self._cancelling:
            return
        self._cancelling = True
        self._timeout_timer.stop()
        logger.info("Cancelling processing engine run: %s", self.command)

        if self._process.state() == QProcess.ProcessState.NotRunning:
            # Nothing to signal — QProcess would never emit finished(), so the
            # terminal state has to be reached from here.
            self._settle(CANCELLED, None, detail="Cancelled before the process started")
            return

        self._process.terminate()
        self._kill_timer.start(KILL_GRACE_MS)

    # ------------------------------------------------------------------
    # Output streaming
    # ------------------------------------------------------------------
    def _read_stdout(self) -> None:
        text = bytes(self._process.readAllStandardOutput()).decode("utf-8", "replace")
        self._stdout_chunks.append(text)
        self._stdout_tail = self._emit_lines(self._stdout_tail + text)

    def _read_stderr(self) -> None:
        text = bytes(self._process.readAllStandardError()).decode("utf-8", "replace")
        self._stderr_chunks.append(text)
        self._stderr_tail = self._emit_lines(self._stderr_tail + text)

    def _emit_lines(self, buffered: str) -> str:
        """Emit every complete line in ``buffered``; return the incomplete tail.

        The raw text is accumulated separately and never passes through here, so
        ``stdout``/``stderr`` on the result stay byte-for-byte what a blocking
        ``subprocess.run`` would have produced.
        """
        normalised = buffered.replace("\r\n", "\n").replace("\r", "\n")
        lines = normalised.split("\n")
        tail = lines.pop()
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            self.output_line.emit(stripped)
            cdo_logger.info("%s", stripped)
            percent = self._parse_percent(stripped)
            if percent is not None:
                self.progress.emit(percent)
        return tail

    def _flush_tails(self) -> None:
        """Emit whatever was left over when the process exited."""
        self._stdout_tail = self._emit_lines(self._stdout_tail + "\n")
        self._stderr_tail = self._emit_lines(self._stderr_tail + "\n")

    @staticmethod
    def _parse_percent(line: str) -> int | None:
        match = PERCENT_PATTERN.search(line)
        if match is None:
            return None
        value = int(match.group(1))
        return value if 0 <= value <= 100 else None

    # ------------------------------------------------------------------
    # Process events
    # ------------------------------------------------------------------
    def _on_timeout(self) -> None:
        if self._settled:
            return
        logger.error("Processing engine run exceeded its %ss timeout; terminating",
                     self._run.timeout)
        self._timed_out = True
        self._process.terminate()
        self._kill_timer.start(KILL_GRACE_MS)

    def _on_process_error(self, error: QProcess.ProcessError) -> None:
        # Every other error also arrives as finished(), which is where the
        # terminal state is decided; only a failure to start does not.
        if error != QProcess.ProcessError.FailedToStart:
            logger.debug("QProcess error %s on: %s", error, self.command)
            return
        self._timeout_timer.stop()
        self._settle(
            FAILED, None,
            detail=f"Could not start the processing engine: {self._process.errorString()}",
        )

    def _on_process_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        self._timeout_timer.stop()
        self._kill_timer.stop()
        self._flush_tails()

        if self._timed_out:
            self._settle(
                TIMEOUT, None,
                detail=f"Command timed out after {self._run.timeout} seconds",
            )
        elif self._cancelling:
            self._settle(CANCELLED, None, detail="Cancelled by the user")
        elif exit_status == QProcess.ExitStatus.CrashExit:
            # Recorded as ``-signal``, which is the spelling ``subprocess`` uses
            # on the synchronous path — so ``NCExplorerResult.returncode`` means
            # the same thing however a run was launched, and one
            # ``returncode < 0`` test in ``_determine_command_success`` covers
            # both. Passing None here (which is what this did) made the two
            # paths disagree about the evidence for the same event, and lost the
            # signal name the user is shown.
            #
            # Measured on this platform (macOS 26, PyQt6): a process killed by
            # SIGSEGV reports ``exitStatus == CrashExit`` with ``exitCode() ==
            # 11``, and SIGKILL gives 9 — so on a crash Qt puts the *signal
            # number* in the exit code. Qt does not document this, which is why
            # the value is range-checked rather than trusted: 1..64 is the
            # portable signal range, and anything outside it falls back to None
            # rather than inventing a signal that was never raised.
            crashed_by = self._process.exitCode()
            returncode = -crashed_by if 1 <= crashed_by <= 64 else None
            self._settle(
                FAILED, returncode,
                detail=f"The processing engine terminated abnormally: {self._process.errorString()}",
            )
        else:
            self._settle(FINISHED, exit_code)

    # ------------------------------------------------------------------
    # Settling
    # ------------------------------------------------------------------
    def _settle(self, state: str, returncode: int | None, detail: str = "") -> None:
        """Finalise the run and emit its one terminal signal."""
        if self._settled:
            return
        self._settled = True
        self._timeout_timer.stop()
        self._kill_timer.stop()

        outcome = ProcessOutcome(
            state=state,
            returncode=returncode,
            stdout="".join(self._stdout_chunks),
            stderr="".join(self._stderr_chunks),
            duration=time.time() - self._started_at,
            detail=detail,
        )

        try:
            result = self._run.finalise(outcome)
        except Exception as exc:
            # A failure here means the history or the output relocation broke,
            # not the CDO run — report it rather than pretending the run worked.
            logger.error("Finalising the processing engine run failed: %s", exc, exc_info=True)
            self.failed.emit(str(exc))
            return

        if state == CANCELLED:
            self.cancelled.emit()
        elif state == FINISHED:
            # Emitted whether or not CDO succeeded; the result says which, the
            # same way the synchronous API does.
            self.finished.emit(result)
        else:
            self.failed.emit(detail or result.stderr or f"Processing engine run {state}")
