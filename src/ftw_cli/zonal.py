"""Per-field raster statistics, weighted by how much of each pixel a field covers.

exactextract weights every pixel by the fraction of it inside the field, so a field
smaller than a pixel still gets statistics and edge pixels count for what they overlap.
"""

import json
import re
from pathlib import Path

import duckdb
import pyarrow as pa
import rasterio
from exactextract import exact_extract
from exactextract.feature import JSONFeatureSource
from exactextract.raster import RasterioRasterSource

from ftw_cli import fiboa, vectors

STATS = ("mean", "stdev", "min", "max", "count")


def add(
    fields: Path, rasters: list[Path], out: Path, bands: list[int] | None = None
) -> dict:
    """Copy ``fields`` to ``out`` with each raster band's statistics as new columns.

    Columns are ``{band}_{stat}``, where ``band`` is the band's description or
    ``{raster}_b{n}``. ``count`` is the number of pixels, weighted by coverage.
    """
    vectors.check(out)
    con = vectors.connect()
    # A multi-part field is measured as the one Polygon it is written as.
    con.execute(
        "create table fields as select *, row_number() over () - 1 as _row from ("
        f"select * replace ({vectors.polygon()} as geometry) from {vectors.source(fields)}"
        ") where geometry is not null"
    )
    [(crs,)] = con.execute("select any_value(ST_CRS(geometry)) from fields").fetchall()
    crs = "EPSG:4326" if crs in (None, "OGC:CRS84") else crs
    taken = {d[0] for d in con.execute("select * from fields limit 0").description}
    original = set(taken)
    for i, path in enumerate(rasters):
        table = _stats(con, path, crs, bands, taken)
        con.register(f"stats_{i}", table)
        con.execute(
            f"create or replace table fields as select * from fields "
            f"left join stats_{i} using (_row)"
        )
        taken |= set(table.column_names)
    parquet = fields.suffix.lower() == ".parquet"
    meta = fiboa.read_collection(con, str(fields)) if parquet else None
    meta = meta or fiboa.collection("ftw-cli")
    custom = meta.setdefault("schemas:custom", {"$schema": fiboa.SDL, "properties": {}})
    for column in sorted(taken - original):
        custom["properties"][column] = {
            "type": "double",
            "description": f"{column.rsplit('_', 1)[1]} of the field's pixels, weighted by coverage",
        }
    query = "select * exclude (_row) from fields order by _row"
    count = vectors.write(con, query, out, meta, crs=crs)
    return {"path": str(out), "fields": count}


def _stats(
    con: duckdb.DuckDBPyConnection,
    path: Path,
    crs: str,
    bands: list[int] | None,
    taken: set[str],
) -> pa.Table:
    with rasterio.open(path) as ds:
        chosen = bands or list(range(1, ds.count + 1))
        names = [_name(ds, path, b, taken) for b in chosen]
        # Outlines traced from a raster have vertices on pixel edges, and a reprojection
        # round trip leaves them 1e-9 off; exactextract 0.3 then skips whole interior
        # pixels. Snapping to a millionth of a pixel removes that noise.
        snap = abs(ds.res[0]) / 1e6
        rows = con.execute(
            "select _row, ST_AsGeoJSON(ST_ReducePrecision("
            f"ST_Transform(geometry, '{crs}', '{ds.crs}'), {snap})) from fields order by _row"
        ).fetchall()
        features = [
            {"type": "Feature", "properties": {"_row": r}, "geometry": json.loads(g)}
            for r, g in rows
        ]
        results = exact_extract(
            [
                RasterioRasterSource(ds, b, name=n)
                for b, n in zip(chosen, names, strict=True)
            ],
            JSONFeatureSource(features, srs_wkt=ds.crs.to_wkt()),
            list(STATS),
            include_cols=["_row"],
        )
    props = [f["properties"] for f in results]
    columns = {"_row": [p["_row"] for p in props]}
    for name in names:
        for stat in STATS:
            # With one band, exactextract leaves the band name off its columns.
            key = stat if len(names) == 1 else f"{name}_{stat}"
            # min and max follow the raster's dtype; every stat is declared a double.
            columns[f"{name}_{stat}"] = pa.array([p[key] for p in props], pa.float64())
    return pa.table(columns)


def _name(ds: rasterio.DatasetReader, path: Path, band: int, taken: set[str]) -> str:
    """The band's description, or ``{raster}_b{n}``; prefixed by the raster if taken."""
    desc = ds.descriptions[band - 1]
    name = _safe(desc) if desc else f"{_safe(path.stem)}_b{band}"
    if any(c.startswith(f"{name}_") for c in taken):
        name = f"{_safe(path.stem)}_{name}"
    return name


def _safe(text: str) -> str:
    return re.sub(r"\W+", "_", text).strip("_")
