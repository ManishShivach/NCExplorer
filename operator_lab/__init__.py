"""Bulk testing for the CDO integration and its 943 operators.

One harness, three front ends: ``testCDOcommands.py`` (pick operators, press
Run), ``test_all_operators.py`` (the same sweep from the command line) and
``testCDO.py`` (the integration's own unit tests plus a live preflight). They
share this package so a result cannot depend on which one produced it.

    from operator_lab import sweep
    report = sweep(operators=["timmean", "info"])
    print(report.count("pass"), report.count("fail"))

:func:`sweep` writes nothing; hand its :class:`~.harness.RunReport` to
:func:`~.excel.write_report` for the workbook.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from .excel import write_report
from .harness import (
    DEFAULT_TIMEOUT, FAIL, OUTPUT_BUDGET_BYTES, PASS, SKIPPED,
    OperatorOutcome, OperatorTestRunner, PreflightCheck, RunReport, prune,
)
from .profiles import (
    DATA_EXTENSIONS, PARAMETER_DEFAULTS, UNTESTABLE, output_kind,
    preferred_input_extension, preferred_output_extension, skip_reason,
)
from .samples import SampleError, SampleSet
from .surfaces import OperatorSurfaces, SurfaceScan, configure_headless, scan

__all__ = [
    "DATA_EXTENSIONS", "DEFAULT_TIMEOUT", "FAIL", "OUTPUT_BUDGET_BYTES",
    "PASS", "SKIPPED", "OperatorOutcome", "OperatorSurfaces",
    "OperatorTestRunner", "PARAMETER_DEFAULTS", "PreflightCheck", "RunReport",
    "SampleError", "SampleSet", "SurfaceScan", "UNTESTABLE",
    "configure_headless", "default_output_dir", "output_kind",
    "preferred_input_extension", "preferred_output_extension", "prune", "scan",
    "skip_reason", "sweep", "write_report",
]

logger = logging.getLogger(__name__)


def default_output_dir() -> Path:
    """Where a run puts its outputs unless told otherwise.

    Under the project rather than the user's Desktop — the previous harness
    wrote to a hard-coded Desktop folder, which is not somewhere a checkout
    should scatter a few hundred NetCDF files. ``.gitignore`` covers it.
    """
    return Path(__file__).resolve().parent.parent / "operator_test_output"


def sweep(
    *,
    operators: Optional[Sequence[str]] = None,
    integration=None,
    samples: Optional[SampleSet] = None,
    output_dir: Optional[Path] = None,
    timeout: int = DEFAULT_TIMEOUT,
    parameter_overrides: Optional[Dict[str, str]] = None,
    skip_untestable: bool = True,
    prune_outputs: bool = True,
    on_result: Optional[Callable[[int, int, OperatorOutcome], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> RunReport:
    """Run ``operators`` (default: all installed) and return the report.

    Everything is optional so the common case is one call. Passing an
    ``integration`` or a ``SampleSet`` reuses work the caller has already done —
    which the GUI does, because resolving CDO and building the samples together
    take longer than the first dozen operators.

    ``prune_outputs`` deletes the largest operator outputs once their sizes have
    been recorded; pass False to keep every byte. See :func:`harness.prune`.
    """
    from ncexplorer_toolkit.core.nc_integration import create_NCExplorer_integration

    started = datetime.now()
    integration = integration or create_NCExplorer_integration()
    output_dir = Path(output_dir or default_output_dir())
    samples = samples or SampleSet.generate(
        output_dir / "_samples", binary=integration.NCExplorer_binary)

    runner = OperatorTestRunner(
        integration, samples, output_dir,
        timeout=timeout,
        parameter_overrides=parameter_overrides,
        skip_untestable=skip_untestable,
    )

    preflight = runner.preflight()
    selection: List[str] = list(operators) if operators else runner.available_operators()
    outcomes = runner.run_many(selection, on_result=on_result, should_stop=should_stop)

    # Measured before pruning, so the report says what the sweep *wrote* rather
    # than what happens to survive it.
    bytes_written = sum(outcome.output_bytes or 0 for outcome in outcomes)
    pruned = prune(outcomes, output_dir) if prune_outputs else 0

    version = integration.get_NCExplorer_version()
    version_line = (version.stdout or version.stderr or "").strip().splitlines()

    return RunReport(
        bytes_written=bytes_written,
        bytes_pruned=pruned,
        outcomes=outcomes,
        preflight=preflight,
        started=started,
        finished=datetime.now(),
        sample_description=samples.describe(),
        output_dir=output_dir,
        cdo_binary=integration.NCExplorer_binary,
        cdo_version=version_line[0] if version_line else "unknown",
        surface_errors=dict(runner.surfaces.errors),
    )
