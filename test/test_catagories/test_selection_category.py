"""The Selection section's season parameter, and the grammar it actually has.

``selseas``/``selseason`` take a *list* of seasons. Declared as a ``string``
carrying ``choices``, they were checked against the closed-set rule, which
refused the comma form outright:

    ValueError: selseas: seasons must be one of DJF, MAM, JJA, SON, not 'DJF,MAM'

so the application declined a command the binary runs. The full operator sweep
is what found it — the two were the only failures of 943 attributable to this
project rather than to CDO.

Two things are asserted here, and both were measured on the installed CDO 2.6.3
against a 730-step daily series (the ``series`` fixture builds the same one):

* **A list is accepted, and means something.** ``selseas,DJF,MAM`` keeps 364
  timesteps where ``selseas,DJF`` keeps 180, so the comma is not a second
  parameter being ignored.
* **The vocabulary is case-sensitive.** ``selseas,djf`` aborts with exit 1.
  That matters because the closed-set check the old declaration went through
  matches case-*insensitively* — argued from Magics, whose manual prints its
  enums in both cases — so the string form was lax in the one direction it
  should have been strict. ``multiselect`` compares each item exactly.

The ``:len`` suffix is the third measurement and the reason
:attr:`OperatorParam.item_suffix` exists. ``cdo selseas,DJF:8`` aborts, but the
suffix grammar used to be keyed on ``kind == "multiselect"``, so every list
parameter would have inherited a tail that only ``outputtab``/``outputkey``
have. See ``test_import_export_category.py`` for that side of it.
"""

import shutil
import subprocess

import pytest

from ncexplorer_toolkit.core.categories import (
    OPERATOR_SCHEMA, invalid_parameter_values, parameter_tokens,
)
from ncexplorer_toolkit.core.session_log import OperatorRequest

cdo_required = pytest.mark.skipif(
    shutil.which("cdo") is None, reason="needs an installed CDO")

#: The two spellings of one operator. ``cdo --operators`` lists both, and the
#: binary answers identically to each, so they carry one declaration.
SEASON_OPERATORS = ("selseas", "selseason")

#: What CDO accepts, in the manual's own case.
SEASONS = ("DJF", "MAM", "JJA", "SON")


# --- what the schema says ---------------------------------------------------

@pytest.mark.parametrize("operator", SEASON_OPERATORS)
def test_seasons_is_a_list_rather_than_one_choice(operator):
    seasons, = OPERATOR_SCHEMA[operator].params
    assert seasons.kind == "multiselect"
    assert seasons.choices == SEASONS


@pytest.mark.parametrize("operator", SEASON_OPERATORS)
def test_seasons_has_no_per_item_suffix(operator):
    """``name:12`` is ``outputtab``'s grammar, and only its own.

    Left on, ``DJF:8`` would pass validation and reach a command that aborts.
    """
    seasons, = OPERATOR_SCHEMA[operator].params
    assert seasons.item_suffix is False


# --- what it accepts and refuses --------------------------------------------

@pytest.mark.parametrize("operator", SEASON_OPERATORS)
@pytest.mark.parametrize("value", [
    "DJF", "DJF,MAM", "DJF,MAM,JJA,SON",
    "SON,DJF",              # order is the user's, and CDO does not mind
    "DJF,DJF",              # a repeat is harmless — measured, same 180 steps
    " DJF , MAM ",          # a form's whitespace is not the user's mistake
])
def test_accepted_seasons_are_accepted(operator, value):
    assert invalid_parameter_values(operator, [value]) == []


@pytest.mark.parametrize("operator", SEASON_OPERATORS)
@pytest.mark.parametrize("value,fragment", [
    ("djf", "not one of"),          # CDO is case-sensitive here; exit 1
    ("DJF,mam", "not one of"),      # one bad item is enough
    ("XYZ", "not one of"),
    ("DJF:8", "not one of"),        # the colon is part of the value
    ("DJF,,MAM", "empty entry"),
])
def test_refused_seasons_are_named_in_the_apps_own_words(
        operator, value, fragment):
    problems = invalid_parameter_values(operator, [value])
    assert problems, value
    assert fragment in problems[0]


@pytest.mark.parametrize("operator", SEASON_OPERATORS)
def test_the_seasons_token_is_the_command_cdo_documents(operator):
    """One comma-joined value, joined again into the operator token."""
    assert parameter_tokens(operator, ["DJF,MAM"]) == ["DJF,MAM"]
    request = OperatorRequest(operator=operator, input_files=("in.nc",),
                              output_files=("out.nc",), parameters=("DJF,MAM",),
                              nin=1, nout=1)
    assert request.command_line() == f"cdo {operator},DJF,MAM in.nc out.nc"


# --- re-measuring the binary ------------------------------------------------

@pytest.fixture(scope="module")
def series(tmp_path_factory):
    """Two years of daily steps, built with CDO itself.

    730 steps from 2000-01-01, which is what makes the counts below stable:
    DJF is 180 of them and MAM 184.
    """
    if shutil.which("cdo") is None:                     # pragma: no cover
        pytest.skip("needs an installed CDO")
    path = tmp_path_factory.mktemp("selection") / "series.nc"
    subprocess.run(
        ["cdo", "-s", "-f", "nc",
         "-settaxis,2000-01-01,00:00:00,1day", "-duplicate,730",
         "-random,r4x2", str(path)],
        check=True, capture_output=True, stdin=subprocess.DEVNULL)
    return path


def _ntime(path):
    result = subprocess.run(["cdo", "-s", "ntime", str(path)],
                            capture_output=True, text=True,
                            stdin=subprocess.DEVNULL)
    return int(result.stdout.strip())


@cdo_required
@pytest.mark.parametrize("operator", SEASON_OPERATORS)
@pytest.mark.parametrize("value,steps", [("DJF", 180), ("DJF,MAM", 364)])
def test_the_binary_takes_a_season_list(operator, value, steps, series, tmp_path):
    """The measurement the declaration rests on: the comma adds a season.

    If CDO were ignoring the second token this would return 180 for both, and
    a single-choice declaration would have been right.
    """
    out = tmp_path / f"{operator}_{value.replace(',', '_')}.nc"
    result = subprocess.run(
        ["cdo", "-s", f"{operator},{value}", str(series), str(out)],
        capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert result.returncode == 0, result.stderr
    assert _ntime(out) == steps


@cdo_required
@pytest.mark.parametrize("operator", SEASON_OPERATORS)
@pytest.mark.parametrize("value", ["djf", "DJF:8", "XYZ"])
def test_the_binary_refuses_what_the_app_refuses(operator, value, series, tmp_path):
    """Every value validation rejects, the binary rejects — so it is not lax."""
    assert invalid_parameter_values(operator, [value])
    out = tmp_path / "refused.nc"
    result = subprocess.run(
        ["cdo", "-s", f"{operator},{value}", str(series), str(out)],
        capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert result.returncode != 0, f"{operator},{value} was accepted by CDO"
