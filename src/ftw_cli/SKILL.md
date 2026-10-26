---
name: ftw
description: Agricultural field boundaries for any place on Earth with the ftw CLI from Fields of The World. Use for field or farm polygons, field counts and area for a place, how fields changed from 2017 to 2025, detecting fields in new Sentinel-2 imagery, or writing field boundaries to GeoParquet, GeoPackage, GeoJSON, or FlatGeobuf.
---

# ftw

`ftw` queries the FTW 2nd Edition, field boundaries for 2017 to 2025 worldwide, where it is published, and runs FTW models on new Sentinel-2 imagery. Piped output is JSON on stdout. Progress, the geocoded place, and errors go to stderr.

- `data query` prints `{year, fields, area_ha, mean_score}`, with `area_ha` the total field area in hectares. `data history` prints a list of those, one per year.
- With `--out`, the output includes `{path, fields, crs}`, so `crs` confirms the CRS written.
- Without `--year`, `data query` uses the latest year (2025). An area with no published fields gives `fields: 0`, and `--out` then fails with an error.

## Pick a command

| Need | Command |
|---|---|
| Field count, area, and mean score in an area | `ftw data query AREA [--year Y]` |
| The fields themselves | `ftw data query AREA --year Y --out fields.parquet` |
| How the fields changed over the years | `ftw data history AREA` |
| Fields in imagery from any year, including ones the 2nd Edition lacks | `ftw detect AREA --year Y --out fields.gpkg` |
| A picture of the fields | `ftw view AREA --out map.png` |
| Whether the install and network work | `ftw doctor` |
| Every parameter of a command as JSON Schema | `ftw schema [COMMAND]` |

AREA is a quoted place name (`"Story County, Iowa"`) or `xmin,ymin,xmax,ymax` in degrees. A place name means that place's own bounding box, so a city is its urban extent; for the farmland around a town, use its county or a bbox. Check the place and bbox printed on stderr, since a vague name can geocode to the wrong place.

## Rules

- Use `data query` rather than `detect` for 2017 to 2025. It reads precomputed fields in seconds; `detect` downloads imagery and runs a model, which takes tens of seconds to minutes and needs `pip install 'ftw-cli[inference]'`.
- `--out` picks the format from the suffix. `.parquet` is fiboa GeoParquet and the only format that keeps the collection metadata (model, method, date). Use `.gpkg` for desktop GIS and `.geojson` for small web maps.
- Output is EPSG:4326 unless you pass `--crs` with an EPSG code (`3857`) or `utm`. `utm` is the zone at the area's center for `data query` and the imagery's own zone for `detect`. `metrics:area` is always in m² and `metrics:perimeter` in m.
- Areas over 10,000 km² read several GB. Narrow the area first when you can.
- A failure prints one `error:` line on stderr and exits 1. A missing extra says which `pip install` fixes it.
- `score` (0 to 100) in the 2nd Edition is the model's confidence in a field.

## Examples

```sh
ftw data query "Chartres, France" --year 2023 | jq .fields
ftw data history "Ames, Iowa" | jq '.[] | {year, fields}'
ftw data query "Story County, Iowa" --year 2023 --out story.gpkg --crs utm | jq .crs
ftw data query "-93.7,41.95,-93.6,42.05" --out ames.parquet
ftw detect "Poltava, Ukraine" --year 2024 --out poltava.gpkg
ftw schema detect
```
