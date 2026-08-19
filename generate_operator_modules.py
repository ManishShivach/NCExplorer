# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT

#!/usr/bin/env python3
"""Regenerate ``CDO_OPERATOR_MODULES`` in ``core/nc_operator_catalog.py``.

CDO groups its operators into modules, and the grouping is the only authority on
which operators belong together: ``ymonsub`` is arithmetic and ``ymonmean`` is a
statistic, and no rule over their names can tell you that. The application used
to guess with a prefix cascade and filed 32 of the 71 Arithmetic operators under
three different wrong categories — see ``_infer_category`` in ``core/categories.py``
for what replaced it.

The probe is ``cdo --help <operator>``, once per operator, because it is the only
one that answers for *every* operator:

* A documented operator prints a ``NAME`` block whose first line is
  ``op1, op2, ... - Title of the module``. The title is the group key. Operators
  that share a title share a module, including ones the title's own list omits
  (``muldoy`` resolves to "Arithmetic with days" without appearing in it).
* An undocumented operator aborts with ``Help for <op> in module <M> not found``,
  which names the module directly.

``cdo --module_info <M>`` is not used. It takes a module *identifier* rather than
an operator, there is no way to enumerate the identifiers, and it does not know
all of them: on CDO 2.6.0 ``--module_info Yseasarith`` reports "Module not found"
while ``--help yseasadd`` documents those four operators perfectly well.

Usage:
    python generate_operator_modules.py            # rewrite the catalog in place
    python generate_operator_modules.py --check    # exit 1 if it would change
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CATALOG = Path(__file__).resolve().parent / "ncexplorer_toolkit" / "core" / "nc_operator_catalog.py"

#: Start and end of the generated block, so the hand-written parts of the
#: catalog and the descriptions above it are never touched.
BEGIN = "CDO_OPERATOR_MODULES: dict[str, str] = {"
END = "}"

#: "Help for log in module Math not found"
_UNDOCUMENTED = re.compile(r"Help for \S+ in module (\S+) not found")


def installed_operators() -> list[str]:
    """Every operator name ``cdo --operators`` lists."""
    out = subprocess.run(["cdo", "--operators"], capture_output=True, text=True,
                         timeout=60)
    names = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if line:
            names.append(line.split()[0])
    return names


def _name_block_title(text: str) -> str:
    """The module title out of a ``NAME`` block, or "" if there is none.

    The block reads ``NAME`` then ``op1, op2, ... - Title``, and the title wraps
    onto the following line whenever the operator list is long — which for Math
    leaves the first line ending in a bare ``-``. Three lines are joined so both
    shapes parse the same way.
    """
    lines = [line.rstrip() for line in text.splitlines()]
    for index, line in enumerate(lines):
        if line.strip() != "NAME":
            continue
        block = " ".join(part.strip() for part in lines[index + 1:index + 4])
        _, separator, title = block.partition(" - ")
        if not separator:
            return ""
        # Cut at the start of the next section: the join above flattened the
        # blank line that separated them into a run of spaces.
        return re.split(r"\s{2,}", title.strip())[0].strip()
    return ""


def module_of(operator: str) -> str:
    """The module ``operator`` belongs to, as CDO reports it. "" when unknown."""
    try:
        out = subprocess.run(["cdo", "--help", operator], capture_output=True,
                             text=True, timeout=30)
    except subprocess.SubprocessError:
        return ""

    text = out.stdout + out.stderr

    undocumented = _UNDOCUMENTED.search(text)
    if undocumented:
        # This path names the module by its *identifier* ("Math") while the
        # documented path names it by its *title* ("Mathematical functions").
        # Left alone, `log` would land in a group of its own beside the other
        # seventeen Math operators. Ask the identifier for its own title so both
        # halves of a module agree on one key.
        identifier = undocumented.group(1)
        try:
            info = subprocess.run(["cdo", "--module_info", identifier],
                                  capture_output=True, text=True, timeout=30)
        except subprocess.SubprocessError:
            return identifier
        return _name_block_title(info.stdout + info.stderr) or identifier

    return _name_block_title(text)


def collect() -> dict[str, str]:
    operators = installed_operators()
    with ThreadPoolExecutor(max_workers=8) as pool:
        modules = list(pool.map(module_of, operators))
    return {op: module for op, module in zip(operators, modules) if module}


def render(mapping: dict[str, str]) -> str:
    width = max(len(f'"{op}":') for op in mapping) + 1
    lines = [f'{BEGIN}']
    for op in sorted(mapping):
        key = f'"{op}":'
        lines.append(f'    {key:<{width}} "{mapping[op]}",')
    lines.append(END)
    return "\n".join(lines)


def splice(source: str, block: str) -> str:
    """Replace the generated block, or append it with its banner."""
    start = source.find(BEGIN)
    if start == -1:
        banner = (
            "\n\n"
            "#: ``operator -> CDO module``, probed from the installed binary by\n"
            "#: ``generate_operator_modules.py``. The module is what decides an\n"
            "#: operator's category; see ``categories._MODULE_CATEGORY``.\n"
        )
        return source.rstrip("\n") + banner + block + "\n"
    end = source.find(f"\n{END}\n", start)
    if end == -1:
        raise SystemExit("CDO_OPERATOR_MODULES block is not terminated")
    return source[:start] + block + source[end + len(f"\n{END}"):]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if the catalog is out of date, write nothing")
    args = parser.parse_args()

    mapping = collect()
    if not mapping:
        print("No modules resolved — is cdo on PATH?", file=sys.stderr)
        return 2

    current = CATALOG.read_text(encoding="utf-8")
    updated = splice(current, render(mapping))

    if args.check:
        if current != updated:
            print(f"{CATALOG} is out of date; rerun without --check",
                  file=sys.stderr)
            return 1
        print(f"{CATALOG} is up to date ({len(mapping)} operators)")
        return 0

    CATALOG.write_text(updated, encoding="utf-8")
    modules = sorted(set(mapping.values()))
    print(f"Wrote {len(mapping)} operators in {len(modules)} modules to {CATALOG}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
