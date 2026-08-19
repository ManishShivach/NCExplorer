# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""The session's operator history, and the exporters that make it reproducible.

``NCExplorerIntegration.command_history`` is not the right source for any of
this, for two reasons:

* It is bounded by ``MAX_COMMAND_HISTORY``, so the *start* of a long session
  falls off the end. A reproducibility log that silently loses its first steps
  is worse than none, so :class:`SessionLog` is deliberately unbounded — a step
  is a few hundred bytes and even an exhausting session produces a few hundred
  of them.
* Its ``argv`` holds the paths CDO actually saw, which are not necessarily the
  paths the user chose. When an input or output contains a space,
  ``execute_operator`` rewrites it into a throwaway symlink under the temp store
  and moves the result back afterwards. A script generated from that argv would
  reference alias paths that no longer exist — broken in a way nobody notices
  until they run it a week later on a different machine.

So a step records the invocation *as the user specified it*
(:class:`OperatorRequest`) and reconstructs the command from that at export
time. ``argv`` is kept alongside it for reference and debugging only, and never
consulted by an exporter.

Every exported artefact runs without NCExplorer installed, and every one of them
invokes the plain ``cdo`` on ``PATH`` rather than whatever binary this process
resolved — a frozen build's bundled ``cdo`` lives inside the application bundle
and means nothing on someone else's machine.
"""

from __future__ import annotations

import json
import logging
import shlex
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

logger = logging.getLogger(__name__)

#: Outcome of one recorded step.
OK = "ok"
FAILED = "failed"
CANCELLED = "cancelled"

STATUS_LABELS = {OK: "OK", FAILED: "Failed", CANCELLED: "Cancelled"}

#: The binary an exported artefact calls. Never the resolved path — see the
#: module docstring.
CDO = "cdo"


def operator_token(operator: str, parameters: Sequence[str]) -> str:
    """The first CDO argument: ``timmean``, or ``sellonlatbox,0,30,-10,10``.

    Trailing empty values are dropped, which is what
    ``NCExplorerIntegration._resolve_operator_call`` does before building the
    real command — an optional parameter the user left blank must not become a
    trailing comma.

    The spelling of each remaining value comes from ``parameter_tokens``, the
    same function the execution layer builds argv with. This used to be a second
    copy of the rule — ``','.join(trimmed)`` — and a second copy is exactly what
    it could not afford to be, because everything this module renders claims to
    be the command that ran: the session log, the exported shell script, the
    Python and the CDO-chain exporters, and the model builder's preview. Once
    keyword parameters existed the copy would have written
    ``cdo bitrounding,0.999``, which is not merely a different spelling from the
    ``bitrounding,inflevel=0.999`` that actually ran — it is a parse error, so
    the exported script would not run at all.

    Imported inside the function because this module is deliberately the
    dependency-free end of ``core``; the import is one-way (``categories`` knows
    nothing about the session log) and this keeps it that way at module scope.
    """
    trimmed = list(parameters)
    while trimmed and trimmed[-1] == "":
        trimmed.pop()
    try:
        from .categories import parameter_tokens
    except ImportError:                                     # pragma: no cover
        rendered = trimmed
    else:
        rendered = parameter_tokens(operator, trimmed)
    return operator if not rendered else f"{operator},{','.join(rendered)}"


@dataclass(frozen=True, slots=True)
class OperatorRequest:
    """One operator invocation as the user specified it.

    These paths are the originals the user typed or browsed to, *not* the
    temporary aliases the integration may have substituted for them. That is the
    whole point of recording at this level; see the module docstring.
    """

    operator: str
    input_files: tuple[str, ...] = ()
    output_files: tuple[str, ...] = ()
    parameters: tuple[str, ...] = ()
    nin: int = 1
    nout: int = 1
    #: Where this operator's *printed* output should be kept, if anywhere. Only
    #: the info operators (``nout == 0``) have any — they write no file and
    #: report entirely on stdout, so without this a ``sinfo`` over three hundred
    #: files leaves nothing behind but a console nobody scrolled back through.
    #:
    #: Deliberately not an entry in ``output_files``: the engine validates that
    #: list against the operator's own arity and would reject a target for an
    #: operator declaring none. This is a redirection the *shell* performs, not
    #: an argument the engine is given, which is also why it appears in
    #: :meth:`command_line` and never in :meth:`arguments`.
    stdout_file: str = ""
    #: The file whose *contents* are piped to this operator on standard input.
    #: Only the three ``Formatted input`` operators have one — see
    #: ``categories.reads_stdin`` — because they are the only operators in the
    #: catalog whose data is not named on the command line.
    #:
    #: The exact mirror of ``stdout_file`` and for the same reasons: it is a
    #: redirection the shell performs rather than an argument the engine is
    #: given, so it appears in :meth:`command_line` as ``< file`` and never in
    #: :meth:`arguments`. Putting it in ``input_files`` would have been the
    #: wrong shape twice — the engine validates that list against the operator's
    #: arity, and these operators declare ``nin == 0``, so a path there is
    #: refused before it can be misused as an argv token.
    stdin_file: str = ""
    #: CDO's *global* options for this run, as separate tokens — ``("-f", "nc")``
    #: — which CDO takes between the binary and the operator and nowhere else.
    #:
    #: Empty for almost every run, and that emptiness is load-bearing: a call
    #: with no options must build exactly the argv it built before this field
    #: existed, which is asserted in
    #: ``test_import_export_category.py::test_no_options_builds_the_same_argv``.
    #:
    #: It is here rather than folded into ``parameters`` because the two go to
    #: different places on the command line and mixing them produces a command
    #: CDO cannot parse: ``parameters`` become the ``op,a,b`` token after the
    #: operator name, options must precede it. Every documented example of both
    #: import operators opens with ``cdo -f nc``, and without it CDO writes its
    #: default format whatever the output file is called — measured on 2.6.3,
    #: ``cdo import_binary demo.ctl out.nc`` produces a GRIB file with 16-bit
    #: packing, so the user loses both the format they named and precision.
    options: tuple[str, ...] = ()
    #: Environment variables that change what this operator computes, as
    #: ``(name, value)`` pairs. A tuple rather than a dict so the request stays
    #: hashable and comparable like every other field on it.
    #:
    #: Not in :meth:`arguments` and not in ``output_files``, for the same reason
    #: ``stdout_file`` is in neither: these never appear on the command line.
    #: They do appear in :meth:`command_line`, as the ``NAME=value cdo …``
    #: prefix, because that is what makes an *exported* script reproduce the
    #: run — a replayed ``cdo eof,3`` without the CDO_WEIGHT_MODE it was
    #: recorded with computes different numbers and says nothing about it.
    env: tuple[tuple[str, str], ...] = ()
    #: The directory this run must start in, or "" when it does not matter —
    #: which is every operator but one.
    #:
    #: ``cmor`` is the exception and the reason this field exists: ``drs_root``
    #: defaults to the working directory and ``info`` defaults to
    #: ``CWD/.cdocmorinfo``, so two of the three things deciding what the run
    #: produces are absent from its command line. An exported script that
    #: replayed ``cdo cmor,CMIP6_day.json in.nc`` from wherever the user
    #: happened to be would read a different attribute file and write the tree
    #: somewhere else — the same argv, no error, a different result, which is
    #: the class of failure ``env`` above was added for.
    #:
    #: Empty by default, so every step recorded before this existed and every
    #: operator that does not care builds exactly the command line it built
    #: before. Set by ``gui/session_dock.py`` from the run's own
    #: ``CommandRecord.cwd``, and only for the operators
    #: ``categories.CWD_DEPENDENT_OPERATORS`` names — a ``cd`` in front of
    #: ``timmean`` would be noise asserting something untrue.
    cwd: str = ""

    def arguments(self) -> list[str]:
        """The arguments following ``cdo``, in CDO's own order.

        ``options`` lead, because that is the only place CDO accepts them —
        ``cdo -f nc import_binary demo.ctl out.nc``, never after the operator
        name. They are arguments in a way the two redirections are not, which is
        why they are here and ``stdin_file``/``stdout_file`` are not: those are
        the argv the process is started with, and ``<`` and ``>`` are things the
        shell does, not things the engine is passed.
        """
        return [
            *self.options,
            operator_token(self.operator, self.parameters),
            *self.input_files,
            *self.output_files,
        ]

    def command_line(self) -> str:
        """The invocation as a shell-quoted one-liner, redirection included.

        Environment overrides lead, in ``NAME=value cdo …`` form, which is both
        what a shell accepts verbatim and what the CDO manual's own examples
        look like.

        A run with a :attr:`cwd` is wrapped in ``(cd <dir> && …)``. The
        parentheses are the point: a bare ``cd`` would change the directory for
        every step that followed it in an exported script, so one ``cmor`` step
        would silently relocate the rest of the session. A subshell puts it back.
        """
        prefix = [f"{name}={shlex.quote(value)}" for name, value in self.env]
        line = " ".join([*prefix,
                         *(shlex.quote(part) for part in [CDO, *self.arguments()])])
        # Input redirection before output, which is the conventional order and
        # the one CDO's own manual writes these operators in.
        if self.stdin_file:
            line += " < " + shlex.quote(self.stdin_file)
        if self.stdout_file:
            line += " > " + shlex.quote(self.stdout_file)
        # Outside the redirections, so ``>`` resolves against the same directory
        # CDO ran in rather than against the caller's.
        if self.cwd:
            line = f"(cd {shlex.quote(self.cwd)} && {line})"
        return line

    def environment(self) -> dict[str, str]:
        """The overrides as the mapping the execution layer takes."""
        return dict(self.env)

    def with_paths(self, input_files: Sequence[str], output_files: Sequence[str],
                   stdout_file: str | None = None) -> "OperatorRequest":
        """A copy pointing at different files — the substitution replay uses."""
        return OperatorRequest(
            operator=self.operator,
            input_files=tuple(input_files),
            output_files=tuple(output_files),
            parameters=self.parameters,
            nin=self.nin,
            nout=self.nout,
            stdout_file=self.stdout_file if stdout_file is None else stdout_file,
            # Carried through every retarget for the same reason the environment
            # is: a replayed ``input`` that lost the file it reads runs the same
            # command line against an empty stdin and aborts on "Too few input
            # elements", which reads as a broken replay rather than a dropped
            # field. It is not among the paths a retarget substitutes because it
            # is not one of the operator's arguments.
            stdin_file=self.stdin_file,
            options=self.options,
            env=self.env,
        )

    @property
    def produces_file(self) -> bool:
        """False for the info operators, which only ever print to stdout."""
        return self.nout != 0

    @property
    def is_split(self) -> bool:
        """True when the single output is a *prefix* and CDO emits many files."""
        return self.nout == -1


def write_stdout_capture(request: OperatorRequest, text: str) -> bool:
    """Keep what an info operator printed, if it asked for that. False on failure.

    Every driver that runs an :class:`OperatorRequest` calls this, so the file
    appears whether the step was run from the model builder or as one job of a
    batch. The redirection in :meth:`OperatorRequest.command_line` is what makes
    an *exported* script do the same thing; this is the in-process equivalent,
    and the two have to agree.
    """
    if not request.stdout_file:
        return False
    try:
        path = Path(request.stdout_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text or "", encoding="utf-8")
    except OSError as exc:
        # The operator itself succeeded and its reading is in the console; not
        # being able to file a copy of it is worth a warning, not a failed step.
        logger.warning("Could not write %s: %s", request.stdout_file, exc)
        return False
    return True


@dataclass(frozen=True, slots=True)
class SessionStep:
    """One executed operation, successful or not."""

    request: OperatorRequest
    status: str = OK
    returncode: int | None = None
    duration: float = 0.0
    started_at: datetime = field(default_factory=datetime.now)
    #: The resolved command as the operating system saw it. Reference only —
    #: exporters must not read it (module docstring explains why).
    argv: tuple[str, ...] = ()

    @property
    def succeeded(self) -> bool:
        return self.status == OK

    def summary(self) -> str:
        """One line naming the operator and its files, for the session panel."""
        parts = [operator_token(self.request.operator, self.request.parameters)]
        parts.extend(self.request.input_files)
        parts.extend(self.request.output_files)
        return " ".join(parts)


class SessionLog:
    """Every operation run this session, in order, without a cap.

    Plain Python on purpose: the panel that displays it owns the Qt side, and
    keeping the model free of widgets is what lets the exporters be used from a
    script or a test.
    """

    def __init__(self) -> None:
        self._steps: list[SessionStep] = []

    def __len__(self) -> int:
        return len(self._steps)

    def __iter__(self):
        return iter(self._steps)

    @property
    def steps(self) -> list[SessionStep]:
        """A snapshot of the recorded steps; mutating it changes nothing."""
        return list(self._steps)

    def record(self, step: SessionStep) -> SessionStep:
        """Append one executed step."""
        self._steps.append(step)
        logger.debug("Session step %d recorded: %s (%s)",
                     len(self._steps), step.summary(), step.status)
        return step

    def remove(self, index: int) -> None:
        """Drop one step. Out-of-range indices are ignored."""
        if 0 <= index < len(self._steps):
            del self._steps[index]

    def move(self, index: int, offset: int) -> int:
        """Move a step by ``offset`` places; returns its new index.

        Clamped rather than wrapped: dragging the first step up should leave it
        where it is, not send it to the bottom.
        """
        if not (0 <= index < len(self._steps)):
            return index
        target = max(0, min(len(self._steps) - 1, index + offset))
        if target != index:
            self._steps.insert(target, self._steps.pop(index))
        return target

    def clear(self) -> None:
        """Forget every step."""
        self._steps.clear()

    def input_paths(self) -> list[str]:
        """The distinct input paths used across the session, in first-use order.

        This is what the replay dialog offers for substitution. Outputs of
        earlier steps are excluded: they are produced by the chain itself and
        get redirected automatically, so asking the user to supply them would be
        both confusing and wrong.
        """
        produced = {
            path
            for step in self._steps
            for path in step.request.output_files
        }
        seen: list[str] = []
        for step in self._steps:
            for path in step.request.input_files:
                if path not in produced and path not in seen:
                    seen.append(path)
        return seen


# ---------------------------------------------------------------------------
# Exporters
# ---------------------------------------------------------------------------

def _header_lines(steps: Sequence[SessionStep], cdo_version: str,
                  generated_at: datetime | None) -> list[str]:
    """The provenance block every exported artefact carries."""
    stamp = (generated_at or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    runnable = sum(1 for step in steps if step.succeeded)
    lines = [
        f"Generated by NCExplorer on {stamp}",
        f"CDO version: {cdo_version or 'unknown'}",
        f"Steps: {len(steps)} recorded, {runnable} reproduced here",
    ]
    if runnable != len(steps):
        lines.append(
            "Steps that failed or were cancelled are shown commented out, "
            "so this file cannot fail in the same way."
        )
    return lines


def _skip_note(step: SessionStep) -> str:
    label = STATUS_LABELS.get(step.status, step.status)
    return f"{label.lower()} in NCExplorer (rc={step.returncode})"


def export_shell(steps: Sequence[SessionStep], *, cdo_version: str = "",
                 generated_at: datetime | None = None) -> str:
    """A POSIX shell script that re-runs the session.

    ``set -e`` on purpose: a chain whose third step failed should stop there
    rather than carry on feeding the next operator a file that was never
    written.
    """
    lines = ["#!/bin/sh"]
    lines += [f"# {line}" for line in _header_lines(steps, cdo_version, generated_at)]
    lines += ["", "set -e", ""]

    for number, step in enumerate(steps, start=1):
        lines.append(f"# Step {number}: {step.request.operator}")
        if step.succeeded:
            lines.append(step.request.command_line())
        else:
            lines.append(f"# {_skip_note(step)}")
            lines.append(f"# {step.request.command_line()}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _is_file_target(step: SessionStep) -> bool:
    """True when the step produces exactly one file make can date-stamp.

    A split operator's output is a prefix standing for a number of files nobody
    can predict, so it has to be a phony target. An info operator normally writes
    nothing at all — but one whose printed output is redirected to a file does
    produce exactly one, and make can date-stamp that like any other.
    """
    if step.request.stdout_file:
        return True
    return step.request.nout == 1 and len(step.request.output_files) == 1


def _target_path(step: SessionStep) -> str:
    """The single file a step leaves behind, or "" when it leaves none."""
    if step.request.stdout_file:
        return step.request.stdout_file
    if step.request.nout == 1 and len(step.request.output_files) == 1:
        return step.request.output_files[0]
    return ""


def _make_target(number: int, step: SessionStep) -> str:
    """The target name for one step: its output file, or a phony step name."""
    return _target_path(step) or f"step{number}-{step.request.operator}"


def export_makefile(steps: Sequence[SessionStep], *, cdo_version: str = "",
                    generated_at: datetime | None = None) -> str:
    """A Makefile with one target per step, so only stale work is redone."""
    runnable = [(number, step) for number, step in enumerate(steps, start=1) if step.succeeded]
    targets = [_make_target(number, step) for number, step in runnable]

    lines = [f"# {line}" for line in _header_lines(steps, cdo_version, generated_at)]

    if any(any(character.isspace() for character in path)
           for _, step in runnable
           for path in (*step.request.input_files, *step.request.output_files)):
        # make splits prerequisite lists on whitespace and gives no way to quote
        # around it; saying so beats emitting a file that quietly misbehaves.
        lines += [
            "#",
            "# WARNING: some paths contain spaces, which make cannot express as",
            "# prerequisites. Rename those files, or use the shell script export.",
        ]

    phony = [
        target for (number, step), target in zip(runnable, targets)
        if not _is_file_target(step)
    ]
    lines += ["", " ".join([".PHONY: all clean", *phony]).rstrip(), ""]
    lines.append(" ".join(["all:", *(shlex.quote(target) for target in targets)]).rstrip())
    lines.append("")

    for (number, step), target in zip(runnable, targets):
        request = step.request
        prerequisites = " ".join(shlex.quote(path) for path in request.input_files)
        lines.append(f"# Step {number}: {request.operator}")
        lines.append(f"{shlex.quote(target)}: {prerequisites}".rstrip())
        lines.append(f"\t{request.command_line()}")
        lines.append("")

    for number, step in enumerate(steps, start=1):
        if not step.succeeded:
            lines.append(f"# Step {number} ({_skip_note(step)}) omitted:")
            lines.append(f"#   {step.request.command_line()}")
            lines.append("")

    removable = [
        shlex.quote(path)
        for _, step in runnable
        if (path := _target_path(step))
    ]
    lines.append("clean:")
    lines.append("\trm -f " + " ".join(removable) if removable else "\t@echo nothing to clean")
    lines.append("")

    return "\n".join(lines)


def _notebook_cell(cell_type: str, source: str, identifier: str) -> dict:
    """One nbformat 4.5 cell.

    ``id`` is mandatory from 4.5 onwards and Jupyter warns loudly without it;
    the source is a list of newline-kept lines, which is the shape the format
    expects and what makes the file diff sensibly.
    """
    cell = {
        "cell_type": cell_type,
        "id": identifier,
        "metadata": {},
        "source": source.splitlines(keepends=True),
    }
    if cell_type == "code":
        cell["execution_count"] = None
        cell["outputs"] = []
    return cell


def export_notebook(steps: Sequence[SessionStep], *, cdo_version: str = "",
                    generated_at: datetime | None = None) -> str:
    """A Jupyter notebook as plain nbformat-4 JSON.

    Written by hand rather than through :mod:`nbformat` so the export has no
    dependency the application does not already carry.
    """
    header = "\n".join(f"{line}  " for line in _header_lines(steps, cdo_version, generated_at))
    cells = [
        _notebook_cell("markdown", f"# NCExplorer session\n\n{header}\n", "header"),
        _notebook_cell("code", _NOTEBOOK_PREAMBLE, "preamble"),
    ]

    for number, step in enumerate(steps, start=1):
        request = step.request
        arguments = ", ".join(repr(argument) for argument in request.arguments())
        call = f"run_cdo([{arguments}]"
        if request.stdout_file:
            call += f", stdout_file={request.stdout_file!r}"
        call += ")"
        if step.succeeded:
            source = f"# Step {number}: {request.operator}\n{call}\n"
        else:
            source = (
                f"# Step {number}: {request.operator} — {_skip_note(step)}.\n"
                f"# Uncomment to run it anyway.\n"
                f"# {call}\n"
            )
        cells.append(_notebook_cell("code", source, f"step-{number}"))

    final = _final_output(steps)
    if final:
        cells.append(_notebook_cell("markdown", "## Result\n", "result-heading"))
        cells.append(_notebook_cell("code", _plot_source(final), "result-plot"))

    notebook = {
        "cells": cells,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    return json.dumps(notebook, indent=1) + "\n"


def _final_output(steps: Sequence[SessionStep]) -> str:
    """The last single-file output the session produced, if there is one."""
    for step in reversed(list(steps)):
        if step.succeeded and step.request.nout == 1 and step.request.output_files:
            return step.request.output_files[0]
    return ""


_NOTEBOOK_PREAMBLE = '''import subprocess


def run_cdo(arguments, stdout_file=None):
    """Run one CDO invocation, echoing what it printed.

    ``stdout_file`` keeps the printed output, which is the only result the
    info operators produce.
    """
    print("cdo", " ".join(arguments))
    completed = subprocess.run(
        ["cdo", *arguments], capture_output=True, text=True, check=False
    )
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.returncode != 0:
        raise RuntimeError(f"cdo exited with {completed.returncode}:\\n{completed.stderr}")
    if stdout_file:
        with open(stdout_file, "w", encoding="utf-8") as handle:
            handle.write(completed.stdout)
    return completed
'''


def _plot_source(path: str) -> str:
    return (
        "import matplotlib.pyplot as plt\n"
        "import xarray as xr\n"
        "\n"
        f"dataset = xr.open_dataset({path!r})\n"
        "print(dataset)\n"
        "\n"
        "# First data variable, first timestep — adjust to taste.\n"
        "name = list(dataset.data_vars)[0]\n"
        "field = dataset[name]\n"
        "while field.ndim > 2:\n"
        "    field = field.isel({field.dims[0]: 0})\n"
        "field.plot()\n"
        "plt.show()\n"
    )


def export_for(fmt: str, steps: Iterable[SessionStep], *, cdo_version: str = "",
               generated_at: datetime | None = None) -> str:
    """Render ``steps`` in one of ``shell`` / ``makefile`` / ``notebook``."""
    exporters = {
        "shell": export_shell,
        "makefile": export_makefile,
        "notebook": export_notebook,
    }
    try:
        exporter = exporters[fmt]
    except KeyError:
        raise ValueError(f"Unknown export format: {fmt}") from None
    return exporter(list(steps), cdo_version=cdo_version, generated_at=generated_at)
