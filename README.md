# ftw-cli

Field boundaries from [Fields of The World](https://fieldsofthe.world/) for practitioners and agents. Query the global FTW 2nd Edition boundaries in place, export an area to the format you need, and (soon) join them with crop type data or run FTW models on your own imagery.

Early work in progress; see [docs/design.md](docs/design.md) for where this is going.

## Install

```sh
uv tool install git+https://github.com/fieldsoftheworld/cli
```

## Use

```sh
# Per-year totals for the FTW 2nd Edition (2017-2025, 1.24B parcels)
ftw data info

# Which partition files a query would read, before reading anything
ftw data files --bbox -93.7,41.95,-93.6,42.05

# Field count, area, and mean score in a box
ftw data query --bbox -93.7,41.95,-93.6,42.05 --year 2024

# Export the fields; the format follows the suffix (.parquet, .geojson, .gpkg, .fgb)
ftw data query --bbox -93.7,41.95,-93.6,42.05 --out iowa.parquet
```

Output is a table in a terminal and JSON when piped. Pass `--json` to force JSON.

## Develop

```sh
uv sync --extra dev
uv run pre-commit run --all-files
uv run pytest                 # unit tests
uv run pytest -m integration  # reads from Source Coop
```
