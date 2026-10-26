# Design

ftw-baselines trains and evaluates the FTW models. ftw-cli is for using them: querying the precomputed global field boundaries, running released models on new imagery, and comparing results with other field boundary datasets. It serves people at a terminal and agents calling it from scripts, and it doesn't depend on torch or ftw-baselines.

## Principles

- Every feature is a plain function in `ftw_cli` that returns data, and the command line is a thin layer over them.
- ftw-cli writes standard files and leaves interactive viewing to tools built for it, such as QGIS and lonboard. Rasters are Cloud Optimized GeoTIFFs (DEFLATE with a predictor, 512 px tiles, averaged overviews). Vector Parquet is fiboa 0.3 GeoParquet: GeoParquet 1.1 with a bbox covering column, Vecorel collection metadata, fiboa's column nullability, and rows sorted along a Hilbert curve. `fiboa.py` streams DuckDB's Arrow batches through pyarrow, because DuckDB's own Parquet writer infers nullability that fiboa fixes. COG bands carry their min/max/mean/stddev/valid-percent statistics inside the file, as the [Portolan](https://portolan-sdi.org) formats spec requires. A CI job checks sample outputs with `fiboa validate`, `rio cogeo validate --strict`, and rashid's Portolan data rules. ftw-cli follows the Portolan formats but doesn't depend on portolan-cli, which pins DuckDB below 1.5.5.
- Data is read where it lives. DuckDB reads the 2nd Edition's small file index, then only the row groups that overlap the request. A 10 km box in Iowa reads one 2.3 GB file in about 3 seconds.
- Output is a table in a terminal and JSON when piped or with `--json`. Errors go to stderr with a non-zero exit, and nothing prompts without a terminal.
- Commands are noun-verb like GDAL's (`ftw data query`, `ftw imagery download`), take an area as a place name, `xmin,ymin,xmax,ymax`, or a polygon file (also as `--bbox` or `--place`), and pick the output format from the file suffix. Place names go through Nominatim with a User-Agent and a local cache, as its usage policy asks. A polygon file is read with DuckDB, reprojected to EPSG:4326 from the CRS it declares, and unioned. `BBox` carries the polygon as WKT, so queries keep the bbox predicate for row-group pruning and then test intersection with the polygon, and `detect` downloads imagery for the bbox and keeps the fields that touch the polygon.
- Progress bars go to stderr, only in a terminal. Library functions take an optional `progress(done, total)` callback and draw nothing themselves.

## Install

| Install | Adds | For |
|---|---|---|
| `ftw-cli` | cyclopts, rich, duckdb, pyarrow, pillow | querying, exporting, and drawing field boundaries |
| `ftw-cli[imagery]` | rasterio, pystac-client | crop-calendar scene selection and Sentinel-2 download |
| `ftw-cli[inference]` | the above plus onnxruntime | running FTW models |

## Commands

Working now:

- `ftw data info`: per-year totals for the 2nd Edition.
- `ftw data query AREA [--year] [--out] [--limit]`: summary stats per year, with a sparkline of the field count when there are several, or the fields written to `.parquet`, `.geojson`, `.gpkg`, or `.fgb` with a `year` column. `--year` is one year, `2019..2024`, `2018,2021`, or `all`; the years are read in parallel. A multi-year Parquet keeps each year's collection under `schemas`, each row's `collection` column says which applies, and properties that differ by year (`determination:details`) become columns.
- `ftw view AREA --out map.png`: the fields drawn to a PNG, colored by score.
- `ftw imagery search|download AREA --year`: the least cloudy Sentinel-2 scenes near the start and end of the growing season from the crop calendar, stacked into one 8-band GeoTIFF.
- `ftw doctor`: versions, extras, GPU, cache, and whether each remote service answers, run in parallel.
- `ftw schema [COMMAND]`: each command's parameters as JSON Schema, generated from cyclopts so it can't drift from `--help`; a test fails if any command or parameter lacks a description. `ftw skill [--out DIR]`: the packaged `SKILL.md`, printed or installed as `DIR/ftw/SKILL.md`.
- `ftw detect AREA --year --out`: imagery, model, and BoundaryVote in one step, writing fields to .parquet (fiboa), .gpkg, .geojson, or .fgb, with `--keep DIR` to keep the rasters and `--crs` (an EPSG code, or `utm`) for the output CRS. Non-4326 GeoParquet carries PROJJSON, which `fiboa.py` gets from DuckDB's bundled PROJ by writing a one-row file, so the base install needs no pyproj. `ftw predict` and `ftw polygonize` run the two halves, and `ftw models list` shows the models. Splitting with `fbp.FTW_2E` keeps touching fields apart; connected regions of the field class, as in ftw-baselines, merge them wherever the predicted boundary has a gap (240 polygons where the 2nd Edition has 1,028 in a 10 km box in Beauce).
- `ftw detect` with ftw-q, the default, follows the 2nd Edition pipeline in ftw-global-data-catalog's `pipeline/inference` and `postprocessing/outlines.py`. Tiles and their grids come from the mosaic mirror's per-quarter STAC GeoParquet (the latest fully published year stands in for earlier years, since the grid doesn't change). Each tile reads its window over the area plus 128 px (1.28 km) of halo, `quarterly.py` fills nodata (`window31`), divides by 3000, upsamples x4 (bilinear, half-pixel centers) and blends 512 px patches at 25% overlap with a Hann window, streaming one row of patches at a time so a full tile needs about 200 MB rather than 13 GB. It matches the catalog's torch `predict_tile` within 1/255 on 52 of 3.84M pixels of a real Ames window, and its nodata fill on all but one of 704k values. A tile keeps the fields whose centroid lies in its own 100 km MGRS square and UTM zone (with the 31V/32V exception). Fields that cross a UTM zone boundary can leave sliver overlaps between the two zones' copies (7 pairs, at most 1.7% of the smaller field, on a 7 by 4 km box across 90°W), which the 2nd Edition's seam union, at 10% overlap, would also leave.

Planned:

- `ftw catalog list|query`: the harmonized field data on Source Coop, 69 national collections with crop types.
- `ftw join catalog --fields f.parquet --collection nl`: match fields to harmonized parcels by IoU and carry their attributes over.
- `ftw join sql "..."`: DuckDB SQL with the datasets registered as views.
- FTP (PlanetScope), which needs its own imagery command.
- `ftw view --web`: a local MapLibre page over the 2nd Edition PMTiles and local files.

## Models

The CLI supports the recent models only: PRUE (FTW v3 and the v3.1 CC-BY variants), FTP, and ftw-q. The v1 and v2 models and DelineateAnything stay in ftw-baselines. The CLI reads single-file ONNX models from Hugging Face under `fieldsoftheworld`, with URLs pinned to a commit: FTP B3/B7, ftw-q, and the PRUE CC-BY B3/B5/B7 (`ftw-prue-ccby`, commit `ffe4452`). The v3 PRUE models without the CC-BY restriction are still checkpoints on GitHub releases and need the same export. Those exports match torch's predicted class on every pixel of the test image (logits within 3e-4), and onnxruntime runs a 256x512 tile in under 0.1 s on CPU.

### Parity with ftw-baselines

The ONNX models in `ftw predict` match `ftw-tools inference run` with the same checkpoint: on an Iowa scene, the predicted class differs on 11 of 936k pixels, from ONNX and PyTorch floating point ties. That needed two details from the torch pipeline. Patches follow torchgeo's `GriddedPatchSampler` grid, centered on the image (a grid anchored at the corner changes 5.6% of pixels, since the models depend on patch context), and the 2x upsampling is bilinear with aligned corners, matching kornia's `Resize`. Scene selection picks the same scenes. `infer polygonize` differs from ftw-baselines by design, since it splits fields with BoundaryVote instead of taking connected regions.

## Data

| Source | Location | Notes |
|---|---|---|
| FTW Global Data 2nd Edition | `data.source.coop/ftw/global-data-2e` | GeoParquet by year and UTM zone, EPSG:4326, CC-BY-4.0. `index/vector.parquet` maps files to bounding boxes. PMTiles per year. |
| Harmonized field data | `data.source.coop/ftw/harmonized-field-data` | fiboa GeoParquet per collection and year, native CRS, license per collection. |
| Sentinel-2 L2A | Planetary Computer, EarthSearch | Same sources and crop calendar as ftw-baselines. |

## Viewing

`ftw view` draws the fields to a PNG with Pillow, colored along the FTW green ramp by score and corrected for latitude, as a quick look that people and agents can open anywhere. Anything interactive is for QGIS, lonboard, or similar tools reading the GeoParquet. `ftw view --web` will serve the 2nd Edition PMTiles and local files to a MapLibre page, which over SSH is one port forward.

## Agents

Help text is written for a reader new to FTW. `ftw schema` describes each command's parameters, and a `SKILL.md` ships with worked examples.

## ftw-baselines

ftw-cli will own the `ftw` command. When we switch, ftw-baselines' command becomes `ftw-tools` (its PyPI name) and its `data` and `inference` groups print deprecation warnings (ftw-baselines#303, draft). Until then, installing both in one environment means whichever installs last owns `ftw`.

## Milestones

1. `data` query and export, `view`, CI. (Done.)
2. `imagery`, `models`, `detect`, `predict`, and `polygonize` with BoundaryVote, and tiled ftw-q detection on quarterly mosaics. (Done.)
3. `catalog`, `join catalog`, and `join sql`.
4. `schema` and `SKILL.md`. (Done.) `view --web`.

## Open questions

- DuckDB 2.0 is days old. It changed its Python API, and its late-materialization optimizer crashes on our top-N query over the bbox filter, so `connect()` disables that optimizer. We pin `<3` and test against it.
