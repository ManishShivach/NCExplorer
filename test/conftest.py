# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""Shared headless-Qt setup for every test directory under ``test/``.

``test/tests_ui/conftest.py`` has done this since the UI suite existed, but a
conftest only applies to its own directory and below — so the category suites in
``test/test_catagories/`` could not see ``qapp``, and every test in them that
drives the model builder errored at setup with "fixture 'qapp' not found"
rather than failing on anything real. That covered
``test_the_all_submenu_groups_by_module`` in the arithmetic suite and
``test_the_builder_can_actually_build_the_companion`` in the comparison one,
which is the test the EOF suite's builder tests are modelled on.

Only the environment setup and ``qapp`` live here — the things every suite
needs. The synthetic NetCDF fixtures stay in ``tests_ui/conftest.py``, where the
tests that use them are; a fixture visible to suites that never ask for it is
the sort of shared state that makes a suite hard to reason about.

``qapp`` is redefined in ``tests_ui/conftest.py`` and that is harmless: a nested
conftest overrides for its own directory, both build the same single
QApplication, and Qt permits only one either way.
"""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# In a conda environment Qt reports the plugin path of conda's own (Qt5) qt
# package, whose platform plugins cannot be loaded by Qt6 — QApplication then
# aborts with "Could not find the Qt platform plugin". Point Qt at PyQt6's own
# plugins, which are always the right ones for the PyQt6 in use.
if "QT_QPA_PLATFORM_PLUGIN_PATH" not in os.environ:
    import PyQt6

    bundled = Path(PyQt6.__file__).resolve().parent / "Qt6" / "plugins"
    if (bundled / "platforms").is_dir():
        os.environ["QT_PLUGIN_PATH"] = str(bundled)
        os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(bundled / "platforms")

from PyQt6.QtWidgets import QApplication  # noqa: E402  (must follow the env setup)


@pytest.fixture(scope="session")
def qapp():
    """One QApplication for the whole session (Qt allows only one)."""
    return QApplication.instance() or QApplication([])
