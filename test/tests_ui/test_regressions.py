"""Guards on behaviour that existed before the navigation/icon work."""

import pytest
from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QResizeEvent
from PyQt6.QtWidgets import QApplication, QLabel, QToolButton

from ncexplorer_toolkit.core.categories import (
    NCExplorerCategory, OPERATOR_SCHEMA, menu_operators)
from ncexplorer_toolkit.gui.command_palette import rank_entries
from ncexplorer_toolkit.gui.toolbar import TOP_LEVEL_LIMIT


@pytest.fixture(scope="module")
def main_window(qapp):
    from ncexplorer_toolkit.gui.main_window import NCExplorerOperatorGUI

    window = NCExplorerOperatorGUI()
    yield window
    window.close()


def test_main_window_constructs(main_window):
    assert main_window.geo_canvas is not None
    assert main_window.nav_overlay is not None


def test_basemap_selector_intact(main_window):
    combo = main_window.basemap_combo
    items = [combo.itemText(i) for i in range(combo.count())]
    # The declared choices come first and the MBTiles chooser stays last; any
    # entries in between are archives discovered on this machine, so an exact
    # count would depend on what happens to be in ~/.ncexplorer/basemaps.
    assert items[:len(main_window.BASEMAP_CHOICES)] == main_window.BASEMAP_CHOICES
    assert items[-1] == main_window.LOAD_MBTILES_ITEM
    # Still wired to the canvas (the in-progress basemap feature).
    assert combo.receivers(combo.currentTextChanged) >= 1


def test_basemap_canvas_api_intact(main_window):
    for name in (
        "set_basemap",
        "_get_basemap_providers",
        "_on_extent_changed_basemap",
        "_request_basemap_refresh",
    ):
        assert hasattr(main_window.geo_canvas, name)


def test_toolbar_exposes_every_category(main_window):
    """Every category has a menu that opens on ten operators.

    This used to assert the menu held exactly the hand-written curated list.
    Statistical values curates 135 of those, which is not a menu anyone reads,
    so a menu now opens on the first ten and keeps the rest behind "All …".
    Parity between the menus, the palette and the model builder is covered in
    ``test_operator_parity.py``.
    """
    toolbar = main_window.toolbar
    # Seventeen since the Graphic with Magics section was given a category of
    # its own, the EOFs section having been the sixteenth and Correlation the
    # fifteenth; the assertion is that the toolbar has a menu for *every*
    # category, so the literal is here to catch a category added without a menu
    # rather than to pin the number.
    assert len(toolbar.category_menus) == len(NCExplorerCategory) == 17

    for category, menu in toolbar.category_menus.items():
        top_level = [
            # The operator name, not the caption — see toolbar._add_operator_actions.
            (action.data() or action.text()) for action in menu.actions()
            if not action.isSeparator() and action.menu() is None
        ]
        curated, rest = menu_operators(category, set(OPERATOR_SCHEMA))
        assert top_level == (curated + rest)[:TOP_LEVEL_LIMIT]
        assert len(top_level) <= TOP_LEVEL_LIMIT


def test_large_categories_offer_an_all_submenu(main_window):
    """Anything past the tenth operator is still reachable by browsing."""
    toolbar = main_window.toolbar

    for category, menu in toolbar.category_menus.items():
        curated, rest = menu_operators(category, set(OPERATOR_SCHEMA))
        submenus = [a.menu() for a in menu.actions() if a.menu() is not None]

        if len(curated) + len(rest) <= TOP_LEVEL_LIMIT:
            assert not submenus, f"{category.value} needs no All submenu"
            continue

        assert len(submenus) == 1, f"{category.value} should have exactly one All submenu"
        assert "All" in submenus[0].title()
        assert str(len(curated) + len(rest)) in submenus[0].title()


def test_category_buttons_are_icon_only_with_a_tooltip(main_window):
    """The label moved into the tooltip; the glyph is the button.

    Fourteen labelled buttons left no room on the toolbar for the basemap
    selector or the operator search, so the name is now shown on hover. Fifteen
    since Correlation became a category, sixteen since EOFs did, seventeen since
    Graphics did — which is the argument holding rather than weakening.
    """
    buttons = [
        b for b in main_window.toolbar.findChildren(QToolButton) if b.menu() is not None
    ]
    assert len(buttons) == len(NCExplorerCategory) == 17

    tooltips = {b.toolTip() for b in buttons}
    assert tooltips == {category.value for category in NCExplorerCategory}

    for button in buttons:
        assert not button.icon().isNull()
        assert button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly


def test_toolbar_order_puts_basemap_first(main_window):
    """Basemap, then Batch, then the operator search, then the categories."""
    toolbar = main_window.toolbar
    widgets = [
        toolbar.widgetForAction(action) for action in toolbar.actions()
        if not action.isSeparator()
    ]

    assert widgets[0] is not None and isinstance(widgets[0], QLabel)
    assert widgets[0].text().strip() == "Basemap:"
    assert widgets[1] is main_window.basemap_combo
    assert toolbar.actions().index(toolbar.batch_action) < \
        toolbar.actions().index(toolbar.search_action)

    first_category = min(
        toolbar.actions().index(action) for action in toolbar.actions()
        if isinstance(toolbar.widgetForAction(action), QToolButton)
        and toolbar.widgetForAction(action).menu() is not None
    )
    assert toolbar.actions().index(toolbar.search_action) < first_category


def test_toolbar_search_opens_the_operator_form(main_window):
    """Typing a name and pressing Enter opens that operator's parameters."""
    search = main_window.toolbar.search
    search.setText("timmean")
    search.returnPressed.emit()

    assert main_window.current_operator == "timmean"
    # The box clears itself on the next turn of the event loop.
    QApplication.processEvents()
    assert search.text() == ""


def test_toolbar_search_is_fuzzy(main_window):
    """The toolbar box searches like the command palette, not like a substring.

    ``timmean`` contains no "tmn", so a substring filter would find nothing.
    """
    index = main_window.toolbar.search.index()
    assert "timmean" in [m.entry.name for m in rank_entries(index, "tmn", limit=200)]
    # Descriptions are searched too, which is the other half of what a
    # substring filter cannot do.
    assert "fldmean" in [m.entry.name for m in rank_entries(index, "field mean", limit=200)]


def test_canvas_takes_keyboard_focus(main_window):
    assert main_window.geo_canvas.focusPolicy() == Qt.FocusPolicy.StrongFocus


def test_resize_event_still_refits_the_extent(main_window):
    """The basemap aspect fix in resizeEvent must survive the overlay hook."""
    canvas = main_window.geo_canvas
    old = canvas.size()
    canvas.resize(600, 600)
    canvas.resizeEvent(QResizeEvent(QSize(600, 600), old))

    assert canvas.aspect_ratio == pytest.approx(1.0)
    extent = canvas.extent
    assert extent[1] - extent[0] == pytest.approx(extent[3] - extent[2])
