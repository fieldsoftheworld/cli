"""FTW Global Data 2nd Edition on Source Coop, queried in place with DuckDB."""

import json
import math
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb

from ftw_cli import vectors
from ftw_cli.filters import NO_FILTERS, Filters

BASE_URL = "https://data.source.coop/ftw/global-data-2e"
VECTOR_INDEX = f"{BASE_URL}/index/vector.parquet"

Progress = Callable[[int, int | None], None]
YEARS = re.compile(r"^\d{4}(\.\.\d{4}|(,\d{4})*)$")


@dataclass(frozen=True)
class BBox:
    xmin: float
    ymin: float
    xmax: float
    ymax: float
    # The area's polygon (WKT, EPSG:4326) when it is not the whole box, as from a file.
    shape: str | None = field(default=None, repr=False)

    @classmethod
    def parse(cls, text: str) -> "BBox":
        """Parse ``xmin,ymin,xmax,ymax`` in EPSG:4326 degrees."""
        parts = text.split(",")
        if len(parts) != 4:
            raise ValueError(f"bbox needs 4 comma-separated numbers, got {text!r}")
        xmin, ymin, xmax, ymax = (float(p) for p in parts)
        box = cls(xmin, ymin, xmax, ymax)
        if not (
            -180 <= box.xmin < box.xmax <= 180 and -90 <= box.ymin < box.ymax <= 90
        ):
            raise ValueError(
                f"bbox must be xmin<xmax in -180..180, ymin<ymax in -90..90: {text!r}"
            )
        return box

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return self.xmin, self.ymin, self.xmax, self.ymax

    def __str__(self) -> str:
        return ",".join(f"{v:g}" for v in self.bounds)

    def overlaps(self, prefix: str = "") -> str:
        """SQL predicate for rows whose ``{prefix}xmin..ymax`` bounds intersect this box."""
        p = prefix
        return (
            f"{p}xmax >= {self.xmin} and {p}xmin <= {self.xmax} "
            f"and {p}ymax >= {self.ymin} and {p}ymin <= {self.ymax}"
        )

    def envelope(self) -> str:
        """SQL for the area: its polygon when it has one, else the box."""
        if self.shape:
            return f"ST_GeomFromText('{self.shape}')"
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


# Size classes in hectares, as (key suffix, lower bound, upper bound).
SIZE_CLASSES = (
    ("lt_1ha", 0, 1),
    ("1_10ha", 1, 10),
    ("10_100ha", 10, 100),
    ("ge_100ha", 100, None),
)
QUANTILES = (
    ("p10_ha", 0.1),
    ("p25_ha", 0.25),
    ("median_ha", 0.5),
    ("p75_ha", 0.75),
    ("p90_ha", 0.9),
)


def summarize(
    con: duckdb.DuckDBPyConnection,
    bbox: BBox,
    year: int,
    filters: Filters = NO_FILTERS,
    stats: bool = False,
) -> dict:
    """Count, area, and mean score of the fields in ``bbox``; ``stats`` adds ``size_stats``."""
    files = hrefs(con, bbox, year)
    row = (0, 0, None)
    if files:
        # fetchall: DuckDB 2.0 refuses the next query while a result is still open.
        [row] = con.execute(
            f"""
            select count(*), round(sum("metrics:area") / 1e4, 1), round(avg(score), 1)
            from read_parquet(?) where {_in_bbox(bbox)} and {filters.sql()}
            """,
            [files],
        ).fetchall()
    count, area, score = row
    summary = {"year": year, "fields": count, "area_ha": area or 0, "mean_score": score}
    return summary | size_stats(con, bbox, files, filters) if stats else summary


def size_stats(
    con: duckdb.DuckDBPyConnection,
    bbox: BBox,
    files: list[str],
    filters: Filters = NO_FILTERS,
) -> dict:
    """Field-size quantiles, and the count and area of fields in each size class, in ha."""
    columns = [f"round(quantile_cont(ha, {q}), 2) as {name}" for name, q in QUANTILES]
    for name, lo, hi in SIZE_CLASSES:
        where = f"ha >= {lo}" + (f" and ha < {hi}" if hi else "")
        columns += [
            f"count(*) filter (where {where}) as fields_{name}",
            f"coalesce(round(sum(ha) filter (where {where}), 1), 0) as area_{name}_ha",
        ]
    source = (
        f'select "metrics:area" / 1e4 as ha from read_parquet(?) '
        f"where {_in_bbox(bbox)} and {filters.sql()}"
        if files
        else "select null::double as ha where false"
    )
    rel = con.execute(
        f"select {', '.join(columns)} from ({source})", [files] if files else []
    )
    return _records(rel)[0]


def parse_years(spec: str | None, published: list[int]) -> list[int]:
    """``2024``, ``2019..2024``, ``2018,2021,2024``, or ``all``; None means the latest."""
    if spec is None:
        return published[-1:]
    if spec == "all":
        return published
    if not YEARS.match(spec):
        raise ValueError(
            f"year must be 2024, 2019..2024, 2018,2021, or all, not {spec!r}"
        )
    if ".." in spec:
        start, end = (int(y) for y in spec.split(".."))
        chosen = list(range(start, end + 1))
    else:
        chosen = [int(y) for y in spec.split(",")]
    unpublished = sorted(set(chosen) - set(published))
    if not chosen or unpublished:
        span = f"{published[0]}..{published[-1]}"
        raise ValueError(
            f"no FTW data for {unpublished or spec}; published years are {span}"
        )
    return sorted(set(chosen))


def summaries(
    bbox: BBox,
    years: list[int],
    progress: Progress | None = None,
    filters: Filters = NO_FILTERS,
    stats: bool = False,
) -> list[dict]:
    """``summarize`` for each year, each on its own connection so the years run at once."""
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(summarize, connect(), bbox, year, filters, stats)
            for year in years
        ]
        for done, _ in enumerate(as_completed(futures), 1):
            if progress:
                progress(done, len(years))
    return [f.result() for f in futures]


def fields(
    con: duckdb.DuckDBPyConnection,
    bbox: BBox,
    year: int,
    limit: int,
    filters: Filters = NO_FILTERS,
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
            from read_parquet(?) where {_in_bbox(bbox)} and {filters.sql()}
            order by "metrics:area" desc limit {int(limit)}
            """,
            [files],
        )
    )
    return [r | {"geometry": json.loads(r["geometry"])} for r in rows]


def export(
    con: duckdb.DuckDBPyConnection,
    bbox: BBox,
    years: list[int],
    out: Path,
    limit: int | None,
    crs: str = "4326",
    filters: Filters = NO_FILTERS,
) -> dict:
    """Write the fields of ``years`` in ``bbox`` to ``out``, with a ``year`` column.

    The format follows the suffix. ``.parquet`` keeps the 2nd Edition's fiboa collection
    metadata; with several years, each row's ``collection`` says which year's it is.
    ``crs`` is an EPSG code, or ``utm`` for the UTM zone at the center of ``bbox``.
    """
    vectors.check(out)
    center = ((bbox.xmin + bbox.xmax) / 2, (bbox.ymin + bbox.ymax) / 2)
    target = vectors.crs(crs, utm=vectors.utm_zone(*center))
    files = {year: hrefs(con, bbox, year) for year in years}
    if not any(files.values()):
        raise LookupError(f"no FTW fields for {years} intersect {bbox}")
    files = {year: hrefs_ for year, hrefs_ in files.items() if hrefs_}
    # fetchall: DuckDB 2.0 refuses new work while a result is still open.
    metas = {
        year: json.loads(
            con.execute(
                "select decode(value) from parquet_kv_metadata(?) where decode(key) = 'collection'",
                [hrefs_[0]],
            ).fetchall()[0][0]
        )
        for year, hrefs_ in files.items()
    }
    meta, varying = _collection(list(metas.values()))
    selects = []
    for year in files:
        extra = "".join(f', {_literal(metas[year][key])} as "{key}"' for key in varying)
        selects.append(
            f"""
            select * exclude (bbox, zone)
                     replace (ST_Transform(geometry, 'EPSG:4326', '{target}') as geometry),
                   {year}::USMALLINT as year{extra}
            from read_parquet(?) where {_in_bbox(bbox)} and {filters.sql()}
            """
        )
    query = " union all by name ".join(selects)
    if limit:
        query = f"select * from ({query}) limit {int(limit)}"
    count = vectors.write(con, query, out, meta, list(files.values()), crs=target)
    return {"path": str(out), "fields": count, "years": list(files), "crs": target}


def _collection(metas: list[dict]) -> tuple[dict, list[str]]:
    """One file's collection metadata from per-year ones, and the keys that differ by year.

    Every year's schemas are kept, keyed by its collection id. A property with one value
    across the years stays in the metadata; one that differs becomes a column, except
    ``collection``, which the rows already carry.
    """
    meta: dict[str, Any] = {
        "schemas": {k: v for m in metas for k, v in m["schemas"].items()}
    }
    varying = []
    for key in dict.fromkeys(k for m in metas for k in m if k != "schemas"):
        values = [m.get(key) for m in metas]
        if all(v == values[0] for v in values):
            meta[key] = values[0]
        elif key != "collection":
            varying.append(key)
    custom = meta.setdefault("schemas:custom", {"properties": {}})
    custom["properties"]["year"] = {
        "type": "uint16",
        "description": "FTW 2nd Edition year the field was mapped for",
    }
    return meta, varying


def _literal(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def select(con: duckdb.DuckDBPyConnection, bbox: BBox, year: int) -> tuple[str, list]:
    """SQL for the fields in ``bbox`` in ``year``, and its parameters."""
    files = hrefs(con, bbox, year)
    if not files:
        raise LookupError(f"no FTW {year} fields intersect {bbox}")
    query = f"select * exclude (bbox, zone) from read_parquet(?) where {_in_bbox(bbox)}"
    return query, [files]


def _in_bbox(bbox: BBox) -> str:
    # The bbox struct lets Parquet skip row groups; ST_Intersects drops bbox-only hits.
    return f"{bbox.overlaps('bbox.')} and ST_Intersects(geometry, {bbox.envelope()})"


def _records(rel: duckdb.DuckDBPyConnection) -> list[dict]:
    columns = [d[0] for d in rel.description]
    return [dict(zip(columns, row, strict=True)) for row in rel.fetchall()]
