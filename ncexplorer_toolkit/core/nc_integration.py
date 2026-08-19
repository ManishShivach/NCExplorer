# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Mapping, Optional, Callable, Union, Tuple

from ..utils.tempfile_store import TempFileStore
from .filetypes import WRITTEN_EXTENSIONS

# Use proper path type hints
PathLike = Union[str, os.PathLike]

#: Seconds a single cdo invocation is allowed before it is abandoned. Shared by
#: the blocking and the asynchronous path so the two cannot disagree.
DEFAULT_COMMAND_TIMEOUT = 300

#: Every extension the engine recognises. It decides the output format solely
#: from this, so a path ending in anything else is silently written as NetCDF4
#: — which is why both the operator form and the batch runner check a name
#: against this set before handing it over.
#:
#: Read from ``core/filetypes.py`` rather than written out here, because the
#: save dialog's filter is built from the same tuple. The two were separate
#: literals and had drifted in the direction that costs the most: the dialog
#: offered NetCDF, GRIB and GRIB2 only, so a user who wanted SERVICE, EXTRA or
#: IEG — three formats this set has always accepted — had to know to type the
#: suffix by hand.
OUTPUT_EXTENSIONS = frozenset(WRITTEN_EXTENSIONS)


#: Things CDO says on **stdout** during a run that succeeded, and that change how
#: its result has to be read. They are not warnings — CDO exits 0 and writes a
#: perfectly well-formed file — which is exactly why they need lifting out.
#:
#: ``Filling up stream`` is the one that matters here. Arith's documentation says
#: "One of the input files can contain only one timestep or one variable", and
#: when that path is taken CDO announces it:
#:
#:     cdo add: Filling up stream2 >b.nc< by copying the first timestep.
#:     cdo add: Filling up stream2 >b.nc< by copying the first variable of each timestep.
#:
#: Both measured on CDO 2.6.0. That line is the difference between "this did what
#: I meant" and "this broadcast one field over 730 timesteps", and on stdout it
#: arrived indistinguishable from the progress meter.
#:
#: The other two are a different case with the same consequence. ``pack`` and
#: ``bitrounding`` are (1|1) operators — they write a file, and every surface
#: reports them by that file — but asked for it they *also* print their
#: per-variable answer to stdout, and that answer is the entire reason to ask.
#: Measured on 2.6.3:
#:
#:     $ cdo pack,printparam=true infile outfile
#:     name=random  add_offset=0.495515036  scale_factor=1.5122603e-05
#:
#:     $ cdo bitrounding,printbits=true infile outfile
#:     random=23
#:
#: Without a pattern here the user ticks "print pack parameters", the run
#: succeeds, and nothing is printed anywhere — the surfaces discard stdout for
#: an operator that writes a file. Anchored on the shapes above rather than on
#: the operator name, because ``stream_notices`` is handed stdout and nothing
#: else; ``add_offset=``/``scale_factor=`` and a bare ``<name>=<digits>`` line
#: are specific enough not to fire on a progress meter or a warning.
_STDOUT_NOTICES = (
    re.compile(r"Filling up stream", re.IGNORECASE),
    re.compile(r"\badd_offset\s*=.*\bscale_factor\s*="),
    re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\d+$"),
)


#: Two operators whose names CDO also uses for *global options*, where the
#: option wins and the operator becomes unreachable by its own name.
#:
#: ``--sortname`` (short ``-Q``) and ``--sortparam`` are documented in
#: ``cdo --help``. CDO's argument parser matches those names before it looks
#: anything up in the operator table, and it matches them **without requiring
#: the dashes** — so ``cdo sortname in out`` is read as "global option
#: sortname, and then no operator at all":
#:
#:     cdo (Abort): … ^ Operator missing, <infile> is a file on disk!
#:
#: ``sortparam`` leaks the mechanism on the way past — ``cdi warning
#: (cdiDefGlobal): Unsupported global key: SORTPARAM`` — which is CDI being
#: handed the name as a global key and not recognising it.
#:
#: A **trailing comma** is the whole fix: ``sortname,`` is no longer an exact
#: match for the option name, so it falls through to the operator table and the
#: operator runs with an empty parameter list. Measured on CDO 2.6.3:
#: ``cdo sortname, in out`` and ``cdo sortparam, in out`` both exit 0 and write
#: their output, where the bare names abort every time.
#:
#: Only these two need it. ``sortcode``, ``sortlevel``, ``sorttimestamp`` and
#: ``sortvar`` share the module and no option name, and run as they are — and
#: ``sortvar``, which ``cdo --operators`` lists as an alias of ``sortname``, is
#: the reason the collision is survivable at all: it is a second spelling of
#: the same operator. ``sortparam`` has no alias, so without the comma there is
#: no way to reach it from anywhere.
#:
#: Applied in ``_resolve_operator_call`` and only when the call has no
#: parameters of its own, since a real parameter already supplies the comma.
_OPTION_SHADOWED_OPERATORS = frozenset({"sortname", "sortparam"})


#: The four ECA indices that CDO 2.6.3 kills itself on while writing NetCDF,
#: and the chain step that stops it.
#:
#: They die on an assertion inside CDI:
#:
#:     Assertion failed: (month >= 1 && month <= 12 && day > 0 && day <= 31 …),
#:     function cdfGetTimeUnits, file stream_cdf_time.c, line 88
#:
#: SIGABRT, no output file, and **nondeterministic** — measured over 20 identical
#: runs, ``eca_pd`` aborted 15 times and ``eca_rr1`` 20, so a single clean run is
#: not evidence of anything here. That is also why the membership below is a
#: *measurement* rather than a family rule: all 62 ``eca_*``/``etccdi_*``
#: operators were run six times each, and these four are the ones that ever
#: aborted. Guessing the family would have been wrong in both directions —
#: ``eca_r10mm`` and ``eca_r20mm`` are clean and sit right beside ``eca_r1mm``,
#: which fails 6 times in 6.
#:
#: What it is not, each measured rather than assumed: not the input (padding the
#: reference time, switching calendar, moving the epoch to 1850 and truncating
#: the series all leave the rate unchanged); not the ECA family, per the 57
#: clean operators above; and not the index calculation, because ``-f grb`` is
#: clean 10 of 10 and only the NetCDF path dies.
#:
#: What singles these two out is that they set the *reference time* of their own
#: output — ``eca_rr1`` to the data date, ``eca_pd`` to nothing at all — where
#: every other index leaves CDI's 1955-01-01 default alone. Replacing it is the
#: only thing that helps: chaining ``setreftime`` in front is clean 12 of 12,
#: while a chained ``copy`` — another full pass through the same writer — still
#: aborts 11 times in 12.
#:
#: The rewrite is transparent, which is why it is done silently. ``setreftime``
#: moves the epoch and rebases the offsets against it, so the verification date
#: the operator chose survives (``eca_rr1`` still stamps 2000-07-02) and so do
#: the values — checked field by field against the same run written as GRIB.
#: Only the encoding of the axis changes.
_ASSERTION_SAFE_REFERENCE_TIME = "setreftime,2000-01-01,00:00:00,days"
_NEEDS_REFERENCE_TIME = frozenset({"eca_pd", "eca_r1mm", "eca_rr1", "etccdi_r1mm"})


#: The same idea on the other stream, and a worse case. These are things CDO
#: says on **stderr** during a run that exits 0 and writes a well-formed file
#: whose contents are not what was asked for.
#:
#: Until this existed, ``stream_notices`` was handed stdout and nothing else, so
#: none of it reached any of the three reporting surfaces. The failure mode this
#: closes, measured on 2.6.3 against sample_climate_tg.nc and the anomaly file
#: built from it:
#:
#:     $ cdo eof,3 anom.nc eval.nc eofs.nc          # exit 0
#:     jacobi_1side (Warning): Eigenvalue computation with one-sided jacobi
#:                             scheme did not converge properly.
#:                             209628 of 209628 pairs of columns did not achieve
#:                             requested orthogonality of 1e-12
#:     cdo    eof (Warning): Setting Matrix and Eigenvalues to 0 before return
#:
#: ``eval.nc`` is then entirely zero — verified with ``cdo output -timmax -abs``,
#: which returned 0 — and the run looks exactly like a good one from every
#: surface. That is the worst shape a failure can take, and it was invisible.
#:
#: **It is not an eof-only problem**, which is the argument for putting the
#: patterns here rather than anywhere nearer the operator. A sweep of ~60
#: one-input operators over the same sample found four more that exit 0, warn
#: only on stderr, and write a file that is not what the name promises:
#:
#:     cdo dv2uv (Warning): Divergence not found!   / Vorticity not found!
#:     cdo uv2dv (Warning): U-wind not found!       / V-wind not found!
#:     cdo sp2gp (Warning): No spectral data found!
#:     cdo gp2sp (Warning): No data on regular Gaussian grid found!
#:
#: Measured: each of those wrote a byte-for-byte *copy of the input* under the
#: transformed name — same 648 points, same variable, 1899780 bytes — so the
#: only evidence a transformation did not happen was the swallowed warning.
#: Hence the last pattern, which is deliberately broad: any ``cdo <op>
#: (Warning): …`` line. CDO reserves that prefix for things it wants a human to
#: read, and a surface that shows all of them is right far more often than a
#: list of the ones somebody thought to enumerate.
_STDERR_NOTICES = (
    # The two halves of the eof non-convergence, matched separately because CDO
    # wraps the second onto its own continuation line with no operator prefix.
    re.compile(r"Setting Matrix and Eigenvalues to 0 before return"),
    re.compile(r"jacobi.*did not converge", re.IGNORECASE),
    re.compile(r"did not achieve requested orthogonality"),
    # The general case, and the reason the four transformation operators above
    # are covered without being named.
    re.compile(r"^cdo\s+\S+\s*\(Warning\):", re.IGNORECASE),
)


#: The subset of the above that means the run produced nothing usable. Kept
#: apart from the notices because the two answer different questions: a notice
#: changes how a result is read, and this changes whether there is a result at
#: all. See ``_determine_command_success`` for the argument.
_STDERR_FAILURES = (
    re.compile(r"Setting Matrix and Eigenvalues to 0 before return"),
)


def stream_notices(stdout: Optional[str],
                   stderr: Optional[str] = None) -> List[str]:
    """The lines of a run that the user needs to see, from either stream.

    Shared by the three surfaces that report a finished run — the operator form,
    the model builder and the batch runner — so none of them can quietly stop
    showing one. Returns the lines stripped and de-duplicated, stdout first and
    then stderr, in the order CDO emitted them; a run with nothing to report
    returns an empty list, which is the overwhelmingly common case.

    ``stderr`` is optional and defaults to None so that every existing caller
    keeps working unchanged while it is threaded through. The two streams are
    matched against *different* pattern sets rather than one combined set: what
    is worth lifting out of stdout is a statement of fact CDO makes in passing
    ("Filling up stream", the pack parameters), and what is worth lifting out of
    stderr is a warning. Matching stdout against the warning patterns would be
    harmless but pointless, and matching stderr against ``^<name>=<digits>$``
    would start quoting fragments of ordinary progress chatter.
    """
    notices: List[str] = []
    seen: set = set()
    for text, patterns in ((stdout, _STDOUT_NOTICES),
                           (stderr, _STDERR_NOTICES)):
        if not text:
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped in seen:
                continue
            if any(pattern.search(stripped) for pattern in patterns):
                seen.add(stripped)
                notices.append(stripped)
    return notices


def run_environment(overrides: Optional[Mapping[str, str]]) -> Optional[Dict[str, str]]:
    """The environment one CDO run should see, or None to inherit unchanged.

    ``None`` rather than ``dict(os.environ)`` for the no-overrides case, because
    ``subprocess.run(env=None)`` and ``QProcess`` with no environment set both
    mean "inherit", and that is the behaviour every run had before this existed.
    Returning a copy instead would be equivalent in principle and different in
    practice: it snapshots the environment at call time, and it makes the
    common path allocate a dictionary of a hundred-odd entries per run.

    Overrides are layered **on top of** ``os.environ``, never in place of it.
    CDO needs PATH to be found at all, and on macOS the bundled build needs
    DYLD_* to resolve its dylibs; handing it a four-entry environment would stop
    it starting. Empty and whitespace-only values are dropped rather than
    exported as empty strings — CDO reads ``CDO_WEIGHT_MODE=`` as a value and
    not as "unset", so a blank field in the GUI must mean "leave it alone".
    """
    if not overrides:
        return None
    cleaned = {str(name): str(value).strip()
               for name, value in overrides.items()
               if name and str(value).strip()}
    if not cleaned:
        return None
    environment = dict(os.environ)
    environment.update(cleaned)
    return environment


#: Where ``build.py`` stages Magics' runtime data inside the app bundle,
#: relative to the bundled ``cdo``. Magics appends ``share/magics`` itself, so
#: ``MAGPLUS_HOME`` points at the directory *containing* that.
#:
#: This constant and ``build._bundle_magics_data`` are one decision written in
#: two files: the bundler creates ``cdo_bundle/magics/share/magics`` and this
#: points at ``cdo_bundle/magics``. Changing either alone produces a bundle that
#: plots without coastlines and says nothing about it.
_BUNDLED_MAGICS_DIRNAME = "magics"


def magics_environment(binary: Optional[str]) -> Dict[str, str]:
    """``MAGPLUS_HOME`` for a bundled CDO, or empty for anything else.

    Magics opens its coastlines, fonts and colour tables by *path* at render
    time, not through the dynamic loader — so ``otool``-driven bundling cannot
    reach them and the loader cannot find them. Left unset, a bundled CDO looks
    for them at the absolute path they had on the **build machine**, typically a
    Homebrew prefix that does not exist on a user's Mac.

    The failure that makes this worth setting explicitly rather than hoping:
    it is quiet. Measured with ``MAGPLUS_HOME`` pointing at a nonexistent
    directory, ``shaded,device=png`` writes **no file at all** while
    ``shaded,device=ps`` writes an 8 KB file instead of 104 KB — and CDO exits
    0 in both cases. A user would get a plot with no coastlines, or no plot,
    and no error either way.

    Only set when the data is actually staged beside the binary, so a
    development run against a Homebrew CDO keeps that CDO's own compiled-in
    path and is not broken by a variable pointing at a directory that is not
    there.
    """
    if not binary:
        return {}
    home = Path(binary).parent / _BUNDLED_MAGICS_DIRNAME
    if not (home / "share" / "magics").is_dir():
        return {}
    return {"MAGPLUS_HOME": str(home)}


def stderr_indicates_failure(stderr: Optional[str]) -> bool:
    """True when CDO's stderr says the run produced nothing usable.

    Separate from :func:`stream_notices` and from the exit code, because it is a
    third thing: a run that exited 0, wrote a file, and told only stderr that
    the file is empty.
    """
    if not stderr:
        return False
    return any(pattern.search(stderr) for pattern in _STDERR_FAILURES)


#: Seconds the bundled-CDO runnability probe is allowed. A ``cdo -V`` is
#: near-instant natively; the ceiling is generous enough for a first Rosetta
#: translation and still short enough not to stall startup on a broken binary.
_CDO_PROBE_TIMEOUT = 10

#: Memoised result of :func:`_bundled_cdo_path`. The probe spawns a process, and
#: the function is called from both the constructor and the binary search.
_bundled_cdo_cache: Optional[Tuple[Optional[str]]] = None

logger = logging.getLogger(__name__)


def _cdo_binary_runs(path: Path) -> bool:
    """True when the binary at ``path`` actually executes on *this* machine.

    ``is_file() and os.access(X_OK)`` is not enough. A CDO built for a
    different architecture satisfies both and still cannot run: exec fails with
    ``OSError`` errno 86, ``Bad CPU type in executable``. That is the common
    case for a .app built on Apple Silicon around an x86_64 Homebrew CDO — it
    works on the build host if Rosetta 2 is installed, and fails on every clean
    arm64 Mac.

    Actually invoking the binary is the only check that answers the question
    being asked ("will operators run?") rather than a proxy for it. An arch
    comparison would reject a translated binary that genuinely works, and would
    still miss a missing dylib in the bundled closure.
    """
    try:
        proc = subprocess.run(
            [str(path), "-V"],
            capture_output=True,
            timeout=_CDO_PROBE_TIMEOUT,
        )
    except OSError as exc:
        # errno 86 (EBADARCH) is the wrong-architecture case; a missing dylib
        # in the staged closure surfaces here too.
        logger.warning("Bundled CDO at %s cannot be executed: %s", path, exc)
        return False
    except subprocess.SubprocessError as exc:
        logger.warning("Bundled CDO at %s did not respond to -V: %s", path, exc)
        return False

    if proc.returncode != 0:
        logger.warning(
            "Bundled CDO at %s exited %d on -V; treating it as unusable.",
            path, proc.returncode,
        )
        return False
    return True


def _bundled_cdo_path() -> Optional[str]:
    """Locate a *working* CDO binary shipped inside a PyInstaller-frozen build.

    When NCExplorer is launched from a PyInstaller-built ``.app`` / ``.exe`` /
    ELF, ``build.py`` stages ``cdo`` plus its dynamic-library dependencies into
    a sibling ``cdo_bundle/`` directory next to the entry executable (macOS
    layout: ``Contents/Resources/cdo_bundle/``).  Returning this path early
    means the GUI uses its own CDO instead of whatever happens to be on the
    user's ``PATH`` — important when the .app is launched from Finder, where
    Homebrew paths are typically absent.

    Each candidate is probed with :func:`_cdo_binary_runs` before it is
    accepted.  Returning ``None`` for a present-but-unrunnable binary is what
    lets the caller's ordinary ``PATH`` search take over, so a user who has
    their own working CDO installed gets a functioning app instead of
    ``Bad CPU type in executable`` on every operator.
    """
    global _bundled_cdo_cache
    if _bundled_cdo_cache is not None:
        return _bundled_cdo_cache[0]

    if not getattr(sys, "frozen", False):
        _bundled_cdo_cache = (None,)
        return None

    exe_dir = Path(sys.executable).resolve().parent
    candidates = [
        # macOS .app layout: MacOS/NCExplorer -> ../Resources/cdo_bundle/cdo
        exe_dir.parent / "Resources" / "cdo_bundle" / "cdo",
        # Linux / Windows one-file layout: same directory as the executable
        exe_dir / "cdo_bundle" / ("cdo.exe" if sys.platform == "win32" else "cdo"),
    ]

    resolved: Optional[str] = None
    for path in candidates:
        if not (path.is_file() and os.access(path, os.X_OK)):
            continue
        if not _cdo_binary_runs(path):
            logger.warning(
                "Ignoring the bundled CDO at %s and falling back to PATH.", path
            )
            continue
        resolved = str(path)
        break

    _bundled_cdo_cache = (resolved,)
    return resolved


@dataclass(slots=True)
class NCExplorerResult:
    """Structured result of a cdo operation.

    ``discovered_outputs`` are files the run created that no argument named —
    the DRS tree ``cmor`` writes under ``drs_root``, one file per output
    variable, whose names CMOR chooses from the project's template. They are
    found by comparing a scan of that directory against one taken before the
    run; see :meth:`NCExplorerIntegration._snapshot_tree`.

    Separate from ``output_file`` rather than folded into it, because they
    answer a different question. ``output_file`` is the path the *caller asked
    for* and is what the next step in a chain consumes; these are paths the
    caller could not have predicted, are plural, and exist so that something in
    this application can tell the user where the result went. An operator with
    ``nout == 0`` has no ``output_file`` and can still have written forty files.
    """
    success: bool
    stdout: str
    stderr: str
    output_file: Optional[str] = None
    execution_time: float = 0.0
    discovered_outputs: Tuple[str, ...] = ()
    #: The process's exit status, or None when it never produced one. Negative
    #: means a *signal* death: ``subprocess`` reports a process killed by signal
    #: N as ``-N``, and CDO does die that way — measured, roughly one run in six
    #: when a Magics operator aborts at the end of a pipe.
    #:
    #: Carried on the result rather than left in the log because ``success`` is
    #: a verdict and this is the evidence for it: a caller that wants to say
    #: "crashed" rather than "failed" has nothing else to read, and
    #: ``_annotate_failure`` needs it to name the signal. Optional with a None
    #: default, so every existing construction of this class is unchanged.
    returncode: Optional[int] = None

    def __bool__(self) -> bool:  # allow `if result:` semantics
        return self.success

    @property
    def killed_by_signal(self) -> bool:
        """Whether the run was killed rather than exiting on its own terms.

        The distinction a surface needs to tell a user "this crashed, the output
        is incomplete" instead of "this failed". See
        :meth:`NCExplorerIntegration._determine_command_success`.
        """
        return self.returncode is not None and self.returncode < 0


@dataclass(frozen=True, slots=True)
class CommandRecord:
    """One executed cdo invocation, as it actually ran.

    ``argv`` is the fully resolved command — after ``_build_command`` has applied
    the WSL/platform rewriting — because that, not the caller's list, is what the
    operating system saw. Kept as a list rather than a joined string so a later
    reproducible-script export can re-quote the arguments itself; ``as_text()``
    is for logs only.

    ``env`` is the run's environment *overrides* — never the whole inherited
    environment, which is neither reproducible nor anyone's business. It is
    recorded because for a good part of the Interpolation section an
    environment variable is the only thing that chose the numbers, and a
    command logged without it is a wrong log: ``cdo remapcon,r36x18 in out``
    and ``CDO_REMAP_NORM=destarea cdo remapcon,r36x18 in out`` are the same
    argv, both exit 0, both write a well-formed file, and they contain
    different values. Kept as a tuple of pairs rather than a dict so the record
    stays frozen and hashable, and in the order the schema declares them so two
    runs of the same operator log identically.

    ``as_text()`` therefore emits ``VAR=value cdo ...``, which is a line that
    can be pasted into a shell and reproduces the run.
    """

    argv: Tuple[str, ...]
    cwd: str
    returncode: Optional[int]
    duration: float
    env: Tuple[Tuple[str, str], ...] = ()

    def as_text(self) -> str:
        prefix = [f"{name}={value}" for name, value in self.env]
        return " ".join([*prefix, *self.argv])


@dataclass(frozen=True, slots=True)
class _ResolvedCall:
    """One validated operator call, ready to be handed to the process layer.

    The synchronous and the asynchronous paths both go through this, so the
    argument validation, the temporary path aliases and the post-run output
    relocations cannot drift apart between them.

    ``side_outputs`` are files the run creates that are *not* trailing argv
    arguments, because they were named by a parameter instead — ``tee``'s
    ``outfile2``. They are already inside the operator token, so they must
    never be appended to the command a second time, but everything else an
    output gets they get: the pre-run snapshot, the clean-up after a run that
    did not finish, and the move back from an alias path.

    ``append_sizes`` maps an output path to the size it had before the run, for
    the operators that extend an existing file rather than create one. Empty
    for all but ``cat``; see :data:`~.categories.APPENDING_OPERATORS`.

    ``variable_output`` says the run treats the output path as a *base* CDO
    appends to, rather than as the one file it was given. It is carried here
    rather than recomputed as ``nout == -1`` at each of the four places that
    need it, because it is not derivable from ``nout``. Two shapes reach it
    with ``nout == 1``: a ``gen*`` operator with ``map3d=true``, which writes
    ``<outfile><00001>.nc``, and the six Magics plot operators, whose trailing
    argument is an obase in CDO's own synopsis and which write
    ``<obase>_<variable>.<device>`` or ``<obase>.<device>``. See
    :func:`~.categories.writes_output_prefix` for both measurements. Computing
    it once here is also what keeps the synchronous and asynchronous paths from
    disagreeing about it.

    ``scan_root`` is a directory whose contents are compared before and after
    the run, for an operator that writes files no argument names. Empty for
    every operator but ``cmor``, which is ``nout == 0`` and still writes a DRS
    tree of NetCDF files under ``drs_root``. Without it, ``outputs``,
    ``aliased_outputs`` and ``side_outputs`` are all empty for such a run, so
    ``_existing_output_paths`` snapshots nothing, ``_discard_failed_outputs``
    removes nothing while reporting that it cleaned up, and no surface can say
    the run produced anything at all.
    """

    cmd: List[str]
    outputs: List[str]
    aliased_outputs: List[str]
    relocations: List[Dict[str, str]]
    nout: int
    side_outputs: List[str] = field(default_factory=list)
    append_sizes: Dict[str, int] = field(default_factory=dict)
    variable_output: bool = False
    scan_root: str = ""
    #: Where the process is started, or "" for the default — the temporary
    #: store's base, which is what both execution paths fall back to and what
    #: every operator but one gets. See
    #: :meth:`NCExplorerIntegration._run_directory`.
    cwd: str = ""


@dataclass(frozen=True, slots=True)
class PreparedRun:
    """Everything needed to run one operator, minus the running itself.

    :meth:`NCExplorerIntegration.prepare_operator_run` does all the work
    :meth:`~NCExplorerIntegration.execute_operator` does *up to* the point of
    blocking, and returns this. ``finalise`` is the other half: given whatever
    the process produced, it records the invocation in the command history,
    applies CDO's exit-code quirks and moves any aliased output back to the
    path the caller asked for.

    Splitting it here is what lets an asynchronous driver reuse the synchronous
    path's rules rather than reimplement them — and, in particular, keeps
    ``_record_command`` on every path including cancel and timeout.
    """

    argv: Tuple[str, ...]
    #: The directory the process is started in — always the temporary store's
    #: base directory, on both the synchronous and the asynchronous path, and
    #: pinned rather than inherited so two runs of one command cannot differ by
    #: where the application happened to be launched from.
    #:
    #: For every operator but one this is bookkeeping: paths reach argv absolute
    #: and the run produces the same files wherever it starts. For ``cmor`` it
    #: decides the result. ``drs_root`` defaults to the working directory, so
    #: this is where the output tree lands; ``info`` defaults to
    #: ``CWD/.cdocmorinfo``, so this is where the run's global attributes are
    #: read from. See ``categories.CWD_DEPENDENT_OPERATORS``, and
    #: ``session_log.OperatorRequest.cwd`` for how it reaches the exported
    #: script — a recorded command whose output depended on an unrecorded
    #: directory does not reproduce, and this is the operator that has one.
    cwd: str
    timeout: int
    finalise: Callable[..., NCExplorerResult]
    #: Environment variables to set for this run *on top of* the inherited
    #: environment — never a replacement for it, since CDO needs PATH and its
    #: own library variables to start at all. Empty for every run that does not
    #: ask, which is nearly all of them. See :func:`run_environment`.
    env: Mapping[str, str] = field(default_factory=dict)
    #: File whose contents are fed to the process on standard input, or "" for
    #: an immediate EOF. Never an inherited terminal — see
    #: :meth:`NCExplorerIntegration._execute_command`.
    stdin_path: str = ""


class NCExplorerError(Exception):
    """Custom exception raised for cdo-related failures."""

    def __init__(self, message: str, *, stdout: str = "", stderr: str = "", returncode: Optional[int] = None):
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.msg = f'(returncode:{returncode}) {stderr}'

    def __str__(self):
        return self.msg


class NCExplorerIntegration:
    """
    Cross-platform integration layer for the cdo command-line tool.  On Windows,
    the wrapper automatically chooses between a native build and the WSL binary.
    """

    # ----- Complete operator list from CDO 2.6.0 catalog -----
    ALL_OPERATORS = [
        # Information (nout == 0)
        #
        # ``cmor`` was in this block, on the same ``nout == 0`` reasoning that
        # put it in the INFORMATION category, and it is as wrong here as it was
        # there: it writes a DRS tree of NetCDF files and is ``nout == 0`` only
        # because CMOR names them rather than CDO. Moved to Import/Export below
        # to match ``categories._MODULE_CATEGORY``, where the argument is set
        # out in full. ``conv_cmor_table`` and ``dump_cmor_table`` stay — both
        # are genuinely ``(0, 0)`` and print to stdout.
        'cdiread', 'cinfo', 'codetab', 'conv_cmor_table', 'dcw', 'diff', 'diffc',
        'diffn', 'diffp', 'difftest', 'diffv', 'dump_cmor_table', 'dumpmap', 'filedes', 'gmtcells',
        'gmtxyz', 'gradsdes', 'gridcellindex', 'griddes', 'griddes2', 'info', 'infoc', 'infon',
        'infop', 'infov', 'linfo', 'map', 'ncode', 'ndate', 'ngridpoints', 'ngrids',
        'nlevel', 'nmon', 'npar', 'ntime', 'nvar', 'nyear', 'output', 'outputarr',
        'outputbounds', 'outputboundscpt', 'outputcenter', 'outputcenter2', 'outputcentercpt',
        'outputext', 'outputf', 'outputfld', 'outputint', 'outputkey', 'outputkml', 'outputsrv',
        'outputtab', 'outputtri', 'outputts', 'outputvector', 'outputvrml', 'outputxyz',
        'partab', 'partab2', 'seinfo', 'seinfoc', 'seinfon', 'seinfop',
        'showattribute', 'showattsvar', 'showchunkspec', 'showcode', 'showdate', 'showfilter',
        'showformat', 'showgrid', 'showhistory', 'showlevel', 'showltype', 'showmon',
        'showname', 'showparam', 'showstdname', 'showtime', 'showtimestamp', 'showunit',
        'showvar', 'showyear', 'sinfo', 'sinfoc', 'sinfon', 'sinfop', 'sinfov',
        'spartab', 'specinfo', 'testcellsearch', 'testpointsearch', 'tinfo', 'vardes',
        'vct', 'vct2', 'verifygrid', 'verifyweights', 'vinfo', 'vlist',
        'writeremapscrip', 'xsinfo', 'xsinfoc', 'xsinfon', 'xsinfop', 'zaxisdes',

        # File operations
        'after', 'afterburner', 'cat', 'clone', 'copy', 'delete', 'merge', 'mergegrid',
        'mergetime', 'replace', 'select', 'splitcode', 'splitdate', 'splitdatetime',
        'splitday', 'splitensemble', 'splitgrid', 'splithour', 'splitlevel', 'splitmon',
        'splitname', 'splitparam', 'splitrec', 'splitseas', 'splitsel', 'splittabnum',
        'splitvar', 'splityear', 'splityearmon', 'splitzaxis', 'szip',

        # Selection
        'del29feb', 'delattribute', 'delcode', 'delday', 'delgridcell', 'delmulti',
        'delname', 'delparam', 'delta_pressure', 'deltat', 'delvar',
        'selcircle', 'selcode', 'seldate', 'selday', 'selgrid', 'selgridcell',
        'selgridname', 'selhour', 'selindexbox', 'sellevel', 'sellevidx',
        'sellonlatbox', 'selltype', 'selmon', 'selmonth', 'selmulti', 'selname',
        'seloperator', 'selparam', 'selrec', 'selregion', 'selseas', 'selseason',
        'selsmon', 'selstdname', 'seltabnum', 'seltime', 'seltimeidx', 'seltimestep',
        'selvar', 'selyear', 'selyearidx', 'selzaxis', 'selzaxisname',

        # Conditional selection
        'ifnotthen', 'ifnotthenc', 'ifthen', 'ifthenc', 'ifthenelse',

        # Comparison
        'eq', 'eqc', 'ge', 'gec', 'gt', 'gtc', 'le', 'lec', 'lt', 'ltc', 'ne', 'nec',
        'ymoneq', 'ymonge', 'ymongt', 'ymonle', 'ymonlt', 'ymonne',
        'yseaseq', 'yseasge', 'yseasgt', 'yseasle', 'yseaslt', 'yseasne',

        # Modification
        'changemulti', 'chcode', 'chlevel', 'chlevelc', 'chlevelv', 'chltype', 'chname',
        'chparam', 'chtabnum', 'chunit', 'chvar', 'enlarge', 'fillmiss', 'fillmiss2',
        'invertlat', 'invertlatdata', 'invertlatdes', 'invertlev', 'invertlon',
        'invertlondata', 'invertlondes', 'mask', 'maskcircle', 'maskindexbox',
        'masklonlatbox', 'maskregion', 'setattribute', 'setcalendar', 'setchunkspec',
        'setcindexbox', 'setclonlatbox', 'setcode', 'setcodetab', 'setctomiss',
        'setdate', 'setday', 'setfilter', 'setgrid', 'setgridarea', 'setgridcell',
        'setgridmask', 'setgridnumber', 'setgridtype', 'setgriduri', 'sethalo',
        'setlevel', 'setltype', 'setmaxsteps', 'setmiss', 'setmisstoc', 'setmisstodis',
        'setmisstonn', 'setmissval', 'setmon', 'setname', 'setparam', 'setpartab',
        'setpartabc', 'setpartabn', 'setpartabp', 'setpartabv', 'setprojparams',
        'setrcaname', 'setreftime', 'setrtoc', 'setrtoc2', 'setrtomiss', 'setstdname',
        'settabnum', 'settaxis', 'settbounds', 'settime', 'settunits', 'setunit',
        'setvals', 'setvar', 'setvrange', 'setyear', 'setzaxis', 'shifttime',
        'shiftx', 'shifty', 'sortcode', 'sortlevel', 'sortname', 'sortparam',
        'sorttaxis', 'sorttimestamp', 'sortvar', 'timfillmiss', 'vertfillmiss',

        # Arithmetic
        'abs', 'acos', 'add', 'addc', 'aexpr', 'aexprf', 'anomaly', 'asin',
        'atan', 'atan2', 'cos', 'div', 'divc', 'divcoslat', 'divdpm', 'divdpy',
        'exp', 'expr', 'exprf', 'int', 'ln', 'log', 'log10', 'max', 'maxc',
        'min', 'minc', 'mod', 'mul', 'mulc', 'mulcoslat', 'muldoy', 'muldpm',
        'muldpy', 'nint', 'not', 'pow', 'reci', 'sin', 'sqr', 'sqrt', 'sub',
        'subc', 'tan',

        # Statistical values
        'dayadd', 'dayavg', 'daycount', 'daydiv', 'daymax', 'daymean', 'daymin',
        'daymul', 'daypctl', 'dayrange', 'daystd', 'daystd1', 'daysub', 'daysum',
        'dayvar', 'dayvar1',
        'dhouravg', 'dhourmax', 'dhourmean', 'dhourmin', 'dhourrange', 'dhourstd',
        'dhourstd1', 'dhoursum', 'dhourvar', 'dhourvar1',
        'dminuteavg', 'dminutemax', 'dminutemean', 'dminutemin', 'dminuterange',
        'dminutestd', 'dminutestd1', 'dminutesum', 'dminutevar', 'dminutevar1',
        'ensavg', 'ensbrs', 'enscrps', 'enskurt', 'ensmax', 'ensmean', 'ensmedian',
        'ensmin', 'enspctl', 'ensrange', 'ensrkhistspace', 'ensrkhisttime', 'ensroc',
        'ensskew', 'ensstd', 'ensstd1', 'enssum', 'ensvar', 'ensvar1',
        'fldavg', 'fldcor', 'fldcount', 'fldcovar', 'fldint', 'fldkurt', 'fldmax',
        'fldmean', 'fldmedian', 'fldmin', 'fldpctl', 'fldrange', 'fldrms', 'fldskew',
        'fldstd', 'fldstd1', 'fldsum', 'fldvar', 'fldvar1',
        'gridboxavg', 'gridboxkurt', 'gridboxmax', 'gridboxmean', 'gridboxmedian',
        'gridboxmin', 'gridboxrange', 'gridboxskew', 'gridboxstd', 'gridboxstd1',
        'gridboxsum', 'gridboxvar', 'gridboxvar1',
        'histcount', 'histfreq', 'histmean', 'histsum',
        'houravg', 'hourcount', 'hourmax', 'hourmean', 'hourmin', 'hourpctl',
        'hourrange', 'hourstd', 'hourstd1', 'hoursum', 'hourvar', 'hourvar1',
        'meravg', 'merkurt', 'mermax', 'mermean', 'mermedian', 'mermin', 'merpctl',
        'merrange', 'merskew', 'merstd', 'merstd1', 'mersum', 'mervar', 'mervar1',
        'monadd', 'monavg', 'moncount', 'mondiv', 'monmax', 'monmean', 'monmin',
        'monmul', 'monpctl', 'monrange', 'monstd', 'monstd1', 'monsub', 'monsum',
        'monvar', 'monvar1',
        'runavg', 'runmax', 'runmean', 'runmin', 'runpctl', 'runrange', 'runstd',
        'runstd1', 'runsum', 'runvar', 'runvar1',
        'seasavg', 'seascount', 'seasmax', 'seasmean', 'seasmin', 'seasmonavg',
        'seasmonmean', 'seaspctl', 'seasrange', 'seasstd', 'seasstd1', 'seassum',
        'seasvar', 'seasvar1',
        'timavg', 'timcor', 'timcount', 'timcovar', 'timcumsum', 'timederivative',
        'timmax', 'timmaxidx', 'timmean', 'timmin', 'timminidx', 'timpctl',
        'timrange', 'timrmsd', 'timselavg', 'timselmax', 'timselmean', 'timselmin',
        'timselpctl', 'timselrange', 'timselstd', 'timselstd1', 'timselsum',
        'timselvar', 'timselvar1', 'timsort', 'timstd', 'timstd1', 'timsum',
        'timvar', 'timvar1',
        'vertavg', 'vertcum', 'vertcumhl', 'vertint', 'vertmax', 'vertmean',
        'vertmin', 'vertrange', 'vertstd', 'vertstd1', 'vertsum', 'vertvar',
        'vertvar1', 'vertwind',
        'ydayadd', 'ydayavg', 'ydaydiv', 'ydaymax', 'ydaymean', 'ydaymin',
        'ydaymul', 'ydaypctl', 'ydayrange', 'ydaystd', 'ydaystd1', 'ydaysub',
        'ydaysum', 'ydayvar', 'ydayvar1',
        'ydrunavg', 'ydrunmax', 'ydrunmean', 'ydrunmin', 'ydrunpctl', 'ydrunstd',
        'ydrunstd1', 'ydrunsum', 'ydrunvar', 'ydrunvar1',
        'yearadd', 'yearavg', 'yearcount', 'yeardiv', 'yearmax', 'yearmaxidx',
        'yearmean', 'yearmin', 'yearminidx', 'yearmonavg', 'yearmonmean', 'yearmul',
        'yearpctl', 'yearrange', 'yearstd', 'yearstd1', 'yearsub', 'yearsum',
        'yearvar', 'yearvar1',
        'yhouradd', 'yhouravg', 'yhourdiv', 'yhourmax', 'yhourmean', 'yhourmin',
        'yhourmul', 'yhourrange', 'yhourstd', 'yhourstd1', 'yhoursub', 'yhoursum',
        'yhourvar', 'yhourvar1',
        'ymonadd', 'ymonavg', 'ymondiv', 'ymonmax', 'ymonmean', 'ymonmin',
        'ymonmul', 'ymonpctl', 'ymonrange', 'ymonstd', 'ymonstd1', 'ymonsub',
        'ymonsum', 'ymonvar', 'ymonvar1',
        'yseasadd', 'yseasavg', 'yseasdiv', 'yseasmax', 'yseasmean', 'yseasmin',
        'yseasmul', 'yseaspctl', 'yseasrange', 'yseasstd', 'yseasstd1', 'yseassub',
        'yseassum', 'yseasvar', 'yseasvar1',
        'zonavg', 'zonkurt', 'zonmax', 'zonmean', 'zonmedian', 'zonmin', 'zonpctl',
        'zonrange', 'zonskew', 'zonstd', 'zonstd1', 'zonsum', 'zonvar', 'zonvar1',
        'boxavg',

        # Regression
        'addtrend', 'detrend', 'regres', 'subtrend', 'trend',

        # Interpolation
        'ap2hl', 'ap2hlx', 'ap2pl', 'ap2plx', 'bandpass', 'distgrid',
        'genbic', 'genbil', 'gencon', 'gendis', 'gengrid', 'genknn', 'genlaf',
        'genlevelbounds', 'gennn', 'genycon', 'genycon2test',
        'gh2hl', 'gh2hlx', 'highpass', 'intgridbil', 'intgriddis', 'intgridknn',
        'intgridnn', 'intgridtraj', 'intlevel', 'intlevel3d', 'intlevelx',
        'intlevelx3d', 'intntime', 'inttime', 'intyear', 'isosurface', 'lowpass',
        'ml2hl', 'ml2hlx', 'ml2pl', 'ml2plx', 'remap', 'remapavg', 'remapavgtest',
        'remapbic', 'remapbil', 'remapcon', 'remapdis', 'remapeta', 'remapeta_s',
        'remapeta_z', 'remapknn', 'remapkurt', 'remaplaf', 'remapmax', 'remapmean',
        'remapmedian', 'remapmin', 'remapnn', 'remaprange', 'remapskew', 'remapstd',
        'remapstd1', 'remapsum', 'remapvar', 'remapvar1', 'remapycon', 'remapycon2test',
        'samplegrid', 'samplegridicon', 'smooth', 'smooth9',

        # Transformation
        # ``spcut`` moved here from the Miscellaneous group below, where this
        # list had it while ``OPERATOR_CATEGORIES`` filed it under
        # Transformation. The schema is right and this was wrong: ``cdo -h
        # spcut`` on 2.6.3 prints the *Specconv* module page — the one headed
        # "sp2sp - Spectral to spectral" — so spcut shares a module with sp2sp,
        # which is two entries along on this line.
        'dv2ps', 'dv2uv', 'dv2uvl', 'fc2gp', 'fc2sp', 'fourier', 'fourier2grid',
        'gp2fc', 'gp2sp', 'gp2spl', 'grid2fourier', 'mrotuv', 'mrotuvb',
        'sp2fc', 'sp2gp', 'sp2gpl', 'sp2sp', 'spcut', 'spectrum', 'uv2dv',
        'uv2dv_cfd', 'uv2dvl', 'uv2vr_cfd',

        # Formatted I/O — the old name for what the schema now calls
        # Import/Export; see ``categories.NCExplorerCategory.IMPORT_EXPORT``.
        # ``cmor`` is here rather than under Information because its product is
        # a file written to another consumer's layout, which is what this group
        # is.
        'cmor', 'input', 'inputext', 'inputsrv',

        # Graphic with Magics
        #
        # These six were in the Miscellaneous block below while
        # ``categories._MODULE_CATEGORY`` named none of their three modules, so
        # both places agreed — wrongly, and for the same reason. Now that CDO's
        # own module titles place them (see
        # ``categories.NCExplorerCategory.GRAPHICS``), this comment group has to
        # follow, or the two say different things about the same six operators.
        #
        # Kept in this list rather than dropped, even though the installed CDO
        # cannot run them: ``cdo --config has-magics`` is a property of the
        # *binary*, the binary is a setting, and hiding an operator would make a
        # MAGICS-enabled CDO's plotting unreachable from the app. The refusal
        # belongs at the point of running, where it can name the cause; see
        # ``missing_build_feature``.
        #
        # ``stream`` is undocumented — no Magstream page exists — but is
        # registered by the binary under Magvector's module and prints
        # Magvector's help. It is listed with the other five on that evidence.
        'contour', 'graph', 'grfill', 'shaded', 'stream', 'vector',

        # Miscellaneous
        'adipot', 'adisit', 'air_density', 'arg', 'bitrounding', 'bottomvalue',
        'cdiwrite', 'cloudlayer', 'cmorlite', 'collgrid', 'complextopol',
        'complextorect', 'conj', 'consecsum', 'consects', 'const',
        'coshill', 'duplicate', 'eof', 'eof3d', 'eof3dspatial', 'eof3dtime',
        'eofcoeff', 'eofcoeff3d', 'eofspatial', 'eoftime', 'export_e5ml',
        'fdns', 'for', 'gheight', 'gheight_full', 'gheight_half', 'gheighthalf',
        'globavg', 'gridarea', 'gridcellidx', 'griddx',
        'griddy', 'gridmask', 'gridweights', 'harmonic', 'hi', 'hpdegrade',
        'hpupgrade', 'hurr', 'im', 'import_binary', 'import_cmsaf', 'import_e5ml',
        'import_fv3grid', 'import_grads', 'import_obs', 'imtocomplex', 'lic',
        'mastrfu', 'meandiff2test', 'ncopy', 'pack', 'pardup', 'parmul', 'pinfo',
        'pinfov', 'pressure', 'pressure_full', 'pressure_half', 'projuvLatLon',
        'query', 'rand', 'random', 're', 'recttocomplex', 'reducegrid',
        'retocomplex', 'rhopot', 'rotuvN', 'rotuvNorth', 'rotuvb',
        'sealevelpressure', 'seq', 'sincos', 'stdatm',
        'strbre', 'strgal', 'strwin', 'subgrid', 'symmetrize',
        'tee', 'temp', 'testfield', 'thinout', 'topo', 'topvalue', 'tpnhalo',
        'transxy', 'tstepcount', 'unpack', 'unsetgridmask', 'usegridnumber',
        'uvDestag', 'varquot2test', 'varrms', 'varsavg', 'varskurt', 'varsmax',
        'varsmean', 'varsmedian', 'varsmin', 'varspctl', 'varsrange', 'varsskew',
        'varsstd', 'varsstd1', 'varssum', 'varsvar', 'varsvar1', 'wct',
        'writegrid', 'writerandom', 'zs2zl', 'zs2zlx', 'zsdepth',

        # ECA / ETCCDI indices
        'eca_cdd', 'eca_cfd', 'eca_csu', 'eca_cwd', 'eca_cwdi', 'eca_cwfi',
        'eca_etr', 'eca_fd', 'eca_gsl', 'eca_hd', 'eca_hwdi', 'eca_hwfi',
        'eca_id', 'eca_pd', 'eca_r10mm', 'eca_r1mm', 'eca_r20mm', 'eca_r75p',
        'eca_r75ptot', 'eca_r90p', 'eca_r90ptot', 'eca_r95p', 'eca_r95ptot',
        'eca_r99p', 'eca_r99ptot', 'eca_rr1', 'eca_rx1day', 'eca_rx5day',
        'eca_sdii', 'eca_su', 'eca_tg10p', 'eca_tg90p', 'eca_tn10p',
        'eca_tn90p', 'eca_tr', 'eca_tx10p', 'eca_tx90p',
        'etccdi', 'etccdi_cdd', 'etccdi_csdi', 'etccdi_cwd', 'etccdi_fd',
        'etccdi_gsl', 'etccdi_hd', 'etccdi_id', 'etccdi_r10mm', 'etccdi_r1mm',
        'etccdi_r20mm', 'etccdi_r95p', 'etccdi_r99p', 'etccdi_rx1day',
        'etccdi_rx1daymon', 'etccdi_rx5day', 'etccdi_rx5daymon', 'etccdi_sdii',
        'etccdi_su', 'etccdi_tn10p', 'etccdi_tn90p', 'etccdi_tr', 'etccdi_tx10p',
        'etccdi_tx90p', 'etccdi_wsdi',
    ]

    # The seven category sets that used to live here — INFO_OPERATORS,
    # SINGLE_FILE_OPERATORS, TWO_INPUT_OPERATORS, THREE_INPUT_OPERATORS,
    # MULTI_INPUT_OPERATORS, SELECTION_OPERATORS, RUNNING_OPERATORS — and
    # EXTRA_PARAM_COUNTS are gone. About 255 lines, and nothing in the repository
    # read any of the seven; EXTRA_PARAM_COUNTS had exactly one reader, in
    # _invoke_legacy_operator, on a branch that cannot be taken because every
    # operator that path accepts comes from ``cdo --operators`` and is therefore
    # in OPERATOR_SCHEMA.
    #
    # They were also wrong. SINGLE_FILE_OPERATORS listed the Math operators but
    # omitted pow, reci, not, mulcoslat and the whole Arithdays family, which is
    # what a second copy of the truth does when only one copy is consulted.
    # OPERATOR_SCHEMA is built from the installed binary's own catalog and
    # answers all of these questions:
    #
    #     spec.nin / spec.nout            in place of the arity sets
    #     spec.category                   in place of the category sets
    #     operator_total_param_count()    in place of EXTRA_PARAM_COUNTS


    #: How many entries ``command_history`` retains. A long session can run
    #: thousands of operators; only the recent ones are of any diagnostic use.
    MAX_COMMAND_HISTORY = 100

    # ----- Construction -----

    def __init__(self,
                 *,
                 NCExplorer_binary_path: str = "cdo",
                 temp_dir: Optional[str] = None,
                 use_wsl: Optional[bool] = None,
                 force_platform: Optional[str] = None,
                 auto_find_NCExplorer: bool = True,
                 ) -> None:

        self.NCExplorer_binary = NCExplorer_binary_path
        self.logger = logging.getLogger(self.__class__.__name__)
        self.temp_dir = temp_dir or tempfile.gettempdir()
        self._tstore = TempFileStore(temp_dir)
        self.last_command: str = ""
        # Developer-facing execution trail. Bounded because a long session can
        # run thousands of operators, and nothing in the GUI reads it — this is
        # groundwork for exporting a reproducible script later.
        self.command_history: List[CommandRecord] = []

        # Detect OS
        self.platform = (force_platform or platform.system()).lower()

        # Prefer the CDO bundled inside a frozen build over anything on PATH —
        # the .app should be self-contained and reproducible.
        if auto_find_NCExplorer:
            bundled = _bundled_cdo_path()
            if bundled:
                self.NCExplorer_binary = bundled
                self.logger.info("Using bundled NCExplorer at: %s", bundled)
            elif not self._test_NCExplorer_availability(use_wsl=False):
                found_NCExplorer = self._find_NCExplorer_binary()
                if found_NCExplorer:
                    self.NCExplorer_binary = found_NCExplorer
                    self.logger.info("Found NCExplorer at: %s", found_NCExplorer)

        if self.platform == "windows":
            self.use_wsl = self._init_windows_NCExplorer(use_wsl)
        elif self.platform in ("linux", "darwin"):
            self.use_wsl = False
            if not self._verify_unix_NCExplorer():
                # Provide helpful error message with suggestions
                error_msg = self._get_installation_help()
                raise NCExplorerError(error_msg)
        else:
            raise NCExplorerError(f"Unsupported platform: {self.platform}")

        self.operator_signatures, self.operator_descriptions = self._load_operator_metadata()

        # Dynamically create operator methods
        self._generate_operator_methods()

    def _load_operator_metadata(self) -> Tuple[Dict[str, Tuple[int, int]], Dict[str, str]]:
        """Load operator signatures from the installed CDO binary, with static fallback."""
        command = self._build_command([self.NCExplorer_binary, "--operators"])
        signatures: Dict[str, Tuple[int, int]] = {}
        descriptions: Dict[str, str] = {}

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            if result.returncode == 0:
                pattern = re.compile(r"^(\S+)\s+(.*?)\s+\((-?\d+)\|(-?\d+)\)\s*$")
                for line in result.stdout.splitlines():
                    match = pattern.match(line.rstrip())
                    if not match:
                        continue
                    name, description, nin, nout = match.groups()
                    signatures[name] = (int(nin), int(nout))
                    descriptions[name] = description.strip()
        except (subprocess.SubprocessError, OSError):
            signatures = {}
            descriptions = {}

        if signatures:
            return signatures, descriptions

        # The binary could not be probed. Fall back to the pinned catalog by
        # way of the schema, rather than to a table kept by hand beside it.
        #
        # This used to read ``OPERATOR_SIGNATURES``, which covered 716 of the
        # catalog's 943 operators and gave the missing 227 no entry at all —
        # and an operator with no entry here is rejected outright by
        # ``_resolve_operator_call``'s "Unknown or unavailable operator". So the
        # fallback was not merely coarser than the schema, it made 227
        # operators unrunnable in exactly the situation a fallback exists to
        # survive. Descriptions come back too, which the old path returned empty.
        try:
            from .categories import OPERATOR_SCHEMA
        except ImportError:                                     # pragma: no cover
            try:
                from ..core.categories import OPERATOR_SCHEMA
            except ImportError:
                OPERATOR_SCHEMA = {}

        return (
            {name: (spec.nin, spec.nout) for name, spec in OPERATOR_SCHEMA.items()},
            {name: spec.description for name, spec in OPERATOR_SCHEMA.items()},
        )

    @staticmethod
    def _coerce_string_list(values: Optional[Union[str, os.PathLike, List[str], Tuple[str, ...]]]) -> List[str]:
        """Normalise a file/parameter value into a string list."""
        if values is None:
            return []
        if isinstance(values, (str, os.PathLike)):
            return [str(values)]
        return [str(value) for value in values]

    @staticmethod
    def _safe_path_token(value: str) -> str:
        """Create a filesystem-safe token for temporary path aliases."""
        return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_") or "path"

    #: Input formats that reference a sibling file by a path relative to
    #: themselves, and therefore cannot be aliased file-by-file. Only GrADS
    #: descriptors do this today; the suffix is the whole test because it is
    #: what CDO itself dispatches on.
    _SIDECAR_SUFFIXES = frozenset({".ctl"})

    #: Operators that write a file **beside their input**, in the input's own
    #: directory, with no argument that can redirect it.
    #:
    #: One operator does: ``gradsdes`` writes ``<input-stem>.ctl`` (and a
    #: ``.gmp`` map alongside it for GRIB1 input). ``cdo gradsdes infile`` has
    #: no output slot at all — it is ``nout == 0`` — so that descriptor is the
    #: entire result of the run.
    #:
    #: It is listed here rather than detected, because there is nothing to
    #: detect until after the command has run and by then the file is already
    #: in the wrong place. See :meth:`_create_input_alias` for what the listing
    #: buys and why the alternative was rejected.
    #:
    #: ``dumpmap`` shares the module and is deliberately absent: it *reads* a
    #: GrADS map file and prints it, writing nothing.
    _OPERATORS_WRITING_BESIDE_INPUT = frozenset({"gradsdes"})

    def _create_input_alias(self, original: str,
                            *, alias_whole_directory: bool = False) -> str:
        """Create a temporary alias for an input path that contains spaces.

        A plain file symlink is wrong for a GrADS ``.ctl``, and measurably so.
        The descriptor names its binary with ``DSET ^demo.bin``, where ``^``
        means "the directory this .ctl is in" — and CDO resolves that against
        the *lexical* directory of the path it was handed, not against the
        symlink's target. Measured on 2.6.3 with the .ctl living in a directory
        whose name contains a space:

            ln -s "…/with space/demo.ctl" …/aliases/in_1_demo.ctl
            cdo -f nc import_binary …/aliases/in_1_demo.ctl out.nc
            -> cdo import_binary (Abort): Could not open file:
               …/aliases/demo.bin

        The alias moved the descriptor and left its data behind, so the operator
        this app offers for reading raw binary could not read any raw binary
        whose path contained a space — the exact case aliasing exists to rescue.

        Symlinking the *containing directory* instead fixes it, because the
        sibling then resolves through the same link: ``…/aliases/dir_1/demo.ctl``
        finds ``…/aliases/dir_1/demo.bin`` finds ``…/with space/demo.bin``.
        Measured with the same fixture: exit 0, and the NetCDF file written.

        Refusing was the alternative and is worse — the directory link costs one
        symlink and keeps every documented use of the operator working, whereas
        a refusal would reject a call CDO handles perfectly well when the path
        is spelled for it.

        ``alias_whole_directory`` asks for the same directory link for the
        opposite reason: not because the input *reads* a sibling, but because
        the operator *writes* one. ``gradsdes`` puts ``<stem>.ctl`` in the
        directory of the file it was handed and offers no way to redirect it,
        so with a plain file alias the descriptor lands in the temp alias
        directory and is silently lost — and it is the only product of the run,
        since ``gradsdes`` writes no output file. Measured on 2.6.3 against
        ``…/with space/sample.nc``:

            plain file alias   -> aliases/in_1_sample.ctl   (wrong directory,
                                  and named after the alias rather than the
                                  file the user chose)
            directory alias    -> …/with space/sample.ctl   (exit 0)

        The directory link is what makes the second row work: CDO writes to
        ``aliases/dir_1/sample.ctl``, and ``dir_1`` *is* the user's directory,
        so the write lands beside their file under its own name.

        Two alternatives were considered. Exempting ``gradsdes`` from aliasing
        altogether is not available: CDO cannot take a space in a path at all,
        even passed as a single argv element — ``cdo gradsdes "sp/with
        space/sample.nc"`` is "To many inputs", because CDO re-splits its own
        command line. Detecting the descriptor afterwards and moving it back
        would work, but has to undo the alias's *name* as well as its
        directory, and would run after a command this layer may have already
        reported on; the link gets both right before CDO starts.
        """
        if " " not in original:
            return original

        alias_dir = Path(self._tstore.base_dir) / "cdo_path_aliases"
        alias_dir.mkdir(parents=True, exist_ok=True)
        original_path = Path(original).expanduser()

        # A descriptor is aliased with its whole directory, so anything it
        # references relative to itself comes along — and so is an input whose
        # operator writes a sibling, so that what it writes comes back.
        if (alias_whole_directory
                or original_path.suffix.lower() in self._SIDECAR_SUFFIXES):
            parent_alias = alias_dir / f"dir_{time.time_ns()}"
            try:
                parent_alias.symlink_to(original_path.parent,
                                        target_is_directory=True)
            except OSError:
                # No symlinks available (Windows without the privilege). Copy
                # the descriptor *and* its siblings rather than the descriptor
                # alone, since the descriptor alone is the broken case above.
                shutil.copytree(original_path.parent, parent_alias)
            return str(parent_alias / original_path.name)

        alias_name = f"in_{time.time_ns()}_{self._safe_path_token(original_path.name)}"
        alias_path = alias_dir / alias_name

        try:
            alias_path.symlink_to(original_path)
        except OSError:
            shutil.copy2(original_path, alias_path)

        return str(alias_path)

    def _alias_file_parameters(
        self, operator: str, parameters: List[str],
    ) -> Tuple[List[str], List[str], List[Dict[str, str]]]:
        """Alias every file-valued parameter, routing outputs as outputs.

        Returns ``(aliased_parameters, side_outputs, relocations)``.

        ``_create_input_alias`` is a no-op for a path without spaces, so this
        costs nothing for the ordinary case and fixes the one that used to
        produce a command CDO could not parse.

        A parameter the operator *writes* — ``tee``'s ``outfile2`` and
        ``writeremapscrip``'s ``scrip`` — goes through ``_prepare_output_target``
        instead, which is the whole point of :attr:`OperatorParam.writes`.
        Sending it through the input aliaser was wrong in four ways at once, and
        only the first is visible on this platform:

        * the input aliaser does ``alias_path.symlink_to(original_path)`` on a
          target that does not exist yet, falling back to ``shutil.copy2`` on
          ``OSError``. POSIX allows the dangling symlink, so it happens to work
          here; on Windows ``symlink_to`` raises and ``copy2`` then fails on a
          missing source, so ``cdo tee`` with a space in the path is broken.
        * it produced no relocation entry, so the file was left in the alias
          directory and never moved to the path the user asked for.
        * ``_existing_output_paths`` and ``_discard_partial_outputs`` never saw
          it, so a failed run left a partial second file behind while reporting
          that it had cleaned up after itself.
        * nothing downstream could tell the file had been written at all.

        ``side_outputs`` is deliberately separate from the call's ``outputs``:
        these paths are already inside the operator token and must not be
        appended to argv a second time.

        A blank is left alone: an optional parameter that was not filled in is
        not a path, and handing it to either aliaser would make one out of
        nothing.

        ``kind="grid"`` is deliberately *not* aliased, and that was checked
        rather than assumed, because a grid descriptor may be a path as easily
        as a preset name. It survives a space as it stands: the operator token
        is one argv element, so ``remapbil,/…/My Grids/target grid.txt`` reaches
        CDO whole. Measured end to end through ``execute_operator`` on 2.6.3 —
        exit 0, 41,112 bytes written — and the WSL path cannot split it either,
        since ``_build_command`` maps each argument individually rather than
        joining them into a shell string. What would break is a *preset* name
        containing a space, which does not exist. So the file parameters listed
        above are aliased, and grids are correctly left alone.
        """
        try:
            from .categories import (file_parameter_indexes,
                                     output_parameter_indexes)
        except ImportError:                                     # pragma: no cover
            return parameters, [], []

        aliased = list(parameters)
        side_outputs: List[str] = []
        relocations: List[Dict[str, str]] = []
        writes = set(output_parameter_indexes(operator))

        for index in file_parameter_indexes(operator):
            if index >= len(aliased) or not aliased[index].strip():
                continue
            if index in writes:
                target, relocation = self._prepare_output_target(
                    aliased[index], variable_output=False
                )
                aliased[index] = target
                side_outputs.append(target)
                if relocation is not None:
                    relocations.append(relocation)
            else:
                aliased[index] = self._create_input_alias(aliased[index])
        return aliased, side_outputs, relocations

    def _run_directory(self, operator: str) -> str:
        """The directory this run's process starts in, or "" for the default.

        "" means the temporary store's base, which is what every run has always
        used and what both execution paths still fall back to. Returned rather
        than resolved here so that resolving a call does not touch ``_tstore``
        for operators that do not need it — that coupling is new, unnecessary
        for 942 of the 943, and it broke a test whose integration is built
        without ``__init__`` precisely to have no temporary store.

        A real directory comes back only for the operators whose *result*
        depends on where the process started, which get a fresh one of their
        own.

        That exception is not tidiness, it is a correctness and a safety fix, and
        it was found by looking at what the default actually is. ``TempFileStore``
        with no explicit directory takes ``tempfile.gettempdir()``, so the shared
        system temp root — ``/var/folders/…/T`` on this machine — is the working
        directory of every run. For every other operator that is harmless: paths
        reach argv absolute and nothing is written relative to it. For ``cmor``
        it is three separate problems at once:

        * an unset ``drs_root`` writes the DRS tree into the shared system temp
          root, where the user will not find it and the operating system may
          remove it;
        * an unset ``info`` looks for ``.cdocmorinfo`` there, so one user's stray
          file would silently configure another run;
        * and the pre/post scan this class does to discover the output would be
          walking a directory every process on the machine writes to — expensive,
          and wrong in the dangerous direction, since a file some other program
          created during the run would be reported as this run's output and
          *deleted* by the clean-up if the run then failed.

        A directory per run rather than one per operator: two ``cmor`` runs in a
        session must not have to reason about each other's trees, and the
        snapshot's whole meaning is "what was here before *this* run".

        The path is handed to the user through the session log; see
        ``session_log.OperatorRequest.cwd``.
        """
        try:
            from .categories import depends_on_working_directory
        except ImportError:                                     # pragma: no cover
            return ""

        if not depends_on_working_directory(operator):
            return ""

        run_dir = (Path(self._tstore.base_dir)
                   / f"ncexplorer_{operator}_{time.time_ns()}")
        try:
            run_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            # Better the shared directory than no run at all — the scan guards
            # itself with ``_MAX_SCANNED_TREE``, and the user still gets the
            # path in the log.
            self.logger.warning("Could not create a run directory for %s (%s); "
                                "falling back to %s", operator, exc,
                                self._tstore.base_dir)
            return ""
        return str(run_dir)

    def _output_scan_root(self, operator: str, parameters: List[str],
                          default_root: str) -> str:
        """Where to look for files this operator writes without naming them.

        Returns "" for every operator that names its outputs, which is all but
        one — so the scan below costs nothing for the rest of the catalog. The
        one is ``cmor``: ``nout == 0``, and it writes a DRS tree of NetCDF files
        under ``drs_root`` whose names CMOR composes from the project's
        template, so nothing on the command line names any of them.

        Inventing an argv outfile and appending it was the other way to give the
        execution layer something to watch, and it is rejected outright:
        ``cdo cmor,<table> infile`` takes exactly one file, and a second one is
        read as a second *input* rather than written as an output. The command
        would change meaning rather than gain a target.

        Which operators these are, and which parameter redirects each, is
        ``categories.CWD_DEPENDENT_OPERATORS`` — the same table ``session_log``
        reads to know a command needs a ``cd`` in front of it to reproduce.

        The parameter's *index* comes from the schema rather than from a
        constant here, on the same invariant every other consumer of a parameter
        list depends on: index *i* of ``parameters`` is ``spec.params[i]``.
        Hard-coding "9" would be a second copy of the declaration order, and it
        would be wrong the first time a parameter is inserted above it.
        """
        try:
            from .categories import CWD_DEPENDENT_OPERATORS, OPERATOR_SCHEMA
        except ImportError:                                     # pragma: no cover
            return ""

        parameter = CWD_DEPENDENT_OPERATORS.get(operator)
        if parameter is None:
            return ""

        spec = OPERATOR_SCHEMA.get(operator)
        if spec is not None:
            for index, param in enumerate(spec.params):
                if param.name != parameter:
                    continue
                if index < len(parameters) and str(parameters[index]).strip():
                    return str(Path(str(parameters[index]).strip()).expanduser())
                break
        # Unset, so CDO's own default applies: the process's working directory.
        return default_root

    #: Ceiling on how many files one pre/post scan will walk. A guard rather
    #: than a policy: ``drs_root`` defaults to the run's working directory,
    #: which this application owns and keeps small, but a user may point it at a
    #: home directory. Walking that twice per run would cost more than the run.
    #:
    #: Exceeding it disables the comparison for that run rather than truncating
    #: it, because a truncated "before" set makes ``_discard_partial_outputs``
    #: think it created files it did not — and the consequence of that mistake
    #: is deleting a user's data. Losing the report is the safe direction to
    #: fail in, and it is logged.
    _MAX_SCANNED_TREE = 20000

    def _snapshot_tree(self, root: str) -> Optional[set]:
        """Every file under ``root``, or None when the tree could not be read.

        None and the empty set are deliberately different answers. Empty means
        "the directory is there and holds nothing", which makes every file found
        afterwards this run's work. None means "no usable answer" — the tree is
        too large to walk, or unreadable — and the caller must then neither claim
        the outputs nor delete anything, because both need a trustworthy
        "before".

        A missing directory is an empty set rather than None: ``drs_root``
        commonly does not exist until CMOR creates it, and that is a perfectly
        good "before" — nothing was there.
        """
        if not root:
            return None
        base = Path(root)
        if not base.is_dir():
            return set()

        found: set = set()
        try:
            for path in base.rglob("*"):
                if path.is_file():
                    found.add(str(path))
                    if len(found) > self._MAX_SCANNED_TREE:
                        self.logger.warning(
                            "Not tracking output under %s: more than %d files. "
                            "Files this run writes there will not be reported "
                            "or cleaned up.", root, self._MAX_SCANNED_TREE)
                        return None
        except OSError as exc:
            self.logger.warning("Could not scan %s for output: %s", root, exc)
            return None
        return found

    def _discovered_tree_outputs(self, root: str,
                                 before: Optional[set]) -> Tuple[str, ...]:
        """Files that appeared under ``root`` while the run was going.

        Sorted, so two runs of the same command report identically and a test
        can assert on the list rather than on a set.
        """
        if before is None:
            return ()
        after = self._snapshot_tree(root)
        if after is None:
            return ()
        return tuple(sorted(after - before))

    def _discovered_prefix_outputs(self, call, before: set) -> Tuple[str, ...]:
        """The family of files a ``variable_output`` run wrote under its prefix.

        The other half of ``discovered_outputs``. ``_discovered_tree_outputs``
        answers it for ``cmor``, which names no output at all; this answers it
        for the operators that name a *base* and let CDO choose the suffixes —
        every ``split*``, ``distgrid``, a ``gen*`` run with ``map3d=true``, and
        the two Ensval operators.

        It matters most for Ensval, because there the fan-out is not a numbered
        series a user can predict from the base they typed. Measured on 2.6.3:

            cdo ensbrs,5 ref e1..e5 obase
                -> obase.brs.nc  obase.brs_reli.nc
                   obase.brs_reso.nc  obase.brs_unct.nc
            cdo enscrps   ref e1..e5 cbase
                -> cbase.crps.nc cbase.crps_pot.nc cbase.crps_reli.nc

        Four files and three files, with names the caller has no way to guess —
        and the module page gets five of those seven suffixes wrong (it says
        ``reli``, ``crpspot``, ``brsreli``, ``brsreso``, ``brsunct``), so
        reading the manual does not rescue it either. Until this existed the
        run reported "completed successfully" and named none of them.

        Globbed against ``call.outputs`` — the paths the *caller* asked for —
        and deliberately not ``aliased_outputs``: this runs after
        ``_materialise_output_aliases`` has moved everything back, so the alias
        directory is empty by now and the user-facing prefix is where the files
        are. ``before`` is the pre-run snapshot taken over the same prefixes,
        so a sibling that merely shares the stem is never reported as output —
        the same rule, and for the same reason, as
        :meth:`_existing_output_paths` states at length.
        """
        if not call.variable_output:
            return ()
        after = self._existing_output_paths(call.outputs, variable_output=True)
        return tuple(sorted(after - before))

    def _discard_tree_outputs(self, root: str, before: Optional[set]) -> None:
        """Remove what a failed or cancelled run left under ``root``.

        The same rule as :meth:`_discard_partial_outputs`, applied to a tree
        instead of a path: only files absent from the pre-run snapshot are
        removed, so anything that was already there survives however well it
        matches. Without this, a ``cmor`` run that failed half way through left
        a partial DRS tree on disk while the application reported it had cleaned
        up — the failure mode ``_discard_failed_outputs`` was written for, in the
        one place that function could not see.

        Directories are deliberately left behind, empty ones included. Removing
        them means deciding whether a directory was created by this run, which
        the file snapshot does not say, and an empty directory is untidy where a
        wrongly removed one is destructive.
        """
        for path in self._discovered_tree_outputs(root, before):
            try:
                Path(path).unlink()
                self.logger.debug("Removed partial output %s", path)
            except OSError as exc:
                self.logger.warning("Could not remove partial output %s: %s",
                                    path, exc)

    def _prepare_output_target(self, original: str, *, variable_output: bool) -> Tuple[str, Optional[Dict[str, str]]]:
        """Create a temporary alias for an output target if it contains spaces."""
        if " " not in original:
            return original, None

        alias_dir = Path(self._tstore.base_dir) / "cdo_path_aliases"
        alias_dir.mkdir(parents=True, exist_ok=True)
        original_path = Path(original).expanduser()
        alias_name = f"out_{time.time_ns()}_{self._safe_path_token(original_path.name)}"
        alias_path = alias_dir / alias_name

        return str(alias_path), {
            "kind": "prefix" if variable_output else "file",
            "alias": str(alias_path),
            "original": str(original_path),
        }

    def _materialise_output_aliases(self, relocations: List[Dict[str, str]]) -> None:
        """Move generated output aliases back to the user-requested paths."""
        for relocation in relocations:
            alias_path = Path(relocation["alias"])
            original_path = Path(relocation["original"])
            original_path.parent.mkdir(parents=True, exist_ok=True)

            if relocation["kind"] == "file":
                if alias_path.exists():
                    shutil.move(str(alias_path), str(original_path))
                continue

            prefix = alias_path.name
            for candidate in alias_path.parent.glob(f"{prefix}*"):
                suffix = candidate.name[len(prefix):]
                target = original_path.parent / f"{original_path.name}{suffix}"
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(candidate), str(target))

    def get_operator_catalog(self) -> Dict[str, Dict[str, Union[str, Tuple[int, int]]]]:
        """Return installed operator metadata keyed by operator name."""
        return {
            name: {
                "signature": self.operator_signatures[name],
                "description": self.operator_descriptions.get(name, ""),
            }
            for name in self.operator_signatures
        }

    def get_operator_signatures(self) -> Dict[str, Tuple[int, int]]:
        """Return a copy of the installed operator signature table."""
        return dict(self.operator_signatures)

    @staticmethod
    def _require_parameters(operator: str,
                            extra_parameters: Optional[List[str]]) -> List[str]:
        """The parameter list to build the operator token from, or refuse.

        Two things happen to a caller's list here, and the order matters.

        Trailing blanks are dropped, because a form that renders one field per
        declared parameter hands back one value per field whether or not the
        optional ones were filled in. That is the legitimate case and it stays.

        The trim is a *positional* rule and stays safe now that keyword and flag
        parameters exist, because it only ever pops from the tail: index *i* of
        what comes back is still ``spec.params[i]``, which is the invariant
        ``parameter_tokens`` and the three checkers all rely on. It is also a
        no-op for a keyword list in substance — an unset keyword renders to
        nothing wherever it sits, not only at the end — so the trim neither
        helps nor harms there, and removing it would change the positional case
        it was written for.

        A value that cannot be what the schema declares it to be is refused
        here too, and here is the *only* place it is refused. ``kind`` was a
        widget hint that nothing parsed, so ``gtc,abc`` reached argv and failed
        inside CDO in CDO's words. The check belongs at this choke point rather
        than in the widget for the same reason the blank check does: the batch
        runner and the model runner never go near a widget, and a rule enforced
        only where a form can enforce it is not enforced for two of the three
        surfaces that reach argv.

        What does *not* stay is trimming a blank the operator actually needs.
        This method is the last thing between a parameter list and argv, and
        both the batch runner and the model runner reach it, so a silent trim
        here turned "the user left a field empty" into ``cdo pow ifile ofile``
        — which CDO answers with an unbounded prompt on stdin rather than an
        abort, and which the surfaces above therefore cannot report as a failed
        run. Refusing costs a caller nothing it was not already handling: every
        other rejection below is a ``ValueError`` too.
        """
        raw = [str(value) for value in (extra_parameters or [])]

        # Checked before trimming: a blank in a required slot must be seen, and
        # the trim would otherwise eat it whenever it is also the last one.
        try:
            from .categories import (invalid_parameter_values,
                                     missing_parameter_files,
                                     missing_required_parameters)
        except ImportError:                                     # pragma: no cover
            invalid_parameter_values = None                      # type: ignore
            missing_parameter_files = None                       # type: ignore
            missing_required_parameters = None                   # type: ignore
        if missing_required_parameters is not None:
            missing = missing_required_parameters(operator, raw)
            if missing:
                fields = ", ".join(missing)
                raise ValueError(
                    f"{operator}: needs {'a value' if len(missing) == 1 else 'values'} "
                    f"for {fields}")

        # After the blank check, so a missing value is reported as missing
        # rather than as an unparseable one.
        if invalid_parameter_values is not None:
            invalid = invalid_parameter_values(operator, raw)
            if invalid:
                raise ValueError(f"{operator}: " + "; ".join(invalid))

        # Last of the three, because "this is not a file" is only worth saying
        # about a value that is present and otherwise well-formed. A file-valued
        # parameter is the one kind of parameter that names something outside
        # the command, and CDO's own complaint about it arrives after a
        # subprocess and talks about the path rather than the field.
        if missing_parameter_files is not None:
            absent = missing_parameter_files(operator, raw)
            if absent:
                raise ValueError(f"{operator}: " + "; ".join(absent))

        while raw and raw[-1] == "":
            raw.pop()
        return raw

    def _resolve_operator_call(
        self,
        operator: str,
        input_files: Optional[Union[str, os.PathLike, List[str], Tuple[str, ...]]],
        output_files: Optional[Union[str, os.PathLike, List[str], Tuple[str, ...]]],
        extra_parameters: Optional[List[str]],
        options: Optional[Union[str, List[str], Tuple[str, ...]]] = None,
    ) -> _ResolvedCall:
        """Validate one operator call and build its command line.

        Raises ``ValueError`` for an unknown operator, a file count the
        operator's signature does not permit, or a required parameter left
        blank. Nothing is executed here.

        ``options`` are CDO's *global* options — ``-f``, ``-b``, ``-z``, ``-r``,
        ``-O``, ``--chunkspec`` — passed as separate tokens, e.g.
        ``["-f", "nc4", "-z", "zip"]``. They are not operator parameters and do
        not go in the operator token; CDO takes them before the operator name
        and nowhere else.

        They exist because a whole section of the manual is written around them
        and there was previously no slot for one at all: ``cdo -f nc copy`` is
        what makes ``copy`` a format converter, ``pack`` without ``-b`` is
        locked to 16-bit integers, and ``bitrounding`` without ``-z`` produces a
        file exactly as large as the one it started from, which makes it
        pointless. They are deliberately not validated here — CDO's option set
        is large, versioned, and its own business, and this layer refusing an
        option the installed binary accepts would be the worse failure.
        """
        if operator not in self.operator_signatures:
            raise ValueError(f"Unknown or unavailable operator: {operator}")

        # Before any work on the arguments, because no argument can fix it. One
        # operator reaches this — ``cmor`` on a CDO built without CMOR — and
        # without it the only diagnosis a user ever got was CDO's "CMOR support
        # not compiled in!", after a subprocess, having filled in the form.
        #
        # A refusal rather than a warning, because ``cdo --config has-cmor``
        # answering ``no`` is the binary itself saying the run cannot succeed;
        # this is the same class of "not ready to run" as a missing required
        # parameter and is raised the same way. It is silent whenever the probe
        # cannot answer — see :meth:`build_capability` — so a CDO too old for
        # ``--config`` is still run and its abort translated afterwards by
        # :meth:`explain_failure`.
        #
        # This is the one thing in this method that touches the binary, which
        # the paragraph above about executing nothing is otherwise still true
        # of: the probe is cached per instance and per feature, so it costs one
        # subprocess for the life of the application rather than one per run.
        unsupported = self.missing_build_feature(operator)
        if unsupported:
            raise ValueError(f"{operator}: {unsupported}")

        inputs = self._coerce_string_list(input_files)
        outputs = self._coerce_string_list(output_files)
        parameters = self._require_parameters(operator, extra_parameters)
        nin, nout = self.operator_signatures[operator]

        if nin == -1:
            if len(inputs) < 1:
                raise ValueError(f"{operator}: expected at least 1 input file")
        elif len(inputs) != nin:
            raise ValueError(f"{operator}: expected {nin} input file(s), got {len(inputs)}")

        if nout == -1:
            if len(outputs) != 1:
                raise ValueError(f"{operator}: expected exactly 1 output prefix/base path")
        elif len(outputs) != nout:
            raise ValueError(f"{operator}: expected {nout} output target(s), got {len(outputs)}")

        # Whether this run's output path is a file or a base CDO appends to is
        # a fact about the *call*, not only about the operator: ``nout == -1``
        # is the static half, ``map3d=true`` on a gen* operator is the half only
        # the parameter values can answer, and the Magics six are a third —
        # ``nout == 1`` with an obase, decidable from neither. Resolved once,
        # here, so all four users of it below see the same answer.
        try:
            from .categories import writes_output_prefix
        except ImportError:                                     # pragma: no cover
            variable_output = (nout == -1)
        else:
            variable_output = writes_output_prefix(operator, parameters)

        # ``gradsdes`` writes its descriptor into the directory of its input, so
        # its input is aliased by directory rather than by file — otherwise the
        # only thing the run produces lands in a temp directory. See
        # ``_OPERATORS_WRITING_BESIDE_INPUT``.
        alias_by_directory = operator in self._OPERATORS_WRITING_BESIDE_INPUT
        aliased_inputs = [
            self._create_input_alias(path, alias_whole_directory=alias_by_directory)
            for path in inputs
        ]
        aliased_outputs: List[str] = []
        output_relocations: List[Dict[str, str]] = []
        for output in outputs:
            aliased_output, relocation = self._prepare_output_target(
                output, variable_output=variable_output)
            aliased_outputs.append(aliased_output)
            if relocation is not None:
                output_relocations.append(relocation)

        # A file-valued parameter is a path like any other, and until now it was
        # the one path in the command that never got aliased: ``reducegrid``'s
        # mask went into the operator token verbatim, so a mask living under
        # "/Users/me/My Data/mask.nc" reached CDO as two arguments and failed on
        # a filename nobody typed. Driven off the schema's ``kind`` rather than
        # off a list of operator names, so an operator gains the fix by being
        # declared rather than by being remembered.
        parameters, side_outputs, side_relocations = self._alias_file_parameters(
            operator, parameters
        )
        output_relocations.extend(side_relocations)

        # How each parameter is spelled is the schema's business, not this
        # method's. What stood here was ``','.join(parameters)`` — purely
        # positional, which is right for ``gtc,273.15`` and ``distgrid,2,3`` and
        # wrong for most of the File operation section, which CDO documents in
        # keyword form (``bitrounding,inflevel=0.999``) and which rejects the
        # positional spelling outright ("missing '=' in key/value string").
        #
        # ``parameter_tokens`` returns the values unchanged for every operator
        # whose parameters are all positional, which is every operator outside
        # that section — so the token this builds for the comparison,
        # arithmetic, selection and ECA families is byte-identical to the one
        # the join produced. ``tests/test_file_operations_category.py`` asserts
        # exactly that against a fixed list of calls.
        try:
            from .categories import parameter_tokens
        except ImportError:                                     # pragma: no cover
            tokens = parameters
        else:
            tokens = parameter_tokens(operator, parameters)

        op_token = operator if not tokens else f"{operator},{','.join(tokens)}"
        if not tokens and operator in _OPTION_SHADOWED_OPERATORS:
            # A bare trailing comma, and it is load-bearing. See
            # ``_OPTION_SHADOWED_OPERATORS``.
            op_token = f"{operator},"
        # Two operators are run as the inner step of a chain rather than on
        # their own, because on their own they abort the process. See
        # ``_NEEDS_REFERENCE_TIME``.
        op_tokens = ([_ASSERTION_SAFE_REFERENCE_TIME, f"-{op_token}"]
                     if operator in _NEEDS_REFERENCE_TIME else [op_token])

        # Global options go between the binary and the operator, which is the
        # only place CDO accepts them: ``cdo -f nc4 -z zip bitrounding,… in out``.
        # Empty for every caller that does not ask, so the command is unchanged
        # for everything that was working before.
        cmd = [self.NCExplorer_binary, *self._coerce_string_list(options),
               *op_tokens, *aliased_inputs, *aliased_outputs]

        # Recorded before the run and only for the operators that append, so a
        # failure can put the file back the size it was. See
        # ``categories.APPENDING_OPERATORS`` for the measurement behind this.
        append_sizes: Dict[str, int] = {}
        try:
            from .categories import APPENDING_OPERATORS
        except ImportError:                                     # pragma: no cover
            APPENDING_OPERATORS = frozenset()                    # type: ignore
        if operator in APPENDING_OPERATORS:
            for target in aliased_outputs:
                path = Path(target)
                if path.is_file():
                    append_sizes[str(path)] = path.stat().st_size

        # Pinned per call rather than read off ``self`` at run time, so the
        # directory the scan below compares against is provably the one the
        # process will start in.
        run_cwd = self._run_directory(operator)

        # Resolved from the *aliased* parameters, which is the list CDO will be
        # given, so a drs_root the user spelled with a space is scanned where
        # the run will actually write. ``drs_root`` is a directory and therefore
        # not a ``file`` parameter, so it is never aliased and the two lists
        # agree here — asserted rather than assumed by resolving from the same
        # list the token is built from.
        scan_root = self._output_scan_root(operator, parameters, run_cwd)

        return _ResolvedCall(
            cmd=cmd,
            outputs=outputs,
            aliased_outputs=aliased_outputs,
            relocations=output_relocations,
            nout=nout,
            side_outputs=side_outputs,
            append_sizes=append_sizes,
            variable_output=variable_output,
            scan_root=scan_root,
            cwd=run_cwd,
        )

    def execute_operator(
        self,
        operator: str,
        *,
        input_files: Optional[Union[str, os.PathLike, List[str], Tuple[str, ...]]] = None,
        output_files: Optional[Union[str, os.PathLike, List[str], Tuple[str, ...]]] = None,
        extra_parameters: Optional[List[str]] = None,
        options: Optional[Union[str, List[str], Tuple[str, ...]]] = None,
        env: Optional[Mapping[str, str]] = None,
        stdin_path: str = "",
    ) -> NCExplorerResult:
        """Execute one operator using explicit input/output/parameter groups.

        Blocks until CDO exits. :meth:`execute_operator_async` is the
        non-blocking counterpart for callers that own an event loop.

        ``options`` are CDO's global options, as separate tokens
        (``["-f", "nc4", "-z", "zip"]``); see :meth:`_resolve_operator_call`.

        ``env`` are environment variables CDO reads that change what an operator
        *computes* rather than how it is spelled — ``CDO_WEIGHT_MODE`` and the
        three jacobi settings the EOFs section is governed by. They are layered
        over the inherited environment; see :func:`run_environment`. Which ones
        an operator honours is declared in the schema
        (``categories.operator_env``) and deliberately not validated here, for
        the same reason ``options`` is not: the set is CDO's business and
        versioned, and refusing one the installed binary accepts would be the
        worse failure.

        ``stdin_path`` names a file to feed the operator on standard input, for
        the three ``Formatted input`` operators whose data arrives that way.
        Empty for everything else, which then gets an immediate EOF rather than
        an inherited terminal; see :meth:`_execute_command`.
        """
        call = self._resolve_operator_call(
            operator, input_files, output_files, extra_parameters, options
        )

        # Snapshotted before CDO can write anything, exactly as the async path
        # does it, so a failed run deletes only what it created itself and never
        # a file that was already there.
        pre_existing = self._existing_output_paths(
            call.aliased_outputs, variable_output=call.variable_output
        ) | self._existing_output_paths(call.side_outputs, variable_output=False)
        # And the tree an operator writes without naming it. None for every
        # operator but ``cmor``; see ``_ResolvedCall.scan_root``.
        pre_existing_tree = self._snapshot_tree(call.scan_root)
        # And the family a ``variable_output`` run writes under its base path.
        # Taken over the caller's own prefixes rather than the aliased ones,
        # because the after-snapshot is taken once the aliases have been moved
        # back; see ``_discovered_prefix_outputs``.
        pre_existing_prefixes = self._existing_output_paths(
            call.outputs, variable_output=True) if call.variable_output else set()

        result = self._execute_command(call.cmd, env=env, stdin_path=stdin_path,
                                       cwd=call.cwd)
        if result.success:
            if call.relocations:
                self._materialise_output_aliases(call.relocations)
            result.discovered_outputs = (
                self._discovered_tree_outputs(call.scan_root, pre_existing_tree)
                or self._discovered_prefix_outputs(call, pre_existing_prefixes))
        else:
            # An appending operator that failed has grown its output without
            # committing a header, so the file reads as untouched and is not.
            # Nothing else here deletes a pre-existing output, and nothing
            # should — this only puts one back the length it was.
            self._truncate_appended_outputs(call.append_sizes)
            self._discard_failed_outputs(call, pre_existing)
            self._discard_tree_outputs(call.scan_root, pre_existing_tree)
            # ``getattr`` rather than an attribute access: ``_execute_command``
            # is private, and the tests that stand in for it construct a
            # minimal result deliberately — "what it is asserting is the
            # *command* that reaches it, not the keywords the caller passes".
            # Requiring every such double to grow a field each time the real
            # result does is how a stub becomes a second copy of the type.
            result.stderr = self._annotate_failure(
                result.stdout, result.stderr,
                getattr(result, "returncode", None))
        # ``variable_output`` excluded deliberately: with ``map3d=true`` a gen*
        # operator still has ``nout == 1``, but the path it was given is a
        # prefix and no file exists at it. Reporting it as *the* output file
        # sends every downstream caller — the plot dock, the model builder's
        # next node — to a path that is not there. There is no single file to
        # name in that case, so none is named.
        if (result.success and call.nout == 1 and len(call.outputs) == 1
                and not call.variable_output):
            result.output_file = call.outputs[0]
        return result

    def prepare_operator_run(
        self,
        operator: str,
        *,
        input_files: Optional[Union[str, os.PathLike, List[str], Tuple[str, ...]]] = None,
        output_files: Optional[Union[str, os.PathLike, List[str], Tuple[str, ...]]] = None,
        extra_parameters: Optional[List[str]] = None,
        timeout: int = DEFAULT_COMMAND_TIMEOUT,
        options: Optional[Union[str, List[str], Tuple[str, ...]]] = None,
        env: Optional[Mapping[str, str]] = None,
        stdin_path: str = "",
    ) -> PreparedRun:
        """Resolve one operator invocation without running it.

        Same validation, aliasing and platform rewriting as
        :meth:`execute_operator`; see :class:`PreparedRun` for why the two
        halves are separable, and ``core/async_executor.py`` for the driver
        that puts them back together around a ``QProcess``.

        ``env`` is carried on the :class:`PreparedRun` rather than applied here,
        since nothing is started at this point. The asynchronous driver applies
        it to its ``QProcess``; the rule for what it means is the same one
        :func:`run_environment` states, and both paths go through that function
        so they cannot drift.
        """
        call = self._resolve_operator_call(
            operator, input_files, output_files, extra_parameters, options
        )

        if len(call.cmd) > 1:
            self._validate_NCExplorer_arguments(*call.cmd[1:])

        argv = self._build_command(call.cmd)
        # The call's own, so the asynchronous path starts the process where the
        # synchronous one would and the recorded ``cwd`` is the one that ran.
        cwd = call.cwd or str(self._tstore.base_dir)
        self.last_command = " ".join(argv)
        # Logged before the run, exactly as in the synchronous path, so a
        # command that hangs or is killed still leaves a record of the attempt.
        self.log_last_cdo_command()

        # Snapshotted before CDO can write anything: a cancelled run must delete
        # only what it created itself, never a file that was already there.
        #
        # Taken in two passes because the two kinds of target are shaped
        # differently. ``aliased_outputs`` may be prefixes — when ``nout == -1``
        # or when a gen* operator was given ``map3d=true``, which is why the
        # call carries the answer rather than this recomputing it;
        # ``side_outputs`` — ``tee``'s second file — is always a plain path, and
        # globbing it as a prefix would sweep in unrelated siblings.
        pre_existing = self._existing_output_paths(
            call.aliased_outputs, variable_output=call.variable_output
        ) | self._existing_output_paths(call.side_outputs, variable_output=False)
        # Taken here rather than inside ``finalise`` for the reason the two
        # passes above are: "before" has to mean before the process started, and
        # ``finalise`` runs after it has finished.
        pre_existing_tree = self._snapshot_tree(call.scan_root)
        # Same reasoning again, for the ``variable_output`` fan-out. Over the
        # caller's prefixes, not the aliased ones — ``finalise`` reads it after
        # the aliases have been moved back.
        pre_existing_prefixes = self._existing_output_paths(
            call.outputs, variable_output=True) if call.variable_output else set()

        def finalise(outcome) -> NCExplorerResult:
            """Turn one finished/cancelled/failed process into a result."""
            # ``env`` is closed over rather than read off the outcome, because
            # the worker applies it to its QProcess and never hands it back. It
            # is the same mapping the synchronous path records, so the two
            # histories cannot disagree about what a run was given.
            self._record_command(argv, cwd, outcome.returncode, outcome.duration,
                                 env)

            if not outcome.completed:
                # Cancelled, timed out or never started. Nothing this run wrote
                # is trustworthy, and half a NetCDF file is worse than none.
                self._discard_partial_outputs(
                    call.aliased_outputs, pre_existing, variable_output=call.variable_output
                )
                self._discard_partial_outputs(
                    call.side_outputs, pre_existing, variable_output=False
                )
                self._discard_tree_outputs(call.scan_root, pre_existing_tree)
                # And for an appending operator, the output that was already
                # there has been grown rather than created, so deleting is
                # exactly what must not happen — see _truncate_appended_outputs.
                self._truncate_appended_outputs(call.append_sizes)
                self.logger.info("Processing engine run did not complete (%s)", outcome.state)
                # ``detail`` alone used to be the whole of the reported stderr,
                # which threw away CDO's own message on exactly the runs that
                # need it most. A crash is the case that proves it: CDO 2.6.3
                # dies by SIGSEGV on roughly one run in six when a Magics
                # operator aborts inside a pipe, and the line that says *why* —
                # "MAGICS support not compiled in!" — is written to stderr
                # before the crash and survives it intact. Reporting only "the
                # processing engine terminated abnormally" turned a diagnosable
                # failure into a mystery, and it did so more often the more
                # useful the diagnosis was.
                #
                # Both are kept, ``detail`` first because it says what happened
                # to the process and CDO's stderr says what it was complaining
                # about. ``_annotate_failure`` then runs over the real stderr,
                # so the capability explanation is found on this path exactly as
                # it is on the synchronous one.
                stderr = "\n\n".join(
                    part for part in (outcome.detail, outcome.stderr) if part)
                return NCExplorerResult(
                    False, outcome.stdout,
                    self._annotate_failure(outcome.stdout, stderr,
                                           outcome.returncode),
                    execution_time=outcome.duration,
                    returncode=outcome.returncode,
                )

            success = self._determine_command_success(
                call.cmd, outcome.returncode, outcome.stdout, outcome.stderr
            )
            result = NCExplorerResult(
                success, outcome.stdout, outcome.stderr,
                execution_time=outcome.duration, returncode=outcome.returncode,
            )
            if success and call.relocations:
                self._materialise_output_aliases(call.relocations)
            if success:
                result.discovered_outputs = (
                    self._discovered_tree_outputs(call.scan_root,
                                                  pre_existing_tree)
                    or self._discovered_prefix_outputs(call,
                                                       pre_existing_prefixes))
            if not success:
                self._truncate_appended_outputs(call.append_sizes)
                self._discard_failed_outputs(call, pre_existing)
                self._discard_tree_outputs(call.scan_root, pre_existing_tree)
                result.stderr = self._annotate_failure(
                    result.stdout, result.stderr, outcome.returncode)
            if success and call.nout == 1 and len(call.outputs) == 1:
                result.output_file = call.outputs[0]
            if outcome.stderr and not success:
                self.logger.warning("STDERR: %s", outcome.stderr)
            return result

        return PreparedRun(argv=tuple(argv), cwd=cwd, timeout=timeout,
                           # Through ``_run_env`` for the same reason the
                           # synchronous path is: a bundled MAGICS build needs
                           # MAGPLUS_HOME whether it was launched blocking or
                           # not, and the two paths silently disagreeing about
                           # the environment is exactly the drift that helper
                           # exists to stop.
                           finalise=finalise, env=self._run_env(env),
                           stdin_path=stdin_path)

    def execute_operator_async(
        self,
        operator: str,
        *,
        input_files: Optional[Union[str, os.PathLike, List[str], Tuple[str, ...]]] = None,
        output_files: Optional[Union[str, os.PathLike, List[str], Tuple[str, ...]]] = None,
        extra_parameters: Optional[List[str]] = None,
        timeout: int = DEFAULT_COMMAND_TIMEOUT,
        parent=None,
        options: Optional[Union[str, List[str], Tuple[str, ...]]] = None,
        env: Optional[Mapping[str, str]] = None,
        stdin_path: str = "",
    ):
        """Start one operator off the calling thread and return its worker.

        The returned :class:`~.async_executor.CdoProcessWorker` carries
        ``progress`` / ``output_line`` / ``finished`` / ``failed`` /
        ``cancelled`` signals and a ``cancel()`` method. It is already started,
        but the process itself is launched from the event loop rather than from
        this call, so a caller still connects its signals afterwards without a
        race. Requires a running Qt event loop; :meth:`execute_operator` remains
        the right call for scripts.

        The import is deliberately local: the worker is a ``QObject``, and
        keeping the dependency inside this method is what lets the rest of the
        module stay usable without Qt installed.
        """
        from .async_executor import CdoProcessWorker

        run = self.prepare_operator_run(
            operator,
            input_files=input_files,
            output_files=output_files,
            extra_parameters=extra_parameters,
            timeout=timeout,
            options=options,
            env=env,
            stdin_path=stdin_path,
        )
        worker = CdoProcessWorker(run, parent=parent)
        worker.start()
        return worker

    def _truncate_appended_outputs(self, append_sizes: Dict[str, int]) -> None:
        """Put an appending operator's output back the length it was.

        Only ``cat`` reaches this, and only when its run did not succeed. Every
        other operator creates its output, so the rule elsewhere — delete what
        this run made, leave what was already there — is right, and it is
        precisely wrong here: ``cat``'s output *is* a file that was already
        there, and what the failed run did to it was make it longer.

        Truncation rather than deletion because the bytes up to the recorded
        size are the caller's own data, which this run did not write and has no
        business removing. A file that grew is truncated; one that did not is
        left alone, which covers the common clean failures — a bad open or a
        grid mismatch aborts before a single record is appended and leaves the
        file byte-identical.

        Failing to truncate is logged, not raised: the run has already failed
        and the caller is owed that failure, not a second one about tidying up.
        """
        for target, original_size in append_sizes.items():
            path = Path(target)
            try:
                if not path.is_file() or path.stat().st_size <= original_size:
                    continue
                with open(path, "r+b") as handle:
                    handle.truncate(original_size)
            except OSError as exc:
                self.logger.warning(
                    "Could not restore %s to its pre-run size of %d bytes: %s",
                    path, original_size, exc)
            else:
                self.logger.info(
                    "Restored %s to its pre-run size of %d bytes after a failed "
                    "append", path, original_size)

    def _existing_output_paths(self, targets: List[str], *, variable_output: bool) -> set:
        """Output paths that exist before a run starts.

        Split operators name a *prefix* rather than a file, so everything
        already matching that prefix is collected instead.

        The prefix glob is ``<name>*`` in the target's own directory, which does
        reach a sibling that merely shares the stem — ``obase=mon`` matches an
        unrelated ``monthly_totals.nc``. That is safe rather than lucky, and it
        is worth writing down why: the glob is run twice, once here before the
        run and once in ``_discard_partial_outputs`` after it, and only paths
        absent from this snapshot are ever removed. A sibling that existed
        before the run is therefore never touched no matter how well it matches.
        The residue is a file created *during* the run by something outside this
        application that also happens to share the stem, which is not a case the
        app can distinguish from its own output and not one worth narrowing the
        glob for — narrowing it would strand the real outputs, whose suffixes
        CDO chooses.
        """
        existing = set()
        for target in targets:
            path = Path(target)
            if variable_output:
                existing.update(str(match) for match in path.parent.glob(f"{path.name}*"))
            elif path.exists():
                existing.add(str(path))
        return existing

    def _discard_failed_outputs(self, call, pre_existing: set) -> None:
        """Remove what a *completed but failed* run wrote.

        Until this existed, only a run that never completed — cancelled, timed
        out, never started — had its half-written output cleaned up, so a
        cancelled run left the working directory tidier than a failed one.

        Measured on CDO 2.6.3, and the reason this was worth finding: ``cdo
        timcor a.nc b3.nc out.nc`` with mismatched timestep counts exits 1 after
        a cdi error and leaves a 1360-byte ``out.nc`` behind that CDO itself
        then refuses to reopen — "cdf_check_variables: Number of time steps
        undefined, skipped variable tas", "No data arrays found!", "Unsupported
        file structure". The file exists and has a plausible size. On disk it is
        indistinguishable from a real result, and a later step that reads it
        fails somewhere else entirely, about something else.

        Called from both the synchronous and the asynchronous path, which is
        the point of it being a method: the two had already drifted once, and a
        cleanup rule enforced on one of the two ways this application runs CDO
        is not enforced.

        ``pre_existing`` is the snapshot taken before the run. Only what this
        run created is removed; a pre-existing output is left exactly as it was,
        which is also what keeps this from undoing
        :meth:`_truncate_appended_outputs`.
        """
        self._discard_partial_outputs(
            call.aliased_outputs, pre_existing,
            variable_output=call.variable_output,
        )
        self._discard_partial_outputs(
            call.side_outputs, pre_existing, variable_output=False,
        )

    def _discard_partial_outputs(self, targets: List[str], pre_existing: set,
                                 *, variable_output: bool) -> None:
        """Delete output this run created, leaving anything older untouched."""
        for target in targets:
            path = Path(target)
            candidates = (
                list(path.parent.glob(f"{path.name}*")) if variable_output else [path]
            )
            for candidate in candidates:
                if str(candidate) in pre_existing or not candidate.exists():
                    continue
                try:
                    candidate.unlink()
                    self.logger.debug("Removed partial output %s", candidate)
                except OSError as exc:
                    self.logger.warning("Could not remove partial output %s: %s", candidate, exc)

    def _invoke_legacy_operator(self, operator: str, *args: str) -> NCExplorerResult:
        """
        Compatibility adapter for callers that still pass a flat positional argument list.
        Extra parameters are expected first, followed by input files and output files.
        """
        nin, nout = self.operator_signatures.get(operator, (1, 1))
        from .categories import operator_total_param_count

        n_extra = operator_total_param_count(operator)
        # Respect legacy callers that pass fewer extras (e.g. one combined
        # "val1,val2" string) than the schema technically expects. Cap n_extra
        # so we always leave room for the required file args.
        if nin == -1:
            min_files = 1 if nout == -1 else max(1, nout)
        else:
            min_files = nin + (1 if nout == -1 else max(0, nout))
        if n_extra > 0 and len(args) - n_extra < min_files:
            n_extra = max(0, len(args) - min_files)
        extra_parameters = [str(value) for value in args[:n_extra]]
        file_args = [str(value) for value in args[n_extra:]]

        if nin == -1:
            if nout == 0:
                inputs = file_args
                outputs: List[str] = []
            elif nout == -1:
                inputs = file_args[:-1]
                outputs = file_args[-1:]
            else:
                inputs = file_args[:-nout]
                outputs = file_args[-nout:]
        else:
            inputs = file_args[:nin]
            if nout == 0:
                outputs = []
            elif nout == -1:
                outputs = file_args[nin:]
            else:
                outputs = file_args[nin:nin + nout]

        return self.execute_operator(
            operator,
            input_files=inputs,
            output_files=outputs,
            extra_parameters=extra_parameters,
        )

    def _build_signature_aware_method(self, operator: str) -> Callable[..., NCExplorerResult]:
        """Create a bound operator method that delegates to the explicit executor."""
        def _method(*args: str) -> NCExplorerResult:
            return self._invoke_legacy_operator(operator, *args)

        return _method

    def _find_NCExplorer_binary(self) -> Optional[str]:
        """Try to find cdo binary in common locations."""
        # If running from a frozen build, prefer the bundled binary.
        bundled = _bundled_cdo_path()
        if bundled:
            return bundled

        # Common paths where cdo might be installed
        common_paths = [
            '/usr/bin/cdo',
            '/usr/local/bin/cdo',
            '/opt/local/bin/cdo',  # MacPorts
            '/sw/bin/cdo',  # Fink
            '/opt/homebrew/bin/cdo',  # Homebrew on Apple Silicon
            '/usr/local/Cellar/cdo/*/bin/cdo',  # Homebrew pattern
        ]

        # Try using the 'which' command
        try:
            result = subprocess.run(['which', 'cdo'], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                return result.stdout.strip()
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            self.logger.debug("Binary discovery through 'which' failed: %s", e)

        # Try using the 'whereis' command on Linux
        if self.platform == "linux":
            try:
                result = subprocess.run(['whereis', 'cdo'], capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    parts = result.stdout.strip().split()
                    if len(parts) > 1:
                        return parts[1]  # First path after "cdo"
            except (subprocess.SubprocessError, FileNotFoundError) as e:
                self.logger.debug("Binary discovery through 'whereis' failed: %s", e)

        # Check common installation paths
        for path in common_paths:
            if '*' in path:
                # Handle glob patterns
                from glob import glob
                matches = glob(path)
                if matches:
                    return matches[0]
            else:
                if os.path.isfile(path) and os.access(path, os.X_OK):
                    return path

        return None

    def _get_installation_help(self) -> str:
        """Generate a helpful error message with installation instructions."""
        base_msg = (f"Processing engine binary not found on {self.platform}: "
                    f"{self.NCExplorer_binary}")

        if self.platform == "darwin":  # macOS
            help_msg = """
    cdo Installation Instructions for macOS:

    1. Using Homebrew (recommended):
       brew install cdo

    2. Using MacPorts:
       sudo port install cdo

    3. Manual installation:
       Download from https://code.mpimet.mpg.de/projects/cdo

    After installation, cdo is typically available at:
    - /usr/local/bin/cdo (Homebrew)
    - /opt/local/bin/cdo (MacPorts)
    """
        elif self.platform == "linux":
            help_msg = """
    cdo Installation Instructions for Linux:

    1. Ubuntu/Debian:
       sudo apt-get update
       sudo apt-get install cdo

    2. CentOS/RHEL/Fedora:
       sudo yum install cdo
       # or
       sudo dnf install cdo

    3. From source:
       Download from https://code.mpimet.mpg.de/projects/cdo

    After installation, cdo is typically available at:
    - /usr/bin/cdo
    - /usr/local/bin/cdo
    """
        else:
            help_msg = f"""
    Please install cdo for your platform ({self.platform}).
    Visit: https://code.mpimet.mpg.de/projects/cdo
    """

        return base_msg + help_msg

    def __getattr__(self, name: str) -> Callable[..., NCExplorerResult]:
        """Create operator methods lazily when they are requested."""
        if name in self.operator_signatures:
            method = self._build_signature_aware_method(name)
            setattr(self, name, method)
            return method
        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")

    # -------------------------------------------------------------------------
    # Private helpers for platform detection and command execution
    # -------------------------------------------------------------------------

    def _init_windows_NCExplorer(self, use_wsl: Optional[bool]) -> bool:
        if use_wsl is None:
            return self._auto_detect_windows_NCExplorer()
        if not self._test_NCExplorer_availability(use_wsl):
            method = "WSL" if use_wsl else "native Windows"
            raise NCExplorerError(
                f"Processing engine binary not found using {method} method: "
                f"{self.NCExplorer_binary}"
            )
        return use_wsl

    def _auto_detect_windows_NCExplorer(self) -> bool:
        if self._test_NCExplorer_availability(use_wsl=False):
            self.logger.info("Using the native Windows binary")
            return False
        if self._test_NCExplorer_availability(use_wsl=True):
            self.logger.info("Using the WSL binary")
            return True
        raise NCExplorerError(
            "Processing engine binary not found in native Windows or WSL environment"
        )

    # Verification helpers
    def _verify_unix_NCExplorer(self) -> bool:
        return self._test_NCExplorer_availability(use_wsl=False)

    def _test_NCExplorer_availability(self, use_wsl: bool) -> bool:
        try:
            cmd = ["wsl", self.NCExplorer_binary, "--version"] if use_wsl else [self.NCExplorer_binary, "--version"]
            return subprocess.run(cmd, capture_output=True, text=True, timeout=10).returncode == 0
        except (FileNotFoundError, subprocess.SubprocessError):
            return False

    # Command building
    def _build_command(self, NCExplorer_cmd: List[str]) -> List[str]:
        if self.platform == "windows" and self.use_wsl:
            converted = [self._to_wsl_argument(arg) for arg in NCExplorer_cmd]
            return ["wsl"] + converted
        return NCExplorer_cmd

    @classmethod
    def _to_wsl_argument(cls, arg: str) -> str:
        """One argv entry with every Windows path inside it made a WSL path.

        Not simply ``_win_to_wsl(arg)``, because an operator token can *contain*
        a path rather than be one. ``reducegrid,C:\\Users\\me\\mask.nc`` matched
        ``_is_file_path`` on the backslashes, so the whole token went to
        ``_win_to_wsl``, which looks at ``token[1] == ":"`` — position 1 of that
        string is "e", not a colon — and fell through to the branch that only
        swaps separators. The result was ``reducegrid,C:/Users/me/mask.nc``:
        still a Windows path, and one WSL cannot open. It never became
        ``/mnt/c/...``.

        Splitting on commas fixes it because a CDO operator token is exactly
        that: the operator followed by comma-separated parameters, any of which
        may be a path. A plain path has no comma in it and so passes through the
        loop unchanged.

        The case no one runs locally, and the only one this project can test
        without a Windows host — hence ``tests/test_conditional_selection.py``.
        """
        if not isinstance(arg, str):
            return arg
        if "," not in arg:
            return cls._win_to_wsl(arg) if cls._is_file_path(arg) else arg
        return ",".join(
            cls._win_to_wsl(part) if cls._is_file_path(part) else part
            for part in arg.split(",")
        )

    # Path utilities
    @staticmethod
    def _is_file_path(arg: str) -> bool:
        if not isinstance(arg, str):  # Ensure we only process strings
            return False
        climate_ext = (".nc", ".grb", ".grib", ".grib2", ".hdf", ".h5")
        return arg.endswith(climate_ext) or "/" in arg or "\\" in arg or (len(arg) > 1 and arg[1] == ":")

    @staticmethod
    def _win_to_wsl(path: PathLike) -> str:
        path_str = str(path)
        if len(path_str) > 1 and path_str[1] == ":":
            drive, rest = path_str[0].lower(), path_str[2:].replace("\\", "/")
            return f"/mnt/{drive}{rest}"
        return path_str.replace("\\", "/")

    @staticmethod
    def _validate_NCExplorer_arguments(*args):
        """Validate that all arguments are proper strings, not widget objects"""
        for i, arg in enumerate(args):
            if hasattr(arg, '__class__') and 'PyQt' in str(arg.__class__):
                raise ValueError(f"Argument {i} is a PyQt widget object instead of a string: {arg}")
            if not isinstance(arg, str):
                raise ValueError(f"Argument {i} must be a string, got {type(arg)}: {arg}")

    def _execute_command(self, cmd: List[str], timeout: int = DEFAULT_COMMAND_TIMEOUT,
                         env: Optional[Mapping[str, str]] = None,
                         stdin_path: str = "", cwd: str = "") -> NCExplorerResult:
        """Run one CDO command and collect what it produced.

        ``cwd`` overrides the directory the process starts in. Empty — which is
        every caller but one — keeps the temporary store's base directory, the
        behaviour this method has always had. See :meth:`_run_directory` for the
        one operator that needs its own.

        ``stdin_path`` names a file whose contents are the process's standard
        input. Empty — which is every operator but three — means an immediate
        EOF, and *never* the inherited stdin this call used to pass.

        That default is the fix for a whole class of hang. ``subprocess.run``
        with no ``stdin=`` hands the child whatever the parent has, which under
        a GUI launched from a terminal is that terminal and under a GUI launched
        from Finder is something that never reaches EOF either way. A dozen
        operators read from standard input, and against an inherited stdin they
        wait for a human who is looking at a window with no prompt in it: the
        run appears to hang and only ends at the timeout, minutes later, with
        nothing to show. ``operator_lab/harness.py`` has passed
        ``stdin=subprocess.DEVNULL`` since it was written and its docstring says
        exactly why; the production path simply never got the same treatment.

        Measured on 2.6.3: ``cdo input,r4x2 out.nc`` with stdin at
        ``/dev/null`` exits in milliseconds with "Too few input elements (0 of
        8)!", which is an honest, immediate, reportable failure. The same
        command with stdin attached returns nothing at all.

        What DEVNULL does *not* fix, because it is worth not over-claiming: an
        operator missing a required *parameter* does not block on input, it
        spins. ``cdo outputtab`` with no keynames was measured to run for over
        two minutes with stdin at ``/dev/null`` before being killed. Only
        ``missing_required_parameters`` prevents that one, and it still does.
        """
        # Everything about the command itself is logged at DEBUG and never shown
        # in the GUI: it is a developer affordance, reachable through the log file
        # or by launching with NCEXPLORER_DEBUG=1.
        if len(cmd) > 1:
            self._validate_NCExplorer_arguments(*cmd[1:])

        self.logger.debug("Executing NCExplorer command: %s", " ".join(cmd))

        # Defined before the try so the history still records an invocation whose
        # command could not even be built.
        full_cmd = list(cmd)
        cwd = cwd or str(self._tstore.base_dir)
        start = time.time()
        try:
            full_cmd = self._build_command(cmd)
            self.last_command = " ".join(full_cmd)
            # Logged before the run as well as after, so a command that hangs or
            # is killed still leaves a record of what was attempted.
            self.log_last_cdo_command()

            # Opened here rather than passed as a path so the descriptor is
            # closed on every exit from this block, timeout and exception
            # included.
            stdin_handle = open(stdin_path, "rb") if stdin_path else None
            try:
                outcome: subprocess.CompletedProcess[str] = subprocess.run(
                    full_cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=cwd,
                    # None for every run that declares no overrides *and* no
                    # bundled Magics data, which is the inherit-unchanged
                    # behaviour this call had before ``env`` existed. See
                    # :func:`run_environment` and :func:`magics_environment`.
                    env=run_environment(self._run_env(env)),
                    # DEVNULL rather than the inherited stdin, always. See this
                    # method's docstring for what that was costing.
                    stdin=stdin_handle if stdin_handle is not None
                    else subprocess.DEVNULL,
                )
            finally:
                if stdin_handle is not None:
                    stdin_handle.close()

            # Determine success based on operator type and output
            success = self._determine_command_success(
                cmd, outcome.returncode, outcome.stdout, outcome.stderr
            )

            duration = time.time() - start
            result = NCExplorerResult(
                success=success,
                stdout=outcome.stdout,
                stderr=outcome.stderr,
                execution_time=duration,
                returncode=outcome.returncode,
            )

            self._record_command(full_cmd, cwd, outcome.returncode, duration, env)
            if outcome.stdout:
                self.logger.debug("STDOUT: %s", outcome.stdout[:500])
            if outcome.stderr:
                # CDO writes ordinary progress chatter to stderr, so this is only
                # a problem when the command actually failed.
                level = logging.WARNING if not success else logging.DEBUG
                self.logger.log(level, "STDERR: %s", outcome.stderr)

            return result

        except subprocess.TimeoutExpired:
            self._record_command(full_cmd, cwd, None, time.time() - start, env)
            self.logger.error("Command timed out after %s seconds", timeout)
            return NCExplorerResult(False, "", f"Command timed out after {timeout} seconds")
        except Exception as exc:
            self._record_command(full_cmd, cwd, None, time.time() - start, env)
            self.logger.error("Command execution failed: %s", exc, exc_info=True)
            return NCExplorerResult(False, "", str(exc))

    def _run_env(self, overrides: Optional[Mapping[str, str]]) -> Dict[str, str]:
        """The caller's environment overrides plus anything the binary needs.

        One place, because the synchronous and the asynchronous paths must hand
        CDO the same environment and had no shared step where that could be
        guaranteed. Today the only addition is ``MAGPLUS_HOME`` for a bundled
        MAGICS build — see :func:`magics_environment` for why leaving it unset
        fails quietly rather than loudly.

        The caller wins on a collision, deliberately: a user who sets
        ``MAGPLUS_HOME`` explicitly is pointing at their own Magics
        installation, and this should not overrule them.
        """
        merged = dict(magics_environment(self.NCExplorer_binary))
        merged.update({str(k): str(v) for k, v in (overrides or {}).items()})
        return merged

    def _record_command(self, argv: List[str], cwd: str,
                        returncode: Optional[int], duration: float,
                        env: Optional[Mapping[str, str]] = None) -> CommandRecord:
        """Append one invocation to the bounded history and log it at DEBUG.

        ``env`` is the run's overrides only, and it is filtered the same way
        :func:`run_environment` filters them — blank values dropped — so the
        history records what was actually exported rather than what the form
        happened to contain. See :class:`CommandRecord` for why a command
        logged without them is a wrong log.
        """
        overrides = tuple(
            (str(name), str(value).strip())
            for name, value in (env or {}).items()
            if name and str(value).strip()
        )
        record = CommandRecord(
            argv=tuple(argv), cwd=cwd, returncode=returncode, duration=duration,
            env=overrides,
        )
        self.command_history.append(record)
        if len(self.command_history) > self.MAX_COMMAND_HISTORY:
            del self.command_history[:-self.MAX_COMMAND_HISTORY]

        self.logger.debug("Command finished rc=%s in %.2fs (cwd=%s): %s",
                          record.returncode, record.duration, record.cwd, record.as_text())
        return record

    def _determine_command_success(self, cmd: List[str], returncode: Optional[int],
                                   stdout: str, stderr: str) -> bool:
        """
        Determine if a cdo command succeeded based on operator type and output.

        Some operators like 'diff' return non-zero exit codes for valid results.

        Takes the three fields it actually reads rather than a
        ``subprocess.CompletedProcess``, because the asynchronous path
        (``core/async_executor.py``) never produces one and these rules must
        apply identically there.
        """
        if returncode is None:
            # No returncode at all: the process never started, or the caller
            # timed out waiting and never reaped one. Distinct from the signal
            # case below — this comment used to claim it covered "killed", and
            # it never did, because a killed process has a returncode.
            return False

        # A *signal* death, before any operator-specific rule gets a say.
        #
        # ``subprocess`` reports a process killed by signal N as the negative
        # number ``-N``, not as None, so every rule below that asks "is the
        # returncode small enough" silently accepts one. Two were measured
        # returning True for a crashed run:
        #
        #   _determine_command_success(["cdo","diffn","a","b"], -11, "rows\n", "")
        #       -> True, because the Diff branch tests ``returncode <= 1`` and
        #          -11 <= 1.
        #   _determine_command_success(["cdo","info","a"], -11, "partial\n", "")
        #       -> True, because every ``nout == 0`` operator is accepted on
        #          ``returncode == 0 or bool(stdout.strip())``.
        #
        # Both are a crash reported as a success with truncated output, which is
        # the worst shape a failure can take here: the user gets a partial
        # report with no indication it is partial.
        #
        # This is not hypothetical. CDO 2.6.3 dies by SIGSEGV on roughly one run
        # in six when a Magics operator aborts at the end of a pipe — a race in
        # its abort path while the inner pipe thread is still running, only
        # reproducible when stdout is a TTY. It is a general CDO property
        # rather than a Magics one, which is why the guard is here rather than
        # anywhere near the plotting code.
        #
        # No operator's success can legitimately be expressed as a signal
        # death, so this needs no exceptions and takes precedence over all of
        # them.
        if returncode < 0:
            return False

        if len(cmd) < 2:
            return returncode == 0

        operator = cmd[1].split(",", 1)[0]
        nin, nout = self.operator_signatures.get(operator, (1, 1))

        # The Diff module — which CDO files under Information, not under the
        # Comparison section. The two are easy to conflate and must not be: a
        # ``diff`` prints its report to stdout and writes no file, so exit 1
        # means "the files differ" and is a successful run. The twenty-four
        # operators of the Comparison section (eq/ne/le/lt/ge/gt, the Compc
        # constants, and the ymon/yseas families) write an output file and are
        # deliberately *not* listed here: for them a non-zero exit is a real
        # failure, and the default ``returncode == 0`` below is correct.
        # Measured on CDO 2.6.0 against the operator_lab samples — one operator
        # from each of the four modules — ``gt``, ``gtc`` and ``ymongt`` all
        # exit 0, and ``yseasgt`` exits 1. That last one is exactly the case
        # that must keep being reported as the failure it is: it aborts on its
        # own season table and leaves a header-only file with no data arrays
        # behind, so judging it by whether an output file appeared would call it
        # a success. See _SURPRISING_DEFAULTS in core/categories.py.
        #
        # The File operation section needs no case here, and that is the
        # finding rather than an omission. One operator from each of its
        # seventeen modules was run against the operator_lab samples on 2.6.3 —
        # copy, clone, cat, szip, tee, pack, unpack, bitrounding, replace,
        # duplicate, mergegrid, merge, mergetime, splitname, splitmon,
        # splitsel, splitdate, splitdatetime, splitrec, distgrid, collgrid and
        # ncopy — and every one of them exits 0 on success. The only non-zero
        # exits measured in the whole section were setchunkspec and setfilter
        # given a parameter file this build cannot parse, which are real
        # failures and are reported as such. Nothing in this section may be
        # accommodated by loosening the default below: ``yseasgt`` exits 1, that
        # is a genuine failure, and it must keep being reported as one.
        # The mirror image of ``yseasgt``, and it is reported as a failure.
        #
        # yseasgt exits non-zero and leaves a header-only file: the exit code is
        # right and the file is the lie. This is the other way round — CDO exits
        # 0, writes a structurally perfect NetCDF file, and puts the only
        # evidence that it contains nothing on stderr:
        #
        #     cdo    eof (Warning): Setting Matrix and Eigenvalues to 0 before return
        #
        # Measured on 2.6.3: the eigenvalue file that follows that line has a
        # maximum absolute value of 0, and so does the eigenvector file. There
        # is no sense in which that run succeeded.
        #
        # The argument for reporting it as a *notice on a successful run*
        # instead, since it was a real choice: the exit code is the engine's own
        # verdict, this layer already refuses to second-guess it for the
        # File operation section, and a user who wanted the zeros — to confirm
        # non-convergence, say — gets an error dialog for a run that did what
        # CDO said it would.
        #
        # It loses on what the two options cost when they are wrong. Called a
        # success, an all-zero decomposition flows onward: it draws as a blank
        # map, and eofcoeff projects onto it and writes a full set of
        # plausible-looking zero coefficients. Called a failure, the cost is one
        # dismissable error on a run whose output was zeros anyway. The
        # asymmetry is the whole reason the Comparison section's ``yseasgt``
        # exit code is likewise never softened.
        #
        # Deliberately keyed on the *message* and not on ``operator.startswith
        # ("eof")``. The execution layer does not know what an eof is and should
        # not learn: this is "CDO said the result is zeros", and any operator
        # that ever says it gets the same treatment.
        #
        # The honest limit, measured and worth stating because it bounds what
        # this can promise: with CDO_SVD_MODE=danielson_lanczos the same input
        # returns the same all-zero spectrum and prints *nothing at all* on
        # either stream, exit 0. This check catches the jacobi path, which is
        # the default and the one users will hit; it cannot catch a failure the
        # binary does not report. That is an argument for also reading the
        # eigenvalues, which belongs above this layer.
        if stderr_indicates_failure(stderr):
            return False

        if operator in ['diff', 'diffv', 'diffc', 'diffn', 'diffp']:
            # For diff operators:
            # - Exit code 0: files are identical
            # - Exit code 1: differences found (this is SUCCESS, not failure)
            # - Exit code >1: actual error
            if returncode <= 1 and stdout:
                return True
            elif returncode > 1:
                return False
            else:
                # No output but exit code 0 or 1 - probably an error
                return returncode == 0

        # Special handling for info operators (nout=0)
        elif nout == 0:
            # Info operators succeed if they produce output OR return code 0
            return returncode == 0 or bool(stdout.strip())

        # Special handling for validation/check operators
        elif operator in ['checkfile', 'verify', 'validate']:
            # These might return non-zero for "validation failed" vs. "error occurred"
            # Accept exit codes 0-1 if there's meaningful output
            return returncode <= 1 and (stdout or returncode == 0)

        # Default case: only exit code 0 is success
        return returncode == 0

    # -------------------------------------------------------------------------
    # Dynamic operator method factory
    # -------------------------------------------------------------------------

    def _generate_operator_methods(self) -> None:
        """
        Create one Python method per cdo operator using the signature
        published by `cdo --operators` (numbers in brackets: input|output).

        Legend
        -------
        Nin  =  -1  …  variable number of input files   (>=1)
                0   …  no input files
                1   …  exactly one input file
                2   …  exactly two input files
                3   …  exactly three input files
                …   etc.

        Nout =  -1  …  variable number of output files (>=1)
                0   …  no output file – the command prints to stdout
                1   …  exactly one output file
                …   etc.
        """

        for op_name in self.operator_signatures:
            setattr(self, op_name, self._build_signature_aware_method(op_name))

    # -------------------------------------------------------------------------
    # Generic utilities
    # -------------------------------------------------------------------------
    def get_operator_syntax(self, operator: str) -> str:
        """
        Retrieves the syntax (and possibly a brief description) for a given cdo operator
        by calling `CDO -h <operator>`.

        Args:
            operator: The name of the CDO operator (e.g., "sinfo", "selvar").

        Returns:
            A string containing the operator's syntax and description, or an error message.
        """
        try:
            # We use _execute_command directly here as we don't need a temporary output file
            # and want raw stdout/stderr for parsing help.
            cmd = self._build_command([self.NCExplorer_binary, "-h", operator])
            process = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                encoding='utf-8'
            )

            if process.returncode == 0:
                # CDO help output format often has the syntax line near the top.
                # This parsing is heuristic and might need adjustment for specific CDO versions.
                lines = process.stdout.splitlines()
                syntax_info = []
                for line in lines:
                    line_stripped = line.strip()
                    if line_stripped.startswith(operator):
                        syntax_info.append(line_stripped)
                    elif syntax_info and line_stripped:  # Add subsequent description lines
                        syntax_info.append(line_stripped)
                    if len(syntax_info) > 5 and not line_stripped:  # Limit description
                        break

                if syntax_info:
                    return "\n".join(syntax_info)
                else:
                    return (f"Syntax for '{operator}' not found in the operator help. "
                            f"Full output:\n{process.stdout}")
            else:
                self.logger.warning("Operator help call failed for '%s'. Stderr: %s",
                                    operator, process.stderr)
                return (f"Could not retrieve syntax for '{operator}'. "
                        f"Engine error: {process.stderr}")
        except FileNotFoundError:
            return "Processing engine binary not found. Cannot retrieve operator syntax."
        except Exception as e:
            self.logger.error("Error retrieving syntax for '%s': %s", operator, e, exc_info=True)
            return f"An error occurred while fetching syntax for '{operator}': {e}"

    def get_execution_info(self) -> Dict[str, str]:
        return {
            "platform": self.platform,
            "NCExplorer_binary": self.NCExplorer_binary,
            "execution_method": "WSL" if self.platform == "windows" and self.use_wsl else "native",
            "temp_dir": self.temp_dir,
        }

    def log_last_cdo_command(self) -> str:
        """Log the last CDO command at DEBUG and return it.

        Deliberately not a ``print``: the command text is developer information
        and must not reach the user's screen, so it goes to the log file and — at
        DEBUG — to the log dock. The return value is unchanged for programmatic
        callers.
        """
        if self.last_command:
            self.logger.debug("Last command: %s", self.last_command)
        else:
            self.logger.debug("No command has been run yet")
        return self.last_command

    def get_NCExplorer_version(self) -> NCExplorerResult:
        return self._execute_command([self.NCExplorer_binary, "--version"])

    #: Answer of the last ``cdo --config`` probe: True, False, or None for
    #: "could not be established". Cached because the binary does not change
    #: under a running instance and a subprocess per run is a subprocess per
    #: run; see :meth:`build_capability`.
    _capabilities: Optional[Dict[str, Optional[bool]]] = None

    def build_capability(self, feature: str) -> Optional[bool]:
        """Whether this CDO was compiled with ``feature``, or None if unknown.

        ``cdo --config <name>`` is CDO's own machine-readable answer to this and
        is what makes a capability check honest rather than an inference.
        Measured on the installed 2.6.3::

            cdo --config has-cmor     -> "no",  exit 0
            cdo --config all          -> a JSON object of 24 has-* keys

        The alternative, and the one this method exists instead of, is reading
        ``cdo --version``'s Features line: on this build it is "8GB 8threads
        c++20 Fortran pthreads HDF5 NC4/HDF5 dap sz proj sse4_2", which names no
        CMOR. But an *absence* in a list is not an answer — it is
        indistinguishable from the token being spelled differently, or from the
        line's contents changing between versions — and a capability check built
        on one would refuse valid work on the first build that words it
        otherwise. ``--config`` says yes and no, which is the difference between
        a check and a guess.

        ``None`` is returned for every way of not knowing: an older CDO with no
        ``--config``, a non-zero exit, a value that is neither yes nor no, or a
        binary that cannot be started. Callers must treat it as "proceed" —
        refusing on an unanswered probe would make this application stricter
        than the tool it fronts, which is the failure mode the whole schema is
        written to avoid.

        Cached per instance. One subprocess for the life of the application:
        ``--config all`` answers every key at once, so asking about a second
        feature costs nothing. See :meth:`build_capabilities`.
        """
        key = feature if feature.startswith("has-") else f"has-{feature}"
        return self.build_capabilities().get(key)

    def build_capabilities(self) -> Dict[str, Optional[bool]]:
        """Every ``has-*`` flag this CDO reports, probed once and cached.

        ``cdo --config all`` returns a JSON object — measured on 2.6.3, 24 keys
        of the form ``"has-magics":"no"`` — so one subprocess answers every
        capability question the application will ever ask. It replaced a probe
        that ran ``cdo --config has-<name>`` per feature, which was one
        subprocess per question for no more information.

        Parsed as JSON rather than scraped. The output is valid JSON as it
        stands (verified against the installed 2.6.3), and a parser is the
        difference between reading a documented format and matching on
        punctuation that may be reformatted between releases.

        Returns a mapping to True / False / None, where **None is a real answer
        and not an error**: it means "this build did not say", which is what an
        older CDO with no ``--config`` gives, and every caller must treat it as
        "proceed" rather than "refuse". Making the app stricter than the tool it
        fronts is the failure mode the whole capability layer is written to
        avoid.

        Deliberately *not* built on ``cdo -V``'s ``Features:`` line. On this
        build that line reads "8GB 8threads c++20 Fortran pthreads HDF5
        NC4/HDF5 dap sz proj sse4_2" — it does not mention MAGICS at all, in
        either direction. An absence in a list is not an answer: it cannot be
        told apart from the token being spelled differently or the line's
        contents changing between versions. ``--config`` says yes and no.
        """
        if self._capabilities is not None:
            return self._capabilities

        found: Dict[str, Optional[bool]] = {}
        try:
            probe = subprocess.run(
                self._build_command([self.NCExplorer_binary, "--config", "all"]),
                capture_output=True, text=True, timeout=20, check=False,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            self.logger.debug("Could not probe CDO build capabilities: %s", exc)
        else:
            if probe.returncode == 0:
                try:
                    raw = json.loads(probe.stdout or "{}")
                except ValueError:
                    self.logger.debug(
                        "cdo --config all was not JSON: %r", (probe.stdout or "")[:200])
                    raw = {}
                for name, value in raw.items():
                    text = str(value).strip().lower()
                    found[str(name)] = (
                        True if text == "yes" else False if text == "no" else None)

        self._capabilities = found
        if found:
            missing = sorted(k for k, v in found.items() if v is False)
            self.logger.info("CDO build features: %d known, missing %s",
                             len(found), ", ".join(missing) or "none")
        return found

    #: What CDO says when a build feature is missing, and what to say instead.
    #: Keyed by the marker matched case-insensitively against a run's combined
    #: output, so the same sentence serves the pre-run probe and the post-run
    #: translation and the two can never word it differently.
    _MISSING_FEATURE_ABORTS: Dict[str, str] = {
        "cmor support not compiled in":
            "This CDO was built without CMOR support, so the cmor operator "
            "cannot run whatever parameters it is given. Check with "
            "`cdo --config has-cmor`; the fix is a CDO binary built with CMOR, "
            "not a different command.",
        # The Magics six. Matching on this text rather than on a return code is
        # not a stylistic preference here — it is the only thing that works.
        #
        # Measured on 2.6.3: with a Magics operator at the end of a chain and
        # stdout on a terminal, ``cdo graph,device=png -fldmean in.nc plot``
        # exited 1 on 25 of 30 runs and died of SIGSEGV on the other 5. It is a
        # race in CDO's abort path while the inner pipe thread is still running,
        # it only reproduces on a TTY (which is what makes CDO start the
        # threaded pipe and print "cdo(1) fldmean: Process started"), and it is
        # independent of input size. A signal death arrives as a *negative*
        # returncode, which every ``returncode != 0`` test in this file reads
        # correctly but which a ``returncode == 1`` test would not — and the
        # async path reports it as a crash rather than as a failed run.
        #
        # The stderr line survives intact on every crashing run, so this
        # explanation reaches the user on all 30. That is the whole argument
        # for the table being keyed on text.
        "magics support not compiled in":
            "This CDO was built without MAGICS support, so the plotting "
            "operators (contour, shaded, grfill, vector, stream, graph) cannot "
            "run whatever parameters they are given. Check with "
            "`cdo --config has-magics`; the fix is a CDO binary built with "
            "MAGICS, not a different command.",
        # The one gap with no ``--config`` key, so this text is the *only*
        # evidence there is — see _BUILD_FEATURE_OPERATORS. Spelled "fftw3"
        # because that is what CDO writes: "FFTW3 support not compiled in!",
        # measured on 2.6.3. Matching "fftw " would miss it.
        "fftw3 support not compiled in":
            "This CDO was built without FFTW3 support, so fourier2grid cannot "
            "run whatever parameters it is given. Unlike the other build "
            "features, `cdo --config` has no key for FFTW3, so this can only "
            "be discovered by running the operator — which is why you are "
            "seeing it now rather than before the run. The fix is a CDO binary "
            "built with FFTW3.",
    }

    #: Operators that cannot run at all unless the binary carries a build
    #: feature, mapped to the ``cdo --config`` key that decides it and the
    #: :data:`_MISSING_FEATURE_ABORTS` marker naming what to say.
    #:
    #: Seven entries: ``cmor``, and the six Magics plot operators. These are the
    #: operators in the catalog whose failure mode is a compile-time option
    #: rather than anything about the data, and CDO's own message for each —
    #: "CMOR support not compiled in!", "MAGICS support not compiled in!" — is
    #: accurate and useless in equal measure to a user who has just filled in a
    #: form.
    #:
    #: ``cmorlite`` is deliberately absent, and it is the reason this is a table
    #: rather than a check on the name: it implements the CMOR *table* handling
    #: with CDO's own I/O library and needs no CMOR runtime, so it runs fine on a
    #: build where ``has-cmor`` is no. Sharing four letters is not sharing a
    #: dependency. ``conv_cmor_table`` and ``dump_cmor_table`` likewise.
    #:
    #: The Magics six are listed by name rather than derived from
    #: ``categories.NCExplorerCategory.GRAPHICS`` on purpose. The category is a
    #: statement about where an operator belongs in a menu; this is a statement
    #: about what the binary was linked against, and the two are free to diverge
    #: — a future CDO could add a plotting operator that does not need MAGICS,
    #: and it would be filed under Graphics and belong nowhere near this table.
    #: The names are also what ``cdo --config has-magics`` is being asked *on
    #: behalf of*, which is a fact about CDO rather than about this application.
    #:
    #: Measured on the installed 2.6.3: ``cdo --config has-magics`` answers
    #: "no" with exit 0, and all six abort with "MAGICS support not compiled
    #: in!" and exit 1 — the gate firing *before any parameter is parsed*, which
    #: was confirmed by giving ``shaded`` a parameter with no ``=`` in it and
    #: getting the identical abort rather than a parse error.
    #:
    #: ``fourier2grid`` is the one operator that belongs here on the same
    #: grounds and cannot be added: it needs FFTW3, and ``cdo --config`` has no
    #: key for it — ``has-fftw3``, ``has-fftw`` and ``has-FFTW3`` are all
    #: "unknown config option", and the 24 keys ``--config all`` returns name no
    #: transform library. There is nothing to probe, so a pre-run refusal would
    #: have to be a guess. It is left to :meth:`explain_failure` instead, which
    #: is the honest half of this pair: no entry here, and no marker in
    #: ``_MISSING_FEATURE_ABORTS`` either, since CDO's own "FFTW support not
    #: compiled in!" is not improved by restating it.
    #: The ``--config`` key is ``""`` when the binary offers none, which is the
    #: second evidence source and not an oversight — see below.
    _BUILD_FEATURE_OPERATORS: Dict[str, Tuple[str, str]] = {
        "cmor": ("has-cmor", "cmor support not compiled in"),
        **{
            name: ("has-magics", "magics support not compiled in")
            for name in ("contour", "shaded", "grfill",
                         "vector", "stream", "graph")
        },
        # FFTW3, and the reason this table stores a key that can be empty.
        #
        # ``fourier2grid`` needs FFTW3 exactly as ``cmor`` needs CMOR, and it
        # fails identically: "FFTW3 support not compiled in!", measured on this
        # 2.6.3. But **``cdo --config`` has no key for it**. Measured:
        # ``has-fftw3``, ``has-fftw`` and ``has-FFTW3`` are each "unknown config
        # option", and none of the 24 keys ``--config all`` returns names a
        # transform library at all.
        #
        # So one gate abstraction, two evidence sources: a ``--config`` flag
        # where the binary offers one, and the runtime error text where it does
        # not. The consequence is visible to the user and is stated rather than
        # hidden — ``fourier2grid`` cannot be refused *before* the run the way
        # the other seven can, so it is the one build gap this application
        # explains afterwards instead of preventing. An empty key means
        # :meth:`missing_build_feature` declines to guess, which is the same
        # rule it applies when a probe returns None.
        "fourier2grid": ("", "fftw3 support not compiled in"),
    }

    def missing_build_feature(self, operator: str) -> str:
        """The reason ``operator`` cannot run on this binary, or "" if it can.

        Asked before a run, so a user finds out from this application that their
        CDO cannot do this rather than from an abort after a subprocess. The
        operator is deliberately still offered everywhere it was offered before:
        the binary is a setting, a user may point the app at one that does carry
        CMOR or MAGICS, and hiding the operator would make that unreachable.

        For the Magics six this also avoids a failure the user would otherwise
        see as a crash. A plot operator at the end of a chain aborts from inside
        a running pipe, and CDO's abort path races the pipe thread: measured on
        2.6.3, 5 runs in 30 died of SIGSEGV rather than exiting 1. Refusing
        before the subprocess starts means the intermittent crash is not reached
        at all, which is a better outcome than translating it afterwards — and
        :meth:`explain_failure` still handles the case where this could not.

        Silent whenever :meth:`build_capability` cannot establish an answer, for
        the reason given there — an unanswered probe is not a refusal.
        """
        entry = self._BUILD_FEATURE_OPERATORS.get(operator)
        if entry is None:
            return ""
        feature, marker = entry
        # No ``--config`` key exists for this feature (FFTW3). There is nothing
        # to probe, so there is nothing to refuse on: the run goes ahead and
        # ``explain_failure`` translates the abort afterwards. Guessing here
        # would refuse ``fourier2grid`` on every build, including the ones that
        # can run it.
        if not feature:
            return ""
        if self.build_capability(feature) is not False:
            return ""
        return self._MISSING_FEATURE_ABORTS[marker]

    def capability_gap(self, operator: str) -> Optional[Tuple[str, str]]:
        """``(summary, detail)`` for an operator this build cannot run, or None.

        The same answer :meth:`missing_build_feature` gives, with a short label
        added for the surfaces that have to *show* the gap rather than raise it:
        ``("needs MAGICS support", "This CDO was built without…")``. Every
        operator surface — the toolbar menus, the command palette, the model
        builder's palette — marks a gated operator by calling this, so none of
        them can name a different feature from the one the run would, or grey
        out an operator the run would have allowed.

        The summary is derived from the same table entry as the detail rather
        than written beside it, for that reason: the ``--config`` key where the
        binary offers one (``has-magics`` -> MAGICS), the abort marker where it
        does not (``fftw3 support not compiled in`` -> FFTW3). Adding an
        operator to ``_BUILD_FEATURE_OPERATORS`` is therefore the whole change;
        no surface needs touching for it.

        None — never a placeholder gap — whenever the probe could not establish
        an answer, which is what stops a CDO too old for ``--config`` from
        greying out eight operators it may well be able to run.
        """
        detail = self.missing_build_feature(operator)
        if not detail:
            return None

        entry = self._BUILD_FEATURE_OPERATORS.get(operator)
        feature = "this build"
        if entry:
            feature = (entry[0].replace("has-", "") or
                       entry[1].split(" support", 1)[0]).upper()
        return f"needs {feature} support", detail

    @classmethod
    def explain_failure(cls, stdout: str, stderr: str) -> str:
        """Translate a known CDO abort into a sentence that names the cause.

        The other half of :meth:`missing_build_feature`, for the case where the
        capability could not be established beforehand — an older CDO with no
        ``--config``, or a probe that failed. CDO's own message is accurate and
        unhelpful in equal measure: "CMOR support not compiled in!" tells a user
        who has just filled in twenty-three fields nothing about what to do, and
        nothing at all about it being a property of their *build* rather than of
        their command.

        Returns "" for every message not in the table, so the caller falls back
        to CDO's own words. Deliberately additive: this never replaces the
        stderr a user or a bug report needs, it only says what it meant.
        """
        haystack = f"{stdout or ''}\n{stderr or ''}".lower()
        for marker, explanation in cls._MISSING_FEATURE_ABORTS.items():
            if marker in haystack:
                return explanation
        return ""

    @classmethod
    def _annotate_failure(cls, stdout: str, stderr: str,
                          returncode: Optional[int] = None) -> str:
        """``stderr`` with the explanation in front of it, when there is one.

        Prepended rather than substituted, deliberately: CDO's own message is
        what a bug report needs and what a user searching the web will match on,
        so it stays verbatim underneath. This only puts the sentence that names
        the cause where the surfaces already look, which is the ``stderr`` field
        of the result — the log dock, the batch report and the error dialog all
        read it, and adding a fourth field would have meant changing all three.

        Unchanged for every failure this application has nothing extra to say
        about, which is nearly all of them.

        ``returncode`` is optional and used only to name a signal death. It is a
        separate sentence from :meth:`explain_failure`'s table because the two
        answer different questions and a run can need both: a Magics operator
        aborting inside a pipe on a build without MAGICS produces the "not
        compiled in" explanation *and*, one run in six, a SIGSEGV. Keeping them
        separate is also what lets the capability explanation still be found —
        see :meth:`describe_signal`, and the note in
        :meth:`_determine_command_success` for why the stderr text survives the
        crash and the return code does not.
        """
        parts = [p for p in (cls.describe_signal(returncode),
                             cls.explain_failure(stdout, stderr)) if p]
        if not parts:
            return stderr
        explanation = "\n\n".join(parts)
        return f"{explanation}\n\n{stderr}" if stderr else explanation

    @staticmethod
    def describe_signal(returncode: Optional[int]) -> str:
        """Name the signal that killed a run, or "" if it exited normally.

        ``subprocess`` reports a signal death as ``-N``. Translated to a name
        here rather than shown as a bare negative number, because "-11" tells a
        user nothing and "killed by signal 11 (SIGSEGV)" tells them their run
        crashed rather than failed — which is the difference between "my data
        is wrong" and "this is a bug worth reporting".

        The name comes from :mod:`signal` rather than a table, so it is correct
        on whatever platform this runs on; an unrecognised number still gets the
        number.
        """
        if returncode is None or returncode >= 0:
            return ""
        number = -returncode
        try:
            import signal as _signal
            name = _signal.Signals(number).name
        except (ImportError, ValueError):
            name = f"signal {number}"
        return (
            f"CDO was killed by {name} (signal {number}) rather than exiting. "
            f"The run did not finish, so any output it produced is incomplete "
            f"even if a file exists and looks plausible."
        )

    def get_temp_filename(self, suffix: str = ".nc") -> str:
        if not suffix.startswith("."):
            suffix = "." + suffix
        return os.path.join(self.temp_dir, f"NCExplorer_temp_{os.getpid()}{suffix}")

    def cleanup_temp_files(self, pattern: str = "NCExplorer_temp_*") -> None:
        for fp in Path(self.temp_dir).glob(pattern):
            try:
                fp.unlink()
            except OSError:
                pass

    def __del__(self) -> None:
        self._tstore.cleanup()


# -----------------------------------------------------------------------------#
# Convenience factory functions
# -----------------------------------------------------------------------------#

def create_NCExplorer_integration(NCExplorer_binary_path: str = "cdo",
                           temp_dir: Optional[str] = None) -> NCExplorerIntegration:
    """Automatic selection (native vs. WSL on Windows)."""
    return NCExplorerIntegration(NCExplorer_binary_path=NCExplorer_binary_path, temp_dir=temp_dir)


def create_native_NCExplorer(NCExplorer_binary_path: str = "cdo",
                      temp_dir: Optional[str] = None) -> NCExplorerIntegration:
    """Force native execution (helpful on Windows when WSL is unwanted)."""
    return NCExplorerIntegration(NCExplorer_binary_path=NCExplorer_binary_path,
                          temp_dir=temp_dir, use_wsl=False)


def create_wsl_NCExplorer(NCExplorer_binary_path: str = "cdo",
                   temp_dir: Optional[str] = None) -> NCExplorerIntegration:
    """Force WSL execution (Windows only)."""
    if platform.system().lower() != "windows":
        raise NCExplorerError("WSL execution is only valid on Windows hosts")
    return NCExplorerIntegration(NCExplorer_binary_path=NCExplorer_binary_path,
                          temp_dir=temp_dir, use_wsl=True)
