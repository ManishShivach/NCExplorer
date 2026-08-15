"""Applying one recorded pipeline to a folder full of files.

The unit of work here is a *job*: one input file taken through every step of the
pipeline. Three decisions shape the module, and each of them is the opposite of
the obvious first guess:

* **The pipeline is a list of :class:`~.session_log.OperatorRequest`, not a
  format of its own.** That is what the session log already records and what a
  project file already stores, and ``OperatorRequest.with_paths`` exists exactly
  to point one at different files. A second representation would need a
  converter, and the converter would be where the two drift apart.
* **Steps inside a job are sequential, jobs across files are parallel.** Step
  *k* usually consumes what step *k-1* wrote, so running the steps concurrently
  would race on files that do not exist yet — the same reasoning
  ``SessionReplay`` sets out. Different input files share nothing, so those do
  run at once, which is where the wall-clock saving of a batch actually comes
  from.
* **Only the last step's output reaches the destination folder.** Everything a
  job writes on the way there is an intermediate that nobody asked for; those go
  to the temp store and are deleted when the run ends. A destination folder
  holding four intermediates per input would be worse than useless.

Nothing here blocks. Every process is started through
``execute_operator_async``, the same ``QProcess`` path a single run from the
main window uses, so the event loop keeps turning and Cancel can actually
interrupt. That is also why a worker pool needs no locking: ``QProcess`` is
driven by the event loop, so every callback below already runs on the GUI
thread, one at a time.

The module holds no widgets. ``gui/batch_dialog.py`` is the whole of the UI, and
keeping the engine free of it is what lets a batch be driven from a script.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Sequence

from PyQt6.QtCore import QObject, pyqtSignal

from ..utils.logging_setup import redact_text
from ..utils.tempfile_store import TempFileStore
from .nc_integration import (
    DEFAULT_COMMAND_TIMEOUT, OUTPUT_EXTENSIONS, stream_notices,
)
from .session_log import OperatorRequest, write_stdout_capture

logger = logging.getLogger(__name__)

#: Status of one job, in the order a job passes through them.
PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
SKIPPED = "skipped"
CANCELLED = "cancelled"

STATUS_LABELS = {
    PENDING: "Pending",
    RUNNING: "Running",
    DONE: "Done",
    FAILED: "Failed",
    SKIPPED: "Skipped",
    CANCELLED: "Cancelled",
}

#: Terminal states — a job in one of these will not run again this batch.
SETTLED = frozenset({DONE, FAILED, SKIPPED, CANCELLED})

#: What to do when the name a job would write is already taken.
COLLISION_SKIP = "skip"
COLLISION_OVERWRITE = "overwrite"
COLLISION_SUFFIX = "suffix"

COLLISION_LABELS = {
    COLLISION_SKIP: "Skip the file",
    COLLISION_OVERWRITE: "Overwrite it",
    COLLISION_SUFFIX: "Write alongside it (_2, _3, …)",
}

DEFAULT_PATTERN = "*.nc"
DEFAULT_TEMPLATE = "{stem}_processed{ext}"

#: Recorded in the destination folder so an interrupted batch can be resumed
#: rather than started again from the top.
MANIFEST_NAME = "batch_manifest.json"
MANIFEST_VERSION = 1

#: Tokens :func:`render_output_name` understands, with the description the
#: dialog shows beside the template field.
TEMPLATE_TOKENS: tuple[tuple[str, str], ...] = (
    ("{stem}", "input file name without its extension"),
    ("{ext}", "input file extension, dot included"),
    ("{name}", "input file name with its extension"),
    ("{index}", "position in the batch, from 1"),
    ("{operator}", "last operator in the pipeline"),
    ("{date}", "today, as YYYY-MM-DD"),
)


def default_concurrency() -> int:
    """How many jobs to run at once unless told otherwise.

    Capped at four regardless of core count: these processes are bound by
    reading and writing NetCDF files far more than by arithmetic, and a dozen of
    them mostly queue up on the same disk while making the machine unusable.
    """
    return min(4, os.cpu_count() or 1)


# ---------------------------------------------------------------------------
# Input discovery
# ---------------------------------------------------------------------------

def discover_inputs(folder: str, pattern: str = DEFAULT_PATTERN,
                    recursive: bool = False) -> list[str]:
    """Files under ``folder`` matching ``pattern``, sorted, directories excluded.

    Sorted so a batch processes the same files in the same order on every run,
    which is what makes the report comparable between runs and the ``{index}``
    token stable.
    """
    root = Path(folder)
    if not root.is_dir():
        return []
    matches = root.rglob(pattern) if recursive else root.glob(pattern)
    return sorted(str(path) for path in matches if path.is_file())


# ---------------------------------------------------------------------------
# Output naming
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class OutputNaming:
    """Where a job's final file goes and what it is called."""

    directory: str
    template: str = DEFAULT_TEMPLATE
    collision: str = COLLISION_SUFFIX

    def describe(self) -> str:
        """One line for the dialog's summary label."""
        return f"{self.template} → {self.directory} ({COLLISION_LABELS.get(self.collision, self.collision)})"


def render_output_name(template: str, source: str, index: int, operator: str,
                       when: datetime | None = None) -> str:
    """Fill the template's tokens for one input file.

    An unknown token is left as it stands rather than raising: a typo in the
    template should produce a visibly odd file name that the preview shows
    before the run starts, not an exception halfway through a long batch.
    """
    name = Path(source).name
    stem, extension = os.path.splitext(name)
    values = {
        "stem": stem,
        "ext": extension,
        "name": name,
        "index": str(index),
        "operator": operator,
        "date": (when or datetime.now()).strftime("%Y-%m-%d"),
    }
    rendered = template
    for token, value in values.items():
        rendered = rendered.replace("{" + token + "}", value)
    return rendered


def ensure_output_extension(path: str, source: str) -> str:
    """Give ``path`` an extension the engine recognises.

    The output format is inferred from the extension and from nothing else, so a
    name the template left bare or ended in something unrecognised would quietly
    be written as NetCDF4. The input file's own extension is inherited where it
    is usable, which keeps a GRIB batch producing GRIB; ``.nc`` is the fallback.
    This is the rule the operator form applies to a single run, kept identical
    here on purpose.
    """
    stem, extension = os.path.splitext(path)
    if extension.lower() in OUTPUT_EXTENSIONS:
        return path
    source_extension = os.path.splitext(source)[1].lower()
    return stem + (source_extension if source_extension in OUTPUT_EXTENSIONS else ".nc")


def resolve_collision(path: str, policy: str, claimed: set[str]) -> str | None:
    """Apply the collision policy to one planned output path.

    Returns the path to write, or ``None`` when the job should be skipped.
    ``claimed`` holds the paths earlier jobs in this same batch have already
    taken — two inputs can easily render to one name (``{operator}.nc`` for
    every file, say), and without this the second would silently destroy the
    first while the report claimed both succeeded.
    """
    def taken(candidate: str) -> bool:
        return candidate in claimed or os.path.exists(candidate)

    if not taken(path):
        return path
    if policy == COLLISION_OVERWRITE:
        # An existing file is fine to replace, but a path this run has already
        # produced is not: that would throw away work the report calls done.
        return None if path in claimed else path
    if policy == COLLISION_SKIP:
        return None

    stem, extension = os.path.splitext(path)
    counter = 2
    candidate = f"{stem}_{counter}{extension}"
    while taken(candidate):
        counter += 1
        candidate = f"{stem}_{counter}{extension}"
    return candidate


# ---------------------------------------------------------------------------
# Planning one job
# ---------------------------------------------------------------------------

def pipeline_operator(pipeline: Sequence[OperatorRequest]) -> str:
    """The pipeline's last operator, for the ``{operator}`` token."""
    return pipeline[-1].operator if pipeline else ""


def primary_input(pipeline: Sequence[OperatorRequest]) -> str:
    """The recorded path a batch input file stands in for.

    The first input of the first step: the file the pipeline was originally
    built around. Every other recorded input is left alone — a second operand or
    a grid description named in a parameter still refers to its own file, and
    substituting the batch input for those would be nonsense.
    """
    for request in pipeline:
        if request.input_files:
            return request.input_files[0]
    return ""


def final_producer(pipeline: Sequence[OperatorRequest]) -> int:
    """Index of the last step that writes a file, or -1 if none does.

    That step's output is the one the batch keeps; everything before it is an
    intermediate. An info-only pipeline (``ndate``, ``sinfo``) has no producer at
    all, which is a legitimate batch that simply leaves no files behind — unless
    one of its steps captures what it printed, which is a file like any other and
    is the result that batch keeps.
    """
    for index in range(len(pipeline) - 1, -1, -1):
        request = pipeline[index]
        if request.stdout_file:
            return index
        if request.produces_file and request.output_files:
            return index
    return -1


def keeps_text(pipeline: Sequence[OperatorRequest]) -> bool:
    """True when what this pipeline keeps is a captured reading, not a data file."""
    keeper = final_producer(pipeline)
    return keeper >= 0 and bool(pipeline[keeper].stdout_file)


def _sibling_destination(destination: str, request: OperatorRequest,
                         slot: int) -> str:
    """Where output ``slot`` of a multi-output keeper step goes.

    ``/out/tg.nc`` with the eof schema gives ``/out/tg_eofs.nc``: the
    destination's stem plus the slot's declared suffix, in the folder the user
    picked for the batch. The same rule and the same suffixes
    ``ModelGraph._sibling_output`` uses, so a pipeline behaves the same whether
    it was drawn in the model builder or replayed over a folder.

    Returns "" when the operator declares no suffix for the slot, which sends
    the caller to a temporary rather than inventing a name beside the user's.
    """
    from .categories import operator_outputs

    outputs = operator_outputs(request.operator)
    if slot >= len(outputs) or not outputs[slot].suffix:
        return ""
    stem, extension = os.path.splitext(destination)
    return f"{stem}{outputs[slot].suffix}{extension}"


def plan_job(pipeline: Sequence[OperatorRequest], source: str, destination: str,
             temp_path: Callable[[str], str]) -> list[OperatorRequest]:
    """Retarget every step of ``pipeline`` onto one input file.

    Built as a single path→path map, the way ``SessionReplay`` plans a replay,
    because that is what makes a chain hold together: a step reading the
    previous step's recorded output finds that path already redirected to the
    temporary file the previous step now writes, without anything having to
    reason about the chain's shape.
    """
    mapping: dict[str, str] = {}

    original = primary_input(pipeline)
    if original:
        mapping[original] = source

    keeper = final_producer(pipeline)
    for index, request in enumerate(pipeline):
        # A captured stdout is an output too, and the same rule applies to it:
        # the keeper's copy goes to the destination, anything earlier is scratch.
        targets = [*request.output_files]
        if request.stdout_file:
            targets.append(request.stdout_file)
        # Only the *first* output of the keeper step may claim the destination.
        # This used to be "every output of the keeper step", which is correct
        # for the (n|1) shape every pipeline had when it was written and silently
        # wrong for the eleven (n|2) operators: an `eof` keeper mapped both its
        # eigenvalues and its eigenvectors onto the one destination path, so the
        # two files of every job in the batch collided — CDO refusing the second
        # with "Outputfile already exists", or, where the run got that far, one
        # overwriting the other. Nothing said so; the job simply reported the
        # failure of a step whose command line looked right.
        #
        # The siblings are named from the destination the same way
        # ``ModelGraph._output_paths`` names them from a sink — same stem, the
        # schema's per-slot suffix — so a batch over two hundred files produces
        # two hundred pairs that can be told apart, rather than two hundred
        # collisions or a folder of temporaries.
        claimed_destination = False
        for slot, output in enumerate(targets):
            if output in mapping:
                continue
            if index == keeper and not claimed_destination:
                mapping[output] = destination
                claimed_destination = True
            elif index == keeper and slot < len(request.output_files):
                sibling = _sibling_destination(destination, request, slot)
                # No declared suffix means no safe name to invent next to the
                # user's own file; scratch is the honest fallback, and the
                # operator is one nothing has declared outputs for.
                mapping[output] = sibling or temp_path(
                    os.path.splitext(output)[1] or ".nc")
            else:
                # Split operators name a prefix rather than a file; a temporary
                # prefix still works, and its pieces are cleaned up with the
                # rest of the store.
                mapping[output] = temp_path(os.path.splitext(output)[1] or ".nc")

    return [
        request.with_paths(
            [mapping.get(path, path) for path in request.input_files],
            [mapping.get(path, path) for path in request.output_files],
            mapping.get(request.stdout_file, request.stdout_file),
        )
        for request in pipeline
    ]


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class BatchJob:
    """One input file's trip through the pipeline, and how it went.

    Mutable, unlike the value objects around it: this *is* the row the report
    table shows, updated in place as the run progresses.
    """

    index: int
    source: str
    output: str = ""
    status: str = PENDING
    message: str = ""
    duration: float = 0.0
    step: int = 0
    started_at: float = 0.0
    #: What CDO said on stdout, per step, that changes how this job's result
    #: reads — see :func:`~.nc_integration.stream_notices`. Collected as the run
    #: goes and folded into ``message`` when the job settles, because a batch of
    #: two hundred files is precisely where nobody reads two hundred stdouts.
    notices: list[str] = field(default_factory=list)
    #: Whether the destination was already on disk before this job began. A
    #: failed job cleans up after itself, and this is what stops that cleanup
    #: from deleting a file the job never got as far as overwriting.
    replaced_existing: bool = False

    @property
    def name(self) -> str:
        return Path(self.source).name

    @property
    def settled(self) -> bool:
        return self.status in SETTLED

    def as_row(self, total_steps: int) -> dict[str, str]:
        """The job as flat strings, for the CSV and JSON reports."""
        return {
            "index": str(self.index),
            "input": self.source,
            "output": self.output,
            "status": STATUS_LABELS.get(self.status, self.status),
            "steps_completed": f"{self.step}/{total_steps}",
            "duration_seconds": f"{self.duration:.2f}",
            "message": self.message,
        }


def export_report_csv(jobs: Sequence[BatchJob], total_steps: int) -> str:
    """The report as CSV, one row per input file."""
    buffer = io.StringIO()
    fields = ["index", "input", "output", "status", "steps_completed",
              "duration_seconds", "message"]
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for job in jobs:
        writer.writerow(job.as_row(total_steps))
    return buffer.getvalue()


def export_report_json(jobs: Sequence[BatchJob], total_steps: int,
                       generated_at: datetime | None = None) -> str:
    """The report as JSON, with the counts the table's footer shows."""
    counts: dict[str, int] = {}
    for job in jobs:
        counts[job.status] = counts.get(job.status, 0) + 1
    payload = {
        "generated_at": (generated_at or datetime.now()).isoformat(timespec="seconds"),
        "steps_per_job": total_steps,
        "counts": counts,
        "jobs": [job.as_row(total_steps) for job in jobs],
    }
    return json.dumps(payload, indent=2) + "\n"


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ManifestEntry:
    """One input this destination folder has already been given."""

    source: str
    size: int
    mtime: float
    output: str
    completed_at: str

    def matches(self, path: str) -> bool:
        """True when ``path`` is still the file this entry recorded.

        Size and mtime together, rather than the path alone: a folder re-run
        after some of its files were regenerated must redo those, and comparing
        content hashes of multi-gigabyte climate files to find that out would
        cost more than simply processing them again.
        """
        try:
            stat = os.stat(path)
        except OSError:
            return False
        return stat.st_size == self.size and abs(stat.st_mtime - self.mtime) < 1e-6


class BatchManifest:
    """What a destination folder already holds, so a batch can be resumed.

    Written after every completed job rather than once at the end — a manifest
    that only appears on a clean finish would be missing precisely when it is
    needed, which is after a batch was interrupted.
    """

    def __init__(self, directory: str, entries: dict[str, ManifestEntry] | None = None):
        self.directory = directory
        self.entries: dict[str, ManifestEntry] = entries or {}

    @property
    def path(self) -> str:
        return str(Path(self.directory) / MANIFEST_NAME)

    @classmethod
    def load(cls, directory: str) -> "BatchManifest":
        """Read the manifest in ``directory``; an empty one if there is none.

        A manifest that cannot be read is treated as absent. It is a
        convenience, and refusing to run a batch because a stale JSON file will
        not parse would be a poor trade.
        """
        manifest = cls(directory)
        path = Path(directory) / MANIFEST_NAME
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return manifest

        for item in raw.get("entries", []):
            try:
                entry = ManifestEntry(
                    source=str(item["source"]),
                    size=int(item.get("size", -1)),
                    mtime=float(item.get("mtime", 0.0)),
                    output=str(item.get("output", "")),
                    completed_at=str(item.get("completed_at", "")),
                )
            except (KeyError, TypeError, ValueError):
                continue
            manifest.entries[entry.source] = entry
        return manifest

    def already_done(self, source: str) -> bool:
        """True when this exact file has been processed here before."""
        entry = self.entries.get(os.path.abspath(source))
        return entry is not None and entry.matches(source)

    def completed(self, sources: Iterable[str]) -> list[str]:
        """Which of ``sources`` this manifest says are already done."""
        return [path for path in sources if self.already_done(path)]

    def record(self, job: BatchJob) -> None:
        """Note one finished job. Only successes are worth resuming past."""
        if job.status != DONE:
            return
        try:
            stat = os.stat(job.source)
        except OSError:
            return
        absolute = os.path.abspath(job.source)
        self.entries[absolute] = ManifestEntry(
            source=absolute,
            size=stat.st_size,
            mtime=stat.st_mtime,
            output=job.output,
            completed_at=datetime.now().isoformat(timespec="seconds"),
        )

    def save(self) -> bool:
        """Write the manifest. False if it could not be written."""
        payload = {
            "version": MANIFEST_VERSION,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "entries": [
                {
                    "source": entry.source,
                    "size": entry.size,
                    "mtime": entry.mtime,
                    "output": entry.output,
                    "completed_at": entry.completed_at,
                }
                for entry in self.entries.values()
            ],
        }
        try:
            Path(self.path).write_text(json.dumps(payload, indent=2) + "\n",
                                       encoding="utf-8")
        except OSError as exc:
            # A batch whose results are on disk has not failed because a
            # bookkeeping file could not be written; say so and carry on.
            logger.warning("Could not write the batch manifest %s: %s", self.path, exc)
            return False
        return True


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class BatchPlan:
    """Everything a run needs, settled before the first process starts."""

    pipeline: tuple[OperatorRequest, ...]
    sources: tuple[str, ...]
    naming: OutputNaming
    concurrency: int = 4
    timeout: int = DEFAULT_COMMAND_TIMEOUT
    skip_completed: bool = False

    @property
    def steps_per_job(self) -> int:
        return len(self.pipeline)


class BatchRunner(QObject):
    """Runs one :class:`BatchPlan`, several files at a time.

    The pool is deliberately simple: :meth:`_fill` starts jobs until the
    concurrency limit is reached, and every settled job calls it again. There is
    no thread and no lock, because ``QProcess`` delivers its callbacks on the
    GUI thread — so "several at once" here means several processes, driven from
    one thread that is never blocked.
    """

    #: (job index) — that job's row changed and should be redrawn.
    job_changed = pyqtSignal(int)
    #: (settled jobs, total jobs)
    progress = pyqtSignal(int, int)
    #: (jobs) — every job has settled, whether well or badly.
    finished = pyqtSignal(object)

    def __init__(self, integration, plan: BatchPlan, parent: QObject | None = None):
        super().__init__(parent)
        self._integration = integration
        self._plan = plan
        self._temp = TempFileStore(tag="ncexplorer_batch")
        self._manifest = BatchManifest.load(plan.naming.directory)

        self.jobs: list[BatchJob] = [
            BatchJob(index=number, source=path)
            for number, path in enumerate(plan.sources, start=1)
        ]

        # Paths this run has already promised to some job. Shared across jobs,
        # so two inputs rendering to one name cannot both claim it.
        self._claimed: set[str] = set()
        # Job index → (worker, remaining requests) for everything in flight.
        self._active: dict[int, tuple[object, list[OperatorRequest]]] = {}
        self._queue: list[int] = []
        self._cancelling = False
        self._running = False
        # A job that fails while being planned settles from inside _fill(),
        # which would call _fill() again from within itself; with one failing
        # job per file that recursion is as deep as the batch is long. The guard
        # makes the nested call a no-op and lets the outer loop carry on.
        self._filling = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    @property
    def plan(self) -> BatchPlan:
        return self._plan

    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        """Begin the batch. Returns immediately; watch the signals."""
        if self._running:
            logger.warning("Ignoring start(): this batch is already running")
            return

        self._running = True
        self._cancelling = False
        self._queue = list(range(len(self.jobs)))

        if self._plan.skip_completed:
            for position in self._queue:
                job = self.jobs[position]
                if self._manifest.already_done(job.source):
                    entry = self._manifest.entries[os.path.abspath(job.source)]
                    job.status = SKIPPED
                    job.output = entry.output
                    job.message = "Already processed into this folder"
                    self.job_changed.emit(position)

        logger.info("Batch started: %d file(s), %d step(s) each, %d at a time",
                    len(self.jobs), self._plan.steps_per_job, self._plan.concurrency)
        self._emit_progress()
        self._fill()

    def cancel(self) -> None:
        """Stop everything: queued jobs are dropped, running ones killed."""
        if not self._running or self._cancelling:
            return
        self._cancelling = True
        logger.info("Cancelling the batch: %d queued, %d in flight",
                    len(self._queue), len(self._active))

        for position in self._queue:
            job = self.jobs[position]
            if not job.settled:
                job.status = CANCELLED
                job.message = "Cancelled before it started"
                self.job_changed.emit(position)
        self._queue.clear()

        for worker, _ in list(self._active.values()):
            worker.cancel()

        # Nothing was in flight, so no callback is coming to notice the queue is
        # empty; finish from here instead.
        if not self._active:
            self._settle_batch()

    def cleanup(self) -> None:
        """Delete the intermediates. Safe to call whether or not a run happened."""
        self._temp.cleanup()

    # ------------------------------------------------------------------
    # Pool
    # ------------------------------------------------------------------
    def _fill(self) -> None:
        """Start queued jobs until the concurrency limit is reached."""
        if self._filling:
            return
        self._filling = True
        try:
            limit = max(1, self._plan.concurrency)
            while self._queue and len(self._active) < limit and not self._cancelling:
                position = self._queue.pop(0)
                if self.jobs[position].settled:
                    continue  # skipped by the manifest
                self._begin_job(position)
        finally:
            self._filling = False

        if not self._queue and not self._active:
            self._settle_batch()

    def _begin_job(self, position: int) -> None:
        """Plan one job's requests and start its first step."""
        job = self.jobs[position]
        destination, reason = self._destination_for(job)

        if destination is None:
            job.status = SKIPPED
            job.message = reason
            self.job_changed.emit(position)
            self._emit_progress()
            return

        try:
            requests = plan_job(
                self._plan.pipeline, job.source, destination,
                lambda suffix: self._temp.new(suffix=suffix),
            )
        except Exception as exc:
            # Planning touches the filesystem only through the temp store, so
            # this is a broken pipeline or a temp directory that cannot be
            # written — either way this job cannot start.
            logger.error("Could not plan %s: %s", job.source, exc, exc_info=True)
            self._finish_job(position, FAILED, str(exc))
            return

        job.status = RUNNING
        job.output = destination
        job.replaced_existing = os.path.exists(destination)
        job.started_at = time.time()
        job.step = 0
        self._claimed.add(destination)
        self.job_changed.emit(position)

        self._active[position] = (None, requests)
        self._start_step(position)

    def _destination_for(self, job: BatchJob) -> tuple[str | None, str]:
        """This job's final output path, or ``None`` and why it is being skipped."""
        naming = self._plan.naming
        rendered = render_output_name(
            naming.template, job.source, job.index, pipeline_operator(self._plan.pipeline)
        )
        candidate = str(Path(naming.directory) / rendered)
        if not keeps_text(self._plan.pipeline):
            # A pipeline whose result is a captured reading writes text, and
            # forcing a data extension onto it would produce a folder of .nc
            # files that no reader can open.
            candidate = ensure_output_extension(candidate, job.source)
        resolved = resolve_collision(candidate, naming.collision, self._claimed)
        if resolved is not None:
            return resolved, ""
        if candidate in self._claimed:
            # Two inputs rendered to one name. Overwriting here would destroy a
            # result this same batch has already produced and reported as done.
            return None, f"Another file in this batch already writes {Path(candidate).name}"
        return None, f"{Path(candidate).name} already exists"

    def _start_step(self, position: int) -> None:
        """Run the next request of one job."""
        _, remaining = self._active[position]
        job = self.jobs[position]

        if not remaining:
            self._finish_job(position, DONE, "")
            return

        request = remaining[0]
        try:
            worker = self._integration.execute_operator_async(
                request.operator,
                input_files=list(request.input_files),
                output_files=list(request.output_files),
                extra_parameters=list(request.parameters),
                timeout=self._plan.timeout,
                parent=self,
                # Recorded on the request and carried through ``with_paths``, so
                # every file in the batch is processed under the environment the
                # pipeline was built with rather than the first one's.
                env=request.environment(),
                # Both carried the same way and for the same reason: a batch
                # that dropped the pipeline's ``-f nc`` would write a different
                # format for every file in it, and one that dropped the stdin
                # file would run ``input`` against an empty stream.
                options=list(request.options),
                stdin_path=request.stdin_file,
            )
        except Exception as exc:
            # Argument validation and alias creation happen before any process
            # exists, so this is the one place starting a step can raise.
            logger.error("Could not start %s for %s: %s",
                         request.operator, job.source, exc, exc_info=True)
            self._finish_job(position, FAILED, f"{request.operator}: {exc}")
            return

        self._active[position] = (worker, remaining)
        # default=position binds the loop-free but still late-bound closure
        # argument, so every job's callbacks address its own row.
        worker.finished.connect(lambda result, index=position: self._on_step_finished(index, result))
        worker.failed.connect(lambda message, index=position: self._on_step_failed(index, message))
        worker.cancelled.connect(lambda index=position: self._on_step_cancelled(index))

    # ------------------------------------------------------------------
    # Step outcomes
    # ------------------------------------------------------------------
    def _on_step_finished(self, position: int, result) -> None:
        entry = self._active.get(position)
        if entry is None:
            return  # already settled — a late signal from a cancelled worker
        worker, remaining = entry
        job = self.jobs[position]
        request = remaining[0]

        if not result.success:
            # The last non-empty line of stderr is the one that says what went
            # wrong; the lines above it are the engine's own preamble. Each is
            # stripped because that output is column-aligned with padding that
            # makes no sense once a single line is lifted out of it.
            lines = [line.strip() for line in (result.stderr or "").splitlines() if line.strip()]
            # Redacted here rather than left to the logging filter: this message
            # is shown in the report table and written into the exported CSV,
            # neither of which is a log sink the filter can reach.
            self._finish_job(
                position, FAILED,
                redact_text(f"{request.operator}: "
                            f"{lines[-1] if lines else 'the operation failed'}"),
            )
            return

        job.step += 1
        for notice in stream_notices(result.stdout, result.stderr):
            job.notices.append(redact_text(f"{request.operator}: {notice}"))
        write_stdout_capture(request, result.stdout)
        # Retired and forgotten in one move. Leaving the finished worker in the
        # entry would let _finish_job retire it a second time if this turns out
        # to have been the last step, and a worker is only ever retired once.
        self._retire(worker)
        self._active[position] = (None, remaining[1:])
        self.job_changed.emit(position)
        self._start_step(position)

    def _on_step_failed(self, position: int, message: str) -> None:
        if position not in self._active:
            return
        self._finish_job(position, FAILED, message)

    def _on_step_cancelled(self, position: int) -> None:
        if position not in self._active:
            return
        self._finish_job(position, CANCELLED, "Cancelled")

    def _retire(self, worker) -> None:
        """Let go of a worker whose one terminal signal has been delivered.

        Workers are parented to the runner so a batch of a thousand files would
        otherwise leave a thousand ``QProcess`` objects alive until the runner
        itself goes.
        """
        if worker is not None:
            worker.deleteLater()

    def _finish_job(self, position: int, status: str, message: str) -> None:
        """Settle one job and let the next queued one start."""
        job = self.jobs[position]
        job.status = status
        # A job that succeeded has no message of its own, which is exactly the
        # space a notice needs: "done" and "done, but stream2 was broadcast over
        # every timestep" must not read the same in the report table. A job that
        # failed keeps its failure text — that is the more urgent thing to say.
        job.message = message or "; ".join(job.notices)
        if job.started_at:
            job.duration = time.time() - job.started_at

        entry = self._active.pop(position, None)
        if entry is not None:
            self._retire(entry[0])

        if status != DONE:
            # The destination was promised to a job that did not produce it, so
            # release the name — and do not leave a half-written file behind
            # claiming to be a result.
            self._claimed.discard(job.output)
            self._discard_partial(job)
            job.output = ""

        if status == DONE:
            self._manifest.record(job)
            self._manifest.save()

        logger.info("Batch job %d/%d %s: %s", job.index, len(self.jobs), status, job.name)
        self.job_changed.emit(position)
        self._emit_progress()
        self._fill()

    def _discard_partial(self, job: BatchJob) -> None:
        """Remove the output of a job that did not finish writing it.

        Half a NetCDF file in the destination folder is worse than none: it
        looks like a result, and nothing downstream can tell that it is not.

        A file that was already there when the job started is left alone. Under
        the overwrite policy that file is somebody's existing data, and deleting
        it because *our* run failed would destroy something this batch was never
        asked to touch.
        """
        if not job.output or job.replaced_existing:
            return
        try:
            Path(job.output).unlink(missing_ok=True)
        except OSError as exc:
            logger.debug("Could not remove the partial output %s: %s", job.output, exc)

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------
    def counts(self) -> dict[str, int]:
        """How many jobs are in each status right now."""
        tally: dict[str, int] = {}
        for job in self.jobs:
            tally[job.status] = tally.get(job.status, 0) + 1
        return tally

    def _emit_progress(self) -> None:
        settled = sum(1 for job in self.jobs if job.settled)
        self.progress.emit(settled, len(self.jobs))

    def _settle_batch(self) -> None:
        if not self._running:
            return
        self._running = False
        self._manifest.save()
        tally = self.counts()
        logger.info("Batch finished: %s",
                    ", ".join(f"{count} {status}" for status, count in sorted(tally.items())))
        self.finished.emit(self.jobs)
