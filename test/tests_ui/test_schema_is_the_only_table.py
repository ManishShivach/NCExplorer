"""The schema answers the questions the deleted lookup tables used to.

``nc_integration`` carried seven category sets — INFO_OPERATORS,
SINGLE_FILE_OPERATORS, TWO_INPUT_OPERATORS, THREE_INPUT_OPERATORS,
MULTI_INPUT_OPERATORS, SELECTION_OPERATORS, RUNNING_OPERATORS — plus
EXTRA_PARAM_COUNTS: about 255 lines that nothing read, or that one dead branch
read. They had also drifted, which is the point: a second copy of the truth that
nobody consults is a second copy that nobody corrects.

These tests do not assert that the tables are absent — a deleted name is not
worth a test. They assert that the schema gives the right answer for the
operators the deleted tables gave the wrong one for, and that the one code path
that used to read EXTRA_PARAM_COUNTS still works.
"""

import pytest

from ncexplorer_toolkit.core.categories import (
    NCExplorerCategory, OPERATOR_SCHEMA, operator_total_param_count,
)


#: What ``SINGLE_FILE_OPERATORS`` claimed to be "the one-input operators". It
#: listed the Math family and stopped there.
SINGLE_FILE_OMISSIONS = ("pow", "reci", "not", "mulcoslat", "divcoslat",
                         "muldpm", "divdpm", "muldpy", "divdpy", "muldoy")


@pytest.mark.parametrize("operator", SINGLE_FILE_OMISSIONS)
def test_the_schema_knows_what_the_deleted_set_omitted(operator):
    """Every one of these is 1-in/1-out, and the hand-written set missed it."""
    spec = OPERATOR_SCHEMA[operator]
    assert (spec.nin, spec.nout) == (1, 1)
    assert spec.category is NCExplorerCategory.ARITHMETIC


def test_parameter_counts_come_from_the_schema():
    """``operator_total_param_count`` is what replaced EXTRA_PARAM_COUNTS."""
    assert operator_total_param_count("pow") == 1
    assert operator_total_param_count("sellonlatbox") == 4
    assert operator_total_param_count("random") == 2       # grid + optional seed
    # ``ymonmean`` in place of the ``timmean`` this used to name: Timstat now
    # declares ``complete_only`` and Ymonstat is the family that really takes
    # nothing ("Too many arguments! Need 0 found 1" on 2.6.3).
    assert operator_total_param_count("ymonmean") == 0
    assert operator_total_param_count("timmean") == 1      # complete_only
    assert operator_total_param_count("not_a_cdo_operator") == 0


def test_every_operator_the_legacy_path_can_reach_is_in_the_schema():
    """Why the EXTRA_PARAM_COUNTS fallback was unreachable, asserted.

    ``_invoke_legacy_operator`` only ever sees names in ``operator_signatures``,
    which comes from ``cdo --operators`` — the same catalog ``OPERATOR_SCHEMA``
    is built from. The ``else`` branch that read EXTRA_PARAM_COUNTS therefore
    had no input that could take it.
    """
    from ncexplorer_toolkit.core.nc_integration import create_NCExplorer_integration

    integration = create_NCExplorer_integration()
    assert set(integration.operator_signatures) <= set(OPERATOR_SCHEMA)


def test_the_legacy_attribute_api_still_builds_the_right_command(tmp_path,
                                                                 monkeypatch):
    """``integration.addc(...)`` is public, so its rewrite has to keep working.

    Extra parameters first, then inputs, then outputs — the flat positional
    order that path exists to accept.
    """
    from ncexplorer_toolkit.core.nc_integration import (
        NCExplorerIntegration, create_NCExplorer_integration,
    )

    integration = create_NCExplorer_integration()
    built: list[list[str]] = []
    # ``**_`` rather than a fixed signature: this stub stands in for a private
    # method, and what it is asserting is the *command* that reaches it, not the
    # keywords the caller passes. Spelling them out made the test fail when
    # ``_execute_command`` gained ``env`` — a change it has no opinion about.
    monkeypatch.setattr(
        NCExplorerIntegration, "_execute_command",
        lambda self, cmd, **_: built.append(list(cmd)) or type(
            "R", (), {"success": False, "stdout": "", "stderr": "",
                      "output_file": None, "execution_time": 0.0})())

    source = tmp_path / "in.nc"
    source.write_bytes(b"CDF\x01")
    integration.addc("-273.15", str(source), str(tmp_path / "out.nc"))

    assert built, "the legacy path never reached the executor"
    assert built[0][1] == "addc,-273.15"


def test_the_legacy_path_refuses_a_blank_required_parameter(tmp_path):
    """Phase 1's gate covers this entry point too — it goes through the same resolve."""
    from ncexplorer_toolkit.core.nc_integration import create_NCExplorer_integration

    integration = create_NCExplorer_integration()
    source = tmp_path / "in.nc"
    source.write_bytes(b"CDF\x01")

    with pytest.raises(ValueError, match="constant"):
        integration.addc("", str(source), str(tmp_path / "out.nc"))
