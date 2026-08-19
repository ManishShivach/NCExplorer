# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""The file chooser offers what CDO reads in *that* slot, and nothing else.

Every Browse button in the operator form used to open the same dialog, built
from one module-level ``INPUT_FILE_DIALOG_FILTER`` with nine entries:

    All Supported Files (*.nc *.grb *.grib *.grb2 *.grib2 *.tif *.tiff *.ctl
                         *.h5 *.hdf *.hdf5 *.txt *.dat *.asc *.gmt *.tab *.csv
                         *.shp *.geojson *.gpkg *.gml *.kml)
    NetCDF Files (*.nc)
    GRIB Files (*.grb *.grib *.grb2 *.grib2)
    GrADS Descriptors (*.ctl)
    HDF5 Files (*.h5 *.hdf *.hdf5)
    Text / Table Files (*.txt *.dat *.asc *.gmt *.tab *.csv)
    GeoTIFF Files (*.tif *.tiff)
    Vector Files (*.shp *.geojson *.gpkg *.gml *.kml)
    All Files (*)

CDO reads GRIB, NetCDF, SERVICE, EXTRA and IEG (manual §1.1) and nothing else,
so five of those entries name formats no CDO run can open — they are the *map
canvas's* formats, and they were in front of the operator form because one
constant served both surfaces. The other two, ``.ctl`` and HDF5, are real CDO
inputs but of exactly two operators each.

The tests below are in two halves. The first asserts the negative — that the
formats CDO cannot read are gone from every operator's chooser, which is the
complaint this work came from. The second asserts the positive, per slot: that
``remap``'s SCRIP weights get a NetCDF chooser while its target grid does not,
that ``remapeta``'s ASCII ``vct`` and its data-file ``oro`` get different ones
though they sit in the same form, and that ``import_binary`` gets the ``.ctl``
chooser its synopsis needs.

One rule is asserted for every kind: **All Files is always the last entry**.
CDO's own examples name files with no extension (``cdo griddes infile > mygrid``
then ``cdo setgrid,mygrid …``), and SERVICE/EXTRA/IEG data is conventionally
extensionless, so a chooser that could not reach an unsuffixed file would refuse
correct input — the error this work exists to stop making in the other
direction.
"""

import pytest

from ncexplorer_toolkit.core import filetypes as ft
from ncexplorer_toolkit.core.categories import (
    OPERATOR_SCHEMA, input_file_kind, parameter_file_kind,
)


#: Extensions the old shared filter offered that no CDO run can open. Written
#: out rather than derived, so the test fails if one is quietly re-added.
NOT_CDO_INPUTS = (".tif", ".tiff", ".shp", ".geojson", ".gpkg", ".gml", ".kml")

#: Real CDO inputs, but only of the Import section. Offering them everywhere was
#: the milder half of the same bug.
IMPORT_ONLY = (".ctl", ".h5", ".hdf", ".hdf5")


def entries(filter_string):
    """The dialog's dropdown, as a list of ``caption (*.a *.b)`` strings."""
    return filter_string.split(";;")


# ---------------------------------------------------------------------------
# The negative: what is no longer offered
# ---------------------------------------------------------------------------

def test_no_operator_is_offered_a_format_cdo_cannot_read():
    """The whole catalog, not a sample: this was one constant, so it was uniform."""
    offenders = []
    for name in sorted(OPERATOR_SCHEMA):
        chooser = ft.dialog_filter(input_file_kind(name))
        for extension in NOT_CDO_INPUTS:
            if f"*{extension}" in chooser:
                offenders.append((name, extension))
    assert not offenders


def test_the_import_formats_reach_only_the_import_operators():
    """``.ctl`` and HDF5 are CDO inputs of six operators, not of 943."""
    allowed = {"import_binary", "import_grads", "import_cmsaf"}
    offenders = []
    for name in sorted(OPERATOR_SCHEMA):
        if name in allowed:
            continue
        chooser = ft.dialog_filter(input_file_kind(name))
        for extension in IMPORT_ONLY:
            if f"*{extension}" in chooser:
                offenders.append((name, extension))
    assert not offenders


def test_the_data_chooser_is_four_entries_not_nine():
    """Short enough to read, and every entry a format CDO documents."""
    got = entries(ft.dialog_filter(ft.DATA))
    assert got == [
        "CDO Data Files (*.nc *.nc1 *.nc2 *.nc4 *.nc5 *.cdf *.netcdf "
        "*.grb *.grb1 *.grb2 *.grib *.grib2 *.srv *.ext *.ieg)",
        "NetCDF Files (*.nc *.nc1 *.nc2 *.nc4 *.nc5 *.cdf *.netcdf)",
        "GRIB Files (*.grb *.grb1 *.grb2 *.grib *.grib2)",
        "All Files (*)",
    ]


def test_the_local_formats_are_offered():
    """SERVICE, EXTRA and IEG are CDO inputs and were in no chooser in the app.

    Manual §1.1: "The local MPI-MET data formats SERVICE, EXTRA and IEG are also
    supported". ``OUTPUT_EXTENSIONS`` has accepted all three on the way out
    since it was written, so the app would write a ``.srv`` it then could not
    offer to open.
    """
    chooser = ft.dialog_filter(ft.DATA)
    for extension in (".srv", ".ext", ".ieg"):
        assert f"*{extension}" in chooser


# ---------------------------------------------------------------------------
# The rule every kind follows
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", sorted(ft.FILE_KINDS))
def test_every_kind_ends_in_all_files(key):
    """An extensionless description file must always be reachable."""
    got = entries(ft.dialog_filter(key))
    assert got[-1] == "All Files (*)"
    assert got.count("All Files (*)") == 1


@pytest.mark.parametrize("key", sorted(ft.FILE_KINDS))
def test_no_kind_is_as_long_as_the_filter_it_replaced(key):
    """"Too many options" was the complaint; nine was the number.

    Four is the ceiling, and ``grid`` is the kind that reaches it — CDO accepts
    three different things there and the combined entry makes a fourth.
    """
    assert len(entries(ft.dialog_filter(key))) <= 4


@pytest.mark.parametrize("key", sorted(ft.FILE_KINDS))
def test_every_kind_says_what_it_wants(key):
    """The summary becomes the browse button's tooltip, so it has to exist."""
    assert ft.summary(key).strip()


def test_an_unknown_kind_hides_nothing():
    """A schema entry naming a kind this module has not grown must not raise."""
    assert ft.dialog_filter("a-kind-nobody-wrote") == "All Files (*)"


# ---------------------------------------------------------------------------
# The positive: per slot
# ---------------------------------------------------------------------------

def test_import_binary_asks_for_the_descriptor():
    """"cdo import_binary infile.ctl outfile" — the .ctl *is* infile.

    The binary it points at is named inside it (``DSET ^infile.bin``) and is
    never handed to CDO, so a chooser offering NetCDF and GRIB here points the
    user at the one file the operator cannot take.
    """
    for name in ("import_binary", "import_grads"):
        got = entries(ft.dialog_filter(input_file_kind(name)))
        assert got[0] == "GrADS Data Descriptors (*.ctl)"
        assert not any("NetCDF" in entry for entry in got)


def test_import_cmsaf_asks_for_hdf5():
    got = entries(ft.dialog_filter(input_file_kind("import_cmsaf")))
    assert got[0].startswith("HDF5 Files")


def kind_of(operator, param_name):
    """The filetypes key for one named parameter of ``operator``."""
    spec = OPERATOR_SCHEMA[operator]
    param = next(p for p in spec.params if p.name == param_name)
    return parameter_file_kind(param)


def test_remap_wants_a_netcdf_for_its_weights_and_not_for_its_grid():
    """Two file parameters of one operator, two formats.

    "weights [STRING] Interpolation weights (SCRIP NetCDF file)" against
    "targetgrid [STRING] Target grid description file or name". The old chooser
    could not tell them apart because it did not consult the parameter at all.
    """
    assert kind_of("remap", "weights") == ft.NETCDF
    assert kind_of("remap", "grid") == ft.GRID

    weights = ft.dialog_filter(kind_of("remap", "weights"))
    assert "*.grb" not in weights


def test_remapeta_wants_text_for_vct_and_data_for_oro():
    """The costliest pair in the catalog, because they sit side by side.

    "vct [STRING] File name of an ASCII dataset with the vertical coordinate
    table" and "oro [STRING] File name with the orography (surf. geopotential)
    of the target dataset".
    """
    assert kind_of("remapeta", "vct") == ft.TEXT
    assert kind_of("remapeta", "oro") == ft.DATA


def test_setgridarea_wants_a_data_file_not_a_grid_description():
    """It shares a widget with setgrid and does not share its meaning.

    "gridarea [STRING] Data file, the first field is used as grid cell area" —
    so the preset dropdown beside the field is beside the point, and a .txt
    grid description dropped in here is the wrong file.
    """
    for operator in ("setgridarea", "setgridmask"):
        assert kind_of(operator, "grid") == ft.DATA
    assert kind_of("setgrid", "grid") == ft.GRID


def test_the_ascii_parameters_are_not_restricted_to_netcdf():
    """Every description and table file CDO takes, in one assertion.

    A ``*.nc``-only chooser on any of these would hide the correct file. The
    inverse of the bug the rest of this file is about, and the reason the fix
    is a per-slot kind rather than a narrower shared filter.
    """
    ascii_slots = (
        ("maskregion", "regions"), ("setzaxis", "zaxis"),
        ("setcodetab", "table"), ("setpartab", "table"),
        ("bitrounding", "filename"), ("setchunkspec", "filename"),
        ("setfilter", "filename"), ("pack", "filename"),
        ("intlevel", "zdescription"), ("after", "vct"),
    )
    for operator, param in ascii_slots:
        assert kind_of(operator, param) == ft.TEXT, (operator, param)
        chooser = ft.dialog_filter(ft.TEXT)
        assert "*.nc" not in chooser


def test_every_file_parameter_in_the_catalog_resolves():
    """No file-valued parameter falls through to a chooser with no answer.

    ``ANY`` is a legitimate answer — it is what an undocumented parameter gets
    — but nothing in the catalog needs it today, and this is the test that says
    so out loud rather than leaving the coverage to be re-derived.
    """
    unresolved = []
    for name, spec in sorted(OPERATOR_SCHEMA.items()):
        for param in spec.params:
            if param.kind not in ("file", "grid"):
                continue
            if parameter_file_kind(param) == ft.ANY:
                unresolved.append((name, param.name))
    assert not unresolved


def test_a_non_file_parameter_gets_no_chooser():
    """The empty string means "no browse button belongs on this row"."""
    spec = OPERATOR_SCHEMA["addc"]
    assert all(parameter_file_kind(p) == "" for p in spec.params)


# ---------------------------------------------------------------------------
# The wiring: what the form actually opens
# ---------------------------------------------------------------------------

@pytest.fixture
def window(qapp):
    from ncexplorer_toolkit import NCExplorerOperatorGUI

    widget = NCExplorerOperatorGUI()
    yield widget
    widget.close()
    widget.deleteLater()


def browse_buttons(window):
    """``label -> QPushButton`` for every row of the open form that has one."""
    from PyQt6.QtWidgets import QPushButton

    found = {}
    for label, widget in window.parameter_widgets.items():
        if not hasattr(widget, "findChildren"):
            continue
        for button in widget.findChildren(QPushButton):
            if button.text() == "Browse":
                found[label] = button
    return found


def capture_filter(monkeypatch):
    """Record the filter string the next open dialog is given."""
    from PyQt6.QtWidgets import QFileDialog

    seen = []
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName",
        staticmethod(lambda *args, **kwargs: (seen.append(args[3]), ("", ""))[1]),
    )
    return seen


def test_the_form_opens_the_slots_own_chooser(window, monkeypatch):
    """End to end: click Browse on ``import_binary``'s input, get ``.ctl``.

    The point of the whole change, asserted through the widget rather than
    through the table — the previous filter was correct in a constant and wrong
    at the button, because the button never asked which row it was on.
    """
    seen = capture_filter(monkeypatch)
    window.show_operator_parameters("import_binary")

    buttons = browse_buttons(window)
    label = next(name for name in buttons if "Input" in name)
    buttons[label].click()

    assert seen and "*.ctl" in seen[0]
    assert "*.shp" not in seen[0]


def test_two_parameters_of_one_operator_open_different_choosers(window, monkeypatch):
    """``remapeta``'s ASCII vct and its data-file oro, from the same form."""
    seen = capture_filter(monkeypatch)
    window.show_operator_parameters("remapeta")

    buttons = browse_buttons(window)
    buttons["vct"].click()
    buttons["oro"].click()

    vct_filter, oro_filter = seen[-2], seen[-1]
    assert vct_filter != oro_filter
    assert "*.nc" not in vct_filter
    assert "*.nc" in oro_filter


def test_an_ordinary_operators_input_offers_only_cdo_formats(window, monkeypatch):
    """``timmean`` rather than ``sinfo``: a one-input operator draws the button.

    A variable-arity operator gets the MultiFileInputWidget instead, which the
    last test in this file covers.
    """
    seen = capture_filter(monkeypatch)
    window.show_operator_parameters("timmean")

    buttons = browse_buttons(window)
    label = next(name for name in buttons if "Input" in name)
    buttons[label].click()

    assert seen
    for extension in NOT_CDO_INPUTS + IMPORT_ONLY:
        assert f"*{extension}" not in seen[0]


def test_the_multi_file_widget_agrees_with_the_browse_button(window):
    """A two-input operator's list and a one-input operator's button, same set.

    They were written out separately once and disagreed — this one had no
    ``.grb2`` and the button had no ``.ctl`` — so which a user met was decided
    by the operator's arity.
    """
    window.show_operator_parameters("timcor")          # nin == 2
    widget = window.parameter_widgets["multi_file_widget"]

    assert widget.FILE_DIALOG_F == ft.dialog_filter(ft.DATA)
    assert ".grb2" in widget.FILE_FILTER
    assert ".ctl" not in widget.FILE_FILTER
