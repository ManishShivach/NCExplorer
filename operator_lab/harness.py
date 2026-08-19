# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""Running the operators and recording what happened.

Every invocation goes through :meth:`NCExplorerIntegration.prepare_operator_run`
rather than straight to ``subprocess``, so a sweep exercises the code path the
app itself uses — the same argument validation, the same temporary-path
aliasing, the same "``diff`` exiting 1 is a result, not a failure" rules. What
the harness keeps for itself is the *waiting*: the app's default timeout is five
minutes, which across 943 operators is a run nobody will sit through, so the
process is driven here and the integration's own ``finalise`` is handed the
outcome.

Driving it here also means bounding it. A sweep runs 943 operators nobody has
vetted, and at least one of them (``spectrum``) emits gigabytes of stderr in
seconds; pipe capture turns that into an unkillable hang. Every operator
therefore streams to disk in its own process group, under a time limit *and* a
size limit, so the worst an operator can do is fail.

An informational operator (``nout == 0``) has no output file; its result is what
it printed. The harness redirects that into a ``.txt`` beside the other outputs,
which is what makes "output file" mean something for all 943 rather than 836.
"""
from __future__ import annotations

import logging
import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from ncexplorer_toolkit.core.expr_reference import reads_from_file
from ncexplorer_toolkit.core.fieldshape import check_fields
from ncexplorer_toolkit.core.categories import (
    OPERATOR_SCHEMA, operator_inputs, operator_syntax,
)

from .profiles import (
    OPERATOR_PARAMETERS, PARAMETER_DEFAULTS, output_kind, parameter_file_content,
    preferred_input_extension, preferred_output_extension, skip_reason,
)
from .samples import SampleSet
from .surfaces import OperatorSurfaces, SurfaceScan, scan

logger = logging.getLogger(__name__)

#: Seconds one operator may take before it is killed and recorded as a timeout.
#: Generous enough for a remap over the sample grid, short enough that a stuck
#: operator costs a run half a minute rather than five.
DEFAULT_TIMEOUT = 60

#: Bytes of stdout+stderr one operator may write before it is killed.
#:
#: Not a theoretical limit. ``cdo spectrum`` against a 730-step sample emits
#: **2.4 GB of stderr in twenty seconds**, and capturing that through a pipe
#: buries the harness: Python accumulates the chunks in memory, stops draining
#: while it joins them, and CDO then blocks forever writing into a full pipe —
#: a hang that no ``timeout=`` can break, because the timeout only bounds the
#: read loop and not the reassembly after it. Streaming to disk and capping the
#: size turns that into an ordinary reportable failure.
MAX_OUTPUT_BYTES = 256 * 1024 * 1024

#: Bytes an operator may write to its *output files* before it is killed.
#:
#: Four times the stream limit, because unlike stderr this is data somebody
#: might want: a remap onto a fine grid legitimately runs to hundreds of
#: megabytes. But it is a limit, because ``cdiwrite`` against the sample writes
#: **972 MB** and exits 0, so nothing in the pass/fail logic would ever notice.
MAX_FILE_BYTES = 256 * 1024 * 1024

#: Total bytes of operator output a finished sweep may leave on disk.
#:
#: A budget rather than a per-file limit, because after the parameter fixes the
#: sizes are a fat middle and not a few whales: 846 outputs totalling 971 MB, of
#: which the largest is 41 MB and some 350 sit between 1 and 2 MB. A per-file
#: threshold either keeps nearly all of it or throws away half the results;
#: deleting the biggest until the rest fits keeps the most inspectable outputs
#: per byte kept — exactly the ones a per-file rule would have taken first.
OUTPUT_BUDGET_BYTES = 400 * 1024 * 1024

#: How much of each stream ends up in the report. The rest stays on disk.
CAPTURE_BYTES = 32 * 1024

#: A kept stderr file larger than this is rewritten with just the captured
#: tail. Nobody scrolls 64 MB of repeated warnings, and a sweep should not
#: leave hundreds of megabytes behind for the few operators that emit them.
SHRINK_ABOVE_BYTES = 1024 * 1024

#: How often the runner looks at a running operator. Small enough that the
#: timeout is honoured closely, large enough to cost nothing.
POLL_SECONDS = 0.05

PASS, FAIL, SKIPPED = "pass", "fail", "skipped"

#: The issue type that means "the installed CDO was not built with this", as
#: opposed to ``profiles.UNTESTABLE``, which means "this harness cannot drive
#: it". The two are both skips and the report keeps them apart: one is fixed by
#: rebuilding CDO, the other by giving the harness better inputs.
UNBUILT = "Not available in this CDO build"

FINISHED, TIMED_OUT, FLOODED, BROKEN = "finished", "timeout", "flooded", "failed"
OVERSIZED = "oversized"


# --------------------------------------------------------------------------
# Failure classification
# --------------------------------------------------------------------------

#: ``(pattern, issue type)``, tried in order against the error text. The type is
#: what makes the Issues sheet sortable: forty rows reading "Abort" are a wall,
#: forty rows split across six causes are a work list. First match wins, so the
#: specific patterns come before the general ones.
_ISSUE_PATTERNS: Sequence = tuple(
    (re.compile(pattern, re.IGNORECASE), label) for pattern, label in (
        (r"more than \d+ MB of output", "Wrote a runaway amount of output"),
        (r"output files? past \d+ MB", "Wrote an oversized output file"),
        (r"timed out|timeout", "Timed out"),
        (r"unknown or unavailable operator", "Operator not installed"),
        # Before the generic "(abort)" below, because first match wins and every
        # one of these arrives as an abort. Both say the same thing — *this*
        # binary cannot run the operator — so both are skips rather than
        # failures. "Unexpected operatorID" is CDO 2.6.0 registering an operator
        # its own dispatch does not handle; it lands on rotuvN, rotuvNorth,
        # projuvLatLon and uvDestag, which are IDs 0-3 of one module.
        (r"support not compiled in|unexpected operatorid",
         "Not available in this CDO build"),
        # A signal death, and its position in this list is the whole point.
        #
        # It sits *after* the capability entry above on purpose. CDO 2.6.3 dies
        # by SIGSEGV on roughly one run in six when a Magics operator aborts at
        # the end of a pipe, and on every one of those runs the stderr line
        # "MAGICS support not compiled in!" is written before the crash and
        # survives it. Classifying by the signal first would file one run in six
        # of the same operator under a different cause than the other five —
        # the report would show contour as both a build gap and a crash, and
        # neither number would be right.
        #
        # Text before signal, therefore: if CDO managed to say what was wrong,
        # that is the finding, and the crash is how the process happened to end.
        # It sits *before* the generic "(abort)" and the "segmentation fault"
        # text pattern below because a signal death is more specific than
        # either, and because nothing writes "segmentation fault" to stderr when
        # the shell is not the one reporting it — the marker below is written by
        # ``explain`` from the return code, which is the only evidence there is.
        (r"killed by SIG", "Killed by a signal"),
        (r"no variables? selected|variable .* not found|unknown variable",
         "Sample lacks the requested variable"),
        (r"no timesteps?|too few timesteps?|number of timesteps",
         "Sample lacks enough time steps"),
        (r"z-?axis|no vertical|level .* not found|unsupported.*level",
         "Sample lacks the vertical levels"),
        (r"grid .* unsupported|unsupported grid|only.*regular grid|cell (corner|center)",
         "Grid unsupported by this operator"),
        (r"open failed|no such file|could not (open|read)", "Input file unreadable"),
        (r"outputfile .* exists|file .* already exists", "Output file already exists"),
        (r"missing (an )?(operator )?(argument|parameter)|too few arguments|"
         r"parameter .* (missing|expected)", "Wrong parameter count"),
        (r"too many arguments", "Wrong parameter count"),
        (r"invalid (argument|parameter|number)|could not (parse|convert)|"
         r"wrong (number|format)", "Parameter value rejected"),
        (r"not implemented|unsupported (operator|method)|function not available",
         "Not implemented in this CDO build"),
        (r"segmentation fault|bus error|core dumped", "Crashed"),
        (r"out of memory|cannot allocate", "Out of memory"),
        (r"\(abort\)", "CDO aborted"),
        (r"\(warning\)", "CDO warning treated as failure"),
    )
)


#: ``operator -> the ``-f`` format its output needs``.
#:
#: The three operators that produce complex numbers cannot write NetCDF
#: classic: CDI stops them with "CDI library does not support complex numbers
#: with NetCDF classic!". That is a property of the *output format*, not of the
#: operator or of this build, and NetCDF4 carries the type — so the sweep asks
#: for NetCDF4 instead of recording a failure that says nothing.
#:
#: The application has the same problem and does not have this fix: running
#: retocomplex from the model builder hits the identical abort, because
#: ``prepare_operator_run`` has no way to ask for a format.
#: ``grid2fourier`` is here for a different reason: NetCDF4 does not make it
#: work, it makes it *honest*. In classic it stops at the CDI complex error and
#: lands in "Unclassified"; with nc4 it gets far enough to say what is really
#: wrong, which is that this CDO has no FFTW.
_OUTPUT_FORMAT_FOR = {
    # Produce complex numbers.
    "retocomplex": "nc4",
    "imtocomplex": "nc4",
    "recttocomplex": "nc4",
    "grid2fourier": "nc4",
    # Read them, and write complex or a complex-typed intermediate back.
    "fourier": "nc4",
    "conj": "nc4",
    "im": "nc4",
    "re": "nc4",
    "complextopol": "nc4",
    "complextorect": "nc4",
}


def _with_output_format(prepared, operator: str):
    """``prepared`` with ``-f <format>`` inserted, when the operator needs one.

    Inserted immediately before the operator token rather than after argv[0]:
    on Windows the command is wrapped in ``wsl``, so argv[0] is not always the
    binary, but the operator token always follows it.
    """
    fmt = _OUTPUT_FORMAT_FOR.get(operator)
    if fmt is None:
        return prepared

    argv = list(prepared.argv)
    for index, token in enumerate(argv):
        if token == operator or token.startswith(f"{operator},"):
            argv[index:index] = ["-f", fmt]
            return replace(prepared, argv=tuple(argv))
    logger.warning("Could not place -f %s for %s in %s", fmt, operator, argv)
    return prepared


def classify(text: str) -> str:
    """A short root-cause label for one failure's error text."""
    for pattern, label in _ISSUE_PATTERNS:
        if pattern.search(text):
            return label
    return "Unclassified" if text.strip() else "Failed with no message"


def signal_name(returncode: Optional[int]) -> str:
    """``SIGSEGV`` for ``-11``, or "" when the run exited on its own terms.

    ``subprocess`` reports a signal death as ``-N``. The lab needs the name for
    the same reason the application does: "-11" is not a finding a reader can
    act on, and a crashed run is a different kind of result from a failed one.
    """
    if returncode is None or returncode >= 0:
        return ""
    try:
        import signal as _signal
        return _signal.Signals(-returncode).name
    except (ImportError, ValueError):
        return f"signal {-returncode}"


def explain(returncode: Optional[int], stdout: str, stderr: str) -> str:
    """The one line that best says why a run failed.

    CDO puts its real complaint on stderr and pads it with progress chatter, so
    the last non-empty line beating the first is deliberate: ``cdo(1) selname
    (Abort): No variables selected!`` arrives after whatever came before it.

    A signal death is reported *alongside* CDO's own message rather than in
    place of it, and that ordering is deliberate. CDO 2.6.3 crashes on about one
    run in six when a Magics operator aborts inside a pipe, and the abort line
    naming the real cause survives the crash — so replacing the message with
    "killed by SIGSEGV" would throw away the finding on exactly those runs.
    Suffixed instead, which also gives :func:`classify` a marker to match on
    while leaving the capability patterns ahead of it able to win.
    """
    killed = signal_name(returncode)
    suffix = f" [killed by {killed}]" if killed else ""

    for stream in (stderr, stdout):
        lines = [line.strip() for line in stream.splitlines() if line.strip()]
        aborts = [line for line in lines if "(Abort)" in line or "Error" in line]
        if aborts:
            return aborts[-1] + suffix
        if lines:
            return lines[-1] + suffix
    if killed:
        return f"killed by {killed} with no message"
    if returncode is None:
        return "the process did not exit normally"
    return f"exit code {returncode} with no message"


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------

@dataclass
class _Outcome:
    """What one process produced, in the shape ``finalise`` expects.

    Deliberately a local mirror of ``core.async_executor.ProcessOutcome``
    rather than an import of it: that module is built on ``QObject``, and the
    command-line sweep should not need Qt loaded to run an operator.
    """

    state: str
    returncode: Optional[int]
    stdout: str
    stderr: str
    duration: float
    detail: str = ""

    @property
    def completed(self) -> bool:
        return self.state == FINISHED


def _kill_tree(process: subprocess.Popen) -> None:
    """Kill the operator and anything it started.

    ``start_new_session`` puts CDO in its own process group, so one signal
    reaches any helper it forked. Killing only the direct child would leave
    those helpers holding the output files open and still running.
    """
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        logger.warning("Process %s ignored SIGKILL", process.pid)


def _run_bounded(argv: Sequence[str], cwd: str, timeout: int,
                 stdout_path: Path, stderr_path: Path,
                 outputs: Sequence[Path] = ()) -> Tuple[str, Optional[int]]:
    """Run ``argv`` with its streams on disk, bounded by time and by size.

    Returns ``(state, returncode)``; the return code is None whenever the
    process was killed rather than allowed to exit.

    Two size limits, not one. The streams get :data:`MAX_OUTPUT_BYTES` because a
    prompting operator can emit tens of megabytes of the same line. The *output
    files* get the far larger :data:`MAX_FILE_BYTES`, because a legitimate
    remapping really can write hundreds of megabytes — but they get a limit at
    all, because without one ``cdiwrite`` wrote 972 MB and was recorded as a
    pass.

    ``stdin`` is ``/dev/null`` on purpose. A dozen operators read a namelist
    from standard input, and inheriting a terminal's stdin makes them wait for
    a human forever; an immediate EOF turns that into a fast, honest error.
    """
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)

    with open(stdout_path, "wb") as out, open(stderr_path, "wb") as err:
        process = subprocess.Popen(
            list(argv), cwd=cwd, stdout=out, stderr=err,
            stdin=subprocess.DEVNULL, start_new_session=True,
        )

        deadline = time.monotonic() + timeout
        state = FINISHED
        while process.poll() is None:
            if time.monotonic() > deadline:
                state = TIMED_OUT
                break
            if _stream_bytes(stdout_path, stderr_path) > MAX_OUTPUT_BYTES:
                state = FLOODED
                break
            if outputs and _stream_bytes(*outputs) > MAX_FILE_BYTES:
                state = OVERSIZED
                break
            time.sleep(POLL_SECONDS)

        if state != FINISHED:
            _kill_tree(process)
            return state, None

    return FINISHED, process.returncode


def prune(outcomes: Sequence["OperatorOutcome"], output_dir: Path,
          budget: int = OUTPUT_BUDGET_BYTES) -> int:
    """Delete the largest operator outputs until the rest fit ``budget``.

    Returns the bytes freed. The same bargain ``SHRINK_ABOVE_BYTES`` strikes for
    stderr, one level up: the report already carries every output's size, path
    and command, so the file itself buys nothing that re-running that one
    operator would not buy back. The sweep that produced this plan left 3.7 GB
    behind, and a sweep that costs 3.7 GB is a sweep people stop running.

    Only files this sweep wrote are touched, and only inside ``output_dir`` —
    never the samples, the parameter files, ``_streams/`` or a report workbook.
    """
    output_dir = Path(output_dir).resolve()

    sized = sorted(
        ((outcome.output_bytes or 0, outcome) for outcome in outcomes
         if outcome.output_path and outcome.output_bytes),
        key=lambda pair: -pair[0],
    )

    total = sum(size for size, _ in sized)
    freed = 0

    for size, outcome in sized:
        if total - freed <= budget:
            break

        # The split family records a description rather than a path
        # ("12 files: foo_split01.nc …"), so its pieces come back from the same
        # glob that counted them in the first place.
        candidate = Path(outcome.output_path)
        targets = ([candidate] if candidate.is_file()
                   else sorted(output_dir.glob(f"{outcome.operator}_split*")))

        for target in targets:
            try:
                resolved = target.resolve()
                if resolved.parent != output_dir or not resolved.is_file():
                    continue
                freed += resolved.stat().st_size
                resolved.unlink()
            except OSError as exc:
                logger.warning("Could not prune %s: %s", target, exc)

    return freed


def _stream_bytes(*paths: Path) -> int:
    total = 0
    for path in paths:
        try:
            total += path.stat().st_size
        except OSError:
            pass
    return total


def _read_bounded(path: Path, *, tail: bool) -> str:
    """At most :data:`CAPTURE_BYTES` of ``path``, decoded leniently.

    ``tail=True`` for stderr, where CDO's actual complaint is the last thing
    printed; the head for stdout, where an informational operator's answer
    starts at the top.
    """
    try:
        size = path.stat().st_size
        with open(path, "rb") as handle:
            if tail and size > CAPTURE_BYTES:
                handle.seek(size - CAPTURE_BYTES)
            raw = handle.read(CAPTURE_BYTES)
    except OSError:
        return ""

    text = raw.decode("utf-8", "replace")
    if size > CAPTURE_BYTES:
        note = f"\n[… {size - CAPTURE_BYTES} more bytes in {path.name}]"
        return (note.lstrip() + text) if tail else (text + note)
    return text


@dataclass
class OperatorOutcome:
    """One row of the report."""

    operator: str
    status: str
    category: str = ""
    description: str = ""
    why: str = ""
    issue_type: str = ""
    signature: str = ""
    syntax: str = ""
    parameters: str = ""
    command: str = ""
    returncode: Optional[int] = None
    duration: float = 0.0
    input_files: str = ""
    output_path: str = ""
    output_bytes: Optional[int] = None
    input_extension: str = ""
    output_extension: str = ""
    output_kind: str = ""
    #: Set when the operator exited 0 having produced an output indistinguishable
    #: from its input. Empty for every run where that was not checked or not
    #: true. Reported, never a failure — see :func:`_passthrough_note`.
    passthrough: str = ""
    surfaces: OperatorSurfaces = field(default_factory=OperatorSurfaces)

    @property
    def failed(self) -> bool:
        return self.status == FAIL


@dataclass
class PreflightCheck:
    """One yes/no answer about the CDO integration itself."""

    name: str
    ok: bool
    detail: str


@dataclass
class RunReport:
    """Everything one sweep produced."""

    outcomes: List[OperatorOutcome]
    preflight: List[PreflightCheck]
    started: datetime
    finished: datetime
    sample_description: str
    output_dir: Path
    cdo_binary: str
    cdo_version: str
    surface_errors: Dict[str, str] = field(default_factory=dict)
    #: Bytes of operator output this sweep wrote, before any pruning.
    bytes_written: int = 0
    #: Of those, how many were deleted again by :func:`prune`.
    bytes_pruned: int = 0

    def count(self, status: str) -> int:
        return sum(1 for outcome in self.outcomes if outcome.status == status)

    @property
    def duration(self) -> float:
        return (self.finished - self.started).total_seconds()

    @property
    def failures(self) -> List[OperatorOutcome]:
        return [outcome for outcome in self.outcomes if outcome.status == FAIL]

    @property
    def unreachable(self) -> List[OperatorOutcome]:
        """Operators the app cannot offer from all three surfaces.

        A separate class of defect from a failed run, and the reason the
        report carries surface columns at all.
        """
        return [
            outcome for outcome in self.outcomes
            if outcome.surfaces.toolbar is not None and not outcome.surfaces.reachable
        ]


# --------------------------------------------------------------------------
# The runner
# --------------------------------------------------------------------------

class OperatorTestRunner:
    """Runs operators against a :class:`~.samples.SampleSet` and records rows.

    Construct once, call :meth:`run_many`. Nothing here touches Qt, so the same
    runner drives the command-line sweep and the GUI's worker thread.
    """

    def __init__(
        self,
        integration,
        samples: SampleSet,
        output_dir: Path,
        *,
        timeout: int = DEFAULT_TIMEOUT,
        parameter_overrides: Optional[Dict[str, str]] = None,
        surface_scan: Optional[SurfaceScan] = None,
        skip_untestable: bool = True,
    ) -> None:
        self.integration = integration
        self.samples = samples
        self.output_dir = Path(output_dir).expanduser()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # Resolved for the same reason the samples are: operators run with
        # CDO's temp directory as their cwd, so a relative output path would
        # write somewhere nobody is looking.
        self.output_dir = self.output_dir.resolve()
        self.timeout = timeout
        self.skip_untestable = skip_untestable

        # The sample's own variable wins over the generator's name, so a run
        # against a real rainfall file selects RAINFALL rather than 'random'.
        self.parameters = dict(PARAMETER_DEFAULTS)
        for key in ("var", "vars", "variable", "variables", "name", "names"):
            self.parameters[key] = samples.variable
        self.parameters["instr"] = f"{samples.variable}={samples.variable}*1;"
        self.parameters["oldname_newname"] = f"{samples.variable},renamed"

        # Kept separately as well as merged: a value the caller typed in has to
        # beat the per-operator table too, and merging alone would let
        # OPERATOR_PARAMETERS silently override what the user asked for.
        self._overrides = dict(parameter_overrides or {})
        self.parameters.update(self._overrides)

        self.surfaces = surface_scan if surface_scan is not None else scan(integration)
        self._param_files = self.output_dir / "_parameter_files"
        self._streams = self.output_dir / "_streams"

    # -- available operators -------------------------------------------

    def available_operators(self) -> List[str]:
        """Every operator this run could attempt, installed ones first."""
        if self.surfaces.installed:
            return sorted(self.surfaces.installed)
        return sorted(OPERATOR_SCHEMA)

    # -- preflight -----------------------------------------------------

    def preflight(self) -> List[PreflightCheck]:
        """Check the integration itself before blaming any single operator.

        A sweep that reports 943 failures because CDO could not be resolved is
        worse than useless, so these run first and their answers head the
        report.
        """
        checks: List[PreflightCheck] = []

        version = self.integration.get_NCExplorer_version()
        first_line = (version.stdout or version.stderr or "").strip().splitlines()
        checks.append(PreflightCheck(
            "CDO binary responds to --version", bool(version.success),
            first_line[0] if first_line else "no output",
        ))

        catalog_size = len(self.surfaces.installed)
        checks.append(PreflightCheck(
            "Operator catalog read from the binary", catalog_size > 0,
            f"{catalog_size} operators from `cdo --operators`",
        ))

        info = self.integration.get_execution_info()
        checks.append(PreflightCheck(
            "Execution method resolved", bool(info.get("NCExplorer_binary")),
            f"{info.get('execution_method', '?')} via {info.get('NCExplorer_binary', '?')}",
        ))

        checks.append(PreflightCheck(
            "Sample inputs readable", self.samples.series.is_file(),
            self.samples.describe(),
        ))

        # A real one-operator round trip: if `copy` cannot write a file, no
        # per-operator result in the report means anything.
        probe = self.output_dir / "_preflight_copy.nc"
        probe.unlink(missing_ok=True)
        outcome = self._execute("copy", [self.samples.series], [probe], [])
        checks.append(PreflightCheck(
            "End-to-end operator round trip (copy)",
            outcome.status == PASS and probe.is_file(),
            outcome.why or f"wrote {probe.name}",
        ))

        for surface, message in self.surfaces.errors.items():
            checks.append(PreflightCheck(f"Surface scan: {surface}", False, message))
        if not self.surfaces.errors:
            checks.append(PreflightCheck(
                "Operator surfaces inspected", True,
                f"toolbar {len(self.surfaces.menus)}, palette {len(self.surfaces.palette)}, "
                f"model builder {len(self.surfaces.builder)}",
            ))

        return checks

    # -- running -------------------------------------------------------

    def run_many(
        self,
        operators: Sequence[str],
        *,
        on_result: Optional[Callable[[int, int, OperatorOutcome], None]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> List[OperatorOutcome]:
        """Run each operator in turn, reporting progress as it goes.

        ``should_stop`` is polled between operators so the GUI's Stop button
        takes effect within one operator rather than at the end of the sweep.
        """
        results: List[OperatorOutcome] = []
        total = len(operators)
        for index, operator in enumerate(operators, start=1):
            if should_stop is not None and should_stop():
                logger.info("Sweep stopped after %d of %d operators", index - 1, total)
                break
            outcome = self.run(operator)
            results.append(outcome)
            if on_result is not None:
                on_result(index, total, outcome)
        return results

    def run(self, operator: str) -> OperatorOutcome:
        """Run one operator and return its row."""
        spec = OPERATOR_SCHEMA.get(operator)
        signature = self.surfaces.installed.get(operator)
        if signature is None and spec is not None:
            signature = (spec.nin, spec.nout)
        if signature is None:
            return self._row(operator, SKIPPED, why="not offered by the installed CDO")

        nin, nout = signature

        reason = skip_reason(operator) if self.skip_untestable else None
        if reason:
            return self._row(operator, SKIPPED, why=reason)

        parameters, parameter_note = self._resolve_parameters(operator)
        if parameters is None:
            return self._row(operator, SKIPPED, why=parameter_note)

        inputs = self.samples.inputs_for(operator, nin)
        outputs = self._output_targets(operator, nout)
        return self._execute(operator, inputs, outputs, parameters)

    # -- internals -----------------------------------------------------

    def _resolve_parameters(self, operator: str):
        """``(values, note)``; values is None when a required one has no default."""
        spec = OPERATOR_SCHEMA.get(operator)
        if spec is None or not spec.params:
            return [], "none required"

        per_operator = OPERATOR_PARAMETERS.get(operator, {})
        values: List[str] = []
        for param in spec.params:
            # Three sources, most specific first: what the caller typed, then
            # the per-operator table, then the shared name-keyed defaults.
            #
            # The last of the three is tracked separately because a *name* in
            # this catalog does not imply a *kind*, and ``weights`` is the case
            # that proves it. It is a file on ``remap``, ``verifyweights`` and
            # ``writeremapscrip`` — the remap weight file, opened with nc_open —
            # and a boolean on the twenty-five Fldstat and Vertstat operators
            # that take area or layer weighting. One shared default cannot serve
            # both, and the shared table is keyed by name alone, so a bool
            # default of "false" reached ``remap`` as a filename and the run
            # failed with "weights: no such file, 'false'".
            #
            # Resolved by kind rather than by adding three per-operator
            # overrides, because the fixture those three actually need is
            # generated at run time (``samples.parameter_files``) and no static
            # table can name it. A per-operator value still wins — that is the
            # point of ``per_operator`` — but the *shared* default is ignored
            # for a file parameter that has a generated fixture waiting.
            shared = self.parameters.get(param.name, "")
            value = self._overrides.get(
                param.name, per_operator.get(param.name, shared))
            from_shared_table = (
                param.name not in self._overrides
                and param.name not in per_operator
                and bool(shared)
            )
            if (param.kind == "file" and from_shared_table
                    and param.name in self.samples.parameter_files):
                value = ""

            # ``exprf``/``aexprf`` declare kind "expression" so the GUI opens the
            # expression editor on them, but the value they carry is still a
            # *path* — the editor writes the script there. Treated as a file
            # here for exactly that reason: without this they lose their
            # synthesised script and skip for "no default", which is not a fact
            # about the operators.
            kind = param.kind
            if kind == "expression" and reads_from_file(operator):
                kind = "file"

            if kind == "file" and not value:
                if param.optional:
                    values.append("")
                    continue
                # A generated NetCDF beats a synthesised text stub wherever one
                # exists: the operators that read weights or a mask open theirs
                # with nc_open and reject anything else.
                generated = self.samples.parameter_files.get(param.name)
                if generated is not None:
                    values.append(str(generated))
                    continue
                self._param_files.mkdir(parents=True, exist_ok=True)
                synthesised = self._param_files / f"{operator}_{param.name}.txt"
                synthesised.write_text(
                    parameter_file_content(param.name, operator))
                value = str(synthesised)
            elif kind == "grid" and not value:
                value = self.parameters.get("grid", "")

            if not value and not param.optional:
                return None, f"no default for the required parameter '{param.name}'"
            values.append(value)

        # A trailing empty optional would become 'operator,a,' — a syntax error
        # rather than an omission.
        while values and values[-1] == "":
            values.pop()
        return values, "defaults"

    def _output_targets(self, operator: str, nout: int) -> List[Path]:
        """Where this operator's output goes, cleared of any earlier run's.

        CDO refuses to overwrite ("Outputfile already exists"), so a second
        sweep into the same directory would fail every operator that passed the
        first time.
        """
        if nout == 0:
            return []
        if nout == -1:
            base = self.output_dir / f"{operator}_split"
            for stale in self.output_dir.glob(f"{operator}_split*"):
                _unlink(stale)
            return [base]

        targets = (
            [self.output_dir / f"{operator}{preferred_output_extension(operator)}"]
            if nout == 1 else
            [self.output_dir / f"{operator}_{index}{preferred_output_extension(operator)}"
             for index in range(1, nout + 1)]
        )
        for target in targets:
            _unlink(target)
        return targets

    def _execute(self, operator: str, inputs: List[Path], outputs: List[Path],
                 parameters: List[str]) -> OperatorOutcome:
        """Prepare, run and finalise one invocation."""
        try:
            prepared = self.integration.prepare_operator_run(
                operator,
                input_files=[str(path) for path in inputs],
                output_files=[str(path) for path in outputs],
                extra_parameters=parameters,
                timeout=self.timeout,
            )
        except Exception as exc:
            # A build-capability refusal is not a finding about the integration,
            # it is the same "this binary cannot do it" the CDO abort below is
            # classified as — reached earlier, which is the whole point of the
            # pre-run gate. Without this branch the gate silently reclassified
            # its own operators from skipped to failed: ``cmor`` and, once the
            # Magics six were added to ``_BUILD_FEATURE_OPERATORS``, all seven.
            #
            # Asked of the integration rather than pattern-matched on the
            # message, so this cannot drift from the refusal it is recognising —
            # ``missing_build_feature`` is the one authority for it, and it is
            # already silent when the ``--config`` probe cannot answer, which is
            # exactly when the run should go ahead and be classified from CDO's
            # own stderr instead.
            unbuilt = ""
            try:
                unbuilt = self.integration.missing_build_feature(operator)
            except Exception:                                   # pragma: no cover
                unbuilt = ""
            if unbuilt:
                return self._row(
                    operator, SKIPPED, why=unbuilt, issue_type=UNBUILT,
                    parameters=",".join(parameters), inputs=inputs,
                    outputs=outputs,
                )
            # Any other ValueError here is the integration rejecting the call —
            # a wrong file count, a bad argument type. That is a genuine finding
            # about the integration, not a reason to skip.
            return self._row(
                operator, FAIL, why=f"{type(exc).__name__}: {exc}",
                issue_type="Rejected by the integration layer",
                parameters=",".join(parameters), inputs=inputs, outputs=outputs,
            )

        prepared = _with_output_format(prepared, operator)
        command = " ".join(prepared.argv)

        # An operator with no output file prints its answer instead, so stdout
        # is redirected straight to the .txt that stands in for one — no copy,
        # and no risk of holding a large answer in memory to write it out.
        capturing_stdout = not outputs
        stdout_path = (
            self.output_dir / f"{operator}{preferred_output_extension(operator)}"
            if capturing_stdout else self._streams / f"{operator}.out"
        )
        stderr_path = self._streams / f"{operator}.err"

        start = time.monotonic()
        try:
            state, returncode = _run_bounded(
                prepared.argv, prepared.cwd, self.timeout, stdout_path, stderr_path,
                outputs=outputs)
            detail = {
                TIMED_OUT: f"timed out after {self.timeout} seconds",
                FLOODED: (f"killed after writing more than "
                          f"{MAX_OUTPUT_BYTES // (1024 * 1024)} MB of output"),
                OVERSIZED: (f"killed after growing its output files past "
                            f"{MAX_FILE_BYTES // (1024 * 1024)} MB"),
            }.get(state, "")
        except Exception as exc:
            state, returncode, detail = BROKEN, None, str(exc)

        outcome = _Outcome(
            state, returncode,
            _read_bounded(stdout_path, tail=False),
            _read_bounded(stderr_path, tail=True),
            time.monotonic() - start, detail=detail,
        )

        result = prepared.finalise(outcome)

        output_path = ""
        if capturing_stdout:
            if stdout_path.is_file() and stdout_path.stat().st_size:
                output_path = str(stdout_path)
            else:
                stdout_path.unlink(missing_ok=True)
        else:
            _unlink(stdout_path)
            if outputs:
                output_path = str(outputs[0])

        # Keep a stderr file only when it says something; 900 empty ones are
        # just clutter around the handful worth opening. And keep it small —
        # a flooding operator leaves 64 MB of near-identical lines, of which
        # the captured tail is the only part anybody will read.
        if stderr_path.is_file():
            if stderr_path.stat().st_size == 0:
                stderr_path.unlink(missing_ok=True)
            elif stderr_path.stat().st_size > SHRINK_ABOVE_BYTES:
                stderr_path.write_text(outcome.stderr)

        why = "" if result.success else (
            outcome.detail or explain(outcome.returncode, outcome.stdout, outcome.stderr)
        )
        issue_type = "" if result.success else classify(why + "\n" + outcome.stderr)

        # "This binary cannot do it" is not a failure of the operator, and
        # counting it as one understates the pass rate of everything that was
        # actually tested. Decided from the error text rather than from a list,
        # so a CDO built with MAGICS attempts these and passes them.
        if issue_type == UNBUILT:
            status = SKIPPED
        else:
            status = PASS if result.success else FAIL

        # Only on a pass: a failed run has already said why, and its output is
        # whatever CDO left behind rather than a claim about the operator.
        passthrough = ""
        if status == PASS:
            passthrough = _passthrough_note(operator, inputs or [], outputs or [])

        return self._row(
            operator, status, why=why,
            issue_type=issue_type,
            parameters=",".join(parameters), command=command,
            returncode=outcome.returncode, duration=result.execution_time or outcome.duration,
            inputs=inputs, outputs=outputs, output_path=output_path,
            passthrough=passthrough,
        )

    def _row(self, operator: str, status: str, *, why: str = "", issue_type: str = "",
             parameters: str = "", command: str = "", returncode: Optional[int] = None,
             duration: float = 0.0, inputs: Optional[List[Path]] = None,
             outputs: Optional[List[Path]] = None, output_path: str = "",
             passthrough: str = "") -> OperatorOutcome:
        spec = OPERATOR_SCHEMA.get(operator)
        signature = self.surfaces.installed.get(operator) or (
            (spec.nin, spec.nout) if spec else (0, 0))

        size: Optional[int] = None
        if output_path and Path(output_path).is_file():
            size = Path(output_path).stat().st_size
        elif outputs and signature[1] == -1:
            pieces = sorted(self.output_dir.glob(f"{operator}_split*"))
            if pieces:
                output_path = f"{len(pieces)} files: {pieces[0].name} …"
                size = sum(piece.stat().st_size for piece in pieces)
        elif outputs:
            # A run whose output is not at the path it was given, because the
            # path turned out to be a *prefix*. ``nout == -1`` above is the
            # static case; this is the one decided by a parameter at runtime —
            # a gen* operator with map3d=true writes <outfile><00001>.nc, so
            # ``genbil.nc`` becomes ``genbil.nc00001.nc`` and the exact-path
            # test above finds nothing.
            #
            # Globbed rather than derived from the parameters because this row
            # is built on every path, including the ones that have no parameter
            # list to consult. Without it the sweep reported map3d runs as a
            # pass with no output at all, which is the same misleading shape
            # ``writes_output_prefix`` fixes in the execution layer.
            first = Path(outputs[0])
            pieces = sorted(first.parent.glob(f"{first.name}?????*"))
            if pieces:
                output_path = f"{len(pieces)} files: {pieces[0].name} …"
                size = sum(piece.stat().st_size for piece in pieces)

        return OperatorOutcome(
            operator=operator,
            status=status,
            category=getattr(spec.category, "value", "") if spec else "",
            description=spec.description if spec else "",
            why=why,
            issue_type=issue_type,
            signature=_signature(*signature),
            syntax=operator_syntax(operator),
            parameters=parameters,
            command=command,
            returncode=returncode,
            duration=round(duration, 3),
            input_files=", ".join(path.name for path in (inputs or [])),
            output_path=output_path,
            output_bytes=size,
            input_extension=preferred_input_extension(operator),
            output_extension=preferred_output_extension(operator),
            output_kind=output_kind(operator),
            passthrough=passthrough,
            surfaces=self.surfaces.get(operator),
        )


def _field_fingerprint(path: Path) -> Optional[tuple]:
    """``(dimension sizes, variable names)`` for one file, or None if unreadable.

    Deliberately coarse. The question is not "are these numbers the same" but
    "is this the same *kind of thing*", and the pass-through this exists to
    catch does not alter either half: the output of ``cdo sp2gp`` over a lonlat
    field is byte-comparable to its input in every respect this reads.

    Dimensions rather than coordinate values because that is what changes when
    a transformation actually happens — a Gaussian ``{lat: 32, lon: 64}``
    becomes a spectral ``{nsp: 253, nc2: 2}`` — and reading two coordinate
    arrays per operator across a 943-operator sweep is a cost with nothing to
    show for it.
    """
    try:
        import xarray as xr
    except ImportError:                     # pragma: no cover - hard dependency
        return None
    try:
        with xr.open_dataset(path, decode_times=False) as dataset:
            return (
                tuple(sorted((str(k), int(v)) for k, v in dataset.sizes.items())),
                tuple(sorted(str(name) for name in dataset.data_vars)),
            )
    except Exception:
        logger.debug("Could not fingerprint %s", path, exc_info=True)
        return None


def _passthrough_note(operator: str, inputs: Sequence[Path],
                      outputs: Sequence[Path]) -> str:
    """Say so when a "successful" run produced its own input back.

    The gap this closes is the one the whole Transformation change is about: an
    operator that exits 0 having copied its input is, on the sweep's pass/fail
    rule, indistinguishable from one that worked. Measured on 2.6.3, ``cdo
    gp2sp`` against a lonlat field warns on stderr, exits 0, and writes the
    input's own variable on the input's own grid — and the sweep recorded that
    as a pass for seven operators.

    **Reported, not failed.** A pass-through is a fact about the *sample*, not
    about the operator: given the right input these operators transform
    correctly, and turning this into a failure would mean a sweep on a machine
    whose sample generation half-failed reports the operators as broken. It also
    cannot distinguish a genuine identity transform from a no-op, and ``cdo
    sp2sp,21`` over a T21 field is a legitimate example of the former.

    **Two conditions, and the first is what makes it precise.** The input must
    have failed its declared shape check *and* the output must be
    indistinguishable from it. Unchanged output on its own is not evidence of
    anything, and scoping on "the operator declares a shape" alone produced two
    false positives on the first run of this:

      * ``fourier`` transforms values along time and returns a complex field of
        the same dimensions — ``cdo -f nc4 fourier,-1`` over the complex sample
        is a correct run whose fingerprint cannot change;
      * ``spcut,1,2`` removes wave numbers from a T21 field and hands back a
        T21 field, so ``nsp`` is 253 before and after.

    Both are legitimate. What is not legitimate is an operator handed a field
    it cannot use, and that is precisely what ``fieldshape.check_fields``
    already answers — so this asks it rather than guessing from the output.

    The pairing also says what a bare shape warning cannot: the warning alone
    means "this input looks wrong", and the two together mean "it was wrong and
    the run did nothing", which is the difference between a caution and a
    finding. In practice this fires when sample generation has quietly
    regressed and an operator has fallen back to the ordinary lonlat series —
    the exact condition that had seven of these recorded as passing.
    """
    if not inputs or not outputs:
        return ""
    if not any(slot.shape for slot in operator_inputs(operator)):
        return ""
    if not check_fields(operator, [str(path) for path in inputs]):
        return ""
    before = _field_fingerprint(Path(inputs[0]))
    if before is None:
        return ""
    after = _field_fingerprint(Path(outputs[0]))
    if after is None or after != before:
        return ""
    dims = ", ".join(f"{name}={size}" for name, size in before[0])
    return (f"exit 0, but the input was not the kind of field this operator "
            f"needs and the output is indistinguishable from it ({dims}; "
            f"variables {', '.join(before[1]) or 'none'}) — nothing was "
            f"transformed")


def _signature(nin: int, nout: int) -> str:
    spell = lambda value: "n" if value == -1 else str(value)  # noqa: E731
    return f"{spell(nin)}→{spell(nout)}"


def _unlink(path: Path) -> None:
    try:
        if path.is_dir():
            return
        path.unlink(missing_ok=True)
    except OSError:
        logger.debug("Could not remove stale output %s", path, exc_info=True)
