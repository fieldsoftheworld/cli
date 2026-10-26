# ftw-cli

Field boundaries from [Fields of The World](https://fieldsofthe.world/), for people and agents. Query the global FTW 2nd Edition in place and export any area to the format you need. Running models on new imagery is next; see [docs/design.md](docs/design.md).

## Install

```sh
uv tool install git+https://github.com/fieldsoftheworld/cli
```

## Use

```sh
ftw data info                                        # per-year totals, 2017-2025
ftw data files --bbox -93.7,41.95,-93.6,42.05        # files a query would read
ftw data query --bbox -93.7,41.95,-93.6,42.05        # field count, area, mean score
ftw data query --bbox -93.7,41.95,-93.6,42.05 --year 2024 --out iowa.parquet
```

`--out` accepts `.parquet`, `.geojson`, `.gpkg`, and `.fgb`. Output is a table in a terminal and JSON when piped; `--json` forces JSON.

```sh
ftw view --bbox 1.55,48.20,1.70,48.30 --out beauce.png   # map of the fields, colored by score
```

## Run models on new imagery

```sh
uv tool install 'ftw-cli[inference] @ git+https://github.com/fieldsoftheworld/cli'

# Everything at once: scenes from the crop calendar, download, inference, polygons
ftw infer all --bbox -93.7,41.95,-93.6,42.05 --year 2024 --out-dir iowa

# Or step by step
ftw imagery search --bbox -93.7,41.95,-93.6,42.05 --year 2024
ftw imagery download --bbox -93.7,41.95,-93.6,42.05 --year 2024 --out iowa.tif
ftw infer run iowa.tif --out scores.tif --model prue-ccby-b5
ftw infer polygonize scores.tif --out fields.parquet
```

`ftw imagery` picks the least cloudy Sentinel-2 scene near the start and end of the growing season from the crop calendar, as ftw-baselines does. `ftw infer` runs the models in ONNX through onnxruntime, so there's no PyTorch dependency, and uses a GPU when onnxruntime has one. `ftw models list` shows the models; they download once to `~/.cache/ftw-cli` (or `$FTW_CACHE_DIR`). `ftw infer run` writes field and boundary probabilities, and `ftw infer polygonize` splits them into fields with BoundaryVote from [ftw-fbp](https://pypi.org/project/ftw-fbp/), using `fbp.FTW_2E`, the setting of the FTW 2nd Edition. The polygons are GeoParquet in EPSG:4326 with the same columns as the 2nd Edition.

## TUI

```sh
uv tool install 'ftw-cli[tui] @ git+https://github.com/fieldsoftheworld/cli'
ftw tui
```

Pick an area and year, and the TUI shows the fields on a map colored by model score, with a summary and a table of the largest fields. `r` queries, `e` exports the area to GeoParquet, `q` quits. The map is a full-resolution image in terminals that support the kitty or sixel graphics protocols (kitty, WezTerm, iTerm2, recent Windows Terminal) and colored blocks elsewhere, including plain SSH.

## Develop

```sh
uv sync --extra dev
uv run pre-commit run --all-files
uv run pytest                 # unit tests
uv run pytest -m integration  # reads from Source Coop
```
