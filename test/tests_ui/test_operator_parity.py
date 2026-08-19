# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""The three operator surfaces offer the same operators, and only real ones.

The toolbar menus, the command palette (Ctrl+K) and the model builder palette
each answered "which operators exist?" from a different place. The menus read a
hand-written dict of 386; the other two read ``cdo --operators`` at run time,
which on CDO 2.6.0 is 943. Browsing therefore reached 561 fewer operators than
searching, and the dict listed six operators no installed CDO has — clicking
one opened a form that could only fail with "Unknown or unavailable operator".

These tests pin the fix from both ends: every operator a menu offers must be
one the installed binary can actually run, and every operator the builder
offers must be reachable by browsing too.
"""

import shutil

import pytest

from ncexplorer_toolkit.core.categories import (
    NCExplorerCategory, OBASE_OPERATORS, OPERATOR_CATEGORIES, OPERATOR_SCHEMA,
    menu_operators, operator_syntax,
)
from ncexplorer_toolkit.core.model import OperatorCatalog
from ncexplorer_toolkit.core.nc_integration import create_NCExplorer_integration
from ncexplorer_toolkit.gui.command_palette import build_entries
from ncexplorer_toolkit.gui.toolbar import TOP_LEVEL_LIMIT, NCExplorerToolbar

cdo_required = pytest.mark.skipif(
    shutil.which("cdo") is None, reason="needs an installed CDO")


@pytest.fixture(scope="module")
def integration():
    """The real integration, so the catalog is the installed binary's."""
    return create_NCExplorer_integration()


@pytest.fixture
def toolbar(qapp, integration):
    """A toolbar built against the installed CDO.

    ``QToolBar`` needs a real widget parent, and the toolbar reads its
    ``NCExplorer`` attribute for the runtime catalog, so the stand-in has to be
    a widget rather than a plain object.
    """
    from PyQt6.QtWidgets import QMainWindow

    class Host(QMainWindow):
        NCExplorer = integration

    return NCExplorerToolbar(Host())


def menu_operator_names(menu):
    """Every operator reachable from ``menu``, submenus included."""
    names = []
    for action in menu.actions():
        if action.isSeparator():
            continue
        submenu = action.menu()
        if submenu is not None:
            names.extend(menu_operator_names(submenu))
        else:
            # ``data()`` is the operator name; ``text()`` is the caption,
            # which carries a "(needs MAGICS support)" suffix for an
            # operator this CDO cannot run. Identity, not presentation.
            names.append(action.data() or action.text())
    return names


# --- the split ------------------------------------------------------------

def test_curated_and_rest_partition_the_category():
    """Together they are the category; separately they never overlap."""
    for category in NCExplorerCategory:
        curated, rest = menu_operators(category)
        assert not set(curated) & set(rest)
        assert set(curated) | set(rest) == {
            name for name, spec in OPERATOR_SCHEMA.items()
            if spec.category is category
        }


def test_every_category_is_covered_exactly_once():
    """No operator is missing from the menus and none is listed twice."""
    seen = []
    for category in NCExplorerCategory:
        curated, rest = menu_operators(category)
        seen.extend(curated + rest)

    assert len(seen) == len(set(seen))
    assert set(seen) == set(OPERATOR_SCHEMA)


def test_available_filter_drops_unknown_operators():
    """An operator the installed build lacks is offered by no menu."""
    trimmed = set(OPERATOR_SCHEMA) - {"timmean", "ensmean"}
    offered = set()
    for category in NCExplorerCategory:
        curated, rest = menu_operators(category, trimmed)
        offered |= set(curated) | set(rest)

    assert "timmean" not in offered
    assert "ensmean" not in offered
    assert offered == trimmed


def test_curated_operators_are_all_real():
    """The hand-written lists carry no operator the catalog lacks.

    ``gradsdes1``, ``gradsdes2``, ``interpolate``, ``pardes``, ``setgatt`` and
    ``setgatts`` used to fail this: they are in no CDO 2.6.0 build.
    """
    curated = {op for ops in OPERATOR_CATEGORIES.values() for op in ops}
    assert curated <= set(OPERATOR_SCHEMA)


# --- parity across the three surfaces -------------------------------------

@cdo_required
def test_menus_offer_exactly_what_the_builder_does(toolbar, integration):
    """Browsing and searching reach the same operators."""
    from_menus = set()
    for menu in toolbar.category_menus.values():
        from_menus |= set(menu_operator_names(menu))

    from_palette = {entry.name for entry in build_entries(integration)}
    from_builder = set(OperatorCatalog.from_integration(integration).names())

    assert from_menus == from_palette
    assert from_menus == from_builder


@cdo_required
def test_no_menu_offers_an_unavailable_operator(toolbar, integration):
    """Every menu entry survives the check that raises on an unknown operator."""
    signatures = integration.get_operator_signatures()
    for menu in toolbar.category_menus.values():
        for name in menu_operator_names(menu):
            assert name in signatures, f"{name} is offered but not installed"


@cdo_required
def test_no_operator_is_offered_by_two_categories(toolbar):
    """One operator, one category.

    Within a category an operator may appear twice — the ten a menu opens on
    are repeated inside its complete "All …" listing, so that listing has no
    holes in the alphabet. Across categories a repeat would mean the schema
    filed one name under two headings.
    """
    seen = {}
    for category, menu in toolbar.category_menus.items():
        for name in set(menu_operator_names(menu)):
            assert name not in seen, f"{name} is in both {seen.get(name)} and {category}"
            seen[name] = category


@cdo_required
def test_the_first_ten_lead_every_menu(toolbar):
    """A menu opens on ten operators, curated ones first.

    Statistical values curates 135 operators; a menu that listed them all is a
    menu with scroll arrows. The head of the curated list leads instead, and
    the rest of the category stays one click away under "All …".
    """
    for category, menu in toolbar.category_menus.items():
        top_level = [
            (action.data() or action.text()) for action in menu.actions()
            if not action.isSeparator() and action.menu() is None
        ]
        curated, rest = menu_operators(category, set(OPERATOR_SCHEMA))

        assert len(top_level) <= TOP_LEVEL_LIMIT
        assert top_level == (curated + rest)[:TOP_LEVEL_LIMIT]


@cdo_required
def test_the_all_submenu_holds_the_whole_category(toolbar):
    """"All …" means all of it, not just the part that did not fit."""
    for category, menu in toolbar.category_menus.items():
        curated, rest = menu_operators(category, set(OPERATOR_SCHEMA))
        whole = set(curated) | set(rest)
        if len(whole) <= TOP_LEVEL_LIMIT:
            continue

        submenus = [a.menu() for a in menu.actions() if a.menu() is not None]
        assert len(submenus) == 1
        assert set(menu_operator_names(submenus[0])) == whole


# --- the derived syntax hint ----------------------------------------------

@pytest.mark.parametrize("name,expected", [
    # The ordinary shape. This used to be ``timmean``, which is no longer
    # parameterless: Timstat takes ``complete_only`` and the case below pins
    # that. ``ymonmean`` is the replacement because Ymonstat genuinely takes
    # nothing — ``cdo ymonmean,complete_only=TRUE`` is "Too many arguments!
    # Need 0 found 1" on 2.6.3, which is what makes it a stable stand-in.
    ("ymonmean", "ifile ofile"),
    # The five modules that do take it, one representative. Keyword, and
    # spelled out here because the usage line is the only place a user learns
    # that ``timmean,TRUE`` is a parse error.
    ("timmean", "ifile ofile [,complete_only=true]"),
    ("copy", "ifiles ofile"),            # n inputs — the old table said "ifile ofile"
    ("info", "ifiles"),                  # n inputs, no output file
    ("diff", "ifile1 ifile2"),           # two inputs, prints to stdout
    # Writes a family of files from a prefix, and takes the one parameter the
    # Splittime module documents. It reads as a whole-module parameter and is
    # not one: `cdo splithour,%Y infile obase` is "Too many arguments! Need 0
    # found 1." on 2.6.3, and splitmon is the only operator of the six that
    # accepts it. Positional, so no `format=` here — see OperatorParam.form.
    ("splitmon", "ifile obase [,format]"),
    # The old table said one input; the declaration was also a parameter short
    # of `cdo -h eca_gsl`, which is `[,nday[,T[,fland]]]`.
    ("eca_gsl", "ifile1 ifile2 ofile [,nday][,T][,fland]"),
    ("selname", "ifile ofile vars"),     # a required parameter
    ("const", "ofile const,grid"),       # no input at all
])
def test_operator_syntax_shapes(name, expected):
    assert operator_syntax(name) == expected


def test_operator_syntax_matches_every_signature():
    """The hint's file tokens agree with the installed arity, for all of them."""
    for name, spec in OPERATOR_SCHEMA.items():
        syntax = operator_syntax(name)
        tokens = syntax.split()

        if spec.nin == -1:
            assert "ifiles" in tokens, name
        elif spec.nin == 1:
            assert "ifile" in tokens, name
        elif spec.nin > 1:
            assert f"ifile{spec.nin}" in tokens, name
            assert f"ifile{spec.nin + 1}" not in tokens, name

        if spec.nout == -1:
            assert "obase" in tokens, name
        elif spec.nout == 1:
            # ``nout == 1`` is an argument count, not a promise that the
            # argument is a file. The six Magics plot operators are (n|1) and
            # their trailing argument is an *obase* in CDO's own synopsis —
            # ``cdo shaded,parameter infile obase`` — so the hint says obase for
            # them, which is the whole point of the change that added them.
            expected = "obase" if name in OBASE_OPERATORS else "ofile"
            assert expected in tokens, name
        elif spec.nout > 1:
            assert f"ofile{spec.nout}" in tokens, name


def test_operator_syntax_names_every_parameter():
    """A parameter with a field in the form is a parameter named in the hint."""
    for name, spec in OPERATOR_SCHEMA.items():
        syntax = operator_syntax(name)
        for param in spec.params:
            assert param.name in syntax, f"{name}: {param.name} missing from {syntax!r}"


def test_operator_syntax_falls_back_for_unknown_operators():
    assert operator_syntax("no_such_operator") == "ifile ofile"
