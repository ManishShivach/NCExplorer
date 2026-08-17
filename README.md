# NCExplorer 1.0.0

Cross-platform desktop GUI for climate data analysis — the complete CDO operator
catalog, an open-source geospatial canvas, and a reproducible record of every
run.

NCExplorer wraps the [Climate Data Operators (CDO)](https://code.mpimet.mpg.de/projects/cdo)
toolchain in a PyQt6 application. Load NetCDF, GeoTIFF or shapefile data, browse
it on a Cartopy-backed map, and run any of CDO's 943 operators interactively —
individually, wired into a processing graph, or swept across a whole folder —
without writing a shell pipeline or inventing a name for a single intermediate
file.

- **Author** — Manish Shivach (<iammanishshivach@gmail.com>)
- **License** — MIT
- **Repository** — <https://github.com/ManishShivach/NCproject>

---

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [The operator catalog](#the-operator-catalog)
- [Guardrails: the failures CDO does not report](#guardrails-the-failures-cdo-does-not-report)
- [Model builder](#model-builder)
- [Reproducibility](#reproducibility)
- [Batch processing](#batch-processing)
- [Analysis panels](#analysis-panels)
- [The map canvas](#the-map-canvas)
- [File formats](#file-formats)
- [What the operator form's choosers offer](#what-the-operator-forms-choosers-offer)
- [Which CDO build features NCExplorer ships with](#which-cdo-build-features-ncexplorer-ships-with)
- [Building the bundled CDO yourself](#building-the-bundled-cdo-yourself)
- [Building a standalone executable](#building-a-standalone-executable)
- [Testing the operators](#testing-the-operators)
- [The reference sweep](#the-reference-sweep)
- [Keyboard shortcuts](#keyboard-shortcuts)
- [Project structure](#project-structure)

---

## Features

- **The complete CDO 2.6.3 catalog** — all 943 operators from `cdo --operators`,
  exposed through a searchable, category-grouped interface with descriptions
  merged from the official CDO User Guide. The category menus, the toolbar
  search box, the command palette and the model builder's palette all offer the
  identical set, and `audit_operator_surfaces.py` proves it by walking the real
  menus rather than restating the code that builds them.
- **Seventeen categories** — Information, File operations, Selection,
  Conditional selection, Comparison, Modification, Arithmetic, Statistical
  values, Correlation, EOFs, Regression, Interpolation, Transformation,
  Import/Export, Graphics, Miscellaneous and ECA indices, each with its own
  toolbar glyph.
- **Forms derived from the schema, never tabulated.** Arity, parameters,
  outputs, options and environment for all 943 operators come from one operator
  schema, so a multi-input operator draws multiple input rows and a
  multi-output operator gets one destination per output.
- **Model builder** — wire operators into a processing graph, watch the CDO
  invocation assemble as you draw, and run the whole chain with no intermediate
  filenames. Branching and recombining are supported, so shapes a linear
  pipeline cannot express are drawable.
- **Reproducibility** — every run is recorded in a session log, saved into
  `.ncx` project files, replayable, and exportable as a shell script, a
  Makefile or a Jupyter notebook.
- **Batch processing** — apply a recorded session or a drawn model to every file
  in a folder, with configurable output naming.
- **Guardrails before and after the run** — unit, grid, timestep and field-shape
  checks that catch the operators which exit 0 while producing something wrong.
- **Analysis panels** — time-series plots, zonal and field statistics,
  two-dataset comparison, animation playback with GIF/MP4 export, region
  masking, and an expression editor for the `expr` family.
- **Geospatial canvas** — Cartopy-on-Qt rendering with pan / zoom /
  drag-and-drop, multi-layer stacking, custom symbology, graticule, scale bar,
  colorbar, a floating navigation cluster, and per-layer property editing.
- **Eight projections** — PlateCarree, Mercator, Robinson, Mollweide, Lambert
  Conformal, Albers Equal Area and the two polar stereographics. Each one's
  parameters are derived from what is on screen, and every layer is redrawn
  from its source array on a switch.
- **Fourteen file formats** — shapefile, GeoJSON, GML, GeoPackage, GPX,
  KML/KMZ, Idrisi vector, GeoTIFF, USGS DEM, ENVI, Idrisi raster, HDF5 and
  NetCDF. Projected layers are reprojected for display; mixed-geometry and
  multi-layer files are drawn whole.
- **NetCDF-aware** — multi-band navigation, time-slider playback, automatic
  variable and coordinate detection, CF calendar decoding (including `360_day`
  and `noleap`), layer extent fitting.
- **Thirteen online basemaps plus two offline ones** — Esri and Carto layers,
  NASA composites and Sentinel-2 cloudless, a Natural Earth backdrop that needs
  no network at all, and local `.mbtiles` archives.
- **Scientific colormaps** — matplotlib's own, plus cmocean and cmcrameri when
  installed, offered from one registry so the app can never advertise a name
  matplotlib cannot resolve.
- **Native + WSL CDO backends** — automatically picks between a local `cdo`
  binary and a WSL-hosted one on Windows.
- **No tracking, no telemetry, no API keys** — every basemap and every map
  dataset is open and keyless.

---

## Requirements

### Python 3.13 — install this one

3.10 is only the language floor (several modules evaluate `X | None`
annotations at import time, which is a syntax error before it). Cartopy sets
the ceiling, and Cartopy is not optional here: it draws the map.

Cartopy 0.25.0, the current release, publishes binary wheels for CPython
3.10–3.13 and no further. On 3.14 there is no wheel to install, so pip falls
back to the source distribution and tries to compile Cartopy against GEOS —
which needs the GEOS and PROJ development headers and a C++ toolchain (MSVC on
Windows) before it will even begin, and is where a `pip install -r
requirements.txt` on a new Python typically stops. Every other library in the
stack — numpy, matplotlib, shapely, pyproj, rasterio, netCDF4 — already ships
3.14 wheels; Cartopy is the one holding the line.

Going the other way costs something too: on 3.10 and 3.11, pip resolves the rest
of the stack to older releases, because the current numpy and rasterio require
3.12+. So 3.12 works and 3.13 is the top of the supported range — and 3.13 is
what the packaged macOS and Windows builds are frozen with, which makes it the
combination that actually gets tested.

```bash
python3.13 -m venv .venv
```

### CDO

A `cdo` binary on `PATH`, or a WSL-hosted one on Windows:

```bash
brew install cdo          # macOS
```

```bash
sudo apt install cdo      # Debian / Ubuntu
```

Or follow the [official build instructions](https://code.mpimet.mpg.de/projects/cdo/wiki/Cdo).
Which optional libraries your CDO was compiled with decides whether a handful of
operators can run at all — see
[Which CDO build features NCExplorer ships with](#which-cdo-build-features-ncexplorer-ships-with).

### Python packages

Listed in [requirements.txt](requirements.txt): PyQt6, Cartopy, xarray, netCDF4,
cftime, geopandas, rasterio, matplotlib, numpy, pandas, shapely, contextily,
cmocean, cmcrameri, imageio, pillow and requests. cmocean, cmcrameri and
`imageio-ffmpeg` are soft — their features narrow gracefully and the app starts
without them.

---

## Quick start

```bash
git clone https://github.com/ManishShivach/NCproject.git
```

```bash
cd NCproject && python3.13 -m venv .venv && source .venv/bin/activate
```

On Windows, activate with `.venv\Scripts\activate` instead.

```bash
pip install -r requirements.txt
```

```bash
python main.py
```

Verify the dependencies resolved correctly:

```bash
python check_requirement.py
```

Print version metadata without launching the GUI:

```bash
python main.py --version
```

Check that the toolbar menus, the command palette and the model builder all
offer exactly the operators the installed CDO has. This walks the real menu tree
and the real palettes rather than restating the code that builds them, writes
`docs/operator_audit.md`, and exits non-zero on any disagreement:

```bash
QT_QPA_PLATFORM=offscreen python audit_operator_surfaces.py
```

---

## The operator catalog

All 943 operators are reachable, from four surfaces that are guaranteed to
agree:

| Surface | How to reach it | What it offers |
| --- | --- | --- |
| Category menus | The seventeen toolbar glyphs | Ten operators on open, plus an **All …** submenu holding the complete category, chunked alphabetically where it is large |
| Toolbar search | The box beside Batch | Loose name matching — `tmn` finds `timmean` — and description matching, so "field mean" finds `fldmean` |
| Command palette | `Ctrl+K` | The same ranking, full-window |
| Model builder palette | `Ctrl+Shift+M` | The same ranking, draggable into a graph |

Every surface filters against the resolved CDO's own operator list, so nothing
is offered that the installed binary does not have.

**Operator forms are generated from the schema.** Arity, parameter names and
kinds, output slots, per-operator global options and environment variables are
all declared once in [`core/categories.py`](ncexplorer_toolkit/core/categories.py)
and read by every surface. That is what makes the following true without a
hand-maintained table behind any of it:

- Multi-input operators — the twelve `ymon*`/`yseas*` comparisons, `timcor`,
  `timcovar`, `varrms`, `wct`, `subtrend` among them — draw one row per input.
- Multi-output operators get one destination per output, named from the schema,
  with captions saying which file is which.
- Parameters render as the widget their kind implies: checkboxes for booleans,
  closed dropdowns for enumerations, multi-selects for lists, a grid picker for
  grid descriptions, and a full editor for the `expr` family.
- Parameter grammar is enforced per operator, not per kind. `outputtab`'s
  keynames each take an optional `:width` tail; `selseas` takes a comma-separated
  list of case-sensitive season names and nothing else. Both were measured
  against the binary rather than inferred from the manual.
- The three stdin-reading operators (`input`, `inputsrv`, `inputext`) are
  runnable — the child process is given its own stdin rather than inheriting the
  parent's.
- The `info` / `sinfo` / `show*` family writes no file and reports on stdout, so
  its output can be captured to a file, and batch treats a captured reading as
  the result the pipeline keeps.

**Nothing is silently unavailable.** An operator this build cannot run is never
hidden — a missing operator reads as a bug in the application, so it is shown
disabled with the reason instead. All four surfaces derive both halves of that
answer, the short label and the sentence, from the same table the refusal itself
comes from: the category menus grey it, the search box paints the reason beside
the suggestion and declines on Enter, the palette greys the whole row and
explains rather than opening a form whose Run would refuse, and the model
builder disables the node so it cannot be dragged into a graph.

---

## Guardrails: the failures CDO does not report

Several CDO operators exit 0 while producing something wrong. NCExplorer checks
for each of these, and says which one it found:

- **`eof` writes an all-zero eigenvalue file** after printing a Jacobi
  convergence warning on stderr. Stderr is read alongside stdout and that
  warning is treated as a failure despite the exit code.
- **Transformation operators copy their input through unchanged** when handed
  the wrong kind of field, warning rather than failing. Field *representation* —
  spectral or gridpoint, Gaussian or lonlat, divergence/vorticity or a wind
  pair, complex or real — is checked before the run by
  [`core/fieldshape.py`](ncexplorer_toolkit/core/fieldshape.py).
- **`fldcor` and `fldcovar` silently truncate** to the shorter of two series.
  Grid and timestep agreement between operands is checked by
  [`core/pairing.py`](ncexplorer_toolkit/core/pairing.py), with a blocking
  prompt when two inputs do not match.
- **The climate indices assume units CDO never converts.** The temperature
  indices read the field as Kelvin while their threshold argument is in Celsius,
  so a field already in °C counts every day of the year as a summer day; the
  precipitation indices want an amount in mm, so a field carrying a rate in mm/s
  is about 10⁵ times too small. [`core/units.py`](ncexplorer_toolkit/core/units.py)
  reads the file's own units attribute and asks before the run.
- **An operator with no parameter can hang forever.** Nineteen of them never
  exit when their parameter is blank; a partly-filled one aborts cleanly. The
  form will not submit an empty required parameter.

Runs are cancellable, streamed to the console, and written to the session log.
The first failure in a chain stops the run and tints the node that failed.

---

## Model builder

The operator form runs one operator against one set of files. A real analysis is
four of them, and the intermediate files exist only because someone had to
invent names for them — and the shape that motivates the feature, one input
fanning out to two reductions and recombining through `sub`, is not a sequence at
all.

**Model → Model Builder**, or `Ctrl+Shift+M`, is a canvas for drawing that graph:

- Validation runs while you draw, not when you press Run, so a missing parameter
  is reported at the moment it goes missing.
- A live command preview assembles as boxes are wired together.
- The builder warns when two connected nodes disagree about a CDO environment
  variable — `CDO_WEIGHT_MODE` and friends, which the eight EOF operators expose
  as editable rows.
- Models save to `.ncmodel`, are stored inside `.ncx` projects, and export to a
  shell script, a Makefile or a notebook.
- A recorded session can be turned into a model and then branched.
- A model can be run over a whole folder through the batch dialog.
- Runs go through the same execution controller as a single operator, so every
  guarantee above about cancellation, streaming and logging holds here too.

[`core/model.py`](ncexplorer_toolkit/core/model.py) holds the graph, its wiring
rules and its compiler with no Qt import at all, so it can be exercised from a
test or a script. It compiles to the same `OperatorRequest` list that the session
log records and the batch runner retargets, which is why batch, export and
project storage work against a drawn model with no converter in between.

---

## Reproducibility

Every run lands in the session log with its full invocation, its inputs, its
outputs and its result. From there:

- **Replay** — re-run a recorded step, or the whole session, against the same or
  different inputs.
- **Projects** — `.ncx` files carry the loaded layers, the canvas state, the
  session log and any drawn models. The format is schema 1.1, and a file written
  by a newer major schema is refused with an explanation rather than
  misinterpreted.
- **Export** — a session or a model becomes a shell script, a Makefile or a
  Jupyter notebook, so an analysis developed in the GUI can leave it.

---

## Batch processing

Point the batch dialog at a folder and it applies a recorded session or a drawn
model to every file in it, with configurable output naming. The batch runner
reads the same schema every other surface does, so a multi-output operator
produces its full set of files per input, and a captured `info`-family reading is
kept as the result rather than discarded.

---

## Analysis panels

Dockable, each with its own shortcut:

| Panel | Shortcut | What it shows |
| --- | --- | --- |
| Plot | `Ctrl+Shift+P` | Time series at a clicked point or over a region |
| Statistics | `Ctrl+Shift+I` | Zonal and field statistics for the active layer |
| Comparison | `Ctrl+Shift+C` | Two datasets side by side, with differences |
| Animation | `Ctrl+Shift+A` | Time-slider playback, exportable to GIF or MP4 |
| Session | `Ctrl+Shift+R` | The run log, replayable and exportable |
| Console | `Ctrl+L` | Live CDO stdout and stderr |

Plus a **region mask** dialog that masks a NetCDF file to a shapefile polygon by
three routes with very different costs: `sellonlatbox` on the polygon's extent
(fast, keeps the rectangle), a true `maskregion` polygon mask (cells outside
become missing), or the box first and the polygon second — which on a global file
is dramatically cheaper than the mask alone.

---

## The map canvas

Cartopy on Qt, with the whole map surface built rather than borrowed:

- **Eight projections**, switchable at any time. The registry in
  [`geocanvas/projections.py`](ncexplorer_toolkit/geocanvas/projections.py)
  derives each one's parameters — central meridian, standard parallels, the
  latitude a polar cap is cut at — from what is on screen rather than asking for
  them. Three things make the switch trustworthy:
  - **Rasters are redrawn from the array the file gave us**, never from the
    artist's. Cartopy warps at `imshow` time, so re-warping a warped array
    compounds the resampling until the picture is no longer the data. Time steps
    and variable switches go through the same path.
  - **`self.extent` is lon/lat, always.** The axes' own coordinates are metres in
    every projection but the default, so clicks, the rubber-band region and the
    cursor read-out convert through `_axes_to_lonlat` rather than trusting
    `xdata`. A point in the corner beside a Mollweide ellipse is off the globe and
    transforms to infinity, which passes any range check made on its own.
  - **The axes is rebuilt, not cleared.** `add_subplot` has not reused a subplot
    since matplotlib 3.6, so clearing stacked one more axes per switch and made
    every redraw slower than the last.
- **A variable is flattened to two dimensions along every axis but the grid's.**
  NOAAGlobalTemp carries a singleton `z` level between the time axis and the
  grid, and model output can add an ensemble member; each such dimension is
  taken at its first entry, matching what the statistics, plot and comparison
  panels already do. A 3-D array reaching `imshow` is read as an image with one
  colour channel per column, and the error that follows — matplotlib's "Invalid
  shape" on the default projection, or Cartopy's "zero-size array to reduction
  operation maximum" from inside its own warp on any other — was raised in a Qt
  slot, which took the whole application down rather than one frame.
- **Cartopy's boundary-polygon defect is shimmed.**
  [SciTools/cartopy#2176](https://github.com/SciTools/cartopy/issues/2176) — open
  since May 2023 and still unfixed on `main` — makes an inverted interior ring
  that runs *along* the projection boundary intersect to a `GeometryCollection`,
  which `MultiPolygon()` then subscripts and dies on.
  [`cartopy_polygon_fix.py`](ncexplorer_toolkit/geocanvas/cartopy_polygon_fix.py)
  probes for the defect before patching, so a fixed Cartopy makes it a no-op
  without anyone having to remember to remove it.
- **Layer manager** — a file-explorer-style sidebar with per-layer visibility and
  dataset metadata. Every layer keeps its own property record. The list is the
  map's drawing order: the top entry draws on top, and dragging a layer, using
  the ▲/▼ buttons or "Bring to Front"/"Send to Back" restacks the canvas to
  match — across every layer type, rather than pinning vectors above rasters as
  the old fixed per-type z-orders did.
- **Symbology**, on each layer's own properties panel (the Symbology button, or
  right-click → Properties): opacity, and then whatever that kind of layer is
  drawn with — colormap, reverse, zero-centring, value range and interpolation
  for a raster or NetCDF layer; colours, line width, line style and marker size
  for a vector one. Every control writes straight through to the layer's stored
  style, so the map follows as you edit and a saved project keeps the result.
  Each layer holds its own colour scale, so two rasters on one map can be told
  apart.
- **Map furniture** — graticule (`Ctrl+G`), colorbar (`Ctrl+B`), a geodesic scale
  bar that rounds to a readable 1/2/5 × 10ⁿ figure, and a floating navigation
  cluster whose buttons call straight into the canvas API, so they and the
  keyboard shortcuts can never disagree.
- **Colormaps** from one registry: matplotlib's own, plus cmocean's ocean and
  climate maps and cmcrameri's colour-vision-deficiency-safe scientific maps when
  those packages are installed.

### Basemaps

Thirteen keyless online sources — Carto Light, Dark and Voyager; Esri Satellite,
Topographic, Terrain, Shaded relief, National Geographic and Ocean; OpenTopoMap;
Sentinel-2 cloudless; NASA Blue Marble and NASA Night Lights — selected from the
toolbar. None needs an account or an API key, and a provider that a given
`xyzservices` release has renamed or dropped simply goes missing from the
selector rather than breaking it.

Two work with no internet at all, and
[`geocanvas/offline_basemap.py`](ncexplorer_toolkit/geocanvas/offline_basemap.py)
may never touch the network or import contextily — that is the point of the
module:

- **Natural Earth backdrop** — a real cartographic drawing (ocean, land, lakes,
  rivers, coastline, borders) from the shapefiles already in Cartopy's data
  directory.
- **MBTiles** — pick **Load MBTiles…** from the basemap selector to open a local
  `.mbtiles` archive; its tiles are stitched into the visible extent exactly as
  an online provider's are. Archives dropped into `~/.ncexplorer/basemaps` are
  found at startup, and paths you have opened before are remembered.

---

## File formats

Everything the canvas can draw is declared in one place,
[`geocanvas/formats.py`](ncexplorer_toolkit/geocanvas/formats.py). The Open
dialog, drag-and-drop and the loader all read that table, so what the chooser
offers is what the loader accepts.

| Format | Extensions | Notes |
| --- | --- | --- |
| Shapefile | `.shp` | Needs `.shx` and `.dbf` beside it; reads `.prj`/`.cpg`/`.sbn`/`.sbx` too |
| GeoJSON | `.geojson`, `.json` | A plain non-geographic `.json` is refused |
| KML | `.kml` | Needs pyogrio — see [Vector engines](#vector-engines) |
| KMZ | `.kmz` | Zipped KML, opened through GDAL's `/vsizip/`; needs pyogrio |
| GML | `.gml` | |
| GeoPackage | `.gpkg` | Multi-layer; every layer is drawn |
| GPX | `.gpx` | Multi-layer (waypoints, routes, tracks); every layer is drawn |
| Idrisi Vector | `.vct` | Read-only; `.vdc` carries the metadata. Needs pyogrio |
| GeoTIFF | `.tif`, `.tiff` | |
| USGS DEM | `.dem` | |
| ENVI | `.dat` | The `.hdr` is mandatory — without it the file cannot be identified at all |
| Idrisi Raster | `.rst` | `.rdc` carries the CRS; readable without it, but unreferenced |
| HDF5 | `.h5`, `.hdf5` | Container: variables are exposed as selectable subdatasets |
| NetCDF | `.nc`, `.nc4` | Variable and time selection, via xarray |

Three behaviours apply across all of them, and each exists because the file
otherwise loaded successfully and showed the wrong thing:

- **Projected data is reprojected for display.** Layers are converted to
  EPSG:4326 before drawing (rasters through a warped VRT), because the canvas
  draws under `ccrs.PlateCarree()`. Drawn as-is, a UTM shapefile's metres are
  read as degrees, so it hits the point path's `-180..180` filter and loses every
  feature; a UTM raster lands off the map. A layer with no CRS at all is assumed
  to be lon/lat already.
- **Mixed-geometry files are drawn whole.** Reading `geometry.iloc[0].geom_type`
  alone draws one kind and drops the rest, so a file holding points, lines and
  polygons becomes one layer per kind — `roads (lines)`, `roads (points)` — each
  independently styleable.
- **Multi-layer files are drawn whole.** All layers of a GeoPackage, GML or GPX
  are read and merged. `waypoints` is layer 0 of every GPX, so reading layer 0
  alone makes a GPX carrying a track come up empty.

HDF5 needs one more: its variables are GDAL subdatasets, so `count` is 0 and a
naive reader rejects the file as having no bands. Container files open, with
their other variables offered.

**Not supported, and why.** `.svg` carries no coordinate reference system, so
there is nothing to say where it belongs on a map — and GDAL's driver named
`SVG` reads Cloudmade vector streams, not drawings. `.hdf` (classic HDF4) needs a
driver the GDAL inside the rasterio wheel is not built with; HDF5 works. Both are
refused with that sentence rather than a generic parse error.

### Vector engines

geopandas picks its I/O engine at run time — `pyogrio` first, then `fiona` — and
**the two vendor different GDAL builds**: pyogrio's carries 65 vector drivers,
fiona's 17. KML, KMZ and Idrisi vector are among the ones only pyogrio has.

NCExplorer asks the running engine rather than assuming, and refuses a format it
cannot open with a message naming the missing package. The packaged build
excludes pyogrio, so those three formats work from a source checkout and not in
the frozen app. To get them from source:

```bash
pip install pyogrio
```

### What the operator form's choosers offer

The table above is what the **map** reads. CDO reads a different and much smaller
set — GRIB, NetCDF, and the local MPI-MET SERVICE, EXTRA and IEG formats — so the
operator form has its own vocabulary in
[`core/filetypes.py`](ncexplorer_toolkit/core/filetypes.py), and each Browse
button asks the schema which slot it is on before opening.

That has to be per-slot, because the answer differs *within* one operator:

| Slot | Chooser | CDO's words |
| --- | --- | --- |
| any operator's input | NetCDF / GRIB / SERVICE / EXTRA / IEG | "…file format independent access to GRIB and NetCDF datasets. The local MPI-MET data formats SERVICE, EXTRA and IEG are also supported" |
| `import_binary` input | GrADS `.ctl` | `cdo import_binary infile.ctl outfile` — the descriptor **is** infile; the `.bin` it names is never passed to CDO |
| `import_cmsaf` input | HDF5 | "imports gridded CM-SAF … HDF5 files" |
| `remap,weights` | NetCDF | "Interpolation weights (SCRIP NetCDF file)" |
| `remapeta,vct` | text | "an ASCII dataset with the vertical coordinate table" |
| `remapeta,oro` | data file | "File name with the orography (surf. geopotential) of the target dataset" |
| `setgrid,grid` | description, SCRIP NetCDF or data file | "Grid description file or name" — §1.5.2 allows all three |
| `setgridarea,grid` | data file | "Data file, the first field is used as grid cell area" |
| `cmor,MIPtable` | JSON | "Name of the MIP table as used by CMOR" |
| `maskregion,regions` | text | "ASCII formatted files with different regions" |

Every chooser ends in **All Files (\*)**, and that is deliberate rather than a
hedge: CDO's own examples name files with no extension at all (`cdo griddes
infile > mygrid`, then `cdo setgrid,mygrid …`), and SERVICE/EXTRA/IEG data is
conventionally unsuffixed. Where CDO documents no format for a parameter, the
chooser says so and offers everything rather than inventing a suffix that would
hide the right file.

The batch runner, the replay dialog and the model builder read this same table.

---

## Which CDO build features NCExplorer ships with

Some CDO operators depend on libraries linked into CDO **at compile time**. They
are not plugins: a CDO built without one can never gain it, and installing the
library separately changes nothing, because the already-built binary will not
pick it up. Which of them your CDO has is therefore a property of the *binary*,
not of your command or your data.

Ask any CDO which it has:

```bash
cdo --config all
```

Three of those decide whether operators in this application can run:

| Feature | Operators | Shipped in the macOS `.app` | If missing |
|---|---|---|---|
| **MAGICS** | `contour`, `shaded`, `grfill`, `vector`, `stream`, `graph` | Yes | The six stay visible in the Graphics menu but are greyed out, with the reason on hover. Runs are refused before CDO is started. |
| **FFTW3** | `fourier2grid`, and the `linear`/`cubic` types of the spectral-to-gridpoint transforms (`sp2gpl`, `dv2uv`, `dv2uvl`) | Yes | The operator runs and CDO aborts; NCExplorer translates the abort into a sentence naming the build. |
| **CMOR** | `cmor` | No | Refused before the run, with the reason. |

**FFTW3 is the one gap that can only be found by trying.** `cdo --config`
publishes `has-magics` and `has-cmor` but has **no key for FFTW3** — `has-fftw3`,
`has-fftw` and `has-FFTW3` are all "unknown config option", and none of the 24
keys it does return names a transform library. So MAGICS and CMOR are checked
before a run and FFTW3 is explained after one. The application uses one gate for
all three with two sources of evidence, rather than pretending it can predict the
third.

The "Yes" in the table is therefore a statement about how the bundled CDO is
*built* — `provision_cdo_macos.sh` installs `fftw` and configures `--with-fftw3`
— and not something `cdo --config` can confirm afterwards. A stock Homebrew `cdo`
is built without it, which is why the measurements recorded throughout
`core/categories.py` and `operator_lab/profiles.py` show `dv2uv,linear` and
`dv2uv,cubic` aborting with "LIBFFTW3 support not compiled in!". Those were taken
against the stock binary, not the bundled one.

**CMOR is deliberately absent** from the bundle. It exists to write
CMIP-compliant output under a controlled vocabulary, which is a publication
workflow rather than an exploration one, and it pulls in a large dependency tree
for a single operator. `cmorlite` needs no CMOR runtime and is unaffected.

---

## Building the bundled CDO yourself

The macOS `.app` embeds whatever `cdo` the build host has, so plotting support
comes from the build machine rather than from anything the end user installs. To
reproduce it:

```bash
./provision_cdo_macos.sh
```

That installs the libraries via Homebrew, builds CDO from source against them,
and refuses to finish unless the result both reports `has-magics:yes` **and**
renders a real test plot of a plausible size. It is idempotent — re-running with
a good binary already in place exits immediately — and it needs no `sudo`.

Then just build. No `PATH` export is needed: `build.py` looks under
`$CDO_MAGICS_PREFIX` (`~/.local/cdo-magics` by default, the same variable the
script itself reads) and prefers what it finds there over whatever is on `PATH`,
because MAGICS is a compile-time link that Homebrew's `cdo` cannot acquire — on a
host with both, taking `PATH` would bundle the only one of the two that cannot
plot. The prefix is preferred only when it *proves* `has-magics:yes`, so a
half-built tree there falls through rather than shadowing a working system CDO.

```bash
python build.py
```

```bash
python build.py --require-magics
```

Shipping without MAGICS warns rather than fails, because the cost is bounded and
visible: the six plot operators are greyed in every menu and refuse themselves
before CDO is launched, naming MAGICS as the reason. A build without it is one
with six operators that explain themselves, not one that fails mysteriously — so
it is a decision rather than an accident, and `--require-magics` is how you say
the answer should be "no, fix it".

On **Windows** CDO runs inside WSL, so there is no binary to bundle and the
installer provisions a MAGICS-enabled CDO inside the distro instead. That is the
one platform where installation does work on your machine, and the one place a
restart may be required. The provisioning step is a module rather than installer
script, reachable three ways — during install, from the `RunOnce` resume after the
restart `wsl --install` needs, and afterwards at any time from the Start Menu or
via `NCExplorer.exe --repair-cdo` — because an installer that only works during
installation is one the user cannot recover from.

---

## Building a standalone executable

`build.py` wraps PyInstaller to produce an artefact for the host platform:

```bash
python build.py
```

On macOS this yields `dist/NCExplorer.app` plus a
`dist/NCExplorer-1.0.0-macos.dmg` for distribution. `build.py` is the **only**
build definition — there is no checked-in `.spec` file, and the one PyInstaller
generates is written into `build/` as a disposable artefact. Do not build with
`pyinstaller <spec>`.

The application icon is generated at build time from `assest/NCE_icon.png` into a
multi-resolution `.ico` and a full `.icns` variant set, so the artwork is never
hand-exported into three files that drift. Windows gets an explicit
AppUserModelID, without which a Python-hosted process shows the Python icon on
the taskbar no matter what Qt is told. A splash screen covers the several seconds
Qt, Cartopy and the netCDF stack take to import — stepped by the real startup
stages rather than a timer, and composed at the screen's device-pixel ratio so
the wordmark is not soft on HiDPI.

Preview the splash on its own:

```bash
python splash_screen.py
```

### Opening a downloaded release on macOS

The `.app` is signed **ad-hoc** (there is no Apple Developer ID certificate for
this project) and is not notarized. macOS attaches a quarantine flag to anything
downloaded from a browser, and an ad-hoc signature plus quarantine produces a
misleading error:

> "NCExplorer" is damaged and can't be opened. You should move it to the Trash.

The app is not damaged. Clear the quarantine flag after copying it to
`/Applications`:

```bash
xattr -dr com.apple.quarantine /Applications/NCExplorer.app
```

Then open it normally. This is needed once, and only for a downloaded copy — a
`.app` built locally by `build.py` is never quarantined.

---

## Testing the operators

The operator test lab runs CDO operators against sample data and writes an Excel
workbook saying, for each one, whether it passed, why it failed, where in the app
it can be picked from, and which file extensions its input and output usually
carry. Open the window, tick what you want and press **Run**:

```bash
python testCDOcommands.py
```

The same sweep without a window, for CI or a full run you do not want to watch:

```bash
python test_all_operators.py
```

One category at a time:

```bash
python test_all_operators.py --category "Statistical values"
```

Against your own data instead of the generated samples:

```bash
python test_all_operators.py --input /path/to/rainfall.nc --timeout 60
```

To re-run only what broke, use the window: **Select failures** ticks exactly the
operators that failed last time, so a fix can be checked in seconds rather than
by sweeping all 943 again.

Sample inputs are generated with CDO — two years of daily data on a small global
grid, a multi-level file for the vertical operators, and the per-month,
per-season and per-year statistics that the `ymon*`/`yseas*` families need as
their *second* input — so a run needs nothing but an installed CDO. `--input`
replaces them with your own files and reads the variable name back out of the
first one, so parameters such as `selname,<var>` name something the file actually
contains.

The workbook has five sheets: **Summary** (headline counts and the integration
preflight), **Results** (one filterable row per operator), **Issues** (failures
grouped by root cause), **Surfaces** (operators not reachable from all four
pickers) and **Environment** (what was run, against what).

### The reference sweep

The operator counts quoted throughout this README come from one run against the
CDO the macOS `.app` bundles, kept as two files so they can be re-checked rather
than taken on trust:

```bash
python3 test_all_operators.py --binary ~/.local/cdo-magics/bin/cdo --json docs/sweep_1.0.0.json --excel docs/sweep_1.0.0.xlsx
```

- **`--binary ~/.local/cdo-magics/bin/cdo`** — test the MAGICS-enabled CDO that
  [`provision_cdo_macos.sh`](provision_cdo_macos.sh) builds, rather than whatever
  `cdo` happens to be on `PATH`. This is the difference between measuring what
  NCExplorer ships and measuring your development machine: MAGICS is a
  compile-time link, so against a stock Homebrew CDO the plot operators cannot
  pass at all — CDO aborts naming the missing build. Any path is accepted; a run
  without the flag is still a valid report, just of a different binary.
- **`--json`** — the raw rows, including everything the workbook summarises: the
  exact command line, return code, duration, output size and extension, and which
  of the app's pickers offer each operator. This is the file to diff between two
  runs — a new CDO, a changed catalog — since diffing the workbook tells you
  nothing.
- **`--excel`** — the workbook at a fixed, version-stamped name instead of the
  default timestamped one, so re-running 1.0.0 replaces it in place instead of
  leaving a pile of near-identical files.

That run, on 2026-08-17 against CDO 2.6.3 from the MAGICS build, reported
**856 passed, 0 failed, 87 skipped** of 943 — 100% of the operators that could be
attempted, with all six preflight checks green.

Skipped is not "untested": each skip carries the reason it could not be
attempted, and they are properties of the sample data rather than of CDO — 10
want 3D data on hybrid sigma-pressure levels, 11 read a namelist or their field
data from stdin, 5 want a GMT colour-palette file, 3 a rotated lon/lat grid, 2 a
HEALPix grid, 2 a CMOR table, and so on. `--include-untestable` attempts them
anyway; most then abort or time out, and the reason lands in the report instead
of the skip. Five of the six MAGICS operators pass on this binary; `graph` is
skipped because it needs a single-gridpoint time series, not because it cannot
plot.

Budget for about three minutes of wall time — 168 s of that inside CDO — and
860 MB of output written into `operator_test_output/`, pruned back to 400 MB once
each file's size has been recorded. `--keep-large-outputs` keeps all of it.

The exit code makes the sweep usable as a gate: **0** when nothing failed, **1**
when something did, **2** when the run could not start or a preflight check
failed. Skips never fail the run.

`docs/` is git-ignored, so those two files are not in a fresh clone — the command
above is how you produce them. Point `--json`/`--excel` somewhere else if you
want the results tracked.

The integration layer has its own tests — mocked, catalog-consistency and live —
which are the precondition for trusting a sweep:

```bash
python testCDO.py
```

All three drive the `operator_lab` package, so a result cannot depend on which
one produced it.

The unit and UI suites run under pytest, headless:

```bash
QT_QPA_PLATFORM=offscreen python -m pytest test/
```

---

## Keyboard shortcuts

Every binding is declared once in
[`gui/shortcuts.py`](ncexplorer_toolkit/gui/shortcuts.py) and installed from
there, so the **Help → Keyboard Shortcuts** cheat sheet can never drift from what
is actually bound. Press `?` over the canvas to open it.

| | |
| --- | --- |
| `Ctrl+O` / `Ctrl+S` / `Ctrl+Q` | Open file, save output, quit |
| `Ctrl+Shift+N` / `Ctrl+Shift+O` / `Ctrl+Shift+S` / `Ctrl+Alt+S` | New, open, save, save-as project |
| `Ctrl+K` | Find an operator (command palette) |
| `Ctrl+Shift+M` | Model builder |
| `Ctrl+Shift+B` | Batch process a folder |
| `Ctrl+B` / `Ctrl+G` / `Ctrl+L` | Colorbar, graticule, console |
| `Ctrl+Shift+P` / `Ctrl+Shift+I` / `Ctrl+Shift+C` / `Ctrl+Shift+A` / `Ctrl+Shift+R` | Plot, statistics, comparison, animation, session panels |
| `Ctrl+=` / `Ctrl+-` / `Ctrl+0` | Zoom in, zoom out, full extent |
| `Ctrl+Shift+L` | Zoom to active layer |

---

## Project structure

```
ncexplorer_toolkit/
  core/         CDO subprocess integration, the operator catalog and schema,
                session log, projects, batch runner, the model graph and its
                runner, and the three preflight checkers — units.py (is this
                the quantity the operator assumes?), pairing.py (are these two
                files comparable?) and fieldshape.py (is this the right kind
                of field?)
  geocanvas/    Cartopy-on-Qt canvas, layers, symbology, colormaps, colorbar,
                scale bar, projections, NetCDF rendering, basemap sources and
                the offline ones, plus file I/O — formats.py is the one table
                of supported formats, with vector_io.py / raster_io.py behind it
  gui/          PyQt6 main window, menus, toolbar, layer manager, file explorer,
                analysis docks, command palette, model builder, expression
                editor, mask dialog, batch dialog, shortcut registry, theme
                manager, navigation overlay, splash screen
  resources/    Bundled offline assets: the category SVGs (icons.py) and the
                app icon / splash artwork resolver (branding.py)
  utils/        Cross-cutting helpers (temp-file store, CF time axes,
                regionmask, etc.)

assest/         The two branding PNGs: NCE_icon.png (window, dock and taskbar
                icon; build.py converts it to .ico / .icns for the installer
                and the .app) and NCE_logo_with_name.png (splash background)

operator_lab/   Operator test harness shared by the three testers below:
                sample generation, per-operator profiles, surface scanning,
                the runner and the Excel report writer

installer/      Inno Setup script and the WSL CDO provisioning shell script
test/           pytest suites — test_catagories/ (per-category operator
                behaviour) and tests_ui/ (canvas, widgets, parity)
docs/           Operator audit output and project write-ups

main.py         Application entry point
build.py        PyInstaller build script — the only build definition
provision_cdo_macos.sh   Builds a MAGICS-enabled CDO for bundling
splash_screen.py         Standalone preview of the startup splash
audit_operator_surfaces.py  Cross-checks the operator surfaces
testCDOcommands.py       Operator test lab — pick operators, press Run
test_all_operators.py    The same sweep from the command line
testCDO.py               Integration-layer tests (mocked, catalog and live)
check_requirement.py     Dependency verification
requirements.txt
```

The toolkit is import-cheap: PyQt6, Cartopy, rasterio and geopandas are loaded
lazily on first attribute access, so `import ncexplorer_toolkit` does not pull in
the GUI stack.

---

## Author

**Manish Shivach** — [iammanishshivach@gmail.com](mailto:iammanishshivach@gmail.com)

## License

MIT
