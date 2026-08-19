# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""Category icons: mapped, present on disk, and actually rendering pixels."""

from pathlib import Path

import pytest

from ncexplorer_toolkit.core.categories import NCExplorerCategory
from ncexplorer_toolkit.resources import icons


def test_every_category_is_mapped():
    assert set(icons.CATEGORY_ICONS) == set(NCExplorerCategory)


@pytest.mark.parametrize("category", list(NCExplorerCategory), ids=lambda c: c.name)
def test_mapped_file_exists(category):
    assert icons.icon_path(icons.CATEGORY_ICONS[category]).is_file()


def test_icon_filenames_are_unique():
    filenames = list(icons.CATEGORY_ICONS.values())
    assert len(set(filenames)) == len(filenames)


@pytest.mark.parametrize("category", list(NCExplorerCategory), ids=lambda c: c.name)
def test_icon_renders_visible_pixels(qapp, category):
    """Catches the silent failure mode: an icon that loads but draws nothing."""
    icons.clear_icon_cache()
    icon = icons.category_icon(category)
    assert not icon.isNull()

    pixmap = icon.pixmap(24, 24)
    assert not pixmap.isNull()

    image = pixmap.toImage()
    painted = sum(
        1
        for y in range(image.height())
        for x in range(image.width())
        if image.pixelColor(x, y).alpha() > 0
    )
    assert painted > 0, f"{category.name} rendered fully transparent"


def test_icon_path_resolves_in_source_tree():
    path = icons.icon_path("information.svg")
    assert path.is_absolute()
    assert path.parent == Path(icons.__file__).resolve().parent / "icons"


def test_unmapped_category_raises(qapp):
    class Fake:
        name = "NOT_A_CATEGORY"

    with pytest.raises(KeyError):
        icons.category_icon(Fake())


def test_icons_are_cached(qapp):
    icons.clear_icon_cache()
    first = icons.category_icon(NCExplorerCategory.INFORMATION)
    second = icons.category_icon(NCExplorerCategory.INFORMATION)
    assert first is second
