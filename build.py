# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""
Build script for NCExplorer.

Pipeline (per invocation):
    clean -> pyinstaller -> smoke-test -> sha256 -> (optional) installer

Cross-platform artefact paths:
    Windows : dist/NCExplorer.exe          (one-file)
    Linux   : dist/NCExplorer              (one-file ELF)
    macOS   : dist/NCExplorer.app          (bundle; inner Mach-O hashed)
              dist/NCExplorer-<ver>-macos.dmg  (disk image for distribution)

The macOS build additionally bundles a ``cdo`` binary and its dylib dependency
chain into ``NCExplorer.app/Contents/Resources/cdo_bundle/``, so the .app is
self-contained and works on machines without Homebrew/MacPorts. Which binary:
the MAGICS-enabled one under ``$CDO_MAGICS_PREFIX`` if ``provision_cdo_macos.sh``
has built one, otherwise whatever is on ``PATH`` — see ``_find_system_cdo``.
Bundling a CDO without MAGICS warns rather than fails; ``--require-magics``
turns that back into a refusal.

Single source of truth for version + name comes from
ncexplorer_toolkit/__version__.py — never duplicated here.

**This module is the only build definition.**  There is deliberately no
hand-maintained NCExplorer.spec: two descriptions of the same freeze drift, and
the one that used to sit in the repo root had drifted far enough to exclude
PyQt6.QtSvg — which resources/icons.py imports at module scope — so an app
built from it died on the ImportError handler in main.py.  pyinstaller still
writes a spec on every run, but ``--specpath`` puts it under build/ where it
reads as the generated artefact it is.  Always build with ``python build.py``.

Usage:
    python build.py                      # full pipeline (auto-detect platform)
    python build.py --clean-only         # remove dist/build only
    python build.py --skip-clean         # incremental rebuild
    python build.py --debug              # console window + tracebacks
    python build.py --no-smoke-test      # skip artefact invocation
    python build.py --no-checksum        # skip SHA256 generation
    python build.py --no-installer       # never offer Inno Setup
    python build.py --no-cdo-bundle      # (macOS) skip bundling cdo + dylibs
    python build.py --no-dmg             # (macOS) skip .dmg packaging
    python build.py --require-magics     # refuse to ship a CDO without MAGICS
"""

from __future__ import annotations

import argparse
import functools
import glob
import hashlib
import json
import logging
import os
import platform
import runpy
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants — single source of truth
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
_version_globals = runpy.run_path(
    str(ROOT / "ncexplorer_toolkit" / "__version__.py")
)
APP_VERSION: str = _version_globals["__version__"]
APP_NAME:    str = _version_globals["APP_NAME"]      # "NCExplorer"

PACKAGE_NAME = "ncexplorer_toolkit"
ENTRY_POINT  = "main.py"
DATA_SEP     = os.pathsep                            # ';' on Win, ':' elsewhere

DIST = ROOT / "dist"

log = logging.getLogger("build")


# ---------------------------------------------------------------------------
# Platform-aware artefact paths
# ---------------------------------------------------------------------------
def primary_artefact() -> Path:
    """Return the artefact a user downloads / ships."""
    if sys.platform == "win32":
        return DIST / f"{APP_NAME}.exe"
    if sys.platform == "darwin":
        return DIST / f"{APP_NAME}.app"            # bundle directory
    return DIST / APP_NAME                          # Linux ELF


def hashable_binary() -> Path:
    """Return the file (not directory) that should be hashed and smoke-tested."""
    if sys.platform == "darwin":
        return DIST / f"{APP_NAME}.app" / "Contents" / "MacOS" / APP_NAME
    return primary_artefact()


def platform_label() -> str:
    return {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")


# ---------------------------------------------------------------------------
# Clean
# ---------------------------------------------------------------------------
def clean_build() -> None:
    log.info("Cleaning previous builds...")

    art = primary_artefact()
    if art.exists():
        try:
            shutil.rmtree(art) if art.is_dir() else art.unlink()
            log.info("Cleaned artefact: %s", art.relative_to(ROOT))
        except PermissionError:
            log.error("%s is in use — close all instances and re-run.", art.name)
            sys.exit(1)

    for d in ("dist", "build", "__pycache__"):
        p = ROOT / d
        if p.exists():
            try:
                shutil.rmtree(p)
                log.info("Cleaned: %s/", d)
            except OSError as e:
                log.warning("Could not remove %s/: %s", d, e)

    # NB: '*.spec' is deliberately NOT in this list.  build.py is the single
    # source of truth for the freeze (see build_pyinstaller_command), and the
    # spec pyinstaller generates is written into build/ via --specpath, which
    # the loop above already removes wholesale.  Globbing '*.spec' at the repo
    # root only risks eating a file a human put there.
    for pattern in ("*.egg-info",):
        for path in glob.glob(str(ROOT / pattern)):
            try:
                p = Path(path)
                shutil.rmtree(p) if p.is_dir() else p.unlink()
                log.info("Cleaned: %s", p.name)
            except OSError as e:
                log.warning("Could not clean %s: %s", path, e)


# ---------------------------------------------------------------------------
# pyinstaller command assembly — split into focused helpers
# ---------------------------------------------------------------------------
def _add_data(src: str, dst: str) -> list[str]:
    return ["--add-data", f"{src}{DATA_SEP}{dst}"]


#: Branding lives in one directory, spelled as the repository spells it.
#: resources/branding.py resolves the same two files at runtime.
BRANDING_DIR = ROOT / "assest"
BRANDING_ICON_PNG = BRANDING_DIR / "NCE_icon.png"

#: Where the platform icon formats are generated.  An output, not an input:
#: build/ is wiped by clean_build, so the .ico / .icns are rebuilt from the PNG
#: on every run and can never go stale against the artwork.
GENERATED_ICON_DIR = ROOT / "build" / "icons"

#: Sizes the Windows .ico carries.  Explorer picks per context (16 for the tree,
#: 32 for the desktop, 256 for the extra-large view); a single-size .ico is
#: rescaled by the shell and looks it.
_ICO_SIZES = [(s, s) for s in (16, 24, 32, 48, 64, 128, 256)]

#: (pixels, iconset basename) pairs Apple's iconutil expects.  1024 is only
#: reachable as 512@2x — there is no icon_1024x1024 in the format.
_ICNS_VARIANTS = [
    (16, "icon_16x16.png"), (32, "icon_16x16@2x.png"),
    (32, "icon_32x32.png"), (64, "icon_32x32@2x.png"),
    (128, "icon_128x128.png"), (256, "icon_128x128@2x.png"),
    (256, "icon_256x256.png"), (512, "icon_256x256@2x.png"),
    (512, "icon_512x512.png"), (1024, "icon_512x512@2x.png"),
]


def _generate_ico(source: Path, dest: Path) -> Path | None:
    """Convert the branding PNG to a multi-resolution Windows .ico."""
    try:
        from PIL import Image
    except ImportError:
        log.warning("Pillow is not installed; cannot generate %s.", dest.name)
        return None

    try:
        with Image.open(source) as img:
            img.convert("RGBA").save(dest, format="ICO", sizes=_ICO_SIZES)
    except Exception as exc:                               # noqa: BLE001
        log.warning("Could not generate %s (%s).", dest.name, exc)
        return None
    return dest


def _generate_icns(source: Path, dest: Path) -> Path | None:
    """Convert the branding PNG to a macOS .icns via Apple's iconutil.

    iconutil ships with the Command Line Tools and is the only converter that
    produces the full variant set Finder and the Dock expect; Pillow's ICNS
    writer is read-oriented and not available on every build host.
    """
    if shutil.which("iconutil") is None:
        log.warning("iconutil not found; cannot generate %s.", dest.name)
        return None
    try:
        from PIL import Image
    except ImportError:
        log.warning("Pillow is not installed; cannot generate %s.", dest.name)
        return None

    iconset = dest.with_suffix(".iconset")
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir(parents=True)

    try:
        with Image.open(source) as img:
            rgba = img.convert("RGBA")
            for size, name in _ICNS_VARIANTS:
                rgba.resize((size, size), Image.LANCZOS).save(iconset / name)
        rc = subprocess.run(
            ["iconutil", "--convert", "icns", str(iconset), "--output", str(dest)],
            capture_output=True, text=True,
        )
        if rc.returncode != 0 or not dest.exists():
            log.warning("iconutil failed (%s).", rc.stderr.strip() or rc.returncode)
            return None
    except Exception as exc:                               # noqa: BLE001
        log.warning("Could not generate %s (%s).", dest.name, exc)
        return None
    finally:
        shutil.rmtree(iconset, ignore_errors=True)
    return dest


def platform_icon() -> Path | None:
    """The icon file pyinstaller should embed, generated from the branding PNG.

    Windows wants .ico and macOS wants .icns; the repository holds one 1024²
    PNG, because keeping three hand-exported copies of the same artwork in sync
    is a job nobody does.  Linux needs no --icon at all — the window icon comes
    from ``QApplication::setWindowIcon`` at runtime.

    Falls back to the PNG itself if conversion is unavailable: pyinstaller will
    convert it through Pillow, and if that also fails it warns rather than
    failing the build.
    """
    if not BRANDING_ICON_PNG.exists():
        return None

    GENERATED_ICON_DIR.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        generated = _generate_ico(
            BRANDING_ICON_PNG, GENERATED_ICON_DIR / f"{APP_NAME}.ico")
    elif sys.platform == "darwin":
        generated = _generate_icns(
            BRANDING_ICON_PNG, GENERATED_ICON_DIR / f"{APP_NAME}.icns")
    else:
        return None

    if generated is not None:
        log.info("Generated app icon: %s", generated.relative_to(ROOT))
        return generated

    log.warning("Falling back to the raw PNG for --icon; pyinstaller will convert it.")
    return BRANDING_ICON_PNG


def _find_icon() -> list[str]:
    # A pre-exported icon still wins if someone has put one where the build
    # used to look, so an existing packaging setup is not overridden silently.
    candidates = [
        ROOT / "assets" / "icon.ico",
        ROOT / "assets" / f"{APP_NAME}.ico",
        ROOT / "assets" / f"{APP_NAME}.icns",       # macOS
        ROOT / "installer" / "icon.ico",
        ROOT / f"{APP_NAME}.ico",
        ROOT / "icon.ico",
    ]
    for path in candidates:
        if path.exists():
            log.info("Using icon: %s", path.relative_to(ROOT))
            return ["--icon", str(path)]

    icon = platform_icon()
    if icon is not None:
        return ["--icon", str(icon)]

    log.info("No icon found; building without --icon.")
    return []


def gather_data_files() -> list[str]:
    # Sources must be absolute: --specpath moves the spec into build/, and
    # pyinstaller resolves relative --add-data sources against the spec's
    # directory, not the cwd.
    flags = list(_add_data(str(ROOT / PACKAGE_NAME), PACKAGE_NAME))
    # The window icon and the splash artwork are read at runtime from these
    # PNGs — the .ico / .icns above only cover the *file* icon the desktop
    # shows before the app starts.  resources/branding.py looks for this
    # directory under sys._MEIPASS, so the bundled name must match the source.
    if BRANDING_DIR.is_dir():
        for asset in sorted(BRANDING_DIR.glob("*.png")):
            flags += _add_data(str(asset), BRANDING_DIR.name)
    else:
        log.warning(
            "%s/ is missing — the packaged app will fall back to a drawn splash "
            "and have no window icon.", BRANDING_DIR.name,
        )
    if (ROOT / "check_requirement.py").exists():
        flags += _add_data(str(ROOT / "check_requirement.py"), ".")
    return flags


#: Directory inside the frozen bundle that mimics a Cartopy data dir.
#: geocanvas/offline_basemap.py::_cartopy_data_dirs looks here first.
NE_BUNDLE_DIR = "cartopy_data"


def _requested_natural_earth() -> tuple[list[tuple[str, str]], list[str]]:
    """The ``(category, name)`` layers and the scales offline_basemap asks for.

    Read straight out of the module that does the asking, so the bundle can
    never contain a different set from the one the code looks up.  Loaded by
    file path rather than imported: ``import ncexplorer_toolkit...`` would
    execute the package __init__ and drag Qt into the build script, and this
    module needs nothing but the standard library and numpy.
    """
    import importlib.util

    path = ROOT / PACKAGE_NAME / "geocanvas" / "offline_basemap.py"
    spec = importlib.util.spec_from_file_location("_ncx_offline_basemap", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    layers = [(category, name) for category, name, _kind in module.NE_LAYERS]
    # Only the two scales the code will ever request. Natural Earth's 10m set is
    # the bulk of the on-disk cache and offline_basemap deliberately never asks
    # for it, so shipping it would be dead weight.
    scales = [module.SCALE_WIDE, module.SCALE_CLOSE]
    return layers, scales


def gather_natural_earth_data() -> list[str]:
    """Stage the Natural Earth shapefiles the offline basemap needs.

    ``cartopy.config['data_dir']`` is a *per-user cache* (~/.local/share/cartopy
    on Linux/macOS) that Cartopy populates by downloading on demand.  Nothing
    put it inside the app, so the offline backdrop silently vanished on every
    machine except the build host: ``natural_earth_path`` returns None rather
    than fetching — deliberately, that is what keeps the module offline — and
    each layer is then dropped from the backdrop.
    """
    try:
        import cartopy
        data_dir = Path(str(cartopy.config.get("data_dir")))
    except Exception as exc:                       # noqa: BLE001
        log.warning("Cartopy not importable (%s); no Natural Earth data bundled.", exc)
        return []

    if not data_dir.is_dir():
        log.warning(
            "Cartopy data dir %s does not exist — the packaged app will have no "
            "offline basemap.  Populate it first (draw an offline map once from "
            "source, or `python -c \"import cartopy.io.shapereader as s; "
            "s.natural_earth(name='land')\"`) and rebuild.",
            data_dir,
        )
        return []

    layers, scales = _requested_natural_earth()

    flags: list[str] = []
    staged = 0
    missing: list[str] = []
    total_bytes = 0

    for scale in scales:
        for category, name in layers:
            stem = f"ne_{scale}_{name}"
            src_dir = data_dir / "shapefiles" / "natural_earth" / category
            # A shapefile is a file *set*: .shp geometry is unreadable without
            # the .shx index and .dbf attributes, and .prj carries the CRS.
            sidecars = sorted(src_dir.glob(f"{stem}.*"))
            if not any(p.suffix == ".shp" for p in sidecars):
                missing.append(stem)
                continue

            dst = f"{NE_BUNDLE_DIR}/shapefiles/natural_earth/{category}"
            for sidecar in sidecars:
                flags += _add_data(str(sidecar), dst)
                total_bytes += sidecar.stat().st_size
            staged += 1

    log.info("Natural Earth: staging %d layers (%.1f MB) from %s",
             staged, total_bytes / (1024 * 1024), data_dir)
    if missing:
        log.warning(
            "Natural Earth layers not in the local cache, so absent from the "
            "bundle: %s.  Those layers will be dropped from the offline "
            "backdrop at runtime.", ", ".join(missing),
        )
    return flags


def gather_optional_imports(modules: list[str]) -> list[str]:
    flags: list[str] = []
    for mod in modules:
        try:
            __import__(mod)
            flags += ["--hidden-import", mod]
            log.debug("Including hidden-import: %s", mod)
        except ImportError:
            log.debug("Skipping hidden-import: %s (not installed)", mod)
    return flags


def gather_collect_alls(modules: list[str]) -> list[str]:
    flags: list[str] = []
    for mod in modules:
        try:
            __import__(mod)
            flags += ["--collect-all", mod]
            log.info("Collecting all: %s", mod)
        except ImportError:
            log.info("Skipping collect-all: %s (not installed)", mod)
    return flags


def gather_internal_hidden_imports() -> list[str]:
    flags: list[str] = []
    for sub in ("core", "geocanvas", "gui", "resources", "utils"):
        flags += ["--hidden-import", f"{PACKAGE_NAME}.{sub}"]
    return flags


def build_pyinstaller_command(*, debug: bool) -> list[str]:
    cmd: list[str] = [
        "pyinstaller",
        "--noconfirm",
        "--name", APP_NAME,
        # Keep the generated spec out of the repo root.  This file is an
        # *output* of the flags assembled below, never an input: a stale
        # NCExplorer.spec sitting next to build.py invites someone to run
        # `pyinstaller NCExplorer.spec` and get a different — and historically
        # broken — application.  Writing it under build/ makes that mistake
        # impossible and lets `--clean` reclaim it with the rest of build/.
        "--specpath", str(ROOT / "build"),
    ]
    # macOS uses --onedir because PyInstaller is deprecating --onefile +
    # --windowed for .app bundles (an error in v7.0) — a .app cannot be a
    # single file and the self-extracting bootloader clashes with Gatekeeper
    # / Hardened Runtime.  Windows / Linux stay on --onefile.
    cmd.append("--onedir" if sys.platform == "darwin" else "--onefile")
    cmd.append("--console" if debug else "--windowed")

    cmd += _find_icon()
    cmd += gather_data_files()
    cmd += gather_natural_earth_data()
    # PyQt6: --collect-all already covers binaries, data, submodules, metadata
    cmd += ["--collect-all", "PyQt6"]
    cmd += gather_optional_imports([
        "cartopy", "xarray", "netCDF4", "cftime", "geopandas", "shapely",
        "matplotlib", "numpy", "pandas", "rasterio", "h5netcdf",
        "matplotlib.backends.backend_qtagg",
        "matplotlib.backends.backend_qtcairo",
        # Animation export. imageio is imported lazily inside time_player, which
        # is invisible to PyInstaller's static analysis.
        "imageio", "imageio.v2",
        # contextily / mercantile are imported *inside functions* in
        # geocanvas/canvas.py so that a missing install degrades to "no online
        # basemap" instead of failing the whole canvas.  That lazy import is
        # invisible to pyinstaller's static analysis, so without these the
        # frozen app silently reports contextily as unavailable and every XYZ
        # basemap disappears.
        "contextily", "mercantile",
    ])
    # cmcrameri ships its colormaps as data files (.txt colour tables) rather
    # than Python code, and cmocean registers its own on import — a bare
    # --hidden-import leaves the frozen app with an empty colormap catalogue, so
    # both need --collect-all to pull the data across.
    # imageio_ffmpeg ships an ffmpeg *binary* next to its Python code, so a
    # hidden-import alone would freeze an app whose MP4 export cannot find an
    # encoder. GIF export works either way.
    # xyzservices keeps its whole provider catalogue in a data file
    # (providers.json), so geocanvas/basemap_sources.py resolves to an empty
    # provider list under a bare --hidden-import.
    cmd += gather_collect_alls([
        "rasterio", "cartopy", "scipy", "cmocean", "cmcrameri", "imageio_ffmpeg",
        "xyzservices",
    ])
    cmd += gather_internal_hidden_imports()
    # Exclude rival Qt bindings — pyinstaller aborts if both PyQt5 and PyQt6
    # are reachable through the import graph (a common situation in mixed
    # anaconda envs).  PySide2 / PySide6 are excluded for the same reason.
    # The heavy ML / notebook stack is excluded too: NCExplorer never imports
    # them, but they ride along through anaconda's site-packages and either
    # crash pyinstaller's hooks (tensorflow's _collect_submodules segfaults)
    # or bloat the .app by hundreds of megabytes.
    excludes: list[str] = [
        "PyQt5", "PySide2", "PySide6",
        "tensorflow", "torch", "jax", "jaxlib",
        "IPython", "ipykernel", "jupyter", "notebook", "nbformat",
        "sphinx", "pytest", "spyder",
        # pyogrio wheels for macOS arm64 lack Mach-O headerpad space, which
        # makes pyinstaller's install_name rewriting fail.  Geopandas falls
        # back to fiona for I/O when pyogrio is unavailable.
        #
        # NOTE (unverified, 2026-08-08): on pyogrio 0.11.1 that no longer
        # reproduces by hand — `install_name_tool -change` and `-add_rpath`
        # with an over-long path both succeed on _io.cpython-313-darwin.so and
        # on the vendored libgdal.36.3.10.3.dylib.  The exclusion is kept until
        # a real freeze confirms it, because the second half of the reason
        # still stands: there is no hook-pyogrio, so its .dylibs would need
        # collecting by hand.  Lifting this is what would make KML, KMZ and
        # Idrisi vector work in the packaged app -- fiona's GDAL has no KML
        # driver, so those formats are currently source-checkout-only.
        "pyogrio",
    ]
    # NCExplorer only imports PyQt6.QtCore / QtGui / QtWidgets / QtSvg.
    # Excluding every other Qt6 submodule shrinks the bundle by ~250 MB and
    # sidesteps pyinstaller's --onedir symlink-collision bug on Qt framework
    # directories (QtBluetooth/QtPositioning duplicate-symlink errors during
    # COLLECT).  QtSvg must stay in: resources/icons.py rasterises the operator
    # category glyphs through QSvgRenderer, and excluding it leaves the toolbar
    # blank in the packaged app.
    qt_unused = (
        "Qt3DAnimation Qt3DCore Qt3DExtras Qt3DInput Qt3DLogic Qt3DRender "
        "QtBluetooth QtCharts QtConcurrent QtDBus QtDataVisualization "
        "QtDesigner QtHelp QtLocation QtMultimedia QtMultimediaWidgets "
        "QtNetwork QtNetworkAuth QtNfc QtOpenGL QtOpenGLWidgets QtPdf "
        "QtPdfWidgets QtPositioning QtPrintSupport QtQml QtQuick QtQuick3D "
        "QtQuickControls2 QtQuickWidgets QtRemoteObjects QtScxml QtSensors "
        "QtSerialBus QtSerialPort QtSpatialAudio QtSpeech QtSql "
        "QtStateMachine QtSvgWidgets QtTest QtTextToSpeech "
        "QtWebChannel QtWebEngineCore QtWebEngineQuick QtWebEngineWidgets "
        "QtWebSockets QtWebView QtXml QtXmlPatterns"
    ).split()
    excludes += [f"PyQt6.{m}" for m in qt_unused]
    for excl in excludes:
        cmd += ["--exclude-module", excl]
    cmd.append(str(ROOT / ENTRY_POINT))     # absolute, for the same reason
    return cmd


# ---------------------------------------------------------------------------
# Build runner
# ---------------------------------------------------------------------------
def check_shapefile_engine() -> bool:
    """Fail loudly at build time if the one vector I/O engine is missing.

    geopandas 1.x tries pyogrio first and falls back to fiona.  This build
    excludes pyogrio on purpose (its macOS arm64 wheels lack the Mach-O
    headerpad space pyinstaller needs to rewrite install names, and
    pyinstaller-hooks-contrib ships no hook-pyogrio), which leaves fiona as the
    *only* engine.  fiona being present is currently an accident of the build
    environment rather than anything the build asserts, so a freeze done in an
    env without it would produce an app that cannot open a single shapefile —
    and would say so only when a user tried.
    """
    try:
        import fiona                                    # noqa: F401
    except ImportError:
        log.error("=" * 68)
        log.error("MISSING VECTOR I/O ENGINE — 'fiona' is not importable.")
        log.error("")
        log.error("pyogrio is excluded from this build by design, so fiona is")
        log.error("the only engine geopandas can use.  Without it the packaged")
        log.error("app cannot read shapefiles, GeoJSON, GML, GPKG or GPX at all.")
        log.error("(KML, KMZ and Idrisi vector need pyogrio and are already")
        log.error(" unavailable in this build — fiona's GDAL has no KML driver.)")
        log.error("")
        log.error("    pip install fiona")
        log.error("")
        log.error("(Un-excluding pyogrio has not been validated as a fix — see")
        log.error(" the note on the headerpad exclusion in EXCLUDED_MODULES.)")
        log.error("=" * 68)
        return False

    log.info("Vector I/O engine: fiona %s", getattr(fiona, "__version__", "?"))
    return True


#: Build features the shipped CDO must have, and what is lost without each.
#:
#: MAGICS only. ``has-cmor`` is deliberately absent — ``cmor`` writes
#: CMIP-compliant output under a controlled vocabulary, which is a publication
#: workflow rather than an exploration one, and it costs a large dependency tree
#: for one operator. FFTW3 cannot be listed here at all: ``cdo --config`` has no
#: key for it (``has-fftw3``, ``has-fftw`` and ``has-FFTW3`` are each "unknown
#: config option"), so there is nothing to assert against. The application
#: explains that one from the runtime error text instead.
_REQUIRED_CDO_FEATURES = {
    "has-magics": "the six plotting operators "
                  "(contour, shaded, grfill, vector, stream, graph)",
}


def check_cdo_capabilities(cdo: Path | None = None, *,
                           required: bool = False) -> bool:
    """Report what the CDO about to be bundled cannot do, and by default ship anyway.

    Same shape and same argument as :func:`check_shapefile_engine`: the build
    host's environment is currently an *accident* rather than something the
    build asserts, and the failure it produces is invisible until a user tries
    the feature. Here the accident is worse, because the bundled CDO is the one
    every user gets — freezing on a host whose ``brew install cdo`` has no
    MAGICS ships a plotting application that cannot plot, to everybody, silently.

    "Silently" is the word that stopped being true, and it is why this warns
    rather than refuses. Two things now stand between a no-MAGICS bundle and a
    user who does not understand why plotting fails, and neither existed when
    this check was written to fail the build:

    * :func:`_find_system_cdo` prefers a provisioned MAGICS build over ``PATH``,
      so the common cause of this warning — a working MAGICS CDO sitting off
      ``PATH`` while Homebrew's shadows it — no longer reaches here at all.
    * ``nc_integration.missing_build_feature`` refuses each of the six operators
      *before* CDO is launched, naming the cause and the fix. A shipped
      no-MAGICS build is a build with six operators that explain themselves, not
      one that fails mysteriously.

    So the cost of shipping without MAGICS is now bounded and legible, which
    makes it a decision rather than an accident — and blocking every build on a
    decision the operator has already made is not a check, it is an obstacle.
    Pass ``required=True`` (``--require-magics``) to restore the refusal for a
    release build, where the answer should be "no, actually fix it".

    Read from ``cdo --config``, which is CDO's own machine-readable answer.
    **Not** from ``cdo -V``'s ``Features:`` line: on a stock Homebrew 2.6.3 that
    line reads "8GB 8threads c++20 Fortran pthreads HDF5 NC4/HDF5 dap sz proj
    sse4_2" and does not mention MAGICS in either direction. An absence in a
    list is not an answer.

    Returns True when the probe cannot be run at all, rather than blocking a
    build on an unanswerable question — the same rule the runtime capability
    layer follows, and it keeps this from failing on a CDO too old for
    ``--config``. What it will not do is pass a build whose CDO answers "no".
    """
    cdo = cdo or _find_system_cdo()
    if cdo is None:
        # bundle_cdo_into_app already warns about this and degrades to the
        # user's system CDO; it is not this check's failure to report.
        return True

    try:
        probe = subprocess.run([str(cdo), "--config", "all"],
                               capture_output=True, text=True, timeout=30)
        config = json.loads(probe.stdout or "{}") if probe.returncode == 0 else {}
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        log.warning("Could not probe CDO build features (%s) — not blocking "
                    "the build on an unanswerable question.", exc)
        return True

    if not config:
        log.warning("`cdo --config all` returned nothing usable — this CDO may "
                    "predate the option. Not blocking the build.")
        return True

    missing = {key: what for key, what in _REQUIRED_CDO_FEATURES.items()
               if str(config.get(key, "")).strip().lower() == "no"}
    if not missing:
        have = sorted(k.replace("has-", "") for k, v in config.items()
                      if str(v).lower() == "yes")
        log.info("CDO build features OK (%s): %s", cdo, ", ".join(have))
        return True

    emit = log.error if required else log.warning
    emit("=" * 68)
    emit("CDO IS MISSING A BUILD FEATURE THIS APP SHIPS%s",
         " — refusing to build." if required else ".")
    emit("")
    emit("    %s", cdo)
    for key, what in missing.items():
        emit("    %-14s no    -> %s will not work", key, what)
    emit("")
    emit("This is a compile-time link, not a plugin: installing the")
    emit("library alone does nothing, because the existing binary will")
    emit("never pick it up. The bundled CDO is the one every user gets.")
    emit("")
    emit("    ./provision_cdo_macos.sh")
    emit("")
    # No PATH export is suggested any more: _find_system_cdo picks the
    # provisioned binary up from its prefix directly, and telling someone to
    # export a PATH that the build no longer consults for this would be one
    # instruction too many, in the file that is meant to be the fix.
    if required:
        emit("Then rebuild. To ship without MAGICS anyway, drop --require-magics.")
        emit("=" * 68)
        return False

    emit("Building anyway. The shipped app will list these operators and refuse")
    emit("them at the point of running, naming this as the cause. Pass")
    emit("--require-magics to make this a build failure instead.")
    emit("=" * 68)
    return True


def run_pyinstaller(*, debug: bool) -> None:
    log.info("Building %s %s with pyinstaller...", APP_NAME, APP_VERSION)

    if not (ROOT / PACKAGE_NAME).is_dir():
        log.error("Package directory '%s' not found.", PACKAGE_NAME)
        sys.exit(1)
    if not (ROOT / ENTRY_POINT).is_file():
        log.error("Entry point '%s' not found.", ENTRY_POINT)
        sys.exit(1)

    # --specpath will not create its target directory.
    (ROOT / "build").mkdir(exist_ok=True)

    cmd = build_pyinstaller_command(debug=debug)
    log.info("Command: %s", " ".join(cmd))

    # The default 1000-frame Python recursion limit is too shallow for
    # pyinstaller's modulegraph walk in fat anaconda envs (sklearn, bokeh,
    # cartopy etc. push it past the wall).  We re-launch the CLI through a
    # Python prelude that lifts the limit before importing pyinstaller — the
    # same workaround the .spec file would apply if we were using one.
    pyinst_args = cmd[1:]                          # drop literal 'pyinstaller'
    # pyinstaller's COLLECT phase on macOS calls os.symlink() while replicating
    # Qt6 .framework bundles, and tries to create the same Versions/Current/*
    # symlink twice on certain frameworks (QtBluetooth, QtPositioning, ...).
    # Wrap os.symlink to be EEXIST-tolerant for the duration of the build.
    prelude = (
        "import os, sys\n"
        "sys.setrecursionlimit(5000)\n"
        "_orig_symlink = os.symlink\n"
        "def _safe_symlink(src, dst, *a, **kw):\n"
        "    # pyinstaller's COLLECT occasionally schedules the same Qt6\n"
        "    # framework alias (Versions/Current -> A, Resources, Helpers...)\n"
        "    # twice — once via a directory entry and once via a symlink TOC\n"
        "    # entry — and aborts the build when os.symlink raises EEXIST.\n"
        "    # Silently skip the redundant symlink so COLLECT can proceed.\n"
        "    try:\n"
        "        return _orig_symlink(src, dst, *a, **kw)\n"
        "    except FileExistsError:\n"
        "        return None\n"
        "os.symlink = _safe_symlink\n"
        f"sys.argv = ['pyinstaller', *{pyinst_args!r}]\n"
        "from PyInstaller.__main__ import run\n"
        "run()\n"
    )
    rc = subprocess.run(
        [sys.executable, "-c", prelude],
        cwd=ROOT,
    ).returncode
    if rc != 0:
        log.error("pyinstaller build failed (exit code %d).", rc)
        sys.exit(rc)
    log.info("pyinstaller build succeeded.")


# ---------------------------------------------------------------------------
# Smoke test — actually invoke the artefact
# ---------------------------------------------------------------------------
def smoke_test(*, timeout: int = 90) -> bool:
    log.info("Smoke-testing the artefact...")

    binary = hashable_binary()
    if not binary.exists():
        log.error("Artefact missing: %s", binary)
        return False

    # main.py supports a fast-path --version that exits before Qt is touched.
    try:
        result = subprocess.run(
            [str(binary), "--version"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        log.error("Smoke test timed out after %ss.", timeout)
        return False
    except OSError as e:
        log.error("Could not invoke artefact: %s", e)
        return False

    output = (result.stdout + result.stderr).strip()
    log.info("Artefact output: %s", output or "<empty>")

    if result.returncode != 0:
        log.error("Smoke test failed (exit code %d).", result.returncode)
        return False
    if APP_VERSION not in output:
        log.error("Smoke test output did not contain version %s.", APP_VERSION)
        return False

    log.info("Smoke test passed.")
    return smoke_test_bundled_cdo()


def verify_branding(art: Path) -> bool:
    """Confirm the icon and splash artwork actually reached the artefact.

    Cosmetic, so this warns rather than failing the build — but it warns *at
    build time*, which is the only moment anybody is looking.  The failure it
    catches is silent by construction: resources/branding.py degrades to a
    drawn card and a null icon when the PNGs are missing, so an app frozen
    without them starts and looks almost right.
    """
    ok = True

    if art.is_dir():
        wanted = {p.name for p in BRANDING_DIR.glob("*.png")} if BRANDING_DIR.is_dir() else set()
        found = {p.name for p in art.rglob(f"{BRANDING_DIR.name}/*.png")}
        missing = wanted - found
        if wanted and not missing:
            log.info("Branding: %d asset(s) bundled under %s/.",
                     len(found), BRANDING_DIR.name)
        elif missing:
            log.warning("Branding: %s did not reach the bundle — the packaged "
                        "app will draw a fallback splash.", ", ".join(sorted(missing)))
            ok = False
    else:
        # --onefile: the payload is appended to the launcher and only exists
        # after extraction, so there is nothing to walk here.
        log.info("Branding: one-file artefact — assets verified at build time only.")

    if sys.platform == "darwin" and art.is_dir():
        import plistlib
        plist = art / "Contents" / "Info.plist"
        try:
            icon_name = plistlib.loads(plist.read_bytes()).get("CFBundleIconFile")
        except (OSError, ValueError) as exc:
            log.warning("Branding: could not read %s (%s).", plist.name, exc)
            return False
        icon_file = art / "Contents" / "Resources" / str(icon_name or "")
        # Info.plist may name the icon with or without its extension.
        if icon_name and (icon_file.exists() or icon_file.with_suffix(".icns").exists()):
            log.info("Branding: bundle icon is %s.", icon_name)
        else:
            log.warning("Branding: the .app has no bundle icon (CFBundleIconFile=%r).",
                        icon_name)
            ok = False

    return ok


#: A plot smaller than this is treated as a failure even though it exists.
#:
#: Measured, and the reason this is a size rather than a bool: with
#: ``MAGPLUS_HOME`` pointing at a directory that is not there, a PostScript plot
#: is **still written** — 8 KB against 104 KB for the real thing — and CDO exits
#: 0. A check for "the file exists and is non-empty" passes that. A correct
#: 36x18 shaded PNG measured ~50 KB, so 5 KB is far below anything real and far
#: above the degenerate form.
_MIN_PLOT_BYTES = 5000


def smoke_test_bundled_cdo(*, timeout: int = 120) -> bool:
    """Prove the bundled CDO can actually plot, not merely that it says it can.

    Two things are checked and the second is the point of the first being
    insufficient:

    1. ``cdo --config has-magics`` reports yes. This is the capability *flag*.
    2. The binary renders a real plot from a generated sample, and the result is
       a plausible size.

    A flag is not proof the link works. The bundle rewrites every install name
    to ``@loader_path/`` and re-signs each Mach-O; a mistake there produces a
    binary that still reports ``has-magics:yes`` — the flag is compiled in — and
    fails at the first ``dlopen``. Equally, the Magics *data* is copied by
    ``_bundle_magics_data`` and referenced by an environment variable, neither
    of which the capability flag knows anything about; getting that wrong is
    what produces the 8 KB plot described at :data:`_MIN_PLOT_BYTES`.

    Skipped, not failed, when there is no bundled CDO or it has no MAGICS —
    ``check_cdo_capabilities`` is what decides whether that is acceptable, and
    two checks failing a build for one reason produces two error messages for
    one problem.
    """
    if platform.system() != "Darwin":
        return True

    app = primary_artefact()
    cdo = app / "Contents" / "Resources" / "cdo_bundle" / "cdo"
    if not cdo.exists():
        log.info("No bundled CDO to smoke-test.")
        return True

    env = dict(os.environ)
    magics_home = cdo.parent / "magics"
    if magics_home.is_dir():
        env["MAGPLUS_HOME"] = str(magics_home)

    try:
        probe = subprocess.run([str(cdo), "--config", "has-magics"],
                               capture_output=True, text=True, timeout=timeout,
                               env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        log.error("Bundled CDO could not be run at all: %s", exc)
        return False
    if (probe.stdout or "").strip().lower() != "yes":
        log.info("Bundled CDO reports no MAGICS — skipping the plot test.")
        return True

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        sample, base = work / "in.nc", work / "plot"
        try:
            subprocess.run([str(cdo), "-s", "-f", "nc", "-topo,r36x18", str(sample)],
                           capture_output=True, timeout=timeout, env=env, check=False)
            if not sample.exists():
                log.error("Bundled CDO could not generate a sample field.")
                return False
            subprocess.run([str(cdo), "-s", "shaded,device=png", str(sample), str(base)],
                           capture_output=True, timeout=timeout, env=env, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            log.error("Bundled CDO failed while plotting: %s", exc)
            return False

        # An obase, not a file: Magplot writes <obase>_<variable>.<device>, so
        # the name is CDO's to choose and this has to glob for it.
        plots = sorted(work.glob("plot*"))
        if not plots:
            log.error("=" * 68)
            log.error("BUNDLED CDO REPORTS MAGICS BUT PRODUCED NO PLOT.")
            log.error("Most likely the Magics runtime data is missing or")
            log.error("MAGPLUS_HOME is not being set — see _bundle_magics_data.")
            log.error("=" * 68)
            return False

        biggest = max(p.stat().st_size for p in plots)
        if biggest < _MIN_PLOT_BYTES:
            log.error("=" * 68)
            log.error("BUNDLED CDO PRODUCED A DEGENERATE PLOT (%d bytes).", biggest)
            log.error("A plot this small is what Magics writes when it cannot")
            log.error("find its data files — coastlines and fonts are missing.")
            log.error("The file exists, so this would otherwise have passed.")
            log.error("=" * 68)
            return False

        log.info("Bundled CDO plotted %s (%d bytes) — MAGICS works.",
                 plots[0].name, biggest)
    return True


# ---------------------------------------------------------------------------
# SHA256 checksum
# ---------------------------------------------------------------------------
def write_checksum() -> Path | None:
    binary = hashable_binary()
    if not binary.exists():
        log.warning("No binary to hash at %s.", binary)
        return None

    h = hashlib.sha256()
    with binary.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    digest = h.hexdigest()

    sidecar_root = primary_artefact() if primary_artefact().is_file() else DIST
    sidecar = (
        sidecar_root.with_suffix(sidecar_root.suffix + ".sha256")
        if sidecar_root.is_file()
        else DIST / f"{APP_NAME}-{APP_VERSION}-{platform_label()}.sha256"
    )
    sidecar.write_text(f"{digest}  {binary.name}\n", encoding="utf-8")
    log.info("SHA256: %s", digest)
    log.info("Wrote checksum: %s", sidecar.relative_to(ROOT))
    return sidecar


# ---------------------------------------------------------------------------
# Inno Setup (Windows-only, optional)
# ---------------------------------------------------------------------------
def create_installer() -> bool:
    log.info("Creating Windows installer with Inno Setup...")

    iss = ROOT / "installer" / "setup_script.iss"
    if not iss.exists():
        log.info("%s not found; skipping installer.", iss.relative_to(ROOT))
        return False
    if shutil.which("iscc") is None:
        log.warning("Inno Setup compiler 'iscc' not found in PATH.")
        log.warning("Download: https://jrsoftware.org/isdl.php")
        return False

    rc = subprocess.run(["iscc", str(iss)], cwd=ROOT).returncode
    if rc != 0:
        log.error("Inno Setup compilation failed (exit code %d).", rc)
        return False
    log.info("Installer created.")
    return True


# ---------------------------------------------------------------------------
# macOS — CDO bundling + DMG packaging
# ---------------------------------------------------------------------------
#
# Strategy
# --------
# After pyinstaller produces ``dist/NCExplorer.app`` (an arm64 wrapper around
# our Python entry-point), we copy ``cdo`` and every dylib it transitively
# depends on into ``Contents/Resources/cdo_bundle/`` as a flat directory.  All
# install names are rewritten with ``install_name_tool`` to use
# ``@loader_path/<basename>`` so the dynamic loader resolves them relative to
# the bundled location at runtime — regardless of where the user moves the
# .app.  Each rewritten binary is then re-signed (ad-hoc) so Gatekeeper
# accepts it.
#
# CDO on Apple Silicon is typically built for x86_64 via Homebrew; Rosetta
# transparently translates it as a child process of our arm64 Python, so the
# .app itself stays single-arch arm64 without any runtime arch juggling.
# ---------------------------------------------------------------------------

_SYSTEM_DYLIB_PREFIXES = ("/usr/lib/", "/System/")


#: Where ``provision_cdo_macos.sh`` installs the MAGICS-enabled CDO it builds.
#:
#: Read from the same environment variable the script itself reads, defaulting
#: to the same path, so the two agree by construction rather than by both
#: hard-coding a string that one of them can later change alone.
_CDO_MAGICS_PREFIX = Path(
    os.environ.get("CDO_MAGICS_PREFIX") or Path.home() / ".local" / "cdo-magics"
)


def _cdo_has_magics(cdo: Path) -> bool:
    """``cdo --config has-magics`` as a bool; False when it cannot be asked.

    False for "no" and for "could not establish" alike, which is the opposite of
    the rule :func:`check_cdo_capabilities` follows — deliberately. There, an
    unanswered probe must not condemn a CDO. Here it only decides whether to
    *prefer* one candidate over another, and a binary that cannot answer is not
    evidence for preferring it.
    """
    try:
        probe = subprocess.run([str(cdo), "--config", "has-magics"],
                               capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0 and (probe.stdout or "").strip().lower() == "yes"


@functools.lru_cache(maxsize=1)
def _find_system_cdo() -> Path | None:
    """Locate a usable ``cdo`` on the build host.

    A CDO provisioned by ``provision_cdo_macos.sh`` wins over ``PATH``, and this
    is the one place in the search order that is a preference rather than a
    fallback. The reason is that MAGICS is a compile-time link: Homebrew's
    ``cdo`` cannot acquire it, so on a host that has both, the Homebrew binary
    on ``PATH`` and a provisioned one that answers ``has-magics:yes``, taking
    ``PATH`` would bundle the only one of the two that cannot plot. The
    provisioned binary exists for no other purpose.

    It is preferred only when it *proves* MAGICS. A stale or half-built tree
    under the prefix falls through to ``PATH`` rather than shadowing a working
    system CDO — which is also what makes this safe to apply unconditionally
    instead of behind a flag.

    Cached: the answer cannot change during a build, and without the cache the
    ``--config`` probe and its log line would repeat for each caller.
    """
    provisioned = _CDO_MAGICS_PREFIX / "bin" / "cdo"
    if (provisioned.is_file() and os.access(provisioned, os.X_OK)
            and _cdo_has_magics(provisioned)):
        log.info("Using the MAGICS-enabled CDO at %s (preferred over PATH)",
                 provisioned)
        return provisioned.resolve()

    direct = shutil.which("cdo")
    if direct:
        return Path(direct).resolve()
    for candidate in (
        "/opt/homebrew/bin/cdo",       # Apple Silicon Homebrew
        "/usr/local/bin/cdo",          # Intel Homebrew
        "/opt/local/bin/cdo",          # MacPorts
        "/sw/bin/cdo",                 # Fink
    ):
        p = Path(candidate)
        if p.is_file() and os.access(p, os.X_OK):
            return p
    return None


def _macho_archs(binary: Path) -> list[str]:
    """Architectures a Mach-O file contains, via ``lipo -archs``."""
    try:
        out = subprocess.check_output(
            ["lipo", "-archs", str(binary)], text=True, stderr=subprocess.DEVNULL
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return []
    return out.split()


def _warn_on_cdo_arch_mismatch(cdo: Path) -> bool:
    """Warn loudly when ``cdo`` cannot run natively on the app's architecture.

    pyinstaller emits an app for the architecture of the Python running it, so
    that — not the shape of the host — is what the bundled CDO has to match.
    An x86_64 Homebrew CDO staged into an arm64 .app works on a build host with
    Rosetta 2 installed and fails on every clean Apple Silicon Mac with
    ``Bad CPU type in executable``, which is exactly the kind of defect that
    survives local testing and ships.

    Returns True when the architectures match (or cannot be determined).
    """
    target = platform.machine()                  # 'arm64' or 'x86_64'
    archs = _macho_archs(cdo)
    if not archs:
        log.warning("Could not determine the architecture of %s.", cdo)
        return True
    if target in archs:
        log.info("cdo architecture %s matches the target (%s).",
                 "/".join(archs), target)
        return True

    log.warning("=" * 68)
    log.warning("ARCHITECTURE MISMATCH — the bundled CDO will not run on a")
    log.warning("clean %s Mac.", target)
    log.warning("")
    log.warning("  cdo binary : %s", cdo)
    log.warning("  its arch   : %s", "/".join(archs))
    log.warning("  app target : %s", target)
    log.warning("")
    log.warning("This build only works here because Rosetta 2 translates it.")
    log.warning("Every operator will fail on a machine without Rosetta with")
    log.warning("OSError errno 86, 'Bad CPU type in executable'.")
    log.warning("")
    log.warning("Fix — install a native CDO under /opt/homebrew and rebuild:")
    log.warning("")
    log.warning("    arch -%s brew install cdo", target)
    log.warning("")
    log.warning("At runtime the app probes the bundled binary and falls back")
    log.warning("to a working CDO on PATH, so this degrades rather than")
    log.warning("breaks — but the .app is not self-contained.")
    log.warning("=" * 68)
    return False


def _load_rpaths(binary: Path) -> list[str]:
    """The ``LC_RPATH`` entries of one Mach-O, in order.

    Needed to resolve ``@rpath/...`` references, which are the ones this
    bundler used to drop on the floor. See :func:`_resolve_ref`.
    """
    try:
        out = subprocess.check_output(
            ["otool", "-l", str(binary)], text=True, stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []

    paths: list[str] = []
    lines = out.splitlines()
    for index, line in enumerate(lines):
        if "LC_RPATH" not in line:
            continue
        for follow in lines[index:index + 4]:
            stripped = follow.strip()
            if stripped.startswith("path "):
                paths.append(stripped.split(" (", 1)[0][5:].strip())
                break
    return paths


def _resolve_ref(ref: str, binary: Path) -> Path | None:
    """Turn one load reference into a real file, or None.

    The three ``@``-prefixed forms are resolved rather than skipped, and that is
    a **bug fix rather than a refinement**. Skipping them was fine for as long as
    the bundle was cdo plus a dozen flat dylibs, none of which used them. Adding
    MAGICS broke that: Magics links Qt, Qt frameworks reference each other as
    ``@rpath/QtDBus.framework/Versions/A/QtDBus``, and a skipped reference is a
    library that is never copied.

    Measured before this existed: the staged bundle held 53 files, reported
    success, and the bundled ``cdo`` would not start at all —
    ``dyld: Library not loaded: @rpath/QtDBus.framework/Versions/A/QtDBus``.
    Four distinct ``@rpath`` references were being dropped, and QtDBus was
    reachable by no other route.

    * ``@rpath/X``          – tried against each ``LC_RPATH`` of the *referring*
      binary, in order, which is what dyld itself does.
    * ``@loader_path/X``    – relative to the referring binary's directory.
    * ``@executable_path/X`` – relative to the same, which is as close as this
      can get without knowing the final executable; it is only used by things
      already inside a bundle.
    """
    if ref.startswith(_SYSTEM_DYLIB_PREFIXES):
        return None

    def _existing(candidate: Path) -> Path | None:
        """The path *as referenced*, verified to exist — deliberately not resolved.

        Returning ``candidate.resolve()`` here was a bug, and a quiet one. The
        bundle is flat and keyed on basenames: ``bundle_cdo_into_app`` copies to
        ``bundle_dir / src.name`` and ``_rewrite_install_names`` rewrites a
        reference only when ``Path(ref).name`` is among the copied names. A
        Homebrew dylib is normally a symlink whose target has a longer name, so
        resolving changed the basename and broke that match:

            ref     /usr/local/opt/hdf5/lib/libhdf5.320.dylib
            resolve /usr/local/Cellar/hdf5/2.1.1/lib/libhdf5.320.1.1.dylib
            copied  libhdf5.320.1.1.dylib
            lookup  libhdf5.320.dylib      -> not found, ref left pointing out

        The result was a bundle that still referenced ``/usr/local`` and worked
        only on a machine that had Homebrew — which is the one thing bundling is
        for. Five libraries were in that state (libhdf5, libhdf5_hl, libxcb,
        libgraphite2, libdouble-conversion).

        ``shutil.copy2`` follows the symlink, so the *content* copied is still
        the real library; only the name it is stored under changes, and it now
        matches what the loader will ask for. ``is_file()`` follows symlinks too,
        so a dangling link is still rejected.
        """
        return candidate if candidate.is_file() else None

    if ref.startswith("@rpath/"):
        suffix = ref[len("@rpath/"):]
        for rpath in _load_rpaths(binary):
            base = rpath.replace("@loader_path", str(binary.parent)) \
                        .replace("@executable_path", str(binary.parent))
            found = _existing(Path(base) / suffix)
            if found:
                return found
        return None

    for prefix in ("@loader_path/", "@executable_path/"):
        if ref.startswith(prefix):
            return _existing(binary.parent / ref[len(prefix):])

    if ref.startswith("@"):                          # @rpath handled above
        return None
    return _existing(Path(ref))


def _otool_deps(binary: Path) -> list[Path]:
    """Return the absolute dylib paths a Mach-O binary depends on (non-system).

    ``@rpath``/``@loader_path`` references are resolved rather than ignored;
    see :func:`_resolve_ref` for the dyld failure that came of ignoring them.
    """
    try:
        out = subprocess.check_output(
            ["otool", "-L", str(binary)], text=True, stderr=subprocess.DEVNULL
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []

    deps: list[Path] = []
    for raw in out.splitlines()[1:]:                # first line is the binary itself
        line = raw.strip()
        if not line:
            continue
        ref = line.split(" (", 1)[0].strip()
        if not ref:
            continue
        path = _resolve_ref(ref, binary)
        if path is not None:
            deps.append(path)
    return deps


def _collect_dylib_closure(root: Path) -> list[Path]:
    """Walk ``otool -L`` recursively to gather every non-system dylib in the
    transitive closure under ``root``."""
    seen: dict[Path, None] = {}
    queue: list[Path] = [root]
    while queue:
        node = queue.pop()
        for dep in _otool_deps(node):
            if dep in seen:
                continue
            seen[dep] = None
            queue.append(dep)
    return list(seen)


def _rewrite_install_names(bundle_dir: Path, files: list[Path]) -> None:
    """Rewrite each file's own LC_ID_DYLIB and all its LC_LOAD_DYLIB entries to
    ``@loader_path/<basename>`` so the loader finds siblings in the same dir.
    Then re-sign ad-hoc so the rewritten Mach-Os pass Gatekeeper."""
    names = {f.name for f in files}

    for f in files:
        # 1. Set own id to @rpath-free relative form (no effect on executables,
        #    but harmless).
        #
        #    Not gated on a ``.dylib`` suffix any more. A Qt framework's binary
        #    is named ``QtCore`` with no extension, so the old test skipped it
        #    and left its LC_ID_DYLIB reading
        #    ``/usr/local/opt/qtbase/lib/QtCore.framework/Versions/A/QtCore``.
        #    That is cosmetic — dyld loads a dependency by the path recorded in
        #    the *dependent*, which is rewritten below — but it makes an
        #    otherwise self-contained bundle look like it still needs Homebrew,
        #    and "is this bundle self-contained" is a question that gets asked
        #    by grepping for /usr/local.
        if not f.is_dir():
            subprocess.run(
                ["install_name_tool", "-id", f"@loader_path/{f.name}", str(f)],
                check=False,
            )

        # 2. Rewrite each non-system load reference whose basename we ship.
        try:
            otool_out = subprocess.check_output(
                ["otool", "-L", str(f)], text=True, stderr=subprocess.DEVNULL
            )
        except subprocess.CalledProcessError:
            continue

        for raw in otool_out.splitlines()[1:]:
            ref = raw.strip().split(" (", 1)[0].strip()
            if not ref or ref.startswith(_SYSTEM_DYLIB_PREFIXES):
                continue
            # ``@loader_path/x`` is already what this pass produces, so leaving
            # it alone keeps the function idempotent. Everything else with an
            # ``@`` still has to be rewritten — ``@rpath/QtDBus.framework/…``
            # points outside the bundle exactly as an absolute path does, and
            # skipping it (which this did) left the bundled cdo unable to start.
            # The basename is what matters because the bundle is flat:
            # ``@rpath/QtDBus.framework/Versions/A/QtDBus`` and
            # ``/opt/homebrew/lib/libfoo.dylib`` both become
            # ``@loader_path/<basename>``.
            if ref.startswith("@loader_path/"):
                continue
            basename = Path(ref).name
            if basename not in names:
                continue
            subprocess.run(
                ["install_name_tool", "-change", ref, f"@loader_path/{basename}", str(f)],
                check=False,
            )

        # 3. Ad-hoc re-sign — install_name_tool invalidates the existing signature.
        subprocess.run(
            ["/usr/bin/codesign", "--force", "--sign", "-", "--preserve-metadata=entitlements", str(f)],
            check=False,
            capture_output=True,
        )


def bundle_cdo_into_app(app_path: Path) -> bool:
    """Stage ``cdo`` and its dylib closure inside the .app bundle.

    Returns True on success, False if cdo cannot be found.  Idempotent: a
    pre-existing ``cdo_bundle`` is wiped first.
    """
    cdo = _find_system_cdo()
    if cdo is None:
        log.warning(
            "cdo binary not found on the build host — the .app will fall "
            "back to the user's system CDO at runtime.  Install with "
            "`brew install cdo` to embed it."
        )
        return False

    log.info("Bundling cdo from %s", cdo)
    _warn_on_cdo_arch_mismatch(cdo)
    bundle_dir = app_path / "Contents" / "Resources" / "cdo_bundle"
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)
    bundle_dir.mkdir(parents=True)

    # 1. Copy cdo itself.
    target_cdo = bundle_dir / "cdo"
    shutil.copy2(cdo, target_cdo)
    target_cdo.chmod(0o755)

    # 2. Walk the dylib closure and copy each one.
    dylibs = _collect_dylib_closure(target_cdo)
    log.info("Found %d transitive dylib dependencies", len(dylibs))
    copied: list[Path] = [target_cdo]
    for src in dylibs:
        dst = bundle_dir / src.name
        if dst.exists():                       # dedupe by basename
            continue
        shutil.copy2(src, dst)
        dst.chmod(0o755)
        copied.append(dst)

    # 3. Magics' runtime data, which otool cannot see and the loader will not
    #    find. Skipped silently when this CDO has no MAGICS — that is a valid
    #    build, and ``check_cdo_capabilities`` is what decides whether it is an
    #    acceptable one.
    #
    #    **Before the rewrite, and resolved from the original binary**, both of
    #    which are load-bearing. ``_rewrite_install_names`` turns every load
    #    reference into ``@loader_path/…`` and ``_otool_deps`` skips anything
    #    starting with ``@`` — so after that pass the copied binary's closure is
    #    empty and libMagPlus cannot be found by walking it. Doing this second
    #    silently bundled no data at all while still reporting success, which is
    #    the failure the smoke test exists to catch and which is cheaper to not
    #    write in the first place.
    data_mb = _bundle_magics_data(cdo, bundle_dir)

    # 4. Rewrite all install names + re-sign.
    log.info("Rewriting install names for %d binaries", len(copied))
    _rewrite_install_names(bundle_dir, copied)

    total_mb = sum(f.stat().st_size for f in copied) / (1024 * 1024)
    log.info("cdo_bundle/: %d files, %.1f MB (+ %.1f MB Magics data)",
             len(copied), total_mb, data_mb)
    return True


#: Magics data the bundle deliberately leaves out, as directory names under
#: ``share/magics``.
#:
#: ``10m`` is the 1:10,000,000 coastline set and is **61 MB of the 72 MB**
#: directory. It is dropped because CDO cannot ask for it: the Magplot,
#: Magvector and Maggraph parameter tables expose no coastline-resolution
#: option, so nothing a user can type through this application selects it.
#:
#: Verified rather than assumed, because "probably unused" is how data files go
#: missing. Five plots were rendered against the full directory and against a
#: copy with ``10m`` removed — shaded PNG, contour PostScript, shaded under
#: robinson and under polar_stereographic, and a graph — and every pair came out
#: **byte-identical** once the PostScript ``%%CreationDate:`` line (the only
#: non-deterministic bytes) was stripped. Bundle size 72 MB -> 10 MB.
#:
#: If a future CDO gains a coastline-resolution parameter, this is the line to
#: delete.
_MAGICS_DATA_EXCLUDE = ("10m",)


def _magics_share_dir(cdo: Path) -> Path | None:
    """Locate the ``share/magics`` directory the bundled libMagPlus needs.

    Found by walking the dylib closure for ``libMagPlus`` and going up from its
    install location, rather than by asking Homebrew: the build host may have
    provisioned CDO from somewhere else entirely, and a hard-coded
    ``/usr/local/opt/magics`` would produce a bundle that works on the build
    machine and nowhere else — which is the exact failure this whole function
    exists to prevent.
    """
    for dep in _collect_dylib_closure(cdo):
        if not dep.name.startswith("libMagPlus"):
            continue
        # <prefix>/lib/libMagPlus.dylib -> <prefix>/share/magics
        share = dep.parent.parent / "share" / "magics"
        if share.is_dir():
            return share
    return None


def _bundle_magics_data(cdo: Path, bundle_dir: Path) -> float:
    """Copy Magics' runtime data into the bundle. Returns the MB copied.

    ``otool`` sees libraries and nothing else, so this is the part of the
    dependency closure that a dylib walk structurally cannot find: coastlines,
    fonts, colour tables and style definitions that Magics opens by *path* at
    render time. Without them the bundle silently depends on a Homebrew prefix
    that is not present on a user's machine.

    "Silently" is measured and is the reason the smoke test checks a file size
    rather than a file's existence. With ``MAGPLUS_HOME`` pointing somewhere
    wrong, a PNG plot is not produced at all, but a PostScript plot **still
    writes a file** — 8 KB instead of 104 KB. It exits 0. Anything checking only
    that an output appeared would pass a build whose plots have no coastlines.

    The directory is staged as ``cdo_bundle/magics/share/magics`` so that
    ``MAGPLUS_HOME`` can point at ``cdo_bundle/magics``; Magics appends
    ``share/magics`` itself. ``nc_integration.run_environment`` sets that
    variable for the bundled binary — the layout here and the variable there are
    one decision in two files and must change together.
    """
    share = _magics_share_dir(cdo)
    if share is None:
        log.info("No libMagPlus in the closure — skipping Magics data "
                 "(this CDO has no MAGICS support)")
        return 0.0

    target = bundle_dir / "magics" / "share" / "magics"
    target.parent.mkdir(parents=True, exist_ok=True)
    log.info("Bundling Magics runtime data from %s", share)
    shutil.copytree(
        share, target,
        ignore=shutil.ignore_patterns(*_MAGICS_DATA_EXCLUDE),
        symlinks=True,
    )

    copied = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
    log.info("Magics data: %.1f MB (excluding %s)",
             copied / (1024 * 1024), ", ".join(_MAGICS_DATA_EXCLUDE))
    return copied / (1024 * 1024)


# ---------------------------------------------------------------------------
# macOS — dylib basename-collision repair
# ---------------------------------------------------------------------------
#: How ``nm`` spells a SQLite C symbol. Mach-O prefixes every C name with an
#: underscore, in ``nm -u`` (what a binary imports) and ``nm -gU`` (what it
#: exports) alike, so the two outputs are directly comparable. Matching on the
#: prefix is what separates SQLite's symbols from the 254 ``_Py*`` ones a
#: CPython extension also imports.
_SQLITE_SYMBOL_PREFIX = "_sqlite3_"


def _nm_names(binary: Path, *flags: str) -> set[str] | None:
    """Symbol names ``nm`` reports for ``binary``, or None when it cannot be asked.

    None rather than an empty set, because the two questions asked below point
    in opposite directions and each caller has to read its own answer: "imports
    nothing" is a pass and "exports nothing" is a failure, so a probe that never
    ran must not be silently taken for either.

    ``nm -gU`` prints ``<address> <type> <name>`` and ``nm -u`` a bare name;
    both put the name last. A fat binary's ``(for architecture …)`` banner
    survives as a token no symbol comparison can match, which is why it is left
    rather than filtered.
    """
    try:
        out = subprocess.check_output(
            ["nm", *flags, str(binary)], text=True, stderr=subprocess.DEVNULL
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    return {line.split()[-1] for line in out.splitlines() if line.strip()}


def repair_sqlite_collision(app_path: Path) -> bool:
    """Prove the bundled ``_sqlite3`` extension can resolve SQLite at runtime.

    Four wheels in this dependency set (rasterio, fiona, pyproj, plus anaconda's
    own) each vendor their own ``libsqlite3.0.dylib``.  PyInstaller flattens
    binaries into ``Contents/Frameworks`` keyed by *basename*, so the four
    collide; it keeps one and symlinks the top-level name at it.  Which one wins
    is incidental, and it can lose: it picks rasterio's, which is older than the
    ``_sqlite3`` of a CPython that links SQLite dynamically requires.  That
    extension loads ``@rpath/libsqlite3.0.dylib``, resolves through the symlink,
    and ``import sqlite3`` dies with

        Symbol not found: _sqlite3_deserialize

    and every import that reaches ``sqlite3`` goes with it — for NCExplorer that
    is ``geocanvas/offline_basemap.py`` (MBTiles), hence the canvas, hence the
    main window.  The build's ``--version`` smoke test never catches this
    because it returns before any of that is imported.

    **What this asks, and why it is no longer what it used to ask.** It used to
    test the bundled ``libsqlite3.0.dylib`` for one symbol and fail the build
    when no copy exported it.  That is a question about the dylibs; whether
    ``import sqlite3`` works is a question about the *extension module*, and on
    a Python whose ``_sqlite3`` links SQLite statically the two have no
    connection at all.  Measured on the python.org 3.13 framework build this
    project freezes with: ``_sqlite3.cpython-313-darwin.so`` imports **0**
    ``sqlite3_*`` symbols and exports **275** of them, and links nothing but
    ``libSystem``.  It never loads a bundled copy — and none of the four the
    bundle carries exports ``sqlite3_deserialize``, so the old check failed that
    build every time.  An ERROR that is wrong on the build you actually ship is
    worse than no check: it is the only ERROR in an otherwise clean run, and it
    teaches its reader to ignore errors.

    So the order of questions is now what the module imports, where dyld will
    find it, and whether that file exports everything the module asked for.
    Homebrew's ``python@3.13`` is the other specimen and the one the check
    exists for: its ``_sqlite3`` imports **88** ``sqlite3_*`` symbols,
    ``sqlite3_deserialize`` among them, from
    ``/usr/local/opt/sqlite/lib/libsqlite3.dylib`` — which, frozen, becomes an
    ``@rpath`` reference into the collision above.

    Returns True when the bundle ends up with a usable SQLite, and also when the
    question could not be answered — the rule the rest of this file follows for
    probes, since a build must not fail on an unreadable ``nm``.
    """
    modules = sorted(app_path.rglob("_sqlite3*.so"))
    if not modules:
        log.debug("No _sqlite3 extension in the bundle; `import sqlite3` is not "
                  "shipped and there is nothing to check.")
        return True

    # A list rather than a generator inside all(): this is a diagnostic, and
    # short-circuiting would report the first bad module and stay silent about
    # the rest. There is normally exactly one.
    return all([_sqlite_module_is_satisfiable(module, app_path)
                for module in modules])


def _sqlite_module_is_satisfiable(module: Path, app_path: Path) -> bool:
    """One ``_sqlite3`` extension: can dyld resolve every SQLite symbol it imports?

    Repairs the one failure that is repairable — a basename collision that left
    the reference resolving onto a copy too old — by re-pointing it at a bundled
    copy that does export everything.  SQLite is strongly backwards compatible,
    so a wheel that reaches the newer library through the same name is
    unaffected; see :func:`repair_sqlite_collision` for what produces the state.
    """
    imported = _nm_names(module, "-u")
    if imported is None:
        log.warning("Could not read %s's imported symbols — not blocking the "
                    "build on an unanswerable question.", module.name)
        return True

    needed = {name for name in imported
              if name.startswith(_SQLITE_SYMBOL_PREFIX)}
    if not needed:
        # "exported" rather than "defined": nm -gU counts the externally
        # visible ones only, so this is a floor on what the module carries and
        # not a count of SQLite's whole symbol table.
        exports = _nm_names(module, "-gU") or set()
        log.info("%s links SQLite statically (%d sqlite3 symbols exported, 0 "
                 "imported) — no bundled libsqlite3 is on its load path, so "
                 "none can break `import sqlite3`.",
                 module.name,
                 sum(1 for name in exports
                     if name.startswith(_SQLITE_SYMBOL_PREFIX)))
        return True

    # Where dyld will actually land, ``@rpath`` and all — see _resolve_ref.
    # _otool_deps drops system references, so no match here means macOS's own
    # libsqlite3 from the shared cache: present on every Mac, and not something
    # this bundle can get wrong.
    provider = next((dep for dep in _otool_deps(module)
                     if dep.name.startswith("libsqlite3")), None)
    if provider is None:
        log.info("%s imports %d sqlite3 symbols and resolves them against the "
                 "system SQLite — nothing bundled to check.",
                 module.name, len(needed))
        return True

    exported = _nm_names(provider, "-gU")
    if exported is None:
        log.warning("Could not read the exports of %s — not blocking the build "
                    "on an unanswerable question.", provider.name)
        return True

    missing = needed - exported
    if not missing:
        log.info("%s resolves all %d of its sqlite3 symbols against %s.",
                 module.name, len(needed), provider.name)
        return True

    log.warning(
        "%s resolves to %s, which lacks %d of the %d sqlite3 symbols it "
        "imports (%s) — `import sqlite3` would fail at runtime.  Looking for a "
        "newer copy.",
        module.name, provider.name, len(missing), len(needed),
        _first_few(missing),
    )

    frameworks = app_path / "Contents" / "Frameworks"
    for candidate in sorted(frameworks.rglob("libsqlite3*.dylib")):
        if candidate == provider or candidate.is_symlink():
            continue
        if needed - (_nm_names(candidate, "-gU") or set()):
            continue

        # Replacing the reference's target with a symlink, which is what the
        # collision produced in the first place and therefore what the loader
        # already expects to find at that name.
        target = os.path.relpath(candidate, provider.parent)
        provider.unlink()
        provider.symlink_to(target)
        log.info("Re-pointed %s -> %s", provider.name, target)
        return True

    log.error(
        "No bundled libsqlite3 exports the %d sqlite3 symbols %s imports "
        "(missing %s).  The packaged app will fail on `import sqlite3`.  Check "
        "that the build environment's SQLite is recent enough for CPython %s.",
        len(needed), module.name, _first_few(missing),
        f"{sys.version_info.major}.{sys.version_info.minor}",
    )
    return False


def _first_few(symbols: set[str], limit: int = 3) -> str:
    """A few symbol names for a log line, with a count when there are more."""
    listed = sorted(symbols)
    if len(listed) <= limit:
        return ", ".join(listed)
    return f"{', '.join(listed[:limit])} and {len(listed) - limit} more"


_MACHO_MAGICS = frozenset({
    b"\xcf\xfa\xed\xfe",     # 64-bit little-endian
    b"\xce\xfa\xed\xfe",     # 32-bit little-endian
    b"\xfe\xed\xfa\xcf",     # 64-bit big-endian
    b"\xfe\xed\xfa\xce",     # 32-bit big-endian
    b"\xca\xfe\xba\xbe",     # fat
    b"\xbe\xba\xfe\xca",     # fat, byte-swapped
})


def _is_macho(path: Path) -> bool:
    """True when ``path`` starts with a Mach-O (or fat) magic number."""
    try:
        with path.open("rb") as fh:
            return fh.read(4) in _MACHO_MAGICS
    except OSError:
        return False


def _codesign(paths: list[str], *, chunk: int = 200) -> int:
    """Ad-hoc sign every path; returns the number of failed invocations."""
    failures = 0
    for start in range(0, len(paths), chunk):
        batch = paths[start:start + chunk]
        result = subprocess.run(
            ["/usr/bin/codesign", "--force", "--sign", "-", *batch],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            failures += 1
            log.warning("codesign failed for a batch of %d: %s",
                        len(batch), result.stderr.strip().splitlines()[-1:] or "?")
    return failures


def codesign_app(app_path: Path) -> bool:
    """Ad-hoc codesign the .app inside-out, then verify the seal.

    Staging cdo_bundle/ under Contents/Resources and re-pointing the SQLite
    symlink both invalidate PyInstaller's own seal, so the bundle has to be
    re-signed.  This deliberately does *not* use ``--deep``:

    * Apple has deprecated it — ``codesign`` warns, and it is slated for
      removal — and it was never meant for signing something you built, only
      for inspecting or fixing up something you received.
    * It signs outside-in.  A bundle's signature seals the nested code it
      contains, so a nested Mach-O signed *after* its enclosing bundle leaves
      the outer seal describing content that no longer matches.  On a bundle
      modified after PyInstaller ran — which this one is — that yields a
      signature that ``codesign --verify --strict`` rejects.

    Signing inside-out (deepest nested code first, the .app last) means every
    seal is computed over content that is already final.  Nested frameworks are
    signed as whole bundles rather than as loose Mach-Os, because a
    ``.framework`` is itself a code bundle with its own seal.

    No Apple Developer ID is involved: this is an ad-hoc signature, which is
    enough for the app to run locally but *not* enough to survive the
    quarantine flag on a downloaded copy — see the README for the
    ``xattr -dr com.apple.quarantine`` the recipient needs.

    Returns True when both verification passes succeed.
    """
    log.info("Ad-hoc codesigning %s (inside-out)", app_path.name)

    frameworks = sorted(
        (p for p in app_path.rglob("*.framework") if p.is_dir() and not p.is_symlink()),
        key=lambda p: len(p.parts), reverse=True,
    )
    framework_parts = {p for fw in frameworks for p in (fw,)}

    def _inside_a_framework(path: Path) -> bool:
        return any(fw in path.parents for fw in framework_parts)

    # Loose nested Mach-Os: everything that is not inside a .framework (those
    # get signed as bundles below) and is not the bundle's own entry binary
    # (that one is sealed by signing the .app itself).
    main_binary = app_path / "Contents" / "MacOS" / APP_NAME
    loose: list[Path] = []
    for path in app_path.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        if path == main_binary or _inside_a_framework(path):
            continue
        if _is_macho(path):
            loose.append(path)

    # Deepest first, so an inner dylib is final before anything sealing it.
    loose.sort(key=lambda p: len(p.parts), reverse=True)

    log.info("Signing %d nested Mach-O files and %d frameworks",
             len(loose), len(frameworks))
    failures = _codesign([str(p) for p in loose])
    failures += _codesign([str(p) for p in frameworks])

    # The .app last: its seal now covers content that is already signed.
    result = subprocess.run(
        ["/usr/bin/codesign", "--force", "--sign", "-", str(app_path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        log.error("codesign of the bundle failed: %s", result.stderr.strip())
        return False
    if failures:
        log.warning("%d nested signing batch(es) reported errors.", failures)

    return verify_signature(app_path)


def verify_signature(app_path: Path) -> bool:
    """Run the two checks that decide whether the .app is actually loadable."""
    ok = True

    verify = subprocess.run(
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", "--verbose=2",
         str(app_path)],
        capture_output=True, text=True,
    )
    if verify.returncode == 0:
        log.info("codesign --verify --deep --strict: PASS")
    else:
        ok = False
        log.error("codesign --verify --deep --strict: FAIL\n%s",
                  verify.stderr.strip())

    # spctl is the Gatekeeper assessment.  An ad-hoc signature is *expected* to
    # be rejected here on a machine with the default policy — that is the whole
    # reason recipients need to strip the quarantine xattr — so a rejection is
    # reported without failing the build.
    spctl = subprocess.run(
        ["/usr/sbin/spctl", "-a", "-t", "exec", "-vv", str(app_path)],
        capture_output=True, text=True,
    )
    output = (spctl.stdout + spctl.stderr).strip()
    if spctl.returncode == 0:
        log.info("spctl -a -t exec: ACCEPTED\n%s", output)
    else:
        log.warning(
            "spctl -a -t exec: rejected (expected for an ad-hoc, unnotarized "
            "signature)\n%s", output,
        )

    return ok


def create_dmg(app_path: Path) -> Path | None:
    """Wrap the .app in a compressed disk image for distribution.

    Prefers ``create-dmg`` (homebrew) for a nicer layout with an Applications
    symlink; falls back to ``hdiutil`` if create-dmg is unavailable.
    """
    dmg_path = DIST / f"{APP_NAME}-{APP_VERSION}-macos.dmg"
    if dmg_path.exists():
        dmg_path.unlink()

    if shutil.which("create-dmg"):
        log.info("Building DMG with create-dmg → %s", dmg_path.name)
        cmd = [
            "create-dmg",
            "--volname", f"{APP_NAME} {APP_VERSION}",
            "--window-size", "540", "380",
            "--icon-size", "128",
            "--icon", f"{APP_NAME}.app", "140", "190",
            "--app-drop-link", "400", "190",
            "--no-internet-enable",
            str(dmg_path),
            str(app_path),
        ]
        rc = subprocess.run(cmd).returncode
        if rc == 0 and dmg_path.exists():
            log.info("DMG created: %s (%.1f MB)",
                     dmg_path.relative_to(ROOT),
                     dmg_path.stat().st_size / (1024 * 1024))
            return dmg_path
        log.warning("create-dmg exit %d — falling back to hdiutil.", rc)
        if dmg_path.exists():
            dmg_path.unlink()

    log.info("Building DMG with hdiutil → %s", dmg_path.name)
    # Stage the .app + Applications symlink in a temp dir so the DMG opens
    # with a familiar drag-to-install layout.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="ncexplorer-dmg-") as staging:
        stage = Path(staging)
        shutil.copytree(app_path, stage / app_path.name, symlinks=True)
        (stage / "Applications").symlink_to("/Applications")
        rc = subprocess.run(
            [
                "hdiutil", "create",
                "-volname", f"{APP_NAME} {APP_VERSION}",
                "-srcfolder", str(stage),
                "-ov",
                "-format", "UDZO",        # zlib-compressed
                str(dmg_path),
            ],
            capture_output=True, text=True,
        ).returncode
    if rc != 0 or not dmg_path.exists():
        log.error("hdiutil failed to produce %s", dmg_path)
        return None

    log.info("DMG created: %s (%.1f MB)",
             dmg_path.relative_to(ROOT),
             dmg_path.stat().st_size / (1024 * 1024))
    return dmg_path


def write_dmg_checksum(dmg_path: Path) -> None:
    """Sidecar SHA256 for the DMG itself (separate from the .app hash)."""
    h = hashlib.sha256()
    with dmg_path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    sidecar = dmg_path.with_suffix(dmg_path.suffix + ".sha256")
    sidecar.write_text(f"{h.hexdigest()}  {dmg_path.name}\n", encoding="utf-8")
    log.info("DMG SHA256: %s", h.hexdigest())
    log.info("Wrote DMG checksum: %s", sidecar.relative_to(ROOT))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="build.py",
        description=f"Build {APP_NAME} {APP_VERSION} for the current platform.",
    )
    p.add_argument("--clean-only",  action="store_true",
                   help="Remove dist/build/spec artefacts and exit.")
    p.add_argument("--skip-clean",  action="store_true",
                   help="Reuse existing build/ for an incremental rebuild.")
    p.add_argument("--debug",       action="store_true",
                   help="Build with --console (visible tracebacks).")
    p.add_argument("--no-smoke-test", action="store_true",
                   help="Skip the post-build artefact invocation.")
    p.add_argument("--no-checksum", action="store_true",
                   help="Skip SHA256 sidecar generation.")
    p.add_argument("--no-installer", action="store_true",
                   help="Never offer Inno Setup, even on Windows.")
    p.add_argument("--allow-unbuilt-cdo", action="store_true",
                   help="(deprecated, now the default) ship even if the CDO to "
                        "be bundled lacks MAGICS")
    p.add_argument("--require-magics", action="store_true",
                   help="fail the build unless the CDO to be bundled has "
                        "MAGICS — for release builds")
    p.add_argument("--no-cdo-bundle", action="store_true",
                   help="(macOS) Skip bundling the system cdo binary into the .app.")
    p.add_argument("--no-dmg", action="store_true",
                   help="(macOS) Skip packaging the .app into a .dmg.")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Verbose logging (DEBUG level).")
    return p.parse_args()


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    args = parse_args()
    configure_logging(args.verbose)

    log.info("=" * 60)
    log.info("%s %s — build for %s", APP_NAME, APP_VERSION, platform_label())
    log.info("=" * 60)

    if args.clean_only:
        clean_build()
        return 0

    if not args.skip_clean:
        clean_build()

    if not check_shapefile_engine():
        log.error("Refusing to build an app that cannot read vector files.")
        return 1

    # Before pyinstaller rather than after, so a --require-magics build on a
    # host without one fails in seconds instead of after a full freeze — and so
    # the warning about what the bundle will lack is read before the wait
    # rather than after it. The bundled CDO is the one every user gets; see
    # check_cdo_capabilities.
    if not args.no_cdo_bundle:
        strict = args.require_magics and not args.allow_unbuilt_cdo
        if not check_cdo_capabilities(required=strict):
            return 1

    run_pyinstaller(debug=args.debug)

    art = primary_artefact()
    if not art.exists():
        log.error("Expected artefact not produced: %s", art)
        return 1

    # macOS post-pyinstaller: inject CDO and re-sign before the smoke test, so
    # the test exercises the same artefact users will run.
    dmg_path: Path | None = None
    if sys.platform == "darwin":
        if not args.no_cdo_bundle:
            bundle_cdo_into_app(art)
        # Must precede signing: it rewrites a symlink inside Contents/Frameworks.
        repair_sqlite_collision(art)
        codesign_app(art)

    verify_branding(art)

    size_mb = (
        sum(f.stat().st_size for f in art.rglob("*") if f.is_file())
        if art.is_dir() else art.stat().st_size
    ) / (1024 * 1024)
    log.info("Artefact: %s  (%.1f MB)", art.relative_to(ROOT), size_mb)

    if not args.no_smoke_test and not smoke_test():
        log.error("Smoke test FAILED — artefact left in place for inspection.")
        return 1

    if not args.no_checksum:
        write_checksum()

    if sys.platform == "darwin" and not args.no_dmg:
        dmg_path = create_dmg(art)
        if dmg_path and not args.no_checksum:
            write_dmg_checksum(dmg_path)

    if sys.platform == "win32" and not args.no_installer:
        try:
            response = input("\nCreate Windows installer with Inno Setup? (y/n): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            response = "n"
        if response == "y":
            create_installer()

    log.info("=" * 60)
    log.info("BUILD COMPLETE  ·  %s", art.relative_to(ROOT))
    if dmg_path and dmg_path.exists():
        log.info("Disk image: %s", dmg_path.relative_to(ROOT))
    if sys.platform == "darwin":
        # The single most common support question for an ad-hoc signed app, so
        # the answer ships with the build output rather than only in the README.
        log.info("")
        log.info("NOTE  This .app is ad-hoc signed and NOT notarized (there is")
        log.info("      no Developer ID certificate for this project).  A copy")
        log.info("      that is *downloaded* carries a quarantine flag, and")
        log.info("      macOS then reports it as \"damaged and can't be opened\".")
        log.info("      It is not damaged.  Recipients clear the flag with:")
        log.info("")
        log.info("          xattr -dr com.apple.quarantine /Applications/%s.app",
                 APP_NAME)
        log.info("")
    out_dir = ROOT / "installer_output"
    if out_dir.exists():
        for f in sorted(out_dir.glob("*.exe")):
            log.info("Installer: %s", f.relative_to(ROOT))
    log.info("=" * 60)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log.warning("Build cancelled by user.")
        sys.exit(1)
    except Exception as exc:                  # noqa: BLE001
        log.exception("Unexpected error: %s", exc)
        sys.exit(1)
