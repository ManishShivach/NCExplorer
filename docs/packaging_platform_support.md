# Which platforms NCExplorer can ship on

What each platform's build actually produces, measured rather than intended.

**Measured:** 2026-08-18, against the 1.0.0 artefacts in `dist/` (built
2026-08-16 on macOS arm64, Python 3.13.6) and against `build.py` as it stands
today. Numbers in the earlier version of this note were taken against 0.2.0 and
no longer hold.

## Summary

| Platform | What `python build.py` produces today | Verdict |
|---|---|---|
| macOS arm64 | `NCExplorer.app` (882 MB) and a 387 MB `.dmg`, with a MAGICS-enabled CDO inside | **Supported.** The only artefact that contains everything it needs. |
| macOS x86_64 | Never built here | Same code path; needs an Intel host or a cross build. |
| Linux x86_64 | A one-file binary with no CDO in it | Build-from-source. Three known gaps, listed below. |
| Windows | A one-file `.exe` plus an Inno Setup installer that provisions CDO inside WSL | **Written, never executed on Windows.** |

## What changed since this note was first written

The first version recommended dropping Windows. That is no longer the plan:
the installer (`installer/setup_script.iss`), the provisioning script
(`installer/provision_cdo_wsl.sh`) and `core/cdo_repair.py` now exist, and
between them install a MAGICS-enabled CDO inside a WSL distro and give the user
three ways back to that step if it fails.

The reason for the original recommendation has not changed, though. CDO is not
distributed as a native Windows binary — the supported routes are WSL and
Cygwin — so every operator invocation on Windows crosses the WSL boundary, with
`_win_to_wsl` translating each file argument. What the installer buys is that
the user no longer has to know that. What it cannot buy is a self-contained
"download and run" Windows artefact.

## macOS: the only self-contained target

`bundle_cdo_into_app` copies CDO plus the closure of its dynamic libraries into
`Contents/Resources/cdo_bundle/` and rewrites the install names, so the app uses
its own CDO rather than whatever is on `PATH`. This matters most when the app is
launched from Finder, where Homebrew's `PATH` is typically absent.

Measured on the shipped 1.0.0 bundle:

| | |
|---|---|
| `Contents/Frameworks` | 506 MB |
| `Contents/Resources` | 328 MB, of which `cdo_bundle` is 122 MB |
| `Contents/MacOS/NCExplorer` | 45 MB |
| Whole `.app` | 882 MB on disk, 387 MB compressed in the `.dmg` |

The bundled CDO answers `has-magics:yes` and `has-cmor:no`, which matches what
the README promises. `codesign --verify --deep --strict` passes on the bundle as
it stands.

## Windows: implemented, not yet run

`cdo_repair.py` and `setup_script.iss` both carry a banner saying the WSL
handling was written on macOS and has never been executed on Windows. The four
cases the `.iss` header lists are the ones to run before this is offered to
anyone: a machine with no WSL at all, one with WSL 1 only, one with
virtualization disabled in firmware, and one already fully provisioned, where
the installer must do nothing. The exit codes of `wsl --status` are the part
most likely to be wrong, because they were taken from documentation rather than
from a machine.

One concrete defect, visible without a Windows box: `setup_script.iss` still
declares `AppVersion 0.5.0`, while `setup.py` says `1.0.0`. The installer would
be named `NCExplorer-0.5.0-windows-setup.exe` and would register that version in
Add/Remove Programs. The version is defined in two places, which is why they
have drifted.

## Linux: three things to do first

Nothing here is hard; none of it is done.

1. **Generalise the CDO bundling to ELF.** `bundle_cdo_into_app` is called only
   under `sys.platform == "darwin"`, so a Linux build ships no CDO. The
   dylib-closure walk (`otool -L` and `install_name_tool`) needs an ELF
   equivalent (`ldd` and `patchelf --set-rpath '$ORIGIN'`). The code is
   structured so this replaces `_otool_deps` and `_rewrite_install_names`
   rather than requiring a rewrite.
2. **Aim the one-file CDO probe at the extraction directory.**
   `_bundled_cdo_path` looks for `cdo_bundle/` next to `sys.executable`. Under
   `--onefile`, `sys.executable` is the self-extracting launcher, not the
   extraction directory (`sys._MEIPASS`), so the probe inspects a directory the
   build never writes to. Harmless today, because nothing is bundled there
   either; it becomes a bug the moment step 1 lands.
3. **Switch Linux to `--onedir`.** macOS is on `--onedir` because the `.app`
   layout requires it, and everything else is on `--onefile`. Linux gains
   nothing from a single file and pays the per-launch extraction cost described
   next.

## Cold start on the one-file platforms

`--onefile` appends the whole bundle to the launcher and re-extracts it to a
temporary directory on **every** launch. At the sizes measured above that is
seconds to tens of seconds on a cold page cache, every time.

`gui/splash.py` covers the second half of that wait: `main.py` shows the splash
before the heavy imports and steps it through them — operator catalogue, map
libraries, interface, canvas. It cannot cover the first half. Extraction happens
in the PyInstaller bootloader, before Python exists, and the only thing that can
draw during that phase is PyInstaller's own `--splash`, which this build does
not use for three reasons: it needs Tcl/Tk on the build host, so a build box
without it turns a cosmetic feature into a failed build; it is unavailable on
macOS, so it would only help the platforms that are already worst off; and it
must be closed explicitly with `pyi_splash.close()`, so any startup path that
misses the call leaves a splash on screen with no owner.

macOS uses `--onedir` and has no extraction phase, so its splash appears about a
second after launch.

## The size the build reports is too large

`main()` measures the artefact with
`sum(f.stat().st_size for f in art.rglob("*") if f.is_file())`. `Path.is_file()`
follows symlinks, and PyInstaller fills `Contents/Frameworks` with symlinks into
`Contents/Resources`, so every cross-linked file is counted twice. On the 1.0.0
bundle that reports **1517.1 MB** against **854.4 MB** of real files, and `du`
agrees with the smaller number. Adding `and not f.is_symlink()` fixes it. This
is a reporting bug, not bloat.

## Where the 854 MB goes

Largest components, measured with `du`. Most packages are split across
`Contents/Frameworks` and `Contents/Resources`, with a symlink joining the
halves; the sizes below add the two real directories and count nothing twice.

| Component | Size | Needed? |
|---|---|---|
| PyQt6 | 187 MB | yes |
| `cdo_bundle` (CDO, MAGICS, its dylibs) | 122 MB | yes — the reason macOS works offline |
| **llvmlite** | 86 MB | **no** |
| scipy | 75 MB | yes |
| rasterio | 57 MB | yes |
| fiona | 45 MB | yes, the only vector I/O engine (`pyogrio` is excluded) |
| eccodeslib | 42 MB | unclear — nothing in the source imports it |
| **babel** | 32 MB | **no** |
| cartopy and its Natural Earth data | 28 MB | yes |
| **botocore** | 23 MB | **no** |
| pandas | 20 MB | yes |
| **numba** | 2 MB | **no** |

The four marked "no" appear nowhere in `requirements.txt` and nowhere in
`ncexplorer_toolkit/` or `main.py`. They arrive through the import graph of the
environment the build runs in: `build/NCExplorer/Analysis-00.toc` collects 344
`numba` modules, 171 `dask` modules, 1082 `babel` and 1919 `botocore`, none of
which the source ever imports. Which optional dependency pulls each one in has
not been traced — `--exclude-module` does not need to know.

### Reductions, in descending confidence — none applied

Each needs its own build-and-launch check, because the failure mode for
over-excluding is a runtime `ImportError` that the `--version` smoke test cannot
see.

1. **Exclude `llvmlite`, `numba`, `babel`, `botocore` (~143 MB).** Same class of
   exclusion the build already applies to tensorflow, torch and jax.
2. **Ship only `ncexplorer_toolkit/resources` rather than the whole package
   (~6 MB).** `gather_data_files` adds the entire source tree with `--add-data`
   on top of the PYZ that already holds every module. `resources_root()` needs
   only the `resources/` subtree, for the SVG icons.
3. **Decide whether both fiona and rasterio must ship (102 MB between them).**
   Each vendors its own GDAL, PROJ and SQLite. Consolidating is real work and
   may not pay for itself, but it is where the remaining weight is.

### The duplicated source tree also ships the developer's bytecode

`Contents/Resources/ncexplorer_toolkit` holds 66 `.py` files and 124 `.pyc`
files. Sixty of those `.pyc` were compiled by Python 3.12 — an interpreter the
bundle does not contain — and were copied straight out of the checkout's
`__pycache__` directories by `--add-data`. They are inside the signature:
`_CodeSignature/CodeResources` seals all 124.

The earlier version of this note recorded a related problem: the app
byte-compiled `.pyc` files into `Contents/Frameworks/rasterio` at first launch
and so invalidated its own signature. No trace of that survives on 1.0.0 —
`Frameworks/rasterio` has no `__pycache__`, and the sealed file count matches
what is on disk, so nothing has been added since signing. That is weaker
evidence than it looks, because the only thing to have run this bundle is the
build's smoke test, and `--version` exits before Qt is imported. A full launch
has not been checked. The condition that produced the original problem is
unchanged either way: loose `.py` files ship inside the signed bundle, and
`PYTHONDONTWRITEBYTECODE` is still not set in the frozen entry point. Narrowing
`--add-data` (reduction 2) removes both the stale bytecode and the risk.

## The build host quietly decides what the app can do

`build.py` bundles whatever is installed in the environment it runs in, and
never checks that environment against `requirements.txt`. Four packages that
`requirements.txt` declares were missing from the machine that built 1.0.0, so
they are missing from the artefact. PyInstaller's own table of contents,
`build/NCExplorer/PYZ-00.toc`, records zero entries for each:

| Absent package | What the user loses |
|---|---|
| `imageio` | The whole animation export. The button reports `Export needs the 'imageio' package — pip install imageio`. |
| `imageio-ffmpeg` | MP4, separately from the above: with `imageio` present but this one absent, the save dialog offers GIF only. |
| `cmocean` | The `cmo.` colormap group. |
| `cmcrameri` | The `cmc.` colormap group. |

The two colormap packages are declared optional, and their absence is handled —
`geocanvas/colormaps.py` omits those groups and the app runs normally. The
imageio pair is different: the advice `time_player.py` gives is correct when
running from source and impossible to act on inside a frozen `.app`, where there
is no `pip` to run.

The fix belongs in the build, not in the runtime: `check_requirement.py` already
knows how to report which dependencies are importable, but `build.py` never runs
it, and PyInstaller cannot warn about a package it was never asked to collect. A
preflight that compares the build environment against `requirements.txt` and
refuses — or at least says loudly what the artefact will be missing — would have
caught all four.

## Recommendation

**Keep macOS as the supported platform.** It is the only build where the
artefact contains everything it needs, including a CDO that can plot.

**Finish Windows or say it is unfinished.** The code exists and the design is
sound, but nothing in it has run on Windows, and the installer version is
already wrong. Either run the four cases in the `.iss` header and fix what they
find, or state in the README that the Windows installer is untested.

**Treat Linux as build-from-source** until the three steps above are done.

**Add a build-environment preflight before the next release.** The 1.0.0 macOS
artefact silently lost animation export, and nothing in the build or the smoke
test noticed.
