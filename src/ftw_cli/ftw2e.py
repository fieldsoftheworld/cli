"""FTW Global Data 2nd Edition on Source Coop, queried in place with DuckDB."""

import json
import math
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import duckdb

from ftw_cli import fiboa

BASE_URL = "https://data.source.coop/ftw/global-data-2e"
VECTOR_INDEX = f"{BASE_URL}/index/vector.parquet"

# GDAL drivers DuckDB spatial uses for non-Parquet exports, keyed by file suffix.
EXPORT_DRIVERS = {".geojson": "GeoJSON", ".gpkg": "GPKG", ".fgb": "FlatGeobuf"}


@dataclass(frozen=True)
class BBox:
    xmin: float
    ymin: float
    xmax: float
    ymax: float

    @classmethod
    def parse(cls, text: str) -> "BBox":
        """Parse ``xmin,ymin,xmax,ymax`` in EPSG:4326 degrees."""
        parts = text.split(",")
        if len(parts) != 4:
            raise ValueError(f"bbox needs 4 comma-separated numbers, got {text!r}")
        bbox = cls(*(float(p) for p in parts))
        if not (-180 <= bbox.xmin < bbox.xmax <= 180):
            raise ValueError(
                f"bbox longitudes must satisfy -180 <= xmin < xmax <= 180: {text!r}"
            )
        if not (-90 <= bbox.ymin < bbox.ymax <= 90):
            raise ValueError(
                f"bbox latitudes must satisfy -90 <= ymin < ymax <= 90: {text!r}"
            )
        return bbox

    def overlaps(self, prefix: str = "") -> str:
        """SQL predicate for rows whose ``{prefix}xmin..ymax`` bounds intersect this box."""
        p = prefix
        return (
            f"{p}xmax >= {self.xmin} and {p}xmin <= {self.xmax} "
            f"and {p}ymax >= {self.ymin} and {p}ymin <= {self.ymax}"
        )

    def area_km2(self) -> float:
        lat = math.radians((self.ymin + self.ymax) / 2)
        return (
            (self.xmax - self.xmin)
            * (self.ymax - self.ymin)
            * 111.32**2
            * math.cos(lat)
        )

    def envelope(self) -> str:
        return f"ST_MakeEnvelope({self.xmin}, {self.ymin}, {self.xmax}, {self.ymax})"


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial;")
    con.execute("SET geometry_always_xy = true")
    con.execute("SET enable_progress_bar = false")
    # DuckDB 2.0.0 crashes ("Vector::Reference used on vector of different type")
    # on ORDER BY ... LIMIT over a filter on the bbox struct with this optimizer on.
    con.execute("SET disabled_optimizers = 'late_materialization'")
    return con


def info(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """Per-year totals from the vector index."""
    rows = con.execute(
        f"""
        select year, count(*) as files, sum(n_parcels) as parcels,
               round(sum(area_km2)) as area_km2, round(sum(size_bytes) / 1e9, 1) as size_gb
        from '{VECTOR_INDEX}' group by year order by year
        """
    )
    return _records(rows)


def years(con: duckdb.DuckDBPyConnection) -> list[int]:
    return [
        y
        for (y,) in con.execute(
            f"select distinct year from '{VECTOR_INDEX}' order by year"
        ).fetchall()
    ]


def history(
    bbox: BBox, progress: Callable[[int, int | None], None] | None = None
) -> list[dict]:
    """``summarize`` for every year, each on its own connection so the years run at once."""
    every = years(connect())
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(summarize, connect(), bbox, year) for year in every]
        for done, _ in enumerate(as_completed(futures), 1):
            if progress:
                progress(done, len(every))
    return [f.result() for f in futures]


def latest_year(con: duckdb.DuckDBPyConnection) -> int:
    return con.execute(f"select max(year) from '{VECTOR_INDEX}'").fetchone()[0]


def files(con: duckdb.DuckDBPyConnection, bbox: BBox, year: int) -> list[dict]:
    """Index rows for the partition files a query over ``bbox`` and ``year`` would read."""
    rows = con.execute(
        f"""
        select year, zone, href, n_parcels, round(size_bytes / 1e9, 2) as size_gb
        from '{VECTOR_INDEX}' where year = ? and {bbox.overlaps()} order by zone
        """,
        [year],
    )
    return _records(rows)


def summarize(con: duckdb.DuckDBPyConnection, bbox: BBox, year: int) -> dict:
    hrefs = [f["href"] for f in files(con, bbox, year)]
    summary = {"year": year, "bbox": list(vars(bbox).values()), "files": len(hrefs)}
    if not hrefs:
        return summary | {"fields": 0, "area_ha": 0, "mean_score": None}
    count, area, score = con.execute(
        f"""
        select count(*), round(sum("metrics:area") / 1e4, 1), round(avg(score), 1)
        from read_parquet(?) where {_in_bbox(bbox)}
        """,
        [hrefs],
    ).fetchone()
    return summary | {"fields": count, "area_ha": area or 0, "mean_score": score}


def fields(
    con: duckdb.DuckDBPyConnection, bbox: BBox, year: int, limit: int
) -> list[dict]:
    """Up to ``limit`` fields in ``bbox`` with GeoJSON geometry, largest first."""
    hrefs = [f["href"] for f in files(con, bbox, year)]
    if not hrefs:
        return []
    rows = con.execute(
        f"""
        select id, round("metrics:area" / 1e4, 2) as area_ha, score,
               ST_AsGeoJSON(geometry) as geometry
        from read_parquet(?) where {_in_bbox(bbox)}
        order by "metrics:area" desc limit {int(limit)}
        """,
        [hrefs],
    )
    records = _records(rows)
    for r in records:
        r["geometry"] = json.loads(r["geometry"])
    return records


def export(
    con: duckdb.DuckDBPyConnection, bbox: BBox, year: int, out: Path, limit: int | None
) -> dict:
    """Write the fields intersecting ``bbox`` to ``out``; the format follows its suffix.

    ``.parquet`` is fiboa GeoParquet with the 2nd Edition's own collection metadata.
    """
    hrefs = [f["href"] for f in files(con, bbox, year)]
    if not hrefs:
        raise LookupError(f"no FTW {year} fields intersect {bbox}")
    suffix = out.suffix.lower()
    if suffix not in (".parquet", *EXPORT_DRIVERS):
        supported = ", ".join([".parquet", *EXPORT_DRIVERS])
        raise ValueError(
            f"unsupported output format {suffix!r}; use one of {supported}"
        )
    limit_sql = f"limit {int(limit)}" if limit else ""
    query = f"""
        select * exclude (bbox, zone) from read_parquet(?)
        where {_in_bbox(bbox)} {limit_sql}
    """
    if suffix == ".parquet":
        row = con.execute(
            "select decode(value) from parquet_kv_metadata(?) where decode(key) = 'collection'",
            [hrefs[0]],
        ).fetchone()
        if row is None:
            raise LookupError(f"{hrefs[0]} has no fiboa collection metadata")
        count = fiboa.copy(con, query, out, json.loads(row[0]), [hrefs])
    else:
        driver = EXPORT_DRIVERS[suffix]
        row = con.execute(
            f"copy ({query}) to '{_quote(out)}' (FORMAT gdal, DRIVER '{driver}')",
            [hrefs],
        ).fetchone()
        count = row[0] if row else 0
    return {"path": str(out), "fields": count, "files_read": len(hrefs)}


def _in_bbox(bbox: BBox) -> str:
    # The bbox struct lets Parquet skip row groups; ST_Intersects drops bbox-only hits.
    return f"{bbox.overlaps('bbox.')} and ST_Intersects(geometry, {bbox.envelope()})"


def _quote(path: Path) -> str:
    return str(path).replace("'", "''")


def _records(rel: duckdb.DuckDBPyConnection) -> list[dict]:
    columns = [d[0] for d in rel.description]
    return [dict(zip(columns, row, strict=True)) for row in rel.fetchall()]
