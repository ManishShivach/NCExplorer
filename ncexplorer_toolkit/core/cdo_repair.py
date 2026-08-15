"""Re-runnable provisioning of CDO's plotting support on Windows.

Reached three ways, all of which land here so there is one implementation:

* the installer, immediately after install;
* the installer's ``RunOnce`` resume step, after the restart that ``wsl
  --install`` requires;
* "Repair CDO plotting support" in the Start Menu, or ``NCExplorer.exe
  --repair-cdo`` from a shell, at any later time.

**The third is the reason this is a module rather than Pascal inside the .iss.**
An installer that only works during installation is one the user cannot recover
from: WSL can be broken by a Windows feature update, a distro can be
unregistered, and a user who declined the prompt the first time needs a way
back. Duplicating the logic in the installer would also mean two
implementations of one procedure, which is the thing this codebase repeatedly
records as the mistake.

macOS needs none of this — the .app carries a MAGICS-enabled CDO, so plotting
works with zero installation. See ``provision_cdo_macos.sh`` and README.

=============================== VERIFICATION ===============================
Written on macOS. The WSL invocations follow Microsoft's documented behaviour
but **have not been executed on Windows**. Every function that shells out to
``wsl.exe`` is marked. Run through the four cases listed in
``installer/setup_script.iss`` before shipping.
============================================================================
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

#: The script that does the distro-side work. Shipped beside the executable by
#: the installer's ``[Files]`` section.
PROVISION_SCRIPT = "provision_cdo_wsl.sh"

#: Seconds. A conda-forge solve plus download of cdo, magics and their
#: dependencies is minutes rather than seconds on a cold cache, and killing it
#: half way leaves a partial prefix that the next run has to redo.
PROVISION_TIMEOUT = 1800


@dataclass(frozen=True)
class RepairOutcome:
    """What one repair attempt did, in a form both a CLI and a dialog can show.

    ``needs_restart`` is separate from ``ok`` because the useful outcome of the
    WSL-install branch is neither success nor failure: the work is staged and
    will complete after a reboot. Collapsing it into a bool would make the
    installer either lie or despair.
    """

    ok: bool
    needs_restart: bool
    message: str

    @property
    def exit_code(self) -> int:
        # A pending restart is not a failure, so it must not look like one to
        # whatever launched the process.
        return 0 if (self.ok or self.needs_restart) else 1


def _run(argv: list[str], timeout: int = 60) -> tuple[int, str]:
    """Run a command, never raise, and return ``(returncode, combined output)``.

    ``returncode`` is -1 when the executable is missing entirely, which on
    Windows is how "this machine has no WSL at all" presents.
    """
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except FileNotFoundError:
        return -1, f"{argv[0]} not found"
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, str(exc)
    # wsl.exe writes UTF-16 on some builds; text=True has already decoded with
    # the console codepage, so NULs can survive. Strip them rather than let a
    # caller match on text that has them interleaved.
    return proc.returncode, (proc.stdout + proc.stderr).replace("\x00", "")


def wsl_available() -> bool:
    """Whether WSL is installed and configured. [UNVERIFIED on Windows]"""
    code, _ = _run(["wsl.exe", "--status"])
    return code == 0


def wsl_has_distro() -> bool:
    """Whether at least one distro is registered. [UNVERIFIED on Windows]

    A separate question from :func:`wsl_available`: WSL can be enabled with no
    distro installed, and CDO needs somewhere to live.
    """
    code, out = _run(["wsl.exe", "-l", "-q"])
    return code == 0 and bool(out.strip())


def _script_path() -> Optional[Path]:
    """The provisioning script, beside the frozen executable or in the repo."""
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).parent / PROVISION_SCRIPT)
    here = Path(__file__).resolve().parents[2]
    candidates.append(here / "installer" / PROVISION_SCRIPT)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def repair(*, from_resume: bool = False) -> RepairOutcome:
    """Bring CDO plotting support up, from whatever state the machine is in.

    Idempotent by construction: every step checks before acting, and the
    distro-side script exits early when it finds a working install. Running
    this on an already-provisioned machine is a no-op that reports success,
    which is what makes it safe to wire to a Start Menu item and to a resume
    step that may fire after the work is already done.

    ``from_resume`` only changes the wording — after a restart the user did not
    ask for this and needs to be told why a window appeared.
    """
    if sys.platform != "win32":
        return RepairOutcome(
            True, False,
            "Plotting support is built into the macOS application — there is "
            "nothing to repair on this platform.")

    if not wsl_available():
        return RepairOutcome(
            False, False,
            "Windows Subsystem for Linux is not available.\n\n"
            "Install it from an elevated PowerShell:\n\n"
            "    wsl --install\n\n"
            "That needs a restart. If the command reports that virtualization "
            "is disabled, it is a BIOS/UEFI setting — enable Intel VT-x or "
            "AMD-V in firmware setup first. Then run this repair again.")

    if not wsl_has_distro():
        return RepairOutcome(
            False, False,
            "WSL is enabled but no Linux distribution is installed.\n\n"
            "Install one from an elevated PowerShell:\n\n"
            "    wsl --install -d Ubuntu\n\n"
            "Then run this repair again.")

    script = _script_path()
    if script is None:
        return RepairOutcome(
            False, False,
            f"The provisioning script ({PROVISION_SCRIPT}) is missing from the "
            f"installation. Reinstall NCExplorer.")

    logger.info("Provisioning CDO inside WSL using %s", script)
    # Converted inside the distro with wslpath rather than by string surgery
    # here: the drive-letter mapping is the distro's business and a hand-built
    # /mnt/c path is wrong for a custom automount root.
    code, out = _run(
        ["wsl.exe", "--", "bash", "-lc",
         f'bash "$(wslpath \'{script}\')"'],
        timeout=PROVISION_TIMEOUT,
    )
    logger.debug("WSL provisioning output:\n%s", out)

    if code == 0:
        prefix = "Setup finished after the restart.\n\n" if from_resume else ""
        return RepairOutcome(
            True, False,
            prefix + "CDO plotting support is enabled. The contour, shaded, "
            "grfill, vector, stream and graph operators are now available.")

    tail = "\n".join(out.strip().splitlines()[-6:])
    return RepairOutcome(
        False, False,
        "CDO plotting support could not be enabled.\n\n"
        "NCExplorer still works — the six plotting operators will be shown "
        "greyed out with the reason. You can retry with "
        "\"Repair CDO plotting support\" in the Start Menu.\n\n"
        f"Last output:\n{tail}")


def main(argv: Optional[list[str]] = None) -> int:
    """``NCExplorer.exe --repair-cdo`` — console entry point.

    Deliberately does not start Qt: this is called by the installer and by a
    ``RunOnce`` resume step, both of which may run before a desktop session is
    fully up, and a GUI that fails to initialise there would turn a working
    repair into a crash. The result goes to stdout and to the exit code.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    outcome = repair(from_resume="--from-resume" in args)
    print(outcome.message)
    return outcome.exit_code
