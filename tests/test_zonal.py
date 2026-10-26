import json
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import pytest

rasterio = pytest.importorskip("rasterio")
pytest.importorskip("exactextract")

import duckdb

from ftw_cli import zonal


def _raster(path, descriptions=("B04 2024-03-19", None)):
    """4 x 4 at 10 m: band 1 is the column index (0-3), band 2 is ten times that."""
    cols = np.tile(np.arange(4, dtype=np.float32), (4, 1))
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=4,
        height=4,
        count=2,
        dtype="float32",
        crs="EPSG:32615",
        transform=rasterio.transform.from_origin(0, 40, 10, 10),
    ) as dst:
        dst.write(np.stack([cols, cols * 10]))
        dst.descriptions = descriptions
    return path


def _fields(path):
    """Two columns of pixels, half of two pixels, and 4 m² inside one pixel."""
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial; SET geometry_always_xy = true")
    con.execute(
        f"""copy (select * from (values
            ('whole', ST_MakeEnvelope(0, 20, 20, 40)),
            ('halves', ST_MakeEnvelope(15, 0, 25, 10)),
            ('tiny', ST_MakeEnvelope(31, 1, 33, 3))) t(id, geom))
        to '{path}' (FORMAT gdal, DRIVER 'GPKG', SRS 'EPSG:32615')"""
    )
    return path


def test_stats_weight_pixels_by_coverage(tmp_path):
    out = tmp_path / "out.parquet"
    zonal.add(_fields(tmp_path / "f.gpkg"), [_raster(tmp_path / "r.tif")], out)

    rows = {r["id"]: r for r in pq.read_table(out).to_pylist()}
    assert rows["whole"]["B04_2024_03_19_mean"] == pytest.approx(0.5)
    assert rows["whole"]["B04_2024_03_19_count"] == pytest.approx(4)
    assert rows["halves"]["B04_2024_03_19_mean"] == pytest.approx(1.5)
    assert rows["halves"]["r_b2_max"] == pytest.approx(20)
    # Smaller than a pixel, but still measured, from the pixel it sits in.
    assert rows["tiny"]["B04_2024_03_19_mean"] == pytest.approx(3)
    assert rows["tiny"]["B04_2024_03_19_count"] == pytest.approx(0.04)
    # Declared for fiboa, as doubles, whatever the raster's dtype.
    custom = json.loads(pq.read_schema(out).metadata[b"collection"])["schemas:custom"]
    assert custom["properties"]["r_b2_max"]["type"] == "double"
    assert pq.read_schema(out).field("r_b2_max").type == "double"


def test_stats_on_chosen_bands_and_a_second_raster(tmp_path):
    fields = _fields(tmp_path / "f.gpkg")
    first, second = _raster(tmp_path / "a.tif"), _raster(tmp_path / "b.tif")
    out = tmp_path / "out.gpkg"
    zonal.add(fields, [first, second], out, bands=[1])

    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial")
    columns = {
        d[0] for d in con.execute(f"select * from ST_Read('{out}') limit 0").description
    }
    # The second raster's band shares the first's description, so it gets its name too.
    assert {"B04_2024_03_19_mean", "b_B04_2024_03_19_mean"} <= columns
    assert not any(c.startswith(("a_b2", "b_b2")) for c in columns)


def test_traced_outline_counts_every_pixel(tmp_path):
    # A real outline traced from a 10 m raster and stored in EPSG:4326. Reprojected back,
    # its vertices sit 1e-9 m off pixel edges, which made exactextract skip 3 pixels.
    wkt = (Path(__file__).parent / "data" / "traced_field.wkt").read_text()
    raster = tmp_path / "ones.tif"
    with rasterio.open(
        raster,
        "w",
        driver="GTiff",
        width=82,
        height=163,
        count=1,
        dtype="float32",
        crs="EPSG:32615",
        transform=rasterio.transform.from_origin(446250, 4658620, 10, 10),
    ) as dst:
        dst.write(np.ones((1, 163, 82), np.float32))
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial; SET geometry_always_xy = true")
    fields = tmp_path / "f.gpkg"
    con.execute(
        f"copy (select 'f' as id, ST_GeomFromText(?) as geom) to '{fields}' "
        "(FORMAT gdal, DRIVER 'GPKG', SRS 'EPSG:4326')",
        [wkt],
    )
    [(area,)] = con.execute(
        "select ST_Area(ST_Transform(ST_GeomFromText(?), 'EPSG:4326', 'EPSG:32615')) / 100",
        [wkt],
    ).fetchall()

    out = tmp_path / "out.parquet"
    zonal.add(fields, [raster], out)

    [row] = pq.read_table(out).to_pylist()
    assert row["ones_b1_count"] == pytest.approx(area, abs=1e-3)  # 7576.5, not 7573.5
