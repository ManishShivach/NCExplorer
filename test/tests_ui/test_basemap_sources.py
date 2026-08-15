"""The added basemap providers resolve, need no key, and all reach the selector.

Nothing here touches the network. What is worth pinning is that each source can
still be found in whatever xyzservices version is installed — providers get
renamed and dropped between releases — and that nothing in the dropdown is a
dead entry.
"""

from ncexplorer_toolkit.geocanvas.basemap_sources import (
    S2_CLOUDLESS_LABEL, STATIC_SOURCES, resolve, sentinel2_cloudless,
)


def test_every_static_source_resolves():
    for label, path in STATIC_SOURCES.items():
        assert resolve(path) is not None, label


def test_resolve_returns_none_for_a_missing_provider():
    """A renamed provider costs its own entry, not the whole selector."""
    assert resolve(("NoSuchFamily", "NoSuchLayer")) is None


def test_sentinel2_provider_is_built_and_keyless():
    provider = sentinel2_cloudless()
    assert provider is not None
    assert not provider.requires_token()
    assert "{z}" in provider["url"] and "{x}" in provider["url"]


def test_no_new_source_needs_an_api_key():
    """Every addition has to degrade the way the original six do."""
    providers = [resolve(path) for path in STATIC_SOURCES.values()]
    providers.append(sentinel2_cloudless())
    for provider in providers:
        assert provider is not None
        assert not provider.requires_token()


def test_every_source_carries_attribution():
    """Tile terms require it, and the canvas draws whatever is here."""
    providers = [resolve(path) for path in STATIC_SOURCES.values()]
    providers.append(sentinel2_cloudless())
    for provider in providers:
        assert provider.get("attribution")


def test_selector_offers_a_provider_for_every_choice(canvas):
    """Nothing in the dropdown can be a dead entry."""
    from ncexplorer_toolkit.gui.main_window import NCExplorerOperatorGUI

    available = canvas._get_basemap_providers()
    for label in NCExplorerOperatorGUI.BASEMAP_CHOICES:
        if label in ("None", canvas.OFFLINE_NATURAL_EARTH):
            continue
        assert label in available, f"{label} is offered but has no provider"


def test_added_labels_are_all_selectable():
    """A source registered but absent from the dropdown is unreachable."""
    from ncexplorer_toolkit.gui.main_window import NCExplorerOperatorGUI

    choices = set(NCExplorerOperatorGUI.BASEMAP_CHOICES)
    for label in list(STATIC_SOURCES) + [S2_CLOUDLESS_LABEL]:
        assert label in choices, f"{label} has a provider but is not offered"
