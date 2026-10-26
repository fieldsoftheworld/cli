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
    result = ftw2e.export(ftw2e.connect(), IOWA, 2025, out, limit=10)
    assert result["fields"] == 10
    metadata = pq.read_schema(out).metadata
    assert json.loads(metadata[b"collection"])["collection"] == "ftw-s2-2025"
    assert json.loads(metadata[b"geo"])["version"] == "1.1.0"
