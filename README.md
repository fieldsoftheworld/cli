# ftw-cli

Field boundaries for any place on Earth, from your terminal. `ftw` queries the [Fields of The World](https://fieldsofthe.world/) 2nd Edition in place (every agricultural field from 2017 to 2025), exports any area to the format you work in, and runs the FTW models on new Sentinel-2 imagery without PyTorch.

![Field boundaries in Beauce, France, drawn by ftw view](docs/img/beauce.png)

<sub>`ftw view "1.55,48.20,1.70,48.30" --out beauce.png`: 986 fields in Beauce, France, from the 2025 edition, colored by model score.</sub>

## Install

```sh
uv tool install 'ftw-cli[inference] @ git+https://github.com/fieldsoftheworld/cli'
ftw --install-completion
```

The base install queries, exports, and draws the 2nd Edition. The `imagery` extra adds Sentinel-2 download, and `inference` adds the models.

## Use

An area is a place name, `xmin,ymin,xmax,ymax` in degrees, or a polygon file (`.geojson`, `.gpkg`, `.parquet`, `.fgb`, or `.shp`). Place names go through OpenStreetMap's geocoder and are cached. A file's polygons are merged into one area, so a county, a watershed, or a farm outline counts only the fields that touch it.

```sh
ftw data query "Story County, Iowa"                  # field count, area, mean score
ftw data query "Ames, Iowa" --year all              # the same for every year, with a trend line
ftw data query "Poltava, Ukraine" --year 2019..2024 --out poltava.parquet
ftw view "Chartres, France" --out chartres.png       # a map of the fields
```

`--out` writes `.parquet`, `.geojson`, `.gpkg`, or `.fgb`. Parquet output is [fiboa](https://fiboa.org) GeoParquet, as the 2nd Edition publishes it, sorted along a Hilbert curve with a bbox column so other readers can filter it by area. Queries read only the parts of the 2nd Edition that overlap the area, so a county in Iowa takes about 8 seconds. An export of several years is one file with a `year` column; in Parquet, each row's `collection` names its year's 2nd Edition collection.

`--min-area` and `--max-area` (m²) keep fields in a size range, and `--min-score` keeps fields the model scored at least that high (0 to 100). They work on `data query` and `view`, and the area bounds also work on `detect` and `polygonize`, which drop fields under 500 m² unless told otherwise:

```sh
ftw data query "Ames, Iowa" --min-area 50000 --min-score 80 --out big.parquet
```

`--year` takes one year, a range (`2019..2024`), a list (`2018,2021,2024`), or `all`. Several years give one summary row per year, ending with a trend line:

```text
fields  2017 █▅█▇▁▃▄▄▄ 2025  -14% since 2017
```

## Detect fields on new imagery

```sh
ftw detect "Ames, Iowa" --year 2024 --out ames.gpkg
```

By default this runs the way the FTW 2nd Edition was made. It reads the year's four quarterly cloudless Sentinel-2 mosaics from their anonymous [Source Cooperative mirror](https://source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics), one Sentinel-2 grid tile at a time and only the part over the area, runs the [ftw-q](https://huggingface.co/fieldsoftheworld/ftw-q) model through onnxruntime (on a GPU if there is one) at 2.5 m, and splits the result into fields with BoundaryVote ([ftw-fbp](https://pypi.org/project/ftw-fbp/)'s `FTW_2E`). A field on a tile seam is kept by the tile whose 100 km square and UTM zone hold its centre, as in the 2nd Edition. Expect about 1.5 minutes per tile on a CPU for a 10 km box. With `--model prue-ccby-b5` (or `-b3`, `-b7`) it instead picks two scenes near planting and harvest from the crop calendar. `--out` takes `.parquet`, `.gpkg`, `.geojson`, or `.fgb`; Parquet is fiboa GeoParquet with the model and BoundaryVote setting in its metadata, which the other formats have no place for. Output is in EPSG:4326 unless you pass `--crs`, either an EPSG code such as `3857` or `utm` to keep the imagery's UTM meters; `ftw data query --out` takes `--crs` too, where `utm` is the zone at the area's center. The rasters are deleted afterwards unless you pass `--keep DIR`; with ftw-q, each tile's probabilities are kept there as a COG, and a rerun over the same area reuses them instead of reading and predicting again. The steps are also separate commands:

```sh
ftw imagery download "Ames, Iowa" --year 2024 --out ames.tif
ftw predict ames.tif --out scores.tif --model prue-ccby-b7    # the PRUE models
ftw polygonize scores.tif --out fields.parquet
```

`ftw models list` shows the models. They download once to `~/.cache/ftw-cli` (or `$FTW_CACHE_DIR`). The imagery and the field and boundary probabilities are Cloud Optimized GeoTIFFs. CI checks sample outputs with `fiboa validate` and `rio cogeo validate`, so they open in QGIS, lonboard, GDAL, and the fiboa tools.

## For scripts and agents

Every command prints JSON when its output is piped, or with `--json`, and tables in a terminal. Progress goes to stderr and only in a terminal. Errors go to stderr with a non-zero exit, and nothing prompts.

```sh
ftw data query "Chartres, France" --year 2023 | jq .fields
ftw schema detect                       # every parameter of a command as JSON Schema
```

The `ftw` agent skill tells an agent which command answers which question, when to use the precomputed data instead of running a model, and which output format to pick. Install it whichever way suits your agent:

```sh
npx skills add fieldsoftheworld/cli                          # Claude Code, Cursor, Codex, and other agents
/plugin install ftw --marketplace fieldsoftheworld/cli       # inside Claude Code, as a plugin
ftw skill --out ~/.claude/skills                             # from an installed ftw
```

The skill is [`skills/ftw/SKILL.md`](skills/ftw/SKILL.md); while the repository is private, the first two need GitHub access to it.

## Troubleshooting

`ftw doctor` checks the install (versions, extras, GPU, cache) and whether Source Coop, Planetary Computer, Hugging Face, and Nominatim are reachable. Please include its output in bug reports. It exits non-zero when a check fails; extras you haven't installed show as missing.

## Develop

```sh
uv sync --all-extras
uv run pre-commit run --all-files
uv run pytest                 # unit tests
uv run pytest -m integration  # reads from Source Coop
```

[docs/design.md](docs/design.md) covers the design and what is planned.
