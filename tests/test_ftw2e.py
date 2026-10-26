import json

import pyarrow.parquet as pq
import pytest

from ftw_cli import ftw2e, output

IOWA = ftw2e.BBox(-93.70, 41.95, -93.60, 42.05)


@pytest.mark.parametrize(
    "text",
    [
        "-93.6,41.95,-93.7,42.05",  # xmin > xmax
        "41.95,-93.7,42.05,-93.6",  # lat/lon swapped
        "-93.7,41.95,-93.6",
    ],
)
def test_bbox_rejects_malformed(text: str):
    with pytest.raises(ValueError):
        ftw2e.BBox.parse(text)


def test_piped_output_is_json(capsys: pytest.CaptureFixture[str]):
    output.configure(None)  # capsys stdout is not a TTY
    output.emit([{"year": 2025, "fields": 394}])
    assert json.loads(capsys.readouterr().out) == [{"year": 2025, "fields": 394}]


@pytest.mark.integration
def test_query_reads_one_zone_file():
    con = ftw2e.connect()
    summary = ftw2e.summarize(con, IOWA, 2025)
    assert summary["fields"] > 0
    fields = ftw2e.fields(con, IOWA, 2025, limit=5)
    assert len(fields) == 5
    assert fields[0]["geometry"]["type"] in ("Polygon", "MultiPolygon")


@pytest.mark.integration
def test_parquet_export_keeps_the_fiboa_collection(tmp_path):
    out = tmp_path / "iowa.parquet"
    result = ftw2e.export(ftw2e.connect(), IOWA, [2025], out, limit=10)
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
    con = ftw2e.connect()
    out = tmp_path / "iowa.parquet"
    result = ftw2e.export(con, IOWA, [2018, 2024], out, limit=None)
    table = pq.read_table(out, columns=["year", "collection"]).to_pylist()
    for year in (2018, 2024):
        rows = [r for r in table if r["year"] == year]
        assert len(rows) == ftw2e.summarize(con, IOWA, year)["fields"]
        assert {r["collection"] for r in rows} == {f"ftw-s2-{year}"}
    assert result["fields"] == len(table)
