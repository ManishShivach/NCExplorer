#!/usr/bin/env python3
"""Run every CDO operator and write the result to an Excel workbook.

The command-line half of the operator test lab. ``testCDOcommands.py`` is the
same sweep with a window around it; both drive ``operator_lab``, so a run from
here and a run from there produce the same rows.

    python test_all_operators.py                          # all 943 → Excel
    python test_all_operators.py --operators timmean,info
    python test_all_operators.py --category "Statistical values"
    python test_all_operators.py --input rain.nc          # against your own file
    python test_all_operators.py --gui                    # open the window instead

Inputs are generated with CDO — two years of daily data on a small global grid,
plus a multi-level file for the vertical operators — so a run needs nothing but
an installed CDO. ``--input`` replaces them with your own files and reads the
variable name back out of the first one, so parameters like ``selname,<var>``
name something the file contains.

Exit code 0 when nothing failed, 1 when something did, 2 when the run could not
start at all.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence

from operator_lab import (
    DEFAULT_TIMEOUT, FAIL, OUTPUT_BUDGET_BYTES, PASS, SKIPPED, RunReport,
    SampleError, SampleSet, default_output_dir, sweep, write_report,
)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Run with --gui for the point-and-click version.",
    )
    selection = parser.add_argument_group("what to run")
    selection.add_argument(
        "--operators",
        help="Comma-separated operator names, or 'all' (the default).")
    selection.add_argument(
        "--category",
        help="Only operators in this category, e.g. \"Statistical values\".")
    selection.add_argument(
        "--limit", type=int,
        help="Stop after this many operators. Useful for a quick check.")
    selection.add_argument(
        "--include-untestable", action="store_true",
        help="Also attempt the operators that need stdin or external data. "
             "Most will abort or time out; the reason is in the report.")

    data = parser.add_argument_group("inputs and outputs")
    data.add_argument(
        "--input", nargs="+", metavar="FILE",
        help="Use these files instead of generated samples. The first is the "
             "primary input; the rest fill the multi-input slots.")
    data.add_argument(
        "--output", type=Path, default=None,
        help=f"Directory for outputs and the report (default: "
             f"{default_output_dir()}).")
    data.add_argument(
        "--excel", type=Path,
        help="Path for the workbook (default: a timestamped file in --output).")
    data.add_argument(
        "--json", type=Path,
        help="Also write the raw rows as JSON, for scripted comparison "
             "between runs.")

    behaviour = parser.add_argument_group("behaviour")
    behaviour.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT,
        help=f"Seconds one operator may take (default: {DEFAULT_TIMEOUT}).")
    behaviour.add_argument(
        "--binary", default="cdo",
        help="CDO binary to test (default: whatever resolves as 'cdo').")
    behaviour.add_argument(
        "--keep-large-outputs", action="store_true",
        help=(f"Keep every output file. By default the largest are deleted, "
              f"once their sizes are in the report, until the rest fit "
              f"{OUTPUT_BUDGET_BYTES // (1024 * 1024)} MB."))
    behaviour.add_argument(
        "--quiet", action="store_true", help="Only print the summary.")
    behaviour.add_argument(
        "--gui", action="store_true",
        help="Open the operator test lab window instead of sweeping here.")

    return parser.parse_args(argv)


def choose_operators(args: argparse.Namespace, available: Sequence[str]) -> List[str]:
    """Resolve the ``--operators``/``--category``/``--limit`` combination."""
    from ncexplorer_toolkit.core.categories import OPERATOR_SCHEMA

    chosen = list(available)

    if args.operators and args.operators.strip().lower() not in {"all", "*"}:
        wanted = [name.strip() for name in args.operators.split(",") if name.strip()]
        unknown = [name for name in wanted if name not in set(available)]
        if unknown:
            raise SystemExit(
                f"Not offered by this CDO: {', '.join(sorted(unknown))}")
        chosen = wanted

    if args.category:
        wanted = args.category.strip().lower()
        chosen = [
            name for name in chosen
            if (spec := OPERATOR_SCHEMA.get(name))
            and spec.category.value.lower() == wanted
        ]
        if not chosen:
            categories = sorted({
                spec.category.value for spec in OPERATOR_SCHEMA.values()})
            raise SystemExit(
                f"No operators in category '{args.category}'.\n"
                f"Known categories: {', '.join(categories)}")

    if args.limit is not None:
        chosen = chosen[:args.limit]

    return chosen


def print_summary(report: RunReport, excel: Path, json_path: Optional[Path]) -> None:
    passed, failed, skipped = (report.count(status)
                               for status in (PASS, FAIL, SKIPPED))
    attempted = passed + failed

    print()
    print("=" * 72)
    print(f"  {passed} passed   {failed} failed   {skipped} skipped"
          f"   ({report.duration:.0f}s)")
    if attempted:
        print(f"  {passed / attempted * 100:.1f}% of the {attempted} operators "
              f"that could be attempted")
    print("=" * 72)

    problems = [check for check in report.preflight if not check.ok]
    if problems:
        print("\nPreflight problems — treat the per-operator results with caution:")
        for check in problems:
            print(f"  ✗ {check.name}: {check.detail}")

    if report.failures:
        print("\nFailures by root cause:")
        for cause, count in Counter(
                outcome.issue_type or "Unclassified"
                for outcome in report.failures).most_common():
            print(f"  {count:4d}  {cause}")

    unreachable = report.unreachable
    if unreachable:
        print(f"\n{len(unreachable)} operator(s) not reachable from every surface:")
        for outcome in unreachable[:10]:
            surfaces = outcome.surfaces
            absent = [label for label, present in (
                ("toolbar", surfaces.toolbar),
                ("command palette", surfaces.palette),
                ("model builder", surfaces.model_builder),
            ) if not present]
            print(f"  {outcome.operator} — missing from {', '.join(absent)}")
        if len(unreachable) > 10:
            print(f"  … and {len(unreachable) - 10} more (see the Surfaces sheet)")

    print(f"\nExcel report: {excel}")
    if json_path:
        print(f"JSON rows:    {json_path}")
    kept = report.bytes_written - report.bytes_pruned
    print(f"Outputs:      {report.output_dir} "
          f"({kept / 1024 ** 2:.0f} MB kept of {report.bytes_written / 1024 ** 2:.0f} "
          f"MB written)")


def write_json(report: RunReport, path: Path) -> None:
    """The rows as JSON, for diffing one run against another."""
    payload = {
        "created_at": report.started.isoformat(timespec="seconds"),
        "cdo_binary": report.cdo_binary,
        "cdo_version": report.cdo_version,
        "samples": report.sample_description,
        "totals": {status: report.count(status)
                   for status in (PASS, FAIL, SKIPPED)},
        "preflight": [asdict(check) for check in report.preflight],
        "results": [
            {**{key: value for key, value in asdict(outcome).items()
                if key != "surfaces"},
             "surfaces": asdict(outcome.surfaces)}
            for outcome in report.outcomes
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    if args.gui:
        from operator_lab.gui import launch
        return launch()

    from ncexplorer_toolkit.core.nc_integration import (
        NCExplorerError, create_NCExplorer_integration,
    )

    try:
        integration = create_NCExplorer_integration(
            NCExplorer_binary_path=args.binary)
    except NCExplorerError as exc:
        print(f"Could not resolve CDO: {exc}", file=sys.stderr)
        return 2

    output_dir = Path(args.output or default_output_dir())
    try:
        samples = (
            SampleSet.from_files([Path(p) for p in args.input],
                                 integration.NCExplorer_binary)
            if args.input else
            SampleSet.generate(output_dir / "_samples",
                               integration.NCExplorer_binary)
        )
    except SampleError as exc:
        print(f"Could not prepare the sample inputs: {exc}", file=sys.stderr)
        return 2

    available = sorted(integration.get_operator_signatures())
    operators = choose_operators(args, available)
    if not operators:
        print("Nothing to run.", file=sys.stderr)
        return 2

    if not args.quiet:
        print(f"CDO:     {integration.NCExplorer_binary}")
        print(f"Samples: {samples.describe()}")
        print(f"Running: {len(operators)} operator(s), {args.timeout}s each")
        print()

    def report_progress(index: int, total: int, outcome) -> None:
        if args.quiet:
            return
        mark = {PASS: "·", FAIL: "F", SKIPPED: "s"}.get(outcome.status, "?")
        # One self-erasing line: a 943-line scroll hides the failures in it.
        print(f"\r[{index:4d}/{total}] {mark} {outcome.operator:<24}", end="",
              flush=True)
        if outcome.status == FAIL:
            print(f"\r  FAIL {outcome.operator:<22} {outcome.why[:80]}")

    report = sweep(
        operators=operators,
        integration=integration,
        samples=samples,
        output_dir=output_dir,
        timeout=args.timeout,
        skip_untestable=not args.include_untestable,
        prune_outputs=not args.keep_large_outputs,
        on_result=report_progress,
    )
    if not args.quiet:
        print("\r" + " " * 78 + "\r", end="")

    excel = Path(args.excel) if args.excel else (
        output_dir / f"cdo_operator_report_{datetime.now():%Y%m%d_%H%M%S}.xlsx")
    excel = write_report(report, excel)

    if args.json:
        write_json(report, Path(args.json))

    print_summary(report, excel, Path(args.json) if args.json else None)

    if any(not check.ok for check in report.preflight):
        return 2
    return 1 if report.count(FAIL) else 0


if __name__ == "__main__":
    sys.exit(main())
