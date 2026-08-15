"""Additional tile sources for the basemap selector, beyond the original set.

Every source here needs no API key and no account, so a missing or renamed one
degrades the way the existing providers do — absent from the selector rather
than broken in it.

Per-day NASA GIBS imagery, bound to the loaded dataset's timestep, was built
here and then removed. Worth knowing before rebuilding it: a single day's tiles
carry that satellite's own orbital swath gaps and its unlit polar regions, so
the backdrop is striped and part-black rather than a clean globe. The undated
NASA composites below (Blue Marble, Night Lights) are mosaics and have neither.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Undated additions. Values are attribute paths into ``xyzservices.providers``.
STATIC_SOURCES: dict[str, tuple] = {
    "NASA Blue Marble": ("NASAGIBS", "BlueMarble"),
    "NASA Night Lights": ("NASAGIBS", "ViirsEarthAtNight2012"),
    "Terrain (Esri)": ("Esri", "WorldTerrain"),
    "Shaded relief (Esri)": ("Esri", "WorldShadedRelief"),
    "Topographic (Esri)": ("Esri", "WorldTopoMap"),
    "National Geographic": ("Esri", "NatGeoWorldMap"),
    "Carto Voyager": ("CartoDB", "Voyager"),
}

#: Sentinel-2 cloudless is not bundled by xyzservices, so its provider is built
#: by hand. The year is the mosaic vintage, not a queryable date — EOX composites
#: a whole year into one cloud-free layer, so there is nothing per-day to select.
S2_CLOUDLESS_YEAR = "2024"
S2_CLOUDLESS_LABEL = "Satellite (Sentinel-2)"


def sentinel2_cloudless():
    """The EOX Sentinel-2 cloudless provider, or None if xyzservices is absent."""
    try:
        from xyzservices import TileProvider
    except ImportError:
        return None

    return TileProvider(
        name=f"EOX.S2Cloudless{S2_CLOUDLESS_YEAR}",
        url="https://tiles.maps.eox.at/wmts/1.0.0/"
            f"s2cloudless-{S2_CLOUDLESS_YEAR}_3857/default/g/" + "{z}/{y}/{x}.jpg",
        attribution=(
            f"Sentinel-2 cloudless {S2_CLOUDLESS_YEAR} by EOX IT Services GmbH "
            "(contains modified Copernicus Sentinel data)"
        ),
        max_zoom=14,
    )


def resolve(path):
    """Walk an attribute path into ``xyzservices.providers``, or None.

    Providers are renamed and dropped between xyzservices releases, so a missing
    one has to be survivable: the caller drops that single entry rather than
    losing the whole selector.
    """
    try:
        import xyzservices

        node = xyzservices.providers
        for part in path:
            node = getattr(node, part)
        return node
    except Exception as exc:
        logger.warning("Tile provider %s unavailable: %s", ".".join(path), exc)
        return None
