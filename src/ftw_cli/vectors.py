"""Read and write field polygons with DuckDB, in the format a file's suffix names."""

from pathlib import Path

import duckdb

from ftw_cli import fiboa

DRIVERS = {".gpkg": "GPKG", ".geojson": "GeoJSON", ".fgb": "FlatGeobuf"}
FORMATS = (".parquet", *DRIVERS)


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial")
    con.execute("SET geometry_always_xy = true; SET enable_progress_bar = false")
    # DuckDB 2.0.0 crashes ("Vector::Reference used on vector of different type")
    # on ORDER BY ... LIMIT over a filter on the bbox struct with this optimizer on.
    con.execute("SET disabled_optimizers = 'late_materialization'")
    return con


def quote(text: object) -> str:
    """``text`` as a SQL string literal."""
    return "'" + str(text).replace("'", "''") + "'"


def source(path: Path) -> str:
    """SQL for a vector file's rows, with its geometry as ``geometry``.

    GDAL's ``fid`` and fiboa's ``bbox`` are dropped; writers add their own.
    """
    if path.suffix.lower() == ".parquet":
        reader = f"read_parquet({quote(path)})"
    else:
        reader = f"(select * rename (geom as geometry) from ST_Read({quote(path)}))"
    return f"(select columns(lambda c: c not in ('bbox', 'fid')) from {reader})"


def check(out: Path) -> None:
    """Fail before any work starts if ``out`` names a format ftw-cli doesn't write."""
    if out.suffix.lower() not in FORMATS:
        raise ValueError(f"unsupported format {out.suffix!r}; use {', '.join(FORMATS)}")


def crs(text: str, utm: str) -> str:
    """``4326``, ``EPSG:3857``, or ``utm`` (returns ``utm``) as a code PROJ knows."""
    if text.lower() == "utm":
        return utm
    code = f"EPSG:{text}" if text.isdigit() else text.upper()
    connect().execute(
        f"select ST_Transform(ST_Point(0, 0), 'EPSG:4326', '{code}', true)"
    )
    return code


def utm_zone(lon: float, lat: float) -> str:
    return f"EPSG:{(32600 if lat >= 0 else 32700) + int((lon + 180) // 6) % 60 + 1}"


def polygon(column: str = "geometry") -> str:
    """SQL for ``column`` as one Polygon: a multi-part geometry keeps its largest."""
    return (
        f"list_max(list_transform(list_filter(ST_Dump({column}), "
        "lambda d: ST_GeometryType(d.geom)::varchar = 'POLYGON'), "
        "lambda d: {'area': ST_Area(d.geom), 'geom': d.geom})).geom"
    )


def write(
    con: duckdb.DuckDBPyConnection,
    query: str,
    out: Path,
    meta: dict,
    params: list | None = None,
    crs: str = "EPSG:4326",
) -> int:
    """Write ``query``'s rows to ``out``, each geometry as one Polygon; returns the count.

    ``.parquet`` is fiboa GeoParquet with ``meta`` as its collection; the GDAL formats
    have nowhere to put it.
    """
    check(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    query = (
        f"select * from (select * replace ({polygon()} as geometry) from ({query})) "
        "where geometry is not null"
    )
    if out.suffix.lower() == ".parquet":
        return fiboa.copy(con, query, out, meta, params, crs)
    out.unlink(missing_ok=True)  # GDAL appends to an existing GeoPackage
    driver = DRIVERS[out.suffix.lower()]
    sql = (
        f"copy ({query}) to {quote(out)} (FORMAT gdal, DRIVER '{driver}', SRS '{crs}')"
    )
    [(count,)] = con.execute(sql, params or []).fetchall()
    return count
