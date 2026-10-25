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

## Develop

```sh
uv sync --extra dev
uv run pre-commit run --all-files
uv run pytest                 # unit tests
uv run pytest -m integration  # reads from Source Coop
```
