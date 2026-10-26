import json

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from ftw_cli import fiboa


@pytest.fixture
def con():
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial; SET geometry_always_xy = true")
    return con


QUERY = """
    select 'f' || i as id, i::float as "metrics:area",
           timestamptz '2024-11-01 17:03:49+00' as "determination:datetime",
           ST_MakeEnvelope(i, i, i + 1, i + 2) as geometry
    from range(3) t(i)
"""


def test_copy_writes_fiboa_nullability_units_and_covering(con, tmp_path):
    out = tmp_path / "fields.parquet"
    meta = fiboa.collection("test", **{"determination:details": "the model's run"})

    assert fiboa.copy(con, QUERY, out, meta) == 3

    schema = pq.read_schema(out)
    assert not schema.field("id").nullable and not schema.field("geometry").nullable
    assert schema.field("metrics:area").nullable and schema.field("bbox").nullable
    assert schema.field("determination:datetime").type == pa.timestamp("ms", tz="UTC")
    geo = json.loads(schema.metadata[b"geo"])
    assert geo["version"] == "1.1.0"
    assert geo["columns"]["geometry"]["covering"]["bbox"]["xmax"] == ["bbox", "xmax"]
    assert json.loads(schema.metadata[b"collection"]) == meta

    rows = pq.read_table(out).to_pylist()
    first = next(r for r in rows if r["id"] == "f1")
    assert first["bbox"] == {"xmin": 1.0, "ymin": 1.0, "xmax": 2.0, "ymax": 3.0}


def test_copy_keeps_timestamps_when_relabeling_the_zone(con, tmp_path):
    con.execute("SET TimeZone = 'America/Chicago'")
    out = tmp_path / "fields.parquet"
    fiboa.copy(con, QUERY, out, fiboa.collection("test"))
    (stamp,) = {r["determination:datetime"] for r in pq.read_table(out).to_pylist()}
    assert stamp.isoformat() == "2024-11-01T17:03:49+00:00"
