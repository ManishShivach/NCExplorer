"""Colormap registry — the single source of truth for colormap choices.

Everything that offers the user a colormap (the style tab's combo box, the
symbology manager) reads its list from here, so the app can never advertise a
name that matplotlib cannot resolve.

Two optional packages widen the catalogue and are imported defensively:

* ``cmocean`` — perceptually uniform maps designed for ocean/climate fields.
* ``cmcrameri`` — Fabio Crameri's scientific maps, including colour-vision-
  deficiency-safe diverging pairs.

Both self-register with matplotlib on import (under the ``cmo.`` and ``cmc.``
prefixes), so importing this module once is enough for plain string names like
``"cmo.balance"`` to work anywhere in the app. If either package is missing its
group is simply omitted — NCExplorer must start and run normally without them.
"""

import math

import matplotlib

# Importing these registers their colormaps with matplotlib as a side effect;
# the modules themselves are never referenced again.
try:
    import cmocean  # noqa: F401
    HAS_CMOCEAN = True
except ImportError:
    HAS_CMOCEAN = False

try:
    from cmcrameri import cm as _cmc  # noqa: F401
    HAS_CMCRAMERI = True
except ImportError:
    HAS_CMCRAMERI = False

#: Fallback used whenever a requested colormap cannot be resolved.
DEFAULT_COLORMAP = "viridis"

# Group definitions: (label, required package or None, candidate names).
#
# The names are *candidates*. available_colormaps() drops any that matplotlib
# cannot resolve, so a name a given matplotlib version does not ship (e.g.
# 'berlin', added in 3.10) disappears instead of breaking the combo box. Groups
# naming a package are also gated on that package having imported — matplotlib's
# registry is global and stays populated once cmocean has been imported, so the
# import flag is the only honest signal of whether the package is really there.
_GROUPS: tuple[tuple[str, str | None, tuple[str, ...]], ...] = (
    ("Perceptually Uniform", None, (
        "viridis", "plasma", "inferno", "magma", "cividis",
    )),
    ("Sequential", None, (
        "Blues", "BuGn", "BuPu", "GnBu", "Greens", "Greys", "Oranges", "OrRd",
        "PuBu", "PuBuGn", "PuRd", "Purples", "RdPu", "Reds", "YlGn", "YlGnBu",
        "YlOrBr", "YlOrRd", "afmhot", "bone", "copper", "gist_earth", "gist_heat",
        "hot", "pink", "terrain", "turbo",
    )),
    ("Diverging", None, (
        "BrBG", "PiYG", "PRGn", "PuOr", "RdBu", "RdGy", "RdYlBu", "RdYlGn",
        "Spectral", "berlin", "bwr", "coolwarm", "managua", "seismic", "vanimo",
    )),
    ("Cyclic", None, (
        "twilight", "twilight_shifted", "hsv",
    )),
    ("Oceanography (cmocean)", "cmocean", (
        "cmo.thermal", "cmo.haline", "cmo.solar", "cmo.ice", "cmo.gray",
        "cmo.oxy", "cmo.deep", "cmo.dense", "cmo.algae", "cmo.matter",
        "cmo.turbid", "cmo.speed", "cmo.amp", "cmo.tempo", "cmo.rain",
        "cmo.balance", "cmo.delta", "cmo.curl", "cmo.diff", "cmo.tarn",
        "cmo.phase", "cmo.topo",
    )),
    ("Scientific (cmcrameri)", "cmcrameri", (
        "cmc.batlow", "cmc.batlowW", "cmc.batlowK", "cmc.devon", "cmc.lajolla",
        "cmc.bamako", "cmc.davos", "cmc.bilbao", "cmc.nuuk", "cmc.oslo",
        "cmc.grayC", "cmc.hawaii", "cmc.lapaz", "cmc.tokyo", "cmc.buda",
        "cmc.acton", "cmc.turku", "cmc.imola",
        "cmc.broc", "cmc.cork", "cmc.vik", "cmc.lisbon", "cmc.tofino",
        "cmc.berlin", "cmc.roma", "cmc.bam", "cmc.vanimo", "cmc.managua",
        "cmc.brocO", "cmc.corkO", "cmc.vikO", "cmc.romaO", "cmc.bamO",
        "cmc.oleron", "cmc.bukavu", "cmc.fes",
    )),
    ("Classic", None, (
        "gray", "jet", "rainbow", "nipy_spectral", "gist_ncar", "gist_rainbow",
        "cool", "spring", "summer", "autumn", "winter", "tab10", "tab20",
        "Set1", "Set2", "Set3", "Pastel1", "Pastel2",
    )),
)

# Base names (prefix and _r stripped) that carry a meaningful midpoint, so
# centring the colour scale on zero is appropriate. Cyclic maps deliberately
# excluded — they have no midpoint, they wrap.
_DIVERGING_BASES = frozenset({
    # matplotlib
    "brbg", "piyg", "prgn", "puor", "rdbu", "rdgy", "rdylbu", "rdylgn",
    "spectral", "berlin", "bwr", "coolwarm", "managua", "seismic", "vanimo",
    # cmocean
    "balance", "delta", "curl", "diff", "tarn", "topo",
    # cmcrameri
    "broc", "cork", "vik", "lisbon", "tofino", "roma", "bam",
    "oleron", "bukavu", "fes",
})


def _registered(name: str) -> bool:
    """True if matplotlib can resolve ``name`` right now."""
    try:
        matplotlib.colormaps[name]
        return True
    except (KeyError, ValueError, TypeError):
        return False


#: Which optional package backs each gated group.
_PACKAGE_FLAGS = {'cmocean': lambda: HAS_CMOCEAN, 'cmcrameri': lambda: HAS_CMCRAMERI}


def available_colormaps() -> dict[str, list[str]]:
    """Grouped colormaps, filtered to those matplotlib can actually resolve.

    Groups whose backing package is missing, or whose entire contents are
    unavailable, are omitted rather than left empty.
    """
    groups: dict[str, list[str]] = {}
    for label, package, names in _GROUPS:
        if package is not None and not _PACKAGE_FLAGS[package]():
            continue
        present = [n for n in names if _registered(n)]
        if present:
            groups[label] = present
    return groups


def flat_colormaps() -> list[str]:
    """Every advertised colormap as a single flat list, groups in order."""
    return [name for names in available_colormaps().values() for name in names]


def base_name(name: str) -> str:
    """Strip the package prefix and any ``_r`` suffix, lowercased.

    ``"cmo.balance_r"`` → ``"balance"``. Used for family lookups that should not
    care about reversal or which package a map came from.
    """
    if not isinstance(name, str):
        return ""
    stem = name.strip()
    if stem.endswith("_r"):
        stem = stem[:-2]
    if "." in stem:
        stem = stem.split(".", 1)[1]
    return stem.lower()


def is_diverging(name: str) -> bool:
    """True if ``name`` is a diverging map, i.e. one with a meaningful centre."""
    return base_name(name) in _DIVERGING_BASES


def resolve_colormap(name: str) -> tuple[str, bool]:
    """Validate a colormap name, falling back to viridis.

    The style combo box is editable, so arbitrary user text reaches this
    function. Returns ``(usable_name, ok)`` — when ``ok`` is False the caller
    should surface a warning (``status_update``) explaining the substitution.
    """
    if isinstance(name, str) and name.strip() and _registered(name.strip()):
        return name.strip(), True
    return DEFAULT_COLORMAP, False


def symmetric_limits(statistics) -> tuple[float, float] | None:
    """Limits centred on zero that still cover a layer's whole value range.

    ``statistics`` is the dict the loaders put in
    ``layer_prop.metadata.statistics``; None is returned when it is missing or
    unusable, so callers can leave the existing scale alone.
    """
    if not statistics:
        return None
    try:
        low = float(statistics['min'])
        high = float(statistics['max'])
    except (KeyError, TypeError, ValueError):
        return None
    if math.isnan(low) or math.isnan(high):
        return None

    reach = max(abs(low), abs(high))
    if not reach > 0:
        return None
    return -reach, reach


def raster_clim(style, statistics) -> tuple | None:
    """Effective ``(vmin, vmax)`` for a raster, or None to leave the scale as is.

    An explicit vmin/vmax set by the user always wins over auto-centring. A
    symmetric ``set_clim`` is used rather than ``TwoSlopeNorm`` because the
    symbology manager already drives the scale through ``set_clim`` — mixing the
    two leaves the artist with conflicting state.
    """
    vmin = getattr(style, 'vmin', None)
    vmax = getattr(style, 'vmax', None)
    if vmin is not None or vmax is not None:
        return vmin, vmax
    if getattr(style, 'diverging_center_zero', False):
        return symmetric_limits(statistics)
    return None


def apply_reverse(name: str, reverse: bool) -> str:
    """Append/strip the matplotlib ``_r`` suffix for a reversed colormap.

    Doubling up (``viridis_r_r``) is not a registered name, so a map that is
    already reversed is returned unchanged.
    """
    if not reverse:
        return name
    return name if name.endswith("_r") else f"{name}_r"
