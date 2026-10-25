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
| `ftw-cli` | cyclopts, rich, duckdb, pyarrow, pillow | querying, exporting, and drawing field boundaries |
| `ftw-cli[imagery]` | rasterio, pystac-client | crop-calendar scene selection and Sentinel-2 download |
| `ftw-cli[inference]` | the above plus onnxruntime | running FTW models |
| `ftw-cli[tui]` | textual, textual-image | the interactive TUI |

## Commands

Working now:

- `ftw data info`: per-year totals for the 2nd Edition.
- `ftw data files --bbox`: the files a query would read, and their sizes.
- `ftw data query --bbox [--year] [--out] [--limit]`: summary stats, or the fields written to `.parquet`, `.geojson`, `.gpkg`, or `.fgb`.
- `ftw view --bbox --out map.png`: the fields drawn to a PNG, colored by score.
- `ftw tui`: pick an area and year, then see the fields on a map with a summary and a table, and export them.

Planned:

- `ftw catalog list|query`: the harmonized field data on Source Coop, 69 national collections with crop types.
- `ftw join catalog --fields f.parquet --collection nl`: match fields to harmonized parcels by IoU and carry their attributes over.
- `ftw join sql "..."`: DuckDB SQL with the datasets registered as views.
- `ftw imagery search|download --bbox --year`: pick the planting and harvest Sentinel-2 scenes from the crop calendar and download them for an AOI.
- `ftw models list|show` and `ftw infer run|polygonize`: run released models and polygonize the output.
- `ftw view --web`: a local MapLibre page over the 2nd Edition PMTiles and local files.
- TUI: imagery and inference screens as those commands land.
- `ftw schema [command]`: a command's parameters as JSON Schema, for agents.

## Models

The CLI supports the recent models only: PRUE (FTW v3 and the v3.1 CC-BY variants), FTP, and ftw-q. The v1 and v2 models and DelineateAnything stay in ftw-baselines. The CLI reads single-file ONNX models from Hugging Face under `fieldsoftheworld`, with URLs pinned to a commit: FTP B3/B7, ftw-q, and the PRUE CC-BY B3/B5/B7 (`ftw-prue-ccby`, commit `ffe4452`). The v3 PRUE models without the CC-BY restriction are still checkpoints on GitHub releases and need the same export. Those exports match torch's predicted class on every pixel of the test image (logits within 3e-4), and onnxruntime runs a 256x512 tile in under 0.1 s on CPU.

## Data

| Source | Location | Notes |
|---|---|---|
| FTW Global Data 2nd Edition | `data.source.coop/ftw/global-data-2e` | GeoParquet by year and UTM zone, EPSG:4326, CC-BY-4.0. `index/vector.parquet` maps files to bounding boxes. PMTiles per year. |
| Harmonized field data | `data.source.coop/ftw/harmonized-field-data` | fiboa GeoParquet per collection and year, native CRS, license per collection. |
| Sentinel-2 L2A | Planetary Computer, EarthSearch | Same sources and crop calendar as ftw-baselines. |

## Viewing

No Python library draws vector tiles in a terminal, so the map is an image. `render.py` draws the fields with Pillow, colored along the FTW green ramp by score and corrected for latitude, and the TUI shows it with textual-image: full resolution over the kitty or sixel graphics protocols, colored half-blocks elsewhere. The TUI draws at most the 20,000 largest fields so a large box stays responsive. `ftw view --web` will serve the 2nd Edition PMTiles and local files to a MapLibre page, which over SSH is one port forward.

## Agents

Help text is written for a reader new to FTW. `ftw schema` describes each command's parameters, and a `SKILL.md` ships with worked examples.

## ftw-baselines

ftw-cli will own the `ftw` command. When we switch, ftw-baselines' command becomes `ftw-tools` (its PyPI name) and its `data` and `inference` groups print deprecation warnings (ftw-baselines#303, draft). Until then, installing both in one environment means whichever installs last owns `ftw`.

## Milestones

1. `data` query and export, `view`, the TUI, CI. (Done.)
2. `imagery`, `models`, and ONNX `infer`, with TUI screens for them.
3. `catalog`, `join catalog`, and `join sql`.
4. `view --web`, `schema`, and `SKILL.md`.

## Open questions

- DuckDB 2.0 is days old. It changed its Python API, and its late-materialization optimizer crashes on our top-N query over the bbox filter, so `connect()` disables that optimizer. We pin `<3` and test against it.
