# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""The eca_* / etccdi_* declarations, checked against the installed CDO.

These operators are the ones where a wrong declaration is worst. They do not
refuse a file that is not what they wanted — ``eca_cwfi`` will run against any
second file with a compatible grid and write plausible, entirely wrong numbers —
so the checks here are about what the app *claims* before CDO is reached:

* every index declares its parameters, and the shape matches ``cdo -h``;
* every extra input slot declares what it must hold and how to build it;
* the recipes are runnable, and their output is what the operator accepts.

The last of those actually runs CDO, so it is skipped where there is none.
"""

import shutil
import subprocess

import pytest

from ncexplorer_toolkit.core.categories import (
    OPERATOR_SCHEMA, UNIT_FAMILIES, format_recipe, operator_inputs,
    operator_syntax,
)
from ncexplorer_toolkit.core import units

cdo_required = pytest.mark.skipif(
    shutil.which("cdo") is None, reason="needs an installed CDO")

#: Every climate index the installed catalog has, aliases included.
INDICES = sorted(
    name for name in OPERATOR_SCHEMA
    if name.startswith("eca_") or name.startswith("etccdi")
)

#: The module name rather than an index: ``cdo -h etccdi`` documents the six
#: bootstrapping operators and CDO refuses the bare name, so it has no input
#: slots of its own to declare.
NOT_AN_INDEX = {"etccdi"}


def test_the_catalog_has_the_indices_this_file_is_about():
    """A guard on the guard: an empty parametrisation would pass everything."""
    assert len(INDICES) > 50


# --- parameters -----------------------------------------------------------

@pytest.mark.parametrize("name", INDICES)
def test_every_index_declares_its_parameters(name):
    """Declared, even when the declaration is "none".

    A missing key and an empty tuple both produce no field in the GUI, but only
    one of them is a statement. ``eca_pd`` is why it matters: run with no
    argument it does not fail, it sits in CDO's interactive prompt loop forever,
    and the only thing between a user and that is a parameter field.
    """
    assert name in OPERATOR_SCHEMA
    from ncexplorer_toolkit.core.categories import _PARAM_SPECS

    assert name in _PARAM_SPECS, f"{name} has no _PARAM_SPECS entry"


def test_eca_pd_keeps_its_threshold_required():
    """The one index whose argument CDO does not default.

    Without it ``cdo eca_pd`` prompts on stdin and never stops; with an optional
    parameter the GUI would happily send exactly that.
    """
    params = OPERATOR_SCHEMA["eca_pd"].params
    assert [p.name for p in params] == ["x"]
    assert not params[0].optional


@pytest.mark.parametrize("name,expected", [
    ("eca_cdd", ["R", "N", "freq"]),
    ("etccdi_cdd", ["R", "N", "freq"]),
    # `cdo -h eca_cwd` prints `[,params]`, but the binary parses R and N
    # positionally exactly as eca_cdd does. The binary wins.
    ("eca_cwd", ["R", "N", "freq"]),
    ("eca_cfd", ["N"]),
    ("eca_csu", ["T", "N"]),
    ("eca_su", ["T", "freq"]),
    ("eca_tr", ["T", "freq"]),
    ("eca_fd", ["freq"]),
    ("eca_id", ["freq"]),
    ("eca_hd", ["T1", "T2"]),
    ("eca_rr1", ["R"]),
    ("eca_sdii", ["R"]),
    ("eca_pd", ["x"]),
    ("eca_r10mm", []),
    ("eca_r20mm", []),
    ("eca_rx1day", ["freq"]),
    ("eca_rx5day", ["x", "freq"]),
    ("eca_cwdi", ["nday", "T"]),
    ("eca_hwdi", ["nday", "T"]),
    ("eca_cwfi", ["nday", "freq"]),
    ("eca_hwfi", ["nday", "freq"]),
    ("etccdi_csdi", ["nday", "freq"]),
    ("etccdi_wsdi", ["nday", "freq"]),
    ("eca_gsl", ["nday", "T", "fland"]),
    ("etccdi_gsl", ["nday", "T", "fland"]),
    ("eca_etr", []),
    ("eca_tg10p", []),
    ("eca_r75ptot", []),
    ("etccdi_r1mm", ["freq"]),
    ("etccdi_rx1daymon", ["freq"]),
    ("etccdi_rx5daymon", ["x", "freq"]),
    # Unchanged, and deliberately different from each other — see the comment
    # on _ETCCDI_PRECIP_PARAMS.
    ("etccdi_tx90p", ["n", "startboot", "endboot", "freq"]),
    ("etccdi_r95p", ["startboot", "endboot", "freq"]),
])
def test_declared_parameter_shapes(name, expected):
    assert [p.name for p in OPERATOR_SCHEMA[name].params] == expected


def test_the_two_frequency_grammars_stay_apart():
    """``freq`` means a key=value pair to the ECA indices and a bare ``m`` to
    the ETCCDI bootstrapping ones. Unifying them breaks one or the other."""
    eca = {p.name: p for p in OPERATOR_SCHEMA["eca_rx1day"].params}["freq"]
    assert eca.choices == ("freq=year", "freq=month")

    boot = {p.name: p for p in OPERATOR_SCHEMA["etccdi_tx90p"].params}["freq"]
    assert boot.choices == ("m",)


@pytest.mark.parametrize("name", INDICES)
def test_every_declared_parameter_reaches_the_syntax_hint(name):
    syntax = operator_syntax(name)
    for param in OPERATOR_SCHEMA[name].params:
        assert param.name in syntax, f"{name}: {param.name} missing from {syntax!r}"


# --- input slots ----------------------------------------------------------

@pytest.mark.parametrize("name", [n for n in INDICES if n not in NOT_AN_INDEX])
def test_every_index_declares_what_its_inputs_hold(name):
    spec = OPERATOR_SCHEMA[name]
    slots = operator_inputs(name)
    assert len(slots) == max(spec.nin, 0)
    for index, slot in enumerate(slots, start=1):
        assert slot.role, f"{name}: input {index} has no role"
        assert slot.field, f"{name}: input {index} does not say what it holds"
        assert slot.key, f"{name}: input {index} has no key to build it by"


@pytest.mark.parametrize("name", [n for n in INDICES if n not in NOT_AN_INDEX])
def test_extra_inputs_say_how_to_get_them(name):
    """Every slot past the first is either derivable or explained.

    A recipe is the useful answer. Where there is none, the slot has to be one
    of the two the documentation says is not a climatology — ``eca_gsl``'s
    land-water mask and ``eca_etr``'s second raw series — because "input 2, no
    idea" is the state this declaration exists to end.
    """
    for index, slot in enumerate(operator_inputs(name), start=1):
        if index == 1:
            continue
        assert slot.recipe or slot.key in {"landmask", "tn"}, (
            f"{name}: input {index} has neither a recipe nor a known exception")


def test_the_documented_non_climatologies_are_declared_as_such():
    """The two cases where reaching for ydrunpctl would be wrong."""
    assert operator_inputs("eca_gsl")[1].key == "landmask"
    assert not operator_inputs("eca_gsl")[1].recipe
    assert operator_inputs("eca_etr")[1].key == "tn"
    assert not operator_inputs("eca_etr")[1].recipe


def test_slots_wanting_the_same_field_share_a_key():
    """``eca_r75p`` and ``eca_r75ptot`` must not be handed two different files."""
    assert operator_inputs("eca_r75p")[1].key == operator_inputs("eca_r75ptot")[1].key
    assert operator_inputs("eca_cwfi")[1].key == operator_inputs("etccdi_csdi")[1].key
    assert operator_inputs("eca_hwfi")[1].key == operator_inputs("etccdi_wsdi")[1].key
    # …and two that want *different* percentiles must not.
    assert operator_inputs("eca_cwfi")[1].key != operator_inputs("eca_hwfi")[1].key


def test_the_bootstrap_indices_take_their_own_running_extremes():
    """The doc's example is ``-ydrunmin,5 txfile -ydrunmax,5 txfile``."""
    slots = operator_inputs("etccdi_tx90p")
    assert len(slots) == 3
    assert "ydrunmin" in slots[1].recipe
    assert "ydrunmax" in slots[2].recipe
    # The window follows the operator's own n rather than being frozen at 5.
    assert format_recipe(slots[1].recipe, in1="tx.nc", n="7") == "cdo ydrunmin,7 tx.nc"


def test_every_declared_unit_family_exists():
    for name in INDICES:
        for slot in operator_inputs(name):
            assert slot.units in UNIT_FAMILIES or slot.units == "", (
                f"{name}: unknown unit family {slot.units!r}")


# --- units ----------------------------------------------------------------

def test_temperature_indices_expect_kelvin():
    """The trap the documentation is explicit about: field in K, threshold in °C."""
    for name in ("eca_su", "eca_tr", "eca_csu", "eca_fd", "eca_id", "eca_hd",
                 "eca_gsl"):
        assert operator_inputs(name)[0].units == "kelvin", name


def test_precipitation_indices_expect_an_amount():
    for name in ("eca_pd", "eca_cdd", "eca_rr1", "eca_sdii", "eca_rx1day"):
        assert operator_inputs(name)[0].units == "precip", name


def test_two_input_temperature_indices_want_matching_units():
    """The docs require both files in the same units rather than a fixed one."""
    for name in ("eca_cwdi", "eca_cwfi", "eca_hwdi", "eca_hwfi", "eca_etr",
                 "eca_tg10p", "eca_tn90p", "eca_tx10p"):
        assert operator_inputs(name)[1].units == "same_as_input1", name


@pytest.mark.parametrize("written,expected", [
    ("K", "k"), ("Kelvin", "kelvin"), ("kg m**-2", "kgm-2"),
    ("kg m-2 s-1", "kgm-2s-1"), ("degrees_C", "degreesc"), ("  mm  ", "mm"),
])
def test_units_normalisation(written, expected):
    assert units.normalise(written) == expected


def test_a_file_with_no_units_is_never_reported_as_wrong(tmp_path):
    """Unverifiable is not the same as wrong, and plenty of output has neither."""
    assert units.read_units(str(tmp_path / "absent.nc")) is None
    assert units.check_inputs("eca_su", [str(tmp_path / "absent.nc")]) == []


def test_an_undeclared_operator_is_never_warned_about():
    assert units.check_inputs("timmean", ["whatever.nc"]) == []


# --- against the binary ---------------------------------------------------

@pytest.fixture(scope="module")
def climate_samples(tmp_path_factory):
    """A daily TG/TN/TX series in Kelvin and an RR series in mm.

    Small on purpose — the point is the units and the shape of the arguments,
    not the size of the field.
    """
    directory = tmp_path_factory.mktemp("climate")
    daily = "-settaxis,2000-01-01,00:00:00,1day -duplicate,400"
    plan = (
        ("tg", f"-setunit,K -setname,tg -addc,273.15 -mulc,40 -subc,0.5 "
               f"{daily} -random,r6x3,11"),
        ("tn", "-setunit,K -setname,tn -subc,5 {tg}"),
        ("tx", "-setunit,K -setname,tx -addc,5 {tg}"),
        ("rr", f"-setunit,mm -setname,rr -mulc,30 {daily} -random,r6x3,12"),
    )
    built = {}
    for key, expression in plan:
        target = directory / f"{key}.nc"
        command = expression.format(tg=str(built.get("tg", "")))
        subprocess.run(["cdo", "-s", "-O", "-f", "nc", *command.split(),
                        str(target)], check=True, capture_output=True)
        built[key] = target
    return built


@cdo_required
@pytest.mark.parametrize("name", [
    "eca_cwdi", "eca_hwdi", "eca_cwfi", "eca_hwfi", "etccdi_csdi",
    "etccdi_wsdi", "eca_tg10p", "eca_tg90p", "eca_tn10p", "eca_tx90p",
    "eca_r75p", "eca_r95ptot", "etccdi_tx90p", "etccdi_r95p",
])
def test_the_declared_recipe_builds_a_file_the_operator_accepts(
        name, climate_samples, tmp_path):
    """End to end: build each extra input from its own recipe, then run.

    This is the check that would have caught the bug the declaration exists for.
    A recipe that produces a file CDO rejects is a wrong recipe, and a recipe
    nobody runs is a comment.
    """
    slots = operator_inputs(name)
    primary = climate_samples[slots[0].key]
    paths = [str(primary)]

    for index, slot in enumerate(slots[1:], start=2):
        assert slot.recipe, f"{name}: input {index} has no recipe to test"
        target = tmp_path / f"{name}_{slot.key}.nc"
        command = slot.recipe.format(in1=str(primary), n="5")
        subprocess.run(["cdo", "-s", "-O", "-f", "nc", *command.split(),
                        str(target)], check=True, capture_output=True)
        paths.append(str(target))

    # Arguments in the shape the GUI would send: the required ones filled, the
    # rest blank. An enumerated parameter takes its first declared choice,
    # which is what the combo box offers and what keeps the two ``freq``
    # grammars apart without this test having to know about them.
    spec = OPERATOR_SCHEMA[name]
    values = {"n": "5", "startboot": "2000", "endboot": "2000"}

    def value_for(param):
        if param.choices:
            return param.choices[0]
        return values.get(param.name, "")

    arguments = [value_for(p) for p in spec.params]
    while arguments and arguments[-1] == "":
        arguments.pop()
    token = name + ("," + ",".join(arguments) if arguments else "")

    result = subprocess.run(
        ["cdo", "-s", "-O", token, *paths, str(tmp_path / f"{name}_out.nc")],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120,
    )
    assert result.returncode == 0, (
        f"cdo {token} rejected its declared inputs: {result.stderr.strip()}")


@cdo_required
def test_the_units_check_catches_a_celsius_field(climate_samples, tmp_path):
    """The failure mode is a finished file full of wrong counts, not an error."""
    celsius = tmp_path / "tx_celsius.nc"
    subprocess.run(["cdo", "-s", "-O", "-f", "nc", "-setunit,degC", "-subc,273.15",
                    str(climate_samples["tx"]), str(celsius)],
                   check=True, capture_output=True)

    assert units.check_inputs("eca_su", [str(climate_samples["tx"])]) == []

    warnings = units.check_inputs("eca_su", [str(celsius)])
    assert len(warnings) == 1
    assert "Kelvin" in warnings[0].message
    assert "273.15" in warnings[0].message

    # CDO itself is perfectly happy with it, which is the whole point.
    ran = subprocess.run(["cdo", "-s", "-O", "eca_su", str(celsius),
                          str(tmp_path / "out.nc")], capture_output=True)
    assert ran.returncode == 0


@cdo_required
def test_the_units_check_catches_a_precipitation_rate(climate_samples, tmp_path):
    rate = tmp_path / "rr_rate.nc"
    subprocess.run(["cdo", "-s", "-O", "-f", "nc", "-setunit,kg m-2 s-1",
                    "-divc,86400", str(climate_samples["rr"]), str(rate)],
                   check=True, capture_output=True)

    warnings = units.check_inputs("eca_pd", [str(rate)])
    assert len(warnings) == 1
    assert "86400" in warnings[0].message


@cdo_required
def test_mismatched_two_input_units_are_reported(climate_samples):
    """``eca_cwfi`` compares its two files directly and converts neither."""
    warnings = units.check_inputs(
        "eca_cwfi", [str(climate_samples["tg"]), str(climate_samples["rr"])])
    assert len(warnings) == 1
    assert warnings[0].slot == 2


# --- output semantics -----------------------------------------------------

@cdo_required
@pytest.mark.parametrize("name,expected", [
    ("eca_cdd", 2), ("eca_cwd", 2), ("eca_cfd", 2), ("eca_csu", 2),
    ("eca_rx5day", 2), ("eca_su", 1), ("eca_fd", 1), ("eca_rx1day", 1),
])
def test_the_two_variable_indices_really_write_two(name, expected,
                                                   climate_samples, tmp_path):
    """The claim in the description, checked against the file CDO writes."""
    source = climate_samples[operator_inputs(name)[0].key]
    target = tmp_path / f"{name}.nc"
    subprocess.run(["cdo", "-s", "-O", name, str(source), str(target)],
                   check=True, capture_output=True)
    assert len(units.data_variables(str(target))) == expected


def test_the_description_says_which_indices_write_two_variables():
    for name in ("eca_cdd", "eca_cwd", "eca_cfd", "eca_csu", "eca_rx5day",
                 "eca_cwfi", "etccdi_wsdi"):
        assert "two variables" in OPERATOR_SCHEMA[name].description, name


def test_the_description_distinguishes_eca_from_etccdi():
    """Somebody choosing between the pair can see why the numbers differ."""
    assert "last timestep" in OPERATOR_SCHEMA["eca_cdd"].description
    assert "yearly by default" in OPERATOR_SCHEMA["etccdi_cdd"].description
    assert "middle of its interval" in OPERATOR_SCHEMA["etccdi_cdd"].description


def test_the_indices_that_break_their_family_say_so():
    """Measured exceptions, not inferred ones — see _SURPRISING_DEFAULTS."""
    assert "one value per input timestep" in OPERATOR_SCHEMA["etccdi_hd"].description
    assert "freq=month" in OPERATOR_SCHEMA["etccdi_rx1daymon"].description
    assert "yearly" not in OPERATOR_SCHEMA["etccdi_sdii"].description.split(
        "Whole-series")[0]
