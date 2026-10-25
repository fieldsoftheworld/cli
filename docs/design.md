# Design

ftw-baselines trains and evaluates the FTW models. ftw-cli is for using them: querying the precomputed global field boundaries, running released models on new imagery, and comparing results with other field boundary datasets. It serves people at a terminal and agents calling it from scripts, and it doesn't depend on torch or ftw-baselines.

## Principles

- Every feature is a plain function in `ftw_cli` that returns data. The command line and the TUI both call those functions.
- Data is read where it lives. DuckDB reads the 2nd Edition's small file index, then only the row groups that overlap the request. A 10 km box in Iowa reads one 2.3 GB file in about 3 seconds.
- Output is a table in a terminal and JSON when piped or with `--json`. Errors go to stderr with a non-zero exit, and nothing prompts without a terminal.
- Commands are noun-verb like GDAL's (`ftw data query`, `ftw infer run`), take `--bbox xmin,ymin,xmax,ymax`, and pick the output format from the file suffix.

## Install

| Install | Adds | For |
|---|---|---|
| `ftw-cli` | cyclopts, rich, duckdb, pyarrow | querying and exporting field boundaries |
| `ftw-cli[imagery]` | rasterio, pystac-client | crop-calendar scene selection and Sentinel-2 download |
| `ftw-cli[inference]` | the above plus onnxruntime | running FTW models |
| `ftw-cli[tui]` | textual, textual-image | the interactive TUI and map previews |

## Commands

Working now:

- `ftw data info`: per-year totals for the 2nd Edition.
- `ftw data files --bbox`: the files a query would read, and their sizes.
- `ftw data query --bbox [--year] [--out] [--limit]`: summary stats, or the fields written to `.parquet`, `.geojson`, `.gpkg`, or `.fgb`.

Planned:

- `ftw catalog list|query`: the harmonized field data on Source Coop, 69 national collections with crop types.
- `ftw join catalog --fields f.parquet --collection nl`: match fields to harmonized parcels by IoU and carry their attributes over.
- `ftw join sql "..."`: DuckDB SQL with the datasets registered as views.
- `ftw imagery search|download --bbox --year`: pick the planting and harvest Sentinel-2 scenes from the crop calendar and download them for an AOI.
- `ftw models list|show` and `ftw infer run|polygonize`: run released models and polygonize the output.
- `ftw view [--web]`: a map preview in the terminal, or a local MapLibre page.
- `ftw tui`: the interactive interface.
- `ftw schema [command]`: a command's parameters as JSON Schema, for agents.

## Models

The CLI supports the recent models only: PRUE (FTW v3 and the v3.1 CC-BY variants), FTP, and ftw-q. The v1 and v2 models and DelineateAnything stay in ftw-baselines. FTP B3/B7 and ftw-q already publish `.onnx` on Hugging Face under `fieldsoftheworld`. The PRUE models ship as checkpoints on GitHub releases, so they need ONNX exports. They're segmentation-models-pytorch U-Nets with EfficientNet encoders, and exported with dynamic height and width, B3 and B7 match torch's predicted class on every pixel of the test image (max logit difference 3e-4). The files are 49 MB for B3 and 251 MB for B7, and onnxruntime runs a 256x512 tile in under 0.1 s on CPU.

## Data

| Source | Location | Notes |
|---|---|---|
| FTW Global Data 2nd Edition | `data.source.coop/ftw/global-data-2e` | GeoParquet by year and UTM zone, EPSG:4326, CC-BY-4.0. `index/vector.parquet` maps files to bounding boxes. PMTiles per year. |
| Harmonized field data | `data.source.coop/ftw/harmonized-field-data` | fiboa GeoParquet per collection and year, native CRS, license per collection. |
| Sentinel-2 L2A | Planetary Computer, EarthSearch | Same sources and crop calendar as ftw-baselines. |

## Viewing

No Python library draws vector tiles in a terminal, so previews are images: fields drawn over a basemap, shown through the kitty or sixel graphics protocol with textual-image, falling back to half-block characters over plain SSH. `ftw view --web` serves the 2nd Edition PMTiles and local files to a MapLibre page, which over SSH is one port forward.

## Agents

Help text is written for a reader new to FTW. `ftw schema` describes each command's parameters, and a `SKILL.md` ships with worked examples.

## ftw-baselines

ftw-cli will own the `ftw` command. When we switch, ftw-baselines' command becomes `ftw-tools` (its PyPI name) and its `data` and `inference` groups print deprecation warnings (ftw-baselines#303, draft). Until then, installing both in one environment means whichever installs last owns `ftw`.

## Milestones

1. `data` and `catalog` query and export, CI.
2. `join catalog` and `join sql`.
3. `imagery`, `models`, and ONNX `infer`.
4. `view` and the TUI.
5. `schema` and `SKILL.md`.

## Open questions

- Should the PRUE ONNX exports go on Hugging Face next to FTP and ftw-q, or on the GitHub releases next to the checkpoints?
- DuckDB 2.0 is days old and changed its Python API, so we pin `<3` and test against it.
