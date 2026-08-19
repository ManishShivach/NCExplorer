# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""Per-operator audit of the three surfaces that offer CDO operators.

The toolbar category menus, the command palette (Ctrl+K) and the model builder
palette each let the user pick an operator. This walks all three as the app
actually builds them — the real ``QMenu`` tree, the real ``build_entries``, the
real ``OperatorCatalog`` — and cross-checks every operator against
``cdo --operators``, so the report is evidence rather than a restatement of the
code that produced it.

    QT_QPA_PLATFORM=offscreen python audit_operator_surfaces.py

Two things are checked, because two things can drift. The surfaces must agree
on *which* operators exist, and on *what parameters each one takes*: the
parameter form behind the toolbar and the palette used to carry its own map of
129 operators beside the schema the model builder reads, and it had gone stale
where nobody could see it — offering ``eca_rx1day`` a ``mode`` argument CDO 2.6
answers with "Argument parse error!".

Writes ``docs/operator_audit.md`` and exits non-zero if the surfaces disagree
on either question, or offer an operator the installed CDO does not have.

The walk itself lives in ``operator_lab.surfaces``, because the operator test
lab fills a per-operator "can a user reach this?" column from the same three
widgets. Two walks would be two things to keep in step, and a disagreement
between them would discredit both reports.
"""
from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

from ncexplorer_toolkit.core.categories import (
    NCExplorerCategory, OPERATOR_CATEGORIES, OPERATOR_SCHEMA, operator_syntax,
)
from ncexplorer_toolkit.core.nc_integration import create_NCExplorer_integration
from operator_lab.surfaces import configure_headless, scan

REPORT = Path(__file__).resolve().parent / "docs" / "operator_audit.md"


def collect():
    """The three surfaces plus the installed catalog, each as they really are."""
    configure_headless()
    integration = create_NCExplorer_integration()
    result = scan(integration)
    if result.errors:
        for surface, message in result.errors.items():
            print(f"could not inspect {surface}: {message}", file=sys.stderr)

    # Which CDO produced the operator list, recorded in the report itself: the
    # counts below mean nothing without it, since a different build offers a
    # different catalog.
    version = integration.get_NCExplorer_version()
    version_lines = (version.stdout or version.stderr or "").strip().splitlines()
    binary = integration.NCExplorer_binary
    binary = shutil.which(binary) or binary

    return {
        "installed": result.installed,
        "menus": result.menus,
        "palette": result.palette,
        "builder": result.builder,
        "parameters": result.parameter_disagreements,
        "scanned_parameters": bool(result.parameters),
        "arity": result.arity_disagreements,
        "scanned_arity": bool(result.arity),
        "binary": binary,
        "version": version_lines[0].strip() if version_lines else "unknown",
    }


def signature(nin, nout):
    fmt = lambda v: "n" if v == -1 else str(v)  # noqa: E731
    return f"{fmt(nin)}→{fmt(nout)}"


def _shape(fields):
    """One surface's parameter list as a readable cell."""
    if not fields:
        return "_none_"
    return ", ".join(
        f"`{name}`:{kind}" + ("?" if optional else "")
        for name, kind, _label, optional in fields
    )


def build_report(data):
    installed = data["installed"]
    menus, palette, builder = data["menus"], data["palette"], data["builder"]
    parameter_problems = data["parameters"]
    arity_problems = data["arity"]
    curated = {op for ops in OPERATOR_CATEGORIES.values() for op in ops}

    names = sorted(set(installed) | set(menus) | set(palette) | builder)
    problems = [
        name for name in names
        if not (name in installed and name in menus
                and name in palette and name in builder)
    ]

    lines = [
        "# Operator surface audit",
        "",
        "NCExplorer lets you reach a CDO operator from three places: the toolbar's "
        "category menus, the command palette (Ctrl+K) and the model builder's "
        "palette. Nothing in the code forces those three to offer the same "
        "operators, or to ask for the same parameters when they do. This report is "
        "the check.",
        "",
        "`audit_operator_surfaces.py` builds the real widgets, reads back what each "
        "one offers, and compares all three against the operator list the installed "
        "CDO prints. Every number below was measured that way, so the report is "
        "evidence rather than a description of the code that produced it.",
        "",
        "Re-run it after any change to the catalog, the menus or the parameter "
        "forms:",
        "",
        "```bash",
        "QT_QPA_PLATFORM=offscreen python audit_operator_surfaces.py",
        "```",
        "",
        "It exits non-zero when a surface disagrees with the others or offers an "
        "operator the installed CDO cannot run, so it can gate a release.",
        "",
        f"- CDO tested: `{data['binary']}` — {data['version']}",
        f"- Report written: {datetime.now():%Y-%m-%d %H:%M}",
        "",
        "## What was found",
        "",
        f"- Installed CDO operators (`cdo --operators`): **{len(installed)}**",
        f"- Reachable from the toolbar menus: **{len(menus)}**",
        f"- Indexed by the command palette (Ctrl+K): **{len(palette)}**",
        f"- Offered by the model builder: **{len(builder)}**",
        f"- Disagreements about which operators exist: **{len(problems)}**",
        f"- Disagreements about an operator's parameters: "
        f"**{len(parameter_problems)}**"
        + ("" if data["scanned_parameters"] else " _(not checked — see above)_"),
        f"- Disagreements about an operator's arity: **{len(arity_problems)}**"
        + ("" if data["scanned_arity"] else " _(not checked — see above)_"),
        "",
    ]

    if arity_problems:
        lines += [
            "## Arity disagreements",
            "",
            "How many input and output files each surface believes an operator "
            "takes. This is the expensive one: a surface that is wrong here "
            "does not draw an odd form, it builds a command CDO refuses with "
            "\"Missing inputs\". The schema is the intended answer.",
            "",
            "| Operator | " + " | ".join(sorted(next(iter(
                arity_problems.values())))) + " |",
            "|---|" + "---|" * len(next(iter(arity_problems.values()))),
        ]
        for name, per_surface in arity_problems.items():
            cells = " | ".join(
                signature(*per_surface[surface]) if per_surface[surface] else "_absent_"
                for surface in sorted(per_surface))
            lines.append(f"| `{name}` | {cells} |")
        lines.append("")

    if parameter_problems:
        lines += [
            "## Parameter disagreements",
            "",
            "Each surface below would draw a different form for the same "
            "operator, so what a user is asked for depends on where they "
            "clicked. The schema is the intended answer.",
            "",
            "| Operator | " + " | ".join(sorted(next(iter(
                parameter_problems.values())))) + " |",
            "|---|" + "---|" * len(next(iter(parameter_problems.values()))),
        ]
        for name, per_surface in parameter_problems.items():
            cells = " | ".join(_shape(per_surface[surface])
                               for surface in sorted(per_surface))
            lines.append(f"| `{name}` | {cells} |")
        lines.append("")

    if problems:
        lines += ["## Disagreements", ""]
        for name in problems:
            where = [
                label for label, present in (
                    ("installed", name in installed), ("menus", name in menus),
                    ("palette", name in palette), ("builder", name in builder),
                ) if present
            ]
            lines.append(f"- `{name}` — only in: {', '.join(where) or 'nothing'}")
        lines.append("")
    else:
        lines += [
            "All four sets are identical: every installed operator is reachable "
            "from all three surfaces, and no surface offers an operator the "
            "installed CDO cannot run.",
            "",
        ]

    lines += [
        "The last two counts are the ones a user pays for. A surface that offers "
        "the right operator but the wrong parameters draws a form CDO answers with "
        "\"Argument parse error!\"; one that is wrong about how many input files an "
        "operator takes builds a command CDO refuses with \"Missing inputs\". "
        "Neither message names the surface that caused it, which is why they are "
        "counted here rather than left to a bug report. `core/categories.py` holds "
        "the intended answer in both cases.",
        "",
    ]

    lines += ["## Per-category totals", "",
              "How the catalog is spread across the sixteen category menus. Each "
              "menu shows a short list of common operators first and keeps the "
              "remainder one click away under **All …**, so no menu is 289 items "
              "long.",
              "",
              "| Category | Top level | Behind “All …” | Total |",
              "|---|---:|---:|---:|"]
    for category in NCExplorerCategory:
        top = sum(1 for c, d in menus.values() if c is category and d == 0)
        deep = sum(1 for c, d in menus.values() if c is category and d > 0)
        lines.append(f"| {category.value} | {top} | {deep} | {top + deep} |")
    top_all = sum(1 for _, d in menus.values() if d == 0)
    deep_all = sum(1 for _, d in menus.values() if d > 0)
    lines += [f"| **Total** | **{top_all}** | **{deep_all}** | **{len(menus)}** |", ""]

    lines += [
        "## Every operator",
        "",
        "One row per operator, sorted by name. What the columns mean:",
        "",
        "| Column | Reads as |",
        "|---|---|",
        "| `Sig` | input files → output files. `n` means \"any number\"; `1→0` is an "
        "operator that prints to the terminal instead of writing a file. |",
        "| `Params` | how many parameters the form asks for. `0` means the operator "
        "runs on the file alone. |",
        "| `Syntax` | the command shape, as CDO documents it. `[,x]` is optional; "
        "`x=<type>` is a keyword parameter rather than a positional one. |",
        "| `Placement` | where the operator sits in its category menu. *top* is a "
        "direct click, *top (curated)* one of the entries promoted for being "
        "commonly used, *All…* the submenu holding the rest of the category. |",
        "| `Menu` `Palette` `Builder` | ticked when the live widget really offers "
        "it. Three ticks on every row is the result this audit exists to confirm. |",
        "",
        "| # | Operator | Category | Sig | Params | Syntax | Placement | Menu | Palette | Builder |",
        "|---:|---|---|---|---:|---|---|:-:|:-:|:-:|",
    ]

    tick = lambda present: "✓" if present else "✗"  # noqa: E731
    for index, name in enumerate(names, 1):
        spec = OPERATOR_SCHEMA.get(name)
        category, depth = menus.get(name, (None, None))
        nin, nout = installed.get(name, (spec.nin, spec.nout) if spec else ("?", "?"))
        placement = "—" if depth is None else ("top" if depth == 0 else "All…")
        if name in curated and depth == 0:
            placement = "top (curated)"
        lines.append(
            f"| {index} | `{name}` "
            f"| {category.value if category else '—'} "
            f"| {signature(nin, nout)} "
            f"| {len(spec.params) if spec else 0} "
            f"| `{operator_syntax(name)}` "
            f"| {placement} "
            f"| {tick(name in menus)} | {tick(name in palette)} | {tick(name in builder)} |"
        )

    lines.append("")
    return "\n".join(lines), problems


def main():
    data = collect()
    report, problems = build_report(data)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(report)

    print(f"wrote {REPORT} ({len(data['installed'])} operators)")
    for label in ("menus", "palette", "builder"):
        print(f"  {label:8s} {len(data[label])}")

    parameter_problems = data["parameters"]
    arity_problems = data["arity"]
    failed = False
    if problems:
        print(f"\n{len(problems)} operator(s) not offered everywhere: "
              f"{', '.join(problems[:10])}")
        failed = True
    if parameter_problems:
        print(f"\n{len(parameter_problems)} operator(s) whose parameters differ "
              f"between surfaces: {', '.join(list(parameter_problems)[:10])}")
        failed = True
    if arity_problems:
        print(f"\n{len(arity_problems)} operator(s) whose arity differs between "
              f"surfaces: {', '.join(list(arity_problems)[:10])}")
        for name, per_surface in list(arity_problems.items())[:10]:
            shown = ", ".join(
                f"{surface}={signature(*value) if value else 'absent'}"
                for surface, value in sorted(per_surface.items()))
            print(f"    {name}: {shown}")
        failed = True
    if failed:
        return 1

    if not (data["scanned_parameters"] and data["scanned_arity"]):
        # Reported rather than passed over: an audit that silently checked some
        # of its three questions is worse than one that says it could not.
        print("\nWARNING: the surfaces could not be inspected, so the parameter "
              "lists and arities were not compared")
        return 1

    print("\nall three surfaces match the installed catalog exactly, and agree "
          "on every operator's parameters and arity")
    return 0


if __name__ == "__main__":
    sys.exit(main())
