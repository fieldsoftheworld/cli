import json

import duckdb
import pyarrow.parquet as pq
import pytest

from ftw_cli import ftw2e, vectors
from ftw_cli.areas import BBox

IOWA = BBox(-93.70, 41.95, -93.60, 42.05)


@pytest.mark.integration
def test_query_reads_one_zone_file():
    con = vectors.connect()
    summary = ftw2e.summarize(con, IOWA, 2025)
    assert summary["fields"] > 0
    fields = ftw2e.fields(con, IOWA, 2025, limit=5)
    assert len(fields) == 5
    assert fields[0]["geometry"]["type"] in ("Polygon", "MultiPolygon")


@pytest.mark.integration
def test_parquet_export_keeps_the_fiboa_collection(tmp_path):
    out = tmp_path / "iowa.parquet"
    result = ftw2e.export(vectors.connect(), IOWA, [2025], out, limit=10)
    assert result["fields"] == 10
    metadata = pq.read_schema(out).metadata
    assert json.loads(metadata[b"collection"])["collection"] == "ftw-s2-2025"
    assert json.loads(metadata[b"geo"])["version"] == "1.1.0"


PUBLISHED = list(range(2017, 2026))


@pytest.mark.parametrize(
    ("spec", "years"),
    [
        (None, [2025]),
        ("2024", [2024]),
        ("2019..2021", [2019, 2020, 2021]),
        ("2024,2018,2024", [2018, 2024]),
        ("all", PUBLISHED),
    ],
)
def test_parse_years(spec, years):
    assert ftw2e.parse_years(spec, PUBLISHED) == years


@pytest.mark.parametrize(
    "spec", ["2016", "2024..2026", "2024..2019", "24", "2019-2021"]
)
def test_parse_years_rejects(spec):
    with pytest.raises(ValueError, match="year|published"):
        ftw2e.parse_years(spec, PUBLISHED)


def test_collection_merges_years_and_moves_what_differs_to_columns():
    schemas = ["https://fiboa.org/specification/v0.3.0/schema.yaml"]
    custom = {"properties": {"score": {"type": "uint8"}}}
    metas = [
        {
            "schemas": {f"ftw-s2-{y}": schemas},
            "collection": f"ftw-s2-{y}",
            "determination:details": f"mosaics of {y}",
            "schemas:custom": custom,
        }
        for y in (2018, 2024)
    ]
    meta, varying = ftw2e._collection(metas)
    assert set(meta["schemas"]) == {"ftw-s2-2018", "ftw-s2-2024"}
    assert "collection" not in meta and varying == ["determination:details"]
    assert set(meta["schemas:custom"]["properties"]) == {"score", "year"}


@pytest.mark.integration
def test_multi_year_export_has_a_row_per_field_per_year(tmp_path):
    con = vectors.connect()
    out = tmp_path / "iowa.parquet"
    result = ftw2e.export(con, IOWA, [2018, 2024], out, limit=None)
    table = pq.read_table(out, columns=["year", "collection"]).to_pylist()
    for year in (2018, 2024):
        rows = [r for r in table if r["year"] == year]
        assert len(rows) == ftw2e.summarize(con, IOWA, year)["fields"]
        assert {r["collection"] for r in rows} == {f"ftw-s2-{year}"}
    assert result["fields"] == len(table)


def test_size_stats_on_known_areas(tmp_path):
    path = tmp_path / "fields.parquet"
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial")
    # 0.5, 2, 50, and 150 ha: one field in each size class
    con.execute(
        f"""
        copy (
            select a * 1e4 as "metrics:area",
                   ST_MakeEnvelope(i, i, i + 0.1, i + 0.1) as geometry,
                   struct_pack(xmin := i, ymin := i, xmax := i + 0.1, ymax := i + 0.1) as bbox
            from (values (0, 0.5), (1, 2.0), (2, 50.0), (3, 150.0)) t(i, a)
        ) to '{path}'
        """
    )
    box = BBox(-1, -1, 5, 5)
    stats = ftw2e.size_stats(vectors.connect(), box, [str(path)])

    assert stats["median_ha"] == 26.0  # halfway between 2 and 50
    assert [stats[f"fields_{c}"] for c, *_ in ftw2e.SIZE_CLASSES] == [1, 1, 1, 1]
    assert stats["area_ge_100ha_ha"] == 150.0


@pytest.mark.integration
def test_size_classes_add_up_to_the_summary():
    summary = ftw2e.summarize(vectors.connect(), IOWA, 2024, stats=True)
    classes = [c for c, *_ in ftw2e.SIZE_CLASSES]
    assert sum(summary[f"fields_{c}"] for c in classes) == summary["fields"]
    assert sum(summary[f"area_{c}_ha"] for c in classes) == pytest.approx(
        summary["area_ha"], abs=0.5
    )
    assert summary["p25_ha"] <= summary["median_ha"] <= summary["p75_ha"]
