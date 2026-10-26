"""Match two sets of field polygons one to one by intersection over union (IoU)."""

from dataclasses import dataclass
from pathlib import Path

import duckdb

from ftw_cli import fiboa, vectors

# A field lies in another when at least this share of its area is inside it.
CONTAINED = 0.5


@dataclass(frozen=True)
class Comparison:
    """How well prediction fields reproduce reference fields.

    ``precision`` is the share of prediction fields matched, ``recall`` the share of
    reference fields matched, and ``matched_area`` the share of reference area in matched
    fields. ``merges`` counts prediction fields that hold two or more reference fields;
    ``splits`` counts reference fields that hold two or more prediction fields.
    """

    reference: int
    prediction: int
    matched: int
    precision: float
    recall: float
    f1: float
    mean_iou: float
    matched_area: float
    merges: int
    splits: int


def load(
    con: duckdb.DuckDBPyConnection, table: str, query: str, params: list | None = None
) -> None:
    """Create ``table`` (rid, id, geom) from ``query``'s rows and its first GEOMETRY column.

    The geometry keeps its CRS; ``project`` puts both sides in one before measuring.
    """
    rel = con.sql(query, params=params or [])
    geom = next(
        (
            name
            for name, kind in zip(rel.columns, rel.types, strict=True)
            if "GEOMETRY" in str(kind).upper()
        ),
        None,
    )
    if geom is None:
        raise ValueError(f"no geometry column in {query!r}")
    ident = '"id"::varchar' if "id" in rel.columns else "rid::varchar"
    con.execute(
        f"""
        create or replace temp table {table} as
        select rid, {ident} as id, "{geom}" as geom
        from (select row_number() over () as rid, * from ({query}))
        where "{geom}" is not null
        """,
        params or [],
    )


def load_file(con: duckdb.DuckDBPyConnection, table: str, path: Path) -> None:
    """``load`` from a .parquet, .gpkg, .geojson, or .fgb file."""
    vectors.check(path)
    quoted = str(path).replace("'", "''")
    source = (
        f"'{quoted}'" if path.suffix.lower() == ".parquet" else f"ST_Read('{quoted}')"
    )
    load(con, table, f"select * from {source}")


def project(con: duckdb.DuckDBPyConnection, *tables: str) -> str:
    """Reproject ``tables`` to the UTM zone at the first one's center, adding ``area``."""
    [(lon, lat)] = con.execute(
        f"""
        select (ST_XMin(e) + ST_XMax(e)) / 2, (ST_YMin(e) + ST_YMax(e)) / 2
        from (select ST_Extent_Agg(ST_Transform(geom, 'EPSG:4326')) as e from {tables[0]})
        """
    ).fetchall()
    utm = vectors.utm_zone(lon, lat)
    for table in tables:
        con.execute(
            f"""
            create or replace temp table {table} as
            select rid, id, g as geom, ST_Area(g) as area
            from (select rid, id, ST_Transform(geom, '{utm}') as g from {table})
            """
        )
    return utm


def pairs(con: duckdb.DuckDBPyConnection, iou: float) -> None:
    """Overlapping (a, b) pairs, and the one-to-one ``matches`` among them.

    Matching is greedy by descending IoU: the best remaining pair is matched first and
    neither field matches again, the usual rule for instance segmentation. At IoU above
    0.5 every field has at most one candidate, so the order no longer matters.
    """
    con.execute(
        """
        create or replace temp table pairs as
        select ar, br, inter, inter / aa as a_inside, inter / ba as b_inside,
               inter / (aa + ba - inter) as iou
        from (
            select a.rid as ar, b.rid as br, a.area as aa, b.area as ba,
                   ST_Area(ST_Intersection(a.geom, b.geom)) as inter
            from a join b on ST_Intersects(a.geom, b.geom)
        )
        where inter > 0
        """
    )
    candidates = con.execute(
        "select ar, br, iou from pairs where iou >= ? order by iou desc, ar, br", [iou]
    ).fetchall()
    used_a, used_b, matched = set(), set(), []
    for ar, br, value in candidates:
        if ar not in used_a and br not in used_b:
            used_a.add(ar)
            used_b.add(br)
            matched.append((ar, br, value))
    con.execute(
        "create or replace temp table matches (ar bigint, br bigint, iou double)"
    )
    if matched:
        con.executemany("insert into matches values (?, ?, ?)", matched)


def compare(con: duckdb.DuckDBPyConnection, iou: float = 0.5) -> Comparison:
    """``pairs`` and the scores, with tables ``a`` (reference) and ``b`` (prediction)."""
    pairs(con, iou)
    [(n_a, area_a)] = con.execute("select count(*), sum(area) from a").fetchall()
    [(n_b,)] = con.execute("select count(*) from b").fetchall()
    [(matched, mean_iou, matched_area)] = con.execute(
        "select count(*), avg(iou), sum(a.area) from matches join a on a.rid = matches.ar"
    ).fetchall()
    [(merges,)] = con.execute(
        f"select count(*) from (select br from pairs where a_inside >= {CONTAINED} group by br having count(*) > 1)"
    ).fetchall()
    [(splits,)] = con.execute(
        f"select count(*) from (select ar from pairs where b_inside >= {CONTAINED} group by ar having count(*) > 1)"
    ).fetchall()
    precision = matched / n_b if n_b else 0.0
    recall = matched / n_a if n_a else 0.0
    return Comparison(
        reference=n_a,
        prediction=n_b,
        matched=matched,
        precision=round(precision, 4),
        recall=round(recall, 4),
        f1=round(2 * precision * recall / (precision + recall), 4) if matched else 0.0,
        mean_iou=round(mean_iou or 0.0, 4),
        matched_area=round((matched_area or 0.0) / area_a, 4) if area_a else 0.0,
        merges=merges,
        splits=splits,
    )


def write_prediction(con: duckdb.DuckDBPyConnection, out: Path) -> int:
    """Table ``b`` to ``out`` in EPSG:4326, with ``match_iou`` and the reference's
    ``matched_id``, both null for unmatched fields."""
    query = """
        select b.id, ST_Transform(b.geom, 'EPSG:4326') as geometry,
               b.area::float as "metrics:area", m.iou as match_iou, a.id as matched_id
        from b left join matches m on m.br = b.rid left join a on a.rid = m.ar
    """
    return vectors.write(con, query, out, fiboa.collection("ftw-cli-compare"))


CHANGES = ("kept", "changed", "split", "merged", "new", "lost")


@dataclass(frozen=True)
class Change:
    """One change class: its fields in the earlier and later year, and their area."""

    change: str
    fields_from: int
    fields_to: int
    area_from_ha: float
    area_to_ha: float


def changes(con: duckdb.DuckDBPyConnection, iou: float = 0.5) -> list[Change]:
    """Label every field of ``a`` (the earlier year) and ``b`` (the later one).

    ``kept`` fields match one to one. ``split`` is a field of ``a`` holding two or more
    fields of ``b``, and those fields; ``merged`` the reverse. ``lost`` and ``new`` fields
    overlap nothing in the other year, and ``changed`` fields overlap but are none of
    these. The labels go to ``a_change`` and ``b_change``, so each year's counts add up.
    """
    pairs(con, iou)
    con.execute(
        f"""
        create or replace temp table split_a as
        select ar from pairs where b_inside >= {CONTAINED} group by ar having count(*) > 1;
        create or replace temp table merge_b as
        select br from pairs where a_inside >= {CONTAINED} group by br having count(*) > 1;
        create or replace temp table a_change as
        select rid, area, case
            when rid in (select ar from matches) then 'kept'
            when rid in (select ar from split_a) then 'split'
            when rid in (select ar from pairs join merge_b using (br) where a_inside >= {CONTAINED})
                then 'merged'
            when rid not in (select ar from pairs) then 'lost'
            else 'changed' end as change
        from a;
        create or replace temp table b_change as
        select rid, area, case
            when rid in (select br from matches) then 'kept'
            when rid in (select br from merge_b) then 'merged'
            when rid in (select br from pairs join split_a using (ar) where b_inside >= {CONTAINED})
                then 'split'
            when rid not in (select br from pairs) then 'new'
            else 'changed' end as change
        from b;
        """
    )
    rows = con.execute(
        """
        select change,
               count(*) filter (where side = 'a'), count(*) filter (where side = 'b'),
               coalesce(sum(area) filter (where side = 'a'), 0) / 1e4,
               coalesce(sum(area) filter (where side = 'b'), 0) / 1e4
        from (select 'a' as side, change, area from a_change
              union all select 'b', change, area from b_change)
        group by change
        """
    ).fetchall()
    found = {name: counts for name, *counts in rows}
    return [
        Change(name, n_from, n_to, round(area_from, 1), round(area_to, 1))
        for name in CHANGES
        for n_from, n_to, area_from, area_to in [found.get(name, (0, 0, 0.0, 0.0))]
    ]


def write_changes(
    con: duckdb.DuckDBPyConnection, out: Path, start: int, end: int
) -> int:
    """The later year's fields and the earlier year's lost ones, with ``year`` and ``change``."""
    query = f"""
        select b.id, {int(end)} as year, c.change, ST_Transform(b.geom, 'EPSG:4326') as geometry,
               b.area::float as "metrics:area"
        from b join b_change c using (rid)
        union all
        select a.id, {int(start)}, c.change, ST_Transform(a.geom, 'EPSG:4326'), a.area::float
        from a join a_change c using (rid) where c.change = 'lost'
    """
    return vectors.write(con, query, out, fiboa.collection("ftw-cli-diff"))
