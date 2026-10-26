"""FTW Global Data 2nd Edition on Source Coop, queried in place with DuckDB."""

import json
import math
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import duckdb

from ftw_cli import vectors

BASE_URL = "https://data.source.coop/ftw/global-data-2e"
VECTOR_INDEX = f"{BASE_URL}/index/vector.parquet"

Progress = Callable[[int, int | None], None]


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
        box = cls(*(float(p) for p in parts))
        if not (
            -180 <= box.xmin < box.xmax <= 180 and -90 <= box.ymin < box.ymax <= 90
        ):
            raise ValueError(
                f"bbox must be xmin<xmax in -180..180, ymin<ymax in -90..90: {text!r}"
            )
        return box

    def __str__(self) -> str:
        return ",".join(f"{v:g}" for v in vars(self).values())

    def overlaps(self, prefix: str = "") -> str:
        """SQL predicate for rows whose ``{prefix}xmin..ymax`` bounds intersect this box."""
        p = prefix
        return (
            f"{p}xmax >= {self.xmin} and {p}xmin <= {self.xmax} "
            f"and {p}ymax >= {self.ymin} and {p}ymin <= {self.ymax}"
        )

    def envelope(self) -> str:
        return f"ST_MakeEnvelope({self.xmin}, {self.ymin}, {self.xmax}, {self.ymax})"

    def area_km2(self) -> float:
        lat = math.radians((self.ymin + self.ymax) / 2)
        return (
            (self.xmax - self.xmin)
            * (self.ymax - self.ymin)
            * 111.32**2
            * math.cos(lat)
        )


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial;")
    con.execute("SET geometry_always_xy = true; SET enable_progress_bar = false")
    # DuckDB 2.0.0 crashes ("Vector::Reference used on vector of different type")
    # on ORDER BY ... LIMIT over a filter on the bbox struct with this optimizer on.
    con.execute("SET disabled_optimizers = 'late_materialization'")
    return con


def info(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """Per-year totals from the vector index."""
    return _records(
        con.execute(
            f"""
            select year, count(*) as files, sum(n_parcels) as parcels,
                   round(sum(area_km2)) as area_km2, round(sum(size_bytes) / 1e9, 1) as size_gb
            from '{VECTOR_INDEX}' group by year order by year
            """
        )
    )


def years(con: duckdb.DuckDBPyConnection) -> list[int]:
    query = f"select distinct year from '{VECTOR_INDEX}' order by year"
    return [y for (y,) in con.execute(query).fetchall()]


def hrefs(con: duckdb.DuckDBPyConnection, bbox: BBox, year: int) -> list[str]:
    """The partition files a query over ``bbox`` and ``year`` reads."""
    query = f"select href from '{VECTOR_INDEX}' where year = ? and {bbox.overlaps()}"
    return [h for (h,) in con.execute(query, [year]).fetchall()]


def summarize(con: duckdb.DuckDBPyConnection, bbox: BBox, year: int) -> dict:
    files = hrefs(con, bbox, year)
    row = (0, 0, None)
    if files:
        row = con.execute(
            f"""
            select count(*), round(sum("metrics:area") / 1e4, 1), round(avg(score), 1)
            from read_parquet(?) where {_in_bbox(bbox)}
            """,
            [files],
        ).fetchone()
    count, area, score = row or (0, 0, None)
    return {"year": year, "fields": count, "area_ha": area or 0, "mean_score": score}


def history(bbox: BBox, progress: Progress | None = None) -> list[dict]:
    """``summarize`` for every year, each on its own connection so the years run at once."""
    every = years(connect())
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(summarize, connect(), bbox, year) for year in every]
        for done, _ in enumerate(as_completed(futures), 1):
            if progress:
                progress(done, len(every))
    return [f.result() for f in futures]


def fields(
    con: duckdb.DuckDBPyConnection, bbox: BBox, year: int, limit: int
) -> list[dict]:
    """Up to ``limit`` fields in ``bbox`` with GeoJSON geometry, largest first."""
    files = hrefs(con, bbox, year)
    if not files:
        return []
    rows = _records(
        con.execute(
            f"""
            select id, round("metrics:area" / 1e4, 2) as area_ha, score,
                   ST_AsGeoJSON(geometry) as geometry
            from read_parquet(?) where {_in_bbox(bbox)}
            order by "metrics:area" desc limit {int(limit)}
            """,
            [files],
        )
    )
    return [r | {"geometry": json.loads(r["geometry"])} for r in rows]


def export(
    con: duckdb.DuckDBPyConnection,
    bbox: BBox,
    year: int,
    out: Path,
    limit: int | None,
    crs: str = "4326",
) -> dict:
    """Write the fields in ``bbox`` to ``out`` in the format its suffix names.

    ``.parquet`` keeps the 2nd Edition's own fiboa collection metadata. ``crs`` is an
    EPSG code, or ``utm`` for the UTM zone at the center of ``bbox``.
    """
    vectors.check(out)
    center = ((bbox.xmin + bbox.xmax) / 2, (bbox.ymin + bbox.ymax) / 2)
    target = vectors.crs(crs, utm=vectors.utm_zone(*center))
    files = hrefs(con, bbox, year)
    if not files:
        raise LookupError(f"no FTW {year} fields intersect {bbox}")
    # fetchall: DuckDB 2.0 refuses new work while a result is still open.
    [(meta,)] = con.execute(
        "select decode(value) from parquet_kv_metadata(?) where decode(key) = 'collection'",
        [files[0]],
    ).fetchall()
    query = f"""
        select * exclude (bbox, zone)
                 replace (ST_Transform(geometry, 'EPSG:4326', '{target}') as geometry)
        from read_parquet(?)
        where {_in_bbox(bbox)} {f"limit {int(limit)}" if limit else ""}
    """
    count = vectors.write(con, query, out, json.loads(meta), [files], crs=target)
    return {"path": str(out), "fields": count, "crs": target}


def _in_bbox(bbox: BBox) -> str:
    # The bbox struct lets Parquet skip row groups; ST_Intersects drops bbox-only hits.
    return f"{bbox.overlaps('bbox.')} and ST_Intersects(geometry, {bbox.envelope()})"


def _records(rel: duckdb.DuckDBPyConnection) -> list[dict]:
    columns = [d[0] for d in rel.description]
    return [dict(zip(columns, row, strict=True)) for row in rel.fetchall()]
