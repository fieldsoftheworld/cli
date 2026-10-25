# ftw CLI design

## Why a separate CLI

ftw-baselines exists to train and evaluate the FTW models, so its CLI carries Lightning, the training datasets, and experiment configs. Most people who want field boundaries never train a model. They want the precomputed global boundaries for an area, or to run a released model on their own imagery, or to join boundaries with crop type data they already have. `ftw-cli` is for them, and for the agents doing that work on their behalf. It reads models released by ftw-baselines but doesn't depend on it.

## Principles

- **One core, three faces.** Every feature is a plain function in `ftw_cli` that takes typed arguments and returns data. The `ftw` command line, the `ftw tui` interface, and the `ftw mcp` server are thin wrappers over the same functions, so they can't drift apart.
- **Query in place.** The FTW 2nd Edition is 1.24 billion parcels across 2017 to 2025. Nothing downloads the whole thing. DuckDB reads the small index first, picks the partition files that overlap the request, and fetches only the matching row groups. A 10 km box in Iowa reads one 2.3 GB file in under 3 seconds.
- **Agent friendly by default.** Output is a table in a terminal and JSON when piped, with `--json` to force it. Errors go to stderr with a non-zero exit code. Nothing prompts when stdin isn't a terminal. Commands that would read or write a lot say how much first (`ftw data files` lists what a query would touch).
- **Feels like GDAL.** Noun-verb commands (`ftw data query`, `ftw infer run`), `--bbox xmin,ymin,xmax,ymax` everywhere, output format chosen by file suffix.
- **Light base install.** `pip install ftw-cli` gets querying, joins, export, and the TUI. PyTorch only comes with `ftw-cli[inference]`.

## Install profiles

| Install | Adds | For |
|---|---|---|
| `ftw-cli` | cyclopts, rich, duckdb, pyarrow | query, export, joins |
| `ftw-cli[tui]` | textual, textual-image | the interactive TUI and in-terminal map previews |
| `ftw-cli[inference]` | torch, segmentation-models-pytorch, rasterio | running FTW models on Sentinel-2 |
| `ftw-cli[mcp]` | fastmcp | serving the CLI to agents over MCP |

Inference ports the model loading, sliding-window inference, and polygonization from ftw-baselines without Lightning or torchgeo, which the inference path doesn't need beyond a windowed raster reader. Writing fiboa GeoParquet directly drops the fiboa-cli dependency and its exact pins.

## Commands

Done in this first slice:

- `ftw data info`: per-year totals for the 2nd Edition.
- `ftw data files --bbox`: the partition files a query would read, with sizes.
- `ftw data query --bbox [--year] [--out f.parquet|.geojson|.gpkg|.fgb] [--limit]`: summary stats, or the fields themselves.

Planned:

- `ftw data` also gets `--country`/`--geom file` filters and `--where` for column predicates (score, area).
- `ftw catalog list|query`: the harmonized field data on Source Coop (69 national collections in fiboa, crop types included).
- `ftw join cdl --fields f.parquet --year 2024`: majority USDA CDL class and purity per field. It clips CDL to the fields' extent through the CropScape API and caches the clip, since we don't mirror CDL.
- `ftw join catalog --fields f.parquet --collection nl`: match FTW fields to harmonized parcels by IoU to carry crop labels across.
- `ftw join sql "..."`: DuckDB SQL with the datasets registered as views, for anything the verbs don't cover.
- `ftw imagery search|download`: Sentinel-2 scene selection with the crop calendar, ported from ftw-baselines.
- `ftw infer run|polygonize|all` and `ftw models list|show`: run released models.
- `ftw chips build`: cut imagery and masks around joined fields into the FTW training layout, so ftw-baselines can fine-tune on them.
- `ftw view`: map preview of a query or file. `ftw view --web` serves a local MapLibre page.
- `ftw tui`, `ftw mcp`, `ftw schema [command]` (JSON Schema of a command's parameters).

## Data sources

| Source | Where | Format | Notes |
|---|---|---|---|
| FTW Global Data 2nd Edition | `data.source.coop/ftw/global-data-2e` | GeoParquet 1.1 by year and UTM zone, PMTiles per year, COG probabilities | EPSG:4326, CC-BY-4.0, `index/vector.parquet` and `index/raster.parquet` for file pruning |
| Harmonized field data | `data.source.coop/ftw/harmonized-field-data` | fiboa GeoParquet + PMTiles per collection and year | native CRS per collection, license per collection |
| USDA CDL | NASS CropScape clip API, national zips | GeoTIFF, EPSG:5070 | 30 m to 2023, 10 m from 2024; no maintained COG mirror |
| USDA Crop Sequence Boundaries | harmonized catalog `us_usda_cropland` | GeoParquet | CONUS field polygons |
| io-lulc, WorldCereal | Planetary Computer, Zenodo | COG | cropland masks for filtering |

## Visualization

No Python library renders vector tiles in a terminal, so previews are rendered images. We draw the fields over a basemap to PNG and show it with the kitty or sixel graphics protocol through textual-image, falling back to half-block characters over plain SSH and to a stats table when there's no terminal. `ftw view --web` serves the 2nd Edition PMTiles and local results to a MapLibre page, which over SSH on a cluster is one port forward.

## Agents

- Every command's `--help` is written for a reader who has never seen FTW.
- `ftw schema` exposes parameters as JSON Schema, and `ftw mcp` exposes the same functions as MCP tools.
- A `SKILL.md` ships in the package with worked examples (query an AOI, join CDL, export for training).

## Relationship to ftw-baselines

ftw-baselines keeps training, evaluation, and the model registry it publishes. ftw-cli reads that registry's checkpoints. Both packages currently want the `ftw` command, which is the first open question.

## Milestones

1. Data: `data` and `catalog` query and export, JSON contract, CI. (This slice starts it.)
2. Joins: CDL and harmonized-catalog joins, `join sql`.
3. Viewing: `view` previews, `view --web`, the TUI shell.
4. Inference: port imagery search and inference without Lightning, `models` commands.
5. Agents: `schema`, `mcp`, SKILL.md, training chip export.

## Open questions

- **Who owns `ftw`?** I'd give the command to ftw-cli and rename the baselines entry point to `ftw-baselines`, so practitioners type `ftw` and researchers keep their tooling under a clearly separate name.
- **DuckDB 2.0.** It's four days old and changed its Python API (`fetchone` is gone from relations), so we pin `<3` and test against it.
