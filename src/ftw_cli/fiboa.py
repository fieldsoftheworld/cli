"""Write fiboa GeoParquet: GeoParquet 1.1 with a bbox covering and a Vecorel collection.

fiboa 0.3 builds on Vecorel 0.1. A file names its schemas in a ``collection`` metadata
entry, which also carries properties that are the same for every row, such as
``determination:method``. ``fiboa validate`` checks the result.
"""

import json
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

SCHEMAS = [
    "https://vecorel.org/specification/v0.1.0/schema.yaml",
    "https://fiboa.org/specification/v0.3.0/schema.yaml",
    "https://vecorel.org/geometry-metrics-extension/v0.1.0/schema.yaml",
]
ROW_GROUP_SIZE = 50_000
# Vecorel requires these; every other column must be nullable.
REQUIRED = {"id", "geometry", "collection"}


def collection(name: str, **properties: object) -> dict:
    """Collection metadata for a file whose rows all belong to collection ``name``."""
    return {"schemas": {name: SCHEMAS}, "collection": name, **properties}


def copy(
    con: duckdb.DuckDBPyConnection,
    query: str,
    out: Path,
    meta: dict,
    params: list | None = None,
    geometry_types: list[str] | None = None,
) -> int:
    """Write the rows of ``query`` to ``out`` and return how many there were.

    ``query`` has a lon/lat GEOMETRY column ``geometry`` and no ``bbox``; this adds the
    bbox covering and sorts rows along a Hilbert curve so readers can skip row groups.
    Batches go through pyarrow because fiboa fixes nullability and timestamp units that
    DuckDB's Parquet writer infers.
    """
    reader = con.sql(
        f"""
        with rows as ({query}),
             extent as (select ST_Extent(ST_Extent_Agg(geometry)) as box from rows)
        select rows.* exclude (geometry),
               ST_AsWKB(geometry) as geometry,
               struct_pack(
                   xmin := ST_XMin(geometry), ymin := ST_YMin(geometry),
                   xmax := ST_XMax(geometry), ymax := ST_YMax(geometry)
               ) as bbox
        from rows, extent
        order by ST_Hilbert(geometry, extent.box)
        """,
        params=params or [],
    ).to_arrow_reader(ROW_GROUP_SIZE)
    schema = _schema(reader.schema).with_metadata(
        {"geo": json.dumps(_geo(geometry_types)), "collection": json.dumps(meta)}
    )
    count = 0
    with pq.ParquetWriter(out, schema, compression="zstd") as writer:
        for batch in reader:
            writer.write_table(pa.Table.from_batches([batch]).cast(schema))
            count += batch.num_rows
    return count


def _schema(source: pa.Schema) -> pa.Schema:
    """``source`` with fiboa's nullability and millisecond UTC timestamps.

    Arrow timestamps are UTC instants, so relabeling the zone keeps the values.
    """
    fields = []
    for field in source:
        kind = field.type
        if pa.types.is_timestamp(kind):
            kind = pa.timestamp("ms", tz="UTC" if kind.tz else None)
        fields.append(pa.field(field.name, kind, nullable=field.name not in REQUIRED))
    return pa.schema(fields)


def _geo(geometry_types: list[str] | None) -> dict:
    # No "crs" means OGC:CRS84, longitude then latitude, which is what the rows hold.
    return {
        "version": "1.1.0",
        "primary_column": "geometry",
        "columns": {
            "geometry": {
                "encoding": "WKB",
                "geometry_types": geometry_types or ["Polygon", "MultiPolygon"],
                "covering": {
                    "bbox": {k: ["bbox", k] for k in ("xmin", "ymin", "xmax", "ymax")}
                },
            }
        },
    }
