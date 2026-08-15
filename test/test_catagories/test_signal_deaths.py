"""A CDO that dies by signal must never be reported as a success.

CDO 2.6.3 can die rather than exit. Measured on this machine: ``cdo
graph,device=png -fldmean in.nc plot`` — a Magics operator at the end of a pipe
on a build without MAGICS — exits 1 on 25 of 30 runs and dies with **SIGSEGV on
the other 5**. It is a race in CDO's abort path while the inner pipe thread is
still running; it only reproduces when stdout is a TTY (which is what makes CDO
start the threaded pipe at all) and it is independent of input size.

``subprocess`` reports that as a **negative returncode**, ``-11``. Not ``None``.
That distinction is the whole bug: ``_determine_command_success`` guarded
``returncode is None`` with a comment claiming it covered a killed process, and
a killed process does not produce ``None``. So ``-11`` fell through into the
operator-specific rules, two of which accept it:

    _determine_command_success(["cdo","diffn","a","b"], -11, "some rows\\n", "")
        -> True, because the Diff branch tests ``returncode <= 1``
    _determine_command_success(["cdo","info","a"], -11, "partial\\n", "")
        -> True, because every ``nout == 0`` operator is accepted on
           ``returncode == 0 or bool(stdout.strip())``

Both are a crashed run reported as a success carrying truncated output — a
partial report with nothing saying it is partial, which is worse than the abort
it was found next to.

**These tests use fabricated returncodes, never a real crash.** The race is
about one run in six and only on a TTY, so a test that tried to provoke it would
fail intermittently in both directions. A flaky test is worse than none; what
has to hold is the *rule*, and the rule is a pure function of (cmd, returncode,
stdout, stderr).
"""

import pytest

from ncexplorer_toolkit.core.nc_integration import (
    NCExplorerIntegration, NCExplorerResult, create_NCExplorer_integration,
)
from operator_lab.harness import classify, explain, signal_name


@pytest.fixture(scope="module")
def integration():
    """A real instance: ``_determine_command_success`` reads operator_signatures.

    Constructed rather than ``__new__``-ed because the attribute lookup goes
    through ``__getattr__``, which recurses without ``__init__``.
    """
    return create_NCExplorer_integration()


# ---------------------------------------------------------------------------
# The two cases that were measured returning True
# ---------------------------------------------------------------------------

def test_diffn_killed_by_signal_is_not_a_success(integration):
    """The Diff branch's ``returncode <= 1`` must not accept ``-11``.

    ``diff`` legitimately exits 1 to mean "the files differ", which is why the
    branch exists at all — so the fix cannot be to tighten that rule, and this
    pins both halves: -11 fails, 1 still passes.
    """
    assert integration._determine_command_success(
        ["cdo", "diffn", "a", "b"], -11, "some rows\n", "") is False


def test_info_killed_by_signal_is_not_a_success(integration):
    """A ``nout == 0`` operator is accepted on stdout alone; a crash is not.

    This is the more dangerous of the two: the run printed part of its report
    before dying, so there *is* stdout, and the old rule read that as the answer.
    """
    assert integration._determine_command_success(
        ["cdo", "info", "a"], -11, "partial\n", "") is False


@pytest.mark.parametrize("returncode", [-1, -2, -6, -9, -11, -15])
def test_no_signal_is_ever_a_success(integration, returncode):
    """Whatever the signal and whatever the operator.

    Parameterised over the signals a CDO run realistically dies of — SIGHUP,
    SIGINT, SIGABRT, SIGKILL, SIGSEGV, SIGTERM — and across the three operator
    shapes whose rules differ, because the guard has to sit ahead of all of them
    rather than be repeated inside each.
    """
    for cmd, stdout in (
        (["cdo", "diffn", "a", "b"], "rows\n"),
        (["cdo", "info", "a"], "partial\n"),
        (["cdo", "fldmean", "a", "b"], ""),
        (["cdo", "sinfon", "a"], "File format : NetCDF\n"),
    ):
        assert integration._determine_command_success(
            cmd, returncode, stdout, "") is False, (cmd, returncode)


# ---------------------------------------------------------------------------
# The rules the guard must not have broken
# ---------------------------------------------------------------------------

def test_normal_exit_codes_keep_their_meaning(integration):
    """The quirks the guard sits in front of still apply.

    Without this the fix could have been "return ``returncode == 0``", which
    would break ``diff`` (exit 1 means the files differ) and every ``nout == 0``
    operator that reports through stdout.
    """
    assert integration._determine_command_success(
        ["cdo", "diffn", "a", "b"], 1, "some rows\n", "") is True
    assert integration._determine_command_success(
        ["cdo", "info", "a"], 0, "output\n", "") is True
    assert integration._determine_command_success(
        ["cdo", "fldmean", "a", "b"], 0, "", "") is True
    assert integration._determine_command_success(
        ["cdo", "fldmean", "a", "b"], 1, "", "") is False


def test_none_returncode_is_still_refused(integration):
    """The original guard's own case, which is a different one.

    ``None`` means no exit status was ever produced — cancelled, timed out,
    never started. It is not a crash, and both must fail.
    """
    assert integration._determine_command_success(
        ["cdo", "info", "a"], None, "partial\n", "") is False


# ---------------------------------------------------------------------------
# What the user is told
# ---------------------------------------------------------------------------

def test_the_signal_is_named_not_numbered():
    """"-11" is not a diagnosis; "SIGSEGV" is."""
    message = NCExplorerIntegration.describe_signal(-11)
    assert "SIGSEGV" in message
    assert "11" in message
    # The part that matters for a user staring at an output file that exists.
    assert "incomplete" in message.lower()


def test_describe_signal_is_silent_for_a_normal_exit():
    """Nothing is prepended to the stderr of a run that merely failed."""
    assert NCExplorerIntegration.describe_signal(0) == ""
    assert NCExplorerIntegration.describe_signal(1) == ""
    assert NCExplorerIntegration.describe_signal(None) == ""


def test_annotate_failure_carries_both_the_signal_and_the_cause():
    """A crashed MAGICS run needs both sentences, and CDO's own text verbatim.

    This is the exact shape of the run that prompted all of this: the operator
    aborted because the build has no MAGICS, *and* the abort crashed. The
    capability explanation must still be found — it is keyed on the stderr text,
    which survives the crash — and CDO's original line must still be present
    underneath for a bug report.
    """
    stderr = "cdo    graph (Abort): MAGICS support not compiled in!"
    annotated = NCExplorerIntegration._annotate_failure("", stderr, -11)
    assert "SIGSEGV" in annotated
    assert "MAGICS" in annotated
    assert stderr in annotated


def test_result_exposes_the_crash():
    """``killed_by_signal`` is what a surface reads to say "crashed"."""
    assert NCExplorerResult(False, "", "", returncode=-11).killed_by_signal is True
    assert NCExplorerResult(False, "", "", returncode=1).killed_by_signal is False
    assert NCExplorerResult(True, "", "", returncode=0).killed_by_signal is False
    # The default keeps every pre-existing construction of this class working.
    assert NCExplorerResult(True, "", "").killed_by_signal is False


# ---------------------------------------------------------------------------
# operator_lab
# ---------------------------------------------------------------------------

def test_signal_name_translates_and_stays_quiet_otherwise():
    assert signal_name(-11) == "SIGSEGV"
    assert signal_name(-9) == "SIGKILL"
    assert signal_name(1) == ""
    assert signal_name(None) == ""


def test_the_lab_keeps_the_cause_when_a_crash_also_happened():
    """The ordering that stops one operator being counted as two causes.

    On a build without MAGICS the six plot operators abort, and about one run in
    six of those aborts crashes. The stderr line survives, so five runs would be
    classified from the text and one from the signal — the report would show
    ``contour`` as both a build gap and a crash and neither count would be true.
    The text has to win.
    """
    text = explain(-11, "", "cdo    graph (Abort): MAGICS support not compiled in!")
    assert "SIGSEGV" in text, "the crash is still reported"
    assert classify(text) == "Not available in this CDO build", \
        "but the finding is the build gap, not the crash"


def test_a_crash_with_no_message_is_its_own_issue_type():
    """When CDO said nothing, the signal is all there is — and is a finding."""
    text = explain(-11, "", "")
    assert classify(text) == "Killed by a signal"


def test_ordinary_failures_are_classified_exactly_as_before():
    """The new pattern must not have shadowed any existing one."""
    assert classify(explain(1, "", "cdo selname (Abort): No variables selected!")) \
        == "Sample lacks the requested variable"
    assert classify(explain(1, "", "cdo (Abort): something else entirely")) \
        == "CDO aborted"
