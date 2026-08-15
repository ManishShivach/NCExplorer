#!/usr/bin/env python3
"""Open the CDO operator test lab: tick operators, press Run, save the Excel.

    python testCDOcommands.py
    python testCDOcommands.py --operators timmean,yearmean,selname

The window lists every operator the installed CDO offers, with its category,
its signature, the file extensions it prefers and a tick for each place in
NCExplorer it can be picked from — the toolbar menus and the model builder.
Tick what you want, press **Run selected** (or **Run all**), watch the rows
fill in, then **Save Excel report…**.

Inputs are generated with CDO unless you choose your own with *Choose files…*.
The sweep runs on a worker thread, so the window stays responsive and **Stop**
takes effect after the operator currently running.

The same sweep without a window is ``test_all_operators.py``; both drive the
``operator_lab`` package, so neither can disagree with the other.
"""
from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--operators",
        help="Comma-separated operator names to pre-tick when the window opens.")
    parser.add_argument(
        "--binary", default="cdo",
        help="CDO binary to test (default: whatever resolves as 'cdo').")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication, QMessageBox

    from ncexplorer_toolkit.core.nc_integration import (
        NCExplorerError, create_NCExplorer_integration,
    )
    from operator_lab.gui import COL_OPERATOR, OperatorLabWindow

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("CDO operator test lab")

    try:
        integration = create_NCExplorer_integration(
            NCExplorer_binary_path=args.binary)
    except NCExplorerError as exc:
        QMessageBox.critical(None, "CDO not available", str(exc))
        return 1

    window = OperatorLabWindow(integration)

    if args.operators:
        wanted = [name.strip() for name in args.operators.split(",") if name.strip()]
        unknown = [name for name in wanted if name not in window.rows]
        for name in wanted:
            row = window.rows.get(name)
            if row is not None:
                window.table.item(row, COL_OPERATOR).setCheckState(Qt.CheckState.Checked)
        if unknown:
            status_bar = window.statusBar()
            if status_bar is not None:
                status_bar.showMessage(
                    f"Not offered by this CDO, so not ticked: {', '.join(unknown)}")

    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
