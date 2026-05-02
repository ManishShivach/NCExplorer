"""
Build script for NCExplorer.

Pipeline (per invocation):
    clean -> pyinstaller -> smoke-test -> sha256 -> (optional) installer

Cross-platform artefact paths:
    Windows : dist/NCExplorer.exe          (one-file)
    Linux   : dist/NCExplorer              (one-file ELF)
    macOS   : dist/NCExplorer.app          (bundle; inner Mach-O hashed)

Single source of truth for version + name comes from
ncexplorer_toolkit/__version__.py — never duplicated here.

Usage:
    python build.py                      # full pipeline (auto-detect platform)
    python build.py --clean-only         # remove dist/build only
    python build.py --skip-clean         # incremental rebuild
    python build.py --debug              # console window + tracebacks
    python build.py --no-smoke-test      # skip artefact invocation
    python build.py --no-checksum        # skip SHA256 generation
    python build.py --no-installer       # never offer Inno Setup
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import logging
import os
import runpy
import shutil
import subprocess
import sys
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

    for pattern in ("*.spec", "*.egg-info"):
        for path in glob.glob(str(ROOT / pattern)):
            try:
                p = Path(path)
                shutil.rmtree(p) if p.is_dir() else p.unlink()
                log.info("Cleaned: %s", p.name)
            except OSError as e:
                log.warning("Could not clean %s: %s", path, e)


# ---------------------------------------------------------------------------
# PyInstaller command assembly — split into focused helpers
# ---------------------------------------------------------------------------
def _add_data(src: str, dst: str) -> list[str]:
    return ["--add-data", f"{src}{DATA_SEP}{dst}"]


def _find_icon() -> list[str]:
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
    log.info("No icon found; building without --icon.")
    return []


def gather_data_files() -> list[str]:
    flags = list(_add_data(PACKAGE_NAME, PACKAGE_NAME))
    if (ROOT / "splash_screen.py").exists():
        flags += _add_data("splash_screen.py", ".")
    if (ROOT / "check_requirement.py").exists():
        flags += _add_data("check_requirement.py", ".")
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
    for sub in ("core", "geocanvas", "gui", "utils"):
        flags += ["--hidden-import", f"{PACKAGE_NAME}.{sub}"]
    return flags


def build_pyinstaller_command(*, debug: bool) -> list[str]:
    cmd: list[str] = [
        "pyinstaller",
        "--onefile",
        "--noconfirm",
        "--name", APP_NAME,
    ]
    cmd.append("--console" if debug else "--windowed")

    cmd += _find_icon()
    cmd += gather_data_files()
    # PyQt6: --collect-all already covers binaries, data, submodules, metadata
    cmd += ["--collect-all", "PyQt6"]
    cmd += gather_optional_imports([
        "cartopy", "xarray", "netCDF4", "geopandas", "shapely",
        "matplotlib", "numpy", "pandas", "rasterio", "h5netcdf",
        "matplotlib.backends.backend_qtagg",
        "matplotlib.backends.backend_qtcairo",
    ])
    cmd += gather_collect_alls(["rasterio", "cartopy", "scipy"])
    cmd += gather_internal_hidden_imports()
    cmd.append(ENTRY_POINT)
    return cmd


# ---------------------------------------------------------------------------
# Build runner
# ---------------------------------------------------------------------------
def run_pyinstaller(*, debug: bool) -> None:
    log.info("Building %s %s with PyInstaller...", APP_NAME, APP_VERSION)

    if not (ROOT / PACKAGE_NAME).is_dir():
        log.error("Package directory '%s' not found.", PACKAGE_NAME)
        sys.exit(1)
    if not (ROOT / ENTRY_POINT).is_file():
        log.error("Entry point '%s' not found.", ENTRY_POINT)
        sys.exit(1)

    cmd = build_pyinstaller_command(debug=debug)
    log.info("Command: %s", " ".join(cmd))

    rc = subprocess.run(cmd, cwd=ROOT).returncode
    if rc != 0:
        log.error("PyInstaller build failed (exit code %d).", rc)
        sys.exit(rc)
    log.info("PyInstaller build succeeded.")


# ---------------------------------------------------------------------------
# Smoke test — actually invoke the artefact
# ---------------------------------------------------------------------------
def smoke_test(*, timeout: int = 15) -> bool:
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

    run_pyinstaller(debug=args.debug)

    art = primary_artefact()
    if not art.exists():
        log.error("Expected artefact not produced: %s", art)
        return 1
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

    if sys.platform == "win32" and not args.no_installer:
        try:
            response = input("\nCreate Windows installer with Inno Setup? (y/n): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            response = "n"
        if response == "y":
            create_installer()

    log.info("=" * 60)
    log.info("BUILD COMPLETE  ·  %s", art.relative_to(ROOT))
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
