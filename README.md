# NCExplorer

Cross-platform GUI for climate data analysis with full CDO operator coverage and an open-source geospatial canvas.

NCExplorer wraps the [Climate Data Operators (CDO)](https://code.mpimet.mpg.de/projects/cdo) toolchain in a PyQt6 desktop application. Load NetCDF / GeoTIFF / shapefile data, browse it on a Cartopy-backed map, and run CDO operators interactively without writing shell pipelines.

## Features

- **Full CDO 2.6.0 catalog** — every operator from `cdo --operators` exposed through a searchable, category-grouped UI, with descriptions auto-merged from the official CDO User Guide.
- **Geospatial canvas** — Cartopy-on-Qt rendering with pan / zoom / drag-and-drop, multi-layer stacking, custom symbology, and per-layer property editing.
- **NetCDF-aware** — multi-band navigation, time-slider playback, automatic variable / coordinate detection, layer extent fitting.
- **Vector + raster support** — shapefile, GeoJSON, KML, GPX, GeoTIFF, PNG/JPEG, and NetCDF (`.nc`, `.nc4`).
- **Layer manager** — file-explorer-style sidebar with per-layer visibility, transparency, ordering, and dataset metadata.
- **Native + WSL CDO backends** — automatically picks between a local `cdo` binary and a WSL-hosted one on Windows.
- **No tracking, no telemetry** — all rendering uses open-source map data; no API keys required.

## Requirements

- **Python 3.7+**
- **CDO** binary on `PATH` (or via WSL on Windows). Install with `brew install cdo` (macOS), `apt install cdo` (Debian/Ubuntu), or follow the [official build instructions](https://code.mpimet.mpg.de/projects/cdo/wiki/Cdo).
- Python packages listed in [requirements.txt](requirements.txt) — PyQt6, Cartopy, xarray, netCDF4, geopandas, rasterio, matplotlib, numpy, pandas, shapely, pillow, requests.

## Quick start

```bash
git clone https://github.com/ManishShivach/NCproject.git
cd NCproject

python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate

pip install -r requirements.txt
python main.py
```

Verify dependencies are installed correctly:

```bash
python check_requirement.py
```

Print version metadata without launching the GUI:

```bash
python main.py --version
```

## Building a standalone executable

`build.py` wraps PyInstaller to produce a single-file binary for the host platform:

```bash
python build.py
```

The resulting executable lands under `dist/`.

## Project structure

```
ncexplorer_toolkit/
  core/         CDO subprocess integration + auto-generated operator catalog
  geocanvas/    Cartopy-on-Qt canvas, layers, symbology, NetCDF rendering
  gui/          PyQt6 main window, menus, toolbar, layer manager, file explorer
  utils/        Cross-cutting helpers (temp-file store, etc.)

main.py         Application entry point
build.py        PyInstaller build script
splash_screen.py  Startup splash screen
requirements.txt
```

The toolkit is import-cheap: PyQt6, Cartopy, rasterio, and geopandas are loaded lazily on first attribute access, so `import ncexplorer_toolkit` does not pull in the GUI stack.

## Author

**Manish Shivach** — [iammanishshivach@gmail.com](mailto:iammanishshivach@gmail.com)

## License

MIT
