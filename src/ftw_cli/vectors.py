"""Write field polygons in the format an output file's suffix names."""

from pathlib import Path

import duckdb

from ftw_cli import fiboa

DRIVERS = {".gpkg": "GPKG", ".geojson": "GeoJSON", ".fgb": "FlatGeobuf"}
FORMATS = (".parquet", *DRIVERS)


def check(out: Path) -> None:
    """Fail before any work starts if ``out`` names a format ftw-cli doesn't write."""
    if out.suffix.lower() not in FORMATS:
        raise ValueError(f"unsupported format {out.suffix!r}; use {', '.join(FORMATS)}")


def crs(text: str, utm: str) -> str:
    """``4326``, ``EPSG:3857``, or ``utm`` (meaning ``utm``) as an authority:code PROJ knows."""
    if text.lower() == "utm":
        return utm
    code = f"EPSG:{text}" if text.isdigit() else text.upper()
    duckdb.execute("INSTALL spatial; LOAD spatial")
    duckdb.execute(f"select ST_Transform(ST_Point(0, 0), 'EPSG:4326', '{code}', true)")
    return code


def utm_zone(lon: float, lat: float) -> str:
    """The WGS 84 UTM zone holding ``lon``, ``lat``."""
    return f"EPSG:{(32600 if lat >= 0 else 32700) + int((lon + 180) // 6) % 60 + 1}"


def polygon(column: str = "geometry") -> str:
    """SQL for ``column`` as one Polygon: a multi-part geometry keeps its largest polygon."""
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
    """Write ``query``'s rows to ``out`` and return how many there were.

    ``.parquet`` is fiboa GeoParquet with ``meta`` as its collection; the GDAL formats
    keep the columns but have nowhere to put the collection.
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
    path = str(out).replace("'", "''")
    sql = f"copy ({query}) to '{path}' (FORMAT gdal, DRIVER '{driver}', SRS '{crs}')"
    [(count,)] = con.execute(sql, params or []).fetchall()
    return count
